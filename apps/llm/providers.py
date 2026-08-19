"""Provider implementations: Groq (primary), Gemini (fallback).

Both wrap their respective HTTP APIs. We deliberately do NOT pull in
the official SDKs — they add ~50MB of deps and a startup cost we
don't need when we're just streaming tokens over HTTP.

The contract is the LLMProvider interface from core.interfaces.
"""
from __future__ import annotations
import json
import logging
from typing import AsyncIterator, Iterable
import urllib.parse

import requests

from core.domain.entities import RetrievedChunk
from core.interfaces import LLMProvider

log = logging.getLogger(__name__)


def _format_context(chunks: Iterable[RetrievedChunk]) -> str:
    """Render retrieved chunks into the system-prompt context block.

    Numbered so the LLM can cite by index, and prefixed with the
    document title so answers carry the source attribution.
    """
    parts = []
    for i, c in enumerate(chunks, 1):
        parts.append(f"[{i}] {c.document_title}\n{c.chunk_text}")
    return "\n\n".join(parts) or "(no context)"


# ----------------------------------------------------------------------
# Groq (primary)
# ----------------------------------------------------------------------
class GroqProvider(LLMProvider):
    """Groq uses an OpenAI-compatible chat completions endpoint."""

    def __init__(self, api_key: str, model: str, base_url: str = "https://api.groq.com/openai/v1"):
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")

    @property
    def name(self) -> str:
        return "groq"

    def health(self) -> bool:
        if not self._api_key:
            return False
        try:
            # cheap models endpoint
            r = requests.get(
                f"{self._base_url}/models",
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=4,
            )
            return r.status_code == 200
        except Exception:
            return False

    async def stream_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        context_chunks: Iterable[RetrievedChunk],
    ) -> AsyncIterator[str]:
        context = _format_context(context_chunks)
        messages = [
            {"role": "system", "content": f"{system_prompt}\n\nContext:\n{context}"},
            {"role": "user", "content": user_prompt},
        ]
        url = f"{self._base_url}/chat/completions"
        with requests.post(
            url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self._model,
                "messages": messages,
                "stream": True,
                "temperature": 0.3,
                "max_tokens": 1024,
            },
            stream=True,
            timeout=30,
        ) as resp:
            if resp.status_code != 200:
                log.warning("groq upstream status=%s body=%s",
                            resp.status_code, resp.text[:200])
                raise RuntimeError(f"groq {resp.status_code}")
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                if payload == "[DONE]":
                    break
                try:
                    obj = json.loads(payload)
                    delta = obj.get("choices", [{}])[0].get("delta", {}).get("content")
                    if delta:
                        yield delta
                except json.JSONDecodeError:
                    continue


# ----------------------------------------------------------------------
# Gemini (fallback)
# ----------------------------------------------------------------------
class GeminiProvider(LLMProvider):
    """Gemini's streamGenerateContent endpoint, SSE-style."""

    def __init__(self, api_key: str, model: str = "gemini-1.5-flash"):
        self._api_key = api_key
        self._model = model

    @property
    def name(self) -> str:
        return "gemini"

    def health(self) -> bool:
        if not self._api_key:
            return False
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={self._api_key}"
            r = requests.get(url, timeout=4)
            return r.status_code == 200
        except Exception:
            return False

    async def stream_completion(
        self,
        system_prompt: str,
        user_prompt: str,
        context_chunks: Iterable[RetrievedChunk],
    ) -> AsyncIterator[str]:
        context = _format_context(context_chunks)
        path = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self._model}:streamGenerateContent?alt=sse"
            f"&key={self._api_key}"
        )
        body = {
            "system_instruction": {"parts": [{"text": f"{system_prompt}\n\nContext:\n{context}"}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {"temperature": 0.3, "maxOutputTokens": 1024},
        }
        with requests.post(
            path,
            headers={"Content-Type": "application/json"},
            json=body,
            stream=True,
            timeout=30,
        ) as resp:
            if resp.status_code != 200:
                log.warning("gemini upstream status=%s body=%s",
                            resp.status_code, resp.text[:200])
                raise RuntimeError(f"gemini {resp.status_code}")
            for line in resp.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data: "):
                    continue
                payload = line[len("data: "):]
                try:
                    obj = json.loads(payload)
                    parts = obj.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                    for p in parts:
                        t = p.get("text")
                        if t:
                            yield t
                except json.JSONDecodeError:
                    continue


__all__ = ["GroqProvider", "GeminiProvider"]

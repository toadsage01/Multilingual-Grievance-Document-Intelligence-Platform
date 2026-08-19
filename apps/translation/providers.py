"""Translator implementations.

The contract is core.interfaces.Translator — three methods:
  translate(text, src, tgt) -> str
  supports(src, tgt) -> bool
  name -> str

We provide:
  - PassthroughTranslator (default when no external service is wired):
    just returns the input. Useful when the LLM was already prompted
    in the target language and we don't need a second hop.
  - BhashiniTranslator (optional, only loaded if BHASHINI_API_KEY is set):
    hits the Bhashini pipeline endpoint. We don't ship the SDK; just
    a thin HTTP client over the public API.
"""
from __future__ import annotations
import logging
import time
import urllib.parse
import requests

from core.exceptions import SetuError
from core.interfaces import Translator

log = logging.getLogger(__name__)


class PassthroughTranslator(Translator):
    """Always returns the input. The default — no translation hop."""

    @property
    def name(self) -> str:
        return "passthrough"

    def translate(self, text: str, source: str, target: str) -> str:
        return text

    def supports(self, source: str, target: str) -> bool:
        return True  # it's the identity function — works for every pair


class BhashiniTranslator(Translator):
    """Thin HTTP client over the Bhashini ULCA pipeline.

    The pipeline is async — first we request a translation, then we
    poll for the result. Real implementations should cache, but for
    a demo flow the synchronous single-call path is fine.

    Doc reference: https://bhashini.gitbook.io/bhashini-apis
    """

    BASE_URL = "https://dhruva-api.bhashini.gov.in/v1"

    def __init__(self, api_key: str, user_id: str):
        self._api_key = api_key
        self._user_id = user_id

    @property
    def name(self) -> str:
        return "bhashini"

    def supports(self, source: str, target: str) -> bool:
        # Bhashini covers the major Indian language pairs. The actual
        # coverage check is done by their service — we trust the call.
        return source in {"en", "hi", "bn", "ta", "te", "mr", "gu",
                          "kn", "ml", "pa", "or", "as", "ur"}

    def translate(self, text: str, source: str, target: str) -> str:
        if source == target:
            return text
        if not self.supports(source, target):
            raise SetuError(f"bhashini does not cover {source}->{target}")
        url = f"{self.BASE_URL}/translate"
        headers = {
            "Authorization": self._api_key,
            "userId": self._user_id,
            "Content-Type": "application/json",
        }
        body = {
            "input": [{"source": text}],
            "config": {
                "sourceLanguage": source,
                "targetLanguage": target,
                "serviceId": "",  # let Bhashini pick
            },
        }
        try:
            r = requests.post(url, headers=headers, json=body, timeout=15)
            r.raise_for_status()
            data = r.json()
            out = data.get("output", [{}])[0].get("target", "")
            if not out:
                log.warning("bhashini returned empty for %s->%s", source, target)
                return text
            return out
        except Exception as e:
            log.warning("bhashini failure src=%s tgt=%s err=%s", source, target, e)
            raise SetuError(f"bhashini: {e}")


# -- factory -------------------------------------------------------------
_translator: Translator | None = None


def get_translator() -> Translator:
    """Return the configured translator. Defaults to passthrough."""
    global _translator
    if _translator is not None:
        return _translator
    from django.conf import settings
    api_key = getattr(settings, "BHASHINI_API_KEY", "")
    user_id = getattr(settings, "BHASHINI_USER_ID", "")
    if api_key and user_id:
        log.info("translator: bhashini")
        _translator = BhashiniTranslator(api_key, user_id)
    else:
        log.info("translator: passthrough (no bhashini creds)")
        _translator = PassthroughTranslator()
    return _translator

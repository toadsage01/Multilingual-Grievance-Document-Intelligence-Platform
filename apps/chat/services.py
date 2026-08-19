"""Chat service — ties retrieval + LLM streaming together.

Key design choices:
1. Retrieval runs in the source language — no English pivot. The user
   typed Hindi, we search Hindi chunks. The LLM is then prompted in
   Hindi directly. Only if the LLM's output quality is weak do we
   hand the response to the optional Translator.
2. Every user message is persisted BEFORE we hit the LLM, so a
   provider outage never loses the citizen's input.
3. Confidence threshold drives the auto-routing decision: high
   confidence -> the assistant message stands; low confidence -> the
   downstream grievance layer escalates rather than silently misrouts.
"""
from __future__ import annotations
import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import AsyncIterator

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.chat.models import Conversation, Message
from apps.retrieval.handlers import (
    SearchQuery, aggregate_confidence, search,
)
from apps.llm.breaker import get_chain
from core.domain.entities import RetrievedChunk
from core.exceptions import ProviderUnavailable

log = logging.getLogger(__name__)


@dataclass
class ChatTurn:
    message_id: uuid.UUID
    conversation_id: uuid.UUID
    cited_chunk_ids: list[uuid.UUID]
    confidence_score: float | None
    tokens: list[str]
    degraded: bool  # True if we returned a "saved but no answer" reply


def start_conversation(
    *, department_id: uuid.UUID, language_code: str = "en",
    citizen_ref: str = "",
) -> Conversation:
    return Conversation.objects.create(
        department_id=department_id,
        language_code=language_code,
        citizen_ref=citizen_ref,
    )


def _persist_user_message(conversation: Conversation, text: str) -> Message:
    return Message.objects.create(
        conversation=conversation,
        department_id=conversation.department_id,
        role="user",
        content=text,
    )


def _build_system_prompt(department_guardrail: str, language: str) -> str:
    base = (
        "You are Setu, a multilingual grievance and document assistant for "
        "Indian government departments. Answer ONLY from the retrieved context. "
        "If the context does not contain the answer, say so plainly and suggest "
        "the citizen file a grievance. Do not invent policy details."
    )
    if department_guardrail:
        base += f"\n\nDepartment-specific guardrails:\n{department_guardrail}"
    if language != "en":
        base += f"\nRespond in language_code={language}."
    return base


async def stream_turn(
    *,
    conversation: Conversation,
    user_text: str,
    department_guardrail: str = "",
) -> AsyncIterator[dict]:
    """Yield SSE event dicts. Caller writes them to the wire.

    Event types:
      token   - {text: "..."}     one chunk of the answer
      citation- {chunk_id, document_title}   per retrieved source
      done    - {message_id, confidence_score, degraded}
    """
    # 1. persist user message (never lost)
    user_msg = _persist_user_message(conversation, user_text)

    # 2. retrieve
    hits = search(SearchQuery(
        department_id=conversation.department_id,
        text=user_text, language_code=conversation.language_code,
        top_k=getattr(settings, "RAG_TOP_K", 5),
    ))
    cited = [h.chunk_id for h in hits]
    for h in hits:
        yield {"event": "citation",
               "data": {"chunk_id": str(h.chunk_id),
                        "document_title": h.document_title,
                        "score": h.score}}

    confidence = aggregate_confidence(hits) if hits else 0.0

    # 3. attempt generation
    system_prompt = _build_system_prompt(department_guardrail, conversation.language_code)
    chain = get_chain()
    tokens: list[str] = []
    degraded = False
    try:
        tokens = await chain.stream_completion(system_prompt, user_text, hits)
    except ProviderUnavailable as e:
        log.warning("all providers down conv=%s err=%s", conversation.id, e)
        degraded = True
        tokens = [
            "I'm sorry, the answer service is temporarily unavailable. "
            "Your message has been saved — a human officer will follow up. "
            "(reference: " + str(user_msg.id) + ")"
        ]

    # stream tokens
    for tok in tokens:
        yield {"event": "token", "data": {"text": tok}}

    # 4. persist assistant message
    with transaction.atomic():
        asst = Message.objects.create(
            conversation=conversation,
            department_id=conversation.department_id,
            role="assistant",
            content="".join(tokens),
            cited_chunk_ids=cited,
            confidence_score=confidence if not degraded else None,
        )

    yield {"event": "done", "data": {
        "message_id": str(asst.id),
        "user_message_id": str(user_msg.id),
        "confidence_score": confidence,
        "degraded": degraded,
        "cited_chunk_ids": [str(c) for c in cited],
    }}


def stream_turn_sync(*, conversation, user_text, department_guardrail=""):
    """Sync wrapper for the SSE view to consume the async generator.

    Returns a generator of (event, data) tuples. We bridge async->sync
    via a queue because Django's StreamingHttpResponse expects a sync
    iterator and we want to ship tokens as they arrive, not buffered.
    """
    import queue, threading
    q: "queue.Queue[tuple[str, dict] | None]" = queue.Queue()

    def _run():
        loop = asyncio.new_event_loop()
        try:
            async def gen():
                async for evt in stream_turn(
                    conversation=conversation,
                    user_text=user_text,
                    department_guardrail=department_guardrail,
                ):
                    q.put((evt["event"], evt["data"]))
            loop.run_until_complete(gen())
        except Exception as e:
            log.exception("stream_turn failure: %s", e)
            q.put(("error", {"text": str(e)}))
        finally:
            loop.close()
            q.put(None)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    while True:
        item = q.get()
        if item is None:
            break
        yield item

"""Retrieval query handler — the "Q" in CQRS.

Owns the pgvector cosine similarity lookup. Multilingual by design:
queries and chunks live in the same shared embedding space, so a
Hindi query matches a Hindi chunk without an English pivot hop.
"""
from __future__ import annotations
import hashlib
import logging
import uuid
from dataclasses import dataclass
from typing import Iterable

from django.conf import settings
from django.core.cache import cache
from django.db import connection

from core.domain.entities import RetrievedChunk

log = logging.getLogger(__name__)


@dataclass
class SearchQuery:
    department_id: uuid.UUID
    text: str
    language_code: str = "en"
    top_k: int = 5


def _cache_key(q: SearchQuery) -> str:
    raw = f"{q.department_id}|{q.language_code}|{q.text}|{q.top_k}"
    return f"setu:search:{hashlib.sha1(raw.encode()).hexdigest()}"


def embed_query(text: str) -> list[float]:
    """Use the ingestion embedder so queries and chunks share a model."""
    from apps.ingestion.embeddings import get_embedder
    return get_embedder().embed_query(text)


def search(q: SearchQuery) -> list[RetrievedChunk]:
    """Cosine top-K against document_chunks. Cached for ~10 minutes."""
    key = _cache_key(q)
    cached = cache.get(key)
    if cached is not None:
        log.debug("search cache hit dept=%s", q.department_id)
        return [RetrievedChunk(**c) for c in cached]

    top_k = min(q.top_k, getattr(settings, "RAG_TOP_K", 5))
    vec = embed_query(q.text)

    # raw SQL because Django ORM can't express the <=> operator
    # cleanly and the cited document_title join matters
    sql = """
        SELECT c.id, d.title, c.chunk_text,
               1 - (c.embedding <=> %s::vector) AS score
        FROM document_chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE c.department_id = %s
          AND d.superseded_by IS NULL
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s;
    """
    with connection.cursor() as cur:
        cur.execute(sql, [vec, str(q.department_id), vec, top_k])
        rows = cur.fetchall()

    results = [
        RetrievedChunk(
            chunk_id=row[0],
            document_title=row[1],
            chunk_text=row[2],
            score=float(row[3]),
        )
        for row in rows
    ]

    # serialize into the cache (uuid needs str)
    cache.set(
        key,
        [
            {
                "chunk_id": str(r.chunk_id),
                "document_title": r.document_title,
                "chunk_text": r.chunk_text,
                "score": r.score,
            }
            for r in results
        ],
        timeout=600,  # 10 minutes
    )
    log.info("search dept=%s lang=%s hits=%d top_score=%.3f",
             q.department_id, q.language_code, len(results),
             results[0].score if results else 0.0)
    return results


def aggregate_confidence(hits: Iterable[RetrievedChunk]) -> float:
    """A cheap proxy for answer confidence: top-hit score, dampened by
    distance to the 3rd hit.

    Used by the RAG layer to decide ANSWERED vs ESCALATED.
    """
    hits = list(hits)
    if not hits:
        return 0.0
    top = hits[0].score
    if len(hits) >= 3:
        gap = top - hits[2].score
        # narrower gap = less confident (top hit isn't clearly separated)
        return round(top * (1 - gap / 2), 3)
    return round(top, 3)

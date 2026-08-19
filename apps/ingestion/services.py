"""Ingestion service — ties together cleaning, chunking, embeddings,
checksum delta and DB writes. This is what /api/v1/ingestion/documents/
hands off to (synchronously in dev, via RQ in prod).

Failure semantics:
- We never lose the document row even if embedding fails. The chunks
  are inserted with NULL embeddings and a re-embed job picks them up.
- We never delete a superseded document — past conversations cite it.
"""
from __future__ import annotations
import logging
import uuid
from dataclasses import dataclass
from typing import Iterable

from django.db import transaction
from django.utils import timezone

from apps.ingestion.models import Document, DocumentChunk
from apps.tenancy.models import Department
from apps.ingestion.cleaning import normalize, language_detect, clean_to_dataframe
from apps.ingestion.chunking import chunk_paragraphs
from apps.ingestion.checksum import checksum, ChecksumDelta
from apps.ingestion.embeddings import get_embedder

log = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    document_id: uuid.UUID
    new: bool
    chunk_count: int
    superseded_id: uuid.UUID | None = None


def ingest_text(
    *,
    department_id: uuid.UUID,
    title: str,
    raw_text: str,
    source_url: str = "",
    language_hint: str | None = None,
) -> IngestionResult:
    """Synchronous ingest path — used by tests and the dev endpoint."""
    text = normalize(raw_text)
    cs = checksum(text)
    dept = Department.objects.get(id=department_id)

    # 1. check existing rows for this (department, source_url, checksum)
    existing = list(
        Document.objects.filter(
            department_id=department_id, source_url=source_url
        ).values_list("id", "source_url", "checksum")
    )
    delta = ChecksumDelta(existing)
    decision = delta.decide(source_url, cs)

    if decision == "SKIP":
        log.info("ingest skip dept=%s url=%s (checksum match)", department_id, source_url)
        # find the existing row id so the caller knows the doc id
        for doc_id, url, doc_cs in existing:
            if url == source_url and doc_cs == cs:
                return IngestionResult(document_id=doc_id, new=False, chunk_count=0)
        # shouldn't happen — defensive
        raise RuntimeError("delta=SKIP but no matching row found")

    # 2. detect language if not given
    lang = language_hint or language_detect(text)
    log.info("ingest dept=%s url=%s lang=%s checksum=%s", department_id, source_url, lang, cs[:8])

    # 3. chunk
    df = clean_to_dataframe(text)
    chunks = chunk_paragraphs(df["paragraph"].tolist())

    # 4. embed — batched
    embedder = get_embedder()
    chunk_texts = [c.text for c in chunks]
    embeddings = embedder.embed(chunk_texts) if chunk_texts else []

    # 5. write — supersede prior rows for the same url
    with transaction.atomic():
        prior_rows = list(
            Document.objects.filter(
                department_id=department_id, source_url=source_url, superseded_by__isnull=True
            )
        )
        superseded_id = None
        if prior_rows:
            # bumping version off the latest
            latest = max(prior_rows, key=lambda d: d.version)
            new_version = latest.version + 1
            # create new doc, point old ones at it
            doc = Document.objects.create(
                department_id=department_id,
                title=title,
                source_url=source_url,
                checksum=cs,
                version=new_version,
            )
            for p in prior_rows:
                p.superseded_by = doc
                p.save(update_fields=["superseded_by"])
            superseded_id = latest.id
        else:
            doc = Document.objects.create(
                department_id=department_id,
                title=title, source_url=source_url, checksum=cs, version=1,
            )

        # 6. write chunks in bulk — order matches chunk_index
        if chunks:
            objs = [
                DocumentChunk(
                    document=doc,
                    department_id=department_id,
                    chunk_index=c.index,
                    chunk_text=c.text,
                    embedding=e,
                )
                for c, e in zip(chunks, embeddings)
            ]
            DocumentChunk.objects.bulk_create(objs, batch_size=500)

    log.info(
        "ingest done dept=%s url=%s chunks=%d new=%s",
        department_id, source_url, len(chunks), decision,
    )
    return IngestionResult(
        document_id=doc.id, new=True, chunk_count=len(chunks),
        superseded_id=superseded_id,
    )

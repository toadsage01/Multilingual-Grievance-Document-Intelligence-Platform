"""Document + chunk models.

The chunk's department_id is intentionally denormalized from the document
table — that lets the RLS policy filter by tenant without an extra join,
which matters at HNSW search time.

Embeddings live in a `VectorField` from pgvector.django. Dimension is
pinned to 768 to match multilingual-e5-base; if a different model is
swapped in via EMBEDDING_MODEL_NAME, this column has to be re-migrated.
"""
import uuid
from django.db import models
from pgvector.django import VectorField

from apps.tenancy.models import Department


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="documents"
    )
    title = models.CharField(max_length=500)
    source_url = models.TextField(blank=True)
    checksum = models.CharField(max_length=64)  # sha256 of normalized raw text
    version = models.PositiveIntegerField(default=1)
    superseded_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL,
        related_name="supersedes",
    )
    ingested_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "documents"
        # idempotent re-ingestion: same dept + url + checksum => skip
        constraints = [
            models.UniqueConstraint(
                fields=["department", "source_url", "checksum"],
                name="uniq_doc_dept_url_checksum",
            ),
        ]
        indexes = [
            # partial index so live retrievals only see non-superseded docs
            models.Index(
                name="idx_documents_dept",
                fields=["department"],
                condition=models.Q(superseded_by__isnull=True),
            ),
        ]

    def __str__(self) -> str:
        return f"{self.title} (v{self.version})"


class DocumentChunk(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    document = models.ForeignKey(
        Document, on_delete=models.CASCADE, related_name="chunks"
    )
    department = models.ForeignKey(
        Department, on_delete=models.CASCADE, related_name="chunks"
    )
    chunk_index = models.PositiveIntegerField()
    chunk_text = models.TextField()
    embedding = VectorField(dimensions=768)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "document_chunks"
        indexes = [
            # HNSW is created via raw SQL migration — Django's index ops
            # don't speak vector_cosine_ops cleanly. See 0002 chunk migration.
            models.Index(name="idx_chunks_dept", fields=["department"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["document", "chunk_index"],
                name="uniq_chunk_doc_index",
            ),
        ]

    def __str__(self) -> str:
        return f"chunk {self.chunk_index} of {self.document_id}"

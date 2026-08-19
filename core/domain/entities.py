"""Domain entities. Plain dataclasses — no Django, no ORM, no network.

This is what keeps retrieval/LLM logic unit-testable without spinning up
Postgres or hitting an embedding API. If a piece of code needs these, it
imports from here, not from apps.* models.
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional
import uuid


@dataclass(frozen=True)
class Chunk:
    """A retrievable text chunk with its embedding."""
    id: uuid.UUID
    document_id: uuid.UUID
    department_id: uuid.UUID
    chunk_index: int
    chunk_text: str
    embedding: tuple[float, ...]  # tuple not list, so it's hashable for tests
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk plus its similarity score — what the retrieval layer returns."""
    chunk_id: uuid.UUID
    document_title: str
    chunk_text: str
    score: float  # cosine similarity in [0, 1] — higher is better


@dataclass
class Grievance:
    """Current-state snapshot of a grievance record."""
    id: uuid.UUID
    department_id: uuid.UUID
    conversation_id: Optional[uuid.UUID]
    status: str
    category: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass
class StatusTransition:
    """Append-only audit row."""
    id: uuid.UUID
    grievance_id: uuid.UUID
    from_status: Optional[str]
    to_status: str
    confidence_score: Optional[float]
    note: Optional[str]
    actor: str
    created_at: datetime


@dataclass
class GenerationResult:
    """Output of an LLM streaming call — collected for tests."""
    text: str
    finish_reason: str
    cited_chunk_ids: list[uuid.UUID] = field(default_factory=list)
    confidence_score: Optional[float] = None

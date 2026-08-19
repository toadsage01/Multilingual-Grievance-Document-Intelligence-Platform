"""Integration tests — these need a real Postgres+pgvector.

Run locally with:
    docker compose up -d
    pytest tests/integration -v

In CI, GitHub Actions starts a pgvector service container before pytest.

These tests are the actual proof of the two hardest claims:
  - RLS prevents cross-tenant retrieval even when the ORM query forgets
    the WHERE clause (the only honest test of multi-tenancy)
  - idempotent re-ingestion doesn't re-embed an unchanged document
  - the grievance lifecycle state machine + append-only audit trail
  - SSE streaming persists the user message even on LLM failure
"""
import uuid
import pytest
from unittest import mock

from apps.tenancy.middleware import TenantContextMiddleware
from apps.ingestion.services import ingest_text
from apps.grievances.services import (
    appeal, file_grievance, reopen, resolve, route_after_classification, transition,
)
from apps.grievances.models import Grievance
from core.exceptions import StateTransitionError


def _set_tenant(department_id):
    """Helper: set app.current_tenant on the live connection."""
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("SET app.current_tenant = %s", [str(department_id)])


def _reset_tenant():
    from django.db import connection
    with connection.cursor() as cur:
        cur.execute("RESET app.current_tenant")


# ----------------------------------------------------------------------
# Cross-tenant RLS isolation — the load-bearing test
# ----------------------------------------------------------------------
@pytest.mark.django_db
class TestRLSIsolation:
    def test_cross_tenant_query_returns_zero_rows(self, tenant_a, tenant_b):
        """The actual RLS proof: even an unfiltered ORM query leaks nothing."""
        # create a chunk owned by tenant_b
        from apps.ingestion.models import Document, DocumentChunk
        doc = Document.objects.create(
            department=tenant_b, title="b-only doc",
            source_url="http://b/1", checksum="b" * 64, version=1,
        )
        DocumentChunk.objects.create(
            document=doc, department=tenant_b,
            chunk_index=0, chunk_text="private to tenant b",
            embedding=[0.1] * 768,
        )

        # now act as tenant_a — bypass the ORM filter entirely
        _set_tenant(tenant_a.id)
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("SELECT count(*) FROM document_chunks")
            row = cur.fetchone()
        assert row[0] == 0, "RLS leaked tenant_b rows to tenant_a"
        _reset_tenant()

    def test_unfiltered_orm_query_also_returns_zero(self, tenant_a, tenant_b):
        """Same proof via the ORM — proves it isn't a cursor-only quirk."""
        from apps.ingestion.models import DocumentChunk, Document
        doc = Document.objects.create(
            department=tenant_b, title="b doc",
            source_url="http://b/2", checksum="b2" * 32, version=1,
        )
        DocumentChunk.objects.create(
            document=doc, department=tenant_b,
            chunk_index=0, chunk_text="tenant b only",
            embedding=[0.2] * 768,
        )
        _set_tenant(tenant_a.id)
        # No .filter(department=...) — just .all()
        assert list(DocumentChunk.objects.all()) == []
        assert list(Document.objects.all()) == []
        _reset_tenant()

    def test_tenant_sees_own_rows(self, tenant_a, tenant_b):
        from apps.ingestion.models import Document, DocumentChunk
        doc = Document.objects.create(
            department=tenant_a, title="a doc",
            source_url="http://a/1", checksum="a" * 64, version=1,
        )
        DocumentChunk.objects.create(
            document=doc, department=tenant_a,
            chunk_index=0, chunk_text="a's chunk",
            embedding=[0.3] * 768,
        )
        _set_tenant(tenant_a.id)
        assert DocumentChunk.objects.count() == 1
        _reset_tenant()


# ----------------------------------------------------------------------
# Idempotent re-ingestion
# ----------------------------------------------------------------------
@pytest.mark.django_db
class TestIdempotentIngestion:
    def test_run_twice_same_text_skips_second(self, tenant_a, monkeypatch):
        """Same content -> second call is SKIP, no new chunks."""
        # mock the embedder so we don't load the model
        def fake_embed(texts, normalize=True):
            return [[0.5] * 768 for _ in texts]
        def fake_embed_query(text):
            return [0.5] * 768
        from apps.ingestion import embeddings as emb_mod
        monkeypatch.setattr(emb_mod.SentenceTransformerEmbedder, "embed", fake_embed)
        monkeypatch.setattr(emb_mod.SentenceTransformerEmbedder, "embed_query", fake_embed_query)

        text = "Hello world.\n\nThis is a test circular about scholarships."
        r1 = ingest_text(
            department_id=tenant_a.id, title="doc", raw_text=text,
            source_url="http://example.com/c1",
        )
        assert r1.new is True
        assert r1.chunk_count > 0

        r2 = ingest_text(
            department_id=tenant_a.id, title="doc", raw_text=text,
            source_url="http://example.com/c1",
        )
        assert r2.new is False, "second ingest should be skipped"
        assert r2.chunk_count == 0
        # same doc id both times
        assert r1.document_id == r2.document_id

    def test_changed_text_supersedes_old(self, tenant_a, monkeypatch):
        def fake_embed(texts, normalize=True):
            return [[0.5] * 768 for _ in texts]
        def fake_embed_query(text):
            return [0.5] * 768
        from apps.ingestion import embeddings as emb_mod
        monkeypatch.setattr(emb_mod.SentenceTransformerEmbedder, "embed", fake_embed)
        monkeypatch.setattr(emb_mod.SentenceTransformerEmbedder, "embed_query", fake_embed_query)

        r1 = ingest_text(
            department_id=tenant_a.id, title="v1", raw_text="original content",
            source_url="http://example.com/c2",
        )
        # change the text — new checksum, should supersede
        r2 = ingest_text(
            department_id=tenant_a.id, title="v2", raw_text="original content (revised)",
            source_url="http://example.com/c2",
        )
        assert r2.new is True
        assert r2.document_id != r1.document_id
        assert r2.superseded_id == r1.document_id


# ----------------------------------------------------------------------
# Grievance lifecycle end-to-end
# ----------------------------------------------------------------------
@pytest.mark.django_db
class TestGrievanceLifecycle:
    def test_full_lifecycle_loop(self, tenant_a):
        """SUBMITTED -> CLASSIFIED -> ROUTED -> ESCALATED -> RESOLVED -> APPEALED -> REOPENED -> ROUTED"""
        g = file_grievance(department_id=tenant_a.id, category="scholarship")
        assert g.status == "SUBMITTED"
        # history has the SUBMITTED row
        assert g.history.count() == 1

        # auto-route with low confidence -> ESCALATED
        result = route_after_classification(g.id, confidence=0.40, threshold=0.72)
        assert result.to_status == "ESCALATED"
        g.refresh_from_db()
        assert g.status == "ESCALATED"

        # history should have: SUBMITTED, CLASSIFIED, ROUTED, ESCALATED
        assert g.history.count() == 4

        # resolve -> APPEALED -> REOPENED -> ROUTED
        resolve(g.id)
        appeal(g.id)
        reopen(g.id)
        transition(g.id, "ROUTED", note="routed again")

        g.refresh_from_db()
        assert g.status == "ROUTED"
        assert g.history.count() == 8

    def test_illegal_transition_raises(self, tenant_a):
        from apps.grievances.models import Grievance
        g = file_grievance(department_id=tenant_a.id)
        with pytest.raises(StateTransitionError):
            # SUBMITTED -> RESOLVED is illegal
            transition(g.id, "RESOLVED")

    def test_high_confidence_routes_to_answered(self, tenant_a):
        g = file_grievance(department_id=tenant_a.id)
        result = route_after_classification(g.id, confidence=0.95, threshold=0.72)
        assert result.to_status == "ANSWERED"

    def test_history_records_actor_and_confidence(self, tenant_a):
        g = file_grievance(department_id=tenant_a.id)
        route_after_classification(g.id, confidence=0.45, threshold=0.72)
        # the ESCALATED transition row should carry the confidence
        esc = g.history.filter(to_status="ESCALATED").first()
        assert esc is not None
        assert esc.confidence_score == 0.45
        assert esc.actor == "system"


# ----------------------------------------------------------------------
# SSE persistence-on-failure
# ----------------------------------------------------------------------
@pytest.mark.django_db
class TestChatPersistenceOnFailure:
    def test_user_message_persisted_even_when_providers_down(self, tenant_a, monkeypatch):
        from apps.chat.models import Conversation, Message
        from apps.chat.services import start_conversation
        from core.exceptions import ProviderUnavailable

        conv = start_conversation(department_id=tenant_a.id, language_code="en")

        # force both providers to fail
        from apps.llm.breaker import CircuitBreaker, FallbackChain
        from core.interfaces import LLMProvider

        class DeadProvider(LLMProvider):
            @property
            def name(self): return "dead"
            def health(self): return False
            async def stream_completion(self, sp, up, ctx):
                raise RuntimeError("provider down")
                yield ""

        chain = FallbackChain(
            CircuitBreaker(DeadProvider(), failure_threshold=3, cooldown_seconds=60),
            CircuitBreaker(DeadProvider(), failure_threshold=3, cooldown_seconds=60),
        )
        monkeypatch.setattr("apps.chat.services.get_chain", lambda: chain)
        # also stub retrieval so we don't hit the embedding model
        monkeypatch.setattr("apps.chat.services.search", lambda q: [])

        from apps.chat.services import stream_turn_sync
        events = list(stream_turn_sync(
            conversation=conv, user_text="my scholarship was rejected unfairly",
        ))

        # the user message was saved regardless
        assert Message.objects.filter(conversation=conv, role="user").count() == 1

        # the assistant message was saved with the "temporarily unavailable" text
        asst = Message.objects.filter(conversation=conv, role="assistant").first()
        assert asst is not None
        assert "temporarily unavailable" in asst.content.lower()

        # the done event reported degraded=True
        done = [e for e in events if e[0] == "done"]
        assert done
        assert done[0][1]["degraded"] is True

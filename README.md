# Setu

> _Setu (सेतु / சேது) — a bridge._ A multilingual grievance and document
> intelligence platform that lets citizens ask questions of their
> government departments in their own language, get grounded answers,
> and file tracked grievances when an answer isn't enough.

Setu is a standalone, multi-tenant Django service where each **tenant**
is a department, ministry, or institution. Citizens submit natural-
language queries in any supported Indian language; the system
classifies intent, retrieves grounded answers from the department's
own corpus via pgvector semantic search, streams the answer back
token-by-token, and — when the query is a grievance rather than an
information request — opens a tracked record that moves through a
defined lifecycle with a full audit trail.

This is **not** a thin wrapper around an OpenAI call. The value lives
in the retrieval grounding, the multi-tenant isolation, the lifecycle
state machine, and the multilingual handling done correctly.

---

## Inspiration

The problem space this project demonstrates is borrowed from real
Indian-government hackathons and live production systems:

- **DARPG Hackathon 2024, Problem Statement 2** — an ML-driven,
  ministry-specific chatbot to help citizens resolve queries about
  filing a grievance on the [CPGRAMS](https://pgportal.gov.in/) portal.
- **DARPG Hackathon 2024, Problem Statement 1** — ML topic clustering
  for auto-categorization of grievance reports to the correct
  last-mile officer. ([source](https://event.data.gov.in/challenge/darpg-challenge-2024/))
- The production successor to this problem space, **Samadhan Didi**,
  is a live multilingual voice-enabled CPGRAMS chatbot covering 22
  Indian languages, built on [Bhashini](https://bhashini.gitbook.io/bhashini-apis).
  This is real precedent — Setu is architected toward it, not away
  from it.
- Recurring SIH theme: a multilingual institutional chatbot for
  deflecting routine student queries and providing 24/7 access.

Setu is **a standalone architectural demonstration of this problem
class**. It does not integrate with the real CPGRAMS API.

---

## Architecture

Modular monolith with an Onion/Clean core internally. Not
microservices — at this scale (single team, single deploy target,
100k documents) microservices add operational overhead with no
corresponding benefit. CQRS is applied as a **code-organization
pattern** (separate command and query handlers with different
consistency needs), not as separate read/write databases.

```
setu/
├── manage.py
├── requirements.txt
├── .env.example
├── docker-compose.yml            # local Postgres+pgvector+Redis for dev
├── render.yaml                   # Render blueprint (auto-deploys from main)
├── config/                       # Django project settings (split: base/dev/prod)
│   ├── settings/
│   ├── urls.py
│   └── asgi.py                   # ASGI for SSE streaming support
├── core/                         # Onion center: domain models, interfaces, NO Django imports
│   ├── domain/                   # dataclasses + state machine
│   ├── interfaces/               # abstract base classes: LLMProvider, EmbeddingProvider, Translator
│   └── exceptions.py
├── apps/
│   ├── tenancy/                  # Department model + RLS policy + tenant middleware
│   ├── ingestion/                # Pandas/NumPy cleaning, chunking, embedding, checksum delta
│   ├── retrieval/                 # semantic search query handlers (the "Q" in CQRS)
│   ├── chat/                      # conversation model, SSE streaming view
│   ├── grievances/                # lifecycle state machine, status history (the "C" in CQRS)
│   ├── translation/               # query/response translation abstraction
│   ├── llm/                       # provider abstraction + fallback chain + circuit breaker
│   └── api/                       # DRF serializers, viewsets, routers — thin
├── tests/
│   ├── unit/                      # no DB, no network — runs in ~0.1s
│   └── integration/               # needs Postgres+pgvector
├── infra/terraform/               # Neon + Upstash + Render as code
└── .github/workflows/ci.yml
```

**Dependency rule:** `apps/*` depend on `core/`, never the reverse.
`core/` has zero Django imports — this is what makes retrieval/LLM
logic unit-testable without a database or running server. Verify
with:

```bash
python -c "
import sys; sys.modules['django'] = None  # block django import
from core.domain import can_transition, auto_route_decision
from core.interfaces import LLMProvider, EmbeddingProvider, Translator
print('core layer has no Django dependency')
"
```

### Request flow

```
citizen ── HTTP ──▶ Django + TenantContextMiddleware
                          │ (sets app.current_tenant on the connection)
                          ▼
                    Conversation (DB row, language_code logged)
                          │
                          ▼
                    apps.retrieval.search()
                          │ 1. embed query (multilingual-e5-base, native language)
                          │ 2. pgvector cosine top-K against document_chunks
                          │ 3. cache result in Redis (10 min TTL)
                          ▼
                    apps.llm.breaker.FallbackChain
                          │ 1. try primary (Groq)  → on failure →
                          │ 2. try fallback (Gemini)
                          │ 3. if both down → ProviderUnavailable,
                          │    persist the user message anyway,
                          │    return "saved, officer will follow up"
                          ▼
                    StreamingHttpResponse (text/event-stream)
                          │
                          ▼
                    if confidence < threshold → file a Grievance,
                       auto-route to ESCALATED (human queue)
```

### Multi-tenancy

Postgres **Row-Level Security**, not schema-per-tenant. RLS gives
real isolation guarantees with one schema — schema-per-tenant
doesn't scale past a few dozen tenants operationally (migrations
must run N times).

Every tenant-scoped table (`documents`, `document_chunks`,
`conversations`, `messages`, `grievances`, `grievance_status_history`)
has an RLS policy that filters by the connection-local
`app.current_tenant` setting, which `TenantContextMiddleware` sets
before any request-scoped query runs. The proof this works is the
integration test `TestRLSIsolation::test_cross_tenant_query_returns_zero_rows`,
which issues an **unfiltered** `SELECT * FROM document_chunks` and
asserts zero rows from a different tenant — the only honest test of
multi-tenancy.

### Grievance lifecycle

A Saga-lite, event-logged state machine. The `grievances` table holds
current state; `grievance_status_history` is append-only and records
every transition (actor, from_state, to_state, confidence_score,
note, created_at). Each transition is one DB transaction: update
current state + insert history row, or neither happens.

```
SUBMITTED → CLASSIFIED → ROUTED → ANSWERED   (auto, high-confidence RAG)
                                 → ESCALATED (low-confidence → human queue) → RESOLVED
RESOLVED → APPEALED → REOPENED → ROUTED  (loops back into the routing step)
```

The `auto_route_decision(confidence, threshold)` function in
`core/domain/state_machine.py` is the small piece of business logic
that makes the system honest about its own uncertainty: a
low-confidence auto-classification gets caught by the threshold and
escalated rather than silently misrouted — the actual
failure-mode-handling enterprise reviewers look for.

---

## The multilingual retrieval decision (read this if you read one section)

**Do not** do `translate-query-to-English → embed → retrieve →
translate-answer-back`. That loses procedural/legal nuance in the
translation hop and is the #1 way these systems silently degrade.

**This system** does this instead:

1. Documents and incoming queries are embedded directly in their
   native language using **`intfloat/multilingual-e5-base`** via
   `sentence-transformers`, run locally/offline (CPU-friendly, no
   API cost, no rate limit). Model card:
   https://huggingface.co/intfloat/multilingual-e5-base
2. Retrieval happens in **shared embedding space** — no translation
   needed for search to work correctly. A Hindi query matches Hindi
   chunks; a Tamil query matches Tamil chunks.
3. Only the final generation step *may* need translation, and only
   if the LLM's native output language quality for that Indian
   language is weak. We **prompt the LLM directly in the target
   language first** — modern LLMs handle major Indian languages
   reasonably. If quality is insufficient, we fall back to explicit
   translation via the Bhashini pipeline, implemented as a swappable
   `Translator` interface in `core/interfaces/` (not hardwired).
4. We log `language_code` on **every** message — this is what makes
   per-language answer-quality metrics defensible instead of made up.

This is the single most interview-worthy design decision in the
system. It exists because the obvious "translate to English"
short-cut looks clever in a demo and degrades silently in
production.

---

## API

OpenAPI 3.1 schema is auto-generated at `/api/schema/`, with a
Swagger UI at `/api/docs/`. Minimum endpoints:

| Method | Path | Purpose |
|---|---|---|
| `GET`  | `/api/v1/departments/` | List tenants (public) |
| `POST` | `/api/v1/conversations/` | Start a conversation (department_id, language_code) |
| `POST` | `/api/v1/conversations/{id}/messages/stream/` | SSE endpoint. Submit a message, stream tokens back as `text/event-stream` |
| `GET`  | `/api/v1/search/` | Direct semantic search (department_id, query, language) — debugging surface |
| `POST` | `/api/v1/ingestion/documents/` | Upload/register a document for ingestion (admin auth) |
| `GET`  | `/api/v1/ingestion/status/{batch_id}/` | Poll ingestion progress |
| `POST` | `/api/v1/grievances/` | File a grievance from a conversation |
| `GET`  | `/api/v1/grievances/{id}/` | Get current status + full history |
| `POST` | `/api/v1/grievances/{id}/appeal/` | Trigger `RESOLVED → APPEALED` |
| `POST` | `/api/v1/grievances/{id}/route/` | Mark CLASSIFIED, then route based on confidence |

SSE response format:

```
event: ready
data: {}

event: citation
data: {"chunk_id": "...", "document_title": "Scholarship SOP 2024", "score": 0.84}

event: token
data: {"text": "partial chunk"}

event: done
data: {"message_id": "...", "confidence_score": 0.84, "degraded": false, "cited_chunk_ids": ["..."]}
```

All inbound payloads are validated by strict DRF serializers —
unknown fields are rejected, not silently dropped.

---

## Local development

Prerequisites: Docker (for the local Postgres+pgvector and Redis),
Python 3.12, and ~2GB disk for the multilingual embedding model
(cache-able across runs).

```bash
# 1. clone + venv
git clone https://github.com/toadsage01/setu.git
cd setu
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2. bring up the local stack
docker compose up -d

# 3. env vars
cp .env.example .env
# edit .env to set DJANGO_SECRET_KEY and your GROQ_API_KEY / GEMINI_API_KEY
# (at least one provider is needed for the chat endpoint to actually answer)

# 4. migrate + seed
python manage.py migrate
python manage.py seed_departments
python manage.py ingest_corpus --seed       # ~42 sample circulars across edu/rwy/health

# 5. run
python manage.py runserver
```

Sanity check:

```bash
# list departments
curl http://localhost:8000/api/v1/departments/ | jq

# semantic search (pick a department id from the list above)
curl "http://localhost:8000/api/v1/search/?department_id=<id>&query=how%20do%20I%20apply%20for%20a%20scholarship%3F" | jq

# start a conversation
curl -X POST http://localhost:8000/api/v1/conversations/ \
  -H 'Content-Type: application/json' \
  -d '{"department_id":"<id>","language_code":"en"}'

# stream a turn (use the conversation id from the previous response)
curl -N -X POST http://localhost:8000/api/v1/conversations/<id>/messages/stream/ \
  -H 'Content-Type: application/json' \
  -d '{"text":"My scholarship was rejected. How do I appeal?"}'
```

### Tests

```bash
# unit tests — pure Python, no DB, ~0.1s
pytest tests/unit

# integration tests — needs Postgres+pgvector + Redis
docker compose up -d
pytest tests/integration
```

The integration tests are the **actual proof** of the two hardest
claims:

- `TestRLSIsolation::test_cross_tenant_query_returns_zero_rows` —
  issues an unfiltered `SELECT *` and asserts zero leakage.
- `TestIdempotentIngestion::test_run_twice_same_text_skips_second` —
  ingests the same text twice and asserts the second call is a no-op.
- `TestGrievanceLifecycle::test_full_lifecycle_loop` — walks the
  full `SUBMITTED → … → REOPENED → ROUTED` cycle and checks every
  transition landed in the audit log.
- `TestChatPersistenceOnFailure::test_user_message_persisted_even_when_providers_down`
  — proves a total provider outage never loses the citizen's input.

---

## Deployment

Production goes to **Render** via the `render.yaml` blueprint. On
every push to `main`, Render auto-deploys: install requirements →
`python manage.py migrate` (release command, runs before traffic
cutover) → `gunicorn config.wsgi`.

Infrastructure (Neon Postgres + Upstash Redis + Render service) is
provisioned via Terraform in `infra/terraform/`. LLM provider API
keys are **not** in Terraform state — they're injected via the Render
dashboard or `render env set` as secrets.

See `infra/terraform/README.md` for usage. The one manual step is
enabling the `vector` extension on Neon:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

---

## Ingestion pipeline

- **Cleaning** (`apps/ingestion/cleaning.py`): Pandas/NumPy-based
  normalisation (NFKC + whitespace collapse), boilerplate-header
  stripping (lone page numbers, ministry footers, citation markers),
  cheap Unicode-script language detection.
- **Chunking** (`apps/ingestion/chunking.py`): paragraph-aware
  accumulation to ~500 tokens with a ~50-token overlap from the
  previous chunk's tail paragraphs. We don't blindly split on
  character count — that destroys procedural context.
- **Idempotent re-ingestion** (`apps/ingestion/checksum.py`): before
  embedding, we compute `sha256` of the normalised raw text. If a
  document with the same `(department_id, source_url, checksum)`
  exists, we skip embedding entirely. If the checksum differs, we
  insert a new `documents` row and supersede the old one — we do not
  delete history, because past conversations may have cited the old
  version.
- **Batched embedding** (`apps/ingestion/embeddings.py`): the
  sentence-transformers `encode()` call is batched (batch_size=32)
  — the per-chunk loop is roughly an order of magnitude slower and
  has no upside.
- **Background execution**: `django-rq` against the same Upstash
  Redis instance. Celery + a separate broker would be overkill at
  this scale.

---

## LLM provider layer

`core/interfaces/providers.py` declares `LLMProvider.stream_completion()`.
Two implementations live in `apps/llm/providers.py`:

- **`GroqProvider`** (primary) — OpenAI-compatible chat completions
  endpoint, streaming via SSE.
- **`GeminiProvider`** (fallback) — Gemini `streamGenerateContent`
  with `alt=sse`.

The chain is wrapped in a **circuit breaker**
(`apps.llm.breaker.CircuitBreaker`): after `LLM_CIRCUIT_FAILURE_THRESHOLD`
consecutive failures on the primary within a rolling window, the
circuit trips open and refuses calls for `LLM_CIRCUIT_COOLDOWN_SECONDS`,
then half-opens for a single probe. If the primary is open, we fall
through to the fallback. **If both are down**, the chat endpoint
raises `ProviderUnavailable`, the user message is still persisted,
and the assistant message is a clear "service temporarily
unavailable, your query has been saved (reference: <message_id>)"
reply — never an empty stream.

---

## Caching and throttling

Redis (Upstash free tier) backs both the cache and the throttling
counters. Semantic-search results are cached keyed on
`(department_id, language_code, normalised_query_hash)` for a short
TTL (~10 minutes) — repeat queries during a grievance surge don't
re-hit the embedding model or DB.

DRF `AnonRateThrottle` is wired in `config/settings/base.py` and
protects the free-tier LLM quota from abuse. Prod is more
restrictive than dev.

---

## Model options

The system is **model-agnostic by design**. The following are
configurable via environment variables — swap them as new releases
come out, no code changes required.

| Layer | Default | Alternatives / Notes |
|---|---|---|
| Embeddings | `intfloat/multilingual-e5-base` (768-dim) | `intfloat/multilingual-e5-large` (1024-dim, requires a schema migration) or any model that produces 768-dim vectors |
| Primary LLM | Groq `llama-3.1-8b-instant` | Any Groq-supported model — `mixtral-8x7b-32768`, `llama-3.3-70b-versatile`, etc. |
| Fallback LLM | Gemini `gemini-1.5-flash` | `gemini-1.5-pro`, `gemini-2.0-flash-exp` |
| Translation | Passthrough (no-op) | Bhashini ULCA pipeline — wired automatically if `BHASHINI_API_KEY` + `BHASHINI_USER_ID` are set |

LLM provider API keys are required for at least one provider; if both
are missing the chat endpoint degrades gracefully (see "LLM provider
layer" above) but you still get the search surface.

---

## Known limitations

- **No real CPGRAMS API integration.** This is a standalone
  architectural demonstration of the same problem class. A real
  deployment would add a `CPGRAMSClient` in `apps/grievances/`
  that forwards `ESCALATED` grievances to CPGRAMS.
- The seed corpus is **synthetic** — 42 hand-written circulars and
  FAQs across Education, Railways, and Health. Real ingestion should
  replace this with the actual published circulars of the relevant
  department, ideally via a scheduled re-ingest that runs the
  checksum delta path.
- The classifier that picks `category` for a grievance is currently
  a stub — the endpoint accepts a manual category. A real classifier
  (the DARPG Hackathon PS1 ask) would be a small fine-tuned model on
  top of the same multilingual embedding.
- The Bhashini translator is wired but not exercised in the default
  config — we prompt the LLM in the target language first, which
  covers major Indian languages acceptably. Bhashini is a secondary
  layer for languages the LLM handles poorly.
- RLS is enforced at the database layer, but the Django ORM's
  `connection` is shared across the request thread. Concurrent
  requests with different tenants on the same worker process could
  in principle race on `SET app.current_tenant`. The middleware
  sets the value at the start of each request; for high-concurrency
  multi-tenant deployments, switch to connection pooling that
  isolates by tenant or to a per-request connection.
- Fine-grained RBAC for the admin endpoints (file a grievance
  on behalf of a citizen, force-transition a grievance) is via
  Django's `IsAdminUser`. A real production deploy would add
  role-based access scoped to a specific department.

---

## License

MIT. See `LICENSE` if applicable. Sample documents in
`apps/ingestion/seed/` are synthetic and clearly marked as such in
the README; they are not real government circulars and should not
be cited as such.

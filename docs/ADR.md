# Orbit — Architecture Decision Records

## ADR-001: Two-Service Design (CRM + Channel)

**Status:** Accepted (revised: real delivery replaced the original simulator)

**Context:**
Messaging providers accept a send request and report the outcome asynchronously. Keeping delivery in a separate process isolates provider credentials, retries and rate limits from the core CRM.

**Decision:**
- The CRM launch task calls `POST http://channel:8001/send` for each message; the channel service returns `202 Accepted` immediately.
- A channel Celery worker sends the message (SMTP for email, Twilio REST for SMS/WhatsApp) and calls `POST http://crm:8000/api/v1/receipts` with a single `delivered` or `failed` receipt, including the provider's failure reason.
- Opens and clicks are never inferred by the gateway. They are recorded only when a recipient loads the open pixel or clicks a tracked link (`/api/v1/track/*`).
- `services/delivery_events.record_event` is the single place that updates a communication and rolls counts up to its campaign; events are deduplicated per communication and status never regresses.

**Consequences:**
- Two Celery workers are required.
- `PUBLIC_BASE_URL` must be publicly reachable for engagement tracking.
- Campaign post-mortems are generated on demand, because real engagement keeps accruing after delivery completes.

---

## ADR-002: Groq + Llama 3.3 70B for all LLM features

**Status:** Accepted

**Context:**
Need a fast, capable model that supports function/tool calling on a free tier.

**Decision:** Groq API with the `llama-3.3-70b-versatile` model (configurable via `GROQ_MODEL`).
- ~200 tokens/second throughput
- OpenAI-compatible function calling
- Free tier: 30 requests/minute

**Consequences:**
- Personalised campaigns batch 20 customers per call; very large campaigns will hit rate limits.
- At scale: upgrade to a paid tier or self-host via vLLM.

---

## ADR-003: Polling for live campaign stats

**Status:** Accepted (supersedes "Supabase Realtime for live campaign stats")

**Context:**
The campaign tracker originally subscribed to Supabase Realtime from the browser using the public anon key. That only works if the `campaigns` table is readable by the anon role, and because no Row Level Security was configured, every table was readable and writable by anyone holding the (public) key. The list page's subscription also re-ran on every update, causing repeated refetches.

**Decision:**
- The frontend polls `GET /api/v1/campaigns/{id}/stats` every 5 seconds while a tracker is open.
- The browser no longer receives any Supabase credentials; all data access goes through the CRM API.
- Migration `006_security_hardening.sql` enables RLS on every table and restricts RPCs to `service_role`.

**Consequences:**
- Up to 5 seconds of latency on live counters, and one lightweight request per open tracker every 5 seconds.
- At scale: push updates from the CRM over SSE/WebSockets, or use authenticated Realtime with per-org RLS policies.

---

## ADR-004: RFM Scoring in pandas (not DB)

**Status:** Accepted

**Context:**
Need churn risk scores for all customers, always fresh.

**Decision:** pandas in-memory computation via Celery tasks.
- Works up to ~1M customers
- Nightly batch job (02:00 UTC) plus re-scoring whenever orders or customers are created or imported

**Consequences:**
- At scale (>1M): push computation to PostgreSQL window functions or dbt.

---

## ADR-005: Multi-tenancy via `org_id`

**Status:** Accepted (demo-grade authentication)

**Context:**
Multiple brands share one deployment and must only see their own data.

**Decision:**
- Every tenant-owned table carries an `org_id` foreign key to `organizations`.
- The frontend sends the signed-in organization's UUID as `X-Org-Id`; `deps.get_org` turns it into an `OrgContext` whose `scope()` filters reads and `stamp()` sets `org_id` on inserts. `X-Org-Id: ALL` (or no header) is the platform-admin view.
- Uniqueness of customer phone/email/external ID is enforced per organization.

**Consequences:**
- The header is **not authenticated**; any client can claim any organization. Production use requires signed sessions (e.g. JWTs issued at login and verified in `get_org`) and removal of the fixed admin credentials.

---

## ADR-006: Local embeddings + pgvector for semantic search

**Status:** Accepted

**Context:**
Marketers want to find customers by intent ("price-sensitive skincare lovers"), which structured filters can't express, without paying per-call embedding API costs.

**Decision:**
- Each customer is rendered into a short natural-language profile document and embedded locally with fastembed (`BAAI/bge-small-en-v1.5`, 384 dimensions, CPU/ONNX).
- Vectors live in `customers.embedding` with an HNSW cosine index; org-scoped `match_customers` / `similar_customers` RPCs perform the search.
- Retrieved customers also ground the Copilot and Strategist prompts (RAG).

**Consequences:**
- The ~130 MB model downloads on first use (cached in `.model_cache/`).
- Embeddings are refreshed on create/import; run `db/backfill_embeddings.py` after bulk changes made outside the API.

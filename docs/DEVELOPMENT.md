# Orbit — Development Guide

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Node.js | 18.17+ (20 recommended) |
| Redis | 7 (Docker is the easiest way to run it) |
| Supabase | A project with the `vector` extension available |
| Groq | An API key from console.groq.com (free tier works) |

## Environment

```bash
cp backend/crm/.env.example backend/crm/.env
cp backend/channel/.env.example backend/channel/.env
cp frontend/.env.local.example frontend/.env.local
```

| File | Required values | Notes |
|------|-----------------|-------|
| `backend/crm/.env` | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GROQ_API_KEY` | `PUBLIC_BASE_URL` must be reachable by email recipients for open/click tracking to work. |
| `backend/channel/.env` | `SMTP_*` and/or `TWILIO_*` | Channels without credentials show as "not configured" in the campaign wizard. |
| `frontend/.env.local` | `NEXT_PUBLIC_CRM_API_URL` | Defaults to `http://localhost:8000`. |

Each service only accepts the variables declared in its `config.py`; unknown keys in a `.env` file make the service fail at startup.

## Database

Run these in the Supabase SQL Editor, in order. Every migration is idempotent.

| Migration | Purpose |
|-----------|---------|
| `001_initial_schema.sql` | Core tables, indexes, `increment_campaign_counter` RPC |
| `002_organizations.sql` | Organization signup/login |
| `003_multi_tenancy.sql` | `org_id` on every table, per-org uniqueness, campaign feedback |
| `004_pgvector_rag.sql` | Embedding column, HNSW index, similarity RPCs |
| `005_real_delivery.sql` | Email opt-out, subject lines, CTA URLs |
| `006_security_hardening.sql` | RLS on all tables, backend-only RPCs |

Optional demo data (run from `backend/crm`, in this order):

```bash
python db/seed.py --clean --customers 500    # fake customers + orders
python db/backfill_demo_org.py                # assign them to the Demo org (login ORB-DEMO01 / demo1234)
python -c "from tasks.score_customers import batch_score_all_customers; batch_score_all_customers()"
python db/backfill_embeddings.py              # embed customers for Smart Search
```

To add a migration, create `NNN_description.sql` in `backend/crm/db/migrations/`, keep it idempotent (`IF NOT EXISTS`, `CREATE OR REPLACE`), and add it to the table above.

## Running locally

### Option A — Docker Compose + npm

```bash
docker compose up --build      # or: make up
cd frontend && npm install && npm run dev
```

### Option B — Windows launcher

Install dependencies once, from the repository root:

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
cd frontend; npm install; cd ..
```

(Per-service virtualenvs in `backend\crm\venv` and `backend\channel\venv` also work; the launcher prefers them when present.)

Then start and stop everything (each service opens in its own window):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\stop.ps1
```

The launcher uses `redis-server` from `PATH`, falls back to a portable build in `.redis\` (git-ignored), and otherwise tells you to start Redis with Docker.

### Option C — Manual (any OS)

```bash
docker run -d -p 6379:6379 redis:7-alpine

# CRM API + worker (two terminals, from backend/crm with its venv active)
uvicorn main:app --reload --port 8000
celery -A celery_app worker --loglevel=info --queues=crm

# Channel API + worker (two terminals, from backend/channel with its venv active)
uvicorn main:app --reload --port 8001
celery -A celery_app worker --loglevel=info --queues=channel

# Frontend
cd frontend && npm run dev
```

On Windows add `-P threads` to the Celery commands.

### Testing email without a provider

```bash
pip install aiosmtpd
python scripts/smtp_capture.py
```

Set `SMTP_HOST=localhost`, `SMTP_PORT=1025`, `SMTP_USE_TLS=false` in `backend/channel/.env`. Every sent email is saved to `.captured_mail/` instead of being delivered.

## Tests & linting

```bash
cd backend/crm     && python -m pytest && ruff check .
cd backend/channel && python -m pytest && ruff check .
cd frontend        && npm run lint && npm run type-check
```

Router tests fake the Supabase client with `backend/crm/tests/fakes.py`, so they need no network or credentials. `test_infra.py` checks real Redis/Supabase connectivity and skips when they're unavailable. CI runs all of the above on every push and pull request (`.github/workflows/ci.yml`).

## Coding conventions

### Python (`backend/`)
- Lint with `ruff check`.
- Route handlers stay thin; business logic lives in `services/`, Pydantic schemas in `models/`.
- Every tenant-owned query goes through `OrgContext.scope()` (reads) or `OrgContext.stamp()` (inserts).

### TypeScript (`frontend/`)
- Strict TypeScript (`strict: true`).
- All API calls go through `lib/api.ts`.
- Surface API failures to the user (toast / empty state) — never substitute placeholder data.

### Git workflow
- Branch naming: `feat/<feature>`, `fix/<bug>`, `chore/<task>`
- Conventional commit messages (`feat:`, `fix:`, `docs:`, `chore:`)
- Pull requests must pass CI.

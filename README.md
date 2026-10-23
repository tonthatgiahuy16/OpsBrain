# OpsBrain — Enterprise Second Brain for IT Operations

OpsBrain is an evidence-first knowledge infrastructure prototype for investigating IT incidents. Its target is to manage versioned documents in PostgreSQL, retrieve authorized evidence, build controlled context, and answer with citations—or abstain when the evidence is insufficient.

The project is implemented as small, tested vertical slices. Retrieval, ACL enforcement, Ollama generation, citations, and Obsidian integration described below are target architecture. They are not treated as implemented without matching runtime, test, migration, query, or evaluation evidence.

## 1. Current status

Latest merged repository checkpoint: `2026-10-23`, merge commit `17b0b5a` on `main` (Pull Request #15). The audit-event storage work described below is local feature-branch work and is not yet merged.

### Implemented and tested

- Docker Compose environment with FastAPI, PostgreSQL 16, and pgvector.
- Health endpoints: `GET /health` and `GET /health/liveness`.
- A sample vault with 12 synthetic documents linked around a Payment Service HTTP 503 scenario.
- A frontmatter validator for service, deployment, alert, incident, postmortem, and runbook documents.
- Accepted frontmatter, minimal API, and storage schema contracts.
- Alembic migrations and SQLAlchemy models for seven storage tables: `organizations`, `principals`, `documents`, `document_versions`, `ingestion_jobs`, `ingestion_job_items`, and `ingestion_job_attempts`.
- Canonical content hashing for validated Markdown.
- A sequential PostgreSQL ingestion lifecycle supporting initial creation, unchanged replay, body and metadata updates, immutable version history, parent-version lineage, review-state reset, and rollback behavior covered by tests.
- Database constraints for ingestion jobs, items, and attempts, including organization isolation, principal-scoped idempotency-key uniqueness, and terminal-state consistency.
- Audit-event storage foundation: the `audit_events` table, scoped foreign-key and check constraints, query indexes, an append-only trigger, and separate database roles for migrations and application runtime.

### Latest recorded evidence

The following results were recorded for implementation commit `3a1aa75` before it was merged through Pull Request #14:

- Local test suite: `63 passed`.
- Docker test suite: `63 passed`.
- Ingestion-job metadata tests: `4 passed`.
- PostgreSQL ingestion-job runtime tests: `3 passed`.
- `python -m alembic check`: `No new upgrade operations detected.`
- Alembic revision `b4b2a6760f09` was applied locally and reported as the migration head.

The following feature-branch results were recorded after recreating the local PostgreSQL volume and applying the current migration chain:

- Audit-event storage tests: `7 passed`.
- Alembic revision `5a58f7c5c515` was applied and reported as the migration head.
- The application role has only `SELECT` and `INSERT` privileges on `audit_events`; update and delete are rejected by database privileges. The append-only trigger also rejects update and delete for the table owner.
- `python -m alembic check`: `No new upgrade operations detected.`

These are repository-recorded results for the stated commit and development environment. They were not independently rerun as part of this README update.

### Not implemented or not yet demonstrated

- Request-to-job orchestration, item processing, a retry worker, and the request-level idempotency flow.
- FastAPI ingestion and query endpoints defined by the minimal API contract.
- Audit-event emission by application workflows, ACL, derived-data, chunk, embedding, and relationship tables.
- Concurrent document creation or concurrent semantic updates.
- Complete source move, delete, restore, and rebuild lifecycles.
- Ollama embeddings or answer generation.
- Vector search, PostgreSQL full-text search, reciprocal rank fusion, and bounded graph expansion.
- ACL-safe retrieval, context construction, citations, and abstention.
- Obsidian integration, a complete CI pipeline, machine-readable structured logging, and backup/restore evidence.

OpsBrain is not currently a production incident-management platform and does not execute remediation or production commands.

## 2. Target architecture

```text
Obsidian Vault + operational evidence
  -> ingestion connector / worker
  -> Markdown + frontmatter validation
  -> versioning, provenance and ACL metadata
  -> stable chunking and Ollama embeddings
  -> PostgreSQL + pgvector + full-text index
  -> authenticated FastAPI
  -> ACL-safe vector / keyword / bounded graph retrieval
  -> controlled context builder
  -> Ollama generation
  -> citation or abstention
  -> Obsidian integration + evaluation artifacts
```

PostgreSQL is the system of record. Chunks, embeddings, full-text indexes, and relationship edges are derived data and must be rebuildable from retained source documents and version history.

## 3. Repository structure

```text
OpsBrain/
├── .env.example
├── .gitignore
├── alembic.ini
├── AUDIT.md
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── alembic/
│   ├── env.py
│   └── versions/
│       ├── 940c9eb1716a_create_core_storage_tables.py
│       ├── b4b2a6760f09_create_ingestion_job_storage.py
│       ├── a827ee2cd02b_create_audit_event_storage.py
│       └── 5a58f7c5c515_enforce_audit_events_append_only.py
├── data/
│   └── sample-vault/
│       └── payment-service/
├── docs/
│   ├── contracts/
│   │   ├── document-frontmatter.md
│   │   ├── minimal-api.md
│   │   └── storage-schema.md
│   └── project-specification.md
├── migrations/
│   ├── init_roles.sh
│   └── init_pgvector.sql
├── src/
│   ├── config.py
│   ├── database.py
│   ├── main.py
│   ├── ingestion/
│   │   ├── canonicalization.py
│   │   ├── frontmatter.py
│   │   └── lifecycle.py
│   └── models/
│       └── storage.py
└── tests/
    ├── conftest.py
    ├── test_frontmatter_validation.py
    ├── test_health.py
    ├── test_ingestion_create_replay.py
    ├── test_ingestion_job_storage.py
    ├── test_ingestion_job_storage_database.py
    ├── test_ingestion_semantic_update.py
    ├── test_storage_models.py
    ├── test_audit_event_storage.py
    └── test_audit_event_storage_database.py
```

`migrations/init_pgvector.sql` only enables the PostgreSQL `vector` extension when the database volume is initialized. Alembic manages the application schema.

Application logs currently go to stdout with timestamp, level, and logger name. Machine-readable structured logging has not been implemented.

## 4. Requirements

- Docker Desktop or Docker Engine with Docker Compose.
- Python 3.11.
- Git.
- PowerShell for the Windows commands below.

Credentials in `.env.example` are development defaults only. Do not use them in production and do not commit `.env`.

## 5. Local setup

### 5.1 Create the configuration file

PowerShell:

```powershell
Copy-Item .env.example .env
```

Bash:

```bash
cp .env.example .env
```

If PostgreSQL host port `5432` is already in use, change `POSTGRES_PORT` in `.env`, for example:

```dotenv
POSTGRES_PORT=55432
```

OpsBrain uses two PostgreSQL roles:

- `opsbrain_owner` owns the schema and is used only to initialize PostgreSQL and run Alembic migrations.
- `opsbrain_app` is the least-privilege runtime role used by the API and application tests. It can read and insert audit events, but cannot update or delete them.

Keep `DATABASE_URL` on the `db` hostname and port `5432` for the API running inside Docker Compose. `MIGRATION_DATABASE_URL` is for Alembic running from the Windows host, so it must use the published host port instead:

```dotenv
DATABASE_URL=postgresql+asyncpg://opsbrain_app:<app-password>@db:5432/opsbrain
MIGRATION_DATABASE_URL=postgresql+asyncpg://opsbrain_owner:<owner-password>@127.0.0.1:<published-port>/opsbrain
```

Never add real credentials to Git, the README, issues, pull requests, or public logs.

### 5.2 Start PostgreSQL

```powershell
docker compose up -d --wait db
docker compose ps
```

Check pgvector:

```powershell
docker compose exec db psql -At `
  -U opsbrain_owner `
  -d opsbrain `
  -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;"
```

If you changed `POSTGRES_OWNER_USER` or `POSTGRES_DB` in `.env`, update the corresponding values in this command.

Show the PostgreSQL port published to the host:

```powershell
docker compose port db 5432
```

`migrations/init_roles.sh` creates the application role and default grants only when PostgreSQL initializes an empty data volume. After changing this bootstrap script, recreate the local development volume before validating the new role setup.

### 5.3 Prepare the Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 5.4 Run Alembic migrations from the Windows host

Python and Alembic running on the Windows host must connect through PostgreSQL's published host port, not the Docker hostname `db`.

Temporarily set `MIGRATION_DATABASE_URL` with the owner user, owner password, database, and published port from `.env`:

```powershell
$env:MIGRATION_DATABASE_URL = "postgresql+asyncpg://<owner-user>:<owner-password>@127.0.0.1:<published-port>/<database>"

python -m alembic upgrade head
python -m alembic current
```

Expected `alembic current` output:

```text
5a58f7c5c515 (head)
```

Remove the migration override from the PowerShell session when finished:

```powershell
Remove-Item Env:MIGRATION_DATABASE_URL
```

### 5.5 Build and start the API

```powershell
docker compose up -d --build api
docker compose ps
```

### 5.6 Check the API

PowerShell:

```powershell
Invoke-RestMethod http://localhost:8000/health |
  ConvertTo-Json -Depth 5
```

Example response when the database and pgvector are available:

```json
{
  "status": "healthy",
  "app": "OpsBrain API",
  "version": "0.1.0",
  "environment": "development",
  "components": {
    "database": {
      "connected": true,
      "pgvector_ready": true,
      "error": null
    }
  }
}
```

Liveness:

```powershell
Invoke-RestMethod http://localhost:8000/health/liveness
```

OpenAPI UI:

```text
http://localhost:8000/docs
```

A successful health check proves only that the API can connect to PostgreSQL and pgvector. It does not prove that migrations, ingestion, or retrieval work.

## 6. Tests

### 6.1 Local test suite

Make sure PostgreSQL is running. Set the runtime URL with the application role and the migration URL with the owner role, both using the published host port:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://<app-user>:<app-password>@127.0.0.1:<published-port>/<database>"
$env:MIGRATION_DATABASE_URL = "postgresql+asyncpg://<owner-user>:<owner-password>@127.0.0.1:<published-port>/<database>"

python -m compileall -q .\src .\tests
python -m pytest .\tests -q -p no:cacheprovider
python -m alembic check

Remove-Item Env:DATABASE_URL
Remove-Item Env:MIGRATION_DATABASE_URL
```

Recorded at implementation commit `3a1aa75`:

```text
63 passed
No new upgrade operations detected.
```

This result is evidence for the recorded commit and environment, not a guarantee of identical behavior or runtime on every machine.

### 6.2 Docker test suite

Make sure the `db` service is running:

```powershell
docker compose up -d --wait db
docker compose build api

docker compose run --rm --no-deps `
  -e PYTHONDONTWRITEBYTECODE=1 `
  -v "${PWD}/tests:/app/tests:ro" `
  -v "${PWD}/data:/app/data:ro" `
  api `
  sh -lc 'python -m pytest /app/tests -q -p no:cacheprovider'
```

Recorded at implementation commit `3a1aa75`:

```text
63 passed
```

## 7. Primary documentation

- [Public project specification](docs/project-specification.md)
- [Document frontmatter contract](docs/contracts/document-frontmatter.md)
- [Minimal API contract](docs/contracts/minimal-api.md)
- [Storage schema contract](docs/contracts/storage-schema.md)
- [Runtime verification and audit trail](AUDIT.md)

`docs/project-specification.md` is the canonical public project specification. This README provides a startup guide and implementation snapshot; it does not replace the contracts or runtime evidence.

## 8. Roadmap

The next storage and ingestion targets are:

1. Add append-only audit-event storage.
2. Implement request-to-job orchestration, request-level idempotency, and retry processing.
3. Complete source move, delete, restore, and derived-data invalidation.
4. Add stable chunking, Ollama embeddings, and pgvector retrieval.
5. Apply ACL filtering before candidate ranking, graph expansion, context construction, and citations.
6. Compare vector-only, hybrid, and bounded-graph retrieval using reproducible evaluation artifacts.
7. Integrate Obsidian through FastAPI.

A target or expected outcome is considered implemented only after matching runtime, test, migration, query, or evaluation evidence exists.

## 9. Safety and current limitations

- The sample vault contains synthetic data, not production or real customer data.
- Frontmatter cannot grant permissions, approve content, or change lifecycle state by itself.
- Clients and any future Obsidian integration must communicate through FastAPI, not connect directly to PostgreSQL or Ollama.
- OpsBrain does not execute remediation or production commands.
- When valid, authorized evidence is insufficient, the target architecture requires the system to abstain.
- Do not commit `.env`, secrets, private vaults, raw operational logs, model caches, or database volumes.

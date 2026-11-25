# OpsBrain — Enterprise Second Brain for IT Operations

OpsBrain is an evidence-first knowledge infrastructure prototype for investigating IT incidents. Its target is to manage versioned documents in PostgreSQL, retrieve authorized evidence, build controlled context, and answer with citations—or abstain when the evidence is insufficient.

The project is implemented as small, tested vertical slices. This README distinguishes code observed in the repository, recorded local evidence, and target architecture. A feature is not treated as end-to-end or production-ready without matching runtime, test, migration, query, or evaluation evidence.

## 1. Current status

Remote merged checkpoint: `2026-11-24`, merge commit `78d0999` on `origin/main` (Pull Request #44). Pull Requests #41-#44 added GitHub Actions verification, document-history integrity enforcement, audited capability administration, and the ACL-safe reindex API. This checkpoint is not a production deployment claim.

### Implemented and tested

- Docker Compose environment with FastAPI, PostgreSQL 16, and pgvector.
- Health endpoints: `GET /health` and `GET /health/liveness`.
- A sample vault with 12 synthetic documents linked around a Payment Service HTTP 503 scenario.
- A frontmatter validator for service, deployment, alert, incident, postmortem, and runbook documents.
- Accepted frontmatter, minimal API, and storage schema contracts.
- Alembic migrations and SQLAlchemy models for organizations/principals, versioned documents, ingestion and audit history, review decisions, derived configurations and sets, chunks, embeddings, reindex jobs, and version-scoped ACL subjects and grants.
- Canonical content hashing for validated Markdown.
- A sequential PostgreSQL ingestion lifecycle supporting initial creation, unchanged replay, body and metadata updates, immutable version history, parent-version lineage, review-state reset, and rollback behavior covered by tests.
- Database constraints for ingestion jobs, items, and attempts, including organization isolation, principal-scoped idempotency-key uniqueness, and terminal-state consistency.
- Audit-event storage foundation: the `audit_events` table, scoped foreign-key and check constraints, query indexes, an append-only trigger, and separate database roles for migrations and application runtime.
- Immutable derived configuration, derived set, and chunk storage with deterministic heading-aware chunking and atomic chunk-build/activation workflows.
- Reindex job, item, and attempt storage; approval-driven enqueueing; chunk worker processing; delayed retries; persisted lease fencing and expired-lease reclaim.
- Ollama `/api/embed` client with model/tag, dimension, count, finite-value, and retryability validation; embedding reindex worker; and a local worker runner.
- `POST /api/v1/queries` in `evidence_only` mode. It resolves the local authenticated principal, filters readable documents before profile discovery and vector ranking, returns citations/evidence, and abstains when no eligible evidence remains or scores are below the configured threshold. `answer` mode explicitly returns `501` until generation is implemented.
- Vector top-k retrieval scoped to organization, active/current/approved temporal eligibility, embedding profile, and the caller-supplied authorized document set.
- Initial user/team/role ACL storage and resolver. Restricted-version grants are materialized from validated frontmatter during ingestion and copied to the review-transition version; query retrieval uses the resolver before ranking and citation construction.
- Capability catalog and role-capability grants with a database allowlist; `document:approve` is enforced after visibility at review, while `query:execute` is enforced before vector retrieval and citations are re-authorized after embedding.
- A 12-note synthetic sample-vault loader, a frozen 12-question vector-only baseline dataset, raw baseline responses, an ACL-sensitive question dataset, and a separate gold v1.1 dataset with section/category evaluation support.

### Recent merged M5 and CI additions

- Tombstone deletion copies a restricted version's ACL snapshot to its tombstone version.
- Migration `f3a9d2c7b518` revokes request-path mutation privileges for document versions/chunks and hard delete for documents; database triggers reject document-version, chunk, and document hard deletes for privileged connections as well.
- A privileged, audited CLI/operator path grants and revokes role capabilities; it is intentionally outside the API process.
- `POST /api/v1/reindex-jobs` enforces `index:rebuild`, accepts only worker-supported singleton targets (`chunks` or `embeddings`), requires idempotency, and queues only documents currently readable by the requester. Its replay and `GET` paths re-filter job items and counts under the caller's current ACL.
- A GitHub Actions workflow provisions PostgreSQL/pgvector plus the dedicated concurrency database, runs migrations, Alembic check, the full suite, and PR/push diff-format checks. The workflow passed on Pull Requests #41-#44.
- RLS hardening slice 1 introduces transaction-local organization/principal context, a hardened active-principal resolver, and fail-closed organization isolation for `documents` and `document_versions`. Background workers use the separate `opsbrain_worker` role with `BYPASSRLS`; this slice intentionally does not yet move per-document ACL enforcement into RLS.

### Latest recorded evidence

For merged checkpoint `78d0999` (PR #44), Huy reported `tests/test_reindex_api.py -v`: `9 passed, 4 warnings in 1.87s`; full `pytest -q` across `opsbrain_test` and `opsbrain_concurrency_test`: `261 passed, 6 warnings in 49.84s`; and `git diff --check` exit code `0`. The migration head is `f3a9d2c7b518`. GitHub Actions passed on Pull Requests #41-#44. These are test-environment results, not production migration or deployment evidence.

Earlier merged evidence remains traceable in `docs/project/CURRENT_STATUS.md`. Key retrieval/runtime evidence is:

- A host Ollama probe and a container-to-host probe returned model `nomic-embed-text`, one embedding, and dimension `768`.
- The synthetic sample-vault loader reported `ingested=12 approved_or_replayed=12 reindex_jobs=12`; the local worker runner subsequently reported `chunks=12 embeddings=12`.
- The frozen EN vector-only baseline is recorded in [docs/evaluation/runs/VECTOR_BASELINE_2026-11-18.md](docs/evaluation/runs/VECTOR_BASELINE_2026-11-18.md): Recall@5 `84.62%` (11/13 expected documents), strict document-level citation proxy `34.00%`, and abstention accuracy `83.33%`. This is a synthetic-corpus baseline, not a generalization or production-quality claim.
- PR #39 evidence includes a full suite on a newly migrated temporary database: `237 passed, 2 skipped, 1 warning`, plus two separately run concurrency-race tests. Current capability migration head is `d8a4f6b2c1e7`.

Older evidence below is retained for historical checkpoints and is not a statement of the current suite size.

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

- Shared request-level idempotency across API/job workflows and production error classification for database/network failures.
- FastAPI ingestion endpoint, request-level idempotency across API/job workflows, and the remaining document/review endpoints from the minimal API contract.
- Capability enforcement for ingestion; ACL enforcement beyond current review, reindex jobs, and vector candidates/citations, including graph traversal, context construction, and generation.
- Full-text and relationship workers, worker heartbeat, and recovery after external side effects.
- Ollama answer generation and a supported `response_mode: answer` path.
- PostgreSQL full-text search, reciprocal rank fusion, bounded graph expansion, and their evaluation.
- Production migration verification and fresh end-to-end runtime verification for every non-test database.
- Obsidian integration, machine-readable structured logging, and backup/restore evidence.

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
│   └── versions/                 # core, lifecycle, review, derived, chunk, embedding, reindex and ACL migrations
├── data/
│   └── sample-vault/
│       └── payment-service/
├── docs/
│   ├── contracts/
│   │   ├── document-frontmatter.md
│   │   ├── minimal-api.md
│   │   └── storage-schema.md
│   ├── evaluation/               # frozen gold questions, raw runs and baseline report
│   └── project/
├── migrations/
│   ├── init_roles.sh
│   └── init_pgvector.sql
├── src/
│   ├── config.py
│   ├── database.py
│   ├── main.py
│   ├── api/                      # minimal query endpoint
│   ├── authorization/            # document read ACL resolver
│   ├── ingestion/
│   │   ├── canonicalization.py
│   │   ├── frontmatter.py
│   │   ├── lifecycle.py
│   │   ├── ollama_embeddings.py
│   │   └── reindex_worker.py
│   └── models/
│       └── storage.py
├── scripts/
│   ├── load_sample_vault.py
│   ├── run_reindex_workers.py
│   └── evaluate_vector_baseline.py
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
- Ollama on the host when exercising embeddings or `/api/v1/queries`; the Docker API reaches it through `host.docker.internal`.

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

For the current merged checkpoint, expected `alembic current` output after `upgrade head` is:

```text
f3a9d2c7b518 (head)
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

### 5.7 Optional Ollama embedding setup

Install Ollama on the host, then pull the embedding model:

```powershell
ollama pull nomic-embed-text
```

Ollama normally starts as a local service. If it is not already running, use `ollama serve`. A bind error on port `11434` usually means that the service is already running.

For a process inside Docker Compose, set the base URL to the Docker Desktop host alias:

```dotenv
OLLAMA_BASE_URL=http://host.docker.internal:11434
OLLAMA_EMBED_MODEL=nomic-embed-text
```

The supported baseline configuration uses `768` dimensions. Test the host endpoint before blaming the application or Docker network:

```powershell
$payload = @{
  model = "nomic-embed-text"
  input = @("OpsBrain embedding probe")
  dimensions = 768
  truncate = $false
} | ConvertTo-Json -Depth 4

Invoke-RestMethod -Method Post `
  -Uri "http://localhost:11434/api/embed" `
  -ContentType "application/json" `
  -Body $payload
```

## 6. Tests

### 6.1 Official test path (Docker)

This is the authoritative verification path. It runs against the Compose-managed PostgreSQL, so results do not depend on host-side database roles or credentials.

Make sure the `db` service is running. The `api` service only defines `DATABASE_URL`, so the migration URL must be passed explicitly for the tests. Inside the Compose network the database host is `db`, not `127.0.0.1`.

```powershell
docker compose up -d --wait db
docker compose build api

docker compose run --rm --no-deps `
  -e PYTHONDONTWRITEBYTECODE=1 `
  -e DATABASE_URL="postgresql+asyncpg://<app-user>:<app-password>@db:5432/<database>" `
  -e MIGRATION_DATABASE_URL="postgresql+asyncpg://<owner-user>:<owner-password>@db:5432/<database>" `
  -v "${PWD}/src:/app/src:ro" `
  -v "${PWD}/tests:/app/tests:ro" `
  -v "${PWD}/data:/app/data:ro" `
  api `
  sh -lc 'python -m pytest /app/tests -q -p no:cacheprovider'
```

Mounting `src` read-only lets one image test the current working tree without rebuilding; rebuild only when dependencies change. Because the mounts are read-only, run `python -m compileall` from section 6.2 on the host instead of inside the container.

Troubleshooting: a shell variable named `DATABASE_URL` exported by another project overrides `.env` during Compose interpolation and silently points the tests at the wrong database. Running `docker compose config` shows the resolved value; the `-e` flags above take precedence over it.

Recorded at implementation commit `3a1aa75`:

```text
63 passed
```

Historical working-tree run (2026-11-18, before the local ACL commits; not a committed-state release gate):

```text
177 passed, 2 skipped
```

The count moves while other slices are in progress; treat pass/fail as the signal, not the number. A fresh full-suite result after the local ACL slices is still required before a release or merge claim.

### 6.2 Optional host checks

Use this only for fast feedback while editing. It is not the official test path: the runtime and migration roles authenticate over the published host port, so a PostgreSQL volume whose passwords no longer match `.env` fails here while section 6.1 still passes.

Set both URLs explicitly in the current shell: a value already exported in the shell (for example by another project) overrides `.env` without any warning.

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://<app-user>:<app-password>@127.0.0.1:<published-port>/<database>"
$env:MIGRATION_DATABASE_URL = "postgresql+asyncpg://<owner-user>:<owner-password>@127.0.0.1:<published-port>/<database>"

python -m compileall -q .\src .\tests
python -m alembic check

Remove-Item Env:DATABASE_URL
Remove-Item Env:MIGRATION_DATABASE_URL
```

`alembic check` belongs here because it is meant to run with the owner role over the published port. If a host run fails with `password authentication failed for user "<owner-user>"`, the role password stored in the PostgreSQL volume does not match `.env`; fix the role or recreate the volume instead of treating it as a test failure.

Recorded at implementation commit `3a1aa75`:

```text
No new upgrade operations detected.
```

## 7. Primary documentation

- [Public project specification](docs/project-specification.md)
- [Document frontmatter contract](docs/contracts/document-frontmatter.md)
- [Minimal API contract](docs/contracts/minimal-api.md)
- [Storage schema contract](docs/contracts/storage-schema.md)
- [Runtime verification and audit trail](AUDIT.md)

`docs/project-specification.md` is the canonical public project specification. This README provides a startup guide and implementation snapshot; it does not replace the contracts or runtime evidence.

## 8. Roadmap

The next targets after the current vector/initial-ACL slice are:

1. Add a FastAPI ingestion endpoint and request-level idempotency; retain the existing query endpoint's local-development authentication only as a development boundary.
2. Extend ACL/capability enforcement from vector candidate filtering to review, activation, context construction, graph expansion, and generation.
3. Add PostgreSQL full-text retrieval and RRF, then compare it with the frozen vector-only baseline under the same corpus/configuration/access context.
4. Add bounded graph expansion only with per-hop ACL enforcement and dedicated negative tests.
5. Integrate Obsidian through FastAPI.

A target or expected outcome is considered implemented only after matching runtime, test, migration, query, or evaluation evidence exists.

## 9. Safety and current limitations

- The sample vault contains synthetic data, not production or real customer data.
- Frontmatter cannot grant permissions, approve content, or change lifecycle state by itself.
- Clients and any future Obsidian integration must communicate through FastAPI, not connect directly to PostgreSQL or Ollama.
- OpsBrain does not execute remediation or production commands.
- When valid, authorized evidence is insufficient, the target architecture requires the system to abstain.
- Do not commit `.env`, secrets, private vaults, raw operational logs, model caches, or database volumes.

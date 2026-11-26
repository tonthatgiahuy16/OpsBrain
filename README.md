# OpsBrain

OpsBrain is an evidence-first knowledge system for IT incident investigation. It stores versioned Markdown documents in PostgreSQL, retrieves only authorized evidence, and returns citations or abstains when evidence is insufficient.

**Stack:** Python 3.11 | FastAPI | PostgreSQL + pgvector (RLS) | SQLAlchemy | Alembic | Ollama | Docker Compose

## Current status

- **Merged baseline:** `6bfd12f` (Pull Request #50, 2026-11-26).
- **M5 closure:** shared error envelopes, request tracing, explicit query strategy/context behavior, and the contract-aligned reindex API.
- **Latest local verification:** `283 passed in 24.12s`, with `0 failed`, `0 skipped`, and `0 warnings`; `git diff --check` passed.
- **Live local E2E:** separate author and reviewer principals completed ingestion, cross-principal job reads, document state, independent approval, vector query, and reindex flows. The reviewer had all seven baseline capabilities.

This is a local prototype, not a production incident-management platform.

## What is implemented

- Validated Markdown ingestion with immutable document versions, lifecycle state, audit events, and idempotency.
- Queued ingestion and reindex jobs with worker attempts, lease fencing, and stale-worker reclaim.
- Organization isolation, user/team/role ACL resolution, capability enforcement, and PostgreSQL RLS for protected records.
- ACL-safe vector retrieval with evidence citations and abstention.
- Document state, review, ingestion, query, and reindex HTTP endpoints.
- Reproducible vector-only and ACL-sensitive evaluation artifacts.

## Quick start

### Prerequisites

- Docker Desktop or Docker Engine with Compose
- Python 3.11
- PowerShell on Windows
- Ollama with `nomic-embed-text` when running vector queries

### Configure and start services

```powershell
Copy-Item .env.example .env
docker compose up -d --build --wait db
docker compose up -d api
docker compose ps
```

The API is available at `http://localhost:8000`; OpenAPI is at `http://localhost:8000/docs`.

### Prepare Python and migrate the database

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Host-side migration commands use the URLs in `.env`. Ensure its host-side URLs use the published PostgreSQL port and the owner role where required. Shell values take precedence over `.env`, so clear stale overrides first:

```powershell
Remove-Item Env:DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:MIGRATION_DATABASE_URL -ErrorAction SilentlyContinue
Remove-Item Env:WORKER_DATABASE_URL -ErrorAction SilentlyContinue

python -m alembic upgrade head
python -m alembic current
python -m alembic check
```

### Verify API health

```powershell
Invoke-RestMethod http://localhost:8000/health | ConvertTo-Json -Depth 5
```

A health response proves PostgreSQL and pgvector connectivity. It does not prove ingestion, retrieval, or migration behavior.

### Run the test suite

Tests run in a disposable API container against two dedicated databases. They never use the development database, and they do not require Windows to resolve the Compose hostname `db`.

The following commands use the development credentials in `.env.example`. If you changed those credentials in `.env`, replace the three password values consistently.

```powershell
$testDatabaseSql = @'
SELECT format('CREATE DATABASE %I', database_name)
FROM (VALUES ('opsbrain_test'), ('opsbrain_concurrency_test')) AS required(database_name)
WHERE NOT EXISTS (
  SELECT 1 FROM pg_database WHERE datname = required.database_name
)
\gexec
'@
$testDatabaseSql | docker compose exec -T db sh -lc 'psql -U "$POSTGRES_OWNER_USER" -d postgres -v ON_ERROR_STOP=1'

foreach ($database in 'opsbrain_test', 'opsbrain_concurrency_test') {
  docker compose exec -T -e "POSTGRES_DB=$database" db sh -lc '
    bash /docker-entrypoint-initdb.d/10_init_roles.sh
    psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/20_init_pgvector.sql
  '

  docker compose run --rm --no-deps `
    -v "${PWD}:/app" `
    -e "DATABASE_URL=postgresql+asyncpg://opsbrain_app:opsbrain_app_password@db:5432/$database" `
    -e "MIGRATION_DATABASE_URL=postgresql+asyncpg://opsbrain_owner:opsbrain_owner_password@db:5432/$database" `
    -e "WORKER_DATABASE_URL=postgresql+asyncpg://opsbrain_worker:opsbrain_worker_password@db:5432/$database" `
    api python -m alembic upgrade head
}

docker compose run --rm --no-deps `
  -v "${PWD}:/app" `
  -e "DATABASE_URL=postgresql+asyncpg://opsbrain_app:opsbrain_app_password@db:5432/opsbrain_test" `
  -e "MIGRATION_DATABASE_URL=postgresql+asyncpg://opsbrain_owner:opsbrain_owner_password@db:5432/opsbrain_test" `
  -e "WORKER_DATABASE_URL=postgresql+asyncpg://opsbrain_worker:opsbrain_worker_password@db:5432/opsbrain_test" `
  -e "OPSBRAIN_CONCURRENCY_DATABASE_URL=postgresql+asyncpg://opsbrain_owner:opsbrain_owner_password@db:5432/opsbrain_concurrency_test" `
  api python -m pytest -q

git diff --check
```

The test databases persist in the local Docker volume and can be reused on later runs. The hosted CI workflow follows the same two-database model.

## Runtime boundaries

- API requests use the least-privilege `opsbrain_app` role and transaction-local identity context.
- Background workers use the separate `opsbrain_worker` role because they process queued work across organizations.
- Capability grants and revocations use `scripts/manage_capability.py` with the owner connection; every actual transition writes an audit event.
- Local opaque query tokens belong only in `.env`; do not commit them.

## Current limitations

- Retrieval is vector-only and `response_mode: answer` is not implemented.
- Full-text/RRF retrieval is Milestone 6; bounded graph expansion is Milestone 7.
- The sample corpus and evaluation data are synthetic. Results apply only to the recorded corpus, access context, model, and configuration.
- Production deployment, external-effect recovery, worker heartbeat, backup/restore, and the Obsidian plugin remain future work.

## Documentation

- [Project specification](docs/project-specification.md)
- [Minimal API contract](docs/contracts/minimal-api.md)
- [Document frontmatter contract](docs/contracts/document-frontmatter.md)
- [Storage schema contract](docs/contracts/storage-schema.md)
- [Evaluation artifacts](docs/evaluation/runs/)
- [Audit trail](AUDIT.md)

## Repository map

| Path | Purpose |
| --- | --- |
| `src/api/` | FastAPI routes, authentication, and error responses |
| `src/ingestion/` | Validation, lifecycle, retrieval, and background workers |
| `src/models/` | SQLAlchemy storage models |
| `alembic/` | Schema migrations and RLS policies |
| `scripts/` | Operators, sample data, workers, and evaluation runners |
| `tests/` | Unit, API, database, and concurrency coverage |

## Repository safety

Do not commit `.env`, opaque tokens, private vaults, raw operational logs, model caches, or database volumes.

# OpsBrain — Enterprise Second Brain for IT Operations

OpsBrain là prototype hạ tầng tri thức đang được xây dựng để hỗ trợ điều tra sự cố công nghệ thông tin theo hướng evidence-first. Mục tiêu là quản lý tài liệu có phiên bản trong PostgreSQL, truy xuất đúng bằng chứng, áp dụng quyền trước khi tạo context và trả lời kèm citation hoặc abstain khi không đủ cơ sở.

Dự án đang được triển khai theo từng vertical slice có kiểm thử. Retrieval, ACL enforcement, Ollama generation, citation và Obsidian integration bên dưới là kiến trúc mục tiêu; chưa được xem là đã triển khai nếu chưa có Evidence tương ứng.

## 1. Trạng thái hiện tại

Mốc Evidence gần nhất: `2026-10-16`, commit `fa6b873`.

### Đã triển khai và kiểm thử

- Docker Compose với FastAPI, PostgreSQL 16 và pgvector.
- Health endpoints:
  - `GET /health`
  - `GET /health/liveness`
- Sample vault gồm 12 tài liệu synthetic liên kết quanh kịch bản Payment Service HTTP 503.
- Frontmatter validator cho service, deployment, alert, incident, postmortem và runbook.
- Frontmatter, minimal API và storage schema contracts đã được chấp nhận.
- Alembic migration cho bốn bảng core:
  - `organizations`
  - `principals`
  - `documents`
  - `document_versions`
- Canonical content hash cho validated Markdown.
- PostgreSQL ingestion lifecycle cho:
  - Tạo document và version đầu tiên.
  - Unchanged replay không tạo version trùng.
  - Body change tạo `content_update`.
  - Metadata-only change tạo `metadata_update`.
  - Giữ immutable version history và parent-version lineage.
  - Đưa resulting semantic update về `in_review`.
  - Giữ current version và lịch sử khi update thất bại trong transaction được kiểm thử.

### Evidence gần nhất

- Toàn bộ local test suite: `56 passed`.
- Toàn bộ Docker test suite: `56 passed`.
- `python -m alembic check`: `No new upgrade operations detected.`
- Alembic upgrade, downgrade và upgrade lại đã được kiểm tra trên PostgreSQL.
- Constraint và index trọng yếu của core storage đã được kiểm tra bằng catalog query và transaction behavior test.

### Chưa triển khai hoặc chưa được chứng minh

- Ingestion jobs, request-level idempotency và retry orchestration.
- Audit, ACL, derived-data, chunk, embedding và relationship tables.
- Concurrent create hoặc concurrent semantic update.
- Source move, delete, restore và rebuild lifecycle hoàn chỉnh.
- FastAPI ingestion và query endpoints theo minimal API contract.
- Ollama embedding hoặc answer generation.
- Vector search, PostgreSQL full-text search, RRF và bounded graph expansion.
- ACL-safe retrieval, context construction, citation và abstention.
- Obsidian plugin, evaluation pipeline, CI hoàn chỉnh và backup/restore.

OpsBrain hiện chưa phải production incident-management platform và không tự động thực thi remediation hoặc production command.

## 2. Kiến trúc mục tiêu

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
  -> Obsidian plugin + evaluation artifacts
```

PostgreSQL là system of record. Chunk, embedding, full-text index và relationship edge là derived data và phải có khả năng rebuild từ tài liệu nguồn cùng version history.

## 3. Cấu trúc repository

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
│       └── 940c9eb1716a_create_core_storage_tables.py
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
    ├── test_ingestion_semantic_update.py
    └── test_storage_models.py
```

`migrations/init_pgvector.sql` chỉ kích hoạt PostgreSQL extension `vector` khi database volume được khởi tạo. Alembic quản lý schema application.

Application log hiện được ghi ra stdout theo format có timestamp, level và logger name. Machine-readable structured logging chưa được triển khai.

## 4. Yêu cầu môi trường

- Docker Desktop hoặc Docker Engine có Docker Compose.
- Python 3.11.
- Git.
- PowerShell cho các lệnh Windows bên dưới.

Các credential trong `.env.example` chỉ là development defaults. Không dùng chúng cho production và không commit file `.env`.

## 5. Khởi động môi trường local

### 5.1 Tạo file cấu hình

PowerShell:

```powershell
Copy-Item .env.example .env
```

Bash:

```bash
cp .env.example .env
```

Nếu cổng PostgreSQL `5432` trên host đang được sử dụng, đổi `POSTGRES_PORT` trong `.env`, ví dụ:

```dotenv
POSTGRES_PORT=55432
```

Giữ `DATABASE_URL` dùng hostname `db` và cổng `5432` cho API chạy trong Docker Compose:

```dotenv
DATABASE_URL=postgresql+asyncpg://<user>:<password>@db:5432/<database>
```

Không đưa credential thật vào Git, README, issue, Pull Request hoặc log công khai.

### 5.2 Khởi động PostgreSQL

```powershell
docker compose up -d --wait db
docker compose ps
```

Kiểm tra pgvector:

```powershell
docker compose exec db psql -At `
  -U opsbrain_user `
  -d opsbrain `
  -c "SELECT extname, extversion FROM pg_extension ORDER BY extname;"
```

Nếu đã thay `POSTGRES_USER` hoặc `POSTGRES_DB` trong `.env`, thay hai giá trị tương ứng trong lệnh.


Xem cổng PostgreSQL được publish ra host:

```powershell
docker compose port db 5432
```

### 5.3 Chuẩn bị Python environment

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

### 5.4 Chạy Alembic migration từ Windows host

Python và Alembic chạy trên Windows host phải kết nối tới cổng PostgreSQL được publish ra host, không dùng hostname `db`.

Đặt tạm `DATABASE_URL` bằng đúng user, password, database và published port trong `.env`:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://<user>:<password>@127.0.0.1:<published-port>/<database>"

python -m alembic upgrade head
python -m alembic current
```

Kết quả mong đợi của `alembic current`:

```text
940c9eb1716a (head)
```

Xóa override khỏi PowerShell session sau khi hoàn tất:

```powershell
Remove-Item Env:DATABASE_URL
```

### 5.5 Build và khởi động API

```powershell
docker compose up -d --build api
docker compose ps
```

### 5.6 Kiểm tra API

PowerShell:

```powershell
Invoke-RestMethod http://localhost:8000/health |
  ConvertTo-Json -Depth 5
```

Phản hồi mẫu khi database và pgvector sẵn sàng:

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

Health thành công chỉ chứng minh API kết nối được PostgreSQL và pgvector. Nó không tự chứng minh migrations, ingestion hoặc retrieval đã hoạt động.

## 6. Chạy kiểm thử

### 6.1 Local test suite

Bảo đảm PostgreSQL đang chạy và đặt `DATABASE_URL` cho Windows host:

```powershell
$env:DATABASE_URL = "postgresql+asyncpg://<user>:<password>@127.0.0.1:<published-port>/<database>"

python -m compileall -q .\src .\tests
python -m pytest .\tests -q -p no:cacheprovider
python -m alembic check

Remove-Item Env:DATABASE_URL
```

Tại commit `fa6b873`, kết quả đã quan sát trên môi trường phát triển hiện tại là:

```text
56 passed
No new upgrade operations detected.
```

Con số này là Evidence cho commit và môi trường đã ghi nhận, không phải cam kết mọi máy luôn có cùng thời gian chạy.

### 6.2 Docker test suite

Bảo đảm service `db` đang chạy:

```powershell
docker compose up -d --wait db
docker compose build api

docker compose run --rm --no-deps `
  -e PYTHONDONTWRITEBYTECODE=1 `
  -v "${PWD}/tests:/app/tests:ro" `
  -v "${PWD}/data:/app/data:ro" `
  api `
  sh -lc 'export DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}"; python -m pytest /app/tests -q -p no:cacheprovider'
```

Kết quả Docker đã quan sát tại commit `fa6b873`:

```text
56 passed
```

## 7. Tài liệu chính

- [Public project specification](docs/project-specification.md)
- [Document frontmatter contract](docs/contracts/document-frontmatter.md)
- [Minimal API contract](docs/contracts/minimal-api.md)
- [Storage schema contract](docs/contracts/storage-schema.md)
- [Runtime verification and audit trail](AUDIT.md)

`docs/project-specification.md` là canonical public project specification. README cung cấp hướng dẫn khởi động và snapshot triển khai, không thay thế contract hoặc Evidence.

## 8. Hướng phát triển tiếp theo

Mục tiêu kế tiếp của storage và ingestion milestone:

1. Bổ sung ingestion-job và audit-event storage foundation.
2. Triển khai request-level idempotency và retry state.
3. Hoàn thiện source move, delete, restore và derived-data invalidation.
4. Bổ sung chunking, Ollama embeddings và pgvector retrieval.
5. Áp dụng ACL trước candidate ranking, graph expansion, context và citation.
6. So sánh vector-only, hybrid và bounded-graph retrieval bằng evaluation artifacts có thể tái hiện.
7. Tích hợp Obsidian thông qua FastAPI.

Target hoặc expected outcome chỉ được mô tả là đã đạt khi có runtime, test, migration, query hoặc evaluation Evidence tương ứng.

## 9. Giới hạn và nguyên tắc an toàn

- Sample vault hiện dùng synthetic data, không phải dữ liệu production hoặc khách hàng thật.
- Frontmatter không thể tự cấp quyền, tự approve hoặc tự thay đổi lifecycle state.
- Client và Obsidian plugin chỉ được giao tiếp với FastAPI, không kết nối trực tiếp PostgreSQL hoặc Ollama.
- OpsBrain không tự chạy remediation hoặc production command.
- Khi không còn evidence hợp lệ và đúng quyền, kiến trúc mục tiêu yêu cầu hệ thống abstain.
- Không commit `.env`, secret, private vault, raw operational log, model cache hoặc database volume.

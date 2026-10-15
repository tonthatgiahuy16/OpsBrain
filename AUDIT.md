# AUDIT TRAIL — Project: OpsBrain

- **Audit coverage:** Repository history and reproducible Evidence through `2026-10-16`.
- **Repository baseline:** `main` and `origin/main` at merge commit `0b40113` after Pull Request #11.
- **Evidence rule:** A merged document or code file proves delivery to the repository; runtime behavior is marked verified only when supported by test, migration, query or service output.
- **Privacy boundary:** Personal progress notes, local mentor instructions, secrets, `.env`, database volumes and private vault data are intentionally excluded from this shared audit.

## Task: TASK-OPSBRAIN-M1
- **Feature:** Infrastructure Foundation (Docker Compose, PostgreSQL + pgvector, FastAPI /health, Config, Structured Logging, Basic Tests)
- **Project Root:** Repository root
- **Initial Request:** OPSBRAIN — MASTER PROMPT: Hệ thống quản lý ngữ cảnh doanh nghiệp và hỗ trợ điều tra sự cố CNTT.
- **Accepted Scope (Milestone 1):**
  1. Docker Compose & Dockerfile cho FastAPI và PostgreSQL 16 + pgvector.
  2. Quản lý cấu hình môi trường (.env.example, src/config.py) qua Pydantic Settings.
  3. Database Connection & kiểm tra readiness của PostgreSQL và extension pgvector.
  4. Endpoint `GET /health` thể hiện chi tiết trạng thái ứng dụng, database và pgvector.
  5. Structured logging và bộ test cơ bản (pytest) kiểm thử health check.
  6. Khởi tạo tài liệu README.md cho Milestone 1.

## Audit Log
| Timestamp | Actor | Event / Action | Details | Status |
| :--- | :--- | :--- | :--- | :--- |
| 2026-10-10T00:58:16 | Human | Request | Yêu cầu đọc folder OpsBrain | RECORDED |
| 2026-10-10T01:00:31 | Human | Inquiry | Hỏi về khả năng đọc file docx | RECORDED |
| 2026-10-10T01:01:02 | Human | Decision | REJECT lệnh terminal đọc docx ban đầu | RECORDED |
| 2026-10-10T01:39:13 | Human | Master Prompt | Cung cấp OPSBRAIN Master Prompt 18 phần, yêu cầu thực hiện Milestone 0 Audit | RECORDED |
| 2026-10-10T01:40:00 | Agent | Milestone 0 Audit | Hoàn thành báo cáo Audit hiện trạng A->H và 9 mục, đề xuất kế hoạch Milestone 1 | PASS |
| 2026-10-10T01:41:16 | Human | ACCEPT Decision | Phê duyệt kế hoạch triển khai Milestone 1 — Infrastructure Foundation | ACCEPTED |
| 2026-10-10T01:42:00 | Agent | Gate 0 Initialization | Khởi tạo OpsBrain/AUDIT.md, OpsBrain/logs/runtime.log và .agents/tasks/TASK-OPSBRAIN-M1.md | PASS |
| 2026-10-10T01:43:00 | Agent | Implementation | Triển khai Dockerfile, docker-compose.yml, .env.example, requirements.txt, config.py, database.py, main.py, init_pgvector.sql, test_health.py, README.md | PASS |
| 2026-10-10T01:44:00 | Agent | Verification Proposal | Đề xuất lệnh Terminal chạy test và kiểm tra cú pháp Docker Compose chờ Human duyệt | RECORDED |
| 2026-10-10T01:45:00 | Human | Terminal Decision | Phê duyệt chung phương án kiểm thử | ACCEPTED |
| 2026-10-10T01:45:26 | Human | Terminal Permission | Từ chối cấp quyền thực thi lệnh docker compose config tại IDE prompt | DENIED |
| 2026-10-10T01:45:30 | Agent | Execution Halt | Dừng thực thi lệnh terminal theo chỉ đạo của Human, chuyển trạng thái xác minh sang tĩnh | RECORDED |
| 2026-10-12 | Human | Docker Compose Validation | `docker compose config --quiet` hoàn thành với exit code 0 | PASS |
| 2026-10-12 | Human | Container Build | Image `opsbrain-api` được build thành công | PASS |
| 2026-10-12 | Human | Runtime Diagnosis | API ban đầu restart do thiếu dependency `greenlet` cho SQLAlchemy asyncio | FAIL |
| 2026-10-12 | Human | Dependency Fix | Đổi sang `sqlalchemy[asyncio]>=2.0.30`; kiểm tra import thành công; PR #1 đã merge | PASS |
| 2026-10-12 | Human | Service Startup | PostgreSQL và API cùng đạt trạng thái healthy | PASS |
| 2026-10-12 | Human | Health Verification | `GET /health` trả HTTP 200; database connected và pgvector ready | PASS |
| 2026-10-12 | Human | pgvector Verification | PostgreSQL xác nhận extension `vector` phiên bản `0.8.7` | PASS |
| 2026-10-12 | Human | Automated Tests | Pytest chạy trong container: `5 passed in 0.86s` | PASS |
| 2026-10-12 | Human | Milestone 1 Runtime Gate | Docker, database, pgvector, API health và test suite đã được kiểm chứng trực tiếp | VERIFIED |

## Current Status

Milestone 1 runtime gate đã được xác minh bằng kết quả Docker, database, pgvector, health endpoint và pytest trên môi trường local ngày 2026-10-12.

### Known limitation

Logging hiện sử dụng định dạng text gồm timestamp, level, logger name và message. Claim “structured logging” trong phạm vi ban đầu chưa được kiểm chứng theo dạng machine-readable như JSON và cần được xử lý trong một pull request riêng.

---

## Repository Delivery History

| Pull Request | Merge commit | Delivered change | Evidence level |
| :--- | :--- | :--- | :--- |
| #1 | `03f7fd6` | Bổ sung SQLAlchemy asyncio dependency qua commit `8124be2` | Merged; runtime import, service health và tests được kiểm chứng |
| #2 | `8ee1d10` | Thêm `.gitignore`, cập nhật README và ngừng theo dõi `logs/runtime.log` qua commit `f724105` | Merged; repository hygiene verified |
| #3 | `1b4ed39` | Ghi Milestone 1 runtime Evidence vào `AUDIT.md` qua commit `d0fa279` | Merged documentation |
| #4 | `0c4720d` | Định nghĩa document frontmatter schema qua commit `e57660e` | Merged contract proposal |
| #5 | `d4b18dc` | Chốt frontmatter baseline qua commit `8a39046` | Merged; contract Accepted `1.0` |
| #6 | `bd0e7e4` | Thay Word specification cũ bằng `docs/project-specification.md` qua commit `b1f6eee` | Merged active specification |
| #7 | `4c7aa4a` | Tạo và mở rộng sample vault `payment-service` qua commits `59341d1`, `931cfbf` | Merged fixture corpus; 12 synthetic notes |
| #8 | `8dbb26c` | Đề xuất và chấp nhận minimal API contract qua commits `3dd6db4`, `ff4c7ad` | Merged; Accepted `1.0` under delegated authority |
| #9 | `bf63153` | Triển khai frontmatter validator qua commit `2541b8d` | Merged; local and Docker tests verified |
| #10 | `b379b5a` | Đề xuất và chấp nhận storage schema contract qua commits `94599a2`, `1cc3bc9` | Merged; contract Accepted `1.0` |
| #11 | `0b40113` | Triển khai core storage migration qua commit `917f703` | Merged; migration and tests verified |

## Task: TASK-OPSBRAIN-SHARED-CONTRACTS

- **Feature:** Project Specification and Shared Data/API/Storage Contracts
- **Scope:** Chốt source-of-truth boundary, Markdown metadata, HTTP boundary và PostgreSQL storage baseline trước implementation phụ thuộc.

## Shared Contract Audit Log

| Timestamp | Actor | Event / Action | Details | Status |
| :--- | :--- | :--- | :--- | :--- |
| 2026-10-13 | Human + Agent | Frontmatter Proposal | Tạo contract cho sáu document type: `service`, `deployment`, `incident`, `postmortem`, `runbook`, `alert` | MERGED |
| 2026-10-14 | Human + Teammate | Frontmatter Decisions | Chốt explicit organization/restricted scopes, independent approval, provenance rules, broken-reference handling, semantic versioning và temporal eligibility | ACCEPTED |
| 2026-10-14 | Human | Frontmatter Contract Delivery | Contract Accepted `1.0`; Pull Request #5 merge tại `d4b18dc` | VERIFIED |
| 2026-10-14 | Human | Specification Migration | Tạo Markdown project specification làm canonical public specification và xóa Word document cũ; Pull Request #6 merge tại `bd0e7e4` | VERIFIED |
| 2026-10-15 | Human | Minimal API Contract | Chốt ingestion, job status, document state, review decision, query, reindex và health boundaries | ACCEPTED |
| 2026-10-15 | Human | API Acceptance Basis | Minimal API `1.0` được Huy chấp nhận theo delegated authority; không ghi nhận independent reviewer approval | RECORDED |
| 2026-10-15 | Human | Minimal API Delivery | Pull Request #8 merge tại `8dbb26c`; contract có 15 section và 9 endpoint trong summary | VERIFIED |
| 2026-10-15 | Human + Teammate | Storage Schema Contract | Chốt identity, immutable version history, ingestion state, ACL, derived data, relationship, reindex, audit, lifecycle và migration rules | ACCEPTED |
| 2026-10-15 | Human | Storage Contract Delivery | Storage schema contract Accepted `1.0`; Pull Request #10 merge tại `b379b5a` | VERIFIED |

### Shared contract limitations

- Contract được merge chỉ chứng minh thiết kế đã được ghi nhận; không tự chứng minh endpoint, database table hoặc lifecycle tương ứng đã chạy.
- Minimal API chưa được triển khai thành các endpoint nghiệp vụ.
- Storage schema contract mới được triển khai một phần qua bốn bảng core trong Pull Request #11.

---

## Task: TASK-OPSBRAIN-M2-SAMPLE-AND-VALIDATION

- **Feature:** Payment-Service Sample Vault and Executable Frontmatter Validation
- **Contract:** `docs/contracts/document-frontmatter.md` — Accepted for Milestone 2, version `1.0`
- **Fixture corpus:** 12 synthetic Markdown notes dưới `data/sample-vault/payment-service`
- **Implementation commit:** `2541b8d`
- **Merge commit:** `bf63153` — Pull Request #9

## Milestone 2 Audit Log

| Timestamp | Actor | Event / Action | Details | Status |
| :--- | :--- | :--- | :--- | :--- |
| 2026-10-14 | Human | Core Sample Scenario | Tạo service, deployment, alert, incident, postmortem và diagnosis runbook đầu tiên cho synthetic HTTP 503 scenario | PASS |
| 2026-10-15 | Human | Sample Vault Expansion | Mở rộng thành 12 notes gồm service dependencies, baseline/problem/hotfix deployments, recovery runbook và proposed connection-pool alert | PASS |
| 2026-10-15 | Human | Sample Vault Delivery | Pull Request #7 merge tại `4c7aa4a` | VERIFIED |
| 2026-10-15 | Human | Validator Implementation | Thêm Pydantic/PyYAML validation cho common fields, sáu document types, ACL, provenance, temporal fields, tags và identifiers | PASS |
| 2026-10-15 | Human | Batch Relationship Validation | Kiểm tra duplicate document ID, required/optional reference, target type, duplicate relationship và self-reference | PASS |
| 2026-10-15 | Human | Frontmatter Test Suite | Frontmatter validation suite đạt `41 passed in 0.33s` | PASS |
| 2026-10-15 | Human | Local Regression Tests | Toàn bộ local test suite đạt `46 passed in 7.53s` | PASS |
| 2026-10-15 | Human | Container Test Diagnosis | Lần Docker test đầu thất bại `29 failed, 5 passed, 1 skipped` vì thiếu mount sample data tại `/app/data` | FAIL |
| 2026-10-15 | Human | Container Regression Tests | Sau khi mount `tests` và `data` read-only, toàn bộ Docker suite đạt `46 passed in 1.91s` | PASS |
| 2026-10-15 | Human | Validator Delivery | Commit `2541b8d` được merge qua Pull Request #9 tại `bf63153`; branch local/remote được dọn sau merge | VERIFIED |

## Current Status — Milestone 2

Milestone 2 gate đã có Evidence cho contract, 12-note synthetic fixture corpus, valid-document acceptance và invalid-document rejection. Validator và batch relationship checks đã chạy ở local và trong Docker image.

### Known limitations

- Sample vault là synthetic corpus, không phải dữ liệu production hoặc gold evaluation dataset hoàn chỉnh.
- Note hợp lệ theo frontmatter contract chưa đồng nghĩa được approve, persisted vào PostgreSQL hoặc retrieval-eligible.
- Chưa có PostgreSQL-backed create/replay/update/delete lifecycle trong Milestone 2.

---

## Task: TASK-OPSBRAIN-M3-CORE-STORAGE

- **Feature:** Core Storage Migration (Alembic, SQLAlchemy Models, PostgreSQL Constraints and Indexes)
- **Contract:** `docs/contracts/storage-schema.md` — Accepted `1.0`
- **Migration revision:** `940c9eb1716a`
- **Implementation commit:** `917f703`
- **Merge commit:** `0b40113` — Pull Request #11
- **Accepted scope:**
  1. Khởi tạo Alembic environment dùng database settings của ứng dụng mà không lưu DSN thật trong repository.
  2. Thêm naming convention cho SQLAlchemy metadata.
  3. Tạo bốn bảng core: `organizations`, `principals`, `documents` và `document_versions`.
  4. Thêm các composite foreign key, check constraint, unique constraint và index cần thiết cho identity, version lineage và current-version ownership.
  5. Kiểm chứng migration bằng upgrade, downgrade, catalog query, behavior query, schema-drift check và pytest.

## Core Storage Audit Log

| Timestamp | Actor | Event / Action | Details | Status |
| :--- | :--- | :--- | :--- | :--- |
| 2026-10-15 | Human + Teammate | Storage Contract | Storage schema contract được chốt ở trạng thái Accepted `1.0`; Pull Request #10 merge tại `b379b5a` | ACCEPTED |
| 2026-10-16 | Human | Migration Tooling | Thêm Alembic dependency, async migration environment và SQLAlchemy naming convention | PASS |
| 2026-10-16 | Human | Core Models | Thêm model cho `organizations`, `principals`, `documents` và `document_versions` | PASS |
| 2026-10-16 | Human | Metadata Tests | `tests/test_storage_models.py`: `4 passed`; kiểm tra table, composite key, constraint và index quan trọng | PASS |
| 2026-10-16 | Human | Migration Generation | Tạo revision `940c9eb1716a`; cyclic current-version foreign key được thêm sau khi hai bảng liên quan tồn tại | PASS |
| 2026-10-16 | Human | Migration Upgrade | `python -m alembic upgrade head` chạy thành công trên PostgreSQL Docker | PASS |
| 2026-10-16 | Human | Catalog Verification | PostgreSQL xác nhận năm bảng gồm `alembic_version` và bốn bảng core; bảy constraint cùng ba index trọng yếu tồn tại | PASS |
| 2026-10-16 | Human | Constraint Behavior | Database từ chối cross-document current version, inconsistent delete state và invalid SHA-256 hash | PASS |
| 2026-10-16 | Human | Verification Cleanup | Constraint test chạy trong transaction; `ROLLBACK` hoàn tất và `remaining_verification_rows = 0` | PASS |
| 2026-10-16 | Human | Migration Round Trip | `downgrade base` gỡ bốn bảng core; `upgrade head` tạo lại đủ bảng; revision cuối là `940c9eb1716a (head)` | PASS |
| 2026-10-16 | Human | Schema Drift Check | `python -m alembic check` trả `No new upgrade operations detected.` | PASS |
| 2026-10-16 | Human | Local Regression Tests | Toàn bộ local test suite: `50 passed in 1.06s` | PASS |
| 2026-10-16 | Human | Container Regression Tests | Rebuild image `opsbrain-api`; toàn bộ test suite trong container: `50 passed in 1.87s` | PASS |
| 2026-10-16 | Human + Agent | Privacy and Scope Review | Mười file trong scope không chứa machine-local path, email, credential, token, private key hoặc credential-bearing URL; cached diff check không có lỗi | PASS |
| 2026-10-16 | Human | Git Delivery | Commit `917f703` được merge qua Pull Request #11 tại `0b40113`; local `main` đồng bộ với `origin/main`; feature branch local và remote đã xóa | VERIFIED |
| 2026-10-16 | Human | Review Basis | Huy thực hiện merge theo quyền quyết định đã báo; không có Evidence về một lượt independent code review cho Pull Request #11 trong audit này | RECORDED |

## Current Status — Core Storage

Lát cắt core storage migration đã được triển khai, kiểm chứng trên PostgreSQL Docker và merge vào `main` ngày 2026-10-16. Evidence hiện có chứng minh bốn bảng core, các constraint/index đã kiểm tra và migration round trip hoạt động trong môi trường local đã ghi nhận.

### Known limitations

- Đây chưa phải toàn bộ storage schema contract hoặc toàn bộ Milestone 3.
- Chưa có bảng ingestion job, ACL, derived data, relationship, reindex, review decision hoặc audit event.
- Chưa có immutable-version enforcement trigger, deferred ACL invariant hoặc Evidence tương ứng.
- Chưa triển khai PostgreSQL ingestion cho create, unchanged replay, update, delete, retry hoặc rebuild.
- Chưa có FastAPI ingestion endpoint hoặc document lifecycle end-to-end.
- Việc chạy Alembic từ Windows trong lượt xác minh dùng host-port tạm thời để tránh xung đột với PostgreSQL local; cấu hình local lâu dài chưa được đưa vào repository.

---

## Overall Verified Status Through `0b40113`

| Area | Status | Evidence |
| :--- | :--- | :--- |
| Repository privacy and hygiene | VERIFIED | `.gitignore` được thêm, tracked runtime log bị loại, machine-local/private artifacts được giữ ngoài Git; Pull Request #2 merge tại `8ee1d10` |
| Milestone 0 shared baselines | PARTIAL / DOCUMENTED | Public project specification cùng frontmatter, minimal API và storage contracts đã merge; không dùng document tồn tại để suy ra implementation |
| Milestone 1 infrastructure | VERIFIED LOCALLY | Docker Compose, API, PostgreSQL, pgvector `0.8.7`, health endpoint và container tests có runtime Evidence |
| Milestone 2 sample and validation | VERIFIED LOCALLY | 12 synthetic notes, frontmatter contract `1.0`, 41 validator tests và 46-test local/container suites |
| Milestone 3 storage and ingestion | IN PROGRESS | Bốn core tables và migration revision `940c9eb1716a` verified; ingestion lifecycle và phần storage còn lại chưa triển khai |
| Milestone 4 vector retrieval | NOT IMPLEMENTED | Chưa có Evidence embedding ingestion, pgvector retrieval, evidence response hoặc citation |
| Milestone 5 ACL enforcement | NOT IMPLEMENTED | Chưa có authenticated principal resolution hoặc negative leakage tests trên retrieval path |
| Milestone 6 hybrid retrieval | NOT IMPLEMENTED | Chưa có PostgreSQL full-text retrieval hoặc Reciprocal Rank Fusion runtime |
| Milestone 7 bounded graph | NOT IMPLEMENTED | Chưa có bounded graph traversal runtime hoặc ACL-safe graph integration |
| Milestone 8 evaluation | NOT IMPLEMENTED | Chưa có reviewed gold dataset, controlled experiment runs hoặc metrics report |
| Milestone 9 Obsidian integration | NOT IMPLEMENTED | Chưa có plugin runtime gọi FastAPI |
| Milestone 10 reliability and release | NOT IMPLEMENTED | Chưa có complete CI, machine-readable structured logging, backup/restore, reproducible release demo hoặc portfolio gate |

## Global Limitations and Next Verified Target

- OpsBrain chưa chạy end-to-end từ Obsidian note tới câu trả lời có citation.
- Chưa có Evidence cho content hash persistence, idempotent unchanged replay, update history, delete invalidation, retry hoặc rebuild equivalence.
- Chưa có Evidence cho Ollama embedding/generation, vector search, full-text search, RRF, graph expansion, ACL-safe context, citation hoặc abstention.
- Chưa có Evidence tái hiện toàn bộ hệ thống trên máy thành viên thứ hai.
- Các quyết định merge dựa trên delegated authority được ghi là delegated authority, không được mô tả thành independent review nếu không có review artifact.
- Mục tiêu kiểm chứng tiếp theo là PostgreSQL ingestion vertical slice cho create và unchanged replay, với test chứng minh không tạo document hoặc version trùng.

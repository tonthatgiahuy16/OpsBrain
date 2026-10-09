# OpsBrain — Enterprise Second Brain for IT Operations

OpsBrain là hệ thống Enterprise Second Brain phục vụ quản lý tri thức vận hành công nghệ thông tin, hỗ trợ kỹ sư tìm kiếm bằng chứng, kết nối các sự kiện vận hành và điều tra sự cố thông qua câu trả lời có nguồn trích dẫn, đúng phiên bản và đúng quyền truy cập (ACL).

---

## 1. Trạng thái hiện tại: Milestone 1 — Infrastructure Foundation

Milestone 1 thiết lập toàn bộ nền tảng hạ tầng kỹ thuật ban đầu có thể tái tạo (reproducible) bao gồm:
* **Docker Compose:** Định nghĩa dịch vụ PostgreSQL 16 + pgvector (`db`) và FastAPI application (`api`).
* **Data Layer:** Container PostgreSQL kèm extension `pgvector` được kích hoạt tự động qua migration `migrations/init_pgvector.sql`.
* **Application Layer:** FastAPI service với cấu hình qua Pydantic Settings (`src/config.py`), kết nối async SQLAlchemy (`src/database.py`) và endpoint kiểm tra tính sẵn sàng `GET /health` (`src/main.py`).
* **Observability:** Structured logging chuẩn mực và bộ test tự động (`tests/test_health.py`).

---

## 2. Cấu trúc thư mục (Repository Tree)

```text
OpsBrain/
├── .env.example               # Mẫu cấu hình biến môi trường
├── AUDIT.md                   # Bảng kiểm soát trách nhiệm giải trình (Gate 0)
├── Dockerfile                 # Image build cho FastAPI app
├── README.md                  # Hướng dẫn hạ tầng và vận hành
├── docker-compose.yml         # File điều phối container cục bộ
├── logs/
│   └── runtime.log            # Nhật ký runtime của dự án (Gate 0)
├── migrations/
│   └── init_pgvector.sql      # Migration kích hoạt extension pgvector
├── project very strong.docx   # Tài liệu đặc tả gốc
├── requirements.txt           # Danh mục dependencies Python
├── src/
│   ├── __init__.py
│   ├── config.py              # Pydantic Settings
│   ├── database.py            # Async engine & pgvector readiness check
│   └── main.py                # FastAPI entrypoint & /health endpoints
└── tests/
    ├── __init__.py
    └── test_health.py         # Test suite kiểm thử liveness và readiness
```

---

## 3. Hướng dẫn khởi động hạ tầng bằng Docker Compose

### Bước 1: Chuẩn bị biến môi trường
Tạo file `.env` từ file mẫu:
```bash
cp .env.example .env
```

### Bước 2: Khởi chạy các container
```bash
docker compose up -d --build
```

### Bước 3: Kiểm tra trạng thái containers
```bash
docker compose ps
```

### Bước 4: Kiểm tra tính sẵn sàng qua API
Truy cập endpoint `/health`:
```bash
curl -s http://localhost:8000/health | jq .
```
Phản hồi mẫu khi hệ thống sẵn sàng:
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

---

## 4. Chạy kiểm thử tự động (Automated Tests)

Chạy pytest trong môi trường local:
```bash
pytest tests/ -v
```

---

## 5. Lộ trình phát triển tiếp theo

* **Milestone 2 — End-to-End Vertical Slice:** Triển khai luồng đọc Markdown, xác thực frontmatter, chunking, tạo embeddings qua Ollama local, lưu trữ pgvector và truy xuất cơ bản.
* **Milestone 3 — Core Capabilities:** Hybrid Retrieval (RRF), Bounded Graph Expansion, ACL Enforcement, Citations & Refusal, Obsidian Plugin.
* **Milestone 4 — Reliability & Demonstration:** Kịch bản nghiệm thu Payment-service 503, Evaluation dataset, Backup/Restore & Verification.

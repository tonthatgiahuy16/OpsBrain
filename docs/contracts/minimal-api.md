# OpsBrain Minimal API Contract

- **Status:** Accepted
- **Contract version:** `1.0`
- **Primary owner:** Backend and Data Engineering
- **Reviewer:** Corpus and Evaluation
- **Decision date:** `2026-10-15`
- **Acceptance basis:** Project owner decision under delegated authority from the reviewer
- **Target milestones:** Milestone 2 to Milestone 9

## 1. Purpose

Contract này định nghĩa giao diện HTTP tối thiểu giữa Obsidian hoặc client, FastAPI, ingestion pipeline, document lifecycle và retrieval.

Mục tiêu là giúp các thành phần có thể được phát triển độc lập nhưng vẫn thống nhất endpoint, request, response, error và security boundary.

Contract mô tả hành vi dự kiến. Sự tồn tại của tài liệu này không chứng minh endpoint đã được triển khai hoặc kiểm thử.

## 2. Scope

Contract baseline bao gồm:

- Gửi document batch để ingestion.
- Theo dõi trạng thái ingestion job.
- Đọc trạng thái document và version hiện tại.
- Ghi nhận quyết định approve hoặc reject.
- Gửi truy vấn và nhận evidence cùng citation.
- Yêu cầu reindex dữ liệu dẫn xuất.
- Kiểm tra trạng thái hệ thống.

Contract chưa bao gồm:

- Giao thức WebSocket hoặc streaming response.
- Agent tự động thực thi code hay remediation.
- API quản trị người dùng và role hoàn chỉnh.
- Public hoặc anonymous access.
- Kết nối trực tiếp từ client tới PostgreSQL hoặc Ollama.

## 3. Source of truth and security boundaries

- PostgreSQL là nguồn dữ liệu và trạng thái chính thức.
- Client chỉ giao tiếp với FastAPI.
- Principal phải được tạo từ authentication context đáng tin cậy của backend.
- API không được tin `user_id`, team, role hoặc capability do client tự khai trong request body.
- Frontmatter không thể tự cấp quyền, tự approve hoặc tự thay đổi lifecycle state.
- ACL phải được áp dụng trước retrieval, ranking, context construction và citation.
- Error, log và audit event không được làm lộ secret hoặc nội dung trái quyền.
- Chunk, embedding, search index và relationship là dữ liệu dẫn xuất có thể rebuild.

## 4. General conventions

- Base path: `/api/v1`
- Request và response sử dụng JSON UTF-8, trừ nội dung Markdown được khai báo rõ.
- Datetime sử dụng RFC 3339 có timezone.
- Backend trả datetime chuẩn hóa theo UTC.
- Mỗi request được gắn `request_id` để truy vết.
- Ingestion và reindex là asynchronous job.
- Job được chấp nhận trả HTTP `202 Accepted`.
- Resource được đọc thành công trả HTTP `200 OK`.
- Validation error trả HTTP `422 Unprocessable Entity`.
- Authentication failure trả HTTP `401 Unauthorized`.
- Authorization failure trả HTTP `403 Forbidden` khi response không làm lộ sự tồn tại của protected resource.
- Resource không tồn tại hoặc principal không được phép biết resource tồn tại trả HTTP `404 Not Found`.

## 5. Endpoint summary

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/ingestions` | Gửi document batch để ingestion |
| `GET` | `/api/v1/ingestions/{job_id}` | Xem trạng thái ingestion job |
| `GET` | `/api/v1/documents/{document_id}` | Xem trạng thái document |
| `POST` | `/api/v1/documents/{document_id}/review-decisions` | Approve hoặc reject document |
| `POST` | `/api/v1/queries` | Truy xuất evidence và citation |
| `POST` | `/api/v1/reindex-jobs` | Yêu cầu rebuild dữ liệu dẫn xuất |
| `GET` | `/api/v1/reindex-jobs/{job_id}` | Xem trạng thái reindex job |
| `GET` | `/health` | Kiểm tra database và pgvector |
| `GET` | `/health/liveness` | Kiểm tra tiến trình API |

## 6. Shared response rules

Mọi response nghiệp vụ phải có:

- `request_id`
- Trạng thái hoặc kết quả tương ứng với endpoint
- Identifier ổn định của resource
- Error hoặc warning có cấu trúc khi cần

Response không được:

- Trả ACL metadata của tài liệu mà principal không được phép biết.
- Xác nhận sự tồn tại của restricted document cho principal trái quyền.
- Trả raw exception, database query hoặc stack trace cho client.
- Dùng HTTP `200` để che một ingestion hoặc validation failure.

## 7. Accepted decisions

Hai thành viên chấp nhận các quyết định baseline sau:

1. Client gửi cả `source_uri` và `markdown_content`.
2. Một ingestion request chứa một batch không rỗng; ingest một document sử dụng batch có một phần tử.
3. Client gửi `Idempotency-Key` trong HTTP header; backend vẫn tính content hash cho từng document.
4. Prototype sử dụng opaque Bearer token được backend ánh xạ sang principal đáng tin cậy.
5. Query sử dụng một endpoint với `response_mode: evidence_only | answer`.
6. Approval yêu cầu capability `document:approve`; reindex yêu cầu capability `index:rebuild`.

Các quyết định này là baseline của contract version `0.1`. Thay đổi semantic phải được review và cập nhật contract version.

## 8. Create ingestion job

### 8.1 Request

```http
POST /api/v1/ingestions
Authorization: Bearer <opaque-token>
Idempotency-Key: <client-generated-key>
Content-Type: application/json
```

Principal được backend xác định từ Bearer token. Request body không được chứa `user_id`, team, role hoặc capability để thay thế authentication context.

Request body:

```json
{
  "documents": [
    {
      "source_uri": "obsidian://sample-vault/payment-service/services/payment-service.md",
      "markdown_content": "---\nschema_version: 1\ndocument_id: payment-service\n...\n---\n\n# Payment Service"
    }
  ]
}
```

### 8.2 Request rules

- `documents` là danh sách bắt buộc và không được rỗng.
- Mỗi phần tử phải có `source_uri` và `markdown_content`.
- `source_uri` là logical source identifier, không phải quyền yêu cầu backend đọc một đường dẫn filesystem tùy ý.
- `markdown_content` chứa frontmatter và Markdown body hoàn chỉnh.
- Client không gửi content hash, database version, chunk ID hoặc embedding.
- Backend phải thu thập identity của toàn bộ document trong batch trước khi resolve relationship.
- Hai document có cùng `document_id` trong một batch phải được ghi nhận là validation failure.
- Full document validation và relationship resolution được thực hiện trong ingestion job.
- Endpoint yêu cầu capability `document:ingest`.

### 8.3 Accepted response

HTTP status:

```http
202 Accepted
```

Response body:

```json
{
  "request_id": "req_01J...",
  "job_id": "ing_01J...",
  "status": "queued",
  "submitted_document_count": 1,
  "idempotency_replayed": false,
  "status_url": "/api/v1/ingestions/ing_01J..."
}
```

### 8.4 Idempotency behavior

- Gửi lại cùng `Idempotency-Key` và cùng canonical request payload không tạo ingestion job mới.
- Backend trả lại `job_id` đã gắn với lần gửi đầu tiên và đặt `idempotency_replayed: true`.
- Dùng lại cùng `Idempotency-Key` với payload khác trả HTTP `409 Conflict`.
- Request-level idempotency không thay thế document-level content hash.
- Replay cùng `document_id` và content hash không được tạo active version, chunk hoặc relationship trùng.

### 8.5 Processing behavior

- HTTP `202` chỉ xác nhận job đã được tiếp nhận; không chứng minh document đã ingest thành công.
- Validation failure không được tạo active version hoặc derived record cho document lỗi.
- Mỗi document phải được xử lý trong transaction boundary ngăn trạng thái nửa vời.
- Batch có thể chứa kết quả thành công, warning và failure theo từng document.
- Job phải ghi đủ kết quả để endpoint trạng thái trả về mà không làm lộ nội dung trái quyền.

### 8.6 Immediate errors

| HTTP status | Error code | Meaning |
|---|---|---|
| `401` | `authentication_required` | Thiếu hoặc sai Bearer token |
| `403` | `ingestion_forbidden` | Principal không có `document:ingest` |
| `409` | `idempotency_conflict` | Cùng idempotency key nhưng payload khác |
| `422` | `invalid_request` | JSON envelope sai hoặc `documents` rỗng |

## 9. Get ingestion job status

### 9.1 Request

```http
GET /api/v1/ingestions/{job_id}
Authorization: Bearer <opaque-token>
```

Principal chỉ được xem job do mình tạo. Principal vận hành có capability `ingestion:read` có thể xem job trong cùng organization.

Job không tồn tại hoặc principal không được phép biết job tồn tại đều trả HTTP `404 Not Found`.

### 9.2 Job states

| Status | Meaning |
|---|---|
| `queued` | Job đang chờ xử lý |
| `running` | Job đang xử lý document |
| `completed` | Không có document nào thất bại |
| `completed_with_errors` | Có cả document thành công và thất bại |
| `failed` | Job lỗi ở cấp hệ thống hoặc không có document nào thành công |

Warning không tự làm job thất bại. Job `completed` vẫn có thể chứa warning.

### 9.3 Successful response

```json
{
  "request_id": "req_01J...",
  "job_id": "ing_01J...",
  "status": "completed_with_errors",
  "submitted_at": "2026-10-15T03:00:00Z",
  "started_at": "2026-10-15T03:00:01Z",
  "completed_at": "2026-10-15T03:00:03Z",
  "summary": {
    "submitted": 2,
    "succeeded": 1,
    "failed": 1,
    "warnings": 0
  },
  "documents": [
    {
      "source_uri": "obsidian://sample-vault/payment-service/services/payment-service.md",
      "document_id": "payment-service",
      "outcome": "created",
      "version": 1,
      "warnings": [],
      "errors": []
    },
    {
      "source_uri": "obsidian://sample-vault/payment-service/services/invalid-service.md",
      "document_id": null,
      "outcome": "failed",
      "version": null,
      "warnings": [],
      "errors": [
        {
          "code": "missing_required_field",
          "field": "document_id",
          "message": "Required field is missing"
        }
      ]
    }
  ]
}
```

### 9.4 Document outcomes

| Outcome | Meaning |
|---|---|
| `created` | Tạo document và active version đầu tiên |
| `updated` | Tạo version mới cho document hiện có |
| `unchanged` | Content hash không đổi; không tạo version hoặc derived record trùng |
| `failed` | Document không được tạo hoặc cập nhật |

Optional broken relationship tạo warning nhưng không được tạo dangling edge.

### 9.5 Response rules

- `started_at` là `null` khi job còn `queued`.
- `completed_at` là `null` khi job chưa kết thúc.
- `document_id` có thể là `null` khi frontmatter không parse được hoặc thiếu ID.
- `version` có thể là `null` khi document thất bại trước khi tạo version.
- Error chỉ mô tả code, field và message an toàn.
- Response không được chứa raw Markdown, secret, stack trace hoặc nội dung trái quyền.
- Kết quả phải phân biệt document-level failure với job-level failure.

### 9.6 Immediate errors

| HTTP status | Error code | Meaning |
|---|---|---|
| `401` | `authentication_required` | Thiếu hoặc sai Bearer token |
| `404` | `ingestion_job_not_found` | Job không tồn tại hoặc principal không được phép biết job tồn tại |

## 10. Get document state

### 10.1 Request

```http
GET /api/v1/documents/{document_id}
Authorization: Bearer <opaque-token>
```

Endpoint này trả trạng thái quản lý của document, không phải nội dung evidence dùng để trả lời câu hỏi.

Principal phải có capability `document:read_state` và phải thỏa ACL hiện hành của document.

Document không tồn tại hoặc principal không được phép biết document tồn tại đều trả HTTP `404 Not Found`.

### 10.2 Successful response

```json
{
  "request_id": "req_01J...",
  "document": {
    "document_id": "payment-service",
    "document_type": "service",
    "title": "Payment Service",
    "document_status": "active",
    "review_state": "in_review",
    "owner_team": "payments-platform",
    "provenance_type": "synthetic",
    "access_scope": "organization",
    "source_uri": "obsidian://sample-vault/payment-service/services/payment-service.md",
    "current_version": 1,
    "retrieval_eligible": false,
    "ineligibility_reasons": [
      "review_state_not_approved"
    ],
    "query_ready": false,
    "index_status": "not_eligible",
    "latest_ingestion_job_id": "ing_01J...",
    "created_at": "2026-10-15T03:00:03Z",
    "updated_at": "2026-10-15T03:00:03Z"
  }
}
```

### 10.3 Retrieval eligibility

`retrieval_eligible` chỉ được đặt thành `true` khi tất cả điều kiện sau đều đúng:

- `document_status` là `active`.
- `review_state` là `approved`.
- Document nằm trong khoảng hiệu lực tại thời điểm kiểm tra.
- Principal thỏa ACL hiện hành.

Các giá trị `ineligibility_reasons` baseline gồm:

| Reason | Meaning |
|---|---|
| `document_not_active` | Document là `deprecated` hoặc `revoked` |
| `review_state_not_approved` | Document chưa được approve |
| `outside_effective_period` | Document chưa có hiệu lực hoặc đã hết hiệu lực |

Không trả lý do ACL cho principal trái quyền; trường hợp đó toàn bộ request trả `404`.

### 10.4 Index states

| Status | Meaning |
|---|---|
| `not_eligible` | Document chưa đủ điều kiện tạo hoặc dùng derived index |
| `pending` | Đang chờ tạo derived data |
| `ready` | Derived data cần thiết đã sẵn sàng |
| `stale` | Derived data không còn khớp current version hoặc ACL |
| `failed` | Lần tạo derived data gần nhất thất bại |


`index_status: ready` không tự làm document đủ điều kiện retrieval nếu lifecycle, review state, temporal validity hoặc ACL không hợp lệ.
`query_ready` chỉ được đặt thành `true` khi `retrieval_eligible` là `true` và `index_status` là `ready`.

Document có thể đủ điều kiện retrieval nhưng chưa query được khi derived data còn `pending`, `stale` hoặc `failed`.

### 10.5 Response rules

- Endpoint không trả raw Markdown body, chunk, embedding hoặc generated answer.
- Endpoint không trả `allowed_users`, `allowed_teams`, `allowed_roles` hoặc capability mapping.
- `current_version` là version do backend quản lý, không phải giá trị client tự khai.
- `source_uri` chỉ được trả sau khi principal đã vượt qua authorization.
- `latest_ingestion_job_id` có thể là `null` nếu chưa có job truy vết được.
- Mọi trạng thái eligibility phải được backend tính từ dữ liệu chính thức.

### 10.6 Immediate errors

| HTTP status | Error code | Meaning |
|---|---|---|
| `401` | `authentication_required` | Thiếu hoặc sai Bearer token |
| `404` | `document_not_found` | Document không tồn tại hoặc principal không được phép biết document tồn tại |

## 11. Create review decision

### 11.1 Request

```http
POST /api/v1/documents/{document_id}/review-decisions
Authorization: Bearer <opaque-token>
Idempotency-Key: <client-generated-key>
Content-Type: application/json
```

Request body:

```json
{
  "decision": "approve",
  "expected_version": 1,
  "reason": "Reviewed against the accepted frontmatter contract and synthetic scenario."
}
```

### 11.2 Request rules

- `decision` chỉ nhận `approve` hoặc `reject`.
- `expected_version` là current version mà reviewer đã kiểm tra.
- `reason` là chuỗi bắt buộc và không được rỗng.
- Endpoint yêu cầu capability `document:approve`.
- Principal quyết định phải khác principal đã submit hoặc sửa current version.
- Backend phải xác định principal từ authentication context.
- Request body không được chứa reviewer ID, role hoặc capability tự khai.
- Document phải đang ở `review_state: in_review`.
- Backend không được chấp nhận approval nếu không truy vết được actor identity.

### 11.3 Approved response

HTTP status:

```http
200 OK
```

Response body:

```json
{
  "request_id": "req_01J...",
  "review_decision_id": "rev_01J...",
  "document_id": "payment-service",
  "decision": "approve",
  "previous_version": 1,
  "current_version": 2,
  "previous_review_state": "in_review",
  "review_state": "approved",
  "retrieval_eligible": true,
  "query_ready": false,
  "index_status": "pending",
  "decided_at": "2026-10-15T04:00:00Z"
}
```
Giá trị `retrieval_eligible` phải được backend tính lại sau decision. Approval không tự buộc giá trị này thành `true` nếu document không active, nằm ngoài temporal validity hoặc principal không thỏa ACL.
Review-state change là semantic metadata change, vì vậy backend phải tạo document version mới và audit event.

Approval không đồng nghĩa document có thể query ngay. Derived data của version mới phải đạt `index_status: ready` trước khi `query_ready` trở thành `true`.

### 11.4 Rejection behavior

Khi `decision` là `reject`:

- `review_state` chuyển từ `in_review` sang `rejected`.
- Backend tạo version mới và audit event.
- Document không đủ điều kiện retrieval.
- Derived data có liên quan phải bị vô hiệu hóa.
- Response trả `retrieval_eligible: false`, `query_ready: false` và `index_status: not_eligible`.

### 11.5 Idempotency and concurrency

- Gửi lại cùng `Idempotency-Key` và cùng payload trả lại cùng `review_decision_id`.
- Dùng lại cùng key với payload khác trả `409 idempotency_conflict`.
- `expected_version` không khớp current version trả `409 version_conflict`.
- Decision không hợp lệ với current review state trả `409 invalid_review_transition`.
- Backend phải kiểm tra version và ghi decision trong cùng transaction boundary.
- Concurrent requests không được tạo hai approval version từ cùng `expected_version`.

### 11.6 Authorization and disclosure

- Document không tồn tại hoặc principal không được phép biết document tồn tại trả `404 document_not_found`.
- Principal thấy được document nhưng thiếu `document:approve` trả `403 approval_forbidden`.
- Principal là người submit hoặc sửa current version trả `403 independent_review_required`.
- Error không được tiết lộ ACL member list hoặc danh tính reviewer khác.

### 11.7 Immediate errors

| HTTP status | Error code | Meaning |
|---|---|---|
| `401` | `authentication_required` | Thiếu hoặc sai Bearer token |
| `403` | `approval_forbidden` | Principal thiếu capability approval |
| `403` | `independent_review_required` | Reviewer không độc lập với current version |
| `404` | `document_not_found` | Document không tồn tại hoặc không được phép tiết lộ |
| `409` | `idempotency_conflict` | Cùng key nhưng payload khác |
| `409` | `version_conflict` | `expected_version` không còn hiện hành |
| `409` | `invalid_review_transition` | Document không ở trạng thái `in_review` |
| `422` | `invalid_request` | Decision, version hoặc reason không hợp lệ |

## 12. Execute query

### 12.1 Request

```http
POST /api/v1/queries
Authorization: Bearer <opaque-token>
Content-Type: application/json
```

Request body:

```json
{
  "question": "What caused the Payment Service HTTP 503 incident?",
  "response_mode": "evidence_only",
  "context": {
    "active_document_id": "payment-service"
  },
  "retrieval": {
    "strategy": "auto",
    "top_k": 5
  }
}
```

### 12.2 Request rules

- `question` là chuỗi bắt buộc và không được rỗng.
- `response_mode` nhận `evidence_only` hoặc `answer`.
- `context` là optional.
- `active_document_id` giúp retrieval ưu tiên ngữ cảnh note đang mở nhưng không được bỏ qua ACL.
- `strategy` nhận `auto`, `vector`, `hybrid` hoặc `hybrid_graph`.
- `top_k` là số nguyên từ `1` đến `20`.
- Endpoint yêu cầu capability `query:execute`.
- Principal, organization, team và role được lấy từ authentication context.
- Request body không được tự khai principal hoặc ACL.
- Strategy chưa được triển khai phải trả lỗi rõ ràng, không âm thầm đổi sang strategy khác.

### 12.3 Evidence-only response

```json
{
  "request_id": "req_01J...",
  "query_id": "qry_01J...",
  "status": "evidence_returned",
  "response_mode": "evidence_only",
  "strategy_used": "vector",
  "answer": null,
  "abstained": false,
  "abstention_reason": null,
  "evidence": [
    {
      "citation_id": "cit_1",
      "document_id": "payment-service-http-503-2026-10-01",
      "document_version": 2,
      "chunk_id": "chk_01J...",
      "document_type": "incident",
      "title": "Payment Service HTTP 503 Incident",
      "source_uri": "obsidian://sample-vault/payment-service/incidents/payment-service-http-503.md",
      "excerpt": "Payment Service returned an elevated number of HTTP 503 responses...",
      "scores": {
        "vector": 0.84,
        "full_text": null,
        "rrf": null
      },
      "relationship_path": []
    }
  ],
  "configuration": {
    "retrieval_config_id": "retrieval-v1",
    "embedding_model": "nomic-embed-text",
    "generator_model": null
  },
  "latency_ms": {
    "retrieval": 42,
    "generation": null,
    "total": 45
  }
}
```

### 12.4 Answer response rules

Khi `response_mode` là `answer`:

- Retrieval và ACL filtering phải hoàn thành trước khi gọi generator.
- `answer` chứa câu trả lời dựa trên authorized evidence.
- Mọi citation trong answer phải tham chiếu một `citation_id` trong danh sách `evidence`.
- `generator_model` phải ghi model identifier thực tế.
- Không được đưa unauthorized document, chunk hoặc relationship vào model context.
- Không được sinh câu trả lời nếu evidence không đủ hoặc không còn hợp lệ.
- Generated answer không thay thế source document.

### 12.5 Abstention response

Khi không có đủ evidence hợp lệ, API trả HTTP `200 OK` với response:

```json
{
  "request_id": "req_01J...",
  "query_id": "qry_01J...",
  "status": "abstained",
  "response_mode": "answer",
  "strategy_used": "hybrid",
  "answer": null,
  "abstained": true,
  "abstention_reason": "no_eligible_evidence",
  "evidence": [],
  "configuration": {
    "retrieval_config_id": "retrieval-v1",
    "embedding_model": "nomic-embed-text",
    "generator_model": null
  },
  "latency_ms": {
    "retrieval": 31,
    "generation": null,
    "total": 33
  }
}
```

Các `abstention_reason` baseline gồm:

| Reason | Meaning |
|---|---|
| `no_eligible_evidence` | Không còn evidence sau lifecycle, temporal và ACL filtering |
| `insufficient_evidence` | Evidence còn lại không đủ hỗ trợ câu trả lời |
| `conflicting_evidence` | Evidence hợp lệ mâu thuẫn và chưa thể kết luận an toàn |

Abstention do thiếu evidence không phải server error. Generator không được gọi khi API đã quyết định abstain.

### 12.6 Enforcement order

Query path phải thực hiện theo thứ tự:

1. Xác thực Bearer token và resolve principal.
2. Xác định organization, team, role và capability từ backend.
3. Lọc document theo organization, ACL, lifecycle, review state, temporal validity và `query_ready`.
4. Thực hiện vector hoặc full-text candidate retrieval trên tập đã lọc.
5. Ranking hoặc Reciprocal Rank Fusion.
6. Nếu dùng graph, kiểm tra ACL tại từng relationship và từng document được mở rộng.
7. Xây context chỉ từ authorized evidence.
8. Gọi generator khi `response_mode: answer` và evidence đủ.
9. Tạo citation chỉ từ evidence đã được phép.
10. Ghi audit event, strategy, evidence ID, score và latency.

ACL không được chỉ áp dụng sau ranking hoặc sau khi model đã nhận context.

### 12.7 Evidence disclosure rules

- Chỉ trả document, chunk, score, relationship và citation đã được principal phép xem.
- Không trả số lượng candidate trước ACL filtering.
- Không xác nhận restricted document đã bị loại khỏi kết quả.
- `relationship_path` chỉ được chứa document và edge đã qua ACL.
- `excerpt` phải đến từ đúng document version được citation.
- Score của các strategy khác nhau không mặc nhiên có cùng ý nghĩa.
- `source_uri` chỉ được trả sau authorization.
- Audit log không được chứa Bearer token hoặc raw unauthorized content.

Mỗi phần tử `relationship_path` phải có `from_document_id`, `relationship_type`, `to_document_id` và `depth`. Mọi document cùng edge trong path phải vượt qua ACL trước khi được trả về.

### 12.8 Immediate errors

| HTTP status | Error code | Meaning |
|---|---|---|
| `401` | `authentication_required` | Thiếu hoặc sai Bearer token |
| `403` | `query_forbidden` | Principal thiếu `query:execute` |
| `404` | `context_document_not_found` | Active document không tồn tại hoặc không được phép tiết lộ |
| `422` | `invalid_request` | Question, response mode, strategy hoặc top-k không hợp lệ |
| `422` | `unsupported_retrieval_strategy` | Strategy hợp lệ theo contract nhưng chưa được triển khai |
| `503` | `retrieval_unavailable` | Thành phần retrieval cần thiết tạm thời không hoạt động |
| `503` | `generation_unavailable` | Generator cần cho answer mode tạm thời không hoạt động |

## 13. Reindex derived data

### 13.1 Create request

```http
POST /api/v1/reindex-jobs
Authorization: Bearer <opaque-token>
Idempotency-Key: <client-generated-key>
Content-Type: application/json
```

Request body cho một nhóm document:

```json
{
  "scope": {
    "type": "documents",
    "document_ids": [
      "payment-service",
      "payment-service-http-503-2026-10-01"
    ]
  },
  "targets": [
    "chunks",
    "embeddings",
    "full_text",
    "relationships"
  ],
  "reason": "Rebuild derived data after changing the chunking configuration."
}
```

Request body cho toàn organization:

```json
{
  "scope": {
    "type": "organization"
  },
  "targets": [
    "chunks",
    "embeddings",
    "full_text",
    "relationships"
  ],
  "reason": "Rebuild all eligible derived data after upgrading the embedding model."
}
```

### 13.2 Request rules

- Endpoint yêu cầu capability `index:rebuild`.
- Organization được lấy từ authenticated principal, không nhận organization ID do client tự khai.
- `scope.type` nhận `documents` hoặc `organization`.
- `document_ids` bắt buộc và không được rỗng khi scope là `documents`.
- `document_ids` không được xuất hiện khi scope là `organization`.
- `targets` là danh sách không rỗng gồm `chunks`, `embeddings`, `full_text` hoặc `relationships`.
- `reason` là chuỗi bắt buộc và không được rỗng.
- Reindex sử dụng source document và current version trong PostgreSQL.
- Request không được yêu cầu backend đọc một filesystem path tùy ý.
- Reindex không được thay đổi source content, document version, review state hoặc ACL.

### 13.3 Accepted response

HTTP status:

```http
202 Accepted
```

Response body:

```json
{
  "request_id": "req_01J...",
  "job_id": "idx_01J...",
  "status": "queued",
  "scope_type": "documents",
  "requested_document_count": 2,
  "targets": [
    "chunks",
    "embeddings",
    "full_text",
    "relationships"
  ],
  "idempotency_replayed": false,
  "status_url": "/api/v1/reindex-jobs/idx_01J..."
}
```

HTTP `202` chỉ xác nhận reindex job đã được tiếp nhận; không chứng minh derived data đã rebuild thành công.

### 13.4 Status request

```http
GET /api/v1/reindex-jobs/{job_id}
Authorization: Bearer <opaque-token>
```

Principal chỉ được xem job do mình tạo. Principal vận hành có capability `index:read` có thể xem job trong cùng organization.

### 13.5 Status response

```json
{
  "request_id": "req_01J...",
  "job_id": "idx_01J...",
  "status": "completed",
  "scope_type": "documents",
  "targets": [
    "chunks",
    "embeddings",
    "full_text",
    "relationships"
  ],
  "configuration": {
    "chunking_version": "chunk-v1",
    "embedding_model": "nomic-embed-text"
  },
  "summary": {
    "requested": 2,
    "rebuilt": 2,
    "skipped": 0,
    "failed": 0
  },
  "failures": [],
  "submitted_at": "2026-10-15T05:00:00Z",
  "started_at": "2026-10-15T05:00:01Z",
  "completed_at": "2026-10-15T05:00:08Z"
}
```
Khi job có failure, mỗi phần tử `failures` chỉ chứa `document_id`, stable error `code` và safe `message`. Không trả raw document content, exception hoặc internal path.

Job status nhận `queued`, `running`, `completed`, `completed_with_errors` hoặc `failed`.

### 13.6 Safety and consistency rules

- Reindex phải giữ nguyên ACL của source document.
- Derived record mới phải truy ngược được tới đúng document version.
- Reindex không được làm document trái quyền xuất hiện trong candidate, graph, context hoặc citation.
- Job phải ghi document version, chunking version, embedding model và configuration đã sử dụng.
- Nếu document version thay đổi trong khi rebuild, derived data của version cũ không được kích hoạt cho current version.
- Derived data thay thế chỉ được kích hoạt sau khi tập cần thiết hoàn thành nhất quán.
- Reindex thất bại không được để trạng thái nửa cũ, nửa mới trở thành active.
- Document đã `deprecated`, `revoked`, ngoài temporal validity hoặc chưa `approved` phải được skip hoặc invalidate theo contract.
- Thu hồi ACL trong lúc rebuild phải thắng kết quả reindex và ngăn dữ liệu cũ tiếp tục được truy xuất.

### 13.7 Idempotency behavior

- Cùng `Idempotency-Key` và cùng canonical payload không tạo job mới.
- Backend trả lại cùng `job_id` và đặt `idempotency_replayed: true`.
- Cùng key nhưng payload khác trả `409 idempotency_conflict`.
- Chạy lại cùng source version và configuration phải tạo logical derived state tương đương.
- Rebuild không được tạo active chunk hoặc relationship trùng.

### 13.8 Authorization and disclosure

- Document scope chứa ID không tồn tại hoặc trái quyền trả lỗi `404` chung.
- Job không tồn tại hoặc principal không được phép biết job tồn tại trả `404`.
- Response không trả raw document body, embedding vector, Bearer token hoặc ACL member list.
- Summary không được làm lộ số lượng document ngoài organization hoặc ngoài quyền của principal.

### 13.9 Immediate errors

| HTTP status | Error code | Meaning |
|---|---|---|
| `401` | `authentication_required` | Thiếu hoặc sai Bearer token |
| `403` | `reindex_forbidden` | Principal thiếu `index:rebuild` |
| `404` | `reindex_scope_not_found` | Document scope không tồn tại hoặc không được phép tiết lộ |
| `404` | `reindex_job_not_found` | Job không tồn tại hoặc không được phép tiết lộ |
| `409` | `idempotency_conflict` | Cùng key nhưng payload khác |
| `422` | `invalid_request` | Scope, targets hoặc reason không hợp lệ |

## 14. Shared errors and health checks

### 14.1 Error envelope

Mọi business API error sử dụng cấu trúc:

```json
{
  "request_id": "req_01J...",
  "error": {
    "code": "validation_failed",
    "message": "Request validation failed.",
    "retryable": false,
    "details": [
      {
        "code": "missing_required_field",
        "field": "documents[0].source_uri",
        "message": "Required field is missing"
      }
    ]
  }
}
```

### 14.2 Error rules

- `request_id` phải xuất hiện để đối chiếu với log và audit event.
- `code` là machine-readable identifier ổn định.
- `message` là mô tả an toàn cho người dùng.
- `retryable` cho biết client có thể thử lại cùng thao tác hay không.
- `details` là danh sách optional cho validation error.
- `field` sử dụng đường dẫn field trong request; không chứa database column hoặc filesystem path nội bộ.
- HTTP `401`, `403`, `404`, `409` và `422` mặc định có `retryable: false`.
- HTTP `503` có thể có `retryable: true`.
- Khi phù hợp, response retryable có thể gửi header `Retry-After`.
- HTTP `500` sử dụng code `internal_error` và message chung.
- Response không được trả raw exception, stack trace, SQL statement, DSN, Bearer token hoặc secret.
- Chi tiết lỗi nội bộ chỉ được ghi vào log an toàn cùng `request_id`.
- Resource không tồn tại và resource trái quyền phải dùng cùng thông báo an toàn khi cần chống existence leakage.

### 14.3 Health endpoints

Health endpoints giữ unversioned path vì được dùng bởi container và công cụ vận hành.

#### Liveness

```http
GET /health/liveness
```

Successful response:

```json
{
  "status": "alive"
}
```

Liveness chỉ kiểm tra tiến trình API đang chạy. Endpoint không kiểm tra PostgreSQL, pgvector hoặc Ollama.

#### Readiness

```http
GET /health
```

Healthy response trả HTTP `200 OK`:

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

Unhealthy response trả HTTP `503 Service Unavailable`.

### 14.4 Health disclosure rules

- Baseline cho phép health endpoints không cần authentication.
- Response chỉ trả trạng thái tối thiểu cần cho vận hành.
- Trường `error` chỉ được chứa stable sanitized error code hoặc `null`.
- Health response không được trả hostname nội bộ, port, database name, username, DSN, exception hoặc stack trace.
- Liveness không được phụ thuộc vào external component.
- Readiness chỉ được đặt `healthy` khi PostgreSQL kết nối được và pgvector sẵn sàng.
- Việc Ollama chưa sẵn sàng không làm API liveness thất bại; readiness cho query có thể được phản ánh bằng component riêng trong contract version sau.

## 15. Acceptance criteria and deferred scope

### 15.1 Capability summary

| Operation | Required authorization |
|---|---|
| Create ingestion job | `document:ingest` |
| Read own ingestion job | Job creator |
| Read another ingestion job in the same organization | `ingestion:read` |
| Read document state | `document:read_state` and current document ACL |
| Approve or reject document | `document:approve`, current ACL and independent-review rule |
| Execute query | `query:execute` and ACL filtering for every evidence item |
| Create reindex job | `index:rebuild` |
| Read own reindex job | Job creator |
| Read another reindex job in the same organization | `index:read` |
| Read health endpoints | No authentication in baseline |

Capability được backend ánh xạ từ authenticated principal. Client không được tự khai capability trong request body hoặc frontmatter.

### 15.2 Contract acceptance gate

Contract version `0.1` được chấp nhận khi:

- Hai thành viên review toàn bộ contract hoặc reviewer ủy quyền rõ ràng cho project owner đưa ra quyết định chấp nhận cuối cùng.
- Endpoint summary khớp với các section chi tiết.
- Không còn open decision chưa được ghi rõ.
- Authentication context, capability và ACL boundary được hai thành viên giải thích thống nhất.
- Ingestion lifecycle phân biệt create, unchanged replay, update và failure.
- Approval bảo đảm capability, independent reviewer và optimistic concurrency.
- Query contract bảo đảm ACL trước ranking, context, generation và citation.
- Reindex contract bảo toàn document version và ACL.
- Error response không làm lộ secret, internal exception hoặc restricted-resource existence.
- Known implementation gaps được ghi rõ.
- Status được đổi từ `Proposed` thành `Accepted`.
- Contract version được đổi từ `0.1` thành `1.0`.

Việc chấp nhận contract không chứng minh endpoint đã được triển khai hoặc kiểm thử.

### 15.3 Required implementation evidence

Khi triển khai, tối thiểu phải có automated test hoặc reproducible runtime Evidence cho:

1. Valid ingestion request trả `202` và tạo đúng một job.
2. Retry cùng idempotency key và payload trả lại cùng job.
3. Cùng idempotency key nhưng payload khác trả `409`.
4. Invalid frontmatter được ghi nhận theo document mà không tạo active version hoặc derived record.
5. Unchanged replay không tạo version, chunk hoặc relationship trùng.
6. Document state phản ánh đúng lifecycle, review, temporal, index và query readiness.
7. Unauthorized document lookup trả cùng hành vi `404` như document không tồn tại.
8. Self-approval bị từ chối.
9. Concurrent approval với stale `expected_version` bị từ chối.
10. Query chỉ xếp hạng evidence đã qua ACL.
11. Unauthorized document, chunk, score, relationship, context và citation không bị rò.
12. Không đủ evidence tạo abstention mà không gọi generator.
13. Citation trỏ đúng document version và source URI.
14. Reindex giữ nguyên ACL, không kích hoạt derived data của version cũ và không tạo record active trùng.
15. Error envelope không chứa raw exception, secret, DSN hoặc Bearer token.
16. Health endpoint dùng sanitized error thay vì exception nội bộ.

### 15.4 Deferred scope

Các nội dung sau chưa được định nghĩa trong baseline:

- Token issuance, login UI và account administration.
- Refresh token và token revocation workflow hoàn chỉnh.
- Streaming hoặc WebSocket response.
- Rate limiting và quota policy.
- Pagination cho corpus hoặc audit-history endpoint.
- API chỉnh sửa Markdown body trực tiếp.
- Agent tự động code, deploy hoặc remediation.
- Unbounded graph traversal.
- Public hoặc anonymous access.
- Production-grade multi-tenant isolation.
- Cơ chế worker queue cụ thể.
- Endpoint quản trị embedding model hoặc generator model.

Các nội dung deferred phải được bổ sung bằng contract version mới trước khi implementation phụ thuộc vào chúng.
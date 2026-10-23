# OpsBrain Storage Schema Contract

- **Status:** Accepted
- **Contract version:** `1.1`
- **Amendment:** `2026-10-23` — Cho phép phased audit foundation trước reindex job và review decision, nhưng không làm suy yếu append-only enforcement, organization isolation hoặc foreign-key integrity.

- **Primary owner:** Backend and Data Engineering
- **Reviewer:** Corpus and Evaluation
- **Target milestone:** Milestone 3

## 1. Purpose

Contract này định nghĩa cấu trúc lưu trữ PostgreSQL tối thiểu cho document ingestion, version history, processing state, access control, derived data và audit của OpsBrain.

Mục tiêu là bảo đảm database schema, migration, ingestion service và retrieval service sử dụng cùng identity, relationship, constraint và lifecycle rule.

Contract mô tả thiết kế dự kiến. Sự tồn tại của tài liệu này không chứng minh migration đã chạy hoặc ingestion lifecycle đã được triển khai.

## 2. Scope

Contract baseline bao gồm:

- Organization và authenticated principal.
- Stable document identity.
- Immutable document versions.
- Ingestion jobs và kết quả xử lý từng document.
- Chunks và trạng thái dữ liệu dẫn xuất.
- Document relationships.
- User, team và role access-control subjects.
- Document ACL entries.
- Audit events.
- Constraint, index và transaction boundary tối thiểu.

Contract chưa bao gồm:

- Embedding vector dimension hoặc pgvector index tuning.
- PostgreSQL full-text search configuration.
- Reciprocal Rank Fusion.
- Graph traversal algorithm.
- Ollama model registry.
- Backup infrastructure hoặc production replication.
- Authentication provider hoàn chỉnh.

Các phần trên được bổ sung ở milestone tương ứng nhưng không được phá vỡ identity và lifecycle invariant đã chốt trong contract này.

## 3. Source-of-truth boundaries

- PostgreSQL là nguồn dữ liệu và trạng thái chính thức sau khi ingestion được chấp nhận.
- Markdown body, author-controlled frontmatter và source URI được lưu trong document version để giữ provenance và khả năng tái tạo.
- Bảng `documents` giữ stable identity cùng con trỏ tới current version.
- Bảng `document_versions` giữ lịch sử version bất biến.
- Chunk, embedding, full-text index và relationship edge là derived data có thể rebuild.
- Ingestion job và audit event phải được giữ để truy vết cả thành công lẫn thất bại.
- Xóa, revoke hoặc supersede document không được xóa audit history.
- Frontmatter không phải nguồn authentication hoặc capability.
- Organization và actor identity phải được lấy từ trusted backend context, không lấy từ field do client hoặc Markdown tự khai.

## 4. Baseline invariants

1. `document_id` ổn định và chỉ unique trong phạm vi một organization.
2. Một document chỉ có một current version tại một thời điểm.
3. Document version đã commit là immutable.
4. Cùng document identity và cùng canonical content hash không tạo version mới.
5. Semantic content hoặc metadata change tạo version mới.
6. Document update phải giữ được version history.
7. Delete hoặc revoke làm document không còn retrieval-eligible nhưng không xóa audit history.
8. Derived records phải truy ngược được về document version nguồn.
9. Ingestion failure không được để current version và derived records ở trạng thái nửa vời.
10. Retry cùng idempotency key và cùng payload không tạo job hoặc version trùng.
11. ACL phải gắn với organization và document hiện hành.
12. Audit event là append-only đối với baseline.
13. Mọi datetime được lưu bằng PostgreSQL `timestamptz` và xử lý theo UTC.
14. Internal database key không thay thế stable external `document_id`.

## 5. Key and type conventions

- Internal primary key sử dụng PostgreSQL `uuid`.
- Stable identifier từ contract sử dụng `text` và được lưu riêng với internal primary key.
- Mỗi bảng nghiệp vụ có `created_at timestamptz`.
- Bảng có mutable operational state bổ sung `updated_at timestamptz`.
- Enum baseline được biểu diễn bằng `text` kết hợp `CHECK` constraint để migration dễ thay đổi có kiểm soát.
- JSON chỉ dùng để giữ snapshot metadata có cấu trúc linh hoạt; field cần constraint, join hoặc index phải có column riêng.
- Foreign key phải khai báo rõ `ON DELETE` behavior.
- Source-of-truth và audit record không sử dụng cascade delete.
- Bearer token, credential và secret không được lưu trong các bảng nghiệp vụ dưới dạng plaintext.

Internal UUID dùng cho join và foreign key. Stable identifier như `payment-service` tiếp tục được API và frontmatter gọi là `document_id`.

## 6. Organization and principal identity

### 6.1 `organizations`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal organization key |
| `organization_key` | `text` | Not null, unique | Stable organization identifier |
| `display_name` | `text` | Not null, non-empty | Tên hiển thị |
| `status` | `text` | Not null, check `active`, `disabled` | Trạng thái organization |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |

`organization_key` do backend configuration hoặc trusted identity mapping cung cấp. Client và frontmatter không được tự chọn organization cho request.

### 6.2 `principals`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal principal key |
| `organization_id` | `uuid` | Not null, FK tới `organizations.id`, restrict delete | Organization của principal |
| `principal_key` | `text` | Not null | Stable authenticated identity |
| `principal_kind` | `text` | Not null, check `user`, `service` | Loại actor |
| `display_name` | `text` | Nullable | Tên hiển thị |
| `status` | `text` | Not null, check `active`, `disabled` | Trạng thái principal |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |

Required constraints:

- `UNIQUE (organization_id, principal_key)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key theo organization.

`principals` lưu identity đã được backend resolve. Bảng này không lưu raw Bearer token. Team, role, membership và capability được chuẩn hóa ở phần ACL thay vì nhét vào một JSON principal context.

## 7. Document identity and immutable versions

### 7.1 `documents`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal document key |
| `organization_id` | `uuid` | Not null, FK tới `organizations.id`, restrict delete | Organization sở hữu document |
| `document_id` | `text` | Not null | Stable external ID từ frontmatter |
| `document_type` | `text` | Not null, check sáu document type | Loại document ổn định |
| `current_source_uri` | `text` | Not null, non-empty | Logical source URI hiện tại do connector quan sát |
| `record_state` | `text` | Not null, check `active`, `deleted` | Trạng thái source record |
| `current_version_id` | `uuid` | Nullable trong lúc tạo | Current committed version |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo identity |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật current state |
| `deleted_at` | `timestamptz` | Nullable | Thời điểm source bị delete |
| `deleted_by_principal_id` | `uuid` | Nullable | Actor thực hiện delete |

Required constraints:

- `UNIQUE (organization_id, document_id)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key theo organization.
- Partial unique index trên `(organization_id, current_source_uri)` khi `record_state = active`.
- `document_type` chỉ nhận `service`, `deployment`, `incident`, `postmortem`, `runbook` hoặc `alert`.
- `record_state: deleted` yêu cầu `deleted_at` và `deleted_by_principal_id`.
- `record_state: active` yêu cầu `deleted_at` và `deleted_by_principal_id` là `null`.
- `(organization_id, deleted_by_principal_id)` tham chiếu principal trong cùng organization.
- `current_version_id` phải trỏ tới một version thuộc chính document và organization đó.

Rules:

- `current_source_uri` là vị trí hiện tại của source; `document_versions.source_uri` giữ vị trí được quan sát khi version đó được tạo.
- Rename hoặc move source có cùng stable `document_id` cập nhật `current_source_uri` nhưng không tự tạo document version.
- `document_type` không được đổi cho cùng một stable `document_id`.
- Source delete không hard-delete row trong `documents`.
- `current_version_id` là nguồn duy nhất xác định current version.
- Không thêm cờ `is_current` vào `document_versions` vì sẽ tạo hai nguồn sự thật có thể mâu thuẫn.

### 7.2 `document_versions`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal version key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `document_pk` | `uuid` | Not null | Internal document identity |
| `parent_version_id` | `uuid` | Nullable đối với version đầu tiên | Version trực tiếp trước đó |
| `version_number` | `integer` | Not null, greater than zero | Số version tăng dần |
| `schema_version` | `integer` | Not null, greater than zero | Frontmatter schema version |
| `canonicalization_version` | `integer` | Not null, greater than zero | Phiên bản quy tắc canonicalization |
| `hash_algorithm` | `text` | Not null, baseline `sha256` | Thuật toán content hash |
| `content_hash` | `text` | Not null | Hash của canonical frontmatter và normalized body |
| `title` | `text` | Not null, non-empty | Tiêu đề tại version này |
| `owner_team` | `text` | Not null, non-empty | Stable owner-team identifier |
| `source_uri` | `text` | Not null, non-empty | Source URI backend quan sát |
| `source_refs` | `text[]` | Not null, default empty | Provenance references |
| `tags` | `text[]` | Not null, default empty | Normalized tags |
| `markdown_body` | `text` | Not null | Nội dung Markdown |
| `frontmatter_snapshot` | `jsonb` | Not null, JSON object | Snapshot semantic frontmatter đã được backend chấp nhận |
| `document_status` | `text` | Not null, check `active`, `deprecated`, `revoked` | Lifecycle state được backend chấp nhận |
| `review_state` | `text` | Not null, check `draft`, `in_review`, `approved`, `rejected` | Review state được backend chấp nhận |
| `provenance_type` | `text` | Not null, check `internal`, `external`, `synthetic` | Nguồn gốc |
| `access_scope` | `text` | Not null, check `organization`, `restricted` | ACL scope |
| `effective_from` | `timestamptz` | Nullable | Bắt đầu temporal eligibility |
| `effective_until` | `timestamptz` | Nullable | Kết thúc temporal eligibility |
| `change_kind` | `text` | Not null | Lý do tạo version |
| `created_by_principal_id` | `uuid` | Not null | Actor tạo version |
| `created_at` | `timestamptz` | Not null | Thời điểm commit version |

Required constraints:

- `UNIQUE (organization_id, document_pk, version_number)`.
- `UNIQUE (organization_id, document_pk, id)` để hỗ trợ current-version và parent-version composite foreign key.
- `UNIQUE (organization_id, document_pk, parent_version_id)` khi `parent_version_id` không null, ngăn hai version cùng kế thừa một current version.
- `(organization_id, document_pk)` tham chiếu document trong cùng organization.
- `(organization_id, created_by_principal_id)` tham chiếu principal trong cùng organization.
- `parent_version_id` phải trỏ tới version thuộc cùng document và organization.
- `content_hash` phải là lowercase SHA-256 hex gồm 64 ký tự.
- `effective_until` phải sau `effective_from` khi cả hai tồn tại.
- `change_kind` chỉ nhận `create`, `content_update`, `metadata_update`, `review_transition`, `delete` hoặc `restore`.
- `frontmatter_snapshot` phải là JSON object.
- `tags` không được chứa giá trị trùng.
- Version đầu tiên phải có `version_number = 1`, `change_kind = create` và `parent_version_id = null`.
- Version sau version đầu tiên phải có `parent_version_id`.

Rules:

- Version đã commit là immutable.
- Insert version mới và cập nhật `documents.current_version_id` phải nằm trong cùng transaction.
- `documents.current_version_id` được bảo vệ bằng composite foreign key để không thể trỏ sang version của document hoặc organization khác.
- ACL rows, chunks và relationships phải tham chiếu `document_versions.id`.
- `frontmatter_snapshot` giữ semantic frontmatter đã được backend chấp nhận; field cần constraint, authorization hoặc query thường xuyên vẫn có column riêng.
- Các relational column và normalized ACL rows là nguồn chính thức cho lifecycle, authorization, filtering và query.
- `frontmatter_snapshot` dùng cho provenance và reproducibility; backend không được đọc trực tiếp security decision từ JSON snapshot.
- Version transaction phải tạo snapshot, relational columns và ACL rows từ cùng một validated model.
- Nếu snapshot và normalized representation không nhất quán, transaction phải thất bại.
- Giá trị trong snapshot không tự chứng minh actor nguồn có quyền approve, revoke hoặc thay đổi ACL.
- Unchanged replay so sánh canonical content hash với current version và không tạo version mới.
- Nếu nội dung quay lại hash của historical version nhưng khác current version, baseline tạo version mới để giữ timeline.
- Optimistic concurrency phải kiểm tra current version trước khi tạo version kế tiếp.

## 8. Ingestion jobs, items and retry attempts

### 8.1 `ingestion_jobs`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Job ID được API trả cho client |
| `organization_id` | `uuid` | Not null | Organization scope |
| `request_id` | `text` | Not null, non-empty | Request tracing identifier |
| `idempotency_key_hash` | `text` | Not null | Hash của client idempotency key |
| `request_payload_hash` | `text` | Not null | Hash của canonical ingestion request |
| `submitted_by_principal_id` | `uuid` | Not null | Principal tạo job |
| `status` | `text` | Not null | Trạng thái job |
| `submitted_document_count` | `integer` | Not null, greater than zero | Số document được gửi |
| `job_error_code` | `text` | Nullable | Sanitized system-level error code |
| `job_error_message` | `text` | Nullable | Sanitized system-level message |
| `submitted_at` | `timestamptz` | Not null | Thời điểm nhận request |
| `started_at` | `timestamptz` | Nullable | Thời điểm bắt đầu xử lý |
| `completed_at` | `timestamptz` | Nullable | Thời điểm kết thúc |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật trạng thái |

Required constraints:

- `(organization_id, submitted_by_principal_id)` tham chiếu principal trong cùng organization.
- `UNIQUE (organization_id, submitted_by_principal_id, idempotency_key_hash)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key từ job items, attempts và audit.
- `idempotency_key_hash` và `request_payload_hash` phải là lowercase SHA-256 hex gồm 64 ký tự.
- `status` chỉ nhận `queued`, `running`, `completed`, `completed_with_errors` hoặc `failed`.
- `queued` yêu cầu `started_at` và `completed_at` là `null`.
- `running` yêu cầu `started_at` có giá trị và `completed_at` là `null`.
- Trạng thái terminal yêu cầu cả `started_at` và `completed_at`.
- `completed_at` không được trước `started_at`.
- Job-level error chỉ dùng cho lỗi hệ thống; document validation error được lưu tại item.

Idempotency rules:

- Cùng organization, principal và idempotency key hash phải trả lại cùng job khi request payload hash giống nhau.
- Cùng idempotency key hash nhưng request payload hash khác nhau là conflict.
- Raw idempotency key không được ghi vào log hoặc audit event.
- Tạo job và toàn bộ job item phải nằm trong một transaction.

### 8.2 `ingestion_job_items`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal job-item key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `ingestion_job_id` | `uuid` | Not null | Parent ingestion job |
| `item_index` | `integer` | Not null, zero or greater | Vị trí trong request batch |
| `source_uri` | `text` | Not null, non-empty | Logical source identifier |
| `submitted_markdown` | `text` | Nullable sau retention cleanup | Raw input cần cho asynchronous processing |
| `submitted_content_hash` | `text` | Not null | Hash của raw submitted item |
| `document_id` | `text` | Nullable | Stable ID sau khi parse thành công |
| `document_pk` | `uuid` | Nullable | Document được tạo hoặc cập nhật |
| `document_version_id` | `uuid` | Nullable | Version được tạo hoặc giữ nguyên |
| `processing_status` | `text` | Not null | Trạng thái xử lý item |
| `outcome` | `text` | Nullable cho tới khi terminal | Kết quả nghiệp vụ |
| `warnings` | `jsonb` | Not null, default empty array | Sanitized structured warnings |
| `errors` | `jsonb` | Not null, default empty array | Sanitized structured errors |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |
| `completed_at` | `timestamptz` | Nullable | Thời điểm item terminal |

Required constraints:

- `UNIQUE (organization_id, ingestion_job_id, item_index)`.
- `(organization_id, ingestion_job_id)` tham chiếu ingestion job trong cùng organization.
- Khi có `document_pk`, document phải thuộc cùng organization.
- Khi có `document_version_id`, version phải thuộc `document_pk` và cùng organization.
- `submitted_content_hash` phải là lowercase SHA-256 hex gồm 64 ký tự.
- `processing_status` chỉ nhận `queued`, `running`, `succeeded` hoặc `failed`.
- `outcome` chỉ nhận `created`, `updated`, `unchanged` hoặc `failed`.
- Item terminal phải có `completed_at`.
- `processing_status: succeeded` yêu cầu outcome là `created`, `updated` hoặc `unchanged`.
- `processing_status: failed` yêu cầu `outcome: failed` và ít nhất một sanitized error.
- `warnings` và `errors` phải là JSON array.
- `document_id` có thể là `null` nếu frontmatter không parse được.

Rules:

- `submitted_markdown` không được xuất hiện trong status response, log hoặc audit payload.
- Accepted version giữ Markdown chính thức trong `document_versions`; raw job payload có retention policy riêng.
- `unchanged` không tạo document version hoặc derived record mới.
- Summary của job được tính từ job items, không lưu thêm các counter có thể mâu thuẫn.
- Mỗi document item được commit trong transaction riêng để batch có thể hoàn thành một phần.
- Failure của một item không được để lại document version, chunk, ACL row hoặc relationship nửa vời.

### 8.3 `ingestion_job_attempts`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal attempt key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `ingestion_job_id` | `uuid` | Not null | Job được chạy hoặc retry |
| `attempt_number` | `integer` | Not null, greater than zero | Số lần thử |
| `status` | `text` | Not null, check `running`, `succeeded`, `failed` | Kết quả attempt |
| `retryable` | `boolean` | Not null | Failure có thể retry hay không |
| `error_code` | `text` | Nullable | Sanitized failure code |
| `error_message` | `text` | Nullable | Sanitized failure message |
| `started_at` | `timestamptz` | Not null | Thời điểm attempt bắt đầu |
| `completed_at` | `timestamptz` | Nullable | Thời điểm attempt kết thúc |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |

Required constraints:

- `UNIQUE (organization_id, ingestion_job_id, attempt_number)`.
- `(organization_id, ingestion_job_id)` tham chiếu job trong cùng organization.
- Attempt `running` yêu cầu `completed_at` là `null`.
- Attempt terminal yêu cầu `completed_at`.
- Attempt `failed` yêu cầu sanitized `error_code`.
- Attempt `succeeded` yêu cầu `retryable = false` và không có error.
- Retry tạo attempt mới; không ghi đè lịch sử attempt cũ.

### 8.4 Transaction boundaries

1. API acceptance transaction tạo một ingestion job và toàn bộ item.
2. Worker claim job bằng row lock để tránh hai worker xử lý cùng job đồng thời.
3. Mỗi item chạy trong transaction riêng.
4. Transaction item gồm version change, current-version pointer, ACL snapshot, relationship snapshot, audit event và derived-data state tương ứng.
5. Item failure rollback toàn bộ thay đổi của item đó.
6. Job status chỉ chuyển terminal sau khi mọi item terminal hoặc job gặp system-level failure.
7. Retry tiếp tục từ trạng thái persisted; không tạo lại item đã commit thành công.
8. Derived processing có thể chạy sau source transaction nhưng phải dùng explicit state và có khả năng rebuild.

## 9. Teams, roles, capabilities and document ACL

### 9.1 `teams`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal team key |
| `organization_id` | `uuid` | Not null, FK tới `organizations.id`, restrict delete | Organization scope |
| `team_key` | `text` | Not null, non-empty | Stable team identifier |
| `display_name` | `text` | Not null, non-empty | Tên hiển thị |
| `status` | `text` | Not null, check `active`, `disabled` | Trạng thái team |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |

Required constraints:

- `UNIQUE (organization_id, team_key)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key.
- `document_versions.owner_team` phải resolve tới `teams.team_key` trong cùng organization.
- Team bị disabled không cấp quyền thông qua membership hoặc document ACL.
- `owner_team` không tự động cấp quyền đọc document.

### 9.2 `roles`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal role key |
| `organization_id` | `uuid` | Not null, FK tới `organizations.id`, restrict delete | Organization scope |
| `role_key` | `text` | Not null, non-empty | Stable role identifier |
| `display_name` | `text` | Not null, non-empty | Tên hiển thị |
| `status` | `text` | Not null, check `active`, `disabled` | Trạng thái role |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |

Required constraints:

- `UNIQUE (organization_id, role_key)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key.
- Role bị disabled không cấp capability hoặc document access.

### 9.3 `principal_team_memberships`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal membership key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `principal_id` | `uuid` | Not null | Principal thuộc team |
| `team_id` | `uuid` | Not null | Team được gán |
| `status` | `text` | Not null, check `active`, `revoked` | Trạng thái membership |
| `assigned_by_principal_id` | `uuid` | Not null | Actor tạo membership |
| `revoked_by_principal_id` | `uuid` | Nullable | Actor thu hồi membership |
| `assigned_at` | `timestamptz` | Not null | Thời điểm gán |
| `revoked_at` | `timestamptz` | Nullable | Thời điểm thu hồi |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |

Required constraints:

- `UNIQUE (organization_id, principal_id, team_id)`.
- Principal, team, actor gán và actor thu hồi phải thuộc cùng organization.
- Membership `active` yêu cầu `revoked_at` và `revoked_by_principal_id` là `null`.
- Membership `revoked` yêu cầu `revoked_at` và `revoked_by_principal_id` có giá trị.
- `revoked_at` không được trước `assigned_at`.
- Chỉ membership đang active mới được dùng khi kiểm tra ACL.

### 9.4 `principal_role_memberships`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal membership key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `principal_id` | `uuid` | Not null | Principal được gán role |
| `role_id` | `uuid` | Not null | Role được gán |
| `status` | `text` | Not null, check `active`, `revoked` | Trạng thái membership |
| `assigned_by_principal_id` | `uuid` | Not null | Actor tạo membership |
| `revoked_by_principal_id` | `uuid` | Nullable | Actor thu hồi membership |
| `assigned_at` | `timestamptz` | Not null | Thời điểm gán |
| `revoked_at` | `timestamptz` | Nullable | Thời điểm thu hồi |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |

Required constraints:

- `UNIQUE (organization_id, principal_id, role_id)`.
- Principal, role, actor gán và actor thu hồi phải thuộc cùng organization.
- Membership `active` yêu cầu `revoked_at` và `revoked_by_principal_id` là `null`.
- Membership `revoked` yêu cầu `revoked_at` và `revoked_by_principal_id` có giá trị.
- `revoked_at` không được trước `assigned_at`.
- Chỉ membership đang active mới cấp capability hoặc document access.

### 9.5 `capabilities`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `capability_key` | `text` | Primary key, non-empty | Stable operation capability |
| `description` | `text` | Not null, non-empty | Ý nghĩa capability |
| `created_at` | `timestamptz` | Not null | Thời điểm đăng ký |

Baseline capability catalog gồm:

- `document:ingest`
- `ingestion:read`
- `document:read_state`
- `document:approve`
- `query:execute`
- `index:rebuild`
- `index:read`

Capability catalog do application migration quản lý. Client và frontmatter không được tự tạo capability.

### 9.6 `role_capabilities`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal grant key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `role_id` | `uuid` | Not null | Role nhận capability |
| `capability_key` | `text` | Not null, FK tới `capabilities.capability_key`, restrict delete | Capability được cấp |
| `status` | `text` | Not null, check `active`, `revoked` | Trạng thái grant |
| `granted_by_principal_id` | `uuid` | Not null | Actor cấp quyền |
| `revoked_by_principal_id` | `uuid` | Nullable | Actor thu hồi quyền |
| `granted_at` | `timestamptz` | Not null | Thời điểm cấp |
| `revoked_at` | `timestamptz` | Nullable | Thời điểm thu hồi |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |

Required constraints:

- `UNIQUE (organization_id, role_id, capability_key)`.
- Role, actor cấp và actor thu hồi phải thuộc cùng organization.
- Grant `active` yêu cầu `revoked_at` và `revoked_by_principal_id` là `null`.
- Grant `revoked` yêu cầu `revoked_at` và `revoked_by_principal_id` có giá trị.
- `revoked_at` không được trước `granted_at`.
- Chỉ active role membership kết hợp với active role capability mới cấp capability.

### 9.7 `document_version_acl_entries`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal ACL-entry key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `document_pk` | `uuid` | Not null | Stable document identity |
| `document_version_id` | `uuid` | Not null | Version chứa ACL snapshot |
| `user_principal_id` | `uuid` | Nullable | Direct user allow entry |
| `team_id` | `uuid` | Nullable | Team allow entry |
| `role_id` | `uuid` | Nullable | Role allow entry |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |

Required constraints:

- Version phải thuộc `document_pk` và cùng organization.
- Chính xác một trong `user_principal_id`, `team_id` hoặc `role_id` phải có giá trị.
- `user_principal_id`, team hoặc role được tham chiếu phải thuộc cùng organization.
- `user_principal_id` phải tham chiếu principal có `principal_kind: user`.
- Ba partial unique index phải ngăn ACL entry trùng cho cùng version và cùng user, team hoặc role.
- ACL entry là immutable cùng document version.
- Xóa hoặc thay đổi allowlist tạo document version mới thay vì sửa ACL snapshot cũ.

Cross-table invariants:

- Version có `access_scope: organization` không được có ACL entry.
- Version có `access_scope: restricted` phải có ít nhất một ACL entry.
- Hai invariant trên phải được kiểm tra khi transaction commit bằng deferred constraint trigger và không chỉ dựa vào application validation.
- Stable identifier từ `allowed_users`, `allowed_teams` và `allowed_roles` phải resolve chính xác trong cùng organization; identifier không tồn tại làm ingestion item thất bại.
- Baseline không hỗ trợ wildcard, anonymous subject hoặc fuzzy matching.

### 9.8 Authorization evaluation rules

Một principal chỉ được thực hiện thao tác trên document khi vượt qua tất cả lớp phù hợp:

1. Principal đã được backend xác thực, đang active và thuộc đúng organization.
2. Operation capability được resolve từ active role membership và active role-capability grant.
3. Document version hiện hành thuộc cùng organization.
4. Với `access_scope: organization`, principal đã xác thực trong organization được vượt qua lớp document ACL.
5. Với `access_scope: restricted`, phải có ít nhất một exact match với direct user, active team membership hoặc active role membership.
6. Lifecycle, review state, temporal validity và derived-data readiness được kiểm tra theo operation.
7. Chỉ sau các bước trên mới được ranking, graph expansion, context construction, generation hoặc citation.

Security rules:

- Capability không tự cấp quyền nhìn document.
- Document ACL không tự cấp capability thực hiện operation.
- `owner_team` không tự tạo ACL entry.
- ACL phải lọc document và derived candidate trước ranking.
- Chunk, relationship, score, context và citation không được tiết lộ nếu source document không vượt qua ACL hiện hành.
- Graph traversal không được đi xuyên document trái quyền.
- Nếu toàn bộ evidence bị loại bởi ACL hoặc eligibility rule, query phải abstain mà không xác nhận protected document tồn tại.
- Thu hồi membership, role capability hoặc document access phải có hiệu lực sau cache invalidation mà không phụ thuộc vào việc embedding đã được rebuild.
- Reindex không được mở rộng access và phải giữ liên kết tới source document version.
- Log và audit event chỉ ghi stable identifier cần thiết; không ghi Bearer token hoặc nội dung trái quyền.
- Approval yêu cầu đồng thời current document ACL, capability `document:approve` và reviewer khác principal đã tạo hoặc sửa current version.

## 10. Derived configurations, build sets, chunks and embeddings

### 10.1 `derived_configurations`

Bảng này ghi lại cấu hình dùng để tạo dữ liệu dẫn xuất. Thay đổi chunking rule, embedding model hoặc cấu hình xử lý phải tạo configuration mới thay vì sửa configuration đã được sử dụng.

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal configuration key |
| `organization_id` | `uuid` | Not null, FK tới `organizations.id`, restrict delete | Organization scope |
| `configuration_key` | `text` | Not null, non-empty | Stable configuration identifier |
| `chunking_version` | `text` | Not null, non-empty | Phiên bản thuật toán chunking |
| `embedding_model` | `text` | Nullable | Embedding model identifier thực tế |
| `embedding_model_revision` | `text` | Nullable | Model revision hoặc digest nếu có |
| `embedding_dimensions` | `integer` | Nullable, greater than zero | Số chiều embedding thực tế |
| `full_text_configuration` | `text` | Nullable | PostgreSQL text-search configuration |
| `relationship_extractor_version` | `text` | Nullable | Phiên bản relationship extraction |
| `configuration_payload` | `jsonb` | Not null, JSON object | Cấu hình tái tạo đầy đủ |
| `configuration_hash` | `text` | Not null | Hash của canonical configuration |
| `status` | `text` | Not null, check `active`, `retired` | Trạng thái configuration |
| `created_by_principal_id` | `uuid` | Not null | Actor đăng ký configuration |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |

Required constraints:

- `UNIQUE (organization_id, configuration_key)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key.
- `(organization_id, created_by_principal_id)` tham chiếu principal trong cùng organization.
- `configuration_hash` phải là lowercase SHA-256 hex gồm 64 ký tự.
- `configuration_payload` phải là JSON object.
- Nếu khai báo embedding model thì `embedding_dimensions` phải có giá trị.
- Configuration đã được một derived set sử dụng là immutable.
- Secret, Bearer token, credential và filesystem path riêng của máy không được lưu trong `configuration_payload`.

### 10.2 `derived_sets`

Một derived set là một lần build nhất quán của dữ liệu dẫn xuất cho đúng một document version và một configuration.

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal derived-set key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `document_pk` | `uuid` | Not null | Stable document identity |
| `document_version_id` | `uuid` | Not null | Source version |
| `configuration_id` | `uuid` | Not null | Configuration dùng để build |
| `build_number` | `integer` | Not null, greater than zero | Số lần build cho cùng version và configuration |
| `status` | `text` | Not null | Trạng thái build |
| `is_active` | `boolean` | Not null, default false | Set hiện được retrieval sử dụng |
| `failure_code` | `text` | Nullable | Sanitized failure code |
| `failure_message` | `text` | Nullable | Sanitized failure message |
| `started_at` | `timestamptz` | Nullable | Thời điểm bắt đầu build |
| `completed_at` | `timestamptz` | Nullable | Thời điểm build kết thúc |
| `activated_at` | `timestamptz` | Nullable | Thời điểm set được kích hoạt |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |

Required constraints:

- `UNIQUE (organization_id, document_version_id, configuration_id, build_number)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key từ derived records và reindex items.
- Document version phải thuộc `document_pk` và cùng organization.
- Configuration phải thuộc cùng organization.
- `status` chỉ nhận `pending`, `building`, `ready`, `stale` hoặc `failed`.
- `pending` yêu cầu `started_at`, `completed_at` và `activated_at` là `null`.
- `building` yêu cầu `started_at` có giá trị và `completed_at` là `null`.
- `ready`, `stale` và `failed` yêu cầu `completed_at` có giá trị.
- `failed` yêu cầu sanitized `failure_code`.
- Chỉ set có `status: ready` mới được đặt `is_active: true`.
- Set active yêu cầu `activated_at` có giá trị.
- Partial unique index bảo đảm tối đa một active derived set cho mỗi `(organization_id, document_pk)`.
- Failure message không được chứa raw document content, stack trace, SQL, secret hoặc Bearer token.

Activation rules:

- Derived set chỉ được kích hoạt sau khi toàn bộ component bắt buộc của configuration hoàn thành nhất quán.
- Activation phải khóa document, đọc lại current version và xác minh document vẫn retrieval-eligible.
- `document_version_id` phải vẫn là current version tại thời điểm activation.
- Kích hoạt set mới và vô hiệu hóa set cũ phải nằm trong cùng transaction.
- Build thất bại không được làm mất active set cũ nếu source version và authorization state vẫn cho phép dùng set cũ.
- Khi document version, lifecycle, review state hoặc ACL thay đổi, active set của version cũ phải bị vô hiệu hóa trước khi version mới có thể query.
- Membership hoặc role bị thu hồi phải ảnh hưởng authorization trực tiếp; không được chờ reindex mới chặn quyền.

### 10.3 `chunks`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal chunk key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `derived_set_id` | `uuid` | Not null | Derived set sở hữu chunk |
| `document_pk` | `uuid` | Not null | Stable source document identity |
| `document_version_id` | `uuid` | Not null | Source document version |
| `chunk_key` | `text` | Not null, non-empty | Stable chunk identifier trong derived set |
| `chunk_index` | `integer` | Not null, zero or greater | Thứ tự chunk |
| `chunking_version` | `text` | Not null, non-empty | Phiên bản thuật toán chunking |
| `heading_path` | `text[]` | Not null, default empty | Heading hierarchy của chunk |
| `content` | `text` | Not null, non-empty | Nội dung chunk |
| `content_hash` | `text` | Not null | Hash của normalized chunk content |
| `start_offset` | `integer` | Nullable, zero or greater | Vị trí bắt đầu trong normalized body |
| `end_offset` | `integer` | Nullable, zero or greater | Vị trí kết thúc trong normalized body |
| `token_count` | `integer` | Nullable, zero or greater | Token count theo tokenizer đã ghi nhận |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |

Required constraints:

- `UNIQUE (organization_id, derived_set_id, chunk_key)`.
- `UNIQUE (organization_id, derived_set_id, chunk_index)`.
- `UNIQUE (organization_id, derived_set_id, id)` để bảo đảm embedding tham chiếu chunk thuộc đúng derived set.
- Derived set, document và version phải thuộc cùng organization.
- `document_version_id` phải trùng source version của derived set.
- `document_pk` phải trùng source document của derived set.
- `chunking_version` phải trùng configuration của derived set.
- `content_hash` phải là lowercase SHA-256 hex gồm 64 ký tự.
- Khi cả hai offset tồn tại, `end_offset` phải lớn hơn `start_offset`.
- Hai chunk trong cùng derived set không được dùng cùng `chunk_key`.
- Chunk đã hoàn thành là immutable.
- Retry cùng source version và configuration phải tạo cùng logical chunk boundaries và chunk keys.
- Unchanged replay không tạo derived set hoặc chunk trùng.

ACL rules:

- Chunk không tự sở hữu ACL độc lập.
- Authorization của chunk phải được resolve qua source document version và ACL hiện hành.
- Không được xếp hạng hoặc trả chunk trước khi source document vượt qua organization, ACL, lifecycle, review và temporal filtering.
- Chunk của historical hoặc inactive derived set không được tham gia retrieval thông thường.

### 10.4 `chunk_embeddings`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal embedding key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `derived_set_id` | `uuid` | Not null | Derived set sở hữu embedding |
| `chunk_id` | `uuid` | Not null | Source chunk |
| `embedding_model` | `text` | Not null, non-empty | Model identifier thực tế |
| `embedding_model_revision` | `text` | Nullable | Model revision hoặc digest |
| `embedding_dimensions` | `integer` | Not null, greater than zero | Số chiều vector |
| `embedding_input_hash` | `text` | Not null | Hash của exact normalized model input |
| `embedding` | `vector` | Not null | pgvector embedding |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |

Required constraints:

- `UNIQUE (organization_id, derived_set_id, chunk_id)`.
- Chunk và derived set phải thuộc cùng organization.
- Chunk phải thuộc chính derived set được tham chiếu.
- Model identifier, revision và dimensions phải khớp derived configuration.
- `embedding_input_hash` phải là lowercase SHA-256 hex gồm 64 ký tự.
- Vector dimension phải khớp `embedding_dimensions`.
- Embedding là immutable; đổi model, revision, dimensions hoặc input tạo derived set mới.
- Embedding vector không được trả trực tiếp qua minimal API.
- `(organization_id, derived_set_id, chunk_id)` tham chiếu chunk thuộc đúng derived set.

Pgvector index type, distance operator và dimension cố định cho migration được chốt ở retrieval milestone. Việc chưa chốt tuning không làm thay đổi yêu cầu provenance hoặc rebuildability.

### 10.5 Document index-state mapping

API `index_status` của current document được tính như sau:

| API state | Storage interpretation |
|---|---|
| `not_eligible` | Current version không retrieval-eligible; không cần active derived set |
| `pending` | Current version đủ điều kiện nhưng chưa có active ready set và đang chờ hoặc đang build |
| `ready` | Có đúng một active ready set cho current version |
| `stale` | Active set không còn khớp current version hoặc required configuration |
| `failed` | Build gần nhất thất bại và không có active set hợp lệ cho current version |

`query_ready` chỉ là `true` khi:

- Current version retrieval-eligible.
- Có đúng một active derived set.
- Derived set có `status: ready`.
- Set trỏ đúng current document version.
- Required configuration vẫn hợp lệ.
- Request principal vượt qua capability và ACL tại thời điểm query.

Derived-data readiness không thay thế lifecycle, review, temporal hoặc ACL validation.

## 11. Document relationships and bounded graph safety

### 11.1 Relationship type mapping

Baseline chuyển các frontmatter relationship field thành directed edge như sau:

| Source document type | Frontmatter field | Relationship type | Required target type | Requirement |
|---|---|---|---|---|
| `service` | `dependency_refs` | `depends_on` | `service` | Optional |
| `deployment` | `service_refs` | `deploys` | `service` | Required |
| `incident` | `affected_service_refs` | `affects` | `service` | Required |
| `postmortem` | `incident_refs` | `analyzes` | `incident` | Required |
| `postmortem` | `contributing_service_refs` | `involves` | `service` | Optional |
| `runbook` | `service_refs` | `applies_to` | `service` | Required |
| `alert` | `service_refs` | `monitors` | `service` | Required |

Edge direction luôn đi từ document chứa frontmatter field tới document được tham chiếu.

Baseline chỉ tạo edge từ explicit validated frontmatter. Relationship do model suy diễn, entity extraction hoặc unrestricted Markdown-link expansion được deferred sang contract version sau.

### 11.2 `document_relationships`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal relationship key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `derived_set_id` | `uuid` | Not null | Derived set tạo edge |
| `relationship_key` | `text` | Not null, non-empty | Deterministic edge identifier |
| `source_document_pk` | `uuid` | Not null | Source stable document |
| `source_document_version_id` | `uuid` | Not null | Source version khai báo relationship |
| `target_document_pk` | `uuid` | Not null | Target stable document |
| `resolved_target_version_id` | `uuid` | Not null | Target version được resolve lúc build |
| `source_field` | `text` | Not null, non-empty | Frontmatter field tạo edge |
| `relationship_type` | `text` | Not null | Directed relationship type |
| `reference_index` | `integer` | Not null, zero or greater | Vị trí reference trong source list |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |

Required constraints:

- `UNIQUE (organization_id, derived_set_id, relationship_key)`.
- `UNIQUE (organization_id, derived_set_id, source_field, target_document_pk)`.
- Derived set, source document, source version, target document và resolved target version phải thuộc cùng organization.
- Source document và source version phải trùng source của derived set.
- `resolved_target_version_id` phải thuộc `target_document_pk`.
- Source và target document phải khác nhau.
- Source document type, `source_field`, `relationship_type` và target document type phải khớp mapping tại mục 11.1.
- `relationship_type` chỉ nhận `depends_on`, `deploys`, `affects`, `analyzes`, `involves`, `applies_to` hoặc `monitors`.
- `relationship_key` phải được tạo xác định từ source version, source field, relationship type và stable target document identity.
- Edge đã tạo trong derived set là immutable.
- Không được tạo relationship row với target không tồn tại.

### 11.3 Relationship resolution

Relationship resolution thực hiện sau khi ingestion đã thu thập stable identity của toàn bộ document trong batch.

Resolution order:

1. Resolve target trong các document hợp lệ của cùng ingestion batch.
2. Nếu không có, resolve target từ `documents` trong cùng organization.
3. Xác minh target document type đúng với relationship mapping.
4. Ghi stable target document và target version hiện hành được quan sát.
5. Chỉ tạo edge sau khi tất cả constraint cần thiết vượt qua.

Required behavior:

- Hai document trùng `document_id` trong cùng batch làm các item xung đột bị từ chối trước relationship resolution.
- Broken required reference làm source item thất bại và không tạo source version hoặc derived record active.
- Broken optional reference tạo structured warning tại ingestion item nhưng không tạo dangling edge.
- Wrong target document type được xử lý như invalid relationship.
- Reference trùng trong cùng field bị frontmatter validator từ chối.
- Self-reference bị từ chối.
- Reference không được resolve xuyên organization.
- Khi optional target được tạo sau đó, source document phải được ingest hoặc reindex lại trước khi edge mới xuất hiện.
- Unchanged replay không tạo relationship trùng.

### 11.4 Versioning and lifecycle behavior

- Relationship edge thuộc source document version, không thuộc toàn bộ lịch sử document.
- Source version mới phải tạo relationship snapshot mới trong derived set mới.
- Edge của historical hoặc inactive derived set không tham gia graph expansion thông thường.
- `resolved_target_version_id` giữ provenance của lần resolution; graph retrieval vẫn phải kiểm tra target current version tại thời điểm query.
- Target bị delete, deprecated, revoked, chưa approved hoặc ngoài temporal validity không được graph traversal sử dụng.
- Source document không còn retrieval-eligible làm toàn bộ outgoing edge của active source version không còn sử dụng được.
- Hard delete source hoặc target document không được dùng làm cơ chế lifecycle thông thường.
- Relationship rebuild không được sửa source document, source version, target document hoặc ACL.

### 11.5 Graph authorization rules

Trước khi một edge được dùng hoặc trả về:

1. Authenticated principal phải vượt qua capability yêu cầu của operation.
2. Source current version phải vượt qua organization, ACL, lifecycle, review, temporal và query-readiness filtering.
3. Target current version phải vượt qua cùng các bước kiểm tra.
4. Edge phải thuộc active derived set của source current version.
5. Relationship type phải nằm trong allowlist của retrieval configuration.
6. Traversal phải còn trong giới hạn depth và document count.

Security requirements:

- ACL phải được kiểm tra tại từng node và từng edge trước graph expansion.
- Không được đi xuyên unauthorized intermediate document để tới một document được phép.
- Reverse traversal vẫn phải kiểm tra ACL của cả source và target.
- Relationship path trả về API chỉ được chứa authorized current document identifiers và allowed relationship types.
- Không được trả edge count, hidden target identifier hoặc failure detail có thể xác nhận restricted document tồn tại.
- Khi target không được phép truy cập, edge phải được xử lý như không tồn tại đối với principal đó.
- Cache graph phải được phân vùng hoặc kiểm tra lại theo organization, principal context, current version và ACL state.
- Thu hồi ACL phải làm edge không còn sử dụng được mà không cần chờ embedding rebuild.
- Nếu graph expansion không còn evidence hợp lệ, hệ thống phải abstain.

### 11.6 Required relationship indexes

Migration phải cung cấp tối thiểu:

- Index theo `(organization_id, source_document_pk)`.
- Index theo `(organization_id, target_document_pk)`.
- Index theo `(organization_id, relationship_type, source_document_pk)`.
- Index theo `(organization_id, derived_set_id)`.
- Partial hoặc composite index phục vụ việc lấy edge từ active derived set.

Các index phục vụ hiệu năng không được trở thành cơ chế authorization duy nhất. Query vẫn phải join hoặc xác minh source document, target document, current version và ACL hiện hành.

## 12. Reindex jobs, items and retry attempts

### 12.1 `reindex_jobs`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Job ID được API trả cho client |
| `organization_id` | `uuid` | Not null | Organization scope |
| `request_id` | `text` | Not null, non-empty | Request tracing identifier |
| `idempotency_key_hash` | `text` | Not null | Hash của client idempotency key |
| `request_payload_hash` | `text` | Not null | Hash của canonical reindex request |
| `submitted_by_principal_id` | `uuid` | Not null | Principal tạo job |
| `scope_type` | `text` | Not null, check `documents`, `organization` | Phạm vi request |
| `targets` | `text[]` | Not null, non-empty | Thành phần cần rebuild |
| `reason` | `text` | Not null, non-empty | Lý do rebuild |
| `configuration_id` | `uuid` | Not null | Server-selected derived configuration |
| `requested_document_count` | `integer` | Not null, zero or greater | Số document được snapshot vào job |
| `status` | `text` | Not null | Trạng thái job |
| `job_error_code` | `text` | Nullable | Sanitized system-level error code |
| `job_error_message` | `text` | Nullable | Sanitized system-level error message |
| `submitted_at` | `timestamptz` | Not null | Thời điểm nhận request |
| `started_at` | `timestamptz` | Nullable | Thời điểm bắt đầu |
| `completed_at` | `timestamptz` | Nullable | Thời điểm kết thúc |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |

Required constraints:

- `(organization_id, submitted_by_principal_id)` tham chiếu principal trong cùng organization.
- `(organization_id, configuration_id)` tham chiếu derived configuration trong cùng organization.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key từ reindex items và audit.
- `UNIQUE (organization_id, submitted_by_principal_id, idempotency_key_hash)`.
- `idempotency_key_hash` và `request_payload_hash` phải là lowercase SHA-256 hex gồm 64 ký tự.
- `targets` chỉ chứa `chunks`, `embeddings`, `full_text` hoặc `relationships`.
- `targets` không được chứa giá trị trùng.
- `status` chỉ nhận `queued`, `running`, `completed`, `completed_with_errors` hoặc `failed`.
- `queued` yêu cầu `started_at` và `completed_at` là `null`.
- `running` yêu cầu `started_at` có giá trị và `completed_at` là `null`.
- Trạng thái terminal yêu cầu `started_at` và `completed_at`.
- `completed_at` không được trước `started_at`.
- Job-level error chỉ dùng cho lỗi hệ thống; lỗi từng document được lưu tại job item.
- Raw idempotency key không được lưu trong database, log hoặc audit payload.

Idempotency rules:

- Cùng organization, principal và idempotency-key hash phải trả cùng job khi payload hash giống nhau.
- Cùng idempotency-key hash nhưng payload hash khác nhau là conflict.
- Với scope `documents`, request payload hash bao gồm scope, danh sách `document_id` đã sort và canonicalize, targets đã canonicalize cùng reason.
- Với scope `organization`, request payload hash bao gồm scope type, targets đã canonicalize cùng reason; không bao gồm tập document được resolve sau đó.
- `configuration_id` được snapshot riêng khi job đầu tiên được tạo.
- Replay cùng idempotency key và client payload phải trả lại job cũ ngay cả khi default configuration của hệ thống đã thay đổi.
- Request idempotency không thay thế derived-set uniqueness.

### 12.2 Scope snapshot and authorization

API acceptance transaction phải:

1. Resolve authenticated principal và capability `index:rebuild`.
2. Resolve organization từ trusted backend context.
3. Validate scope, targets và reason.
4. Resolve server-selected derived configuration.
5. Resolve toàn bộ authorized document identity thuộc request scope.
6. Snapshot current source version của từng document thành reindex job item.
7. Tạo job và toàn bộ item trong cùng transaction.

Rules:

- Scope `documents` yêu cầu danh sách `document_id` không rỗng.
- Scope `organization` không nhận danh sách `document_id` từ client.
- Document không tồn tại và document principal không được phép biết phải có cùng hành vi `404`.
- Organization scope chỉ snapshot document thuộc organization và phạm vi quản trị được backend cho phép.
- `requested_document_count` phải bằng số job item được tạo.
- Job không được đọc arbitrary filesystem path do client cung cấp.
- Reindex chỉ đọc source document version đã commit trong PostgreSQL.
- Việc tạo reindex job không thay đổi source content, version, lifecycle, review state hoặc ACL.
- HTTP `202` chỉ chứng minh job đã được nhận, không chứng minh derived data đã được rebuild.

### 12.3 `reindex_job_items`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal job-item key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `reindex_job_id` | `uuid` | Not null | Parent reindex job |
| `document_pk` | `uuid` | Not null | Document được snapshot |
| `source_document_version_id` | `uuid` | Not null | Current version lúc job được nhận |
| `output_derived_set_id` | `uuid` | Nullable | Derived set được build |
| `processing_status` | `text` | Not null | Trạng thái xử lý |
| `outcome` | `text` | Nullable cho tới khi terminal | Kết quả document |
| `skip_code` | `text` | Nullable | Stable reason khi skip |
| `error_code` | `text` | Nullable | Sanitized failure code |
| `error_message` | `text` | Nullable | Sanitized failure message |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo |
| `updated_at` | `timestamptz` | Not null | Thời điểm cập nhật |
| `completed_at` | `timestamptz` | Nullable | Thời điểm item terminal |

Required constraints:

- `UNIQUE (organization_id, reindex_job_id, document_pk)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key từ item attempts.
- Job, document và source version phải thuộc cùng organization.
- Source version phải thuộc document được tham chiếu.
- Output derived set, nếu có, phải thuộc đúng source document version và organization.
- `processing_status` chỉ nhận `queued`, `running`, `succeeded` hoặc `failed`.
- `outcome` chỉ nhận `rebuilt`, `skipped` hoặc `failed`.
- `processing_status: succeeded` yêu cầu outcome là `rebuilt` hoặc `skipped`.
- `processing_status: failed` yêu cầu `outcome: failed` và `error_code`.
- Outcome `rebuilt` yêu cầu `output_derived_set_id`.
- Outcome `skipped` yêu cầu `skip_code` và không được kích hoạt derived set mới.
- Item terminal yêu cầu `completed_at`.
- Error message không được chứa raw document content, embedding vector, stack trace, internal path, SQL, secret hoặc Bearer token.

Baseline skip codes gồm:

- `document_not_eligible`
- `source_version_changed`
- `configuration_not_applicable`
- `target_not_requested`
- `already_current`

### 12.4 `reindex_job_item_attempts`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Internal attempt key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `reindex_job_item_id` | `uuid` | Not null | Item được xử lý hoặc retry |
| `attempt_number` | `integer` | Not null, greater than zero | Số lần thử |
| `status` | `text` | Not null, check `running`, `succeeded`, `failed` | Trạng thái attempt |
| `retryable` | `boolean` | Not null | Failure có thể retry hay không |
| `error_code` | `text` | Nullable | Sanitized failure code |
| `error_message` | `text` | Nullable | Sanitized failure message |
| `started_at` | `timestamptz` | Not null | Thời điểm bắt đầu |
| `completed_at` | `timestamptz` | Nullable | Thời điểm kết thúc |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |

Required constraints:

- `UNIQUE (organization_id, reindex_job_item_id, attempt_number)`.
- Job item phải thuộc cùng organization.
- Attempt `running` yêu cầu `completed_at` là `null`.
- Attempt terminal yêu cầu `completed_at`.
- Attempt `failed` yêu cầu sanitized `error_code`.
- Attempt `succeeded` yêu cầu `retryable = false` và không có error.
- Retry tạo attempt mới; không ghi đè attempt cũ.
- Retry không được tạo active derived set trùng.

### 12.5 Processing and activation rules

1. Worker claim item bằng row lock để tránh hai worker xử lý cùng item đồng thời.
2. Worker đọc đúng `source_document_version_id` đã snapshot.
3. Worker tạo derived set mới ở trạng thái `pending`, sau đó chuyển sang `building`.
4. Chunks, embeddings, full-text state và relationships được tạo trong derived set mới.
5. Worker không sửa active derived set cũ trong lúc build.
6. Sau khi build hoàn thành, worker khóa document và đọc lại current version, lifecycle, review state, temporal validity và ACL.
7. Nếu current version đã đổi, item được `skipped` với code `source_version_changed`; set mới không được kích hoạt.
8. Nếu document không còn retrieval-eligible, item được `skipped` với code `document_not_eligible`; active data không hợp lệ phải bị vô hiệu hóa.
9. Nếu validation cuối cùng đạt, worker kích hoạt set mới và vô hiệu hóa set cũ trong cùng transaction.
10. Chỉ sau activation thành công item mới có outcome `rebuilt`.
11. Item failure không được làm derived set chưa hoàn tất trở thành active.
12. Job terminal chỉ sau khi mọi item terminal hoặc job gặp system-level failure.

### 12.6 Summary and status derivation

Job summary được tính từ persisted job items:

- `requested`: tổng số item.
- `rebuilt`: item có outcome `rebuilt`.
- `skipped`: item có outcome `skipped`.
- `failed`: item có outcome `failed`.

Job status được xác định như sau:

| Job status | Derivation |
|---|---|
| `queued` | Chưa có item bắt đầu |
| `running` | Ít nhất một item đang chạy và chưa terminal toàn bộ |
| `completed` | Mọi item terminal và không có failure |
| `completed_with_errors` | Có cả item thành công hoặc skipped và item failed |
| `failed` | System-level failure hoặc mọi item đều failed |

Stored counter không được dùng làm nguồn sự thật nếu có thể mâu thuẫn với job items.

### 12.7 Safety and disclosure rules

- Reindex giữ nguyên source document, version history và ACL snapshot.
- Derived record phải truy ngược được tới đúng source version, derived configuration và reindex job item.
- ACL phải được kiểm tra lại khi activation và mỗi lần retrieval.
- Thu hồi ACL trong lúc build phải thắng kết quả reindex.
- Reindex không được đưa unauthorized document, chunk hoặc relationship vào candidate, graph, context hoặc citation.
- Response chỉ trả stable document ID, safe status, safe error code và safe message được principal phép biết.
- Job summary không được làm lộ số document ngoài organization hoặc ngoài authorized reindex scope.
- Job không tồn tại và job principal không được phép biết phải có cùng hành vi `404`.
- Principal tạo job được đọc job của mình; principal khác cần capability `index:read` trong cùng organization.
- Reindex thất bại không được để trạng thái nửa cũ, nửa mới cùng active.

## 13. Review decisions and append-only audit

### 13.1 `document_review_decisions`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Review decision ID trả qua API |
| `organization_id` | `uuid` | Not null | Organization scope |
| `request_id` | `text` | Not null, non-empty | Request tracing identifier |
| `idempotency_key_hash` | `text` | Not null | Hash của client idempotency key |
| `request_payload_hash` | `text` | Not null | Hash của canonical decision request |
| `document_pk` | `uuid` | Not null | Document được review |
| `reviewed_version_id` | `uuid` | Not null | Current version reviewer đã kiểm tra |
| `resulting_version_id` | `uuid` | Not null | Version mới chứa review state kết quả |
| `decision` | `text` | Not null, check `approve`, `reject` | Quyết định |
| `reason` | `text` | Not null, non-empty | Lý do quyết định |
| `reviewer_principal_id` | `uuid` | Not null | Authenticated reviewer |
| `decided_at` | `timestamptz` | Not null | Thời điểm quyết định |
| `created_at` | `timestamptz` | Not null | Thời điểm tạo row |

Required constraints:

- `UNIQUE (organization_id, reviewer_principal_id, idempotency_key_hash)`.
- `UNIQUE (organization_id, resulting_version_id)`.
- `UNIQUE (organization_id, id)` để hỗ trợ composite foreign key từ audit events.
- Document, reviewed version, resulting version và reviewer phải thuộc cùng organization.
- Reviewed version và resulting version phải thuộc cùng document.
- Reviewed version phải là parent trực tiếp của resulting version.
- Reviewed version phải có `review_state: in_review`.
- Decision `approve` yêu cầu resulting version có `review_state: approved`.
- Decision `reject` yêu cầu resulting version có `review_state: rejected`.
- Resulting version phải có `change_kind: review_transition`.
- Reviewer phải khác `created_by_principal_id` của reviewed version.
- `idempotency_key_hash` và `request_payload_hash` phải là lowercase SHA-256 hex gồm 64 ký tự.
- Raw idempotency key không được lưu trong database, log hoặc audit payload.

### 13.2 Review transaction rules

Review transaction phải thực hiện theo thứ tự:

1. Xác thực principal từ trusted backend context.
2. Kiểm tra current document ACL.
3. Kiểm tra capability `document:approve`.
4. Khóa document row.
5. Xác minh `expected_version` vẫn là current version.
6. Xác minh current review state là `in_review`.
7. Xác minh independent-review rule.
8. Tạo resulting document version.
9. Tạo ACL snapshot cho resulting version.
10. Tạo review decision row.
11. Cập nhật `documents.current_version_id`.
12. Cập nhật hoặc vô hiệu hóa derived-data state phù hợp.
13. Ghi audit event.
14. Commit toàn bộ thay đổi cùng transaction.

Version behavior:

- Resulting version giữ nguyên Markdown body và semantic frontmatter ngoại trừ `review_state`.
- Backend phải canonicalize lại frontmatter và tính lại `content_hash`.
- Approval tạo trạng thái derived data `pending` khi document đủ điều kiện retrieval.
- Approval không tự làm `query_ready` thành `true`.
- Rejection làm document không retrieval-eligible và vô hiệu hóa active derived set không còn hợp lệ.
- Concurrent decision từ cùng reviewed version chỉ một transaction được commit.
- Cùng idempotency key và payload trả lại cùng decision.
- Cùng idempotency key nhưng payload khác là conflict.
- Stale `expected_version` không được tạo decision, version hoặc audit success event.

### 13.3 `audit_events`

| Column | Type | Constraint | Meaning |
|---|---|---|---|
| `id` | `uuid` | Primary key | Stable audit-event key |
| `organization_id` | `uuid` | Not null | Organization scope |
| `event_type` | `text` | Not null, non-empty | Stable event classification |
| `outcome` | `text` | Not null, check `succeeded`, `failed`, `denied` | Kết quả event |
| `actor_kind` | `text` | Not null, check `principal`, `system` | Loại actor |
| `actor_principal_id` | `uuid` | Nullable | Authenticated actor khi có |
| `request_id` | `text` | Nullable | Request tracing identifier |
| `correlation_id` | `text` | Nullable | Identifier nối các event cùng workflow |
| `document_pk` | `uuid` | Nullable | Document liên quan |
| `document_version_id` | `uuid` | Nullable | Version liên quan |
| `ingestion_job_id` | `uuid` | Nullable | Ingestion job liên quan |
| `reindex_job_id` | `uuid` | Nullable | Reindex job liên quan |
| `review_decision_id` | `uuid` | Nullable | Review decision liên quan |
| `reason_code` | `text` | Nullable | Stable machine-readable reason |
| `details` | `jsonb` | Not null, JSON object | Sanitized structured metadata |
| `occurred_at` | `timestamptz` | Not null | Thời điểm nghiệp vụ xảy ra |
| `created_at` | `timestamptz` | Not null | Thời điểm event được ghi |

Required constraints:

- Mọi referenced resource phải thuộc cùng organization.
- `actor_kind: principal` yêu cầu `actor_principal_id`.
- `actor_kind: system` yêu cầu `actor_principal_id` là `null`.
- `details` phải là JSON object.
- Ít nhất một trong `request_id`, `correlation_id` hoặc resource reference phải tồn tại.
- Audit event không có `updated_at`.
- Audit event đã commit không được update hoặc delete.
- Correction phải tạo event mới và tham chiếu event cũ trong sanitized details.
- Source-of-truth hoặc audit foreign key sử dụng restrict delete, không cascade delete.

### 13.4 Baseline audit event types

Baseline phải hỗ trợ tối thiểu:

- `ingestion.job.accepted`
- `ingestion.item.created`
- `ingestion.item.updated`
- `ingestion.item.unchanged`
- `ingestion.item.failed`
- `document.review.approved`
- `document.review.rejected`
- `document.review.denied`
- `document.deleted`
- `document.restored`
- `document.source_moved`
- `membership.assigned`
- `membership.revoked`
- `role_capability.granted`
- `role_capability.revoked`
- `reindex.job.accepted`
- `reindex.item.activated`
- `reindex.item.skipped`
- `reindex.item.failed`
- `query.executed`
- `query.abstained`
- `query.denied`

Thêm event type mới không được thay đổi ý nghĩa của event type đã tồn tại.

### 13.5 Audit content and disclosure rules

Audit event có thể ghi:

- Stable organization, principal, document, version và job identifier.
- Operation, outcome và stable reason code.
- Configuration identifier hoặc hash.
- Số lượng item hoặc evidence đã được authorization cho phép.
- Previous và resulting lifecycle hoặc review state.
- Timestamp và correlation information.

Audit event không được ghi:

- Bearer token, password, credential, DSN hoặc secret.
- Raw Markdown body hoặc unrestricted raw log.
- Embedding vector.
- Full generated answer hoặc model context chứa nội dung restricted.
- ACL member list không cần thiết cho event.
- Raw exception, stack trace, SQL statement hoặc private filesystem path.
- Idempotency key nguyên bản.

Additional rules:

- Audit table chỉ được đọc qua privileged backend path; không được trả trực tiếp qua minimal document hoặc query API.
- Denied request phải được audit bằng safe reason code nhưng client response không được xác nhận protected resource tồn tại.
- Query audit không được ghi unauthorized candidate count hoặc hidden document identifier vào client-visible output.
- Retrieval audit phải ghi configuration identifier đủ để tái hiện mà không sao chép restricted evidence vào event.
- Audit failure không được âm thầm biến một security-sensitive operation thành success.
- Nếu transaction nghiệp vụ yêu cầu audit event, business change và audit event phải commit hoặc rollback cùng nhau.

### 13.6 Append-only enforcement

Migration phải bảo vệ audit history bằng:

- Database role của application không có quyền `UPDATE` hoặc `DELETE` trên `audit_events`.
- Trigger từ chối update hoặc delete ngoài controlled administrative migration.
- Foreign key dùng `ON DELETE RESTRICT`.
- Index theo `(organization_id, occurred_at)`.
- Index theo `(organization_id, actor_principal_id, occurred_at)`.
- Index theo `(organization_id, document_pk, occurred_at)`.
- Index theo `request_id` và `correlation_id` khi có giá trị.

Backup retention, archival duration và external immutable storage được deferred khỏi baseline, nhưng việc deferred này không cho phép application sửa audit event đã commit.

### 13.6.1 Phased audit foundation

Audit foundation có thể được tạo trước `reindex_jobs` và `document_review_decisions` để hỗ trợ các ingestion event đã có storage, với các điều kiện bắt buộc sau:

- Revision foundation chỉ tạo composite foreign key tới resource đã tồn tại: organization, principal, document, document version và ingestion job.
- `reindex_job_id` và `review_decision_id` vẫn tồn tại trong schema nhưng mọi row phải bị check constraint buộc hai cột này là `null` cho tới khi bảng referenced tương ứng đã tồn tại.
- Migration tạo reindex job hoặc review decision phải bỏ check constraint tạm, thêm composite foreign key cùng organization, rồi có runtime test cho cả reference hợp lệ và cross-organization reference bị từ chối.
- Phase này không được ghi audit event giả cho workflow chưa triển khai, cũng không biến audit foundation thành bằng chứng rằng reindex, review decision, ACL hoặc retrieval đã tồn tại.
- Yêu cầu tại 13.6 vẫn giữ nguyên: database role dùng bởi application không được có quyền `UPDATE` hoặc `DELETE` trên `audit_events`; trigger từ chối update/delete là lớp bảo vệ bổ sung, không thay thế database privilege.
- Exception này chỉ thay đổi dependency order cho reference chưa có bảng; mọi foreign key đã có thể tạo phải tiếp tục dùng `ON DELETE RESTRICT` và bảo toàn organization isolation.

## 14. Document lifecycle and computed eligibility

### 14.1 Computed state

`retrieval_eligible` và `query_ready` là trạng thái được backend tính, không lưu thành boolean độc lập trong `documents`.

Document chỉ `retrieval_eligible` khi đồng thời:

- Organization đang active.
- `documents.record_state` là `active`.
- Current version có `document_status: active`.
- Current version có `review_state: approved`.
- Current time nằm trong temporal validity.
- ACL snapshot của current version hợp lệ.
- Request principal vượt qua document ACL.

Document chỉ `query_ready` khi:

- `retrieval_eligible` là `true`.
- Có đúng một active derived set.
- Active set có `status: ready`.
- Active set trỏ đúng current document version.
- Request principal có capability cần thiết cho operation.

Không lưu `retrieval_eligible` hoặc `query_ready` dưới dạng nguồn sự thật vì chúng phụ thuộc thời gian, ACL, membership, organization state và derived-data state có thể thay đổi.

### 14.2 Lifecycle event matrix

| Event | Version behavior | Official resulting state | Derived-data behavior | Required audit |
|---|---|---|---|---|
| Create | Tạo version `1` với `change_kind: create` | Record active; review state tối đa là `in_review` qua ingestion | Chưa có active set | `ingestion.item.created` |
| Unchanged replay | Không tạo version mới | Giữ nguyên current state | Không tạo derived record mới | `ingestion.item.unchanged` |
| Body update | Tạo version mới với `change_kind: content_update` | Current version mới chuyển về `in_review` | Vô hiệu hóa active set của version cũ | `ingestion.item.updated` |
| Semantic metadata update | Tạo version mới với `change_kind: metadata_update` | Current version mới chuyển về `in_review` | Vô hiệu hóa active set cũ | `ingestion.item.updated` |
| ACL update | Tạo version mới với `change_kind: metadata_update` | Current version mới là `in_review` với ACL snapshot mới | ACL cũ mất hiệu lực trước retrieval tiếp theo; active set cũ bị vô hiệu hóa | `ingestion.item.updated` |
| Approve | Tạo version mới với `change_kind: review_transition` | Review state thành `approved` | Index state `pending` cho tới khi derived set mới ready | `document.review.approved` |
| Reject | Tạo version mới với `change_kind: review_transition` | Review state thành `rejected` | Không còn active derived set hợp lệ | `document.review.rejected` |
| Deprecate | Tạo version mới với `change_kind: metadata_update` | Document status thành `deprecated` | Vô hiệu hóa active set | Lifecycle audit event |
| Revoke | Tạo version mới với `change_kind: metadata_update` | Document status thành `revoked` | Vô hiệu hóa active set và cache liên quan | Lifecycle audit event |
| Source rename or move | Không tạo version nếu canonical content hash không đổi | Cập nhật `documents.current_source_uri` | Giữ nguyên derived data | `document.source_moved` |
| Source delete | Tạo tombstone version với `change_kind: delete` | Record state thành `deleted`; document status thành `revoked` | Vô hiệu hóa active set | `document.deleted` |
| Restore | Tạo version mới với `change_kind: restore` | Record active; review state thành `in_review` | Không kích hoạt derived data trước approval | `document.restored` |
| Processing failure | Không commit version hoặc current-pointer change | Giữ state trước transaction | Không để lại active derived record mới | `ingestion.item.failed` |

Lifecycle audit event cho `deprecated` và `revoked` phải dùng stable event type được chốt trước migration triển khai các transition đó.

### 14.3 Create and update rules

- Ingestion không được chấp nhận việc source tự chuyển document sang `approved`.
- Giá trị `review_state: approved` trong submitted Markdown không thay thế review endpoint hoặc authenticated reviewer.
- Document mới qua ingestion chỉ được commit ở `draft` hoặc `in_review`.
- Semantic body, metadata hoặc ACL update của document đã approved phải tạo version mới ở `in_review`.
- Update giữ nguyên stable `document_id` và `document_type`.
- Rename hoặc move source URI không tạo document hoặc document version mới khi stable `document_id` và canonical content hash không đổi.
- Backend cập nhật `documents.current_source_uri` và ghi audit event `document.source_moved`.
- `document_versions.source_uri` của historical version không bị sửa.
- Mỗi version mới phải trỏ `parent_version_id` tới current version đã bị thay thế.
- Current pointer chỉ đổi sau khi version, ACL snapshot và audit event tương ứng đã sẵn sàng commit.
- Nếu update thất bại, current version và active derived set trước đó phải giữ nguyên.

### 14.4 Delete rules

Source delete là lifecycle event, không phải hard delete.

Trong cùng transaction, backend phải:

1. Khóa document hiện hành.
2. Xác minh actor hoặc trusted connector được phép ghi nhận deletion.
3. Tạo tombstone version kế tiếp.
4. Copy body và frontmatter snapshot cuối cùng để giữ provenance.
5. Đặt lifecycle state chính thức của tombstone thành `revoked`.
6. Đặt `documents.record_state` thành `deleted`.
7. Ghi `deleted_at` và `deleted_by_principal_id`.
8. Cập nhật current-version pointer tới tombstone.
9. Vô hiệu hóa active derived set và cache liên quan.
10. Ghi audit event `document.deleted`.
11. Commit toàn bộ thay đổi cùng transaction.

Rules:

- Tombstone không tham gia retrieval, graph expansion, context hoặc citation.
- Delete không xóa version history, ingestion history, review decision hoặc audit event.
- Delete lặp lại trên document đã deleted phải idempotent hoặc trả stable conflict; không tạo tombstone trùng.
- Derived records không được trở thành orphan.
- Physical cleanup của inactive derived data chỉ được thực hiện theo retention policy riêng.

### 14.5 Restore rules

Restore yêu cầu source Markdown hợp lệ hoặc một controlled restore input có provenance rõ ràng.

Trong cùng transaction, backend phải:

1. Khóa deleted document identity.
2. Xác minh stable `document_id` và `document_type` không đổi.
3. Validate frontmatter, provenance, relationships và ACL.
4. Tạo version mới với `change_kind: restore`.
5. Đặt `documents.record_state` thành `active`.
6. Xóa `deleted_at` và `deleted_by_principal_id`.
7. Đặt resulting review state thành `in_review`.
8. Không kích hoạt historical derived set.
9. Ghi audit event `document.restored`.
10. Commit toàn bộ thay đổi cùng transaction.

Restore không tự phục hồi approval cũ. Independent reviewer phải approve resulting version trước khi derived data được kích hoạt cho retrieval.

### 14.6 Revoke, deprecate and temporal expiry

- `deprecated` và `revoked` luôn làm document không retrieval-eligible.
- Revoke hoặc deprecate phải tạo semantic version mới và audit event.
- Actor phải được backend authorization policy cho phép; frontmatter không tự cấp quyền thực hiện transition.
- Temporal expiry không tạo document version mới vì source content không thay đổi.
- Khi `effective_until` đến hạn, eligibility thay đổi theo thời gian và active set không được query dù physical derived rows vẫn còn.
- Derived rows có thể được giữ để audit hoặc rebuild nhưng phải bị loại trước ranking.
- Restore từ revoked hoặc deprecated content phải đi qua version mới và review lại.

### 14.7 Transaction and concurrency rules

- Mỗi lifecycle transition phải khóa document row hoặc dùng equivalent optimistic concurrency.
- Transition phải xác minh current version chưa đổi kể từ lúc caller đọc.
- Version insert, ACL snapshot, current pointer, record state, derived-set invalidation và audit event phải commit hoặc rollback cùng nhau khi cùng thuộc transition.
- Hai concurrent transitions từ cùng current version không được cùng thành công.
- Failure sau khi derived build nhưng trước activation không được thay đổi active set.
- Retry phải quan sát persisted state và không tạo version, tombstone, ACL snapshot hoặc audit success event trùng.

## 15. Migration, constraints and database enforcement

### 15.1 Migration tooling

Schema implementation phải sử dụng versioned database migration.

Baseline implementation chọn Alembic cùng SQLAlchemy. `Base.metadata.create_all()` không phải cơ chế triển khai schema chính thức.

Required rules:

- Migration file phải được lưu trong repository.
- Mỗi revision có stable revision identifier và mô tả phạm vi.
- Migration phải có deterministic upgrade path từ database rỗng tới head.
- Destructive hoặc data-losing downgrade phải được ghi rõ và không được chạy ngầm.
- Application startup không tự sửa production schema.
- Migration không được chứa credential, DSN thật hoặc machine-local path.
- PostgreSQL extension `vector` phải được kiểm tra hoặc tạo idempotently trước bảng sử dụng kiểu `vector`.
- Capability catalog baseline phải được seed bằng migration có thể chạy lại an toàn.
- Schema object phải dùng naming convention nhất quán cho primary key, foreign key, unique constraint, check constraint và index.

### 15.2 Baseline creation order

Migration có thể được chia thành nhiều revision, nhưng dependency order phải tương đương với danh sách dưới đây, ngoại trừ phased audit foundation được mô tả tại 13.6.1. Ngoại lệ này không cho phép tạo reference tới bảng chưa tồn tại hoặc bỏ qua constraint bắt buộc của resource đã có.

1. Kiểm tra hoặc tạo required PostgreSQL extensions.
2. Tạo `organizations`.
3. Tạo `principals`, `teams`, `roles` và `capabilities`.
4. Tạo team membership, role membership và role-capability tables.
5. Tạo `documents` với `current_version_id` tạm thời chưa có cyclic foreign key.
6. Tạo `document_versions`.
7. Thêm composite current-version foreign key từ `documents` tới `document_versions`.
8. Tạo ingestion job, item và attempt tables.
9. Tạo document-version ACL entries.
10. Tạo derived configurations, derived sets, chunks và embeddings.
11. Tạo document relationships.
12. Tạo reindex job, item và attempt tables.
13. Tạo document review decisions.
14. Tạo audit events.
15. Tạo deferred constraint triggers, append-only triggers, partial unique indexes và remaining performance indexes.
16. Seed baseline capabilities.
17. Chạy schema verification queries.

Cyclic foreign key giữa `documents.current_version_id` và `document_versions.document_pk` phải được tạo theo nhiều bước; migration không được tắt referential integrity để né dependency này.

### 15.3 Enforcement layers

| Rule category | Primary enforcement |
|---|---|
| Required value, enum, positive number, time ordering | `NOT NULL` và `CHECK` constraint |
| Same-organization reference | Composite foreign key |
| Stable identity và idempotency | Unique constraint hoặc unique index |
| Một active derived set cho mỗi document | Partial unique index |
| Restricted version phải có ACL entry | Deferred constraint trigger |
| Organization-scoped version không có ACL entry | Deferred constraint trigger |
| Current version phải thuộc đúng document | Composite foreign key |
| Audit append-only | Database privileges và rejection trigger |
| Review-state transition | Service transaction, row lock và database consistency checks |
| Lifecycle transition | Service transaction, row lock và database consistency checks |
| Authorization trước retrieval | Backend query construction và integration tests |
| Graph traversal bounds | Retrieval service và integration tests |
| Snapshot và normalized representation nhất quán | Shared validated write model, transaction boundary và integration tests; security query chỉ dùng normalized columns |

Application validation giúp trả error dễ hiểu nhưng không thay thế database constraint cho invariant có thể biểu diễn an toàn trong PostgreSQL.

Database trigger chỉ dùng cho cross-row hoặc deferred invariant không thể biểu diễn bằng ordinary `CHECK` hoặc foreign key. Không dùng trigger để ẩn toàn bộ business workflow.

### 15.4 Foreign-key deletion policy

Source-of-truth và security tables mặc định sử dụng `ON DELETE RESTRICT`, gồm:

- Organizations.
- Principals, teams và roles.
- Documents và document versions.
- Ingestion jobs và items.
- ACL snapshots.
- Review decisions.
- Reindex jobs và items.
- Audit events.

Controlled physical cleanup có thể dùng cascade chỉ trong derived-data subtree:

- Inactive derived set tới chunks.
- Inactive derived set tới chunk embeddings.
- Inactive derived set tới document relationships.

Derived cascade chỉ được gọi bởi privileged retention hoặc rebuild maintenance sau khi xác minh set không active và không còn cần cho Evidence retention.

Không được cascade từ organization, document hoặc document version xuống audit history.

### 15.5 Required non-vector indexes

Migration phải cung cấp tối thiểu:

- `organizations(organization_key)`.
- `principals(organization_id, principal_key)`.
- `teams(organization_id, team_key)`.
- `roles(organization_id, role_key)`.
- `documents(organization_id, document_id)`.
- `documents(organization_id, current_version_id)`.
- `document_versions(organization_id, document_pk, version_number)`.
- `document_versions(organization_id, document_pk, content_hash)`.
- Ingestion-job index theo organization, creator và submitted time.
- Ingestion-job work-claim index theo status và submitted time.
- Active team-membership index theo principal.
- Active role-membership index theo principal.
- Active role-capability index theo role.
- ACL indexes theo document version, direct user, team và role.
- Derived-set index theo source version và configuration.
- Partial unique index cho active derived set.
- Chunk index theo derived set và chunk index.
- Reindex-job work-claim index theo status và submitted time.
- Reindex-item index theo job và processing status.
- Review-decision index theo document và decided time.
- Audit indexes được liệt kê tại mục 13.6.
- Relationship indexes được liệt kê tại mục 11.6.
- Partial unique index trên `documents(organization_id, current_source_uri)` khi record active.

Pgvector access method, distance operator, vector dimension index và PostgreSQL full-text index được chốt ở retrieval milestone. Chưa chốt tuning không cho phép bỏ provenance, source-version hoặc ACL filtering.

### 15.6 Transaction and worker requirements

- Application transaction sử dụng PostgreSQL transaction thật; không giả lập atomicity bằng nhiều independent commits.
- Worker claim dùng `SELECT ... FOR UPDATE SKIP LOCKED` hoặc cơ chế có tính chất tương đương.
- Row lock phải có deterministic acquisition order khi transaction khóa nhiều document.
- Job item chỉ terminal sau khi business transaction tương ứng đã commit.
- Worker crash trước commit phải cho phép retry mà không tạo source version hoặc active derived set trùng.
- Worker crash sau commit nhưng trước acknowledgment phải được xử lý bằng persisted idempotent state.
- Database timeout hoặc serialization failure chỉ được retry khi operation được chứng minh idempotent.
- Retry attempt phải được ghi lại thay vì ghi đè Evidence cũ.

### 15.7 Required migration Evidence

Storage implementation chưa hoàn thành cho tới khi có reproducible Evidence cho:

1. Alembic upgrade từ database rỗng tới head thành công.
2. PostgreSQL xác nhận extension `vector` sẵn sàng.
3. Toàn bộ table, foreign key, check constraint, unique constraint, trigger và index bắt buộc tồn tại.
4. Duplicate organization-scoped document ID bị từ chối.
5. Cross-organization foreign key bị từ chối.
6. Current version không thể trỏ sang document khác.
7. Committed document version không thể bị sửa.
8. Restricted version không ACL bị từ chối khi commit.
9. Organization-scoped version có allowlist bị từ chối.
10. Hai active derived set cho cùng document bị từ chối.
11. Audit-event update và delete bị từ chối.
12. Failed ingestion item không để lại partial version, ACL hoặc derived row.
13. Concurrent update từ cùng current version chỉ một transaction thành công.
14. Migration và integration tests chạy được trong Docker Compose PostgreSQL.
15. Schema inspection hoặc migration output được lưu làm Evidence.

### 15.8 Current implementation gap

Tại thời điểm contract này được soạn:

- Repository có SQLAlchemy async engine, session factory và declarative `Base`.
- Repository chưa có Alembic dependency hoặc migration environment.
- ORM model cho storage contract chưa được triển khai.
- Database constraint và index trong contract chưa có runtime Evidence.
- Reindex worker và lifecycle transaction chưa được triển khai.

Vì vậy contract này chỉ là thiết kế baseline. Không được mô tả storage schema là đã migrated, đã kiểm thử hoặc end-to-end cho tới khi Evidence tại mục 15.7 tồn tại.

## 16. Acceptance gate, deferred scope and related documents

### 16.1 Contract acceptance gate

Storage schema contract chỉ được chuyển từ `Proposed` sang `Accepted` khi:

- Hai thành viên thống nhất stable document identity và organization boundary.
- Hai thành viên giải thích được document/version relationship và current-version pointer.
- Create, unchanged replay, semantic update, review transition, delete, restore, retry và rebuild có behavior rõ ràng.
- Transaction boundary ngăn source state, ACL, audit và active derived data rơi vào trạng thái nửa vời.
- Idempotency rule phân biệt request-level key và document-level content hash.
- ACL model phân biệt capability với document visibility.
- Restricted document fail closed và không có implicit public scope.
- Derived chunk, embedding và relationship truy ngược được tới đúng source version.
- Reindex có snapshot version, retry history và atomic activation.
- Review decision có independent reviewer, optimistic concurrency và audit.
- Delete không xóa source history hoặc audit history.
- Database enforcement phân biệt rõ `CHECK`, foreign key, unique index, partial index, deferred trigger và service transaction.
- Frontmatter contract, minimal API contract và storage contract không còn semantic conflict chưa được ghi nhận.
- Known implementation gap và deferred scope được giữ trung thực.
- Pull request ghi rõ ownership, review decision và Evidence kiểm tra tài liệu.

Khi gate trên được chấp nhận:

- `Status` đổi từ `Proposed` thành `Accepted`.
- `Contract version` đổi từ `0.1` thành `1.0`.
- Semantic change sau đó yêu cầu review và contract-version update.
- Typo hoặc wording correction không đổi hành vi có thể dùng patch revision mà không thay baseline semantics.

Contract acceptance chỉ chứng minh hai thành viên đã thống nhất thiết kế. Nó không chứng minh migration, ingestion lifecycle hoặc retrieval đã được triển khai.

### 16.2 Milestone 3 implementation gate

Milestone 3 chỉ hoàn thành khi có runtime hoặc automated-test Evidence cho:

1. Migration chạy thành công trên PostgreSQL sạch.
2. Schema và required constraints tồn tại đúng contract.
3. Create tạo stable document và version đầu tiên.
4. Unchanged replay không tạo version hoặc derived record trùng.
5. Semantic update tạo version mới và giữ version cũ.
6. ACL update mất hiệu lực trước retrieval tiếp theo.
7. Independent approval tạo review-transition version đúng.
8. Delete tạo tombstone và vô hiệu hóa derived data.
9. Restore giữ stable identity và yêu cầu review lại.
10. Retry không tạo job, version, ACL hoặc active derived set trùng.
11. Required broken relationship rollback source item.
12. Optional broken relationship tạo warning mà không tạo dangling edge.
13. Rebuild tạo logical derived state tương đương.
14. Failed rebuild giữ active set hợp lệ trước đó.
15. Concurrent version transition chỉ một transaction thành công.
16. Audit event không thể update hoặc delete bằng application role.
17. Unauthorized principal không nhìn thấy protected document, chunk, relationship hoặc citation.
18. Test chạy qua Docker Compose PostgreSQL và có output có thể tái hiện.

Code, migration file hoặc contract tồn tại nhưng chưa có output trên không đủ để đánh dấu Milestone 3 hoàn thành.

### 16.3 Deferred scope

Các nội dung sau không thuộc storage baseline hiện tại:

- Pgvector dimension cố định, distance operator và index tuning.
- PostgreSQL full-text dictionary, language configuration và index design.
- Reciprocal Rank Fusion.
- Retrieval scoring và reranking.
- Graph traversal depth, edge-type allowlist và document-count tuning.
- Model-based relationship hoặc entity extraction.
- Worker queue technology cụ thể.
- Production authentication provider.
- PostgreSQL Row-Level Security như lớp defense-in-depth.
- Production multi-tenant isolation.
- Backup infrastructure, replication và disaster-recovery automation.
- Retention duration cho raw ingestion payload, inactive derived set và audit archive.
- Table partitioning và production-scale capacity planning.
- Endpoint quản trị model hoặc derived configuration.
- Agent tự động code, deploy hoặc remediation.

Deferred scope không được dùng để bỏ qua stable identity, provenance, ACL-before-retrieval, audit hoặc rebuildability đã chốt trong baseline.

### 16.4 Known limitations

- Baseline ưu tiên tính đúng đắn và khả năng truy vết hơn tối ưu hiệu năng.
- Exact vector và full-text physical design chưa được benchmark.
- Audit storage chưa có retention hoặc archival policy production.
- Backend ACL là enforcement bắt buộc; PostgreSQL Row-Level Security chưa được thiết kế.
- Organization model là prototype boundary, chưa phải production-grade tenant isolation.
- Relationship graph baseline chỉ dùng explicit validated frontmatter relationship.
- Worker concurrency behavior phải được chứng minh bằng integration test.
- Current document and job counts còn nhỏ; chưa có capacity Evidence.
- Contract không thay thế threat model hoặc production security review.

### 16.5 Related documents

- [OpsBrain project specification](../project-specification.md)
- [Document frontmatter contract](./document-frontmatter.md)
- [Minimal API contract](./minimal-api.md)
- [Repository overview and setup](../../README.md)
- [Runtime verification and audit trail](../../AUDIT.md)

Local planning, ownership and personal-progress documents không được xem là public repository contract trừ khi chúng được chủ dự án quyết định đưa vào Git sau này.
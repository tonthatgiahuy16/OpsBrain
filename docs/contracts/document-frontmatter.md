# OpsBrain Document Frontmatter Contract

- **Status:** Proposal
- **Contract version:** `0.1`
- **Primary owner:** Backend and Data Engineering
- **Reviewer:** Corpus and Evaluation

## 1. Purpose

Contract này quy định metadata mà OpsBrain chấp nhận từ frontmatter của tài liệu Markdown. Mục tiêu là giúp sample vault, validator, PostgreSQL schema và ingestion pipeline sử dụng cùng một cấu trúc dữ liệu.

Đây là proposal để hai thành viên review. Chưa được xem là contract chính thức cho đến khi các open decision được chốt.

## 2. Scope

Contract áp dụng cho sáu loại tài liệu:

- `service`
- `deployment`
- `incident`
- `postmortem`
- `runbook`
- `alert`

Contract quy định:

- Field chung và field riêng theo document type.
- Kiểu dữ liệu và giá trị hợp lệ.
- Stable document identity.
- Provenance và review state.
- Quan hệ giữa tài liệu.
- Metadata ACL.
- Hành vi khi frontmatter không hợp lệ.

## 3. Source of truth boundaries

- Markdown body và frontmatter là đầu vào biên soạn từ Obsidian Vault.
- PostgreSQL giữ trạng thái document, version, lifecycle, ACL và audit chính thức.
- Chunk, embedding, full-text index và relationship edge là dữ liệu dẫn xuất có thể rebuild.
- Frontmatter không phải bằng chứng authentication.
- Quyền truy cập phải được kiểm tra bằng principal của request tại backend.
- Frontmatter thiếu hoặc sai ACL phải fail closed, không được mặc định thành public.

## 4. Common frontmatter fields

| Field | YAML type | Required | Proposed validation | Meaning |
|---|---|---:|---|---|
| `schema_version` | integer | Yes | Bằng `1` trong baseline | Phiên bản frontmatter schema |
| `document_id` | string | Yes | Stable, unique, lowercase kebab-case | Định danh tài liệu không đổi khi rename hoặc move file |
| `document_type` | string enum | Yes | Một trong sáu document type | Loại tài liệu |
| `title` | string | Yes | Không rỗng | Tiêu đề hiển thị |
| `document_status` | string enum | Yes | `active`, `deprecated`, `revoked` | Trạng thái vòng đời của tài liệu |
| `review_state` | string enum | Yes | `draft`, `in_review`, `approved`, `rejected` | Trạng thái kiểm duyệt |
| `owner_team` | string | Yes | Stable team ID | Nhóm chịu trách nhiệm nội dung |
| `provenance_type` | string enum | Yes | `internal`, `external`, `synthetic` | Nguồn gốc nội dung |
| `source_refs` | list of strings | Conditional | Không chứa secret | Ticket, URL hoặc reference hỗ trợ provenance |
| `access_scope` | string enum | Yes | `organization`, `restricted` | Phạm vi truy cập tổng quát |
| `allowed_users` | list of strings | Conditional | Stable principal IDs | User được phép đọc |
| `allowed_teams` | list of strings | Conditional | Stable team IDs | Team được phép đọc |
| `allowed_roles` | list of strings | Conditional | Stable role IDs | Role được phép đọc |
| `tags` | list of strings | No | Lowercase, không trùng | Nhãn hỗ trợ tổ chức nội dung |

## 5. Type-specific fields

Ngoài các field chung, mỗi document type phải tuân theo các field riêng dưới đây. Mọi field có hậu tố `_ref` hoặc `_refs` phải sử dụng `document_id`, không sử dụng title hay đường dẫn file.

### 5.1 Service

| Field | YAML type | Required | Validation | Meaning |
|---|---|---:|---|---|
| `criticality` | string enum | Yes | `low`, `medium`, `high`, `critical` | Mức độ quan trọng của service |
| `dependency_refs` | list of strings | No | Mỗi phần tử tham chiếu một document loại `service` | Các service phụ thuộc trực tiếp |

### 5.2 Deployment

| Field | YAML type | Required | Validation | Meaning |
|---|---|---:|---|---|
| `service_refs` | list of strings | Yes | Không rỗng; mỗi phần tử tham chiếu một document loại `service` | Service được triển khai |
| `environment` | string enum | Yes | `development`, `staging`, `production` | Môi trường triển khai |
| `release_version` | string | Yes | Không rỗng | Phiên bản hoặc định danh bản phát hành |
| `deployed_at` | datetime string | Yes | RFC 3339 và có timezone | Thời điểm triển khai |

### 5.3 Incident

| Field | YAML type | Required | Validation | Meaning |
|---|---|---:|---|---|
| `severity` | string enum | Yes | `sev1`, `sev2`, `sev3`, `sev4` | Mức độ nghiêm trọng |
| `incident_status` | string enum | Yes | `detected`, `investigating`, `mitigated`, `resolved` | Trạng thái xử lý sự cố |
| `affected_service_refs` | list of strings | Yes | Không rỗng; tham chiếu document loại `service` | Service bị ảnh hưởng |
| `started_at` | datetime string | Yes | RFC 3339 và có timezone | Thời điểm sự cố bắt đầu |
| `resolved_at` | datetime string | Conditional | Bắt buộc khi `incident_status: resolved` | Thời điểm sự cố kết thúc |

### 5.4 Postmortem

| Field | YAML type | Required | Validation | Meaning |
|---|---|---:|---|---|
| `incident_refs` | list of strings | Yes | Không rỗng; tham chiếu document loại `incident` | Sự cố được phân tích |
| `contributing_service_refs` | list of strings | No | Tham chiếu document loại `service` | Service liên quan đến nguyên nhân hoặc tác động |

Nguyên nhân gốc, tác động, bài học và action item được trình bày trong Markdown body ở baseline. Contract có thể bổ sung cấu trúc riêng cho action item ở phiên bản sau.

### 5.5 Runbook

| Field | YAML type | Required | Validation | Meaning |
|---|---|---:|---|---|
| `service_refs` | list of strings | Yes | Không rỗng; tham chiếu document loại `service` | Service mà runbook áp dụng |
| `runbook_kind` | string enum | Yes | `diagnosis`, `mitigation`, `recovery`, `maintenance` | Loại thao tác chính của runbook |

Các bước thực hiện, điều kiện dừng và rollback được viết trong Markdown body.

### 5.6 Alert

| Field | YAML type | Required | Validation | Meaning |
|---|---|---:|---|---|
| `service_refs` | list of strings | Yes | Không rỗng; tham chiếu document loại `service` | Service được giám sát |
| `source_system` | string | Yes | Stable identifier, không chứa secret | Hệ thống phát cảnh báo |
| `alert_rule_ref` | string | Yes | Không rỗng, không chứa secret | Định danh quy tắc cảnh báo |
| `severity` | string enum | Yes | `info`, `warning`, `critical` | Mức cảnh báo |

### 5.7 Relationship validation

- Reference phải dùng stable `document_id`.
- Document được tham chiếu phải đúng loại yêu cầu của field.
- Reference trùng trong cùng một danh sách không hợp lệ.
- Self-reference không hợp lệ, trừ khi một contract phiên bản sau cho phép rõ ràng.
- Hành vi đối với reference chưa tồn tại vẫn tuân theo broken relationship policy cần được hai thành viên chốt.

## 6. Identity rules

- `document_id` không được phụ thuộc vào tên hoặc đường dẫn file.
- Rename hoặc move file không tạo document mới nếu `document_id` không đổi.
- Hai file có cùng `document_id` trong một ingestion batch là lỗi.
- Quan hệ giữa tài liệu sử dụng `document_id`, không dùng title làm khóa.
- Obsidian wikilink có thể xuất hiện trong body nhưng không phải định danh authoritative.

## 7. Review rules

- Chỉ tài liệu đồng thời có `document_status: active` và `review_state: approved` mới đủ điều kiện đi vào retrieval baseline.
- Tài liệu ở trạng thái `deprecated`, `revoked`, `draft`, `in_review` hoặc `rejected` có thể được lưu để theo dõi nhưng không được dùng làm evidence cho câu trả lời trong baseline.
- Giá trị `review_state` trong file là input cần kiểm tra; nó không tự chứng minh người sửa có quyền phê duyệt.
- Backend phải lưu audit event khi review state thay đổi.
- `document_status`, `review_state` và các field ACL trong frontmatter chỉ là trạng thái được yêu cầu từ nguồn; backend phải kiểm tra transition và quyền của principal trước khi chấp nhận.
- Người có quyền sửa file không mặc nhiên có quyền approve, revoke hoặc thay đổi ACL.

## 8. ACL rules

- Request phải có authenticated principal gồm user, team và role context.
- `access_scope: organization` chỉ cho phép người dùng đã xác thực trong tổ chức.
- Khi `access_scope: restricted`, ít nhất một trong `allowed_users`, `allowed_teams` hoặc `allowed_roles` phải có giá trị.
- Thiếu hoặc sai ACL làm tài liệu không hợp lệ; không được mặc định cho phép truy cập.
- ACL phải được áp dụng trước ranking, graph expansion, context construction và citation.
- Derived chunks và relationships phải truy ngược được về document cùng ACL hiện hành.
- Thu hồi quyền phải làm kết quả retrieval thay đổi sau invalidation hoặc reindex theo contract.
- Khi `access_scope: organization`, `allowed_users`, `allowed_teams` và `allowed_roles` phải không xuất hiện hoặc phải là danh sách rỗng; nếu một trong các danh sách chứa giá trị thì document phải bị từ chối vì cấu hình ACL mâu thuẫn.
- Với `access_scope: restricted`, principal được phép truy cập khi có ít nhất một exact match giữa user ID, team ID hoặc role ID đáng tin cậy của request với danh sách tương ứng.
- Baseline không hỗ trợ wildcard, anonymous principal hoặc so khớp gần đúng đối với ACL.
- Thay đổi `access_scope` hoặc các danh sách `allowed_*` phải tạo audit event và làm mất hiệu lực cache hoặc dữ liệu dẫn xuất có liên quan trước lần retrieval tiếp theo.

## 9. System-derived metadata

Các giá trị sau do backend tạo và không yêu cầu người viết nhập trong frontmatter:

- Source path hoặc source URI do connector quan sát.
- Content hash.
- Database document version.
- Ingestion timestamp.
- Chunk ID và chunking version.
- Embedding và embedding model.
- Full-text search vector.
- Database primary key.
- Retrieval score và strategy.

## 10. Invalid document behavior

- Thiếu field bắt buộc: từ chối document và trả lỗi chỉ rõ field.
- Sai kiểu dữ liệu hoặc enum: từ chối document.
- `document_id` trùng trong cùng batch: từ chối các record xung đột.
- Quan hệ tới document không tồn tại: xử lý theo relationship policy chưa được chốt.
- ACL không hợp lệ: fail closed.
- Validation thất bại không được tạo active version, chunk, embedding hoặc relationship.
- Lỗi phải được ghi vào ingestion job và audit mà không đưa nội dung trái quyền vào log.

## 11. Open decisions

Hai thành viên cần chốt:

1. Có cần cho phép `access_scope: organization` trong baseline hay chỉ dùng restricted access?
2. Ai có quyền chuyển `review_state` thành `approved`?
3. `source_refs` bắt buộc với loại provenance nào?
4. Broken relationship sẽ làm ingestion fail hay chỉ tạo warning?
5. Metadata change có luôn tạo document version mới không?
6. Có cần thêm `effective_from` và `effective_until` cho tài liệu hết hiệu lực?

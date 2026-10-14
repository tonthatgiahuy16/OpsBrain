# OpsBrain Document Frontmatter Contract

- **Status:** Accepted for Milestone 2
- **Contract version:** `1.0`
- **Primary owner:** Backend and Data Engineering
- **Reviewer:** Corpus and Evaluation
- **Decision date:** `2026-10-14`

## 1. Purpose

Contract này quy định metadata mà OpsBrain chấp nhận từ frontmatter của tài liệu Markdown. Mục tiêu là giúp sample vault, validator, PostgreSQL schema và ingestion pipeline sử dụng cùng một cấu trúc dữ liệu.

Đây là contract baseline đã được hai thành viên chấp nhận cho Milestone 2. Sample vault, validator, database schema và ingestion pipeline phải tuân theo contract này. Mọi thay đổi semantic sau đó phải được review qua Pull Request và ghi nhận bằng contract version mới.

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
| `effective_from` | datetime string | No | RFC 3339 và có timezone | Thời điểm tài liệu bắt đầu đủ điều kiện có hiệu lực |
| `effective_until` | datetime string | No | RFC 3339 và có timezone; phải sau `effective_from` nếu cả hai cùng tồn tại | Thời điểm tài liệu hết hiệu lực |
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
- Self-reference không hợp lệ, trừ khi một contract version sau cho phép rõ ràng.
- Ingestion phải thu thập identity của toàn bộ document trong batch trước khi resolve relationship.
- Broken reference trong field bắt buộc làm document không hợp lệ; không được tạo active version hoặc derived record.
- Các field quan hệ bắt buộc gồm `deployment.service_refs`, `incident.affected_service_refs`, `postmortem.incident_refs`, `runbook.service_refs` và `alert.service_refs`.
- Broken reference trong field tùy chọn tạo warning trong ingestion job; document có thể tiếp tục ingest nhưng không được tạo dangling edge.
- Các field quan hệ tùy chọn gồm `service.dependency_refs` và `postmortem.contributing_service_refs`.

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
- Transition sang `approved` chỉ hợp lệ từ `in_review`.
- Principal thực hiện approval phải có capability `document:approve`.
- Reviewer approve phải khác principal đã submit hoặc sửa nội dung của version hiện tại.
- Nếu backend không truy vết được actor identity thì không được chấp nhận transition sang `approved`.
- Capability được ánh xạ từ role trong backend; frontmatter không được tự khai báo capability.

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
- Baseline chỉ hỗ trợ `organization` và `restricted`; không hỗ trợ `public` hoặc anonymous access.
- `access_scope` là field bắt buộc và không có default. Thiếu field này làm document không hợp lệ.

## 9. Provenance rules

- Mọi document phải có source path hoặc source URI do connector quan sát và backend lưu lại.
- Khi `provenance_type: external`, `source_refs` bắt buộc phải có ít nhất một identifier hoặc URL truy vết được.
- Khi `provenance_type: internal`, `source_refs` là tùy chọn nếu source path hoặc source URI của note đã đủ làm provenance.
- Tài liệu internal dựa trên ticket, log hoặc evidence ngoài note phải khai báo các nguồn đó trong `source_refs`.
- Khi `provenance_type: synthetic`, `source_refs` là tùy chọn nhưng tài liệu phải giữ nhãn synthetic và trải qua independent human review trước khi được `approved`.
- `source_refs` không được chứa secret, credential hoặc raw restricted content.

## 10. Versioning and idempotency rules

- Thay đổi semantic trong Markdown body hoặc author-controlled frontmatter phải tạo document version mới.
- Thay đổi lifecycle, review state hoặc ACL là semantic frontmatter change, vì vậy phải tạo document version và audit event; thay đổi ACL còn phải vô hiệu hóa cache và trạng thái truy cập dẫn xuất liên quan.
- Thứ tự YAML key, biểu diễn YAML tương đương và thay đổi line ending không tạo version mới sau canonicalization.
- Thay đổi system-derived metadata như ingestion timestamp không tạo document version mới.
- Content hash được tính từ canonicalized author-controlled frontmatter và normalized Markdown body; system-derived metadata không được đưa vào hash.
- Replay cùng stable identity và cùng content hash không được tạo active version, chunk hoặc relationship trùng.

## 11. Temporal validity rules

- `effective_from` và `effective_until` là optional.
- Nếu cả hai cùng tồn tại thì `effective_until` phải sau `effective_from`.
- Backend sử dụng thời gian có timezone và so sánh theo UTC.
- Tại thời điểm request `t`, document đủ điều kiện thời gian khi `effective_from` không tồn tại hoặc `t >= effective_from`, đồng thời `effective_until` không tồn tại hoặc `t < effective_until`.
- Không có `effective_from` nghĩa là không giới hạn thời điểm bắt đầu.
- Không có `effective_until` nghĩa là không có thời điểm hết hiệu lực được khai báo.
- `deprecated`, `revoked` hoặc review state khác `approved` luôn làm document không đủ điều kiện retrieval, bất kể khoảng thời gian.
- Document ngoài khoảng hiệu lực vẫn được giữ trong version history và audit.

## 12. System-derived metadata

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

## 13. Invalid document behavior

- Thiếu field bắt buộc: từ chối document và trả lỗi chỉ rõ field.
- Sai kiểu dữ liệu hoặc enum: từ chối document.
- `document_id` trùng trong cùng batch: từ chối các record xung đột.
- Broken relationship phải được xử lý theo mục 5.7; không được tạo dangling edge hoặc tự bỏ qua lỗi của field bắt buộc.
- ACL không hợp lệ: fail closed.
- Validation thất bại không được tạo active version, chunk, embedding hoặc relationship.
- Lỗi phải được ghi vào ingestion job và audit mà không đưa nội dung trái quyền vào log.

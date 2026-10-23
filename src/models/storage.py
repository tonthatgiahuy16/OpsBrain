from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Text,
    UniqueConstraint,
    ForeignKeyConstraint,
    Index,
    text,
    Boolean,
    Integer,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID as PGUUID

from src.database import Base


class Organization(Base):
    __tablename__ = "organizations"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_key = Column(Text, nullable=False)
    display_name = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_key"),
        CheckConstraint(
            "btrim(organization_key) <> ''",
            name="organization_key_not_blank",
        ),
        CheckConstraint(
            "btrim(display_name) <> ''",
            name="display_name_not_blank",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="status_allowed",
        ),
    )


class Principal(Base):
    __tablename__ = "principals"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    principal_key = Column(Text, nullable=False)
    principal_kind = Column(Text, nullable=False)
    display_name = Column(Text, nullable=True)
    status = Column(Text, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "principal_key"),
        UniqueConstraint("organization_id", "id"),
        CheckConstraint(
            "btrim(principal_key) <> ''",
            name="principal_key_not_blank",
        ),
        CheckConstraint(
            "principal_kind IN ('user', 'service')",
            name="principal_kind_allowed",
        ),
        CheckConstraint(
            "display_name IS NULL OR btrim(display_name) <> ''",
            name="display_name_not_blank_when_present",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled')",
            name="status_allowed",
        ),
    )


class IngestionJob(Base):
    __tablename__ = "ingestion_jobs"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    request_id = Column(Text, nullable=False)
    idempotency_key_hash = Column(Text, nullable=False)
    request_payload_hash = Column(Text, nullable=False)
    submitted_by_principal_id = Column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    status = Column(Text, nullable=False)
    submitted_document_count = Column(Integer, nullable=False)
    job_error_code = Column(Text, nullable=True)
    job_error_message = Column(Text, nullable=True)
    submitted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "submitted_by_principal_id",
            "idempotency_key_hash",
        ),
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            [
                "organization_id",
                "submitted_by_principal_id",
            ],
            [
                "principals.organization_id",
                "principals.id",
            ],
            name="fk_ingestion_jobs_submitted_by_principal",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_ingestion_jobs_org_principal_submitted_at",
            "organization_id",
            "submitted_by_principal_id",
            "submitted_at",
        ),
        Index(
            "ix_ingestion_jobs_status_submitted_at",
            "status",
            "submitted_at",
        ),

        CheckConstraint(
            "btrim(request_id) <> ''",
            name="request_id_not_blank",
        ),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="idempotency_key_hash_sha256_hex",
        ),
        CheckConstraint(
            "request_payload_hash ~ '^[0-9a-f]{64}$'",
            name="request_payload_hash_sha256_hex",
        ),
        CheckConstraint(
            (
                "status IN "
                "('queued', 'running', 'completed', "
                "'completed_with_errors', 'failed')"
            ),
            name="status_allowed",
        ),
        CheckConstraint(
            "submitted_document_count > 0",
            name="submitted_document_count_positive",
        ),
        CheckConstraint(
            (
                "(status = 'queued' "
                "AND started_at IS NULL "
                "AND completed_at IS NULL) "
                "OR "
                "(status = 'running' "
                "AND started_at IS NOT NULL "
                "AND completed_at IS NULL) "
                "OR "
                "(status IN ('completed', 'completed_with_errors', 'failed') "
                "AND started_at IS NOT NULL "
                "AND completed_at IS NOT NULL)"
            ),
            name="status_timestamps_consistent",
        ),
        CheckConstraint(
            (
                "completed_at IS NULL "
                "OR started_at IS NULL "
                "OR completed_at >= started_at"
            ),
            name="completion_not_before_start",
        ),
    )


class IngestionJobItem(Base):
    __tablename__ = "ingestion_job_items"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    ingestion_job_id = Column(PGUUID(as_uuid=True), nullable=False)
    item_index = Column(Integer, nullable=False)
    source_uri = Column(Text, nullable=False)
    submitted_markdown = Column(Text, nullable=True)
    submitted_content_hash = Column(Text, nullable=False)
    document_id = Column(Text, nullable=True)
    document_pk = Column(PGUUID(as_uuid=True), nullable=True)
    document_version_id = Column(PGUUID(as_uuid=True), nullable=True)
    processing_status = Column(Text, nullable=False)
    outcome = Column(Text, nullable=True)
    warnings = Column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    errors = Column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
    )
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "ingestion_job_id",
            "item_index",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ingestion_job_id"],
            [
                "ingestion_jobs.organization_id",
                "ingestion_jobs.id",
            ],
            name="fk_ingestion_job_items_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk"],
            [
                "documents.organization_id",
                "documents.id",
            ],
            name="fk_ingestion_job_items_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "organization_id",
                "document_pk",
                "document_version_id",
            ],
            [
                "document_versions.organization_id",
                "document_versions.document_pk",
                "document_versions.id",
            ],
            name="fk_ingestion_job_items_document_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "item_index >= 0",
            name="item_index_non_negative",
        ),
        CheckConstraint(
            "btrim(source_uri) <> ''",
            name="source_uri_not_blank",
        ),
        CheckConstraint(
            "submitted_content_hash ~ '^[0-9a-f]{64}$'",
            name="submitted_content_hash_sha256_hex",
        ),
        CheckConstraint(
            (
                "processing_status IN "
                "('queued', 'running', 'succeeded', 'failed')"
            ),
            name="processing_status_allowed",
        ),
        CheckConstraint(
            (
                "outcome IS NULL OR outcome IN "
                "('created', 'updated', 'unchanged', 'failed')"
            ),
            name="outcome_allowed",
        ),
        CheckConstraint(
            "jsonb_typeof(warnings) = 'array'",
            name="warnings_array",
        ),
        CheckConstraint(
            "jsonb_typeof(errors) = 'array'",
            name="errors_array",
        ),
        CheckConstraint(
            (
                "document_version_id IS NULL "
                "OR document_pk IS NOT NULL"
            ),
            name="document_version_requires_document",
        ),
        CheckConstraint(
            (
                "(processing_status IN ('queued', 'running') "
                "AND outcome IS NULL "
                "AND completed_at IS NULL) "
                "OR "
                "(processing_status = 'succeeded' "
                "AND outcome IN ('created', 'updated', 'unchanged') "
                "AND completed_at IS NOT NULL) "
                "OR "
                "(processing_status = 'failed' "
                "AND outcome = 'failed' "
                "AND completed_at IS NOT NULL "
                "AND jsonb_array_length(errors) > 0)"
            ),
            name="processing_state_consistent",
        ),
    )


class IngestionJobAttempt(Base):
    __tablename__ = "ingestion_job_attempts"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    ingestion_job_id = Column(PGUUID(as_uuid=True), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    retryable = Column(Boolean, nullable=False)
    error_code = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "ingestion_job_id",
            "attempt_number",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ingestion_job_id"],
            [
                "ingestion_jobs.organization_id",
                "ingestion_jobs.id",
            ],
            name="fk_ingestion_job_attempts_job",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "attempt_number > 0",
            name="attempt_number_positive",
        ),
        CheckConstraint(
            "status IN ('running', 'succeeded', 'failed')",
            name="status_allowed",
        ),
        CheckConstraint(
            "error_code IS NULL OR btrim(error_code) <> ''",
            name="error_code_not_blank_when_present",
        ),
        CheckConstraint(
            "error_message IS NULL OR btrim(error_message) <> ''",
            name="error_message_not_blank_when_present",
        ),
        CheckConstraint(
            (
                "(status = 'running' "
                "AND completed_at IS NULL "
                "AND error_code IS NULL "
                "AND error_message IS NULL) "
                "OR "
                "(status = 'succeeded' "
                "AND completed_at IS NOT NULL "
                "AND retryable = false "
                "AND error_code IS NULL "
                "AND error_message IS NULL) "
                "OR "
                "(status = 'failed' "
                "AND completed_at IS NOT NULL "
                "AND error_code IS NOT NULL)"
            ),
            name="attempt_state_consistent",
        ),
        CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="completion_not_before_start",
        ),
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )

    event_type = Column(Text, nullable=False)
    outcome = Column(Text, nullable=False)
    actor_kind = Column(Text, nullable=False)
    actor_principal_id = Column(PGUUID(as_uuid=True), nullable=True)
    request_id = Column(Text, nullable=True)
    correlation_id = Column(Text, nullable=True)
    document_pk = Column(PGUUID(as_uuid=True), nullable=True)
    document_version_id = Column(PGUUID(as_uuid=True), nullable=True)
    ingestion_job_id = Column(PGUUID(as_uuid=True), nullable=True)
    reindex_job_id = Column(PGUUID(as_uuid=True), nullable=True)
    review_decision_id = Column(PGUUID(as_uuid=True), nullable=True)
    reason_code = Column(Text, nullable=True)
    details = Column(JSONB, nullable=False)
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "actor_principal_id"],
            ["principals.organization_id", "principals.id"],
            name="fk_audit_events_actor_principal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk"],
            ["documents.organization_id", "documents.id"],
            name="fk_audit_events_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk", "document_version_id"],
            [
                "document_versions.organization_id",
                "document_versions.document_pk",
                "document_versions.id",
            ],
            name="fk_audit_events_document_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "ingestion_job_id"],
            ["ingestion_jobs.organization_id", "ingestion_jobs.id"],
            name="fk_audit_events_ingestion_job",
            ondelete="RESTRICT",
        ),
        Index(
            "ix_audit_events_org_occurred_at",
            "organization_id",
            "occurred_at",
        ),
        Index(
            "ix_audit_events_org_actor_occurred_at",
            "organization_id",
            "actor_principal_id",
            "occurred_at",
        ),
        Index(
            "ix_audit_events_org_document_occurred_at",
            "organization_id",
            "document_pk",
            "occurred_at",
        ),
        Index(
            "ix_audit_events_request_id",
            "request_id",
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        Index(
            "ix_audit_events_correlation_id",
            "correlation_id",
            postgresql_where=text("correlation_id IS NOT NULL"),
        ),
        CheckConstraint(
            "btrim(event_type) <> ''",
            name="event_type_not_blank",
        ),
        CheckConstraint(
            "outcome IN ('succeeded', 'failed', 'denied')",
            name="outcome_allowed",
        ),
        CheckConstraint(
            "actor_kind IN ('principal', 'system')",
            name="actor_kind_allowed",
        ),
        CheckConstraint(
            (
                "(actor_kind = 'principal' "
                "AND actor_principal_id IS NOT NULL) "
                "OR "
                "(actor_kind = 'system' "
                "AND actor_principal_id IS NULL)"
            ),
            name="actor_kind_consistent",
        ),
        CheckConstraint(
            "request_id IS NULL OR btrim(request_id) <> ''",
            name="request_id_not_blank_when_present",
        ),
        CheckConstraint(
            "correlation_id IS NULL OR btrim(correlation_id) <> ''",
            name="correlation_id_not_blank_when_present",
        ),
        CheckConstraint(
            "reason_code IS NULL OR btrim(reason_code) <> ''",
            name="reason_code_not_blank_when_present",
        ),
        CheckConstraint(
            "jsonb_typeof(details) = 'object'",
            name="details_object",
        ),
        CheckConstraint(
            (
                "btrim(coalesce(request_id, '')) <> '' "
                "OR btrim(coalesce(correlation_id, '')) <> '' "
                "OR actor_principal_id IS NOT NULL "
                "OR document_pk IS NOT NULL "
                "OR document_version_id IS NOT NULL "
                "OR ingestion_job_id IS NOT NULL"
            ),
            name="trace_anchor_present",
        ),
        CheckConstraint(
            "document_version_id IS NULL OR document_pk IS NOT NULL",
            name="document_version_requires_document",
        ),
        CheckConstraint(
            "reindex_job_id IS NULL AND review_decision_id IS NULL",
            name="deferred_refs_null",
        ),
    )


class Document(Base):
    __tablename__ = "documents"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    document_id = Column(Text, nullable=False)
    document_type = Column(Text, nullable=False)
    current_source_uri = Column(Text, nullable=False)
    record_state = Column(Text, nullable=False)
    current_version_id = Column(PGUUID(as_uuid=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    deleted_at = Column(DateTime(timezone=True), nullable=True)
    deleted_by_principal_id = Column(PGUUID(as_uuid=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "document_id"),
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "deleted_by_principal_id"],
            ["principals.organization_id", "principals.id"],
            name="fk_documents_deleted_by_principal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "id", "current_version_id"],
            [
                "document_versions.organization_id",
                "document_versions.document_pk",
                "document_versions.id",
            ],
            name="fk_documents_current_version",
            ondelete="RESTRICT",
            use_alter=True,
        ),
        CheckConstraint(
            "btrim(document_id) <> ''",
            name="document_id_not_blank",
        ),
        CheckConstraint(
            (
                "document_type IN "
                "('service', 'deployment', 'incident', "
                "'postmortem', 'runbook', 'alert')"
            ),
            name="document_type_allowed",
        ),
        CheckConstraint(
            "btrim(current_source_uri) <> ''",
            name="current_source_uri_not_blank",
        ),
        CheckConstraint(
            "record_state IN ('active', 'deleted')",
            name="record_state_allowed",
        ),
        CheckConstraint(
            (
                "(record_state = 'active' "
                "AND deleted_at IS NULL "
                "AND deleted_by_principal_id IS NULL) "
                "OR "
                "(record_state = 'deleted' "
                "AND deleted_at IS NOT NULL "
                "AND deleted_by_principal_id IS NOT NULL)"
            ),
            name="delete_state_consistent",
        ),
        Index(
            "uq_documents_organization_id_current_source_uri_active",
            "organization_id",
            "current_source_uri",
            unique=True,
            postgresql_where=text("record_state = 'active'"),
        ),
        Index(
            "ix_documents_organization_id_current_version_id",
            "organization_id",
            "current_version_id",
        ),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    document_pk = Column(PGUUID(as_uuid=True), nullable=False)
    parent_version_id = Column(PGUUID(as_uuid=True), nullable=True)
    version_number = Column(Integer, nullable=False)
    schema_version = Column(Integer, nullable=False)
    canonicalization_version = Column(Integer, nullable=False)
    hash_algorithm = Column(Text, nullable=False)
    content_hash = Column(Text, nullable=False)
    title = Column(Text, nullable=False)
    owner_team = Column(Text, nullable=False)
    source_uri = Column(Text, nullable=False)
    source_refs = Column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    tags = Column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    markdown_body = Column(Text, nullable=False)
    frontmatter_snapshot = Column(JSONB, nullable=False)
    document_status = Column(Text, nullable=False)
    review_state = Column(Text, nullable=False)
    provenance_type = Column(Text, nullable=False)
    access_scope = Column(Text, nullable=False)
    effective_from = Column(DateTime(timezone=True), nullable=True)
    effective_until = Column(DateTime(timezone=True), nullable=True)
    change_kind = Column(Text, nullable=False)
    created_by_principal_id = Column(PGUUID(as_uuid=True), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "document_pk",
            "version_number",
        ),
        UniqueConstraint(
            "organization_id",
            "document_pk",
            "id",
        ),
        UniqueConstraint(
            "organization_id",
            "document_pk",
            "parent_version_id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk"],
            ["documents.organization_id", "documents.id"],
            name="fk_document_versions_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "organization_id",
                "document_pk",
                "parent_version_id",
            ],
            [
                "document_versions.organization_id",
                "document_versions.document_pk",
                "document_versions.id",
            ],
            name="fk_document_versions_parent",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "created_by_principal_id"],
            ["principals.organization_id", "principals.id"],
            name="fk_document_versions_created_by_principal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "version_number > 0",
            name="version_number_positive",
        ),
        CheckConstraint(
            "schema_version > 0",
            name="schema_version_positive",
        ),
        CheckConstraint(
            "canonicalization_version > 0",
            name="canonicalization_version_positive",
        ),
        CheckConstraint(
            "hash_algorithm = 'sha256'",
            name="hash_algorithm_sha256",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="content_hash_sha256_hex",
        ),
        CheckConstraint(
            "btrim(title) <> ''",
            name="title_not_blank",
        ),
        CheckConstraint(
            "btrim(owner_team) <> ''",
            name="owner_team_not_blank",
        ),
        CheckConstraint(
            "btrim(source_uri) <> ''",
            name="source_uri_not_blank",
        ),
        CheckConstraint(
            (
                "document_status IN "
                "('active', 'deprecated', 'revoked')"
            ),
            name="document_status_allowed",
        ),
        CheckConstraint(
            (
                "review_state IN "
                "('draft', 'in_review', 'approved', 'rejected')"
            ),
            name="review_state_allowed",
        ),
        CheckConstraint(
            "provenance_type IN ('internal', 'external', 'synthetic')",
            name="provenance_type_allowed",
        ),
        CheckConstraint(
            "access_scope IN ('organization', 'restricted')",
            name="access_scope_allowed",
        ),
        CheckConstraint(
            (
                "effective_until IS NULL "
                "OR effective_from IS NULL "
                "OR effective_until > effective_from"
            ),
            name="effective_window_ordered",
        ),
        CheckConstraint(
            (
                "change_kind IN "
                "('create', 'content_update', 'metadata_update', "
                "'review_transition', 'delete', 'restore')"
            ),
            name="change_kind_allowed",
        ),
        CheckConstraint(
            "jsonb_typeof(frontmatter_snapshot) = 'object'",
            name="frontmatter_snapshot_object",
        ),
        CheckConstraint(
            (
                "(version_number = 1 "
                "AND change_kind = 'create' "
                "AND parent_version_id IS NULL) "
                "OR "
                "(version_number > 1 "
                "AND change_kind <> 'create' "
                "AND parent_version_id IS NOT NULL)"
            ),
            name="version_lineage_consistent",
        ),
        Index(
            "ix_document_versions_organization_id_document_pk_content_hash",
            "organization_id",
            "document_pk",
            "content_hash",
        ),
    )

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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID as PGUUID
from pgvector.sqlalchemy import Vector

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


class Team(Base):
    __tablename__ = "teams"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    team_key = Column(Text, nullable=False)
    display_name = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "team_key"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        CheckConstraint("btrim(team_key) <> ''", name="team_key_not_blank"),
        CheckConstraint("btrim(display_name) <> ''", name="display_name_not_blank"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_allowed"),
    )


class Role(Base):
    __tablename__ = "roles"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    role_key = Column(Text, nullable=False)
    display_name = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "role_key"),
        ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        CheckConstraint("btrim(role_key) <> ''", name="role_key_not_blank"),
        CheckConstraint("btrim(display_name) <> ''", name="display_name_not_blank"),
        CheckConstraint("status IN ('active', 'disabled')", name="status_allowed"),
    )


class PrincipalTeamMembership(Base):
    __tablename__ = "principal_team_memberships"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    principal_id = Column(PGUUID(as_uuid=True), nullable=False)
    team_id = Column(PGUUID(as_uuid=True), nullable=False)
    status = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "principal_id", "team_id"),
        ForeignKeyConstraint(["organization_id", "principal_id"], ["principals.organization_id", "principals.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "team_id"], ["teams.organization_id", "teams.id"], ondelete="RESTRICT"),
        CheckConstraint("status IN ('active', 'revoked')", name="status_allowed"),
        Index("ix_principal_team_memberships_active_principal", "organization_id", "principal_id", postgresql_where=text("status = 'active'")),
    )


class PrincipalRoleMembership(Base):
    __tablename__ = "principal_role_memberships"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    principal_id = Column(PGUUID(as_uuid=True), nullable=False)
    role_id = Column(PGUUID(as_uuid=True), nullable=False)
    status = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "principal_id", "role_id"),
        ForeignKeyConstraint(["organization_id", "principal_id"], ["principals.organization_id", "principals.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "role_id"], ["roles.organization_id", "roles.id"], ondelete="RESTRICT"),
        CheckConstraint("status IN ('active', 'revoked')", name="status_allowed"),
        Index("ix_principal_role_memberships_active_principal", "organization_id", "principal_id", postgresql_where=text("status = 'active'")),
    )


class Capability(Base):
    __tablename__ = "capabilities"

    capability_key = Column(Text, primary_key=True)
    description = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        CheckConstraint(
            "btrim(capability_key) <> ''",
            name="capability_key_not_blank",
        ),
        CheckConstraint(
            "btrim(description) <> ''",
            name="description_not_blank",
        ),
    )


class RoleCapability(Base):
    __tablename__ = "role_capabilities"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    role_id = Column(PGUUID(as_uuid=True), nullable=False)
    capability_key = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    granted_by_principal_id = Column(PGUUID(as_uuid=True), nullable=False)
    revoked_by_principal_id = Column(PGUUID(as_uuid=True), nullable=True)
    granted_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), nullable=False,
        server_default=func.now(), onupdate=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "role_id", "capability_key"),
        ForeignKeyConstraint(
            ["capability_key"], ["capabilities.capability_key"],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "capability_key IN "
            "('document:approve', 'document:ingest', 'document:read_state', "
            "'index:read', 'index:rebuild', 'ingestion:read', 'query:execute')",
            name="capability_key_allowlist",
        ),
        ForeignKeyConstraint(
            ["organization_id", "granted_by_principal_id"],
            ["principals.organization_id", "principals.id"],
            name="fk_role_capabilities_granted_by_principal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "revoked_by_principal_id"],
            ["principals.organization_id", "principals.id"],
            name="fk_role_capabilities_revoked_by_principal",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "role_id"],
            ["roles.organization_id", "roles.id"], ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="status_allowed",
        ),
        CheckConstraint(
            "status <> 'active' OR "
            "(revoked_at IS NULL AND revoked_by_principal_id IS NULL)",
            name="active_grant_consistent",
        ),
        CheckConstraint(
            "status <> 'revoked' OR "
            "(revoked_at IS NOT NULL AND revoked_by_principal_id IS NOT NULL)",
            name="revoked_grant_consistent",
        ),
        CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name="revocation_not_before_grant",
        ),
        Index(
            "ix_role_capabilities_active_role",
            "organization_id", "role_id",
            postgresql_where=text("status = 'active'"),
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
    active_attempt_id = Column(PGUUID(as_uuid=True), nullable=True)
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
        ForeignKeyConstraint(
            [
                "organization_id",
                "id",
                "active_attempt_id",
            ],
            [
                "ingestion_job_attempts.organization_id",
                "ingestion_job_attempts.ingestion_job_id",
                "ingestion_job_attempts.id",
            ],
            name="fk_ingestion_jobs_active_attempt",
            ondelete="RESTRICT",
            use_alter=True,
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
        CheckConstraint(
            (
                "(status = 'running' AND active_attempt_id IS NOT NULL) "
                "OR (status IN ('queued', 'completed', 'completed_with_errors', 'failed') "
                "AND active_attempt_id IS NULL)"
            ),
            name="ck_ingestion_jobs_active_attempt_matches_status",
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
        UniqueConstraint(
            "organization_id",
            "ingestion_job_id",
            "id",
            name="uq_ingestion_attempt_job_ref",
        ),
        Index(
            "uq_ingestion_job_attempts_running_job",
            "organization_id",
            "ingestion_job_id",
            unique=True,
            postgresql_where=text("status = 'running'"),
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


class ReindexJob(Base):
    __tablename__ = "reindex_jobs"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    request_id = Column(Text, nullable=False)
    idempotency_key_hash = Column(Text, nullable=False)
    request_payload_hash = Column(Text, nullable=False)
    submitted_by_principal_id = Column(PGUUID(as_uuid=True), nullable=False)
    scope_type = Column(Text, nullable=False)
    targets = Column(ARRAY(Text), nullable=False)
    reason = Column(Text, nullable=False)
    configuration_id = Column(PGUUID(as_uuid=True), nullable=False)
    requested_document_count = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    job_error_code = Column(Text, nullable=True)
    job_error_message = Column(Text, nullable=True)
    submitted_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "submitted_by_principal_id", "idempotency_key_hash"),
        ForeignKeyConstraint(["organization_id", "submitted_by_principal_id"], ["principals.organization_id", "principals.id"], name="fk_reindex_jobs_submitted_by_principal", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "configuration_id"], ["derived_configurations.organization_id", "derived_configurations.id"], name="fk_reindex_jobs_configuration", ondelete="RESTRICT"),
        Index("ix_reindex_jobs_status_submitted_at", "status", "submitted_at"),
        CheckConstraint("btrim(request_id) <> ''", name="request_id_not_blank"),
        CheckConstraint("btrim(reason) <> ''", name="reason_not_blank"),
        CheckConstraint("idempotency_key_hash ~ '^[0-9a-f]{64}$'", name="idempotency_key_hash_sha256_hex"),
        CheckConstraint("request_payload_hash ~ '^[0-9a-f]{64}$'", name="request_payload_hash_sha256_hex"),
        CheckConstraint("scope_type IN ('documents', 'organization')", name="scope_type_allowed"),
        CheckConstraint("cardinality(targets) > 0", name="targets_not_empty"),
        CheckConstraint("targets <@ ARRAY['chunks', 'embeddings', 'full_text', 'relationships']::text[]", name="targets_allowed"),
        CheckConstraint("text_array_has_no_duplicates(targets)", name="targets_unique"),
        CheckConstraint("requested_document_count >= 0", name="requested_document_count_non_negative"),
        CheckConstraint("status IN ('queued', 'running', 'completed', 'completed_with_errors', 'failed')", name="status_allowed"),
        CheckConstraint("job_error_code IS NULL OR btrim(job_error_code) <> ''", name="job_error_code_not_blank_when_present"),
        CheckConstraint("job_error_message IS NULL OR btrim(job_error_message) <> ''", name="job_error_message_not_blank_when_present"),
        CheckConstraint("(status = 'queued' AND started_at IS NULL AND completed_at IS NULL) OR (status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR (status IN ('completed', 'completed_with_errors', 'failed') AND started_at IS NOT NULL AND completed_at IS NOT NULL)", name="status_timestamps_consistent"),
        CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name="completion_not_before_start"),
    )


class ReindexJobItem(Base):
    __tablename__ = "reindex_job_items"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    reindex_job_id = Column(PGUUID(as_uuid=True), nullable=False)
    document_pk = Column(PGUUID(as_uuid=True), nullable=False)
    source_document_version_id = Column(PGUUID(as_uuid=True), nullable=False)
    output_derived_set_id = Column(PGUUID(as_uuid=True), nullable=True)
    processing_status = Column(Text, nullable=False)
    active_attempt_id = Column(PGUUID(as_uuid=True), nullable=True)
    outcome = Column(Text, nullable=True)
    skip_code = Column(Text, nullable=True)
    error_code = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    next_retry_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint("organization_id", "reindex_job_id", "document_pk"),
        ForeignKeyConstraint(["organization_id", "reindex_job_id"], ["reindex_jobs.organization_id", "reindex_jobs.id"], name="fk_reindex_job_items_job", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "document_pk"], ["documents.organization_id", "documents.id"], name="fk_reindex_job_items_document", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "document_pk", "source_document_version_id"], ["document_versions.organization_id", "document_versions.document_pk", "document_versions.id"], name="fk_reindex_job_items_source_version", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "source_document_version_id", "output_derived_set_id"], ["derived_sets.organization_id", "derived_sets.document_version_id", "derived_sets.id"], name="fk_reindex_job_items_output_derived_set", ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "id", "active_attempt_id"], ["reindex_job_item_attempts.organization_id", "reindex_job_item_attempts.reindex_job_item_id", "reindex_job_item_attempts.id"], name="fk_reindex_job_items_active_attempt", ondelete="RESTRICT", use_alter=True),
        Index("ix_reindex_job_items_job_processing_status", "reindex_job_id", "processing_status"),
        CheckConstraint("processing_status IN ('queued', 'running', 'succeeded', 'failed')", name="processing_status_allowed"),
        CheckConstraint("outcome IS NULL OR outcome IN ('rebuilt', 'skipped', 'failed')", name="outcome_allowed"),
        CheckConstraint("skip_code IS NULL OR btrim(skip_code) <> ''", name="skip_code_not_blank_when_present"),
        CheckConstraint("error_code IS NULL OR btrim(error_code) <> ''", name="error_code_not_blank_when_present"),
        CheckConstraint("error_message IS NULL OR btrim(error_message) <> ''", name="error_message_not_blank_when_present"),
        CheckConstraint("next_retry_at IS NULL OR processing_status = 'queued'", name="next_retry_requires_queued"),
        CheckConstraint("(processing_status = 'running' AND active_attempt_id IS NOT NULL) OR (processing_status IN ('queued', 'succeeded', 'failed') AND active_attempt_id IS NULL)", name="active_attempt_matches_processing_status"),
        CheckConstraint("(processing_status IN ('queued', 'running') AND outcome IS NULL AND completed_at IS NULL) OR (processing_status = 'succeeded' AND outcome = 'rebuilt' AND output_derived_set_id IS NOT NULL AND completed_at IS NOT NULL) OR (processing_status = 'succeeded' AND outcome = 'skipped' AND skip_code IS NOT NULL AND output_derived_set_id IS NULL AND completed_at IS NOT NULL) OR (processing_status = 'failed' AND outcome = 'failed' AND error_code IS NOT NULL AND completed_at IS NOT NULL)", name="processing_state_consistent"),
    )


class ReindexJobItemAttempt(Base):
    __tablename__ = "reindex_job_item_attempts"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    reindex_job_item_id = Column(PGUUID(as_uuid=True), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    retryable = Column(Boolean, nullable=False)
    error_code = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    lease_expires_at = Column(DateTime(timezone=True), nullable=False, server_default=text("now() + interval '60 seconds'"))
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "reindex_job_item_id", "attempt_number"),
        UniqueConstraint(
            "organization_id",
            "reindex_job_item_id",
            "id",
            name="uq_reindex_attempt_item_ref",
        ),
        ForeignKeyConstraint(["organization_id", "reindex_job_item_id"], ["reindex_job_items.organization_id", "reindex_job_items.id"], name="fk_reindex_job_item_attempts_item", ondelete="RESTRICT"),
        CheckConstraint("attempt_number > 0", name="attempt_number_positive"),
        CheckConstraint("status IN ('running', 'succeeded', 'failed')", name="status_allowed"),
        CheckConstraint("error_code IS NULL OR btrim(error_code) <> ''", name="error_code_not_blank_when_present"),
        CheckConstraint("error_message IS NULL OR btrim(error_message) <> ''", name="error_message_not_blank_when_present"),
        CheckConstraint("(status = 'running' AND completed_at IS NULL AND error_code IS NULL AND error_message IS NULL) OR (status = 'succeeded' AND completed_at IS NOT NULL AND retryable = false AND error_code IS NULL AND error_message IS NULL) OR (status = 'failed' AND completed_at IS NOT NULL AND error_code IS NOT NULL)", name="attempt_state_consistent"),
        CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name="completion_not_before_start"),
        CheckConstraint("lease_expires_at > started_at", name="lease_after_start"),
        Index("uq_reindex_job_item_attempts_running_item", "organization_id", "reindex_job_item_id", unique=True, postgresql_where=text("status = 'running'")),
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
        ForeignKeyConstraint(
            ["organization_id", "reindex_job_id"],
            ["reindex_jobs.organization_id", "reindex_jobs.id"],
            name="fk_audit_events_reindex_job",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "review_decision_id"],
            ["document_review_decisions.organization_id", "document_review_decisions.id"],
            name="fk_audit_events_review_decision",
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
    )


class DocumentReviewDecision(Base):
    __tablename__ = "document_review_decisions"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    request_id = Column(Text, nullable=False)
    idempotency_key_hash = Column(Text, nullable=False)
    request_payload_hash = Column(Text, nullable=False)
    document_pk = Column(PGUUID(as_uuid=True), nullable=False)
    reviewed_version_id = Column(PGUUID(as_uuid=True), nullable=False)
    resulting_version_id = Column(PGUUID(as_uuid=True), nullable=False)
    decision = Column(Text, nullable=False)
    reason = Column(Text, nullable=False)
    reviewer_principal_id = Column(PGUUID(as_uuid=True), nullable=False)
    decided_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint(
            "organization_id", "reviewer_principal_id", "idempotency_key_hash",
            name="uq_document_review_decisions_idempotency",
        ),
        UniqueConstraint(
            "organization_id", "resulting_version_id", name="uq_document_review_decisions_resulting_version",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk"],
            ["documents.organization_id", "documents.id"],
            name="fk_document_review_decisions_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk", "reviewed_version_id"],
            ["document_versions.organization_id", "document_versions.document_pk", "document_versions.id"],
            name="fk_document_review_decisions_reviewed_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk", "resulting_version_id"],
            ["document_versions.organization_id", "document_versions.document_pk", "document_versions.id"],
            name="fk_document_review_decisions_resulting_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "reviewer_principal_id"],
            ["principals.organization_id", "principals.id"],
            name="fk_document_review_decisions_reviewer",
            ondelete="RESTRICT",
        ),
        CheckConstraint("decision IN ('approve', 'reject')", name="decision_allowed"),
        CheckConstraint("btrim(request_id) <> ''", name="request_id_not_blank"),
        CheckConstraint("btrim(reason) <> ''", name="reason_not_blank"),
        CheckConstraint("idempotency_key_hash ~ '^[0-9a-f]{64}$'", name="idempotency_key_hash_sha256_hex"),
        CheckConstraint("request_payload_hash ~ '^[0-9a-f]{64}$'", name="request_payload_hash_sha256_hex"),
        Index("ix_document_review_decisions_org_document", "organization_id", "document_pk", "decided_at"),
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
        # ACL RLS intentionally cannot make an uncommitted restricted version
        # readable just to satisfy PostgreSQL's INSERT ... RETURNING.  Version
        # IDs are generated in application code, so no returned server value is
        # needed during the write path.
        {"implicit_returning": False},
    )


class DocumentVersionAclEntry(Base):
    """Immutable read grant attached to one source document version."""

    __tablename__ = "document_version_acl_entries"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    document_pk = Column(PGUUID(as_uuid=True), nullable=False)
    document_version_id = Column(PGUUID(as_uuid=True), nullable=False)
    user_principal_id = Column(PGUUID(as_uuid=True), nullable=True)
    team_id = Column(PGUUID(as_uuid=True), nullable=True)
    role_id = Column(PGUUID(as_uuid=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        ForeignKeyConstraint(
            ["organization_id", "document_pk", "document_version_id"],
            ["document_versions.organization_id", "document_versions.document_pk", "document_versions.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "user_principal_id"],
            ["principals.organization_id", "principals.id"], ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(["organization_id", "team_id"], ["teams.organization_id", "teams.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["organization_id", "role_id"], ["roles.organization_id", "roles.id"], ondelete="RESTRICT"),
        CheckConstraint(
            "num_nonnulls(user_principal_id, team_id, role_id) = 1",
            name="exactly_one_subject",
        ),
        Index("uq_document_version_acl_user_principal_id", "organization_id", "document_version_id", "user_principal_id", unique=True, postgresql_where=text("user_principal_id IS NOT NULL")),
        Index("uq_document_version_acl_team_id", "organization_id", "document_version_id", "team_id", unique=True, postgresql_where=text("team_id IS NOT NULL")),
        Index("uq_document_version_acl_role_id", "organization_id", "document_version_id", "role_id", unique=True, postgresql_where=text("role_id IS NOT NULL")),
        Index("ix_document_version_acl_entries_document", "organization_id", "document_version_id"),
    )


class DerivedConfiguration(Base):
    __tablename__ = "derived_configurations"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(
        PGUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    configuration_key = Column(Text, nullable=False)
    chunking_version = Column(Text, nullable=False)
    embedding_model = Column(Text, nullable=True)
    embedding_model_revision = Column(Text, nullable=True)
    embedding_dimensions = Column(Integer, nullable=True)
    full_text_configuration = Column(Text, nullable=True)
    relationship_extractor_version = Column(Text, nullable=True)
    configuration_payload = Column(JSONB, nullable=False)
    configuration_hash = Column(Text, nullable=False)
    status = Column(Text, nullable=False)
    created_by_principal_id = Column(
        PGUUID(as_uuid=True),
        nullable=False,
    )
    first_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "configuration_key"),
        UniqueConstraint("organization_id", "id"),
        ForeignKeyConstraint(
            ["organization_id", "created_by_principal_id"],
            ["principals.organization_id", "principals.id"],
            name="fk_derived_configurations_created_by_principal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "btrim(configuration_key) <> ''",
            name="configuration_key_not_blank",
        ),
        CheckConstraint(
            "btrim(chunking_version) <> ''",
            name="chunking_version_not_blank",
        ),
        CheckConstraint(
            "configuration_hash ~ '^[0-9a-f]{64}$'",
            name="configuration_hash_sha256_hex",
        ),
        CheckConstraint(
            "jsonb_typeof(configuration_payload) = 'object'",
            name="configuration_payload_object",
        ),
        CheckConstraint(
            "status IN ('active', 'retired')",
            name="status_allowed",
        ),
        CheckConstraint(
            "embedding_dimensions IS NULL OR embedding_dimensions > 0",
            name="embedding_dimensions_positive",
        ),
        CheckConstraint(
            "embedding_model IS NULL OR embedding_dimensions IS NOT NULL",
            name="embedding_model_requires_dimensions",
        ),
    )


class DerivedSet(Base):
    __tablename__ = "derived_sets"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    document_pk = Column(PGUUID(as_uuid=True), nullable=False)
    document_version_id = Column(PGUUID(as_uuid=True), nullable=False)
    configuration_id = Column(PGUUID(as_uuid=True), nullable=False)
    build_number = Column(Integer, nullable=False)
    status = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("false"))
    failure_code = Column(Text, nullable=True)
    failure_message = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    activated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "id"),
        UniqueConstraint(
            "organization_id",
            "document_version_id",
            "id",
        ),
        UniqueConstraint(
            "organization_id",
            "document_version_id",
            "configuration_id",
            "build_number",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk", "document_version_id"],
            [
                "document_versions.organization_id",
                "document_versions.document_pk",
                "document_versions.id",
            ],
            name="fk_derived_sets_document_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "configuration_id"],
            ["derived_configurations.organization_id", "derived_configurations.id"],
            name="fk_derived_sets_configuration",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "build_number > 0",
            name="build_number_positive",
        ),
        CheckConstraint(
            "status IN ('pending', 'building', 'ready', 'stale', 'failed')",
            name="status_allowed",
        ),
        CheckConstraint(
            "status <> 'pending' OR "
            "(started_at IS NULL AND completed_at IS NULL AND activated_at IS NULL)",
            name="pending_timestamps",
        ),
        CheckConstraint(
            "status <> 'building' OR "
            "(started_at IS NOT NULL AND completed_at IS NULL)",
            name="building_timestamps",
        ),
        CheckConstraint(
            "status NOT IN ('ready', 'stale', 'failed') OR completed_at IS NOT NULL",
            name="terminal_completed_at",
        ),
        CheckConstraint(
            "status <> 'failed' OR "
            "(failure_code IS NOT NULL AND btrim(failure_code) <> '')",
            name="failed_requires_code",
        ),
        CheckConstraint(
            "NOT is_active OR status = 'ready'",
            name="active_requires_ready",
        ),
        CheckConstraint(
            "NOT is_active OR activated_at IS NOT NULL",
            name="active_requires_activated_at",
        ),
        Index(
            "uq_derived_sets_one_active_per_document",
            "organization_id",
            "document_pk",
            unique=True,
            postgresql_where=text("is_active"),
        ),
    )

class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    derived_set_id = Column(PGUUID(as_uuid=True), nullable=False)
    document_pk = Column(PGUUID(as_uuid=True), nullable=False)
    document_version_id = Column(PGUUID(as_uuid=True), nullable=False)
    chunk_key = Column(Text, nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunking_version = Column(Text, nullable=False)
    heading_path = Column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'::text[]"),
    )
    content = Column(Text, nullable=False)
    content_hash = Column(Text, nullable=False)
    start_offset = Column(Integer, nullable=True)
    end_offset = Column(Integer, nullable=True)
    token_count = Column(Integer, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "derived_set_id",
            "chunk_key",
        ),
        UniqueConstraint(
            "organization_id",
            "derived_set_id",
            "chunk_index",
        ),
        UniqueConstraint(
            "organization_id",
            "derived_set_id",
            "id",
        ),
        ForeignKeyConstraint(
            ["organization_id", "derived_set_id"],
            ["derived_sets.organization_id", "derived_sets.id"],
            name="fk_chunks_derived_set",
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
            name="fk_chunks_document_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "btrim(chunk_key) <> ''",
            name="chunk_key_not_blank",
        ),
        CheckConstraint(
            "chunk_index >= 0",
            name="chunk_index_non_negative",
        ),
        CheckConstraint(
            "btrim(chunking_version) <> ''",
            name="chunking_version_not_blank",
        ),
        CheckConstraint(
            "btrim(content) <> ''",
            name="content_not_blank",
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'",
            name="content_hash_sha256_hex",
        ),
        CheckConstraint(
            "start_offset IS NULL OR start_offset >= 0",
            name="start_offset_non_negative",
        ),
        CheckConstraint(
            "end_offset IS NULL OR end_offset >= 0",
            name="end_offset_non_negative",
        ),
        CheckConstraint(
            "end_offset IS NULL OR start_offset IS NULL OR end_offset > start_offset",
            name="offsets_ordered",
        ),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="token_count_non_negative",
        ),
    )


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    derived_set_id = Column(PGUUID(as_uuid=True), nullable=False)
    chunk_id = Column(PGUUID(as_uuid=True), nullable=False)
    embedding_model = Column(Text, nullable=False)
    embedding_model_revision = Column(Text, nullable=True)
    embedding_dimensions = Column(Integer, nullable=False)
    embedding_input_hash = Column(Text, nullable=False)
    embedding = Column(Vector(), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "derived_set_id", "chunk_id"),
        ForeignKeyConstraint(
            ["organization_id", "derived_set_id"],
            ["derived_sets.organization_id", "derived_sets.id"],
            name="fk_chunk_embeddings_derived_set",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "derived_set_id", "chunk_id"],
            ["chunks.organization_id", "chunks.derived_set_id", "chunks.id"],
            name="fk_chunk_embeddings_chunk",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "btrim(embedding_model) <> ''",
            name="embedding_model_not_blank",
        ),
        CheckConstraint(
            "embedding_dimensions > 0",
            name="embedding_dimensions_positive",
        ),
        CheckConstraint(
            "embedding_input_hash ~ '^[0-9a-f]{64}$'",
            name="embedding_input_hash_sha256_hex",
        ),
        CheckConstraint(
            "vector_dims(embedding) = embedding_dimensions",
            name="embedding_dimensions_match_vector",
        ),
    )


class ChunkFullTextIndex(Base):
    __tablename__ = "chunk_full_text_indexes"

    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id = Column(PGUUID(as_uuid=True), nullable=False)
    derived_set_id = Column(PGUUID(as_uuid=True), nullable=False)
    chunk_id = Column(PGUUID(as_uuid=True), nullable=False)
    document_pk = Column(PGUUID(as_uuid=True), nullable=False)
    document_version_id = Column(PGUUID(as_uuid=True), nullable=False)
    full_text_configuration = Column(Text, nullable=False)
    input_hash = Column(Text, nullable=False)
    search_vector = Column(TSVECTOR(), nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    __table_args__ = (
        UniqueConstraint("organization_id", "derived_set_id", "chunk_id"),
        ForeignKeyConstraint(
            ["organization_id", "derived_set_id"],
            ["derived_sets.organization_id", "derived_sets.id"],
            name="fk_chunk_full_text_indexes_derived_set",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "derived_set_id", "chunk_id"],
            ["chunks.organization_id", "chunks.derived_set_id", "chunks.id"],
            name="fk_chunk_full_text_indexes_chunk",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["organization_id", "document_pk", "document_version_id"],
            [
                "document_versions.organization_id",
                "document_versions.document_pk",
                "document_versions.id",
            ],
            name="fk_chunk_full_text_indexes_document_version",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "full_text_configuration = 'english'",
            name="full_text_configuration_allowed",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name="input_hash_sha256_hex",
        ),
        Index(
            "ix_chunk_full_text_indexes_lookup",
            "organization_id",
            "derived_set_id",
            "chunk_id",
        ),
        Index(
            "ix_chunk_full_text_indexes_search_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

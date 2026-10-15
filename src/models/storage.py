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

"""create reindex job storage

Revision ID: 9c4e7a1d2b3f
Revises: e2b1d6078c4a
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "9c4e7a1d2b3f"
down_revision: Union[str, Sequence[str], None] = "e2b1d6078c4a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("""
        CREATE FUNCTION text_array_has_no_duplicates(input_values text[]) RETURNS boolean
        LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
            SELECT cardinality(input_values) = (
                SELECT count(DISTINCT value) FROM unnest(input_values) AS item(value)
            )
        $$;
    """)
    op.create_unique_constraint(
        "uq_derived_sets_organization_id_document_version_id_id",
        "derived_sets",
        ["organization_id", "document_version_id", "id"],
    )
    op.create_table(
        "reindex_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key_hash", sa.Text(), nullable=False),
        sa.Column("request_payload_hash", sa.Text(), nullable=False),
        sa.Column("submitted_by_principal_id", sa.UUID(), nullable=False),
        sa.Column("scope_type", sa.Text(), nullable=False),
        sa.Column("targets", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("configuration_id", sa.UUID(), nullable=False),
        sa.Column("requested_document_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("job_error_code", sa.Text(), nullable=True),
        sa.Column("job_error_message", sa.Text(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("btrim(request_id) <> ''", name=op.f("ck_reindex_jobs_request_id_not_blank")),
        sa.CheckConstraint("btrim(reason) <> ''", name=op.f("ck_reindex_jobs_reason_not_blank")),
        sa.CheckConstraint("idempotency_key_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_reindex_jobs_idempotency_key_hash_sha256_hex")),
        sa.CheckConstraint("request_payload_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_reindex_jobs_request_payload_hash_sha256_hex")),
        sa.CheckConstraint("scope_type IN ('documents', 'organization')", name=op.f("ck_reindex_jobs_scope_type_allowed")),
        sa.CheckConstraint("cardinality(targets) > 0", name=op.f("ck_reindex_jobs_targets_not_empty")),
        sa.CheckConstraint("targets <@ ARRAY['chunks', 'embeddings', 'full_text', 'relationships']::text[]", name=op.f("ck_reindex_jobs_targets_allowed")),
        sa.CheckConstraint("text_array_has_no_duplicates(targets)", name=op.f("ck_reindex_jobs_targets_unique")),
        sa.CheckConstraint("requested_document_count >= 0", name=op.f("ck_reindex_jobs_requested_document_count_non_negative")),
        sa.CheckConstraint("status IN ('queued', 'running', 'completed', 'completed_with_errors', 'failed')", name=op.f("ck_reindex_jobs_status_allowed")),
        sa.CheckConstraint("job_error_code IS NULL OR btrim(job_error_code) <> ''", name=op.f("ck_reindex_jobs_job_error_code_not_blank_when_present")),
        sa.CheckConstraint("job_error_message IS NULL OR btrim(job_error_message) <> ''", name=op.f("ck_reindex_jobs_job_error_message_not_blank_when_present")),
        sa.CheckConstraint("(status = 'queued' AND started_at IS NULL AND completed_at IS NULL) OR (status = 'running' AND started_at IS NOT NULL AND completed_at IS NULL) OR (status IN ('completed', 'completed_with_errors', 'failed') AND started_at IS NOT NULL AND completed_at IS NOT NULL)", name=op.f("ck_reindex_jobs_status_timestamps_consistent")),
        sa.CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name=op.f("ck_reindex_jobs_completion_not_before_start")),
        sa.ForeignKeyConstraint(["organization_id", "submitted_by_principal_id"], ["principals.organization_id", "principals.id"], name="fk_reindex_jobs_submitted_by_principal", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "configuration_id"], ["derived_configurations.organization_id", "derived_configurations.id"], name="fk_reindex_jobs_configuration", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reindex_jobs")),
        sa.UniqueConstraint("organization_id", "id", name=op.f("uq_reindex_jobs_organization_id_id")),
        sa.UniqueConstraint("organization_id", "submitted_by_principal_id", "idempotency_key_hash", name=op.f("uq_reindex_jobs_organization_id_submitted_by_principal_id_idempotency_key_hash")),
    )
    op.create_index("ix_reindex_jobs_status_submitted_at", "reindex_jobs", ["status", "submitted_at"])
    op.create_table(
        "reindex_job_items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("reindex_job_id", sa.UUID(), nullable=False),
        sa.Column("document_pk", sa.UUID(), nullable=False),
        sa.Column("source_document_version_id", sa.UUID(), nullable=False),
        sa.Column("output_derived_set_id", sa.UUID(), nullable=True),
        sa.Column("processing_status", sa.Text(), nullable=False),
        sa.Column("outcome", sa.Text(), nullable=True),
        sa.Column("skip_code", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("processing_status IN ('queued', 'running', 'succeeded', 'failed')", name=op.f("ck_reindex_job_items_processing_status_allowed")),
        sa.CheckConstraint("outcome IS NULL OR outcome IN ('rebuilt', 'skipped', 'failed')", name=op.f("ck_reindex_job_items_outcome_allowed")),
        sa.CheckConstraint("skip_code IS NULL OR btrim(skip_code) <> ''", name=op.f("ck_reindex_job_items_skip_code_not_blank_when_present")),
        sa.CheckConstraint("error_code IS NULL OR btrim(error_code) <> ''", name=op.f("ck_reindex_job_items_error_code_not_blank_when_present")),
        sa.CheckConstraint("error_message IS NULL OR btrim(error_message) <> ''", name=op.f("ck_reindex_job_items_error_message_not_blank_when_present")),
        sa.CheckConstraint("(processing_status IN ('queued', 'running') AND outcome IS NULL AND completed_at IS NULL) OR (processing_status = 'succeeded' AND outcome = 'rebuilt' AND output_derived_set_id IS NOT NULL AND completed_at IS NOT NULL) OR (processing_status = 'succeeded' AND outcome = 'skipped' AND skip_code IS NOT NULL AND output_derived_set_id IS NULL AND completed_at IS NOT NULL) OR (processing_status = 'failed' AND outcome = 'failed' AND error_code IS NOT NULL AND completed_at IS NOT NULL)", name=op.f("ck_reindex_job_items_processing_state_consistent")),
        sa.ForeignKeyConstraint(["organization_id", "reindex_job_id"], ["reindex_jobs.organization_id", "reindex_jobs.id"], name="fk_reindex_job_items_job", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "document_pk"], ["documents.organization_id", "documents.id"], name="fk_reindex_job_items_document", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "document_pk", "source_document_version_id"], ["document_versions.organization_id", "document_versions.document_pk", "document_versions.id"], name="fk_reindex_job_items_source_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "source_document_version_id", "output_derived_set_id"], ["derived_sets.organization_id", "derived_sets.document_version_id", "derived_sets.id"], name="fk_reindex_job_items_output_derived_set", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reindex_job_items")),
        sa.UniqueConstraint("organization_id", "id", name=op.f("uq_reindex_job_items_organization_id_id")),
        sa.UniqueConstraint("organization_id", "reindex_job_id", "document_pk", name=op.f("uq_reindex_job_items_organization_id_reindex_job_id_document_pk")),
    )
    op.create_index("ix_reindex_job_items_job_processing_status", "reindex_job_items", ["reindex_job_id", "processing_status"])
    op.create_table(
        "reindex_job_item_attempts",
        sa.Column("id", sa.UUID(), nullable=False), sa.Column("organization_id", sa.UUID(), nullable=False), sa.Column("reindex_job_item_id", sa.UUID(), nullable=False), sa.Column("attempt_number", sa.Integer(), nullable=False), sa.Column("status", sa.Text(), nullable=False), sa.Column("retryable", sa.Boolean(), nullable=False), sa.Column("error_code", sa.Text(), nullable=True), sa.Column("error_message", sa.Text(), nullable=True), sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("attempt_number > 0", name=op.f("ck_reindex_job_item_attempts_attempt_number_positive")), sa.CheckConstraint("status IN ('running', 'succeeded', 'failed')", name=op.f("ck_reindex_job_item_attempts_status_allowed")), sa.CheckConstraint("error_code IS NULL OR btrim(error_code) <> ''", name=op.f("ck_reindex_job_item_attempts_error_code_not_blank_when_present")), sa.CheckConstraint("error_message IS NULL OR btrim(error_message) <> ''", name=op.f("ck_reindex_job_item_attempts_error_message_not_blank_when_present")), sa.CheckConstraint("(status = 'running' AND completed_at IS NULL AND error_code IS NULL AND error_message IS NULL) OR (status = 'succeeded' AND completed_at IS NOT NULL AND retryable = false AND error_code IS NULL AND error_message IS NULL) OR (status = 'failed' AND completed_at IS NOT NULL AND error_code IS NOT NULL)", name=op.f("ck_reindex_job_item_attempts_attempt_state_consistent")), sa.CheckConstraint("completed_at IS NULL OR completed_at >= started_at", name=op.f("ck_reindex_job_item_attempts_completion_not_before_start")),
        sa.ForeignKeyConstraint(["organization_id", "reindex_job_item_id"], ["reindex_job_items.organization_id", "reindex_job_items.id"], name="fk_reindex_job_item_attempts_item", ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id", name=op.f("pk_reindex_job_item_attempts")), sa.UniqueConstraint("organization_id", "reindex_job_item_id", "attempt_number", name=op.f("uq_reindex_job_item_attempts_organization_id_reindex_job_item_id_attempt_number")),
    )
    op.drop_constraint(op.f("ck_audit_events_reindex_job_deferred_null"), "audit_events", type_="check")
    op.create_foreign_key("fk_audit_events_reindex_job", "audit_events", "reindex_jobs", ["organization_id", "reindex_job_id"], ["organization_id", "id"], ondelete="RESTRICT")
    op.execute("GRANT SELECT, INSERT, UPDATE ON TABLE reindex_jobs, reindex_job_items, reindex_job_item_attempts TO opsbrain_app")
    op.execute("REVOKE DELETE ON TABLE reindex_jobs, reindex_job_items, reindex_job_item_attempts FROM opsbrain_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT, INSERT, UPDATE ON TABLE reindex_jobs, reindex_job_items, reindex_job_item_attempts FROM opsbrain_app")
    op.drop_constraint("fk_audit_events_reindex_job", "audit_events", type_="foreignkey")
    op.create_check_constraint(op.f("ck_audit_events_reindex_job_deferred_null"), "audit_events", "reindex_job_id IS NULL")
    op.drop_table("reindex_job_item_attempts")
    op.drop_index("ix_reindex_job_items_job_processing_status", table_name="reindex_job_items")
    op.drop_table("reindex_job_items")
    op.drop_index("ix_reindex_jobs_status_submitted_at", table_name="reindex_jobs")
    op.drop_table("reindex_jobs")
    op.drop_constraint("uq_derived_sets_organization_id_document_version_id_id", "derived_sets", type_="unique")
    op.execute("DROP FUNCTION text_array_has_no_duplicates(text[])")

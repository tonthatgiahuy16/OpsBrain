"""create document review decisions

Revision ID: c7f1a2b9d401
Revises: dd809e06c821
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7f1a2b9d401"
down_revision: Union[str, Sequence[str], None] = "dd809e06c821"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_review_decisions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("idempotency_key_hash", sa.Text(), nullable=False),
        sa.Column("request_payload_hash", sa.Text(), nullable=False),
        sa.Column("document_pk", sa.UUID(), nullable=False),
        sa.Column("reviewed_version_id", sa.UUID(), nullable=False),
        sa.Column("resulting_version_id", sa.UUID(), nullable=False),
        sa.Column("decision", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reviewer_principal_id", sa.UUID(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("decision IN ('approve', 'reject')", name=op.f("ck_document_review_decisions_decision_allowed")),
        sa.CheckConstraint("btrim(request_id) <> ''", name=op.f("ck_document_review_decisions_request_id_not_blank")),
        sa.CheckConstraint("btrim(reason) <> ''", name=op.f("ck_document_review_decisions_reason_not_blank")),
        sa.CheckConstraint("idempotency_key_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_document_review_decisions_idempotency_key_hash_sha256_hex")),
        sa.CheckConstraint("request_payload_hash ~ '^[0-9a-f]{64}$'", name=op.f("ck_document_review_decisions_request_payload_hash_sha256_hex")),
        sa.ForeignKeyConstraint(["organization_id", "document_pk"], ["documents.organization_id", "documents.id"], name="fk_document_review_decisions_document", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "document_pk", "reviewed_version_id"], ["document_versions.organization_id", "document_versions.document_pk", "document_versions.id"], name="fk_document_review_decisions_reviewed_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "document_pk", "resulting_version_id"], ["document_versions.organization_id", "document_versions.document_pk", "document_versions.id"], name="fk_document_review_decisions_resulting_version", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "reviewer_principal_id"], ["principals.organization_id", "principals.id"], name="fk_document_review_decisions_reviewer", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_document_review_decisions"),
        sa.UniqueConstraint("organization_id", "id", name="uq_document_review_decisions_organization_id_id"),
        sa.UniqueConstraint("organization_id", "reviewer_principal_id", "idempotency_key_hash", name="uq_document_review_decisions_idempotency"),
        sa.UniqueConstraint("organization_id", "resulting_version_id", name="uq_document_review_decisions_resulting_version"),
    )
    op.create_index("ix_document_review_decisions_org_document", "document_review_decisions", ["organization_id", "document_pk", "decided_at"])

    op.drop_constraint(
        op.f("ck_audit_events_deferred_refs_null"),
        "audit_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_reindex_job_deferred_null"),
        "audit_events",
        "reindex_job_id IS NULL",
    )
    op.create_foreign_key(
        "fk_audit_events_review_decision",
        "audit_events",
        "document_review_decisions",
        ["organization_id", "review_decision_id"],
        ["organization_id", "id"],
        ondelete="RESTRICT",
    )

    op.execute("""
        CREATE FUNCTION enforce_document_review_decision() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            reviewed_row document_versions%ROWTYPE;
            resulting_row document_versions%ROWTYPE;
        BEGIN
            SELECT * INTO reviewed_row FROM document_versions
            WHERE organization_id = NEW.organization_id
              AND document_pk = NEW.document_pk
              AND id = NEW.reviewed_version_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'reviewed document version does not exist';
            END IF;

            SELECT * INTO resulting_row FROM document_versions
            WHERE organization_id = NEW.organization_id
              AND document_pk = NEW.document_pk
              AND id = NEW.resulting_version_id;
            IF NOT FOUND THEN
                RAISE EXCEPTION 'resulting document version does not exist';
            END IF;

            IF reviewed_row.review_state <> 'in_review' THEN
                RAISE EXCEPTION 'reviewed document version is not in review';
            END IF;
            IF resulting_row.parent_version_id IS DISTINCT FROM reviewed_row.id
               OR resulting_row.change_kind <> 'review_transition' THEN
                RAISE EXCEPTION 'invalid document review decision lineage';
            END IF;
            IF reviewed_row.created_by_principal_id = NEW.reviewer_principal_id THEN
                RAISE EXCEPTION 'document author cannot review own version';
            END IF;
            IF NEW.decision = 'approve'
               AND resulting_row.review_state <> 'approved' THEN
                RAISE EXCEPTION 'approve decision requires approved version';
            END IF;
            IF NEW.decision = 'reject'
               AND resulting_row.review_state <> 'rejected' THEN
                RAISE EXCEPTION 'reject decision requires rejected version';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_document_review_decisions_validate
        BEFORE INSERT ON document_review_decisions
        FOR EACH ROW EXECUTE FUNCTION enforce_document_review_decision();
    """)
    op.execute("""
        CREATE FUNCTION prevent_document_review_decision_mutation() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'document_review_decisions are immutable';
        END;
        $$;
    """)
    op.execute("""
        CREATE TRIGGER trg_document_review_decisions_immutable
        BEFORE UPDATE OR DELETE ON document_review_decisions
        FOR EACH ROW EXECUTE FUNCTION prevent_document_review_decision_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_document_review_decisions_immutable ON document_review_decisions")
    op.execute("DROP FUNCTION IF EXISTS prevent_document_review_decision_mutation()")
    op.execute("DROP TRIGGER IF EXISTS trg_document_review_decisions_validate ON document_review_decisions")
    op.execute("DROP FUNCTION IF EXISTS enforce_document_review_decision()")
    op.drop_constraint("fk_audit_events_review_decision", "audit_events", type_="foreignkey")
    op.drop_constraint(
        op.f("ck_audit_events_reindex_job_deferred_null"),
        "audit_events",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_audit_events_deferred_refs_null"),
        "audit_events",
        "reindex_job_id IS NULL AND review_decision_id IS NULL",
    )
    op.drop_index("ix_document_review_decisions_org_document", table_name="document_review_decisions")
    op.drop_table("document_review_decisions")

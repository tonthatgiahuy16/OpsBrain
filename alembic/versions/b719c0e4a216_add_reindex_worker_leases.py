"""add reindex worker leases

Revision ID: b719c0e4a216
Revises: 9c4e7a1d2b3f
Create Date: 2026-11-04
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b719c0e4a216"
down_revision: Union[str, Sequence[str], None] = "9c4e7a1d2b3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("reindex_job_items", sa.Column("active_attempt_id", sa.UUID(), nullable=True))
    op.add_column("reindex_job_items", sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("reindex_job_item_attempts", sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("""
        UPDATE reindex_job_item_attempts
        SET lease_expires_at = started_at + interval '60 seconds'
        WHERE lease_expires_at IS NULL
    """)
    op.alter_column("reindex_job_item_attempts", "lease_expires_at", nullable=False)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM reindex_job_item_attempts
                WHERE status = 'running'
                GROUP BY organization_id, reindex_job_item_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION 'cannot migrate multiple running reindex attempts for one item';
            END IF;
        END $$;
    """)
    op.execute("""
        UPDATE reindex_job_items AS item
        SET active_attempt_id = attempt.id
        FROM reindex_job_item_attempts AS attempt
        WHERE attempt.organization_id = item.organization_id
          AND attempt.reindex_job_item_id = item.id
          AND attempt.status = 'running'
    """)
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM reindex_job_items AS item
                WHERE item.processing_status = 'running'
                  AND item.active_attempt_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot migrate a running reindex item without a running attempt';
            END IF;
        END $$;
    """)
    op.create_unique_constraint("uq_reindex_attempt_item_ref", "reindex_job_item_attempts", ["organization_id", "reindex_job_item_id", "id"])
    op.create_index("uq_reindex_job_item_attempts_running_item", "reindex_job_item_attempts", ["organization_id", "reindex_job_item_id"], unique=True, postgresql_where=sa.text("status = 'running'"))
    op.create_foreign_key("fk_reindex_job_items_active_attempt", "reindex_job_items", "reindex_job_item_attempts", ["organization_id", "id", "active_attempt_id"], ["organization_id", "reindex_job_item_id", "id"], ondelete="RESTRICT")
    op.create_check_constraint("ck_reindex_job_items_next_retry_requires_queued", "reindex_job_items", "next_retry_at IS NULL OR processing_status = 'queued'")
    op.create_check_constraint("ck_reindex_job_items_active_attempt_matches_processing_status", "reindex_job_items", "(processing_status = 'running' AND active_attempt_id IS NOT NULL) OR (processing_status IN ('queued', 'succeeded', 'failed') AND active_attempt_id IS NULL)")
    op.create_check_constraint("ck_reindex_job_item_attempts_lease_after_start", "reindex_job_item_attempts", "lease_expires_at > started_at")
    op.execute("""
        CREATE FUNCTION enforce_reindex_active_attempt() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
            checked_org uuid;
            checked_item uuid;
            item_status text;
            active_attempt uuid;
            running_attempt uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                checked_org := OLD.organization_id;
                IF TG_TABLE_NAME = 'reindex_job_items' THEN
                    checked_item := OLD.id;
                ELSE
                    checked_item := OLD.reindex_job_item_id;
                END IF;
            ELSE
                checked_org := NEW.organization_id;
                IF TG_TABLE_NAME = 'reindex_job_items' THEN
                    checked_item := NEW.id;
                ELSE
                    checked_item := NEW.reindex_job_item_id;
                END IF;
            END IF;
            SELECT processing_status, active_attempt_id
            INTO item_status, active_attempt
            FROM reindex_job_items
            WHERE organization_id = checked_org AND id = checked_item;
            IF NOT FOUND THEN RETURN NULL; END IF;
            SELECT id INTO running_attempt
            FROM reindex_job_item_attempts
            WHERE organization_id = checked_org
              AND reindex_job_item_id = checked_item
              AND status = 'running';
            IF item_status = 'running' THEN
                IF active_attempt IS NULL OR active_attempt IS DISTINCT FROM running_attempt THEN
                    RAISE EXCEPTION 'running reindex item must reference its running attempt';
                END IF;
            ELSIF active_attempt IS NOT NULL OR running_attempt IS NOT NULL THEN
                RAISE EXCEPTION 'non-running reindex item cannot retain a running attempt';
            END IF;
            RETURN NULL;
        END;
        $$;
    """)
    for table_name in ("reindex_job_items", "reindex_job_item_attempts"):
        op.execute(f"""
            CREATE CONSTRAINT TRIGGER trg_{table_name}_active_attempt
            AFTER INSERT OR UPDATE OR DELETE ON {table_name}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION enforce_reindex_active_attempt();
        """)


def downgrade() -> None:
    for table_name in ("reindex_job_item_attempts", "reindex_job_items"):
        op.execute(f"DROP TRIGGER IF EXISTS trg_{table_name}_active_attempt ON {table_name}")
    op.execute("DROP FUNCTION IF EXISTS enforce_reindex_active_attempt()")
    op.drop_constraint("ck_reindex_job_item_attempts_lease_after_start", "reindex_job_item_attempts", type_="check")
    op.drop_constraint("ck_reindex_job_items_active_attempt_matches_processing_status", "reindex_job_items", type_="check")
    op.drop_constraint("ck_reindex_job_items_next_retry_requires_queued", "reindex_job_items", type_="check")
    op.drop_constraint("fk_reindex_job_items_active_attempt", "reindex_job_items", type_="foreignkey")
    op.drop_index("uq_reindex_job_item_attempts_running_item", table_name="reindex_job_item_attempts")
    op.drop_constraint("uq_reindex_attempt_item_ref", "reindex_job_item_attempts", type_="unique")
    op.drop_column("reindex_job_item_attempts", "lease_expires_at")
    op.drop_column("reindex_job_items", "next_retry_at")
    op.drop_column("reindex_job_items", "active_attempt_id")

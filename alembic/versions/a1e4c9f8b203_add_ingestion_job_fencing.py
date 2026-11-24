"""add ingestion job fencing token

Revision ID: a1e4c9f8b203
Revises: f3a9d2c7b518
Create Date: 2026-11-24
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a1e4c9f8b203"
down_revision: Union[str, Sequence[str], None] = "f3a9d2c7b518"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ingestion_jobs",
        sa.Column("active_attempt_id", sa.UUID(), nullable=True),
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM ingestion_job_attempts
                WHERE status = 'running'
                GROUP BY organization_id, ingestion_job_id
                HAVING count(*) > 1
            ) THEN
                RAISE EXCEPTION
                    'cannot migrate multiple running ingestion attempts for one job';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        UPDATE ingestion_jobs AS job
        SET active_attempt_id = attempt.id
        FROM ingestion_job_attempts AS attempt
        WHERE attempt.organization_id = job.organization_id
          AND attempt.ingestion_job_id = job.id
          AND attempt.status = 'running'
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM ingestion_jobs AS job
                WHERE job.status = 'running'
                  AND job.active_attempt_id IS NULL
            ) THEN
                RAISE EXCEPTION
                    'cannot migrate a running ingestion job without a running attempt';
            END IF;
        END $$;
        """
    )
    op.create_unique_constraint(
        "uq_ingestion_attempt_job_ref",
        "ingestion_job_attempts",
        ["organization_id", "ingestion_job_id", "id"],
    )
    op.create_index(
        "uq_ingestion_job_attempts_running_job",
        "ingestion_job_attempts",
        ["organization_id", "ingestion_job_id"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_foreign_key(
        "fk_ingestion_jobs_active_attempt",
        "ingestion_jobs",
        "ingestion_job_attempts",
        ["organization_id", "id", "active_attempt_id"],
        ["organization_id", "ingestion_job_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_ingestion_jobs_active_attempt_matches_status",
        "ingestion_jobs",
        "(status = 'running' AND active_attempt_id IS NOT NULL) "
        "OR (status IN ('queued', 'completed', 'completed_with_errors', 'failed') "
        "AND active_attempt_id IS NULL)",
    )
    op.execute(
        """
        CREATE FUNCTION enforce_ingestion_active_attempt()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            checked_org uuid;
            checked_job uuid;
            job_status text;
            active_attempt uuid;
            running_attempt uuid;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                checked_org := OLD.organization_id;
                IF TG_TABLE_NAME = 'ingestion_jobs' THEN
                    checked_job := OLD.id;
                ELSE
                    checked_job := OLD.ingestion_job_id;
                END IF;
            ELSE
                checked_org := NEW.organization_id;
                IF TG_TABLE_NAME = 'ingestion_jobs' THEN
                    checked_job := NEW.id;
                ELSE
                    checked_job := NEW.ingestion_job_id;
                END IF;
            END IF;

            SELECT status, active_attempt_id
            INTO job_status, active_attempt
            FROM ingestion_jobs
            WHERE organization_id = checked_org
              AND id = checked_job;
            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            SELECT id
            INTO running_attempt
            FROM ingestion_job_attempts
            WHERE organization_id = checked_org
              AND ingestion_job_id = checked_job
              AND status = 'running';

            IF job_status = 'running' THEN
                IF active_attempt IS NULL OR active_attempt IS DISTINCT FROM running_attempt THEN
                    RAISE EXCEPTION
                        'running ingestion job must reference its running attempt';
                END IF;
            ELSIF active_attempt IS NOT NULL OR running_attempt IS NOT NULL THEN
                RAISE EXCEPTION
                    'non-running ingestion job cannot retain a running attempt';
            END IF;

            RETURN NULL;
        END;
        $$;
        """
    )
    for table_name in ("ingestion_jobs", "ingestion_job_attempts"):
        op.execute(
            f"""
            CREATE CONSTRAINT TRIGGER trg_{table_name}_active_attempt
            AFTER INSERT OR UPDATE OR DELETE ON {table_name}
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW
            EXECUTE FUNCTION enforce_ingestion_active_attempt();
            """
        )


def downgrade() -> None:
    for table_name in ("ingestion_job_attempts", "ingestion_jobs"):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_active_attempt ON {table_name}"
        )
    op.execute("DROP FUNCTION IF EXISTS enforce_ingestion_active_attempt()")
    op.drop_constraint(
        "ck_ingestion_jobs_active_attempt_matches_status",
        "ingestion_jobs",
        type_="check",
    )
    op.drop_constraint(
        "fk_ingestion_jobs_active_attempt",
        "ingestion_jobs",
        type_="foreignkey",
    )
    op.drop_index(
        "uq_ingestion_job_attempts_running_job",
        table_name="ingestion_job_attempts",
    )
    op.drop_constraint(
        "uq_ingestion_attempt_job_ref",
        "ingestion_job_attempts",
        type_="unique",
    )
    op.drop_column("ingestion_jobs", "active_attempt_id")

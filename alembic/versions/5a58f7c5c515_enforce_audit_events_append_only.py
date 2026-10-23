"""enforce audit events append only

Revision ID: 5a58f7c5c515
Revises: a827ee2cd02b
Create Date: 2026-10-23 17:39:55.759046

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5a58f7c5c515'
down_revision: Union[str, Sequence[str], None] = 'a827ee2cd02b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_audit_events_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'audit_events are append-only';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_audit_events_prevent_mutation
        BEFORE UPDATE OR DELETE ON audit_events
        FOR EACH ROW
        EXECUTE FUNCTION prevent_audit_events_mutation();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE audit_events FROM opsbrain_app"
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_audit_events_prevent_mutation
        ON audit_events;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS prevent_audit_events_mutation();
        """
    )
    op.execute(
        "GRANT UPDATE, DELETE ON TABLE audit_events TO opsbrain_app"
    )

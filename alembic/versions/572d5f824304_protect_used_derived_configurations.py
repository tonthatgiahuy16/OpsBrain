"""protect used derived configurations

Revision ID: 572d5f824304
Revises: f22b15a83fb2
Create Date: 2026-10-26 19:03:28.652801

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '572d5f824304'
down_revision: Union[str, Sequence[str], None] = 'f22b15a83fb2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION lock_derived_set_configuration()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM 1
            FROM derived_configurations
            WHERE organization_id = NEW.organization_id
              AND id = NEW.configuration_id
            FOR UPDATE;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_derived_sets_lock_configuration
        BEFORE INSERT OR UPDATE OF organization_id, configuration_id
        ON derived_sets
        FOR EACH ROW
        EXECUTE FUNCTION lock_derived_set_configuration();
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_used_configuration_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM derived_sets
                WHERE organization_id = OLD.organization_id
                  AND configuration_id = OLD.id
            ) THEN
                RAISE EXCEPTION 'used derived configuration is immutable';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_derived_configurations_prevent_used_update
        BEFORE UPDATE ON derived_configurations
        FOR EACH ROW
        EXECUTE FUNCTION prevent_used_configuration_update();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_derived_configurations_prevent_used_update
        ON derived_configurations;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_used_configuration_update();")
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_derived_sets_lock_configuration
        ON derived_sets;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS lock_derived_set_configuration();")

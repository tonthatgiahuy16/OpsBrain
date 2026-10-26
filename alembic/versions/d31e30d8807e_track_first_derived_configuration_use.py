"""track first derived configuration use

Revision ID: d31e30d8807e
Revises: 572d5f824304
Create Date: 2026-10-26 19:13:06.449339

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd31e30d8807e'
down_revision: Union[str, Sequence[str], None] = '572d5f824304'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "derived_configurations",
        sa.Column("first_used_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Gỡ trigger cũ để có thể ghi dấu mốc cho các set đã tồn tại.
    op.execute(
        """
        DROP TRIGGER trg_derived_configurations_prevent_used_update
        ON derived_configurations;
        """
    )

    op.execute(
        """
        UPDATE derived_configurations AS configuration
        SET first_used_at = used.first_used_at
        FROM (
            SELECT
                organization_id,
                configuration_id,
                MIN(created_at) AS first_used_at
            FROM derived_sets
            GROUP BY organization_id, configuration_id
        ) AS used
        WHERE configuration.organization_id = used.organization_id
          AND configuration.id = used.configuration_id;
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_used_configuration_update()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF OLD.first_used_at IS NOT NULL THEN
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

    op.execute(
        """
        CREATE OR REPLACE FUNCTION lock_derived_set_configuration()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            PERFORM 1
            FROM derived_configurations
            WHERE organization_id = NEW.organization_id
              AND id = NEW.configuration_id
            FOR UPDATE;

            UPDATE derived_configurations
            SET first_used_at = now()
            WHERE organization_id = NEW.organization_id
              AND id = NEW.configuration_id
              AND first_used_at IS NULL;

            RETURN NEW;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION lock_derived_set_configuration()
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
        CREATE OR REPLACE FUNCTION prevent_used_configuration_update()
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
    op.drop_column("derived_configurations", "first_used_at")

"""protect chunked derived set provenance

Revision ID: dd809e06c821
Revises: 473da472b1f7
Create Date: 2026-11-03 20:27:16.694635

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'dd809e06c821'
down_revision: Union[str, Sequence[str], None] = '473da472b1f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_chunked_derived_set_provenance_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.organization_id IS DISTINCT FROM OLD.organization_id
               OR NEW.document_pk IS DISTINCT FROM OLD.document_pk
               OR NEW.document_version_id
                  IS DISTINCT FROM OLD.document_version_id
               OR NEW.configuration_id IS DISTINCT FROM OLD.configuration_id THEN
                IF EXISTS (
                    SELECT 1
                    FROM chunks
                    WHERE organization_id = OLD.organization_id
                      AND derived_set_id = OLD.id
                ) THEN
                    RAISE EXCEPTION
                        'derived set provenance is immutable after chunks exist';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_derived_sets_prevent_chunked_provenance_mutation
        BEFORE UPDATE OF
            organization_id,
            document_pk,
            document_version_id,
            configuration_id
        ON derived_sets
        FOR EACH ROW
        EXECUTE FUNCTION prevent_chunked_derived_set_provenance_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS
            trg_derived_sets_prevent_chunked_provenance_mutation
        ON derived_sets;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS
            prevent_chunked_derived_set_provenance_mutation();
        """
    )

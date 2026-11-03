"""enforce chunk provenance

Revision ID: 8efd90e3d04a
Revises: 3e8c9a151cf5
Create Date: 2026-11-03 20:12:31.003770

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8efd90e3d04a'
down_revision: Union[str, Sequence[str], None] = '3e8c9a151cf5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_chunk_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_document_pk uuid;
            expected_document_version_id uuid;
            expected_chunking_version text;
        BEGIN
            SELECT
                derived_set.document_pk,
                derived_set.document_version_id,
                configuration.chunking_version
            INTO
                expected_document_pk,
                expected_document_version_id,
                expected_chunking_version
            FROM derived_sets AS derived_set
            JOIN derived_configurations AS configuration
              ON configuration.organization_id = derived_set.organization_id
             AND configuration.id = derived_set.configuration_id
            WHERE derived_set.organization_id = NEW.organization_id
              AND derived_set.id = NEW.derived_set_id
            FOR UPDATE OF derived_set, configuration;

            IF NOT FOUND THEN
                RETURN NEW;
            END IF;

            IF NEW.document_pk IS DISTINCT FROM expected_document_pk
               OR NEW.document_version_id
                  IS DISTINCT FROM expected_document_version_id THEN
                RAISE EXCEPTION 'chunk does not match derived set source';
            END IF;

            IF NEW.chunking_version
               IS DISTINCT FROM expected_chunking_version THEN
                RAISE EXCEPTION
                    'chunking version does not match derived configuration';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_chunks_enforce_provenance
        BEFORE INSERT OR UPDATE OF
            organization_id,
            derived_set_id,
            document_pk,
            document_version_id,
            chunking_version
        ON chunks
        FOR EACH ROW
        EXECUTE FUNCTION enforce_chunk_provenance();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_chunks_enforce_provenance
        ON chunks;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS enforce_chunk_provenance();
        """
    )

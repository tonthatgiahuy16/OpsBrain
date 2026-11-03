"""prevent chunk mutation

Revision ID: 473da472b1f7
Revises: 8efd90e3d04a
Create Date: 2026-11-03 20:16:13.896125

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '473da472b1f7'
down_revision: Union[str, Sequence[str], None] = '8efd90e3d04a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_chunk_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'chunks are immutable';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_chunks_prevent_mutation
        BEFORE UPDATE ON chunks
        FOR EACH ROW
        EXECUTE FUNCTION prevent_chunk_mutation();
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_chunks_prevent_mutation
        ON chunks;
        """
    )
    op.execute(
        """
        DROP FUNCTION IF EXISTS prevent_chunk_mutation();
        """
    )

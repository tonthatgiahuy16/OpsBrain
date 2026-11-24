"""enforce document version, chunk and document immutability

Revision ID: f3a9d2c7b518
Revises: d8a4f6b2c1e7
Create Date: 2026-11-24

Storage contract §6 states that a committed ``document_version`` is immutable:
corrections create a new version. ``chunks`` are immutable derived data, and a
``documents`` row is retired through a tombstone version, never hard-deleted.
Until now those rules were enforced only by application code plus a
``BEFORE UPDATE`` chunk trigger, while ``opsbrain_app`` still held
``UPDATE``/``DELETE`` on the source tables.

This migration makes the invariant a database property for the request path:

* ``opsbrain_app`` loses ``UPDATE``/``DELETE`` on ``document_versions`` and
  ``chunks``, and ``DELETE`` on ``documents``;
* ``DELETE`` is additionally rejected by trigger for every role, including the
  table owner, because no code path may hard-delete history;
* ``chunks`` keep their existing all-role ``UPDATE`` trigger and gain the same
  guard for ``DELETE``.

``UPDATE`` on ``document_versions`` stays available to the migration/owner role
only, as a controlled repair hatch. The API connection never uses that role.
Downgrade restores exactly the privileges the bootstrap granted.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f3a9d2c7b518"
down_revision: Union[str, Sequence[str], None] = "d8a4f6b2c1e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION prevent_document_version_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'document versions are immutable; append a new version instead';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_versions_prevent_delete
        BEFORE DELETE ON document_versions
        FOR EACH ROW
        EXECUTE FUNCTION prevent_document_version_delete();
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_document_hard_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'documents are retired with a tombstone, not hard deleted';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_documents_prevent_hard_delete
        BEFORE DELETE ON documents
        FOR EACH ROW
        EXECUTE FUNCTION prevent_document_hard_delete();
        """
    )
    # ``chunks`` already rejects UPDATE through a trigger; remove the privilege
    # as well and extend the guard to DELETE so both layers agree.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION prevent_chunk_mutation()
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
        CREATE TRIGGER trg_chunks_prevent_delete
        BEFORE DELETE ON chunks
        FOR EACH ROW
        EXECUTE FUNCTION prevent_chunk_mutation();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE document_versions FROM opsbrain_app"
    )
    op.execute("REVOKE DELETE ON TABLE documents FROM opsbrain_app")
    op.execute("REVOKE UPDATE, DELETE ON TABLE chunks FROM opsbrain_app")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON TABLE chunks TO opsbrain_app")
    op.execute("GRANT DELETE ON TABLE documents TO opsbrain_app")
    op.execute("GRANT UPDATE, DELETE ON TABLE document_versions TO opsbrain_app")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_chunks_prevent_delete ON chunks"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_documents_prevent_hard_delete ON documents"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_document_hard_delete()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_document_versions_prevent_delete "
        "ON document_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_document_version_delete()")

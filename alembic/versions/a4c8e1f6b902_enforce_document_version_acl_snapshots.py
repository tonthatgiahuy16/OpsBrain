"""enforce document version ACL snapshots

Revision ID: a4c8e1f6b902
Revises: f1a9c3d7e5b2
Create Date: 2026-11-18
"""

from typing import Sequence, Union

from alembic import op


revision: str = "a4c8e1f6b902"
down_revision: Union[str, Sequence[str], None] = "f1a9c3d7e5b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION enforce_document_version_acl_snapshot()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            checked_organization_id uuid;
            checked_version_id uuid;
            snapshot_scope text;
        BEGIN
            IF TG_TABLE_NAME = 'document_versions' THEN
                checked_organization_id := NEW.organization_id;
                checked_version_id := NEW.id;
            ELSIF TG_OP = 'DELETE' THEN
                checked_organization_id := OLD.organization_id;
                checked_version_id := OLD.document_version_id;
            ELSE
                checked_organization_id := NEW.organization_id;
                checked_version_id := NEW.document_version_id;
            END IF;

            SELECT access_scope
            INTO snapshot_scope
            FROM document_versions
            WHERE organization_id = checked_organization_id
              AND id = checked_version_id;

            IF NOT FOUND THEN
                RETURN NULL;
            END IF;

            IF snapshot_scope = 'organization' AND EXISTS (
                SELECT 1
                FROM document_version_acl_entries
                WHERE organization_id = checked_organization_id
                  AND document_version_id = checked_version_id
            ) THEN
                RAISE EXCEPTION
                    'organization-scoped document version cannot have ACL entries';
            END IF;

            IF snapshot_scope = 'restricted' AND NOT EXISTS (
                SELECT 1
                FROM document_version_acl_entries
                WHERE organization_id = checked_organization_id
                  AND document_version_id = checked_version_id
            ) THEN
                RAISE EXCEPTION
                    'restricted document version requires ACL entry';
            END IF;

            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_document_versions_enforce_acl_snapshot
        AFTER INSERT OR UPDATE OF access_scope ON document_versions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION enforce_document_version_acl_snapshot();
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_document_version_acl_entries_enforce_snapshot
        AFTER INSERT OR DELETE ON document_version_acl_entries
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        EXECUTE FUNCTION enforce_document_version_acl_snapshot();
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_document_version_acl_entry_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'document version ACL entries are immutable';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_document_version_acl_entries_immutable
        BEFORE UPDATE OR DELETE ON document_version_acl_entries
        FOR EACH ROW
        EXECUTE FUNCTION prevent_document_version_acl_entry_mutation();
        """
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE document_version_acl_entries FROM opsbrain_app"
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_document_version_acl_entries_immutable "
        "ON document_version_acl_entries"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_document_version_acl_entry_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_document_version_acl_entries_enforce_snapshot "
        "ON document_version_acl_entries"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_document_versions_enforce_acl_snapshot "
        "ON document_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_document_version_acl_snapshot()")
    op.execute(
        "GRANT UPDATE, DELETE ON TABLE document_version_acl_entries TO opsbrain_app"
    )

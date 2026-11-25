"""Expand organization RLS and enforce document ACLs in PostgreSQL.

Revision ID: e4f7a9c2d601
Revises: c2d8e7a4b901
Create Date: 2026-11-25
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e4f7a9c2d601"
down_revision: Union[str, Sequence[str], None] = "c2d8e7a4b901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ORG_ONLY_TABLES = (
    "teams",
    "roles",
    "principal_team_memberships",
    "principal_role_memberships",
    "role_capabilities",
    "derived_configurations",
    "derived_sets",
    "document_version_acl_entries",
    "document_review_decisions",
    "audit_events",
    "ingestion_jobs",
    "ingestion_job_items",
    "ingestion_job_attempts",
    "reindex_jobs",
    "reindex_job_items",
    "reindex_job_item_attempts",
)

_ACL_TABLES = ("documents", "document_versions", "chunks", "chunk_embeddings")


def _enable_org_policy(table_name: str) -> None:
    op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table_name}_organization_isolation
        ON public.{table_name}
        FOR ALL TO opsbrain_app
        USING (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
        )
        WITH CHECK (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
        )
        """
    )


def _drop_policy_if_exists(table_name: str, policy_name: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS {policy_name} ON public.{table_name}")


def _execute_statements(sql: str) -> None:
    """Execute simple DDL statements separately for asyncpg prepared calls."""
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    # Authentication must resolve its principal before an organization context
    # exists, so organizations/principals intentionally remain outside this
    # slice. The worker role has BYPASSRLS and is unchanged.
    for table_name in _ORG_ONLY_TABLES:
        _enable_org_policy(table_name)

    # Slice 1 used permissive organization-only policies for these tables.
    # Replace them with separate write and ACL-aware read policies: a generic
    # FOR ALL policy would also make SELECT permissive and defeat ACL RLS.
    for table_name in ("documents", "document_versions"):
        _drop_policy_if_exists(table_name, f"{table_name}_organization_isolation")

    _execute_statements(
        """
        CREATE FUNCTION public.app_can_read_document_version(
            requested_document_pk uuid,
            requested_document_version_id uuid
        )
        RETURNS boolean
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            WITH context AS (
                SELECT
                    NULLIF(current_setting('app.organization_id', true), '')::uuid AS organization_id,
                    NULLIF(current_setting('app.principal_id', true), '')::uuid AS principal_id
            )
            SELECT EXISTS (
                SELECT 1
                FROM public.document_versions AS version
                JOIN public.principals AS principal
                  ON principal.id = (SELECT principal_id FROM context)
                 AND principal.organization_id = version.organization_id
                 AND principal.status = 'active'
                JOIN public.organizations AS organization
                  ON organization.id = version.organization_id
                 AND organization.status = 'active'
                WHERE version.document_pk = requested_document_pk
                  AND version.id = requested_document_version_id
                  AND version.organization_id = (SELECT organization_id FROM context)
                  AND (
                    version.access_scope = 'organization'
                    OR (
                        version.access_scope = 'restricted'
                        AND EXISTS (
                            SELECT 1
                            FROM public.document_version_acl_entries AS entry
                            WHERE entry.organization_id = version.organization_id
                              AND entry.document_pk = version.document_pk
                              AND entry.document_version_id = version.id
                              AND (
                                entry.user_principal_id = (SELECT principal_id FROM context)
                                OR entry.team_id IN (
                                    SELECT membership.team_id
                                    FROM public.principal_team_memberships AS membership
                                    JOIN public.teams AS team
                                      ON team.id = membership.team_id
                                     AND team.organization_id = membership.organization_id
                                     AND team.status = 'active'
                                    WHERE membership.organization_id = version.organization_id
                                      AND membership.principal_id = (SELECT principal_id FROM context)
                                      AND membership.status = 'active'
                                )
                                OR entry.role_id IN (
                                    SELECT membership.role_id
                                    FROM public.principal_role_memberships AS membership
                                    JOIN public.roles AS role
                                      ON role.id = membership.role_id
                                     AND role.organization_id = membership.organization_id
                                     AND role.status = 'active'
                                    WHERE membership.organization_id = version.organization_id
                                      AND membership.principal_id = (SELECT principal_id FROM context)
                                      AND membership.status = 'active'
                                )
                              )
                        )
                    )
            )
            )
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.app_can_read_document_version(uuid, uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.app_can_read_document_version(uuid, uuid) TO opsbrain_app")

    for table_name in _ACL_TABLES:
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")

    _execute_statements(
        """
        CREATE POLICY documents_acl_select ON public.documents
        FOR SELECT TO opsbrain_app
        USING (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
            AND (
                -- INSERT ... RETURNING needs to read a newly-created document
                -- before its version is attached in this transaction.
                current_version_id IS NULL
                OR public.app_can_read_document_version(id, current_version_id)
            )
        );
        CREATE POLICY documents_org_insert ON public.documents
        FOR INSERT TO opsbrain_app
        WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);
        CREATE POLICY documents_org_update ON public.documents
        FOR UPDATE TO opsbrain_app
        USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid)
        WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);
        CREATE POLICY documents_org_delete ON public.documents
        FOR DELETE TO opsbrain_app
        USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

        CREATE POLICY document_versions_acl_select ON public.document_versions
        FOR SELECT TO opsbrain_app
        USING (
            public.app_can_read_document_version(document_pk, id)
            OR EXISTS (
                SELECT 1
                FROM public.documents AS document
                WHERE document.organization_id = document_versions.organization_id
                  AND document.id = document_versions.document_pk
                  AND document.current_version_id IS NULL
            )
        );
        CREATE POLICY document_versions_org_insert ON public.document_versions
        FOR INSERT TO opsbrain_app
        WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);
        CREATE POLICY document_versions_org_update ON public.document_versions
        FOR UPDATE TO opsbrain_app
        USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid)
        WITH CHECK (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);
        CREATE POLICY document_versions_org_delete ON public.document_versions
        FOR DELETE TO opsbrain_app
        USING (organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid);

        CREATE POLICY chunks_acl_select ON public.chunks
        FOR SELECT TO opsbrain_app
        USING (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
            AND public.app_can_read_document_version(document_pk, document_version_id)
        );
        CREATE POLICY chunks_org_write ON public.chunks
        FOR ALL TO opsbrain_app
        USING (false)
        WITH CHECK (false);

        CREATE POLICY chunk_embeddings_acl_select ON public.chunk_embeddings
        FOR SELECT TO opsbrain_app
        USING (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
            AND EXISTS (
                SELECT 1
                FROM public.chunks AS chunk
                WHERE chunk.organization_id = chunk_embeddings.organization_id
                  AND chunk.derived_set_id = chunk_embeddings.derived_set_id
                  AND chunk.id = chunk_embeddings.chunk_id
                  AND public.app_can_read_document_version(
                      chunk.document_pk,
                      chunk.document_version_id
                  )
            )
        );
        CREATE POLICY chunk_embeddings_org_write ON public.chunk_embeddings
        FOR ALL TO opsbrain_app
        USING (false)
        WITH CHECK (false);
        """
    )


def downgrade() -> None:
    for table_name in _ACL_TABLES:
        for policy_name in (
            f"{table_name}_acl_select",
            f"{table_name}_org_insert",
            f"{table_name}_org_update",
            f"{table_name}_org_delete",
            f"{table_name}_org_write",
        ):
            _drop_policy_if_exists(table_name, policy_name)

    # Restore Slice 1's organization policies for documents and versions.
    for table_name in ("documents", "document_versions"):
        _enable_org_policy(table_name)
    for table_name in ("chunks", "chunk_embeddings"):
        op.execute(f"ALTER TABLE public.{table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY")

    op.execute("REVOKE EXECUTE ON FUNCTION public.app_can_read_document_version(uuid, uuid) FROM opsbrain_app")
    op.execute("DROP FUNCTION public.app_can_read_document_version(uuid, uuid)")

    for table_name in _ORG_ONLY_TABLES:
        _drop_policy_if_exists(table_name, f"{table_name}_organization_isolation")
        op.execute(f"ALTER TABLE public.{table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY")

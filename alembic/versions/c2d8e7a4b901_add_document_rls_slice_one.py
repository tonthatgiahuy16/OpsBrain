"""add document organization RLS slice one

Revision ID: c2d8e7a4b901
Revises: a1e4c9f8b203
Create Date: 2026-11-24
"""

from typing import Sequence, Union

from alembic import op


revision: str = "c2d8e7a4b901"
down_revision: Union[str, Sequence[str], None] = "a1e4c9f8b203"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_WORKER_TABLES = (
    "organizations",
    "principals",
    "roles",
    "principal_role_memberships",
    "role_capabilities",
    "derived_configurations",
    "derived_sets",
    "documents",
    "document_versions",
    "document_version_acl_entries",
    "document_review_decisions",
    "audit_events",
    "ingestion_jobs",
    "ingestion_job_items",
    "ingestion_job_attempts",
    "reindex_jobs",
    "reindex_job_items",
    "reindex_job_item_attempts",
    "chunks",
    "chunk_embeddings",
)


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'opsbrain_worker') THEN
                RAISE EXCEPTION
                    'opsbrain_worker role is missing; run migrations/init_roles.sh with OPSBRAIN_WORKER_PASSWORD before migrating';
            END IF;
        END $$;
        """
    )
    op.execute(
        """
        CREATE FUNCTION public.app_authenticate_principal(requested_principal_id uuid)
        RETURNS TABLE (id uuid, organization_id uuid)
        LANGUAGE sql
        STABLE
        SECURITY DEFINER
        SET search_path = pg_catalog, public
        AS $$
            SELECT principal.id, principal.organization_id
            FROM public.principals AS principal
            JOIN public.organizations AS organization
              ON organization.id = principal.organization_id
            WHERE principal.id = requested_principal_id
              AND principal.status = 'active'
              AND organization.status = 'active'
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION public.app_authenticate_principal(uuid) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION public.app_authenticate_principal(uuid) TO opsbrain_app")
    for table_name in ("documents", "document_versions"):
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
    op.execute(
        "DO $$ BEGIN EXECUTE format('GRANT CONNECT ON DATABASE %I TO opsbrain_worker', current_database()); END $$"
    )
    op.execute("GRANT USAGE ON SCHEMA public TO opsbrain_worker")
    op.execute("REVOKE CREATE ON SCHEMA public FROM opsbrain_worker")
    table_list = ", ".join(f"public.{table_name}" for table_name in _WORKER_TABLES)
    op.execute(
        f"GRANT SELECT, INSERT, UPDATE ON TABLE {table_list} TO opsbrain_worker"
    )
    op.execute("GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO opsbrain_worker")


def downgrade() -> None:
    table_list = ", ".join(f"public.{table_name}" for table_name in _WORKER_TABLES)
    op.execute(f"REVOKE SELECT, INSERT, UPDATE ON TABLE {table_list} FROM opsbrain_worker")
    op.execute("REVOKE EXECUTE ON FUNCTION public.app_authenticate_principal(uuid) FROM opsbrain_app")
    op.execute("DROP FUNCTION public.app_authenticate_principal(uuid)")
    for table_name in ("document_versions", "documents"):
        op.execute(f"DROP POLICY {table_name}_organization_isolation ON public.{table_name}")
        op.execute(f"ALTER TABLE public.{table_name} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE public.{table_name} DISABLE ROW LEVEL SECURITY")

"""Allow RLS-safe ORM INSERT RETURNING during document creation.

Revision ID: f5a8d1e3b704
Revises: e4f7a9c2d601
Create Date: 2026-11-25
"""

from typing import Sequence, Union

from alembic import op


revision: str = "f5a8d1e3b704"
down_revision: Union[str, Sequence[str], None] = "e4f7a9c2d601"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # PostgreSQL evaluates SELECT policies for INSERT ... RETURNING.  The
    # document/version pair is assembled atomically, so completed restricted
    # versions remain ACL-gated after current_version_id is set.
    op.execute("DROP POLICY documents_acl_select ON public.documents")
    op.execute("DROP POLICY document_versions_acl_select ON public.document_versions")
    op.execute(
        """
        CREATE POLICY documents_acl_select ON public.documents
        FOR SELECT TO opsbrain_app
        USING (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
            AND (
                current_version_id IS NULL
                OR public.app_can_read_document_version(id, current_version_id)
            )
        )
        """
    )
    op.execute(
        """
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
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY document_versions_acl_select ON public.document_versions")
    op.execute("DROP POLICY documents_acl_select ON public.documents")
    op.execute(
        """
        CREATE POLICY documents_acl_select ON public.documents
        FOR SELECT TO opsbrain_app
        USING (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
            AND current_version_id IS NOT NULL
            AND public.app_can_read_document_version(id, current_version_id)
        )
        """
    )
    op.execute(
        """
        CREATE POLICY document_versions_acl_select ON public.document_versions
        FOR SELECT TO opsbrain_app
        USING (public.app_can_read_document_version(document_pk, id))
        """
    )

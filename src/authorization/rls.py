"""Transaction-local PostgreSQL RLS context for request-scoped sessions."""

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def set_rls_context(
    session: AsyncSession,
    *,
    organization_id: UUID,
    principal_id: UUID,
) -> None:
    """Set the authenticated identity for the current transaction only.

    ``is_local=True`` is deliberate: a pooled connection cannot retain one
    request's organization or principal after commit/rollback. Call this after
    every transaction boundary before issuing an RLS-protected statement.
    """
    await session.execute(
        text(
            "SELECT set_config('app.organization_id', :organization_id, true), "
            "set_config('app.principal_id', :principal_id, true)"
        ),
        {
            "organization_id": str(organization_id),
            "principal_id": str(principal_id),
        },
    )

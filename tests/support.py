"""Shared helpers for seeding authorization state in database tests."""

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from src.models.storage import PrincipalRoleMembership, Role, RoleCapability


async def grant_capability(
    session: AsyncSession,
    *,
    organization_id,
    principal_id,
    capability: str = "document:approve",
    role_key: str | None = None,
    granted_by_principal_id=None,
) -> Role:
    """Create an active role grant for a principal and return the role."""
    role = Role(
        id=uuid4(), organization_id=organization_id,
        role_key=role_key or f"grant-{uuid4()}",
        display_name="Capability grant", status="active",
    )
    session.add(role)
    await session.flush()
    session.add_all([
        PrincipalRoleMembership(
            organization_id=organization_id, principal_id=principal_id,
            role_id=role.id, status="active",
        ),
        RoleCapability(
            organization_id=organization_id, role_id=role.id,
            capability_key=capability,
            status="active",
            granted_by_principal_id=granted_by_principal_id or principal_id,
            granted_at=datetime.now(timezone.utc),
        ),
    ])
    await session.flush()
    return role

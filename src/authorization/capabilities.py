"""Resolve capabilities from active role grants for an authenticated principal.

Capability answers "what may this actor do". It says nothing about which
documents the actor may see; that is still resolved by
``src.authorization.acl`` and both checks must pass before protected data is
touched.
"""

from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.storage import (
    Organization,
    Principal,
    PrincipalRoleMembership,
    Role,
    RoleCapability,
)


class AuthorizationError(Exception):
    """Authorization refusal that callers must map to a stable HTTP status.

    Callers must not surface the difference between "does not exist" and "not
    allowed to know it exists" for protected resources.
    """

    code = "authorization_denied"

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.code)


class DocumentNotVisible(AuthorizationError):
    """The principal is not entitled to know the document exists."""

    code = "document_not_found"


class CapabilityDenied(AuthorizationError):
    """The principal may know the resource exists but may not perform the action."""

    code = "capability_denied"

    def __init__(self, capability: str) -> None:
        self.capability = capability
        super().__init__(f"missing capability: {capability}")


async def principal_has_capability(
    session: AsyncSession,
    *,
    organization_id: UUID,
    principal_id: UUID,
    capability: str,
) -> bool:
    """Return whether an active grant gives the principal ``capability`` now.

    A grant counts only when the catalog contains the capability, the grant
    itself is ``active``, the role is active and the membership is active.
    Membership revocation, disabled role, revoked grant and catalog removal all
    take effect on the next call. Do not cache this result without an
    invalidation path tied to those tables.
    """
    granted = await session.scalar(
        select(RoleCapability.id)
        .join(
            Role,
            and_(
                Role.organization_id == RoleCapability.organization_id,
                Role.id == RoleCapability.role_id,
            ),
        )
        .join(
            PrincipalRoleMembership,
            and_(
                PrincipalRoleMembership.organization_id == Role.organization_id,
                PrincipalRoleMembership.role_id == Role.id,
            ),
        )
        .join(
            Principal,
            and_(
                Principal.organization_id == PrincipalRoleMembership.organization_id,
                Principal.id == PrincipalRoleMembership.principal_id,
            ),
        )
        .join(Organization, Organization.id == Principal.organization_id)
        .where(
            RoleCapability.organization_id == organization_id,
            RoleCapability.capability_key == capability,
            RoleCapability.status == "active",
            Role.organization_id == organization_id,
            Role.status == "active",
            PrincipalRoleMembership.organization_id == organization_id,
            PrincipalRoleMembership.principal_id == principal_id,
            PrincipalRoleMembership.status == "active",
            Principal.organization_id == organization_id,
            Principal.id == principal_id,
            Principal.status == "active",
            Organization.id == organization_id,
            Organization.status == "active",
        )
        .limit(1)
    )
    return granted is not None

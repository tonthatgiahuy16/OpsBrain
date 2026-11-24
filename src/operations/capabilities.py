"""Privileged, audited capability and role administration.

The application role may only ``SELECT`` from ``capabilities`` and
``role_capabilities`` (migration ``c3f7a1d84e29``). That is deliberate: a bug in
a request path must not be able to grant itself ``document:approve`` or
``query:execute``.

The consequence is that granting and revoking needs a separate operator path
using a privileged (owner) DSN. This module is that path. It is not imported by
the API, and it appends an audit event for every actual transition so the
privilege change is attributable.

Invariants kept here:

* an unknown capability is rejected against the catalog instead of relying on
  the database allowlist alone;
* a role must be active, or explicitly created with ``ensure_role``;
* re-granting an active capability is a no-op (no duplicate audit noise);
* revoking an active capability records who revoked it and when;
* every outcome is idempotent, so re-running the command is safe.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.storage import (
    AuditEvent,
    Capability,
    Organization,
    Principal,
    PrincipalRoleMembership,
    Role,
    RoleCapability,
)


APP_ROLE = "opsbrain_app"

GrantOutcome = Literal["granted", "reactivated", "unchanged"]
RevokeOutcome = Literal["revoked", "already_revoked"]


@dataclass(frozen=True)
class CapabilityChange:
    """Result of one privileged capability operation."""

    outcome: str
    organization_key: str
    role_key: str
    capability_key: str
    role_id: UUID
    membership_assigned: bool = False


def privileged_dsn(migration_database_url: str) -> str:
    """Return the owner DSN or refuse to run as the application role.

    Refusing here keeps the audit trail honest: a capability change made by the
    request-path role would mean the privilege boundary is already broken.
    """
    username = urlsplit(migration_database_url).username
    if not username:
        raise ValueError("privileged database URL has no username")
    if username == APP_ROLE:
        raise ValueError(
            "capability administration must run with a privileged role, "
            "not the application role"
        )
    return migration_database_url


async def _append_admin_audit(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_principal_id: UUID,
    event_type: str,
    details: dict[str, object],
) -> None:
    session.add(
        AuditEvent(
            organization_id=organization_id,
            event_type=event_type,
            outcome="succeeded",
            actor_kind="principal",
            actor_principal_id=actor_principal_id,
            details=details,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()


async def _require_context(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_principal_id: UUID,
) -> tuple[Organization, Principal]:
    organization = await session.get(Organization, organization_id)
    if organization is None:
        raise ValueError("organization not found")
    if organization.status != "active":
        raise ValueError("organization is not active")
    actor = await session.scalar(
        select(Principal).where(
            Principal.organization_id == organization_id,
            Principal.id == actor_principal_id,
        )
    )
    if actor is None:
        raise ValueError("actor principal not found in organization")
    if actor.status != "active":
        raise ValueError("actor principal is not active")
    return organization, actor


async def _require_role(
    session: AsyncSession,
    *,
    organization_id: UUID,
    role_key: str,
    ensure_role: bool,
) -> tuple[Role, bool]:
    role = await session.scalar(
        select(Role).where(
            Role.organization_id == organization_id,
            Role.role_key == role_key,
        )
    )
    if role is not None:
        if role.status != "active":
            raise ValueError("role is not active")
        return role, False
    if not ensure_role:
        raise ValueError("role not found; pass --ensure-role to create it")
    role = Role(
        id=uuid4(),
        organization_id=organization_id,
        role_key=role_key,
        display_name=role_key,
        status="active",
    )
    session.add(role)
    await session.flush()
    return role, True


async def _assign_membership(
    session: AsyncSession,
    *,
    organization_id: UUID,
    role_id: UUID,
    principal_id: UUID,
) -> bool:
    principal = await session.scalar(
        select(Principal).where(
            Principal.organization_id == organization_id,
            Principal.id == principal_id,
        )
    )
    if principal is None:
        raise ValueError("principal to assign not found in organization")
    if principal.status != "active":
        raise ValueError("principal to assign is not active")

    membership = await session.scalar(
        select(PrincipalRoleMembership).where(
            PrincipalRoleMembership.organization_id == organization_id,
            PrincipalRoleMembership.role_id == role_id,
            PrincipalRoleMembership.principal_id == principal_id,
        )
    )
    if membership is None:
        session.add(
            PrincipalRoleMembership(
                organization_id=organization_id,
                principal_id=principal_id,
                role_id=role_id,
                status="active",
            )
        )
        await session.flush()
        return True
    if membership.status == "active":
        return False
    membership.status = "active"
    await session.flush()
    return True


async def grant_capability(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_principal_id: UUID,
    role_key: str,
    capability_key: str,
    ensure_role: bool = False,
    assign_principal_id: UUID | None = None,
) -> CapabilityChange:
    """Grant ``capability_key`` to ``role_key`` and audit the transition."""
    organization, _ = await _require_context(
        session,
        organization_id=organization_id,
        actor_principal_id=actor_principal_id,
    )
    if await session.get(Capability, capability_key) is None:
        raise ValueError("unknown capability")

    role, role_created = await _require_role(
        session,
        organization_id=organization_id,
        role_key=role_key,
        ensure_role=ensure_role,
    )
    if role_created:
        await _append_admin_audit(
            session,
            organization_id=organization_id,
            actor_principal_id=actor_principal_id,
            event_type="role.created",
            details={"role_key": role_key},
        )

    membership_assigned = False
    if assign_principal_id is not None:
        membership_assigned = await _assign_membership(
            session,
            organization_id=organization_id,
            role_id=role.id,
            principal_id=assign_principal_id,
        )

    now = datetime.now(timezone.utc)
    grant = await session.scalar(
        select(RoleCapability).where(
            RoleCapability.organization_id == organization_id,
            RoleCapability.role_id == role.id,
            RoleCapability.capability_key == capability_key,
        )
    )
    if grant is None:
        session.add(
            RoleCapability(
                organization_id=organization_id,
                role_id=role.id,
                capability_key=capability_key,
                status="active",
                granted_by_principal_id=actor_principal_id,
                granted_at=now,
            )
        )
        outcome: GrantOutcome = "granted"
    elif grant.status == "active":
        outcome = "unchanged"
    else:
        grant.status = "active"
        grant.granted_at = now
        grant.granted_by_principal_id = actor_principal_id
        grant.revoked_at = None
        grant.revoked_by_principal_id = None
        outcome = "reactivated"

    if outcome != "unchanged":
        await session.flush()
        await _append_admin_audit(
            session,
            organization_id=organization_id,
            actor_principal_id=actor_principal_id,
            event_type="capability.granted",
            details={
                "role_key": role_key,
                "capability_key": capability_key,
                "outcome": outcome,
                "membership_assigned": membership_assigned,
            },
        )
    else:
        await session.flush()

    return CapabilityChange(
        outcome=outcome,
        organization_key=organization.organization_key,
        role_key=role_key,
        capability_key=capability_key,
        role_id=role.id,
        membership_assigned=membership_assigned,
    )


async def revoke_capability(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_principal_id: UUID,
    role_key: str,
    capability_key: str,
) -> CapabilityChange:
    """Revoke an active grant, or report that it is already revoked."""
    organization, _ = await _require_context(
        session,
        organization_id=organization_id,
        actor_principal_id=actor_principal_id,
    )
    role = await session.scalar(
        select(Role).where(
            Role.organization_id == organization_id,
            Role.role_key == role_key,
        )
    )
    if role is None:
        raise ValueError("role not found")

    grant = await session.scalar(
        select(RoleCapability).where(
            RoleCapability.organization_id == organization_id,
            RoleCapability.role_id == role.id,
            RoleCapability.capability_key == capability_key,
        )
    )
    if grant is None or grant.status == "revoked":
        outcome: RevokeOutcome = "already_revoked"
    else:
        grant.status = "revoked"
        grant.revoked_at = datetime.now(timezone.utc)
        grant.revoked_by_principal_id = actor_principal_id
        await session.flush()
        await _append_admin_audit(
            session,
            organization_id=organization_id,
            actor_principal_id=actor_principal_id,
            event_type="capability.revoked",
            details={
                "role_key": role_key,
                "capability_key": capability_key,
                "outcome": "revoked",
            },
        )
        outcome = "revoked"

    return CapabilityChange(
        outcome=outcome,
        organization_key=organization.organization_key,
        role_key=role_key,
        capability_key=capability_key,
        role_id=role.id,
    )

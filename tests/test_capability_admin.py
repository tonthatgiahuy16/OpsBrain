"""Privileged, audited capability administration.

The application role can only read ``role_capabilities``. These tests exercise
the operator path that runs with the owner DSN, and assert that every real
transition is attributable through ``audit_events``.
"""

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.storage import (
    AuditEvent,
    Organization,
    Principal,
    PrincipalRoleMembership,
    Role,
    RoleCapability,
)
from src.operations.capabilities import (
    grant_capability,
    privileged_dsn,
    revoke_capability,
)


async def _seed(session: AsyncSession):
    organization_id = uuid4()
    author_id = uuid4()
    reviewer_id = uuid4()
    session.add_all(
        [
            Organization(
                id=organization_id,
                organization_key=f"admin-org-{organization_id}",
                display_name="Capability admin",
                status="active",
            ),
            Principal(
                id=author_id,
                organization_id=organization_id,
                principal_key=f"admin-actor-{author_id}",
                principal_kind="user",
                display_name="Admin actor",
                status="active",
            ),
            Principal(
                id=reviewer_id,
                organization_id=organization_id,
                principal_key=f"admin-reviewer-{reviewer_id}",
                principal_kind="user",
                display_name="Reviewer",
                status="active",
            ),
        ]
    )
    await session.flush()
    return organization_id, author_id, reviewer_id


async def _audit_events(session: AsyncSession, organization_id, event_type: str):
    return (
        await session.scalars(
            select(AuditEvent).where(
                AuditEvent.organization_id == organization_id,
                AuditEvent.event_type == event_type,
            )
        )
    ).all()


@pytest.mark.asyncio
async def test_grant_creates_role_membership_and_audited_capability(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, reviewer_id = await _seed(session)

            change = await grant_capability(
                session,
                organization_id=organization_id,
                actor_principal_id=author_id,
                role_key="operator-reviewer",
                capability_key="document:approve",
                ensure_role=True,
                assign_principal_id=reviewer_id,
            )

            assert change.outcome == "granted"
            assert change.membership_assigned is True

            role = await session.scalar(
                select(Role).where(
                    Role.organization_id == organization_id,
                    Role.role_key == "operator-reviewer",
                )
            )
            assert role is not None
            assert role.status == "active"

            grant = await session.scalar(
                select(RoleCapability).where(
                    RoleCapability.organization_id == organization_id,
                    RoleCapability.capability_key == "document:approve",
                )
            )
            assert grant is not None
            assert grant.status == "active"
            assert grant.granted_by_principal_id == author_id
            assert grant.revoked_at is None

            membership = await session.scalar(
                select(PrincipalRoleMembership).where(
                    PrincipalRoleMembership.role_id == role.id,
                    PrincipalRoleMembership.principal_id == reviewer_id,
                )
            )
            assert membership is not None
            assert membership.status == "active"

            created = await _audit_events(session, organization_id, "role.created")
            granted = await _audit_events(session, organization_id, "capability.granted")
            assert len(created) == 1
            assert len(granted) == 1
            assert granted[0].actor_principal_id == author_id
            assert granted[0].details == {
                "role_key": "operator-reviewer",
                "capability_key": "document:approve",
                "outcome": "granted",
                "membership_assigned": True,
            }
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_grant_is_idempotent_and_does_not_duplicate_audit(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, _ = await _seed(session)

            first = await grant_capability(
                session,
                organization_id=organization_id,
                actor_principal_id=author_id,
                role_key="audited-role",
                capability_key="query:execute",
                ensure_role=True,
            )
            second = await grant_capability(
                session,
                organization_id=organization_id,
                actor_principal_id=author_id,
                role_key="audited-role",
                capability_key="query:execute",
            )

            assert first.outcome == "granted"
            assert second.outcome == "unchanged"
            assert await session.scalar(
                select(func.count()).select_from(RoleCapability)
            ) == 1
            assert len(
                await _audit_events(session, organization_id, "capability.granted")
            ) == 1
            assert len(
                await _audit_events(session, organization_id, "role.created")
            ) == 1
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_revoke_is_audited_and_idempotent_then_regrant_reactivates(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, _ = await _seed(session)
            await grant_capability(
                session,
                organization_id=organization_id,
                actor_principal_id=author_id,
                role_key="revocable-role",
                capability_key="index:rebuild",
                ensure_role=True,
            )

            revoked = await revoke_capability(
                session,
                organization_id=organization_id,
                actor_principal_id=author_id,
                role_key="revocable-role",
                capability_key="index:rebuild",
            )
            assert revoked.outcome == "revoked"

            grant = await session.scalar(select(RoleCapability))
            assert grant.status == "revoked"
            assert grant.revoked_by_principal_id == author_id
            assert grant.revoked_at is not None
            assert len(
                await _audit_events(session, organization_id, "capability.revoked")
            ) == 1

            again = await revoke_capability(
                session,
                organization_id=organization_id,
                actor_principal_id=author_id,
                role_key="revocable-role",
                capability_key="index:rebuild",
            )
            assert again.outcome == "already_revoked"
            assert len(
                await _audit_events(session, organization_id, "capability.revoked")
            ) == 1

            regranted = await grant_capability(
                session,
                organization_id=organization_id,
                actor_principal_id=author_id,
                role_key="revocable-role",
                capability_key="index:rebuild",
            )
            assert regranted.outcome == "reactivated"
            await session.refresh(grant)
            assert grant.status == "active"
            assert grant.revoked_at is None
            assert grant.revoked_by_principal_id is None
            assert len(
                await _audit_events(session, organization_id, "capability.granted")
            ) == 2
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_grant_rejects_unknown_capability_and_missing_role(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, _ = await _seed(session)

            with pytest.raises(ValueError, match="unknown capability"):
                await grant_capability(
                    session,
                    organization_id=organization_id,
                    actor_principal_id=author_id,
                    role_key="missing-role",
                    capability_key="document:teleport",
                    ensure_role=True,
                )

            with pytest.raises(ValueError, match="role not found"):
                await grant_capability(
                    session,
                    organization_id=organization_id,
                    actor_principal_id=author_id,
                    role_key="missing-role",
                    capability_key="query:execute",
                )

            assert await session.scalar(
                select(func.count()).select_from(RoleCapability)
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(Role)
            ) == 0
            assert await session.scalar(
                select(func.count()).select_from(AuditEvent)
            ) == 0
        finally:
            await session.close()
            await transaction.rollback()


def test_privileged_dsn_refuses_the_application_role():
    app_url = (
        "postgresql+asyncpg://opsbrain_app:secret@localhost:5432/opsbrain"
    )
    owner_url = (
        "postgresql+asyncpg://opsbrain_owner:secret@localhost:5432/opsbrain"
    )
    with pytest.raises(ValueError, match="privileged role"):
        privileged_dsn(app_url)
    assert privileged_dsn(owner_url) == owner_url

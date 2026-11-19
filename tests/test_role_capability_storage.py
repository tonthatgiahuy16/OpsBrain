"""Storage-contract tests for the capability catalog and role grants.

Covers storage-schema §9.5 (migration-managed catalog) and §9.6 (grant
lifecycle, actor scope, uniqueness and revocation consistency).
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.authorization.capabilities import principal_has_capability
from src.models.storage import (
    Capability,
    Organization,
    Principal,
    PrincipalRoleMembership,
    Role,
    RoleCapability,
)


CATALOG = {
    "document:approve",
    "document:ingest",
    "document:read_state",
    "index:read",
    "index:rebuild",
    "ingestion:read",
    "query:execute",
}


async def _seed_subjects(session: AsyncSession, *, capability: str = "query:execute"):
    organization_id = uuid4()
    actor_id = uuid4()
    session.add_all([
        Organization(
            id=organization_id, organization_key=f"cap-org-{organization_id}",
            display_name="Capability test", status="active",
        ),
        Principal(
            id=actor_id, organization_id=organization_id,
            principal_key=f"cap-actor-{actor_id}", principal_kind="user",
            display_name="Capability actor", status="active",
        ),
        Role(
            id=uuid4(), organization_id=organization_id,
            role_key=f"cap-role-{organization_id}", display_name="Capability role",
            status="active",
        ),
    ])
    await session.flush()
    role = await session.scalar(
        select(Role).where(Role.organization_id == organization_id)
    )
    grant = RoleCapability(
        organization_id=organization_id, role_id=role.id,
        capability_key=capability, status="active",
        granted_by_principal_id=actor_id,
        granted_at=datetime.now(timezone.utc),
    )
    session.add(grant)
    await session.flush()
    return {
        "organization_id": organization_id,
        "actor_id": actor_id,
        "role": role,
        "grant": grant,
    }


@pytest.mark.asyncio
async def test_capability_catalog_is_seeded_exactly(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            rows = list((await session.scalars(select(Capability))).all())
            assert {row.capability_key for row in rows} == CATALOG
            assert all(row.description.strip() for row in rows)
        finally:
            await session.close()


@pytest.mark.asyncio
async def test_application_role_cannot_write_catalog_or_grants(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        try:
            await connection.execute(text("SET LOCAL ROLE opsbrain_app"))
            for table in ("capabilities", "role_capabilities"):
                assert await connection.scalar(text(
                    "SELECT has_table_privilege(current_user, "
                    f"'{table}', 'SELECT')"
                )) is True
                for privilege in ("INSERT", "UPDATE", "DELETE"):
                    assert await connection.scalar(text(
                        "SELECT has_table_privilege(current_user, "
                        f"'{table}', '{privilege}')"
                    )) is False
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_unknown_capability_is_rejected_by_catalog_foreign_key(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_subjects(session)
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    session.add(RoleCapability(
                        organization_id=seeded["organization_id"],
                        role_id=seeded["role"].id,
                        capability_key="root:everything", status="active",
                        granted_by_principal_id=seeded["actor_id"],
                        granted_at=datetime.now(timezone.utc),
                    ))
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_catalog_entry_outside_allowlist_cannot_be_granted_to_role(
    migration_database_engine,
):
    """A catalog row alone cannot expand the set of grantable operations."""
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_subjects(session)
            session.add(Capability(
                capability_key="root:everything",
                description="Must never become a grantable operation",
            ))
            await session.flush()

            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    session.add(RoleCapability(
                        organization_id=seeded["organization_id"],
                        role_id=seeded["role"].id,
                        capability_key="root:everything", status="active",
                        granted_by_principal_id=seeded["actor_id"],
                        granted_at=datetime.now(timezone.utc),
                    ))
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_grant_is_unique_per_role_and_capability(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_subjects(session)
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    session.add(RoleCapability(
                        organization_id=seeded["organization_id"],
                        role_id=seeded["role"].id,
                        capability_key="query:execute", status="active",
                        granted_by_principal_id=seeded["actor_id"],
                        granted_at=datetime.now(timezone.utc),
                    ))
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_active_grant_must_not_carry_revocation_fields(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_subjects(session)
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    seeded["grant"].revoked_by_principal_id = seeded["actor_id"]
                    seeded["grant"].revoked_at = datetime.now(timezone.utc)
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_revoked_grant_requires_actor_and_timestamp(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_subjects(session)
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    seeded["grant"].status = "revoked"
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_revocation_cannot_predate_grant(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_subjects(session)
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    seeded["grant"].status = "revoked"
                    seeded["grant"].revoked_by_principal_id = seeded["actor_id"]
                    seeded["grant"].revoked_at = (
                        seeded["grant"].granted_at - timedelta(seconds=1)
                    )
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_grant_actor_must_belong_to_the_same_organization(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_subjects(session)
            other_organization = Organization(
                id=uuid4(), organization_key=f"other-cap-{uuid4()}",
                display_name="Other", status="active",
            )
            outsider = Principal(
                id=uuid4(), organization_id=other_organization.id,
                principal_key=f"outsider-{uuid4()}", principal_kind="user",
                display_name="Outsider", status="active",
            )
            session.add_all([other_organization, outsider])
            await session.flush()

            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    session.add(RoleCapability(
                        organization_id=seeded["organization_id"],
                        role_id=seeded["role"].id,
                        capability_key="document:approve", status="active",
                        granted_by_principal_id=outsider.id,
                        granted_at=datetime.now(timezone.utc),
                    ))
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_principal_has_capability_ignores_revoked_and_unknown_grants(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_subjects(session)
            session.add(PrincipalRoleMembership(
                organization_id=seeded["organization_id"],
                principal_id=seeded["actor_id"],
                role_id=seeded["role"].id, status="active",
            ))
            await session.flush()
            assert await principal_has_capability(
                session,
                organization_id=seeded["organization_id"],
                principal_id=seeded["actor_id"],
                capability="query:execute",
            ) is True
            assert await principal_has_capability(
                session,
                organization_id=seeded["organization_id"],
                principal_id=seeded["actor_id"],
                capability="document:approve",
            ) is False

            seeded["grant"].status = "revoked"
            seeded["grant"].revoked_by_principal_id = seeded["actor_id"]
            seeded["grant"].revoked_at = datetime.now(timezone.utc)
            await session.flush()
            assert await principal_has_capability(
                session,
                organization_id=seeded["organization_id"],
                principal_id=seeded["actor_id"],
                capability="query:execute",
            ) is False
        finally:
            await session.close()
            await transaction.rollback()

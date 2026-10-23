from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.models.storage import AuditEvent, Organization


async def create_audit_event(session: AsyncSession) -> AuditEvent:
    organization_id = uuid4()

    session.add(
        Organization(
            id=organization_id,
            organization_key=f"test-{organization_id}",
            display_name="Audit Event Test Organization",
            status="active",
        )
    )
    await session.flush()

    event = AuditEvent(
        organization_id=organization_id,
        event_type="ingestion.job.accepted",
        outcome="succeeded",
        actor_kind="system",
        request_id="audit-event-migration-test",
        details={},
        occurred_at=datetime.now(timezone.utc),
    )
    session.add(event)
    await session.flush()
    return event


@pytest.mark.asyncio
async def test_audit_event_can_be_persisted_after_migration(
    database_engine: AsyncEngine,
):
    organization_id = uuid4()

    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
        )

        try:
            session.add(
                Organization(
                    id=organization_id,
                    organization_key=f"test-{organization_id}",
                    display_name="Audit Event Test Organization",
                    status="active",
                )
            )
            await session.flush()

            event = AuditEvent(
                organization_id=organization_id,
                event_type="ingestion.job.accepted",
                outcome="succeeded",
                actor_kind="system",
                request_id="audit-event-migration-test",
                details={},
                occurred_at=datetime.now(timezone.utc),
            )
            session.add(event)
            await session.flush()

            assert event.id is not None
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_audit_event_app_role_cannot_update(
    database_engine: AsyncEngine,
):
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            event = await create_audit_event(session)
            savepoint = await connection.begin_nested()

            try:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await connection.execute(
                        text(
                            "UPDATE audit_events "
                            "SET outcome = 'failed' "
                            "WHERE id = :event_id"
                        ),
                        {"event_id": event.id},
                    )
            finally:
                await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_audit_event_app_role_cannot_delete(
    database_engine: AsyncEngine,
):
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            event = await create_audit_event(session)
            savepoint = await connection.begin_nested()

            try:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await connection.execute(
                        text(
                            "DELETE FROM audit_events "
                            "WHERE id = :event_id"
                        ),
                        {"event_id": event.id},
                    )
            finally:
                await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_audit_event_owner_update_is_rejected_by_trigger(
    migration_database_engine: AsyncEngine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            event = await create_audit_event(session)
            savepoint = await connection.begin_nested()

            try:
                with pytest.raises(DBAPIError, match="append-only"):
                    await connection.execute(
                        text(
                            "UPDATE audit_events "
                            "SET outcome = 'failed' "
                            "WHERE id = :event_id"
                        ),
                        {"event_id": event.id},
                    )
            finally:
                await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_audit_event_owner_delete_is_rejected_by_trigger(
    migration_database_engine: AsyncEngine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            event = await create_audit_event(session)
            savepoint = await connection.begin_nested()

            try:
                with pytest.raises(DBAPIError, match="append-only"):
                    await connection.execute(
                        text(
                            "DELETE FROM audit_events "
                            "WHERE id = :event_id"
                        ),
                        {"event_id": event.id},
                    )
            finally:
                await savepoint.rollback()
        finally:
            await session.close()
            await transaction.rollback()

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.models.storage import (
    IngestionJob,
    Organization,
    Principal,
    IngestionJobAttempt,
    IngestionJobItem,
)

from datetime import datetime, timezone


def sha256_hex(character: str) -> str:
    return character * 64


@pytest.mark.asyncio
async def test_ingestion_job_idempotency_key_is_unique_per_principal(
    database_engine: AsyncEngine,
):
    organization_id = uuid4()
    principal_id = uuid4()

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
                    display_name="Ingestion Job Test Organization",
                    status="active",
                )
            )
            session.add(
                Principal(
                    id=principal_id,
                    organization_id=organization_id,
                    principal_key=f"test-user-{principal_id}",
                    principal_kind="user",
                    display_name="Ingestion Job Test User",
                    status="active",
                )
            )
            await session.flush()

            session.add(
                IngestionJob(
                    organization_id=organization_id,
                    request_id="request-first",
                    idempotency_key_hash=sha256_hex("a"),
                    request_payload_hash=sha256_hex("b"),
                    submitted_by_principal_id=principal_id,
                    status="queued",
                    submitted_document_count=1,
                )
            )
            await session.flush()

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        IngestionJob(
                            organization_id=organization_id,
                            request_id="request-duplicate",
                            idempotency_key_hash=sha256_hex("a"),
                            request_payload_hash=sha256_hex("c"),
                            submitted_by_principal_id=principal_id,
                            status="queued",
                            submitted_document_count=1,
                        )
                    )
                    await session.flush()

            job_count = await session.scalar(
                select(func.count())
                .select_from(IngestionJob)
                .where(
                    IngestionJob.organization_id == organization_id,
                    IngestionJob.submitted_by_principal_id
                    == principal_id,
                )
            )

            assert job_count == 1
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_ingestion_job_rejects_principal_from_another_organization(
    database_engine: AsyncEngine,
):
    first_organization_id = uuid4()
    second_organization_id = uuid4()
    second_principal_id = uuid4()

    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
        )

        try:
            session.add_all(
                [
                    Organization(
                        id=first_organization_id,
                        organization_key=(
                            f"test-{first_organization_id}"
                        ),
                        display_name="First Test Organization",
                        status="active",
                    ),
                    Organization(
                        id=second_organization_id,
                        organization_key=(
                            f"test-{second_organization_id}"
                        ),
                        display_name="Second Test Organization",
                        status="active",
                    ),
                    Principal(
                        id=second_principal_id,
                        organization_id=second_organization_id,
                        principal_key=(
                            f"test-user-{second_principal_id}"
                        ),
                        principal_kind="user",
                        display_name="Second Test User",
                        status="active",
                    ),
                ]
            )
            await session.flush()

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        IngestionJob(
                            organization_id=first_organization_id,
                            request_id="cross-organization-request",
                            idempotency_key_hash=sha256_hex("d"),
                            request_payload_hash=sha256_hex("e"),
                            submitted_by_principal_id=second_principal_id,
                            status="queued",
                            submitted_document_count=1,
                        )
                    )
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_item_and_attempt_reject_invalid_terminal_states(
    database_engine: AsyncEngine,
):
    organization_id = uuid4()
    principal_id = uuid4()
    now = datetime.now(timezone.utc)

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
                    display_name="State Constraint Test Organization",
                    status="active",
                )
            )
            session.add(
                Principal(
                    id=principal_id,
                    organization_id=organization_id,
                    principal_key=f"test-user-{principal_id}",
                    principal_kind="user",
                    display_name="State Constraint Test User",
                    status="active",
                )
            )

            await session.flush()

            job = IngestionJob(
                organization_id=organization_id,
                request_id="state-constraint-request",
                idempotency_key_hash=sha256_hex("f"),
                request_payload_hash=sha256_hex("0"),
                submitted_by_principal_id=principal_id,
                status="queued",
                submitted_document_count=1,
            )
            session.add(job)
            await session.flush()

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        IngestionJobItem(
                            organization_id=organization_id,
                            ingestion_job_id=job.id,
                            item_index=0,
                            source_uri=(
                                "file:///vault/invalid-item.md"
                            ),
                            submitted_content_hash=sha256_hex("1"),
                            processing_status="failed",
                            outcome="failed",
                            warnings=[],
                            errors=[],
                            completed_at=now,
                        )
                    )
                    await session.flush()

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        IngestionJobAttempt(
                            organization_id=organization_id,
                            ingestion_job_id=job.id,
                            attempt_number=1,
                            status="succeeded",
                            retryable=True,
                            started_at=now,
                            completed_at=now,
                        )
                    )
                    await session.flush()

            item_count = await session.scalar(
                select(func.count())
                .select_from(IngestionJobItem)
                .where(
                    IngestionJobItem.ingestion_job_id == job.id
                )
            )
            attempt_count = await session.scalar(
                select(func.count())
                .select_from(IngestionJobAttempt)
                .where(
                    IngestionJobAttempt.ingestion_job_id == job.id
                )
            )

            assert item_count == 0
            assert attempt_count == 0
        finally:
            await session.close()
            await transaction.rollback()

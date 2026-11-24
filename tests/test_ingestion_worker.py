"""Tests for queued ingestion worker batch validation, attempts, and item transactions."""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.ingestion import ingestion_worker
from src.ingestion.ingestion_worker import (
    _FencedAttempt,
    _claim_next_job,
    _finalize_job,
    _prepare_batch,
    _process_prepared_item,
    process_next_ingestion_job,
)
from src.models.storage import (
    AuditEvent,
    Document,
    IngestionJob,
    IngestionJobAttempt,
    IngestionJobItem,
    Organization,
    Principal,
)

SERVICE_DOC = (
    Path(__file__).parent.parent
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "fraud-service.md"
)
PAYMENT_SERVICE_DOC = (
    Path(__file__).parent.parent
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "payment-service.md"
)
CHECKOUT_SERVICE_DOC = (
    Path(__file__).parent.parent
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "checkout-service.md"
)

POSTMORTEM_BROKEN_REQUIRED = """---
schema_version: 1
document_id: postmortem-missing-incident
document_type: postmortem
title: Postmortem With Missing Incident
document_status: active
review_state: in_review
owner_team: sre
provenance_type: synthetic
access_scope: organization
incident_refs:
  - missing-incident-id
---

# Postmortem
"""


def _session_factory_on(connection):
    @asynccontextmanager
    async def _factory():
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()

    return _factory


async def _seed_queued_job(
    session: AsyncSession,
    *,
    documents: list[tuple[str, str]],
) -> tuple[Organization, Principal, IngestionJob]:
    org_id = uuid4()
    principal_id = uuid4()
    org = Organization(
        id=org_id,
        organization_key=f"worker-ingest-{org_id}",
        display_name="Worker Ingest",
        status="active",
    )
    principal = Principal(
        id=principal_id,
        organization_id=org_id,
        principal_key=f"worker-principal-{principal_id}",
        principal_kind="user",
        display_name="Ingester",
        status="active",
    )
    session.add_all([org, principal])
    await session.flush()

    job = IngestionJob(
        organization_id=org_id,
        request_id=f"req-{uuid4()}",
        idempotency_key_hash="a" * 64,
        request_payload_hash="b" * 64,
        submitted_by_principal_id=principal_id,
        status="queued",
        submitted_document_count=len(documents),
    )
    session.add(job)
    await session.flush()

    for idx, (source_uri, markdown) in enumerate(documents):
        session.add(
            IngestionJobItem(
                organization_id=org_id,
                ingestion_job_id=job.id,
                item_index=idx,
                source_uri=source_uri,
                submitted_markdown=markdown,
                submitted_content_hash=f"{idx:064x}",
                processing_status="queued",
            )
        )
    await session.flush()
    return org, principal, job


@pytest.mark.asyncio
async def test_worker_records_attempt_optional_warning_and_required_ref_failure(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org, _, job = await _seed_queued_job(
                session,
                documents=[
                    # payment-service has optional dependency_refs absent from batch -> warning
                    (
                        "obsidian://vault/payment-service.md",
                        PAYMENT_SERVICE_DOC.read_text(encoding="utf-8"),
                    ),
                    # Postmortem references missing-incident-id -> required reference failure
                    (
                        "obsidian://vault/postmortem.md",
                        POSTMORTEM_BROKEN_REQUIRED,
                    ),
                ],
            )
            job_id = job.id
            org_id = org.id
            await session.commit()

            processed = await process_next_ingestion_job(
                _session_factory_on(connection)
            )
            assert processed == job_id
            session.expire_all()

            refreshed_job = await session.get(IngestionJob, job_id)
            assert refreshed_job.status == "completed_with_errors"
            assert refreshed_job.started_at is not None
            assert refreshed_job.completed_at is not None

            attempt = await session.scalar(
                select(IngestionJobAttempt).where(
                    IngestionJobAttempt.organization_id == org_id,
                    IngestionJobAttempt.ingestion_job_id == job_id,
                )
            )
            assert attempt is not None
            assert attempt.attempt_number == 1
            assert attempt.status == "succeeded"
            assert attempt.completed_at is not None

            items = (
                await session.scalars(
                    select(IngestionJobItem)
                    .where(
                        IngestionJobItem.organization_id == org_id,
                        IngestionJobItem.ingestion_job_id == job_id,
                    )
                    .order_by(IngestionJobItem.item_index.asc())
                )
            ).all()
            assert items[0].processing_status == "succeeded"
            assert items[0].outcome == "created"
            assert len(items[0].warnings) >= 1
            assert all(w["code"] == "broken_optional_reference" for w in items[0].warnings)

            assert items[1].processing_status == "failed"
            assert items[1].outcome == "failed"
            assert items[1].document_pk is None
            assert items[1].errors[0]["code"] == "required_reference_error"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_worker_item_failure_does_not_rollback_committed_item(
    migration_database_engine,
    monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org, _, job = await _seed_queued_job(
                session,
                documents=[
                    (
                        "obsidian://vault/fraud-service.md",
                        SERVICE_DOC.read_text(encoding="utf-8"),
                    ),
                    (
                        "obsidian://vault/checkout-service.md",
                        CHECKOUT_SERVICE_DOC.read_text(encoding="utf-8"),
                    ),
                ],
            )
            job_id = job.id
            org_id = org.id
            await session.commit()

            real_ingest = ingestion_worker.ingest_validated_document
            calls = 0

            async def fail_second_item(item_session, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("simulated transient failure on item 2")
                return await real_ingest(item_session, **kwargs)

            monkeypatch.setattr(
                ingestion_worker, "ingest_validated_document", fail_second_item
            )

            processed = await process_next_ingestion_job(
                _session_factory_on(connection)
            )
            assert processed == job_id
            session.expire_all()

            # Item 1 stayed committed in its own transaction despite item 2 failing.
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.organization_id == org_id)
                )
                == 1
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.organization_id == org_id,
                        AuditEvent.event_type == "ingestion.item.created",
                    )
                )
                == 1
            )

            refreshed_job = await session.get(IngestionJob, job_id)
            assert refreshed_job.status == "completed_with_errors"

            items = (
                await session.scalars(
                    select(IngestionJobItem)
                    .where(
                        IngestionJobItem.organization_id == org_id,
                        IngestionJobItem.ingestion_job_id == job_id,
                    )
                    .order_by(IngestionJobItem.item_index.asc())
                )
            ).all()
            assert items[0].processing_status == "succeeded"
            assert items[1].processing_status == "failed"
            assert items[1].errors == [
                {"code": "ingestion_error", "message": "document ingestion failed"}
            ]
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_worker_marks_attempt_failed_when_all_items_fail(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org, _, job = await _seed_queued_job(
                session,
                documents=[
                    ("obsidian://vault/postmortem.md", POSTMORTEM_BROKEN_REQUIRED),
                ],
            )
            job_id = job.id
            org_id = org.id
            await session.commit()

            processed = await process_next_ingestion_job(
                _session_factory_on(connection)
            )
            assert processed == job_id
            session.expire_all()

            refreshed_job = await session.get(IngestionJob, job_id)
            assert refreshed_job.status == "failed"

            attempt = await session.scalar(
                select(IngestionJobAttempt).where(
                    IngestionJobAttempt.organization_id == org_id,
                    IngestionJobAttempt.ingestion_job_id == job_id,
                )
            )
            assert attempt is not None
            assert attempt.status == "failed"
            assert attempt.error_code == "all_items_failed"
            assert attempt.completed_at is not None
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_worker_reclaims_stale_running_job_and_skips_completed_items(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org, principal, job = await _seed_queued_job(
                session,
                documents=[
                    (
                        "obsidian://vault/fraud-service.md",
                        SERVICE_DOC.read_text(encoding="utf-8"),
                    ),
                    (
                        "obsidian://vault/checkout-service.md",
                        CHECKOUT_SERVICE_DOC.read_text(encoding="utf-8"),
                    ),
                ],
            )
            now = datetime.now(timezone.utc)
            # Simulate a worker that crashed after claiming and finishing only item 0.
            crashed_attempt = IngestionJobAttempt(
                id=uuid4(),
                organization_id=org.id,
                ingestion_job_id=job.id,
                attempt_number=1,
                status="running",
                retryable=False,
                started_at=now - timedelta(seconds=10),
            )
            session.add(crashed_attempt)
            await session.flush()

            job.status = "running"
            job.started_at = now - timedelta(seconds=10)
            job.active_attempt_id = crashed_attempt.id

            items = (
                await session.scalars(
                    select(IngestionJobItem)
                    .where(
                        IngestionJobItem.organization_id == org.id,
                        IngestionJobItem.ingestion_job_id == job.id,
                    )
                    .order_by(IngestionJobItem.item_index.asc())
                )
            ).all()
            items[0].processing_status = "succeeded"
            items[0].outcome = "created"
            items[0].completed_at = now - timedelta(seconds=5)

            job_id = job.id
            org_id = org.id
            await session.commit()

            # Fresh running job within lease window must NOT be reclaimed.
            assert (
                await process_next_ingestion_job(
                    _session_factory_on(connection),
                    stale_after_seconds=300,
                )
                is None
            )

            # Once stale, the next worker reclaims the job, closes attempt 1,
            # and processes only the remaining queued item (item 1).
            reclaimed_id = await process_next_ingestion_job(
                _session_factory_on(connection),
                stale_after_seconds=5,
            )
            assert reclaimed_id == job_id
            session.expire_all()

            refreshed_job = await session.get(IngestionJob, job_id)
            assert refreshed_job.status == "completed"

            attempts = (
                await session.scalars(
                    select(IngestionJobAttempt)
                    .where(
                        IngestionJobAttempt.organization_id == org_id,
                        IngestionJobAttempt.ingestion_job_id == job_id,
                    )
                    .order_by(IngestionJobAttempt.attempt_number.asc())
                )
            ).all()
            assert len(attempts) == 2
            assert attempts[0].status == "failed"
            assert attempts[0].retryable is True
            assert attempts[0].error_code == "worker_lease_expired"
            assert attempts[1].status == "succeeded"

            # Only item 1 created a new Document; item 0 was not re-ingested.
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.organization_id == org_id)
                )
                == 1
            )
            refreshed_items = (
                await session.scalars(
                    select(IngestionJobItem)
                    .where(
                        IngestionJobItem.organization_id == org_id,
                        IngestionJobItem.ingestion_job_id == job_id,
                    )
                    .order_by(IngestionJobItem.item_index.asc())
                )
            ).all()
            assert refreshed_items[0].processing_status == "succeeded"
            assert refreshed_items[1].processing_status == "succeeded"
            assert refreshed_items[1].outcome == "created"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_fenced_worker_cannot_process_or_finalize_after_reclaim():
    """Two-connection test: live worker A is fenced after worker B reclaims."""
    url = os.environ.get("OPSBRAIN_CONCURRENCY_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated concurrency test database is not configured")
    assert make_url(url).database == "opsbrain_concurrency_test"

    engine_a = create_async_engine(url)
    engine_b = create_async_engine(url)
    factory_a = async_sessionmaker(
        bind=engine_a, class_=AsyncSession, expire_on_commit=False
    )
    factory_b = async_sessionmaker(
        bind=engine_b, class_=AsyncSession, expire_on_commit=False
    )

    try:
        async with factory_a() as seed_session:
            org, _, job = await _seed_queued_job(
                seed_session,
                documents=[
                    (
                        "obsidian://vault/fraud-service.md",
                        SERVICE_DOC.read_text(encoding="utf-8"),
                    ),
                    (
                        "obsidian://vault/checkout-service.md",
                        CHECKOUT_SERVICE_DOC.read_text(encoding="utf-8"),
                    ),
                ],
            )
            job_id = job.id
            org_id = org.id
            await seed_session.commit()

        # Worker A claims the job (attempt 1) and pauses before writing items.
        claimed_a = await _claim_next_job(factory_a, stale_after_seconds=300)
        assert claimed_a is not None
        assert claimed_a.job_id == job_id
        prepared_a = _prepare_batch(claimed_a.all_items)

        # Backdate job.started_at so Worker B can reclaim after lease expiry.
        async with factory_b() as age_session:
            aged_job = await age_session.get(IngestionJob, job_id)
            aged_job.started_at = datetime.now(timezone.utc) - timedelta(seconds=600)
            await age_session.commit()

        # Worker B reclaims (attempt 2 becomes active_attempt_id).
        claimed_b = await _claim_next_job(factory_b, stale_after_seconds=300)
        assert claimed_b is not None
        assert claimed_b.job_id == job_id
        assert claimed_b.attempt_id != claimed_a.attempt_id

        # Worker A wakes up and attempts to process item 0 and finalize -> both fenced.
        with pytest.raises(_FencedAttempt):
            await _process_prepared_item(
                factory_a, claimed=claimed_a, prepared=prepared_a[0]
            )
        with pytest.raises(_FencedAttempt):
            await _finalize_job(factory_a, claimed=claimed_a)

        # Nothing was written by A; B still owns and completes the job cleanly.
        async with factory_b() as check_session:
            assert (
                await check_session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.organization_id == org_id)
                )
                == 0
            )
            current_job = await check_session.get(IngestionJob, job_id)
            assert current_job.status == "running"
            assert current_job.active_attempt_id == claimed_b.attempt_id

        prepared_b = _prepare_batch(claimed_b.all_items)
        for item in prepared_b:
            await _process_prepared_item(factory_b, claimed=claimed_b, prepared=item)
        await _finalize_job(factory_b, claimed=claimed_b)

        async with factory_b() as final_session:
            final_job = await final_session.get(IngestionJob, job_id)
            assert final_job.status == "completed"
            assert final_job.active_attempt_id is None
            assert (
                await final_session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.organization_id == org_id)
                )
                == 2
            )
            attempts = (
                await final_session.scalars(
                    select(IngestionJobAttempt)
                    .where(
                        IngestionJobAttempt.organization_id == org_id,
                        IngestionJobAttempt.ingestion_job_id == job_id,
                    )
                    .order_by(IngestionJobAttempt.attempt_number.asc())
                )
            ).all()
            assert [a.status for a in attempts] == ["failed", "succeeded"]
            assert attempts[0].error_code == "worker_lease_expired"
    finally:
        await engine_a.dispose()
        await engine_b.dispose()

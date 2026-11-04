"""Contract tests for the persisted reindex lease and fencing protocol."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.ingestion.reindex_worker import (
    RetryableReindexError,
    _FencedAttempt,
    _finalize_build,
    claim_next_chunk_reindex_item,
    process_next_chunk_reindex_item,
    reclaim_expired_chunk_reindex_item,
)
from src.models.storage import (
    AuditEvent, Chunk, DerivedSet, ReindexJob, ReindexJobItem,
    ReindexJobItemAttempt,
)
from tests.test_reindex_worker import _enqueue_approval


def _lease_concurrency_database_url() -> str:
    url = os.environ.get("OPSBRAIN_CONCURRENCY_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated concurrency test database is not configured")
    assert make_url(url).database == "opsbrain_concurrency_test"
    return url


@pytest.mark.asyncio
async def test_deferred_trigger_rejects_queued_item_with_running_attempt(
    migration_database_engine,
):
    """The DB, not just worker code, rejects an unfenced running attempt."""
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, _, approval = await _enqueue_approval(session)
            item = await session.scalar(select(ReindexJobItem).where(
                ReindexJobItem.reindex_job_id == approval.reindex_job_id
            ))
            item_id = item.id
            organization_id = item.organization_id
            with pytest.raises(IntegrityError, match="ck_reindex_job_items_"):
                async with session.begin_nested():
                    item.processing_status = "running"
                    item.next_retry_at = datetime.now(timezone.utc)
                    await session.flush()
            with pytest.raises(IntegrityError, match="ck_reindex_job_item_attempts_"):
                async with session.begin_nested():
                    started_at = datetime.now(timezone.utc)
                    session.add(ReindexJobItemAttempt(
                        organization_id=organization_id,
                        reindex_job_item_id=item_id,
                        attempt_number=1,
                        status="running",
                        retryable=False,
                        started_at=started_at,
                        lease_expires_at=started_at,
                    ))
                    await session.flush()
            with pytest.raises(DBAPIError, match="non-running reindex item"):
                async with session.begin_nested():
                    started_at = datetime.now(timezone.utc)
                    session.add(ReindexJobItemAttempt(
                        organization_id=organization_id,
                        reindex_job_item_id=item_id,
                        attempt_number=1,
                        status="running",
                        retryable=False,
                        started_at=started_at,
                        lease_expires_at=started_at + timedelta(seconds=60),
                    ))
                    await session.flush()
                    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            with pytest.raises(IntegrityError, match="uq_reindex_job_item_attempts_running_item"):
              async with session.begin_nested():
                started_at = datetime.now(timezone.utc)
                first_attempt = ReindexJobItemAttempt(
                    organization_id=organization_id,
                    reindex_job_item_id=item_id,
                    attempt_number=1,
                    status="running",
                    retryable=False,
                    started_at=started_at,
                    lease_expires_at=started_at + timedelta(seconds=60),
                )
                session.add(first_attempt)
                await session.flush()
                item = await session.get(ReindexJobItem, item_id)
                item.processing_status = "running"
                item.active_attempt_id = first_attempt.id
                await session.flush()
                session.add(ReindexJobItemAttempt(
                    organization_id=organization_id,
                    reindex_job_item_id=item_id,
                    attempt_number=2,
                    status="running",
                    retryable=False,
                    started_at=started_at,
                    lease_expires_at=started_at + timedelta(seconds=60),
                ))
                await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_retry_is_delayed_then_next_claim_creates_new_attempt(
    migration_database_engine, monkeypatch,
):
    from src.ingestion import reindex_worker

    from src.ingestion.derived_build import build_chunk_derived_set
    calls = 0

    async def transient_build(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RetryableReindexError()
        return await build_chunk_derived_set(*args, **kwargs)

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, _, approval = await _enqueue_approval(session)
            monkeypatch.setattr(reindex_worker, "build_chunk_derived_set", transient_build)
            item_id = await process_next_chunk_reindex_item(session)
            item = await session.get(ReindexJobItem, item_id)
            attempts = list((await session.scalars(select(ReindexJobItemAttempt).where(
                ReindexJobItemAttempt.reindex_job_item_id == item_id,
            ))).all())
            assert item.processing_status == "queued"
            assert item.active_attempt_id is None
            assert item.next_retry_at is not None and item.next_retry_at > datetime.now(timezone.utc)
            assert [(attempt.attempt_number, attempt.status, attempt.retryable) for attempt in attempts] == [(1, "failed", True)]
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.reindex_job_id == approval.reindex_job_id,
                AuditEvent.event_type.like("reindex.item.%"),
            )) == 0
            assert await process_next_chunk_reindex_item(session) is None

            item.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.flush()
            assert await process_next_chunk_reindex_item(session) == item_id
            attempts = list((await session.scalars(select(ReindexJobItemAttempt).where(
                ReindexJobItemAttempt.reindex_job_item_id == item_id,
            ).order_by(ReindexJobItemAttempt.attempt_number))).all())
            item = await session.get(ReindexJobItem, item_id)
            assert [attempt.attempt_number for attempt in attempts] == [1, 2]
            assert attempts[1].status == "succeeded"
            assert item.processing_status == "succeeded"
            assert await process_next_chunk_reindex_item(session) is None
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_expired_attempt_is_reclaimed_and_cannot_finalize(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, _, approval = await _enqueue_approval(session)
            claimed_at = datetime.now(timezone.utc)
            claim = await claim_next_chunk_reindex_item(session, now=claimed_at)
            assert claim is not None
            assert await reclaim_expired_chunk_reindex_item(
                session,
                now=claimed_at + timedelta(seconds=61),
            ) == claim.item_id
            item = await session.get(ReindexJobItem, claim.item_id)
            attempt = await session.get(ReindexJobItemAttempt, claim.attempt_id)
            assert item.processing_status == "queued"
            assert item.active_attempt_id is None
            assert attempt.status == "failed" and attempt.error_code == "lease_expired"
            with pytest.raises(_FencedAttempt):
                await _finalize_build(session, claim, now=claimed_at + timedelta(seconds=61))
            await session.rollback()
            assert await session.scalar(select(func.count()).select_from(DerivedSet)) == 0
            assert await session.scalar(select(func.count()).select_from(Chunk)) == 0
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.reindex_job_id == approval.reindex_job_id,
                AuditEvent.event_type.like("reindex.item.%"),
            )) == 0
            item = await session.get(ReindexJobItem, claim.item_id)
            item.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.flush()
            assert await process_next_chunk_reindex_item(session) == claim.item_id
            attempts = list((await session.scalars(select(ReindexJobItemAttempt).where(
                ReindexJobItemAttempt.reindex_job_item_id == claim.item_id,
            ).order_by(ReindexJobItemAttempt.attempt_number))).all())
            assert [attempt.attempt_number for attempt in attempts] == [1, 2]
            assert attempts[1].status == "succeeded"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_expired_lease_during_build_fences_worker_and_reclaims_after_unlock(
    monkeypatch,
):
    """A second connection skips the build lock; reclaim succeeds after Tx2 rolls back."""
    from src.ingestion import reindex_worker
    from src.ingestion.derived_build import build_chunk_derived_set

    engine = create_async_engine(_lease_concurrency_database_url())
    build_started = asyncio.Event()
    finish_build = asyncio.Event()
    base = datetime.now(timezone.utc)
    now_values = iter((base, base + timedelta(seconds=1), base + timedelta(seconds=61)))
    monkeypatch.setattr(reindex_worker, "_now", lambda: next(now_values))

    async def paused_build(*args, **kwargs):
        build_started.set()
        await finish_build.wait()
        return await build_chunk_derived_set(*args, **kwargs)

    worker_task = None
    try:
        # Keep fixtures from earlier runs from winning this global claim. The
        # dedicated DB is intentionally committed across connections, so park
        # existing queued work before seeding this race's target.
        async with AsyncSession(engine, expire_on_commit=False) as cleanup_session:
            cleanup_cutoff = base + timedelta(days=1)
            while await reindex_worker.reclaim_expired_chunk_reindex_item(
                cleanup_session, now=cleanup_cutoff,
            ) is not None:
                pass
            await cleanup_session.execute(
                update(ReindexJobItem)
                .where(ReindexJobItem.processing_status == "queued")
                .values(
                    next_retry_at=base + timedelta(days=2),
                    updated_at=base,
                )
            )
            await cleanup_session.commit()

        async with AsyncSession(engine, expire_on_commit=False) as seed_session:
            _, _, approval = await _enqueue_approval(seed_session)
            job = await seed_session.get(ReindexJob, approval.reindex_job_id)
            # The dedicated DB may retain fixtures from earlier concurrency
            # runs; make this job the deterministic first claim candidate.
            job.submitted_at = datetime(1900, 1, 1, tzinfo=timezone.utc)
            await seed_session.commit()
            job_id = approval.reindex_job_id

        async with AsyncSession(engine, expire_on_commit=False) as worker_session, \
                AsyncSession(engine, expire_on_commit=False) as reclaimer_session:
            item = await worker_session.scalar(select(ReindexJobItem).where(
                ReindexJobItem.reindex_job_id == job_id
            ))
            item_id = item.id
            monkeypatch.setattr(reindex_worker, "build_chunk_derived_set", paused_build)

            worker_task = asyncio.create_task(
                reindex_worker.process_next_chunk_reindex_item(worker_session)
            )
            await asyncio.wait_for(build_started.wait(), timeout=10)

            # The worker still holds its item lock while building. SKIP LOCKED
            # must make the concurrent reclaimer return instead of block.
            locked_attempt = await reclaimer_session.scalar(
                select(ReindexJobItemAttempt).where(
                    ReindexJobItemAttempt.reindex_job_item_id == item_id,
                    ReindexJobItemAttempt.status == "running",
                )
            )
            assert locked_attempt.lease_expires_at == base + timedelta(seconds=60)
            assert locked_attempt.lease_expires_at <= base + timedelta(seconds=61)
            async with engine.connect() as observer:
                observer_transaction = await observer.begin()
                try:
                    with pytest.raises(DBAPIError):
                        await observer.execute(
                            select(ReindexJobItem)
                            .where(ReindexJobItem.id == item_id)
                            .with_for_update(nowait=True)
                        )
                finally:
                    await observer_transaction.rollback()

            while True:
                reclaimed_id = await asyncio.wait_for(
                    reindex_worker.reclaim_expired_chunk_reindex_item(
                        reclaimer_session,
                        now=base + timedelta(seconds=61),
                    ),
                    timeout=5,
                )
                if reclaimed_id is None:
                    break
                assert reclaimed_id != item_id, "reclaimer claimed the locked build item"

            finish_build.set()
            assert await asyncio.wait_for(worker_task, timeout=10) == item_id
            worker_task = None

            # The worker's final strict lease check fences it even though the
            # reclaimer had not yet acquired the locked row during the build.
            item = await reclaimer_session.get(ReindexJobItem, item_id)
            assert item.processing_status == "running"
            assert await reindex_worker.reclaim_expired_chunk_reindex_item(
                reclaimer_session,
                now=base + timedelta(seconds=61),
            ) == item_id
            await reclaimer_session.refresh(item)
            assert item.processing_status == "queued"
            assert item.active_attempt_id is None
            assert item.next_retry_at == base + timedelta(seconds=91)

            attempts = list((await reclaimer_session.scalars(
                select(ReindexJobItemAttempt).where(
                    ReindexJobItemAttempt.reindex_job_item_id == item_id
                )
            )).all())
            assert len(attempts) == 1
            assert (attempts[0].status, attempts[0].error_code) == ("failed", "lease_expired")
            assert await reclaimer_session.scalar(select(func.count()).select_from(DerivedSet).where(
                DerivedSet.organization_id == item.organization_id,
                DerivedSet.document_version_id == item.source_document_version_id,
            )) == 0
            assert await reclaimer_session.scalar(select(func.count()).select_from(Chunk).join(
                DerivedSet, DerivedSet.id == Chunk.derived_set_id,
            ).where(
                DerivedSet.organization_id == item.organization_id,
                DerivedSet.document_version_id == item.source_document_version_id,
            )) == 0
    finally:
        if worker_task is not None:
            finish_build.set()
            try:
                await worker_task
            except Exception:
                pass
        await engine.dispose()


@pytest.mark.asyncio
async def test_third_expired_attempt_terminalizes_once(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, _, approval = await _enqueue_approval(session)
            clock = datetime.now(timezone.utc)
            for number in range(1, 4):
                claim = await claim_next_chunk_reindex_item(session, now=clock)
                assert claim is not None and claim.attempt_number == number
                await reclaim_expired_chunk_reindex_item(
                    session, now=clock + timedelta(seconds=61),
                )
                item = await session.get(ReindexJobItem, claim.item_id)
                if number < 3:
                    assert item.processing_status == "queued"
                    assert item.next_retry_at is not None
                    clock = item.next_retry_at + timedelta(seconds=1)
                else:
                    assert item.processing_status == "failed"
                    assert item.error_code == "lease_expired"
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.reindex_job_id == approval.reindex_job_id,
                AuditEvent.event_type == "reindex.item.failed",
            )) == 1
        finally:
            await session.close()
            await transaction.rollback()

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.review import submit_review_decision
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.ingestion.reindex_worker import process_next_chunk_reindex_item
from src.models.storage import (
    AuditEvent, Chunk, DerivedConfiguration, DerivedSet, Document, DocumentVersion, ReindexJob,
    ReindexJobItem, ReindexJobItemAttempt,
)
from tests.test_document_review_decisions import _seed


async def _enqueue_approval(session):
    organization_id, _, reviewer_id, ingested, configuration = await _seed(session)
    from src.ingestion.chunking import CHUNKING_VERSION
    configuration.chunking_version = CHUNKING_VERSION
    configuration.configuration_payload = {"max_characters": 1200}
    await session.flush()
    result = await submit_review_decision(
        session, organization_id=organization_id,
        document_pk=ingested.document_pk,
        expected_version_id=ingested.document_version_id,
        reviewer_principal_id=reviewer_id, decision="approve",
        reason="Reviewed", request_id=f"worker-{uuid4()}",
        idempotency_key=f"worker-key-{uuid4()}",
        selected_configuration_id=configuration.id,
    )
    return organization_id, ingested.document_pk, result


async def _enqueue_two_item_job(session):
    org_id, _, first_approval = await _enqueue_approval(session)
    reviewer_id = first_approval.decision.reviewer_principal_id
    config_id = (await session.get(ReindexJob, first_approval.reindex_job_id)).configuration_id
    config = await session.get(DerivedConfiguration, config_id)
    second_path = Path(__file__).parents[1] / "data" / "sample-vault" / "payment-service" / "services" / "checkout-service.md"
    parsed = validate_markdown_document(second_path.read_text(encoding="utf-8"), source_uri=second_path.as_uri())
    source = parsed.model_copy(update={"frontmatter": parsed.frontmatter.model_copy(update={"review_state": "in_review"})})
    author_id = await session.scalar(select(DocumentVersion.created_by_principal_id).where(
        DocumentVersion.id == first_approval.decision.reviewed_version_id
    ))
    second_ingested = await ingest_validated_document(
        session, organization_id=org_id,
        actor_principal_id=author_id,
        document=source,
    )
    # The first version's author is the independent reviewer for this fixture.
    second_result = await submit_review_decision(
        session, organization_id=org_id, document_pk=second_ingested.document_pk,
        expected_version_id=second_ingested.document_version_id,
        reviewer_principal_id=reviewer_id, decision="approve", reason="Approved",
        request_id=f"multi-{uuid4()}", idempotency_key=f"multi-key-{uuid4()}",
        selected_configuration_id=config.id,
    )
    original_job = await session.get(ReindexJob, first_approval.reindex_job_id)
    original_item = await session.scalar(select(ReindexJobItem).where(
        ReindexJobItem.reindex_job_id == original_job.id
    ))
    original_job.requested_document_count = 2
    original_item.created_at = datetime.now(timezone.utc)
    session.add(ReindexJobItem(
        organization_id=org_id, reindex_job_id=original_job.id,
        document_pk=second_ingested.document_pk,
        source_document_version_id=second_result.resulting_version.id,
        processing_status="queued",
        created_at=original_item.created_at + timedelta(seconds=1),
    ))
    await session.flush()
    # Remove the second approval's own queued work; only the two-item fixture job remains.
    second_job = await session.get(ReindexJob, second_result.reindex_job_id)
    second_job.status = "failed"
    second_job.started_at = datetime.now(timezone.utc)
    second_job.completed_at = datetime.now(timezone.utc)
    second_item = await session.scalar(select(ReindexJobItem).where(
        ReindexJobItem.reindex_job_id == second_job.id
    ))
    second_item.processing_status = "failed"
    second_item.outcome = "failed"
    second_item.error_code = "fixture_superseded"
    second_item.completed_at = datetime.now(timezone.utc)
    return org_id, original_job.id


@pytest.mark.asyncio
async def test_worker_builds_activates_and_completes_approval_job(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org_id, document_pk, approval = await _enqueue_approval(session)
            item_id = await process_next_chunk_reindex_item(session)
            assert item_id is not None
            item = await session.get(ReindexJobItem, item_id)
            job = await session.get(ReindexJob, approval.reindex_job_id)
            assert (item.processing_status, item.outcome) == ("succeeded", "rebuilt")
            assert job.status == "completed"
            assert await session.scalar(select(func.count()).select_from(ReindexJobItemAttempt).where(
                ReindexJobItemAttempt.reindex_job_item_id == item_id,
                ReindexJobItemAttempt.status == "succeeded",
            )) == 1
            derived = await session.get(DerivedSet, item.output_derived_set_id)
            assert derived.is_active and derived.document_version_id == approval.resulting_version.id
            assert await session.scalar(select(func.count()).select_from(Chunk).where(Chunk.derived_set_id == derived.id)) > 0
            event = await session.scalar(select(AuditEvent).where(
                AuditEvent.reindex_job_id == job.id,
                AuditEvent.event_type == "reindex.item.activated",
            ))
            assert event is not None
            assert "content" not in event.details and "path" not in event.details
        finally:
            await session.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_worker_skips_when_source_version_is_stale(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, document_pk, approval = await _enqueue_approval(session)
            document = await session.get(Document, document_pk)
            original = approval.resulting_version
            changed = DocumentVersion(
                id=uuid4(), organization_id=original.organization_id,
                document_pk=original.document_pk, parent_version_id=original.id,
                version_number=original.version_number + 1,
                schema_version=original.schema_version,
                canonicalization_version=original.canonicalization_version,
                hash_algorithm=original.hash_algorithm,
                content_hash="f" * 64, title=original.title,
                owner_team=original.owner_team, source_uri=original.source_uri,
                source_refs=list(original.source_refs), tags=list(original.tags),
                markdown_body=original.markdown_body,
                frontmatter_snapshot=dict(original.frontmatter_snapshot),
                document_status="active", review_state="approved",
                provenance_type=original.provenance_type,
                access_scope=original.access_scope, effective_from=None,
                effective_until=None, change_kind="content_update",
                created_by_principal_id=original.created_by_principal_id,
            )
            session.add(changed)
            await session.flush()
            document.current_version_id = changed.id
            await session.flush()
            item_id = await process_next_chunk_reindex_item(session)
            item = await session.get(ReindexJobItem, item_id)
            assert item.outcome == "skipped" and item.skip_code == "source_version_changed"
            assert await session.scalar(select(func.count()).select_from(DerivedSet).where(
                DerivedSet.organization_id == original.organization_id,
                DerivedSet.document_version_id == original.id,
                DerivedSet.is_active.is_(True),
            )) == 0
        finally:
            await session.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_worker_rolls_back_partial_build_and_marks_failed(migration_database_engine, monkeypatch):
    from src.ingestion import reindex_worker

    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, _, approval = await _enqueue_approval(session)

            async def fail_build(*args, **kwargs):
                raise RuntimeError("private path and body must not leak")

            monkeypatch.setattr(reindex_worker, "build_chunk_derived_set", fail_build)
            item_id = await process_next_chunk_reindex_item(session)
            item = await session.get(ReindexJobItem, item_id)
            assert item.processing_status == "failed"
            assert item.error_code == "chunk_build_failed"
            attempts = list((await session.scalars(select(ReindexJobItemAttempt).where(
                ReindexJobItemAttempt.reindex_job_item_id == item_id
            ))).all())
            assert len(attempts) == 1
            assert attempts[0].status == "failed"
            assert attempts[0].retryable is False
            job = await session.get(ReindexJob, approval.reindex_job_id)
            assert job.status == "failed"
            assert await session.scalar(select(func.count()).select_from(DerivedSet)) == 0
            assert await session.scalar(select(func.count()).select_from(Chunk)) == 0
            event = await session.scalar(select(AuditEvent).where(
                AuditEvent.reindex_job_id == approval.reindex_job_id,
                AuditEvent.event_type == "reindex.item.failed",
            ))
            assert event is not None
            assert "private" not in str(event.details)
        finally:
            await session.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_chunk_worker_does_not_claim_embedding_job(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, _, approval = await _enqueue_approval(session)
            job = await session.get(ReindexJob, approval.reindex_job_id)
            job.targets = ["embeddings"]
            job_id = job.id
            await session.flush()
            await session.commit()
            assert await process_next_chunk_reindex_item(session) is None
            item = await session.scalar(select(ReindexJobItem).where(
                ReindexJobItem.reindex_job_id == job_id
            ))
            assert item.processing_status == "queued"
            assert await session.scalar(select(ReindexJob.status).where(ReindexJob.id == job_id)) == "queued"
        finally:
            await session.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_worker_skips_when_matching_active_set_already_exists(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, document_pk, approval = await _enqueue_approval(session)
            from src.ingestion.derived_build import build_chunk_derived_set
            from src.ingestion.derived_activation import activate_derived_set

            job = await session.get(ReindexJob, approval.reindex_job_id)
            existing = await build_chunk_derived_set(
                session, organization_id=job.organization_id,
                document_pk=document_pk,
                document_version_id=approval.resulting_version.id,
                configuration_id=job.configuration_id,
            )
            await activate_derived_set(
                session, organization_id=job.organization_id,
                derived_set_id=existing.id,
            )
            before_sets = await session.scalar(select(func.count()).select_from(DerivedSet))
            before_chunks = await session.scalar(select(func.count()).select_from(Chunk))

            item_id = await process_next_chunk_reindex_item(session)
            item = await session.get(ReindexJobItem, item_id)
            event = await session.scalar(select(AuditEvent).where(
                AuditEvent.reindex_job_id == job.id,
                AuditEvent.event_type == "reindex.item.skipped",
            ))
            assert item.processing_status == "succeeded"
            assert item.outcome == "skipped" and item.skip_code == "already_current"
            assert event is not None
            assert await session.scalar(select(func.count()).select_from(DerivedSet)) == before_sets
            assert await session.scalar(select(func.count()).select_from(Chunk)) == before_chunks
        finally:
            await session.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_skip_locked_prevents_two_sessions_claiming_same_item(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        first = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            await _enqueue_approval(first)
            claimed = await process_next_chunk_reindex_item(first)
            assert claimed is not None
            async with migration_database_engine.connect() as second_connection:
                second_tx = await second_connection.begin()
                second = AsyncSession(
                    bind=second_connection, expire_on_commit=False,
                    join_transaction_mode="create_savepoint",
                )
                try:
                    assert await process_next_chunk_reindex_item(second) is None
                finally:
                    await second.close()
                    await second_tx.rollback()
        finally:
            await first.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_retryable_failure_is_delayed_then_succeeds_without_duplicate_output(
    migration_database_engine, monkeypatch,
):
    from src.ingestion import reindex_worker
    real_build = reindex_worker.build_chunk_derived_set
    calls = 0

    async def fail_once_then_build(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise reindex_worker.RetryableReindexError()
        return await real_build(*args, **kwargs)

    monkeypatch.setattr(reindex_worker, "build_chunk_derived_set", fail_once_then_build)
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, _, approval = await _enqueue_approval(session)
            item_id = await process_next_chunk_reindex_item(session)
            item = await session.get(ReindexJobItem, item_id)
            assert item.processing_status == "queued"
            assert item.next_retry_at is not None
            item.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.flush()
            assert await process_next_chunk_reindex_item(session) == item_id
            attempts = list((await session.scalars(select(ReindexJobItemAttempt).where(
                ReindexJobItemAttempt.reindex_job_item_id == item_id
            ).order_by(ReindexJobItemAttempt.attempt_number))).all())
            assert [a.status for a in attempts] == ["failed", "succeeded"]
            assert [a.retryable for a in attempts] == [True, False]
            item = await session.get(ReindexJobItem, item_id)
            job = await session.get(ReindexJob, approval.reindex_job_id)
            derived = await session.get(DerivedSet, item.output_derived_set_id)
            assert (item.processing_status, item.outcome) == ("succeeded", "rebuilt")
            assert derived.is_active is True
            assert job.status == "completed"
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.reindex_job_id == approval.reindex_job_id,
                AuditEvent.event_type == "reindex.item.activated",
            )) == 1
            assert await session.scalar(select(func.count()).select_from(DerivedSet)) == 1
            assert await session.scalar(select(func.count()).select_from(Chunk)) > 0
        finally:
            await session.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_three_retryable_failures_end_item_and_job_failed_once(migration_database_engine, monkeypatch):
    from src.ingestion import reindex_worker

    async def always_transient(*args, **kwargs):
        raise reindex_worker.RetryableReindexError()

    monkeypatch.setattr(reindex_worker, "build_chunk_derived_set", always_transient)
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, _, approval = await _enqueue_approval(session)
            item_id = None
            for _ in range(3):
                item_id = await process_next_chunk_reindex_item(session)
                item = await session.get(ReindexJobItem, item_id)
                if item.processing_status == "queued":
                    item.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    await session.flush()
            item = await session.get(ReindexJobItem, item_id)
            attempts = list((await session.scalars(select(ReindexJobItemAttempt).where(
                ReindexJobItemAttempt.reindex_job_item_id == item_id
            ).order_by(ReindexJobItemAttempt.attempt_number))).all())
            job = await session.get(ReindexJob, approval.reindex_job_id)
            assert len(attempts) == 3
            assert [attempt.status for attempt in attempts] == ["failed", "failed", "failed"]
            assert [a.retryable for a in attempts] == [True, True, False]
            assert attempts[-1].status == "failed"
            assert item.processing_status == "failed" and item.outcome == "failed"
            assert item.error_code == "transient_chunk_build_failure"
            assert job.status == "failed"
            assert await session.scalar(select(func.count()).select_from(DerivedSet)) == 0
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.reindex_job_id == job.id,
                AuditEvent.event_type == "reindex.item.failed",
            )) == 1
        finally:
            await session.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_multi_item_job_derives_running_then_completed_with_errors(
    migration_database_engine, monkeypatch,
):
    from src.ingestion import reindex_worker
    real_build = reindex_worker.build_chunk_derived_set
    calls = 0

    async def succeed_first_then_fail(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("fixture build failure")
        return await real_build(*args, **kwargs)

    monkeypatch.setattr(reindex_worker, "build_chunk_derived_set", succeed_first_then_fail)
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, job_id = await _enqueue_two_item_job(session)
            first_item_id = await process_next_chunk_reindex_item(session)
            job = await session.get(ReindexJob, job_id)
            assert job.status == "running" and job.completed_at is None
            first_item = await session.get(ReindexJobItem, first_item_id)
            assert (first_item.outcome, first_item.processing_status) == ("rebuilt", "succeeded")

            second_item_id = await process_next_chunk_reindex_item(session)
            second_item = await session.get(ReindexJobItem, second_item_id)
            await session.refresh(job)
            assert second_item.outcome == "failed"
            assert job.status == "completed_with_errors"
            assert job.completed_at is not None
            events = list((await session.scalars(select(AuditEvent).where(
                AuditEvent.reindex_job_id == job_id,
                AuditEvent.event_type.in_(["reindex.item.activated", "reindex.item.failed", "reindex.item.skipped"]),
            ))).all())
            assert len(events) == 2
            assert len({event.details["item_id"] for event in events}) == 2
            assert await session.scalar(select(func.count()).select_from(DerivedSet).where(
                DerivedSet.is_active.is_(True),
            )) == 1
        finally:
            await session.close()
            await tx.rollback()


@pytest.mark.asyncio
async def test_multi_item_skipped_and_rebuilt_job_completes_without_errors(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        tx = await connection.begin()
        session = AsyncSession(
            bind=connection, expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            _, job_id = await _enqueue_two_item_job(session)
            items = list((await session.scalars(select(ReindexJobItem).where(
                ReindexJobItem.reindex_job_id == job_id
            ).order_by(ReindexJobItem.created_at))).all())
            from src.ingestion.derived_activation import activate_derived_set
            from src.ingestion.derived_build import build_chunk_derived_set

            job = await session.get(ReindexJob, job_id)
            existing = await build_chunk_derived_set(
                session,
                organization_id=job.organization_id,
                document_pk=items[0].document_pk,
                document_version_id=items[0].source_document_version_id,
                configuration_id=job.configuration_id,
            )
            await activate_derived_set(
                session,
                organization_id=job.organization_id,
                derived_set_id=existing.id,
            )

            first_id = await process_next_chunk_reindex_item(session)
            first = await session.get(ReindexJobItem, first_id)
            job = await session.get(ReindexJob, job_id)
            assert first.outcome == "skipped"
            assert first.skip_code == "already_current"
            assert job.status == "running" and job.completed_at is None
            second_id = await process_next_chunk_reindex_item(session)
            second = await session.get(ReindexJobItem, second_id)
            await session.refresh(job)
            assert second.outcome == "rebuilt"
            assert job.status == "completed" and job.completed_at is not None
            events = list((await session.scalars(select(AuditEvent).where(
                AuditEvent.reindex_job_id == job_id,
                AuditEvent.event_type.in_(["reindex.item.activated", "reindex.item.failed", "reindex.item.skipped"]),
            ))).all())
            assert len(events) == 2
            assert len({event.details["item_id"] for event in events}) == 2
        finally:
            await session.close()
            await tx.rollback()

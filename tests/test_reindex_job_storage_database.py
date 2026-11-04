from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
    AuditEvent,
    DerivedConfiguration,
    Organization,
    Principal,
    ReindexJob,
    ReindexJobItem,
    ReindexJobItemAttempt,
)

SAMPLE_DOCUMENT = Path(__file__).parents[1] / "data" / "sample-vault" / "payment-service" / "services" / "fraud-service.md"


async def _seed(session):
    organization_id, principal_id = uuid4(), uuid4()
    session.add_all([
        Organization(id=organization_id, organization_key=f"org-{organization_id}", display_name="Reindex test", status="active"),
        Principal(id=principal_id, organization_id=organization_id, principal_key=f"user-{principal_id}", principal_kind="user", status="active"),
    ])
    await session.flush()
    document = validate_markdown_document(SAMPLE_DOCUMENT.read_text(encoding="utf-8"), source_uri=SAMPLE_DOCUMENT.as_uri())
    created = await ingest_validated_document(session, organization_id=organization_id, actor_principal_id=principal_id, document=document)
    configuration = DerivedConfiguration(organization_id=organization_id, configuration_key=f"config-{organization_id}", chunking_version="v1", configuration_payload={}, configuration_hash="a" * 64, status="active", created_by_principal_id=principal_id)
    session.add(configuration)
    await session.flush()
    return organization_id, principal_id, created, configuration


@pytest.mark.asyncio
async def test_reindex_storage_enforces_state_scope_and_audit_fk(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, principal_id, created, configuration = await _seed(session)
            job = ReindexJob(organization_id=organization_id, request_id="req-1", idempotency_key_hash="b" * 64, request_payload_hash="c" * 64, submitted_by_principal_id=principal_id, scope_type="documents", targets=["chunks"], reason="rebuild", configuration_id=configuration.id, requested_document_count=1, status="queued")
            session.add(job)
            await session.flush()
            item = ReindexJobItem(organization_id=organization_id, reindex_job_id=job.id, document_pk=created.document_pk, source_document_version_id=created.document_version_id, processing_status="queued")
            session.add(item)
            await session.flush()
            started_at = datetime.now(timezone.utc)
            attempt = ReindexJobItemAttempt(organization_id=organization_id, reindex_job_item_id=item.id, attempt_number=1, status="running", retryable=True, started_at=started_at, lease_expires_at=started_at + timedelta(seconds=60))
            session.add(attempt)
            await session.flush()
            item.processing_status = "running"
            item.active_attempt_id = attempt.id
            event = AuditEvent(organization_id=organization_id, event_type="reindex.job.accepted", outcome="succeeded", actor_kind="principal", actor_principal_id=principal_id, request_id="req-1", reindex_job_id=job.id, details={}, occurred_at=datetime.now(timezone.utc))
            session.add_all([attempt, event])
            await session.flush()

            with pytest.raises(IntegrityError, match="ck_reindex_jobs_targets_unique"):
                async with session.begin_nested():
                    session.add(ReindexJob(organization_id=organization_id, request_id="req-2", idempotency_key_hash="d" * 64, request_payload_hash="e" * 64, submitted_by_principal_id=principal_id, scope_type="documents", targets=["chunks", "chunks"], reason="rebuild", configuration_id=configuration.id, requested_document_count=1, status="queued"))
                    await session.flush()
            with pytest.raises(IntegrityError, match="ck_reindex_job_items_processing_state_consistent"):
                async with session.begin_nested():
                    bad_job = ReindexJob(organization_id=organization_id, request_id="req-3", idempotency_key_hash="f" * 64, request_payload_hash="0" * 64, submitted_by_principal_id=principal_id, scope_type="documents", targets=["chunks"], reason="rebuild", configuration_id=configuration.id, requested_document_count=1, status="queued")
                    session.add(bad_job)
                    await session.flush()
                    session.add(ReindexJobItem(organization_id=organization_id, reindex_job_id=bad_job.id, document_pk=created.document_pk, source_document_version_id=created.document_version_id, processing_status="succeeded", outcome="rebuilt", completed_at=datetime.now(timezone.utc)))
                    await session.flush()

            other_organization_id = uuid4()
            session.add(Organization(id=other_organization_id, organization_key=f"other-{other_organization_id}", display_name="Other", status="active"))
            await session.flush()
            with pytest.raises(IntegrityError, match="fk_audit_events_reindex_job"):
                async with session.begin_nested():
                    session.add(AuditEvent(organization_id=other_organization_id, event_type="reindex.accepted", outcome="succeeded", actor_kind="system", request_id="other", reindex_job_id=job.id, details={}, occurred_at=datetime.now(timezone.utc)))
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_app_role_reindex_permissions(database_engine):
    async with database_engine.connect() as connection:
        for table_name in ("reindex_jobs", "reindex_job_items", "reindex_job_item_attempts"):
            for privilege in ("SELECT", "INSERT", "UPDATE"):
                assert await connection.scalar(text("SELECT has_table_privilege(current_user, :table_name, :privilege)"), {"table_name": table_name, "privilege": privilege}) is True
            assert await connection.scalar(text("SELECT has_table_privilege(current_user, :table_name, 'DELETE')"), {"table_name": table_name}) is False
            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await connection.execute(text(f"DELETE FROM {table_name} WHERE false"))
            finally:
                await savepoint.rollback()

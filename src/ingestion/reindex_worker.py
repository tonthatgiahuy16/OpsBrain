"""Single-item chunk reindex worker with atomic terminal bookkeeping."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.derived_activation import activate_derived_set
from src.ingestion.derived_build import build_chunk_derived_set
from src.ingestion.eligibility import is_pre_acl_retrieval_eligible
from src.models.storage import (
    AuditEvent, DerivedConfiguration, DerivedSet, Document, DocumentVersion, Organization,
    ReindexJob, ReindexJobItem, ReindexJobItemAttempt,
)


class RetryableReindexError(Exception):
    """Signals a transient failure safe to retry immediately."""


async def process_next_chunk_reindex_item(session: AsyncSession) -> UUID | None:
    """Claim and process at most one queued item in the caller transaction."""
    now = datetime.now(timezone.utc)
    async with session.begin_nested():
        item = await session.scalar(
            select(ReindexJobItem)
            .join(ReindexJob, ReindexJob.id == ReindexJobItem.reindex_job_id)
            .where(
                ReindexJobItem.processing_status == "queued",
                ReindexJob.status.in_(["queued", "running"]),
                ReindexJob.targets == ["chunks"],
            )
            .order_by(ReindexJob.submitted_at, ReindexJobItem.created_at)
            .with_for_update(of=ReindexJobItem, skip_locked=True)
            .limit(1)
        )
        if item is None:
            return None
        job = await session.scalar(select(ReindexJob).where(
            ReindexJob.organization_id == item.organization_id,
            ReindexJob.id == item.reindex_job_id,
        ).with_for_update())
        if job is None:
            return None
        job.status = "running"
        job.started_at = job.started_at or now
        item.processing_status = "running"
        item.updated_at = now
        outcome = "rebuilt"
        skip_code = None
        derived_set = None
        terminal_error = None
        for attempt_number in range(1, 4):
            attempt = ReindexJobItemAttempt(
                organization_id=item.organization_id,
                reindex_job_item_id=item.id,
                attempt_number=attempt_number, status="running", retryable=False,
                started_at=datetime.now(timezone.utc),
            )
            session.add(attempt)
            await session.flush()
            try:
                async with session.begin_nested():
                    organization = await session.scalar(select(Organization).where(
                        Organization.id == item.organization_id
                    ))
                    document = await session.scalar(select(Document).where(
                        Document.organization_id == item.organization_id,
                        Document.id == item.document_pk,
                    ).with_for_update())
                    version = await session.scalar(select(DocumentVersion).where(
                        DocumentVersion.organization_id == item.organization_id,
                        DocumentVersion.document_pk == item.document_pk,
                        DocumentVersion.id == item.source_document_version_id,
                    ))
                    configuration = await session.scalar(select(DerivedConfiguration).where(
                        DerivedConfiguration.organization_id == item.organization_id,
                        DerivedConfiguration.id == job.configuration_id,
                    ))
                    if document is None or document.current_version_id != item.source_document_version_id:
                        outcome, skip_code = "skipped", "source_version_changed"
                    elif not is_pre_acl_retrieval_eligible(organization, document, version, now):
                        outcome, skip_code = "skipped", "document_not_eligible"
                    elif configuration is None or configuration.status != "active":
                        outcome, skip_code = "skipped", "configuration_not_applicable"
                    else:
                        current_active_set = await session.scalar(select(DerivedSet.id).where(
                            DerivedSet.organization_id == item.organization_id,
                            DerivedSet.document_version_id == item.source_document_version_id,
                            DerivedSet.configuration_id == job.configuration_id,
                            DerivedSet.status == "ready",
                            DerivedSet.is_active.is_(True),
                        ))
                        if current_active_set is not None:
                            outcome, skip_code = "skipped", "already_current"
                        else:
                            derived_set = await build_chunk_derived_set(
                                session, organization_id=item.organization_id,
                                document_pk=item.document_pk,
                                document_version_id=item.source_document_version_id,
                                configuration_id=job.configuration_id,
                            )
                            await activate_derived_set(
                                session, organization_id=item.organization_id,
                                derived_set_id=derived_set.id,
                            )
            except RetryableReindexError:
                attempt.status = "failed"
                attempt.retryable = attempt_number < 3
                attempt.error_code = "transient_chunk_build_failure"
                attempt.completed_at = datetime.now(timezone.utc)
                if attempt_number < 3:
                    continue
                terminal_error = "transient_chunk_build_failure"
            except Exception:
                attempt.status = "failed"
                attempt.retryable = False
                attempt.error_code = "chunk_build_failed"
                attempt.completed_at = datetime.now(timezone.utc)
                terminal_error = "chunk_build_failed"
            else:
                attempt.status = "succeeded"
                attempt.retryable = False
                attempt.completed_at = datetime.now(timezone.utc)
            break

        if terminal_error is not None:
            item.processing_status = "failed"
            item.outcome = "failed"
            item.error_code = terminal_error
            item.completed_at = datetime.now(timezone.utc)
            event_type = "reindex.item.failed"
        else:
            item.processing_status = "succeeded"
            item.outcome = outcome
            item.skip_code = skip_code
            item.output_derived_set_id = derived_set.id if outcome == "rebuilt" else None
            item.completed_at = attempt.completed_at
            event_type = "reindex.item.activated" if outcome == "rebuilt" else "reindex.item.skipped"

        item.updated_at = datetime.now(timezone.utc)
        session.add(AuditEvent(
            organization_id=item.organization_id,
            event_type=event_type,
            outcome="failed" if item.outcome == "failed" else "succeeded",
            actor_kind="system", request_id=job.request_id,
            document_pk=item.document_pk,
            document_version_id=item.source_document_version_id,
            reindex_job_id=job.id,
            details={"item_id": str(item.id), "code": item.error_code or item.skip_code or "rebuilt"},
            occurred_at=item.completed_at,
        ))
        await session.flush()
        remaining = await session.scalar(select(func.count()).select_from(ReindexJobItem).where(
            ReindexJobItem.organization_id == job.organization_id,
            ReindexJobItem.reindex_job_id == job.id,
            ReindexJobItem.processing_status.in_(["queued", "running"]),
        ))
        if remaining == 0:
            failures = await session.scalar(select(func.count()).select_from(ReindexJobItem).where(
                ReindexJobItem.organization_id == job.organization_id,
                ReindexJobItem.reindex_job_id == job.id,
                ReindexJobItem.processing_status == "failed",
            ))
            successes = await session.scalar(select(func.count()).select_from(ReindexJobItem).where(
                ReindexJobItem.organization_id == job.organization_id,
                ReindexJobItem.reindex_job_id == job.id,
                ReindexJobItem.processing_status == "succeeded",
            ))
            job.status = "failed" if failures and not successes else "completed_with_errors" if failures else "completed"
            job.completed_at = datetime.now(timezone.utc)
        await session.flush()
        return item.id

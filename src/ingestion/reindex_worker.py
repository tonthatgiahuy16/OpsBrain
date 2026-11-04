"""Lease-fenced worker for deterministic chunk reindex work.

Tx1 claims and commits an attempt before any build.  Tx2 can finalize only
while that persisted attempt is still the item's active, unexpired token.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.derived_activation import activate_derived_set
from src.ingestion.derived_build import build_chunk_derived_set
from src.ingestion.eligibility import is_pre_acl_retrieval_eligible
from src.models.storage import (
    AuditEvent, DerivedConfiguration, DerivedSet, Document, DocumentVersion,
    Organization, ReindexJob, ReindexJobItem, ReindexJobItemAttempt,
)


MAX_ATTEMPTS = 3
LEASE_SECONDS = 60
RETRY_DELAY_SECONDS = 30


class RetryableReindexError(Exception):
    """Signals a transient build failure that should be retried later."""


class _FencedAttempt(Exception):
    """The worker no longer owns this attempt or its strict lease expired."""


@dataclass(frozen=True)
class ClaimedChunkReindexItem:
    """The persisted hand-off from Tx1 to Tx2/Tx3."""

    item_id: UUID
    job_id: UUID
    organization_id: UUID
    document_pk: UUID
    source_document_version_id: UUID
    configuration_id: UUID
    attempt_id: UUID
    attempt_number: int
    lease_expires_at: datetime


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _derive_job_status(session: AsyncSession, job: ReindexJob, now: datetime) -> None:
    # Production sessions disable autoflush; counts must see the terminal item.
    await session.flush()
    remaining = await session.scalar(
        select(func.count()).select_from(ReindexJobItem).where(
            ReindexJobItem.organization_id == job.organization_id,
            ReindexJobItem.reindex_job_id == job.id,
            ReindexJobItem.processing_status.in_(["queued", "running"]),
        )
    )
    if remaining:
        job.status = "running"
        return
    failed = await session.scalar(
        select(func.count()).select_from(ReindexJobItem).where(
            ReindexJobItem.organization_id == job.organization_id,
            ReindexJobItem.reindex_job_id == job.id,
            ReindexJobItem.processing_status == "failed",
        )
    )
    succeeded = await session.scalar(
        select(func.count()).select_from(ReindexJobItem).where(
            ReindexJobItem.organization_id == job.organization_id,
            ReindexJobItem.reindex_job_id == job.id,
            ReindexJobItem.processing_status == "succeeded",
        )
    )
    job.status = "failed" if failed and not succeeded else (
        "completed_with_errors" if failed else "completed"
    )
    job.completed_at = now


async def claim_next_chunk_reindex_item(
    session: AsyncSession, *, now: datetime | None = None,
) -> ClaimedChunkReindexItem | None:
    """Tx1: claim one due chunk item and commit its 60-second lease."""
    claimed_at = now or _now()
    try:
        item = await session.scalar(
            select(ReindexJobItem)
            .join(ReindexJob, ReindexJob.id == ReindexJobItem.reindex_job_id)
            .where(
                ReindexJobItem.processing_status == "queued",
                (ReindexJobItem.next_retry_at.is_(None))
                | (ReindexJobItem.next_retry_at <= claimed_at),
                ReindexJob.status.in_(["queued", "running"]),
                ReindexJob.targets == ["chunks"],
            )
            .order_by(ReindexJob.submitted_at, ReindexJobItem.created_at)
            .with_for_update(of=ReindexJobItem, skip_locked=True)
            .limit(1)
        )
        if item is None:
            await session.rollback()
            return None
        job = await session.scalar(
            select(ReindexJob).where(
                ReindexJob.organization_id == item.organization_id,
                ReindexJob.id == item.reindex_job_id,
            ).with_for_update()
        )
        if job is None:
            await session.rollback()
            return None
        attempt_count = await session.scalar(
            select(func.count()).select_from(ReindexJobItemAttempt).where(
                ReindexJobItemAttempt.organization_id == item.organization_id,
                ReindexJobItemAttempt.reindex_job_item_id == item.id,
            )
        )
        if attempt_count >= MAX_ATTEMPTS:
            await session.rollback()
            return None

        attempt = ReindexJobItemAttempt(
            organization_id=item.organization_id,
            reindex_job_item_id=item.id,
            attempt_number=attempt_count + 1,
            status="running",
            retryable=False,
            started_at=claimed_at,
            lease_expires_at=claimed_at + timedelta(seconds=LEASE_SECONDS),
        )
        session.add(attempt)
        await session.flush()  # Must exist before the composite active-attempt FK.
        item.processing_status = "running"
        item.active_attempt_id = attempt.id
        item.next_retry_at = None
        item.updated_at = claimed_at
        job.status = "running"
        job.started_at = job.started_at or claimed_at
        await session.flush()
        claim = ClaimedChunkReindexItem(
            item_id=item.id, job_id=job.id, organization_id=item.organization_id,
            document_pk=item.document_pk,
            source_document_version_id=item.source_document_version_id,
            configuration_id=job.configuration_id, attempt_id=attempt.id,
            attempt_number=attempt.attempt_number,
            lease_expires_at=attempt.lease_expires_at,
        )
        await session.commit()
        return claim
    except Exception:
        await session.rollback()
        raise


async def _lock_claim(
    session: AsyncSession, claim: ClaimedChunkReindexItem, now: datetime,
) -> tuple[ReindexJobItem, ReindexJob, ReindexJobItemAttempt]:
    """Lock item -> job -> attempt and validate the fencing token."""
    item = await session.scalar(
        select(ReindexJobItem).where(
            ReindexJobItem.organization_id == claim.organization_id,
            ReindexJobItem.id == claim.item_id,
        ).with_for_update().execution_options(populate_existing=True)
    )
    job = await session.scalar(
        select(ReindexJob).where(
            ReindexJob.organization_id == claim.organization_id,
            ReindexJob.id == claim.job_id,
        ).with_for_update().execution_options(populate_existing=True)
    )
    attempt = await session.scalar(
        select(ReindexJobItemAttempt).where(
            ReindexJobItemAttempt.organization_id == claim.organization_id,
            ReindexJobItemAttempt.reindex_job_item_id == claim.item_id,
            ReindexJobItemAttempt.id == claim.attempt_id,
        ).with_for_update().execution_options(populate_existing=True)
    )
    if (
        item is None or job is None or attempt is None
        or item.processing_status != "running"
        or item.active_attempt_id != claim.attempt_id
        or attempt.status != "running"
        or attempt.lease_expires_at <= now
    ):
        raise _FencedAttempt()
    return item, job, attempt


async def _audit(
    session: AsyncSession, *, item: ReindexJobItem, job: ReindexJob,
    event_type: str, code: str, occurred_at: datetime,
) -> None:
    session.add(AuditEvent(
        organization_id=item.organization_id, event_type=event_type,
        outcome="failed" if event_type == "reindex.item.failed" else "succeeded",
        actor_kind="system", request_id=job.request_id,
        document_pk=item.document_pk,
        document_version_id=item.source_document_version_id,
        reindex_job_id=job.id,
        details={"item_id": str(item.id), "code": code},
        occurred_at=occurred_at,
    ))


async def _succeed(
    session: AsyncSession, *, item: ReindexJobItem, job: ReindexJob,
    attempt: ReindexJobItemAttempt, outcome: str, code: str | None,
    derived_set: DerivedSet | None, now: datetime,
) -> None:
    attempt.status, attempt.retryable, attempt.completed_at = "succeeded", False, now
    item.processing_status, item.active_attempt_id = "succeeded", None
    item.outcome = outcome
    item.skip_code = code if outcome == "skipped" else None
    item.output_derived_set_id = derived_set.id if outcome == "rebuilt" else None
    item.completed_at, item.updated_at = now, now
    await _audit(
        session, item=item, job=job,
        event_type="reindex.item.activated" if outcome == "rebuilt" else "reindex.item.skipped",
        code=code or "rebuilt", occurred_at=now,
    )
    await _derive_job_status(session, job, now)


async def _fail(
    session: AsyncSession, *, item: ReindexJobItem, job: ReindexJob,
    attempt: ReindexJobItemAttempt, code: str, now: datetime,
) -> None:
    attempt.status, attempt.retryable = "failed", False
    attempt.error_code, attempt.completed_at = code, now
    item.processing_status, item.active_attempt_id = "failed", None
    item.outcome, item.error_code = "failed", code
    item.completed_at, item.updated_at = now, now
    await _audit(session, item=item, job=job, event_type="reindex.item.failed", code=code, occurred_at=now)
    await _derive_job_status(session, job, now)


async def _finalize_build(
    session: AsyncSession, claim: ClaimedChunkReindexItem, *, now: datetime,
) -> UUID:
    """Tx2: build inactive data, then fence/recheck immediately before activation."""
    item, job, attempt = await _lock_claim(session, claim, now)
    organization = await session.scalar(select(Organization).where(Organization.id == claim.organization_id))
    document = await session.scalar(
        select(Document).where(
            Document.organization_id == claim.organization_id,
            Document.id == claim.document_pk,
        ).with_for_update().execution_options(populate_existing=True)
    )
    version = await session.scalar(select(DocumentVersion).where(
        DocumentVersion.organization_id == claim.organization_id,
        DocumentVersion.document_pk == claim.document_pk,
        DocumentVersion.id == claim.source_document_version_id,
    ))
    configuration = await session.scalar(select(DerivedConfiguration).where(
        DerivedConfiguration.organization_id == claim.organization_id,
        DerivedConfiguration.id == claim.configuration_id,
    ))
    if document is None or document.current_version_id != claim.source_document_version_id:
        await _succeed(session, item=item, job=job, attempt=attempt, outcome="skipped", code="source_version_changed", derived_set=None, now=now)
        return item.id
    if not is_pre_acl_retrieval_eligible(organization, document, version, now):
        await _succeed(session, item=item, job=job, attempt=attempt, outcome="skipped", code="document_not_eligible", derived_set=None, now=now)
        return item.id
    if configuration is None or configuration.status != "active":
        await _succeed(session, item=item, job=job, attempt=attempt, outcome="skipped", code="configuration_not_applicable", derived_set=None, now=now)
        return item.id
    active_set = await session.scalar(select(DerivedSet).where(
        DerivedSet.organization_id == claim.organization_id,
        DerivedSet.document_version_id == claim.source_document_version_id,
        DerivedSet.configuration_id == claim.configuration_id,
        DerivedSet.status == "ready", DerivedSet.is_active.is_(True),
    ).with_for_update())
    if active_set is not None:
        await _succeed(session, item=item, job=job, attempt=attempt, outcome="skipped", code="already_current", derived_set=None, now=now)
        return item.id

    # Keep incomplete output behind a savepoint until the mandatory final
    # eligibility check has passed.  A late skip must leave no inactive set or
    # chunks behind.
    build_savepoint = await session.begin_nested()
    derived_set = await build_chunk_derived_set(
        session, organization_id=claim.organization_id, document_pk=claim.document_pk,
        document_version_id=claim.source_document_version_id,
        configuration_id=claim.configuration_id,
    )
    final_now = _now()
    item, job, attempt = await _lock_claim(session, claim, final_now)
    document = await session.scalar(select(Document).where(
        Document.organization_id == claim.organization_id, Document.id == claim.document_pk,
    ).with_for_update().execution_options(populate_existing=True))
    version = await session.scalar(select(DocumentVersion).where(
        DocumentVersion.organization_id == claim.organization_id,
        DocumentVersion.document_pk == claim.document_pk,
        DocumentVersion.id == claim.source_document_version_id,
    ))
    organization = await session.scalar(select(Organization).where(Organization.id == claim.organization_id))
    if document is None or document.current_version_id != claim.source_document_version_id:
        await build_savepoint.rollback()
        await _succeed(session, item=item, job=job, attempt=attempt, outcome="skipped", code="source_version_changed", derived_set=None, now=final_now)
        return item.id
    if not is_pre_acl_retrieval_eligible(organization, document, version, final_now):
        await build_savepoint.rollback()
        await _succeed(session, item=item, job=job, attempt=attempt, outcome="skipped", code="document_not_eligible", derived_set=None, now=final_now)
        return item.id
    await build_savepoint.commit()
    await activate_derived_set(session, organization_id=claim.organization_id, derived_set_id=derived_set.id)
    await _succeed(session, item=item, job=job, attempt=attempt, outcome="rebuilt", code=None, derived_set=derived_set, now=final_now)
    return item.id


async def _record_failure(
    session: AsyncSession, claim: ClaimedChunkReindexItem, *, retryable: bool,
    code: str, now: datetime,
) -> UUID | None:
    try:
        item, job, attempt = await _lock_claim(session, claim, now)
    except _FencedAttempt:
        return None
    if retryable and attempt.attempt_number < MAX_ATTEMPTS:
        attempt.status, attempt.retryable = "failed", True
        attempt.error_code, attempt.completed_at = code, now
        item.processing_status, item.active_attempt_id = "queued", None
        item.next_retry_at = now + timedelta(seconds=RETRY_DELAY_SECONDS)
        item.updated_at, job.status = now, "running"
        return item.id
    await _fail(session, item=item, job=job, attempt=attempt, code=code, now=now)
    return item.id


async def process_next_chunk_reindex_item(session: AsyncSession) -> UUID | None:
    """Run Tx1 then either Tx2 success/skip or Tx3 delayed/terminal failure."""
    claim = await claim_next_chunk_reindex_item(session)
    if claim is None:
        return None
    try:
        item_id = await _finalize_build(session, claim, now=_now())
        await session.commit()
        return item_id
    except _FencedAttempt:
        await session.rollback()
        return claim.item_id
    except RetryableReindexError:
        await session.rollback()
        try:
            item_id = await _record_failure(session, claim, retryable=True, code="transient_chunk_build_failure", now=_now())
            await session.commit()
            return item_id
        except Exception:
            await session.rollback()
            raise
    except Exception:
        await session.rollback()
        try:
            item_id = await _record_failure(session, claim, retryable=False, code="chunk_build_failed", now=_now())
            await session.commit()
            return item_id
        except Exception:
            await session.rollback()
            raise


async def reclaim_expired_chunk_reindex_item(
    session: AsyncSession, *, now: datetime | None = None,
) -> UUID | None:
    """Recover one expired attempt; a later normal claim creates attempt k+1."""
    reclaimed_at = now or _now()
    try:
        item = await session.scalar(
            select(ReindexJobItem).join(
                ReindexJobItemAttempt,
                ReindexJobItemAttempt.id == ReindexJobItem.active_attempt_id,
            ).where(
                ReindexJobItem.processing_status == "running",
                ReindexJobItemAttempt.status == "running",
                ReindexJobItemAttempt.lease_expires_at <= reclaimed_at,
            ).order_by(ReindexJobItemAttempt.lease_expires_at, ReindexJobItem.id)
            .with_for_update(of=ReindexJobItem, skip_locked=True).limit(1)
        )
        if item is None:
            await session.rollback()
            return None
        job = await session.scalar(select(ReindexJob).where(
            ReindexJob.organization_id == item.organization_id,
            ReindexJob.id == item.reindex_job_id,
        ).with_for_update())
        attempt = await session.scalar(select(ReindexJobItemAttempt).where(
            ReindexJobItemAttempt.organization_id == item.organization_id,
            ReindexJobItemAttempt.reindex_job_item_id == item.id,
            ReindexJobItemAttempt.id == item.active_attempt_id,
        ).with_for_update())
        if job is None or attempt is None or attempt.status != "running" or attempt.lease_expires_at > reclaimed_at:
            await session.rollback()
            return None
        if attempt.attempt_number < MAX_ATTEMPTS:
            attempt.status, attempt.retryable = "failed", True
            attempt.error_code, attempt.completed_at = "lease_expired", reclaimed_at
            item.processing_status, item.active_attempt_id = "queued", None
            item.next_retry_at = reclaimed_at + timedelta(seconds=RETRY_DELAY_SECONDS)
            item.updated_at, job.status = reclaimed_at, "running"
        else:
            await _fail(session, item=item, job=job, attempt=attempt, code="lease_expired", now=reclaimed_at)
        await session.commit()
        return item.id
    except Exception:
        await session.rollback()
        raise

"""Background worker for queued Markdown ingestion jobs.

Claiming a job, validating the batch (including cross-document relationships),
and persisting each item run in separate database transactions so partial
successes remain durable even when another item in the batch fails.
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.frontmatter import (
    OPTIONAL_REFERENCE_TYPES,
    REQUIRED_REFERENCE_TYPES,
    ValidatedMarkdownDocument,
    ValidationWarning,
    validate_document_batch,
    validate_markdown_document,
)
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import IngestionJob, IngestionJobAttempt, IngestionJobItem

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]

DEFAULT_STALE_SECONDS = 300


class _FencedAttempt(Exception):
    """Raised when a reclaimed worker attempts to write after losing ownership."""


@dataclass(frozen=True)
class _ClaimedJob:
    job_id: UUID
    organization_id: UUID
    submitted_by_principal_id: UUID
    attempt_id: UUID
    all_items: tuple[tuple[UUID, int, str, str], ...]
    queued_item_ids: frozenset[UUID]


@dataclass(frozen=True)
class _PreparedItem:
    item_id: UUID
    item_index: int
    source_uri: str
    document: ValidatedMarkdownDocument | None
    warnings: list[dict[str, object]]
    errors: list[dict[str, object]]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _format_warning(warning: ValidationWarning) -> dict[str, object]:
    return {
        "code": warning.code,
        "field_name": warning.field_name,
        "reference_id": warning.reference_id,
        "message": warning.message,
    }


def _check_single_document_relationships(
    document: ValidatedMarkdownDocument,
    documents_by_id: dict[str, ValidatedMarkdownDocument],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """Validate one document's relationships against the batch identity map."""
    doc_id = document.frontmatter.document_id
    doc_type = document.frontmatter.document_type
    required_fields = REQUIRED_REFERENCE_TYPES.get(doc_type, {})
    optional_fields = OPTIONAL_REFERENCE_TYPES.get(doc_type, {})
    relationship_fields = {**required_fields, **optional_fields}

    for field_name in relationship_fields:
        reference_ids = getattr(document.frontmatter, field_name)
        if doc_id in reference_ids:
            return [], [
                {
                    "code": "relationship_validation_error",
                    "message": "self-reference is not allowed",
                }
            ]

    for field_name, expected_type in required_fields.items():
        for reference_id in getattr(document.frontmatter, field_name):
            referenced = documents_by_id.get(reference_id)
            if referenced is None:
                return [], [
                    {
                        "code": "required_reference_error",
                        "message": "required reference target is missing from batch",
                    }
                ]
            if referenced.frontmatter.document_type != expected_type:
                return [], [
                    {
                        "code": "required_reference_error",
                        "message": "required reference target has invalid document_type",
                    }
                ]

    warnings: list[dict[str, object]] = []
    for field_name, expected_type in optional_fields.items():
        for reference_id in getattr(document.frontmatter, field_name):
            referenced = documents_by_id.get(reference_id)
            if referenced is None:
                warnings.append(
                    {
                        "code": "broken_optional_reference",
                        "field_name": field_name,
                        "reference_id": reference_id,
                        "message": f"broken optional reference: {field_name}",
                    }
                )
                continue
            if referenced.frontmatter.document_type != expected_type:
                return [], [
                    {
                        "code": "relationship_validation_error",
                        "message": "optional reference target has invalid document_type",
                    }
                ]

    return warnings, []


def _prepare_batch(
    raw_items: tuple[tuple[UUID, int, str, str], ...],
) -> list[_PreparedItem]:
    """Parse and validate batch items using ``validate_document_batch``."""
    parsed_by_item_id: dict[UUID, ValidatedMarkdownDocument] = {}
    parse_errors_by_item_id: dict[UUID, list[dict[str, object]]] = {}

    for item_id, _, source_uri, markdown in raw_items:
        try:
            parsed_by_item_id[item_id] = validate_markdown_document(
                markdown, source_uri=source_uri
            )
        except Exception:
            parse_errors_by_item_id[item_id] = [
                {"code": "validation_error", "message": "document validation failed"}
            ]

    documents_by_id: dict[str, ValidatedMarkdownDocument] = {}
    duplicate_item_ids: set[UUID] = set()
    unique_valid_items: list[tuple[UUID, ValidatedMarkdownDocument]] = []

    for item_id, _, _, _ in raw_items:
        doc = parsed_by_item_id.get(item_id)
        if doc is None:
            continue
        doc_id = doc.frontmatter.document_id
        if doc_id in documents_by_id:
            duplicate_item_ids.add(item_id)
            continue
        documents_by_id[doc_id] = doc
        unique_valid_items.append((item_id, doc))

    warnings_by_doc_id: dict[str, list[dict[str, object]]] = {}
    rel_errors_by_item_id: dict[UUID, list[dict[str, object]]] = {}

    if unique_valid_items:
        candidate_docs = [doc for _, doc in unique_valid_items]
        try:
            batch_result = validate_document_batch(candidate_docs)
            for warning in batch_result.warnings:
                warnings_by_doc_id.setdefault(warning.document_id, []).append(
                    _format_warning(warning)
                )
        except ValueError:
            # At least one item has a relationship failure; isolate per item so
            # valid documents in the same batch still proceed independently.
            surviving_docs: list[ValidatedMarkdownDocument] = []
            for item_id, doc in unique_valid_items:
                item_warnings, item_errors = _check_single_document_relationships(
                    doc, documents_by_id
                )
                if item_errors:
                    rel_errors_by_item_id[item_id] = item_errors
                else:
                    surviving_docs.append(doc)
                    if item_warnings:
                        warnings_by_doc_id[doc.frontmatter.document_id] = item_warnings

            if surviving_docs:
                try:
                    batch_result = validate_document_batch(surviving_docs)
                    warnings_by_doc_id = {}
                    for warning in batch_result.warnings:
                        warnings_by_doc_id.setdefault(warning.document_id, []).append(
                            _format_warning(warning)
                        )
                except ValueError:
                    pass

    prepared: list[_PreparedItem] = []
    for item_id, item_index, source_uri, _ in raw_items:
        if item_id in parse_errors_by_item_id:
            prepared.append(
                _PreparedItem(
                    item_id=item_id,
                    item_index=item_index,
                    source_uri=source_uri,
                    document=None,
                    warnings=[],
                    errors=parse_errors_by_item_id[item_id],
                )
            )
            continue
        if item_id in duplicate_item_ids:
            prepared.append(
                _PreparedItem(
                    item_id=item_id,
                    item_index=item_index,
                    source_uri=source_uri,
                    document=parsed_by_item_id[item_id],
                    warnings=[],
                    errors=[
                        {
                            "code": "duplicate_document_id",
                            "message": "duplicate document_id in batch",
                        }
                    ],
                )
            )
            continue
        if item_id in rel_errors_by_item_id:
            prepared.append(
                _PreparedItem(
                    item_id=item_id,
                    item_index=item_index,
                    source_uri=source_uri,
                    document=parsed_by_item_id[item_id],
                    warnings=[],
                    errors=rel_errors_by_item_id[item_id],
                )
            )
            continue
        doc = parsed_by_item_id[item_id]
        prepared.append(
            _PreparedItem(
                item_id=item_id,
                item_index=item_index,
                source_uri=source_uri,
                document=doc,
                warnings=warnings_by_doc_id.get(doc.frontmatter.document_id, []),
                errors=[],
            )
        )
    return prepared


async def _claim_next_job(
    session_factory: SessionFactory,
    *,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
) -> _ClaimedJob | None:
    if stale_after_seconds < 0:
        raise ValueError("stale_after_seconds must be non-negative")
    async with session_factory() as session:
        now = _utcnow()
        cutoff = now - timedelta(seconds=stale_after_seconds)
        job = await session.scalar(
            select(IngestionJob)
            .where(
                or_(
                    IngestionJob.status == "queued",
                    and_(
                        IngestionJob.status == "running",
                        IngestionJob.started_at <= cutoff,
                    ),
                )
            )
            .order_by(IngestionJob.submitted_at.asc(), IngestionJob.id.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            return None

        if job.status == "running":
            stale_attempts = (
                await session.scalars(
                    select(IngestionJobAttempt).where(
                        IngestionJobAttempt.organization_id == job.organization_id,
                        IngestionJobAttempt.ingestion_job_id == job.id,
                        IngestionJobAttempt.status == "running",
                    )
                )
            ).all()
            for stale_attempt in stale_attempts:
                stale_attempt.status = "failed"
                stale_attempt.retryable = True
                stale_attempt.error_code = "worker_lease_expired"
                stale_attempt.error_message = "previous worker attempt timed out"
                stale_attempt.completed_at = now

        last_attempt = await session.scalar(
            select(func.max(IngestionJobAttempt.attempt_number)).where(
                IngestionJobAttempt.organization_id == job.organization_id,
                IngestionJobAttempt.ingestion_job_id == job.id,
            )
        )
        attempt_id = uuid4()
        attempt = IngestionJobAttempt(
            id=attempt_id,
            organization_id=job.organization_id,
            ingestion_job_id=job.id,
            attempt_number=(last_attempt or 0) + 1,
            status="running",
            retryable=False,
            started_at=now,
        )
        session.add(attempt)
        await session.flush()

        job.status = "running"
        job.started_at = now
        job.active_attempt_id = attempt_id

        rows = (
            await session.scalars(
                select(IngestionJobItem)
                .where(
                    IngestionJobItem.organization_id == job.organization_id,
                    IngestionJobItem.ingestion_job_id == job.id,
                )
                .order_by(IngestionJobItem.item_index.asc())
            )
        ).all()
        all_items = tuple(
            (item.id, item.item_index, item.source_uri, item.submitted_markdown or "")
            for item in rows
        )
        queued_item_ids = frozenset(
            item.id for item in rows if item.processing_status == "queued"
        )
        await session.commit()
        return _ClaimedJob(
            job_id=job.id,
            organization_id=job.organization_id,
            submitted_by_principal_id=job.submitted_by_principal_id,
            attempt_id=attempt.id,
            all_items=all_items,
            queued_item_ids=queued_item_ids,
        )


async def _lock_active_job(
    session: AsyncSession,
    *,
    claimed: _ClaimedJob,
) -> IngestionJob:
    """Lock the job row and verify this attempt still holds the active fence."""
    job = await session.scalar(
        select(IngestionJob)
        .where(
            IngestionJob.organization_id == claimed.organization_id,
            IngestionJob.id == claimed.job_id,
        )
        .with_for_update()
    )
    if (
        job is None
        or job.status != "running"
        or job.active_attempt_id != claimed.attempt_id
    ):
        raise _FencedAttempt("ingestion attempt is no longer active")
    return job


async def _record_item_failure(
    session_factory: SessionFactory,
    *,
    claimed: _ClaimedJob,
    item_id: UUID,
    document_id: str | None,
    errors: list[dict[str, object]],
) -> None:
    async with session_factory() as session:
        try:
            await _lock_active_job(session, claimed=claimed)
        except _FencedAttempt:
            await session.rollback()
            raise
        item = await session.scalar(
            select(IngestionJobItem).where(
                IngestionJobItem.organization_id == claimed.organization_id,
                IngestionJobItem.id == item_id,
            )
        )
        if item is not None:
            item.document_id = document_id
            item.processing_status = "failed"
            item.outcome = "failed"
            item.errors = errors
            item.completed_at = _utcnow()
        await session.commit()


async def _process_prepared_item(
    session_factory: SessionFactory,
    *,
    claimed: _ClaimedJob,
    prepared: _PreparedItem,
) -> None:
    doc_id = (
        prepared.document.frontmatter.document_id
        if prepared.document is not None
        else None
    )
    if prepared.errors or prepared.document is None:
        await _record_item_failure(
            session_factory,
            claimed=claimed,
            item_id=prepared.item_id,
            document_id=doc_id,
            errors=prepared.errors
            or [{"code": "validation_error", "message": "document validation failed"}],
        )
        return

    try:
        async with session_factory() as session:
            await _lock_active_job(session, claimed=claimed)
            result = await ingest_validated_document(
                session,
                organization_id=claimed.organization_id,
                actor_principal_id=claimed.submitted_by_principal_id,
                document=prepared.document,
            )
            item = await session.scalar(
                select(IngestionJobItem).where(
                    IngestionJobItem.organization_id == claimed.organization_id,
                    IngestionJobItem.id == prepared.item_id,
                )
            )
            if item is not None:
                item.document_id = doc_id
                item.document_pk = result.document_pk
                item.document_version_id = result.document_version_id
                item.processing_status = "succeeded"
                item.outcome = result.outcome
                item.warnings = prepared.warnings
                item.completed_at = _utcnow()
            await session.commit()
    except _FencedAttempt:
        raise
    except Exception:
        await _record_item_failure(
            session_factory,
            claimed=claimed,
            item_id=prepared.item_id,
            document_id=doc_id,
            errors=[{"code": "ingestion_error", "message": "document ingestion failed"}],
        )


async def _finalize_job(
    session_factory: SessionFactory,
    *,
    claimed: _ClaimedJob,
) -> None:
    async with session_factory() as session:
        try:
            job = await _lock_active_job(session, claimed=claimed)
        except _FencedAttempt:
            await session.rollback()
            raise
        attempt = await session.scalar(
            select(IngestionJobAttempt).where(
                IngestionJobAttempt.organization_id == claimed.organization_id,
                IngestionJobAttempt.id == claimed.attempt_id,
            )
        )
        items = (
            await session.scalars(
                select(IngestionJobItem).where(
                    IngestionJobItem.organization_id == claimed.organization_id,
                    IngestionJobItem.ingestion_job_id == claimed.job_id,
                )
            )
        ).all()
        succeeded_count = sum(
            1 for item in items if item.processing_status == "succeeded"
        )
        failed_count = sum(1 for item in items if item.processing_status == "failed")
        now = _utcnow()

        job.active_attempt_id = None
        if failed_count == 0 and succeeded_count > 0:
            job.status = "completed"
        elif succeeded_count > 0:
            job.status = "completed_with_errors"
        else:
            job.status = "failed"
        job.completed_at = now

        if attempt is not None:
            if succeeded_count > 0:
                attempt.status = "succeeded"
                attempt.retryable = False
            else:
                attempt.status = "failed"
                attempt.retryable = False
                attempt.error_code = "all_items_failed"
                attempt.error_message = "all ingestion items failed"
            attempt.completed_at = now

        await session.commit()


async def process_next_ingestion_job(
    session_factory: SessionFactory,
    *,
    stale_after_seconds: int = DEFAULT_STALE_SECONDS,
) -> UUID | None:
    """Claim and process one queued or stale-running ingestion job."""
    claimed = await _claim_next_job(
        session_factory,
        stale_after_seconds=stale_after_seconds,
    )
    if claimed is None:
        return None

    prepared_items = _prepare_batch(claimed.all_items)
    try:
        for prepared in prepared_items:
            if prepared.item_id not in claimed.queued_item_ids:
                continue
            await _process_prepared_item(
                session_factory,
                claimed=claimed,
                prepared=prepared,
            )

        await _finalize_job(session_factory, claimed=claimed)
    except _FencedAttempt:
        return None
    return claimed.job_id

"""HTTP boundary for authenticated, capability-gated Markdown ingestion jobs."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import resolve_request_id
from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.authorization.acl import resolve_readable_document_pks
from src.authorization.capabilities import principal_has_capability
from src.database import get_db
from src.models.storage import AuditEvent, DocumentVersion, IngestionJob, IngestionJobItem

router = APIRouter(prefix="/api/v1", tags=["Ingestions"])

INGEST_CAPABILITY = "document:ingest"
INGEST_READ_CAPABILITY = "ingestion:read"


class IngestionDocumentInput(BaseModel):
    source_uri: str = Field(min_length=1)
    markdown_content: str = Field(min_length=1)

    @field_validator("source_uri", "markdown_content")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value must not be blank")
        return value


class IngestionRequest(BaseModel):
    documents: list[IngestionDocumentInput] = Field(min_length=1)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_hash(documents: list[IngestionDocumentInput]) -> str:
    canonical = [
        {"source_uri": item.source_uri, "markdown_content": item.markdown_content}
        for item in documents
    ]
    serialized = json.dumps(
        canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256(serialized.encode("utf-8"))


async def _record_ingestion_audit(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    request_id: str,
    event_type: str,
    outcome: str,
    details: dict[str, object],
    reason_code: str | None = None,
    ingestion_job_id: UUID | None = None,
) -> None:
    """Append an ingestion audit event without disclosing markdown or tokens."""
    session.add(
        AuditEvent(
            organization_id=principal.organization_id,
            event_type=event_type,
            outcome=outcome,
            actor_kind="principal",
            actor_principal_id=principal.id,
            request_id=request_id,
            ingestion_job_id=ingestion_job_id,
            reason_code=reason_code,
            details=details,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()


@router.post("/ingestions", status_code=status.HTTP_202_ACCEPTED)
async def create_ingestion_job(
    request: IngestionRequest,
    http_request: Request,
    session: AsyncSession = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    """Accept and queue a batch of Markdown documents for worker processing."""
    request_id = resolve_request_id(http_request)

    # Capability gate precedes idempotency lookup and any persistence.
    if not await principal_has_capability(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
        capability=INGEST_CAPABILITY,
    ):
        await _record_ingestion_audit(
            session,
            principal=principal,
            request_id=request_id,
            event_type="ingestion.denied",
            outcome="denied",
            reason_code="ingestion_forbidden",
            details={"required_capability": INGEST_CAPABILITY},
        )
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "ingestion_forbidden")

    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "idempotency_key_required"
        )

    key_hash = _sha256(
        f"{principal.organization_id}:{principal.id}:{idempotency_key}".encode("utf-8")
    )
    payload_hash = _payload_hash(request.documents)

    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"ingestion:{principal.organization_id}:{principal.id}:{key_hash}"},
    )

    existing = await session.scalar(
        select(IngestionJob).where(
            IngestionJob.organization_id == principal.organization_id,
            IngestionJob.submitted_by_principal_id == principal.id,
            IngestionJob.idempotency_key_hash == key_hash,
        )
    )
    if existing is not None:
        if existing.request_payload_hash != payload_hash:
            raise HTTPException(status.HTTP_409_CONFLICT, "idempotency_conflict")
        return {
            "request_id": request_id,
            "job_id": str(existing.id),
            "status": existing.status,
            "submitted_document_count": existing.submitted_document_count,
            "idempotency_replayed": True,
            "status_url": f"/api/v1/ingestions/{existing.id}",
        }

    now = datetime.now(timezone.utc)
    job = IngestionJob(
        organization_id=principal.organization_id,
        request_id=request_id,
        idempotency_key_hash=key_hash,
        request_payload_hash=payload_hash,
        submitted_by_principal_id=principal.id,
        status="queued",
        submitted_document_count=len(request.documents),
        submitted_at=now,
    )
    session.add(job)
    await session.flush()

    for idx, doc_input in enumerate(request.documents):
        session.add(
            IngestionJobItem(
                organization_id=principal.organization_id,
                ingestion_job_id=job.id,
                item_index=idx,
                source_uri=doc_input.source_uri,
                submitted_markdown=doc_input.markdown_content,
                submitted_content_hash=_sha256(
                    doc_input.markdown_content.encode("utf-8")
                ),
                processing_status="queued",
            )
        )

    await _record_ingestion_audit(
        session,
        principal=principal,
        request_id=request_id,
        event_type="ingestion.accepted",
        outcome="succeeded",
        ingestion_job_id=job.id,
        details={
            "submitted_document_count": len(request.documents),
            "job_status": "queued",
        },
    )
    await session.commit()

    return {
        "request_id": request_id,
        "job_id": str(job.id),
        "status": "queued",
        "submitted_document_count": job.submitted_document_count,
        "idempotency_replayed": False,
        "status_url": f"/api/v1/ingestions/{job.id}",
    }


@router.get("/ingestions/{job_id}")
async def get_ingestion_job(
    job_id: UUID,
    http_request: Request,
    session: AsyncSession = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> dict[str, object]:
    """Return persisted ingestion job status and ACL-filtered item outcomes."""
    job = await session.scalar(
        select(IngestionJob).where(
            IngestionJob.organization_id == principal.organization_id,
            IngestionJob.id == job_id,
        )
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "ingestion_job_not_found")

    if job.submitted_by_principal_id != principal.id:
        if not await principal_has_capability(
            session,
            organization_id=principal.organization_id,
            principal_id=principal.id,
            capability=INGEST_READ_CAPABILITY,
        ):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "ingestion_job_not_found")

    readable_pks = await resolve_readable_document_pks(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
    )
    items = (
        await session.scalars(
            select(IngestionJobItem)
            .where(
                IngestionJobItem.organization_id == principal.organization_id,
                IngestionJobItem.ingestion_job_id == job.id,
            )
            .order_by(IngestionJobItem.item_index.asc())
        )
    ).all()

    visible_items = [
        item
        for item in items
        if item.document_pk is None or item.document_pk in readable_pks
    ]
    succeeded_count = sum(
        1 for item in visible_items if item.processing_status == "succeeded"
    )
    failed_count = sum(
        1 for item in visible_items if item.processing_status == "failed"
    )
    warning_count = sum(len(item.warnings or []) for item in visible_items)

    version_ids = [
        item.document_version_id
        for item in visible_items
        if item.document_version_id is not None
    ]
    version_by_id: dict[UUID, int] = {}
    if version_ids:
        version_rows = (
            await session.execute(
                select(DocumentVersion.id, DocumentVersion.version_number).where(
                    DocumentVersion.organization_id == principal.organization_id,
                    DocumentVersion.id.in_(version_ids),
                )
            )
        ).all()
        version_by_id = {row[0]: row[1] for row in version_rows}

    if job.status in ("queued", "running"):
        visible_status = job.status
    elif failed_count == 0 and succeeded_count > 0:
        visible_status = "completed"
    elif succeeded_count > 0:
        visible_status = "completed_with_errors"
    elif failed_count > 0:
        visible_status = "failed"
    else:
        visible_status = "completed"

    return {
        "request_id": resolve_request_id(http_request),
        "job_id": str(job.id),
        "status": visible_status,
        "submitted_at": job.submitted_at.isoformat() if job.submitted_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "summary": {
            "submitted": len(visible_items),
            "succeeded": succeeded_count,
            "failed": failed_count,
            "warnings": warning_count,
        },
        "documents": [
            {
                "item_index": item.item_index,
                "source_uri": item.source_uri if item.document_pk is not None else None,
                "document_id": item.document_id if item.document_pk is not None else None,
                "document_pk": str(item.document_pk) if item.document_pk else None,
                "document_version_id": (
                    str(item.document_version_id) if item.document_version_id else None
                ),
                "version": (
                    version_by_id.get(item.document_version_id)
                    if item.document_version_id is not None
                    else None
                ),
                "processing_status": item.processing_status,
                "outcome": item.outcome,
                "warnings": item.warnings or [],
                "errors": item.errors or [],
            }
            for item in visible_items
        ],
    }

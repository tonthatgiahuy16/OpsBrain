"""Minimal HTTP boundary for reindex jobs.

The worker is lease-fenced and only claims work that is already queued here.
This module is intentionally thin: it validates the principal, enforces
``index:rebuild``, persists a queued job plus one item per requested current
document version, and records an audit event. It never touches source content.
"""

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.authorization.acl import resolve_readable_document_pks
from src.authorization.capabilities import principal_has_capability
from src.database import get_db
from src.models.storage import (
    AuditEvent,
    DerivedConfiguration,
    Document,
    DocumentVersion,
    Organization,
    Principal,
    ReindexJob,
    ReindexJobItem,
)
from src.api.queries import get_authenticated_principal

router = APIRouter(prefix="/api/v1", tags=["ReindexJobs"])


class ReindexRequest(BaseModel):
    targets: list[str] = Field(default_factory=lambda: ["chunks"])
    document_ids: list[str] = Field(default_factory=list)
    configuration_id: UUID | None = None
    reason: str = "manual"


class ReindexJobResponse(BaseModel):
    reindex_job_id: str
    status_url: str
    requested_document_count: int
    targets: list[str]


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_hash(
    *,
    targets: list[str],
    document_ids: list[str],
    reason: str,
    configuration_id: UUID | None,
) -> str:
    payload = {
        "scope_type": "documents",
        "targets": sorted(targets),
        "document_ids": sorted(document_ids),
        "reason": reason,
        "configuration_id": str(configuration_id) if configuration_id else None,
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256(serialized.encode("utf-8"))


async def _visible_job_items(
    session: AsyncSession,
    *,
    organization_id: UUID,
    principal_id: UUID,
    job_id: UUID,
) -> list[ReindexJobItem]:
    """Return job items still readable by the principal under current ACL."""
    readable_pks = await resolve_readable_document_pks(
        session,
        organization_id=organization_id,
        principal_id=principal_id,
    )
    items = (
        await session.scalars(
            select(ReindexJobItem).where(
                ReindexJobItem.organization_id == organization_id,
                ReindexJobItem.reindex_job_id == job_id,
            )
        )
    ).all()
    return [item for item in items if item.document_pk in readable_pks]


async def _record_reindex_audit(
    session: AsyncSession,
    *,
    principal: object,
    request_id: str,
    event_type: str,
    outcome: str,
    reason_code: str,
    details: dict[str, object],
) -> None:
    session.add(
        AuditEvent(
            organization_id=principal.organization_id,
            event_type=event_type,
            outcome=outcome,
            actor_kind="principal",
            actor_principal_id=principal.id,
            request_id=request_id,
            details=details,
            reason_code=reason_code,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()


@router.post("/reindex-jobs", status_code=status.HTTP_202_ACCEPTED)
async def create_reindex_job(
    request: ReindexRequest,
    session: AsyncSession = Depends(get_db),
    principal: object = Depends(get_authenticated_principal),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    request_id = str(uuid4())

    # Capability gate.
    if not await principal_has_capability(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
        capability="index:rebuild",
    ):
        await _record_reindex_audit(
            session,
            principal=principal,
            request_id=request_id,
            event_type="reindex.denied",
            outcome="denied",
            reason_code="reindex_forbidden",
            details={"required_capability": "index:rebuild"},
        )
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "reindex_forbidden")

    if not request.targets:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "targets_required")
    if request.targets not in (["chunks"], ["embeddings"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid_target")

    # Idempotency key handling: require a client key for replay semantics.
    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "idempotency_key_required"
        )
    key_hash = _sha256(
        f"{principal.organization_id}:{principal.id}:{idempotency_key}".encode("utf-8")
    )
    payload_hash = _payload_hash(
        targets=request.targets,
        document_ids=request.document_ids,
        reason=request.reason,
        configuration_id=request.configuration_id,
    )

    # Replay handling.
    existing = await session.scalar(
        select(ReindexJob).where(
            ReindexJob.organization_id == principal.organization_id,
            ReindexJob.submitted_by_principal_id == principal.id,
            ReindexJob.idempotency_key_hash == key_hash,
        )
    )
    if existing is not None:
        if existing.request_payload_hash != payload_hash:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "idempotency_conflict"
            )
        visible_items = await _visible_job_items(
            session,
            organization_id=principal.organization_id,
            principal_id=principal.id,
            job_id=existing.id,
        )
        return {
            "reindex_job_id": str(existing.id),
            "status_url": f"/api/v1/reindex-jobs/{existing.id}",
            "requested_document_count": len(visible_items),
            "targets": existing.targets,
            "replayed": True,
        }

    # Resolve configuration.
    if request.configuration_id is not None:
        configuration = await session.scalar(
            select(DerivedConfiguration).where(
                DerivedConfiguration.organization_id == principal.organization_id,
                DerivedConfiguration.id == request.configuration_id,
                DerivedConfiguration.status == "active",
            )
        )
    else:
        configuration = await session.scalar(
            select(DerivedConfiguration).where(
                DerivedConfiguration.organization_id == principal.organization_id,
                DerivedConfiguration.status == "active",
            ).order_by(DerivedConfiguration.created_at.desc()).limit(1)
        )
    if configuration is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "configuration_not_found")

    # Resolve readable document PKs (active only).
    allowed_pks = await resolve_readable_document_pks(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
    )

    document_pk_by_id: dict[str, UUID] = {}
    if request.document_ids:
        rows = await session.execute(
            select(Document).where(
                Document.organization_id == principal.organization_id,
                Document.document_id.in_(request.document_ids),
                Document.record_state == "active",
            )
        )
        for document in rows.scalars().all():
            if document.id in allowed_pks:
                document_pk_by_id[document.document_id] = document.id
        if len(document_pk_by_id) != len(set(request.document_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "document_not_found")
    else:
        rows = await session.execute(
            select(Document).where(
                Document.organization_id == principal.organization_id,
                Document.record_state == "active",
                Document.id.in_(allowed_pks),
            )
        )
        for document in rows.scalars().all():
            document_pk_by_id[document.document_id] = document.id

    if not document_pk_by_id:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "no_documents"
        )

    job = ReindexJob(
        organization_id=principal.organization_id,
        request_id=request_id,
        idempotency_key_hash=key_hash,
        request_payload_hash=payload_hash,
        submitted_by_principal_id=principal.id,
        scope_type="documents",
        targets=request.targets,
        reason=request.reason,
        configuration_id=configuration.id,
        requested_document_count=len(document_pk_by_id),
        status="queued",
    )
    session.add(job)
    await session.flush()

    for document_id, document_pk in document_pk_by_id.items():
        document = await session.get(Document, document_pk)
        if document is None or document.current_version_id is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "current_version_missing"
            )
        version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == principal.organization_id,
                DocumentVersion.document_pk == document_pk,
                DocumentVersion.id == document.current_version_id,
            )
        )
        if version is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "current_version_missing"
            )
        session.add(
            ReindexJobItem(
                organization_id=principal.organization_id,
                reindex_job_id=job.id,
                document_pk=document_pk,
                source_document_version_id=version.id,
                processing_status="queued",
            )
        )

    session.add(
        AuditEvent(
            organization_id=principal.organization_id,
            event_type="reindex.job.accepted",
            outcome="succeeded",
            actor_kind="principal",
            actor_principal_id=principal.id,
            request_id=request_id,
            reindex_job_id=job.id,
            details={
                "targets": request.targets,
                "document_count": len(document_pk_by_id),
                "configuration_id": str(configuration.id),
            },
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    return {
        "reindex_job_id": str(job.id),
        "status_url": f"/api/v1/reindex-jobs/{job.id}",
        "requested_document_count": len(document_pk_by_id),
        "targets": request.targets,
        "replayed": False,
    }


@router.get("/reindex-jobs/{job_id}")
async def get_reindex_job(
    job_id: UUID,
    session: AsyncSession = Depends(get_db),
    principal: object = Depends(get_authenticated_principal),
) -> dict[str, object]:
    job = await session.scalar(
        select(ReindexJob).where(
            ReindexJob.organization_id == principal.organization_id,
            ReindexJob.id == job_id,
        )
    )
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "reindex_job_not_found")
    if job.submitted_by_principal_id != principal.id:
        if not await principal_has_capability(
            session,
            organization_id=principal.organization_id,
            principal_id=principal.id,
            capability="index:read",
        ):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "reindex_job_not_found"
            )

    visible_items = await _visible_job_items(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
        job_id=job.id,
    )
    return {
        "reindex_job_id": str(job.id),
        "status": job.status,
        "targets": job.targets,
        "requested_document_count": len(visible_items),
        "submitted_by_principal_id": str(job.submitted_by_principal_id),
        "items": [
            {
                "document_pk": str(item.document_pk),
                "processing_status": item.processing_status,
                "outcome": item.outcome,
                "skip_code": item.skip_code,
                "error_code": item.error_code,
            }
            for item in visible_items
        ],
    }

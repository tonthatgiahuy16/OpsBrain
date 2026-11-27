"""HTTP boundary for reindex jobs aligned with minimal API contract §13."""

import hashlib
import json
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import resolve_request_id
from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.authorization.acl import resolve_readable_document_pks
from src.authorization.capabilities import principal_has_capability
from src.database import get_db
from src.models.storage import (
    AuditEvent,
    DerivedConfiguration,
    Document,
    DocumentVersion,
    ReindexJob,
    ReindexJobItem,
)

router = APIRouter(prefix="/api/v1", tags=["ReindexJobs"])


class ReindexScope(BaseModel):
    type: Literal["documents", "organization"]
    document_ids: list[str] | None = None

    @model_validator(mode="after")
    def _validate_scope(self) -> "ReindexScope":
        if self.type == "documents":
            if not self.document_ids:
                raise ValueError("document_ids must be non-empty for documents scope")
            if any(not doc_id.strip() for doc_id in self.document_ids):
                raise ValueError("document_ids must not contain blank values")
        elif self.document_ids is not None:
            raise ValueError("document_ids must be omitted for organization scope")
        return self


class ReindexRequest(BaseModel):
    scope: ReindexScope
    targets: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    configuration_id: UUID | None = None

    @field_validator("reason")
    @classmethod
    def _reason_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason must not be blank")
        return value


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_hash(
    *,
    scope: ReindexScope,
    targets: list[str],
    reason: str,
    configuration_id: UUID | None,
) -> str:
    payload = {
        "scope_type": scope.type,
        "document_ids": sorted(scope.document_ids or []),
        "targets": sorted(targets),
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
    principal: AuthenticatedPrincipal,
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
    http_request: Request,
    session: AsyncSession = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    request_id = resolve_request_id(http_request)

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

    if request.targets not in (["chunks"], ["embeddings"], ["full_text"]):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_target")

    if not idempotency_key or not idempotency_key.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "idempotency_key_required"
        )
    key_hash = _sha256(
        f"{principal.organization_id}:{principal.id}:{idempotency_key}".encode("utf-8")
    )
    payload_hash = _payload_hash(
        scope=request.scope,
        targets=request.targets,
        reason=request.reason,
        configuration_id=request.configuration_id,
    )

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
            "request_id": request_id,
            "job_id": str(existing.id),
            "status": existing.status,
            "scope_type": existing.scope_type,
            "requested_document_count": len(visible_items),
            "targets": existing.targets,
            "idempotency_replayed": True,
            "status_url": f"/api/v1/reindex-jobs/{existing.id}",
        }

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

    allowed_pks = await resolve_readable_document_pks(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
    )

    document_pk_by_id: dict[str, UUID] = {}
    if request.scope.type == "documents":
        requested_ids = request.scope.document_ids or []
        rows = await session.execute(
            select(Document).where(
                Document.organization_id == principal.organization_id,
                Document.document_id.in_(requested_ids),
                Document.record_state == "active",
            )
        )
        for document in rows.scalars().all():
            if document.id in allowed_pks:
                document_pk_by_id[document.document_id] = document.id
        if len(document_pk_by_id) != len(set(requested_ids)):
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
            status.HTTP_422_UNPROCESSABLE_CONTENT, "no_documents"
        )

    job = ReindexJob(
        organization_id=principal.organization_id,
        request_id=request_id,
        idempotency_key_hash=key_hash,
        request_payload_hash=payload_hash,
        submitted_by_principal_id=principal.id,
        scope_type=request.scope.type,
        targets=request.targets,
        reason=request.reason,
        configuration_id=configuration.id,
        requested_document_count=len(document_pk_by_id),
        status="queued",
    )
    session.add(job)
    await session.flush()

    for document_pk in document_pk_by_id.values():
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
                "scope_type": request.scope.type,
                "targets": request.targets,
                "document_count": len(document_pk_by_id),
                "configuration_id": str(configuration.id),
            },
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.commit()

    return {
        "request_id": request_id,
        "job_id": str(job.id),
        "status": job.status,
        "scope_type": job.scope_type,
        "requested_document_count": len(document_pk_by_id),
        "targets": request.targets,
        "idempotency_replayed": False,
        "status_url": f"/api/v1/reindex-jobs/{job.id}",
    }


@router.get("/reindex-jobs/{job_id}")
async def get_reindex_job(
    job_id: UUID,
    http_request: Request,
    session: AsyncSession = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
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
        "request_id": resolve_request_id(http_request),
        "job_id": str(job.id),
        "status": job.status,
        "scope_type": job.scope_type,
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

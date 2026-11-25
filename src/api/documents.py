"""Read-only document state endpoint with capability and ACL enforcement."""

from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.authorization.acl import readable_document_clause
from src.authorization.capabilities import principal_has_capability
from src.database import get_db
from src.ingestion.eligibility import is_pre_acl_retrieval_eligible
from src.models.storage import (
    DerivedSet,
    Document,
    DocumentVersion,
    IngestionJob,
    IngestionJobItem,
    Organization,
)


router = APIRouter(prefix="/api/v1", tags=["Documents"])


async def _latest_ingestion_job_id(
    session: AsyncSession,
    *,
    organization_id,
    document_pk,
) -> str | None:
    """Return only the newest traceable ingestion-job identifier."""
    job_id = await session.scalar(
        select(IngestionJob.id)
        .join(IngestionJobItem, IngestionJobItem.ingestion_job_id == IngestionJob.id)
        .where(
            IngestionJob.organization_id == organization_id,
            IngestionJobItem.organization_id == organization_id,
            IngestionJobItem.document_pk == document_pk,
        )
        .order_by(IngestionJob.submitted_at.desc(), IngestionJob.id.desc())
        .limit(1)
    )
    return str(job_id) if job_id is not None else None


async def _index_status(
    session: AsyncSession,
    *,
    organization_id,
    document_pk,
    version_id,
    retrieval_eligible: bool,
) -> str:
    """Derive the public index state without exposing derived-set internals."""
    if not retrieval_eligible:
        return "not_eligible"

    statuses = list(
        (
            await session.scalars(
                select(DerivedSet.status).where(
                    DerivedSet.organization_id == organization_id,
                    DerivedSet.document_pk == document_pk,
                    DerivedSet.document_version_id == version_id,
                )
            )
        ).all()
    )
    if "ready" in statuses:
        return "ready"
    if "building" in statuses or "pending" in statuses:
        return "pending"
    if "stale" in statuses:
        return "stale"
    if "failed" in statuses:
        return "failed"
    # An eligible approved version is waiting for its first derived build.
    return "pending"


@router.get("/documents/{document_id}")
async def get_document_state(
    document_id: str,
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
    session: AsyncSession = Depends(get_db),
):
    """Return management state only for a currently visible document.

    Both an unknown document and a document that fails current ACL/capability
    checks deliberately return the same 404 response, avoiding existence
    disclosure for restricted material.
    """
    document = await session.scalar(
        select(Document)
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .where(
            Document.organization_id == principal.organization_id,
            Document.document_id == document_id,
            DocumentVersion.organization_id == principal.organization_id,
            readable_document_clause(
                organization_id=principal.organization_id,
                principal_id=principal.id,
            ),
        )
    )
    has_capability = await principal_has_capability(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
        capability="document:read_state",
    )
    if document is None or not has_capability:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document_not_found")

    version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == principal.organization_id,
            DocumentVersion.document_pk == document.id,
            DocumentVersion.id == document.current_version_id,
        )
    )
    if version is None:
        # Do not turn an inconsistent internal record into an existence leak.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document_not_found")
    organization = await session.scalar(
        select(Organization).where(Organization.id == principal.organization_id)
    )
    if organization is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document_not_found")

    now = datetime.now(timezone.utc)
    reasons: list[str] = []
    if document.record_state != "active" or version.document_status != "active":
        reasons.append("document_not_active")
    if version.review_state != "approved":
        reasons.append("review_state_not_approved")
    if (
        (version.effective_from is not None and version.effective_from > now)
        or (version.effective_until is not None and version.effective_until <= now)
    ):
        reasons.append("outside_effective_period")
    retrieval_eligible = is_pre_acl_retrieval_eligible(
        organization, document, version, now,
    )
    index_status = await _index_status(
        session,
        organization_id=principal.organization_id,
        document_pk=document.id,
        version_id=version.id,
        retrieval_eligible=retrieval_eligible,
    )
    return {
        "request_id": str(uuid4()),
        "document": {
            "document_id": document.document_id,
            "document_type": document.document_type,
            "title": version.title,
            "document_status": version.document_status,
            "review_state": version.review_state,
            "owner_team": version.owner_team,
            "provenance_type": version.provenance_type,
            "access_scope": version.access_scope,
            "source_uri": version.source_uri,
            "current_version": version.version_number,
            "retrieval_eligible": retrieval_eligible,
            "ineligibility_reasons": reasons,
            "query_ready": retrieval_eligible and index_status == "ready",
            "index_status": index_status,
            "latest_ingestion_job_id": await _latest_ingestion_job_id(
                session,
                organization_id=principal.organization_id,
                document_pk=document.id,
            ),
            "created_at": document.created_at,
            "updated_at": document.updated_at,
        },
    }

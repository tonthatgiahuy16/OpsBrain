"""HTTP boundary for ACL-aware document review decisions."""

from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.errors import resolve_request_id
from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.authorization.acl import resolve_readable_document_pks
from src.authorization.capabilities import (
    CapabilityDenied,
    DocumentNotVisible,
    principal_has_capability,
)
from src.database import get_db
from src.ingestion.review import submit_review_decision
from src.models.storage import DerivedConfiguration, Document, DocumentVersion


router = APIRouter(prefix="/api/v1", tags=["Documents"])


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject)$")
    expected_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=8_000)


async def _document_and_current_version(
    session: AsyncSession,
    *,
    organization_id,
    document_id: str,
) -> tuple[Document, DocumentVersion] | None:
    document = await session.scalar(
        select(Document).where(
            Document.organization_id == organization_id,
            Document.document_id == document_id,
        )
    )
    if document is None or document.current_version_id is None:
        return None
    version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.document_pk == document.id,
            DocumentVersion.id == document.current_version_id,
        )
    )
    return (document, version) if version is not None else None


async def _requested_version(
    session: AsyncSession,
    *,
    organization_id,
    document_pk,
    version_number: int,
) -> DocumentVersion | None:
    """Resolve the client-reviewed version, including one no longer current.

    The review service checks an idempotency replay before it rejects a stale
    version. Resolving by version number therefore lets a retry present its
    original payload after the first decision has advanced the document.
    """
    return await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.document_pk == document_pk,
            DocumentVersion.version_number == version_number,
        )
    )


async def _selected_configuration_id(session: AsyncSession, *, organization_id):
    configurations = list((await session.scalars(
        select(DerivedConfiguration.id).where(
            DerivedConfiguration.organization_id == organization_id,
            DerivedConfiguration.status == "active",
        )
    )).all())
    if len(configurations) != 1:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "retrieval_unavailable")
    return configurations[0]


@router.post("/documents/{document_id}/review-decisions")
async def create_review_decision(
    document_id: str,
    request: ReviewDecisionRequest,
    http_request: Request,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=1),
    session: AsyncSession = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> dict[str, object]:
    """Approve or reject a visible document using the authenticated principal."""
    request_id = resolve_request_id(http_request)
    current = await _document_and_current_version(
        session,
        organization_id=principal.organization_id,
        document_id=document_id,
    )
    if current is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document_not_found")
    document, current_version = current

    # Preserve the disclosure order from the contract before reporting stale
    # versions. The service repeats these checks immediately before writes.
    readable = await resolve_readable_document_pks(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
        active_only=True,
    )
    if document.id not in readable:
        try:
            await submit_review_decision(
                session,
                organization_id=principal.organization_id,
                document_pk=document.id,
                expected_version_id=current_version.id,
                reviewer_principal_id=principal.id,
                decision=request.decision,
                reason=request.reason,
                request_id=request_id,
                idempotency_key=idempotency_key,
            )
        except DocumentNotVisible:
            await session.commit()
            raise HTTPException(status.HTTP_404_NOT_FOUND, "document_not_found")

    if not await principal_has_capability(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
        capability="document:approve",
    ):
        try:
            await submit_review_decision(
                session,
                organization_id=principal.organization_id,
                document_pk=document.id,
                expected_version_id=current_version.id,
                reviewer_principal_id=principal.id,
                decision=request.decision,
                reason=request.reason,
                request_id=request_id,
                idempotency_key=idempotency_key,
            )
        except CapabilityDenied:
            await session.commit()
            raise HTTPException(status.HTTP_403_FORBIDDEN, "approval_forbidden")

    requested_version = await _requested_version(
        session,
        organization_id=principal.organization_id,
        document_pk=document.id,
        version_number=request.expected_version,
    )
    if requested_version is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "version_conflict")

    configuration_id = None
    if request.decision == "approve":
        configuration_id = await _selected_configuration_id(
            session, organization_id=principal.organization_id,
        )
    try:
        result = await submit_review_decision(
            session,
            organization_id=principal.organization_id,
            document_pk=document.id,
            expected_version_id=requested_version.id,
            reviewer_principal_id=principal.id,
            decision=request.decision,
            reason=request.reason,
            request_id=request_id,
            idempotency_key=idempotency_key,
            selected_configuration_id=configuration_id,
        )
    except DocumentNotVisible:
        await session.commit()
        raise HTTPException(status.HTTP_404_NOT_FOUND, "document_not_found")
    except CapabilityDenied:
        await session.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "approval_forbidden")
    except ValueError as error:
        error_map = {
            "idempotency key conflict": (status.HTTP_409_CONFLICT, "idempotency_conflict"),
            "stale expected version": (status.HTTP_409_CONFLICT, "version_conflict"),
            "version is not in review": (status.HTTP_409_CONFLICT, "invalid_review_transition"),
            "author cannot review own version": (status.HTTP_403_FORBIDDEN, "independent_review_required"),
        }
        http_status, code = error_map.get(
            str(error), (status.HTTP_422_UNPROCESSABLE_CONTENT, "invalid_request"),
        )
        raise HTTPException(http_status, code)

    await session.commit()
    eligible = request.decision == "approve" and result.reindex_job_id is not None
    return {
        "request_id": request_id,
        "review_decision_id": str(result.decision.id),
        "document_id": document.document_id,
        "decision": result.decision.decision,
        "previous_version": requested_version.version_number,
        "current_version": result.resulting_version.version_number,
        "previous_review_state": requested_version.review_state,
        "review_state": result.resulting_version.review_state,
        "retrieval_eligible": eligible,
        "query_ready": False,
        "index_status": "pending" if eligible else "not_eligible",
        "decided_at": result.decision.decided_at.isoformat(),
    }

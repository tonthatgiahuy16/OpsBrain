"""Minimal ACL-bounded vector query endpoint."""

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from src.authorization.acl import (
    readable_document_clause,
    resolve_readable_document_pks,
)
from src.authorization.rls import set_rls_context
from src.authorization.capabilities import principal_has_capability
from src.config import get_settings
from src.database import get_db
from src.ingestion.ollama_embeddings import OllamaEmbeddingClient, OllamaEmbeddingError
from src.models.storage import (
    AuditEvent,
    Chunk,
    DerivedConfiguration,
    Document,
    DocumentVersion,
    Organization,
)
from src.retrieval.vector_search import EmbeddingProfile, active_embedding_profiles, vector_top_k


router = APIRouter(prefix="/api/v1", tags=["Queries"])


@dataclass(frozen=True)
class AuthenticatedPrincipal:
    id: UUID
    organization_id: UUID


class RetrievalRequest(BaseModel):
    question: str = Field(min_length=1, max_length=8_000)
    response_mode: Literal["evidence_only", "answer"] = "evidence_only"
    top_k: int = Field(default=5, ge=1, le=20)


def _token_map() -> dict[str, str]:
    raw_value = get_settings().LOCAL_QUERY_TOKEN_MAP_JSON
    try:
        value = json.loads(raw_value)
    except json.JSONDecodeError:
        # Docker Desktop on PowerShell may strip JSON quotes from `-e` values.
        # Accept a single local mapping without quotes for development only.
        if "=" in raw_value and raw_value.count("=") == 1:
            token, principal_id = raw_value.split("=", maxsplit=1)
            value = {token: principal_id}
        else:
            raise RuntimeError("LOCAL_QUERY_TOKEN_MAP_JSON is invalid")
    if not isinstance(value, dict) or not all(
        isinstance(token, str) and isinstance(principal_id, str)
        for token, principal_id in value.items()
    ):
        raise RuntimeError("LOCAL_QUERY_TOKEN_MAP_JSON must map token to principal UUID")
    return value


async def get_authenticated_principal(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_db),
) -> AuthenticatedPrincipal:
    """Resolve a local opaque token from server configuration, never request JSON."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication_required")
    token = authorization.removeprefix("Bearer ")
    try:
        principal_id = UUID(_token_map()[token])
    except RuntimeError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "authentication_unavailable")
    except (KeyError, ValueError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication_required")
    row = await session.execute(
        text(
            "SELECT id, organization_id "
            "FROM public.app_authenticate_principal(:principal_id)"
        ),
        {"principal_id": principal_id},
    )
    principal = row.one_or_none()
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication_required")
    authenticated = AuthenticatedPrincipal(id=principal[0], organization_id=principal[1])
    await set_rls_context(
        session,
        organization_id=authenticated.organization_id,
        principal_id=authenticated.id,
    )
    return authenticated


async def get_embedding_client(
    configuration: DerivedConfiguration,
) -> OllamaEmbeddingClient:
    return OllamaEmbeddingClient.from_settings(
        model=configuration.embedding_model,
        dimensions=configuration.embedding_dimensions,
    )


async def _active_configuration(
    session: AsyncSession, organization_id: UUID,
) -> DerivedConfiguration:
    configurations = list((await session.scalars(
        select(DerivedConfiguration).where(
            DerivedConfiguration.organization_id == organization_id,
            DerivedConfiguration.status == "active",
            DerivedConfiguration.embedding_model.is_not(None),
            DerivedConfiguration.embedding_dimensions.is_not(None),
        ).order_by(DerivedConfiguration.created_at.desc())
    )).all())
    if len(configurations) != 1:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "retrieval_unavailable")
    return configurations[0]


def _configuration_payload(configuration: DerivedConfiguration) -> dict[str, object]:
    return {
        "retrieval_config_id": configuration.configuration_key,
        "embedding_model": configuration.embedding_model,
        "embedding_model_revision": configuration.embedding_model_revision,
        "embedding_dimensions": configuration.embedding_dimensions,
        "generator_model": None,
    }


QUERY_CAPABILITY = "query:execute"


def _latency_payload(started: float) -> dict[str, int | None]:
    elapsed = int((perf_counter() - started) * 1_000)
    return {"retrieval": elapsed, "generation": None, "total": elapsed}


async def _record_query_audit(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    request_id: str,
    event_type: str,
    outcome: str,
    details: dict[str, object],
    reason_code: str | None = None,
) -> None:
    """Append one query audit event without disclosing protected content.

    Disclosure policy for the query path:

    - the question text, Bearer token, excerpts and raw chunk content are never
      written; they can carry secrets and are not needed to audit the decision;
    - identifiers and scores appear only for evidence the principal was already
      allowed to receive in the response;
    - candidate counts taken before ACL filtering are never written, because
      even an aggregate count confirms that restricted material exists;
    - a refusal records the required capability, never a document reference.

    The endpoint commits the event before returning, so a denial is durable even
    though its exception aborts the response body.
    """
    session.add(
        AuditEvent(
            organization_id=principal.organization_id,
            event_type=event_type,
            outcome=outcome,
            actor_kind="principal",
            actor_principal_id=principal.id,
            request_id=request_id,
            reason_code=reason_code,
            details=details,
            occurred_at=datetime.now(timezone.utc),
        )
    )
    await session.flush()
    await session.commit()


@router.post("/queries")
async def execute_query(
    request: RetrievalRequest,
    session: AsyncSession = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> dict[str, object]:
    """Return authorized vector evidence, or abstain without disclosure.

    Capability and ACL are independent gates: ``query:execute`` decides whether
    the actor may query at all (403), current ACL decides which documents may
    influence ranking, scores and citations. An actor without a capability must
    not learn whether eligible evidence exists.

    Every terminal outcome appends an audit event (contract §12.6 step 10):
    ``query.denied``, ``query.abstained`` or ``query.executed``.
    """
    request_id = str(uuid4())
    if not await principal_has_capability(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
        capability=QUERY_CAPABILITY,
    ):
        await _record_query_audit(
            session,
            principal=principal,
            request_id=request_id,
            event_type="query.denied",
            outcome="denied",
            reason_code="query_forbidden",
            details={"required_capability": QUERY_CAPABILITY},
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, "query_forbidden")
    if request.response_mode == "answer":
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "generation_not_implemented")
    started = perf_counter()
    configuration = await _active_configuration(session, principal.organization_id)
    allowed_document_pks = await resolve_readable_document_pks(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
    )
    profiles = await active_embedding_profiles(
        session,
        organization_id=principal.organization_id,
        allowed_document_pks=allowed_document_pks,
    )
    expected = EmbeddingProfile(
        model=configuration.embedding_model,
        revision=configuration.embedding_model_revision,
        dimensions=configuration.embedding_dimensions,
    )
    if profiles and profiles != [expected]:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "retrieval_unavailable")
    if not profiles:
        return await _abstain(
            session, principal=principal, request_id=request_id,
            request=request, configuration=configuration, started=started,
        )
    try:
        client = await get_embedding_client(configuration)
        query_embedding = (await client.embed_texts([request.question]))[0]
    except OllamaEmbeddingError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "embedding_unavailable")
    hits = await vector_top_k(
        session,
        organization_id=principal.organization_id,
        allowed_document_pks=allowed_document_pks,
        query_embedding=query_embedding,
        embedding_model=configuration.embedding_model,
        embedding_model_revision=configuration.embedding_model_revision,
        top_k=request.top_k,
    )
    if not hits:
        return await _abstain(
            session, principal=principal, request_id=request_id,
            request=request, configuration=configuration, started=started,
        )
    hits = [
        hit for hit in hits
        if hit.score >= get_settings().QUERY_MIN_VECTOR_SCORE
    ]
    if not hits:
        return await _abstain(
            session, principal=principal, request_id=request_id,
            request=request, configuration=configuration, started=started,
            reason="insufficient_evidence",
        )
    # Re-resolve after the external embedding call. The window between the
    # first authorization decision and citation construction must not keep
    # citing documents whose access was revoked while Ollama was running.
    allowed_document_pks = await resolve_readable_document_pks(
        session,
        organization_id=principal.organization_id,
        principal_id=principal.id,
    )
    hits = [hit for hit in hits if hit.document_pk in allowed_document_pks]
    if not hits:
        return await _abstain(
            session, principal=principal, request_id=request_id,
            request=request, configuration=configuration, started=started,
        )
    evidence = []
    audit_evidence = []
    for hit in hits:
        # Re-assert authorization and bind the citation to the exact version
        # that was embedded. Ranking and citation must describe the same
        # record; a candidate that moved in between is dropped, never cited.
        row = await session.execute(
            select(Chunk, Document, DocumentVersion)
            .join(Document, Document.id == Chunk.document_pk)
            .join(Organization, Organization.id == Document.organization_id)
            .join(DocumentVersion, DocumentVersion.id == hit.document_version_id)
            .where(
                Chunk.id == hit.chunk_id,
                Chunk.document_pk == hit.document_pk,
                Chunk.organization_id == principal.organization_id,
                Document.organization_id == principal.organization_id,
                Document.record_state == "active",
                Document.current_version_id == DocumentVersion.id,
                DocumentVersion.organization_id == principal.organization_id,
                DocumentVersion.document_pk == Document.id,
                DocumentVersion.review_state == "approved",
                DocumentVersion.document_status == "active",
                Organization.status == "active",
                readable_document_clause(
                    organization_id=principal.organization_id,
                    principal_id=principal.id,
                ),
                (DocumentVersion.effective_from.is_(None)
                 | (DocumentVersion.effective_from <= func.now())),
                (DocumentVersion.effective_until.is_(None)
                 | (DocumentVersion.effective_until > func.now())),
            )
        )
        found = row.one_or_none()
        if found is None:
            continue
        chunk, document, version = found
        evidence.append({
            "citation_id": f"cit_{len(evidence) + 1}",
            "document_id": document.document_id,
            "document_version": version.version_number,
            "chunk_id": str(chunk.id),
            "document_type": document.document_type,
            "title": version.title,
            "source_uri": version.source_uri,
            "excerpt": chunk.content[:500],
            "scores": {"vector": hit.score, "full_text": None, "rrf": None},
            "relationship_path": [],
        })
        # Audit only identifiers and scores of evidence that is already in the
        # response; never the excerpt or question text.
        audit_evidence.append({
            "chunk_id": str(chunk.id),
            "document_version_id": str(version.id),
            "vector_score": round(float(hit.score), 6),
        })
    if not evidence:
        return await _abstain(
            session, principal=principal, request_id=request_id,
            request=request, configuration=configuration, started=started,
        )
    latency = _latency_payload(started)
    await _record_query_audit(
        session,
        principal=principal,
        request_id=request_id,
        event_type="query.executed",
        outcome="succeeded",
        details={
            "strategy": "vector",
            "response_mode": request.response_mode,
            "top_k": request.top_k,
            "retrieval_config_id": configuration.configuration_key,
            "embedding_model": configuration.embedding_model,
            "evidence_count": len(audit_evidence),
            "evidence": audit_evidence,
            "latency_ms": latency,
        },
    )
    return {
        "request_id": request_id,
        "query_id": str(uuid4()),
        "status": "evidence_returned",
        "strategy_used": "vector",
        "response_mode": request.response_mode,
        "answer": None,
        "abstained": False,
        "abstention_reason": None,
        "evidence": evidence,
        "configuration": _configuration_payload(configuration),
        "latency_ms": latency,
    }


async def _abstain(
    session: AsyncSession,
    *,
    principal: AuthenticatedPrincipal,
    request_id: str,
    request: RetrievalRequest,
    configuration: DerivedConfiguration,
    started: float,
    reason: str = "no_eligible_evidence",
) -> dict[str, object]:
    """Return a controlled refusal and audit it as a handled outcome.

    Abstention is not a server error: ``outcome`` stays ``succeeded`` and the
    reason is recorded. No document, chunk, score or candidate count is stored,
    so the event cannot become a side channel for restricted material.
    """
    latency = _latency_payload(started)
    await _record_query_audit(
        session,
        principal=principal,
        request_id=request_id,
        event_type="query.abstained",
        outcome="succeeded",
        reason_code=reason,
        details={
            "strategy": "vector",
            "response_mode": request.response_mode,
            "top_k": request.top_k,
            "retrieval_config_id": configuration.configuration_key,
            "embedding_model": configuration.embedding_model,
            "abstention_reason": reason,
            "evidence_count": 0,
            "latency_ms": latency,
        },
    )
    return {
        "request_id": request_id,
        "query_id": str(uuid4()),
        "status": "abstained",
        "strategy_used": "vector",
        "response_mode": request.response_mode,
        "answer": None,
        "abstained": True,
        "abstention_reason": reason,
        "evidence": [],
        "configuration": _configuration_payload(configuration),
        "latency_ms": latency,
    }

"""Minimal ACL-bounded vector query endpoint."""

import json
from dataclasses import dataclass
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.authorization.acl import resolve_readable_document_pks
from src.config import get_settings
from src.database import get_db
from src.ingestion.ollama_embeddings import OllamaEmbeddingClient, OllamaEmbeddingError
from src.models.storage import Chunk, DerivedConfiguration, Document, DocumentVersion, Organization, Principal
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
        select(Principal.id, Principal.organization_id)
        .join(Organization, Organization.id == Principal.organization_id)
        .where(
            Principal.id == principal_id,
            Principal.status == "active",
            Organization.status == "active",
        )
    )
    principal = row.one_or_none()
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication_required")
    return AuthenticatedPrincipal(id=principal[0], organization_id=principal[1])


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


@router.post("/queries")
async def execute_query(
    request: RetrievalRequest,
    session: AsyncSession = Depends(get_db),
    principal: AuthenticatedPrincipal = Depends(get_authenticated_principal),
) -> dict[str, object]:
    """Return authorized vector evidence, or abstain without disclosure.

    Candidate documents are resolved from current ACL grants before vector
    profile discovery and ranking; citations are built only from those hits.
    """
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
        return _abstention(request, configuration, started)
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
        return _abstention(request, configuration, started)
    hits = [
        hit for hit in hits
        if hit.score >= get_settings().QUERY_MIN_VECTOR_SCORE
    ]
    if not hits:
        return _abstention(
            request, configuration, started, reason="insufficient_evidence"
        )
    evidence = []
    for number, hit in enumerate(hits, start=1):
        row = await session.execute(
            select(Chunk, Document, DocumentVersion)
            .join(Document, Document.id == Chunk.document_pk)
            .join(DocumentVersion, DocumentVersion.id == Chunk.document_version_id)
            .where(Chunk.id == hit.chunk_id)
        )
        chunk, document, version = row.one()
        evidence.append({
            "citation_id": f"cit_{number}",
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
    return {
        "request_id": str(uuid4()),
        "query_id": str(uuid4()),
        "status": "evidence_returned",
        "strategy_used": "vector",
        "response_mode": request.response_mode,
        "answer": None,
        "abstained": False,
        "abstention_reason": None,
        "evidence": evidence,
        "configuration": _configuration_payload(configuration),
        "latency_ms": {
            "retrieval": int((perf_counter() - started) * 1_000),
            "generation": None,
            "total": int((perf_counter() - started) * 1_000),
        },
    }


def _abstention(
    request: RetrievalRequest,
    configuration: DerivedConfiguration,
    started: float,
    reason: str = "no_eligible_evidence",
) -> dict[str, object]:
    return {
        "request_id": str(uuid4()),
        "query_id": str(uuid4()),
        "status": "abstained",
        "strategy_used": "vector",
        "response_mode": request.response_mode,
        "answer": None,
        "abstained": True,
        "abstention_reason": reason,
        "evidence": [],
        "configuration": _configuration_payload(configuration),
        "latency_ms": {
            "retrieval": int((perf_counter() - started) * 1_000),
            "generation": None,
            "total": int((perf_counter() - started) * 1_000),
        },
    }

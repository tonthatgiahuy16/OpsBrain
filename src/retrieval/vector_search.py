from dataclasses import dataclass
from math import isfinite
from typing import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.storage import (
    Chunk,
    ChunkEmbedding,
    DerivedSet,
    Document,
    DocumentVersion,
    Organization,
)


@dataclass(frozen=True)
class EmbeddingProfile:
    model: str
    revision: str | None
    dimensions: int


@dataclass(frozen=True)
class VectorSearchHit:
    chunk_id: UUID
    document_pk: UUID
    document_version_id: UUID
    derived_set_id: UUID
    score: float


async def active_embedding_profiles(
    session: AsyncSession, *, organization_id: UUID,
    allowed_document_pks: set[UUID] | None = None,
) -> list[EmbeddingProfile]:
    """Return profiles behind active, currently retrieval-eligible sets.

    Query callers must resolve one profile from the active configuration and
    refuse to search when more than one profile exists in the same
    organization. Filtering by model alone keeps scores comparable but would
    silently drop documents embedded with another revision, which reduces
    recall without any error.
    """
    if allowed_document_pks is not None and not allowed_document_pks:
        return []

    select_statement = (
        select(
            ChunkEmbedding.embedding_model,
            ChunkEmbedding.embedding_model_revision,
            ChunkEmbedding.embedding_dimensions,
        )
        .distinct()
        .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
        .join(DerivedSet, DerivedSet.id == ChunkEmbedding.derived_set_id)
        .join(Document, Document.id == Chunk.document_pk)
        .join(
            DocumentVersion,
            DocumentVersion.id == DerivedSet.document_version_id,
        )
        .join(Organization, Organization.id == Document.organization_id)
        .where(
            ChunkEmbedding.organization_id == organization_id,
            DerivedSet.organization_id == organization_id,
            DerivedSet.is_active.is_(True),
            Document.organization_id == organization_id,
            Document.record_state == "active",
            Document.current_version_id == DerivedSet.document_version_id,
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.document_pk == Document.id,
            DocumentVersion.document_status == "active",
            DocumentVersion.review_state == "approved",
            (DocumentVersion.effective_from.is_(None)
             | (DocumentVersion.effective_from <= func.now())),
            (DocumentVersion.effective_until.is_(None)
             | (DocumentVersion.effective_until > func.now())),
            Organization.status == "active",
        )
    )
    if allowed_document_pks is not None:
        select_statement = select_statement.where(
            Chunk.document_pk.in_(allowed_document_pks)
        )
    rows = (await session.execute(select_statement)).all()
    profiles = {
        EmbeddingProfile(
            model=row[0],
            revision=row[1],
            dimensions=row[2],
        )
        for row in rows
    }
    return sorted(
        profiles,
        key=lambda profile: (
            profile.model,
            profile.revision or "",
            profile.dimensions,
        ),
    )


async def vector_top_k(
    session: AsyncSession, *, organization_id: UUID,
    allowed_document_pks: set[UUID], query_embedding: Sequence[float],
    embedding_model: str, embedding_model_revision: str | None, top_k: int,
) -> list[VectorSearchHit]:
    """Rank only candidate documents already authorized by the caller.

    The allowlist is supplied by the caller; this function never widens
    access. Chunks are filtered before ranking so unauthorized content cannot
    influence scores or consume the ``top_k`` budget.

    Only embeddings produced by ``embedding_model`` and
    ``embedding_model_revision`` with exactly ``len(query_embedding)``
    dimensions are comparable to the query. Stored vectors may use different
    models, revisions or dimensions in the same organization, so all three
    constraints are enforced in SQL instead of assuming a single embedding
    space. ``embedding_model_revision`` is required; pass ``None`` to match
    embeddings stored without a revision.
    """
    if not allowed_document_pks:
        return []
    if top_k < 1 or not query_embedding:
        raise ValueError("top_k and query_embedding must be non-empty")
    if not embedding_model.strip():
        raise ValueError("embedding_model must be non-empty")
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        for value in query_embedding
    ):
        raise ValueError("query_embedding must contain finite numeric values")
    if not any(value != 0 for value in query_embedding):
        raise ValueError("query_embedding must not be all zeros")

    distance = ChunkEmbedding.embedding.cosine_distance(
        list(query_embedding)
    ).label("distance")
    rows = (await session.execute(
        select(
            Chunk.id, Chunk.document_pk, Chunk.derived_set_id,
            DerivedSet.document_version_id, distance,
        )
        .join(Chunk, Chunk.id == ChunkEmbedding.chunk_id)
        .join(DerivedSet, DerivedSet.id == ChunkEmbedding.derived_set_id)
        .join(Document, Document.id == Chunk.document_pk)
        .join(
            DocumentVersion,
            DocumentVersion.id == DerivedSet.document_version_id,
        )
        .join(Organization, Organization.id == Document.organization_id)
        .where(
            ChunkEmbedding.organization_id == organization_id,
            ChunkEmbedding.embedding_model == embedding_model,
            ChunkEmbedding.embedding_model_revision.is_not_distinct_from(
                embedding_model_revision
            ),
            ChunkEmbedding.embedding_dimensions == len(query_embedding),
            Chunk.organization_id == organization_id,
            Chunk.document_pk.in_(allowed_document_pks),
            DerivedSet.organization_id == organization_id,
            DerivedSet.is_active.is_(True),
            Document.organization_id == organization_id,
            Document.record_state == "active",
            Document.current_version_id == DerivedSet.document_version_id,
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.document_pk == Document.id,
            DocumentVersion.document_status == "active",
            DocumentVersion.review_state == "approved",
            (DocumentVersion.effective_from.is_(None)
             | (DocumentVersion.effective_from <= func.now())),
            (DocumentVersion.effective_until.is_(None)
             | (DocumentVersion.effective_until > func.now())),
            Organization.status == "active",
        ).order_by(distance.asc(), Chunk.id.asc()).limit(top_k)
    )).all()
    return [
        VectorSearchHit(
            chunk_id=row[0],
            document_pk=row[1],
            derived_set_id=row[2],
            document_version_id=row[3],
            score=1.0 - float(row[4]),
        )
        for row in rows
    ]

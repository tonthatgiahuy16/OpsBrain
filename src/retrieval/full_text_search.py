"""ACL-bounded PostgreSQL full-text candidate retrieval."""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import cast, func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.storage import (
    Chunk,
    ChunkFullTextIndex,
    DerivedSet,
    Document,
    DocumentVersion,
    Organization,
)


@dataclass(frozen=True)
class FullTextSearchHit:
    chunk_id: UUID
    document_pk: UUID
    document_version_id: UUID
    derived_set_id: UUID
    score: float


async def full_text_top_k(
    session: AsyncSession,
    *,
    organization_id: UUID,
    allowed_document_pks: set[UUID],
    question: str,
    full_text_configuration: str,
    top_k: int,
) -> list[FullTextSearchHit]:
    """Rank only chunks the caller was already authorized to read.

    The allowlist is a mandatory input, so unauthorized rows are excluded by
    SQL before ``ts_rank_cd`` and ``LIMIT`` can expose their existence through
    rank, score, candidate budget, or response metadata.
    """
    if not allowed_document_pks:
        return []
    if not question.strip():
        raise ValueError("question must be non-empty")
    if full_text_configuration != "english":
        raise ValueError("unsupported full-text configuration")
    if top_k < 1:
        raise ValueError("top_k must be positive")

    configuration = cast(literal(full_text_configuration), REGCONFIG)
    query = func.websearch_to_tsquery(configuration, question)
    score = func.ts_rank_cd(ChunkFullTextIndex.search_vector, query, 32).label(
        "full_text_score"
    )
    rows = (
        await session.execute(
            select(
                Chunk.id,
                Chunk.document_pk,
                Chunk.derived_set_id,
                DerivedSet.document_version_id,
                score,
            )
            .join(
                Chunk,
                (Chunk.id == ChunkFullTextIndex.chunk_id)
                & (Chunk.derived_set_id == ChunkFullTextIndex.derived_set_id),
            )
            .join(DerivedSet, DerivedSet.id == ChunkFullTextIndex.derived_set_id)
            .join(Document, Document.id == Chunk.document_pk)
            .join(DocumentVersion, DocumentVersion.id == DerivedSet.document_version_id)
            .join(Organization, Organization.id == Document.organization_id)
            .where(
                ChunkFullTextIndex.organization_id == organization_id,
                ChunkFullTextIndex.full_text_configuration == full_text_configuration,
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
                ChunkFullTextIndex.search_vector.op("@@")(query),
            )
            .order_by(score.desc(), Chunk.id.asc())
            .limit(top_k)
        )
    ).all()
    return [
        FullTextSearchHit(
            chunk_id=row[0],
            document_pk=row[1],
            derived_set_id=row[2],
            document_version_id=row[3],
            score=float(row[4]),
        )
        for row in rows
    ]

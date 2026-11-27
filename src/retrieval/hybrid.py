"""Deterministic Reciprocal Rank Fusion for authorized retrieval candidates."""

from dataclasses import dataclass
from uuid import UUID

from src.retrieval.full_text_search import FullTextSearchHit
from src.retrieval.vector_search import VectorSearchHit


RRF_K = 60


@dataclass(frozen=True)
class HybridSearchHit:
    chunk_id: UUID
    document_pk: UUID
    document_version_id: UUID
    derived_set_id: UUID
    vector_score: float | None
    full_text_score: float | None
    rrf_score: float


def reciprocal_rank_fusion(
    *,
    vector_hits: list[VectorSearchHit],
    full_text_hits: list[FullTextSearchHit],
    top_k: int,
) -> list[HybridSearchHit]:
    """Fuse source ranks without treating incomparable scores as additive."""
    if top_k < 1:
        raise ValueError("top_k must be positive")

    merged: dict[UUID, HybridSearchHit] = {}

    def add_hit(hit, rank: int, source: str) -> None:
        contribution = 1.0 / (RRF_K + rank)
        existing = merged.get(hit.chunk_id)
        if existing is None:
            merged[hit.chunk_id] = HybridSearchHit(
                chunk_id=hit.chunk_id,
                document_pk=hit.document_pk,
                document_version_id=hit.document_version_id,
                derived_set_id=hit.derived_set_id,
                vector_score=hit.score if source == "vector" else None,
                full_text_score=hit.score if source == "full_text" else None,
                rrf_score=contribution,
            )
            return
        if (
            existing.document_pk != hit.document_pk
            or existing.document_version_id != hit.document_version_id
            or existing.derived_set_id != hit.derived_set_id
        ):
            raise ValueError("candidate sources disagree about chunk provenance")
        merged[hit.chunk_id] = HybridSearchHit(
            chunk_id=existing.chunk_id,
            document_pk=existing.document_pk,
            document_version_id=existing.document_version_id,
            derived_set_id=existing.derived_set_id,
            vector_score=hit.score if source == "vector" else existing.vector_score,
            full_text_score=(
                hit.score if source == "full_text" else existing.full_text_score
            ),
            rrf_score=existing.rrf_score + contribution,
        )

    for rank, hit in enumerate(vector_hits, start=1):
        add_hit(hit, rank, "vector")
    for rank, hit in enumerate(full_text_hits, start=1):
        add_hit(hit, rank, "full_text")

    return sorted(
        merged.values(),
        key=lambda hit: (-hit.rrf_score, str(hit.chunk_id)),
    )[:top_k]

from uuid import uuid4

import pytest

from src.retrieval.full_text_search import FullTextSearchHit
from src.retrieval.hybrid import RRF_K, reciprocal_rank_fusion
from src.retrieval.vector_search import VectorSearchHit
from src.api.queries import _resolve_strategy


def _vector(chunk_id, document_pk, version_id, derived_set_id, score):
    return VectorSearchHit(chunk_id, document_pk, version_id, derived_set_id, score)


def _full_text(chunk_id, document_pk, version_id, derived_set_id, score):
    return FullTextSearchHit(chunk_id, document_pk, version_id, derived_set_id, score)


def test_rrf_fuses_rank_without_adding_incomparable_source_scores():
    document_id, version_id, set_id = uuid4(), uuid4(), uuid4()
    first, second = uuid4(), uuid4()
    fused = reciprocal_rank_fusion(
        vector_hits=[_vector(first, document_id, version_id, set_id, 0.51)],
        full_text_hits=[
            _full_text(second, document_id, version_id, set_id, 0.99),
            _full_text(first, document_id, version_id, set_id, 0.01),
        ],
        top_k=2,
    )
    assert [hit.chunk_id for hit in fused] == [first, second]
    assert fused[0].rrf_score == pytest.approx(1 / (RRF_K + 1) + 1 / (RRF_K + 2))
    assert fused[0].vector_score == 0.51
    assert fused[0].full_text_score == 0.01
    assert fused[1].vector_score is None


def test_rrf_rejects_mismatched_provenance_for_the_same_chunk():
    chunk_id, document_id, version_id, set_id = uuid4(), uuid4(), uuid4(), uuid4()
    with pytest.raises(ValueError, match="provenance"):
        reciprocal_rank_fusion(
            vector_hits=[_vector(chunk_id, document_id, version_id, set_id, 0.5)],
            full_text_hits=[_full_text(chunk_id, uuid4(), version_id, set_id, 1.0)],
            top_k=1,
        )


def test_auto_uses_vector_only_when_full_text_is_not_configured():
    assert _resolve_strategy("auto", full_text_configuration=None) == "vector"
    assert _resolve_strategy("auto", full_text_configuration="english") == "hybrid"

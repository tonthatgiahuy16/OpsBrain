from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.derived_activation import activate_derived_set
from src.ingestion.embedding_build import persist_chunk_embeddings
from src.ingestion.full_text_build import persist_chunk_full_text_indexes
from src.models.storage import Chunk, ChunkEmbedding
from src.retrieval.vector_search import (
    EmbeddingProfile,
    active_embedding_profiles,
    vector_top_k,
)
from tests.test_derived_set_activation import SAMPLE_DOCUMENT, _seed_activation_inputs


OTHER_SAMPLE_DOCUMENT = (
    Path(__file__).parents[1]
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "checkout-service.md"
)


def _vector_at(*, dimensions: int, index: int, magnitude: float) -> list[float]:
    vector = [0.0] * dimensions
    vector[index] = magnitude
    return vector


async def _seed_embedded_document(
    session: AsyncSession,
    *,
    organization_id=None,
    source_document: Path = SAMPLE_DOCUMENT,
    embedding_model: str = "test-embed-model",
    embedding_model_revision: str | None = None,
    embedding_dimensions: int = 384,
    vector_for_chunk: Callable[[Chunk], list[float]] | None = None,
    full_text_configuration: str | None = "english",
):
    (
        organization_id,
        document_pk,
        version,
        configuration,
        derived_set,
    ) = await _seed_activation_inputs(
        session,
        embedding_model=embedding_model,
        embedding_model_revision=embedding_model_revision,
        embedding_dimensions=embedding_dimensions,
        full_text_configuration=full_text_configuration,
        organization_id=organization_id,
        source_document=source_document,
    )
    chunks = list((
        await session.scalars(
            select(Chunk)
            .where(Chunk.derived_set_id == derived_set.id)
            .order_by(Chunk.chunk_index)
        )
    ).all())
    default_vector = _vector_at(
        dimensions=embedding_dimensions, index=0, magnitude=1.0
    )
    await persist_chunk_embeddings(
        session,
        organization_id=organization_id,
        derived_set_id=derived_set.id,
        vectors_by_chunk_id={
            chunk.id: (
                default_vector if vector_for_chunk is None else vector_for_chunk(chunk)
            )
            for chunk in chunks
        },
    )
    if full_text_configuration is not None:
        await persist_chunk_full_text_indexes(
            session,
            organization_id=organization_id,
            derived_set_id=derived_set.id,
        )
    await activate_derived_set(
        session,
        organization_id=organization_id,
        derived_set_id=derived_set.id,
    )
    return {
        "organization_id": organization_id,
        "document_pk": document_pk,
        "version": version,
        "configuration": configuration,
        "derived_set": derived_set,
        "chunks": chunks,
    }


@pytest.mark.asyncio
async def test_vector_search_ranks_only_acl_authorized_active_document_chunks(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_embedded_document(session)
            organization_id = seeded["organization_id"]
            document_pk = seeded["document_pk"]
            vector = _vector_at(
                dimensions=seeded["configuration"].embedding_dimensions,
                index=0,
                magnitude=1.0,
            )
            hits = await vector_top_k(
                session,
                organization_id=organization_id,
                allowed_document_pks={document_pk},
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=3,
            )
            assert hits
            assert {hit.document_pk for hit in hits} == {document_pk}
            assert await vector_top_k(
                session,
                organization_id=organization_id,
                allowed_document_pks=set(),
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=3,
            ) == []

            seeded["version"].effective_until = (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            )
            await session.flush()
            assert await vector_top_k(
                session,
                organization_id=organization_id,
                allowed_document_pks={document_pk},
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=3,
            ) == []
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_vector_search_returns_chunk_ids_from_chunks_not_embeddings(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_embedded_document(session)
            vector = _vector_at(
                dimensions=seeded["configuration"].embedding_dimensions,
                index=0,
                magnitude=1.0,
            )
            hits = await vector_top_k(
                session,
                organization_id=seeded["organization_id"],
                allowed_document_pks={seeded["document_pk"]},
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=len(seeded["chunks"]),
            )
            chunk_ids = {chunk.id for chunk in seeded["chunks"]}
            embedding_ids = set((
                await session.scalars(
                    select(ChunkEmbedding.id).where(
                        ChunkEmbedding.derived_set_id == seeded["derived_set"].id
                    )
                )
            ).all())
            assert {hit.chunk_id for hit in hits} == chunk_ids
            assert {hit.chunk_id for hit in hits}.isdisjoint(embedding_ids)
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_vector_search_excludes_unauthorized_document_with_better_score(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed = await _seed_embedded_document(
                session,
                vector_for_chunk=lambda chunk: [0.6, 0.8] + [0.0] * 382,
            )
            dimensions = allowed["configuration"].embedding_dimensions
            denied = await _seed_embedded_document(
                session,
                organization_id=allowed["organization_id"],
                source_document=OTHER_SAMPLE_DOCUMENT,
            )
            all_documents = {allowed["document_pk"], denied["document_pk"]}
            vector = _vector_at(dimensions=dimensions, index=0, magnitude=1.0)

            # Precondition: without the allowlist the denied document wins.
            unrestricted = await vector_top_k(
                session,
                organization_id=allowed["organization_id"],
                allowed_document_pks=all_documents,
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=1,
            )
            assert unrestricted[0].document_pk == denied["document_pk"]

            hits = await vector_top_k(
                session,
                organization_id=allowed["organization_id"],
                allowed_document_pks={allowed["document_pk"]},
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=10,
            )
            assert hits
            assert {hit.document_pk for hit in hits} == {allowed["document_pk"]}
            assert all(hit.score < unrestricted[0].score for hit in hits)
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_vector_search_returns_empty_for_cross_organization_document_pk(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_a = await _seed_embedded_document(session)
            organization_b = await _seed_embedded_document(session)
            vector = _vector_at(
                dimensions=organization_a["configuration"].embedding_dimensions,
                index=0,
                magnitude=1.0,
            )
            hits = await vector_top_k(
                session,
                organization_id=organization_a["organization_id"],
                allowed_document_pks={
                    organization_a["document_pk"],
                    organization_b["document_pk"],
                },
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=10,
            )
            assert hits
            assert {hit.document_pk for hit in hits} == {
                organization_a["document_pk"]
            }
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_vector_search_isolates_embedding_model_and_dimensions(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            short = await _seed_embedded_document(
                session,
                embedding_model="model-short",
                embedding_dimensions=384,
            )
            long = await _seed_embedded_document(
                session,
                organization_id=short["organization_id"],
                source_document=OTHER_SAMPLE_DOCUMENT,
                embedding_model="model-long",
                embedding_dimensions=768,
            )
            all_documents = {short["document_pk"], long["document_pk"]}

            short_hits = await vector_top_k(
                session,
                organization_id=short["organization_id"],
                allowed_document_pks=all_documents,
                query_embedding=_vector_at(
                    dimensions=384, index=0, magnitude=1.0
                ),
                embedding_model="model-short",
                embedding_model_revision=None,
                top_k=10,
            )
            assert short_hits
            assert {hit.document_pk for hit in short_hits} == {
                short["document_pk"]
            }

            long_hits = await vector_top_k(
                session,
                organization_id=short["organization_id"],
                allowed_document_pks=all_documents,
                query_embedding=_vector_at(
                    dimensions=768, index=0, magnitude=1.0
                ),
                embedding_model="model-long",
                embedding_model_revision=None,
                top_k=10,
            )
            assert long_hits
            assert {hit.document_pk for hit in long_hits} == {long["document_pk"]}
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_vector_search_isolates_embedding_model_revision(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            older = await _seed_embedded_document(
                session,
                embedding_model="shared-model",
                embedding_model_revision="revision-1",
            )
            newer = await _seed_embedded_document(
                session,
                organization_id=older["organization_id"],
                source_document=OTHER_SAMPLE_DOCUMENT,
                embedding_model="shared-model",
                embedding_model_revision="revision-2",
            )
            all_documents = {older["document_pk"], newer["document_pk"]}
            vector = _vector_at(dimensions=384, index=0, magnitude=1.0)

            for revision, expected_document in (
                ("revision-1", older["document_pk"]),
                ("revision-2", newer["document_pk"]),
            ):
                hits = await vector_top_k(
                    session,
                    organization_id=older["organization_id"],
                    allowed_document_pks=all_documents,
                    query_embedding=vector,
                    embedding_model="shared-model",
                    embedding_model_revision=revision,
                    top_k=10,
                )
                assert hits
                assert {hit.document_pk for hit in hits} == {expected_document}

            # No stored embedding matches a NULL revision for this model, so
            # the NULL-safe filter must return nothing instead of everything.
            assert await vector_top_k(
                session,
                organization_id=older["organization_id"],
                allowed_document_pks=all_documents,
                query_embedding=vector,
                embedding_model="shared-model",
                embedding_model_revision=None,
                top_k=10,
            ) == []
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_active_embedding_profiles_lists_every_distinct_profile(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            short = await _seed_embedded_document(
                session,
                embedding_model="model-short",
                embedding_dimensions=384,
            )
            assert await active_embedding_profiles(
                session, organization_id=short["organization_id"]
            ) == [EmbeddingProfile(model="model-short", revision=None, dimensions=384)]

            long = await _seed_embedded_document(
                session,
                organization_id=short["organization_id"],
                source_document=OTHER_SAMPLE_DOCUMENT,
                embedding_model="model-long",
                embedding_model_revision="revision-2",
                embedding_dimensions=768,
            )
            profiles = await active_embedding_profiles(
                session, organization_id=short["organization_id"]
            )
            assert [(p.model, p.revision, p.dimensions) for p in profiles] == [
                ("model-long", "revision-2", 768),
                ("model-short", None, 384),
            ]

            scoped = await active_embedding_profiles(
                session,
                organization_id=short["organization_id"],
                allowed_document_pks={long["document_pk"]},
            )
            assert [(p.model, p.revision, p.dimensions) for p in scoped] == [
                ("model-long", "revision-2", 768)
            ]
            long["version"].effective_until = (
                datetime.now(timezone.utc) - timedelta(minutes=5)
            )
            await session.flush()
            assert await active_embedding_profiles(
                session, organization_id=short["organization_id"]
            ) == [
                EmbeddingProfile(model="model-short", revision=None, dimensions=384)
            ]
            assert await active_embedding_profiles(
                session,
                organization_id=short["organization_id"],
                allowed_document_pks=set(),
            ) == []
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_vector_search_limits_top_k_and_orders_by_descending_score(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            dimensions = 384
            (
                organization_id,
                document_pk,
                _,
                _,
                derived_set,
            ) = await _seed_activation_inputs(
                session,
                embedding_model="test-embed-model",
                embedding_dimensions=dimensions,
            )
            chunks = list((
                await session.scalars(
                    select(Chunk)
                    .where(Chunk.derived_set_id == derived_set.id)
                    .order_by(Chunk.chunk_index)
                )
            ).all())
            assert len(chunks) >= 2
            await persist_chunk_embeddings(
                session,
                organization_id=organization_id,
                derived_set_id=derived_set.id,
                vectors_by_chunk_id={
                    chunk.id: [
                        1.0 - 0.1 * chunk.chunk_index,
                        1.0,
                        *([0.0] * (dimensions - 2)),
                    ]
                    for chunk in chunks
                },
            )
            await activate_derived_set(
                session,
                organization_id=organization_id,
                derived_set_id=derived_set.id,
            )
            vector = _vector_at(dimensions=dimensions, index=0, magnitude=1.0)

            all_hits = await vector_top_k(
                session,
                organization_id=organization_id,
                allowed_document_pks={document_pk},
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=len(chunks),
            )
            scores = [hit.score for hit in all_hits]
            assert len(all_hits) == len(chunks)
            assert scores == sorted(scores, reverse=True)

            limited = await vector_top_k(
                session,
                organization_id=organization_id,
                allowed_document_pks={document_pk},
                query_embedding=vector,
                embedding_model="test-embed-model",
                embedding_model_revision=None,
                top_k=1,
            )
            assert limited == all_hits[:1]
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_vector_search_rejects_all_zero_query_embedding(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_embedded_document(session)
            with pytest.raises(ValueError, match="must not be all zeros"):
                await vector_top_k(
                    session,
                    organization_id=seeded["organization_id"],
                    allowed_document_pks={seeded["document_pk"]},
                    query_embedding=[0.0] * 384,
                    embedding_model="test-embed-model",
                    embedding_model_revision=None,
                    top_k=3,
                )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query_embedding",
    [[float("nan")], [float("inf")], [float("-inf")], [True]],
)
async def test_vector_search_rejects_non_finite_or_boolean_query_values(
    query_embedding,
):
    with pytest.raises(ValueError, match="finite numeric values"):
        await vector_top_k(
            None,
            organization_id=uuid4(),
            allowed_document_pks={uuid4()},
            query_embedding=query_embedding,
            embedding_model="test-embed-model",
            embedding_model_revision=None,
            top_k=1,
        )

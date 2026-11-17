from hashlib import sha256

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.canonicalization import normalize_markdown_body
from src.ingestion.embedding_build import persist_chunk_embeddings
from src.models.storage import Chunk, ChunkEmbedding
from tests.test_derived_set_activation import _seed_activation_inputs


@pytest.mark.asyncio
async def test_persist_chunk_embeddings_is_complete_and_idempotent(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, _, configuration, derived_set = (
                await _seed_activation_inputs(
                    session, embedding_model="test-embed-model",
                )
            )
            chunks = list((await session.scalars(select(Chunk).where(
                Chunk.organization_id == organization_id,
                Chunk.derived_set_id == derived_set.id,
            ).order_by(Chunk.chunk_index))).all())
            vectors = {
                chunk.id: [float(chunk.chunk_index)] * configuration.embedding_dimensions
                for chunk in chunks
            }

            created = await persist_chunk_embeddings(
                session,
                organization_id=organization_id,
                derived_set_id=derived_set.id,
                vectors_by_chunk_id=vectors,
            )
            assert len(created) == len(chunks)
            assert [embedding.embedding_input_hash for embedding in created] == [
                sha256(normalize_markdown_body(chunk.content).encode("utf-8")).hexdigest()
                for chunk in chunks
            ]
            assert await session.scalar(select(func.count()).select_from(ChunkEmbedding)) == len(chunks)

            replay = await persist_chunk_embeddings(
                session,
                organization_id=organization_id,
                derived_set_id=derived_set.id,
                vectors_by_chunk_id=vectors,
            )
            assert [embedding.id for embedding in replay] == [embedding.id for embedding in created]
            assert await session.scalar(select(func.count()).select_from(ChunkEmbedding)) == len(chunks)
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_persist_chunk_embeddings_rejects_incomplete_or_wrong_dimension_batch(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, _, configuration, derived_set = (
                await _seed_activation_inputs(
                    session, embedding_model="test-embed-model",
                )
            )
            chunks = list((await session.scalars(select(Chunk).where(
                Chunk.organization_id == organization_id,
                Chunk.derived_set_id == derived_set.id,
            ))).all())
            assert chunks

            with pytest.raises(ValueError, match="embedding batch must cover every chunk"):
                await persist_chunk_embeddings(
                    session,
                    organization_id=organization_id,
                    derived_set_id=derived_set.id,
                    vectors_by_chunk_id={},
                )
            with pytest.raises(ValueError, match="embedding vector dimension does not match configuration"):
                await persist_chunk_embeddings(
                    session,
                    organization_id=organization_id,
                    derived_set_id=derived_set.id,
                    vectors_by_chunk_id={chunk.id: [0.0] * 3 for chunk in chunks},
                )
            assert await session.scalar(select(func.count()).select_from(ChunkEmbedding)) == 0
        finally:
            await session.close()
            await transaction.rollback()

"""Persist complete, deterministic embedding batches for a derived set."""

from collections.abc import Mapping, Sequence
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.canonicalization import normalize_markdown_body
from src.models.storage import (
    Chunk,
    ChunkEmbedding,
    DerivedConfiguration,
    DerivedSet,
)


async def persist_chunk_embeddings(
    session: AsyncSession,
    *,
    organization_id: UUID,
    derived_set_id: UUID,
    vectors_by_chunk_id: Mapping[UUID, Sequence[float]],
) -> list[ChunkEmbedding]:
    """Persist one vector per chunk, or replay the complete existing batch.

    The caller supplies provider output. This function deliberately has no
    network dependency so provider retry and durable writes remain separate.
    """
    async with session.begin_nested():
        derived_set = await session.scalar(
            select(DerivedSet)
            .where(
                DerivedSet.organization_id == organization_id,
                DerivedSet.id == derived_set_id,
            )
            .with_for_update()
        )
        if derived_set is None:
            raise ValueError("derived set not found")

        configuration = await session.scalar(
            select(DerivedConfiguration)
            .where(
                DerivedConfiguration.organization_id == organization_id,
                DerivedConfiguration.id == derived_set.configuration_id,
            )
            .with_for_update()
        )
        if (
            configuration is None
            or configuration.status != "active"
            or configuration.embedding_model is None
            or configuration.embedding_dimensions is None
        ):
            raise ValueError("derived configuration does not enable embeddings")

        chunks = list((await session.scalars(
            select(Chunk)
            .where(
                Chunk.organization_id == organization_id,
                Chunk.derived_set_id == derived_set_id,
            )
            .order_by(Chunk.chunk_index)
            .with_for_update()
        )).all())
        chunk_ids = {chunk.id for chunk in chunks}
        if not chunks or set(vectors_by_chunk_id) != chunk_ids:
            raise ValueError("embedding batch must cover every chunk")
        if any(
            len(vector) != configuration.embedding_dimensions
            for vector in vectors_by_chunk_id.values()
        ):
            raise ValueError(
                "embedding vector dimension does not match configuration"
            )

        existing = list((await session.scalars(
            select(ChunkEmbedding)
            .where(
                ChunkEmbedding.organization_id == organization_id,
                ChunkEmbedding.derived_set_id == derived_set_id,
            )
            .with_for_update()
        )).all())
        if existing:
            if len(existing) != len(chunks):
                raise ValueError("embedding set is incomplete")
            existing_by_chunk_id = {
                embedding.chunk_id: embedding for embedding in existing
            }
            return [existing_by_chunk_id[chunk.id] for chunk in chunks]

        embeddings = [
            ChunkEmbedding(
                organization_id=organization_id,
                derived_set_id=derived_set_id,
                chunk_id=chunk.id,
                embedding_model=configuration.embedding_model,
                embedding_model_revision=configuration.embedding_model_revision,
                embedding_dimensions=configuration.embedding_dimensions,
                embedding_input_hash=sha256(
                    normalize_markdown_body(chunk.content).encode("utf-8")
                ).hexdigest(),
                embedding=list(vectors_by_chunk_id[chunk.id]),
            )
            for chunk in chunks
        ]
        session.add_all(embeddings)
        await session.flush()
        return embeddings

"""Persist complete, deterministic PostgreSQL full-text indexes for a derived set."""

from hashlib import sha256
from uuid import UUID

from sqlalchemy import cast, func, literal, select
from sqlalchemy.dialects.postgresql import REGCONFIG
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.canonicalization import normalize_markdown_body
from src.models.storage import (
    Chunk,
    ChunkFullTextIndex,
    DerivedConfiguration,
    DerivedSet,
)

ALLOWED_FULL_TEXT_CONFIGURATIONS = frozenset({"english"})


async def persist_chunk_full_text_indexes(
    session: AsyncSession,
    *,
    organization_id: UUID,
    derived_set_id: UUID,
) -> list[ChunkFullTextIndex]:
    """Persist one full-text index row per chunk, or replay the existing batch."""
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
            or configuration.full_text_configuration not in ALLOWED_FULL_TEXT_CONFIGURATIONS
        ):
            raise ValueError("derived configuration does not enable full_text")
        ft_config = configuration.full_text_configuration

        chunks = list(
            (
                await session.scalars(
                    select(Chunk)
                    .where(
                        Chunk.organization_id == organization_id,
                        Chunk.derived_set_id == derived_set_id,
                    )
                    .order_by(Chunk.chunk_index)
                    .with_for_update()
                )
            ).all()
        )
        if not chunks:
            raise ValueError("full-text source chunks are not available")

        expected_hashes = {
            chunk.id: sha256(
                normalize_markdown_body(chunk.content).encode("utf-8")
            ).hexdigest()
            for chunk in chunks
        }

        existing = list(
            (
                await session.scalars(
                    select(ChunkFullTextIndex).where(
                        ChunkFullTextIndex.organization_id == organization_id,
                        ChunkFullTextIndex.derived_set_id == derived_set_id,
                    )
                )
            ).all()
        )
        if existing:
            if len(existing) != len(chunks):
                raise ValueError("full-text index set is incomplete")
            existing_by_chunk_id = {row.chunk_id: row for row in existing}
            if set(existing_by_chunk_id) != set(expected_hashes):
                raise ValueError("full-text index set is incomplete")
            for chunk in chunks:
                row = existing_by_chunk_id[chunk.id]
                if (
                    row.full_text_configuration != ft_config
                    or row.input_hash != expected_hashes[chunk.id]
                ):
                    raise ValueError("full-text index set does not match configuration")
            return [existing_by_chunk_id[chunk.id] for chunk in chunks]

        regconfig_expr = cast(literal(ft_config), REGCONFIG)
        rows = [
            ChunkFullTextIndex(
                organization_id=organization_id,
                derived_set_id=derived_set_id,
                chunk_id=chunk.id,
                document_pk=chunk.document_pk,
                document_version_id=chunk.document_version_id,
                full_text_configuration=ft_config,
                input_hash=expected_hashes[chunk.id],
                search_vector=func.to_tsvector(
                    regconfig_expr,
                    normalize_markdown_body(chunk.content),
                ),
            )
            for chunk in chunks
        ]
        session.add_all(rows)
        await session.flush()
        return rows

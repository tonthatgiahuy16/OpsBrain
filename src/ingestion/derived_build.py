from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.chunking import CHUNKING_VERSION, chunk_markdown_body
from src.models.storage import Chunk, DerivedConfiguration, DerivedSet, DocumentVersion


async def build_chunk_derived_set(
    session: AsyncSession,
    *,
    organization_id: UUID,
    document_pk: UUID,
    document_version_id: UUID,
    configuration_id: UUID,
) -> DerivedSet:
    """Build and persist inactive chunks for one document version.

    The caller owns the surrounding transaction. A nested transaction makes a
    failed chunk insert roll back both the derived set and every chunk created
    by this operation without leaving the caller's transaction unusable.
    """

    async with session.begin_nested():
        document_version = await session.scalar(
            select(DocumentVersion)
            .where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.document_pk == document_pk,
                DocumentVersion.id == document_version_id,
            )
            .with_for_update()
        )
        if document_version is None:
            raise ValueError("document version not found")

        configuration = await session.scalar(
            select(DerivedConfiguration).where(
                DerivedConfiguration.organization_id == organization_id,
                DerivedConfiguration.id == configuration_id,
            )
        )
        if configuration is None:
            raise ValueError("derived configuration not found")
        if configuration.status != "active":
            raise ValueError("derived configuration is not active")
        if configuration.chunking_version != CHUNKING_VERSION:
            raise ValueError(
                "derived configuration chunking_version does not match "
                "the active chunker"
            )

        max_characters = configuration.configuration_payload.get(
            "max_characters"
        )
        if type(max_characters) is not int or max_characters <= 0:
            raise ValueError(
                "configuration max_characters must be a positive integer"
            )

        existing_set = await session.scalar(
            select(DerivedSet)
            .where(
                DerivedSet.organization_id == organization_id,
                DerivedSet.document_version_id == document_version_id,
                DerivedSet.configuration_id == configuration_id,
                DerivedSet.status == "ready",
            )
            .order_by(DerivedSet.build_number.desc())
        )
        if existing_set is not None:
            return existing_set

        deterministic_chunks = chunk_markdown_body(
            document_version.markdown_body,
            chunking_version=CHUNKING_VERSION,
            max_characters=max_characters,
        )
        if not deterministic_chunks:
            raise ValueError("document version produced no chunks")

        highest_build_number = await session.scalar(
            select(func.max(DerivedSet.build_number)).where(
                DerivedSet.organization_id == organization_id,
                DerivedSet.document_version_id == document_version_id,
                DerivedSet.configuration_id == configuration_id,
            )
        )
        build_number = (highest_build_number or 0) + 1

        derived_set = DerivedSet(
            organization_id=organization_id,
            document_pk=document_pk,
            document_version_id=document_version_id,
            configuration_id=configuration_id,
            build_number=build_number,
            status="building",
            started_at=datetime.now(timezone.utc),
        )
        session.add(derived_set)
        await session.flush()

        session.add_all(
            Chunk(
                organization_id=organization_id,
                derived_set_id=derived_set.id,
                document_pk=document_pk,
                document_version_id=document_version_id,
                chunk_key=chunk.chunk_key,
                chunk_index=chunk.chunk_index,
                chunking_version=chunk.chunking_version,
                heading_path=list(chunk.heading_path),
                content=chunk.content,
                content_hash=chunk.content_hash,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                token_count=chunk.token_count,
            )
            for chunk in deterministic_chunks
        )
        await session.flush()

        derived_set.status = "ready"
        derived_set.completed_at = datetime.now(timezone.utc)
        await session.flush()

        return derived_set

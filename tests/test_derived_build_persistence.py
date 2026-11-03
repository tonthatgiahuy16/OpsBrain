from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.chunking import CHUNKING_VERSION, chunk_markdown_body
from src.ingestion.derived_build import build_chunk_derived_set
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
    Chunk,
    DerivedConfiguration,
    DerivedSet,
    Organization,
    Principal,
)


SAMPLE_DOCUMENT = (
    Path(__file__).parents[1]
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "fraud-service.md"
)


async def _seed_build_inputs(
    session: AsyncSession,
    *,
    max_characters: object = 1200,
    chunking_version: str = CHUNKING_VERSION,
    configuration_status: str = "active",
):
    organization_id = uuid4()
    principal_id = uuid4()
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")
    document = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )

    session.add_all(
        [
            Organization(
                id=organization_id,
                organization_key=f"derived-build-org-{organization_id}",
                display_name="Derived Build Test",
                status="active",
            ),
            Principal(
                id=principal_id,
                organization_id=organization_id,
                principal_key=f"derived-build-user-{principal_id}",
                principal_kind="user",
                display_name="Derived Build User",
                status="active",
            ),
        ]
    )
    await session.flush()

    created = await ingest_validated_document(
        session,
        organization_id=organization_id,
        actor_principal_id=principal_id,
        document=document,
    )
    configuration = DerivedConfiguration(
        organization_id=organization_id,
        configuration_key=f"derived-build-config-{organization_id}",
        chunking_version=chunking_version,
        configuration_payload={"max_characters": max_characters},
        configuration_hash="a" * 64,
        status=configuration_status,
        created_by_principal_id=principal_id,
    )
    session.add(configuration)
    await session.flush()

    return organization_id, created, configuration, document


@pytest.mark.asyncio
async def test_build_chunk_derived_set_persists_ready_inactive_chunks(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, created, configuration, document = (
                await _seed_build_inputs(session)
            )

            derived_set = await build_chunk_derived_set(
                session,
                organization_id=organization_id,
                document_pk=created.document_pk,
                document_version_id=created.document_version_id,
                configuration_id=configuration.id,
            )

            stored_chunks = list(
                (
                    await session.scalars(
                        select(Chunk)
                        .where(Chunk.derived_set_id == derived_set.id)
                        .order_by(Chunk.chunk_index)
                    )
                ).all()
            )
            expected_chunks = chunk_markdown_body(
                document.body,
                chunking_version=CHUNKING_VERSION,
                max_characters=1200,
            )

            assert derived_set.organization_id == organization_id
            assert derived_set.document_pk == created.document_pk
            assert (
                derived_set.document_version_id
                == created.document_version_id
            )
            assert derived_set.configuration_id == configuration.id
            assert derived_set.build_number == 1
            assert derived_set.status == "ready"
            assert derived_set.is_active is False
            assert derived_set.completed_at is not None
            assert [
                (
                    chunk.chunk_key,
                    chunk.chunk_index,
                    chunk.chunking_version,
                    chunk.heading_path,
                    chunk.content,
                    chunk.content_hash,
                    chunk.start_offset,
                    chunk.end_offset,
                    chunk.token_count,
                )
                for chunk in stored_chunks
            ] == [
                (
                    chunk.chunk_key,
                    chunk.chunk_index,
                    chunk.chunking_version,
                    list(chunk.heading_path),
                    chunk.content,
                    chunk.content_hash,
                    chunk.start_offset,
                    chunk.end_offset,
                    chunk.token_count,
                )
                for chunk in expected_chunks
            ]
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_build_chunk_derived_set_reuses_ready_build_for_same_source_and_configuration(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, created, configuration, _ = (
                await _seed_build_inputs(session)
            )
            first = await build_chunk_derived_set(
                session,
                organization_id=organization_id,
                document_pk=created.document_pk,
                document_version_id=created.document_version_id,
                configuration_id=configuration.id,
            )
            second = await build_chunk_derived_set(
                session,
                organization_id=organization_id,
                document_pk=created.document_pk,
                document_version_id=created.document_version_id,
                configuration_id=configuration.id,
            )

            derived_set_count = await session.scalar(
                select(func.count())
                .select_from(DerivedSet)
                .where(
                    DerivedSet.organization_id == organization_id,
                    DerivedSet.document_version_id
                    == created.document_version_id,
                    DerivedSet.configuration_id == configuration.id,
                )
            )
            chunk_count = await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.derived_set_id == first.id)
            )

            assert second.id == first.id
            assert derived_set_count == 1
            assert chunk_count > 0
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_build_chunk_derived_set_rolls_back_partial_output(
    migration_database_engine,
    monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, created, configuration, _ = (
                await _seed_build_inputs(session)
            )

            def mismatched_chunks(*args, **kwargs):
                chunks = chunk_markdown_body(*args, **kwargs)
                return [
                    replace(chunk, chunking_version="mismatched-version")
                    for chunk in chunks
                ]

            monkeypatch.setattr(
                "src.ingestion.derived_build.chunk_markdown_body",
                mismatched_chunks,
            )

            with pytest.raises(
                DBAPIError,
                match="chunking version does not match derived configuration",
            ):
                await build_chunk_derived_set(
                    session,
                    organization_id=organization_id,
                    document_pk=created.document_pk,
                    document_version_id=created.document_version_id,
                    configuration_id=configuration.id,
                )

            derived_set_count = await session.scalar(
                select(func.count())
                .select_from(DerivedSet)
                .where(DerivedSet.organization_id == organization_id)
            )
            chunk_count = await session.scalar(
                select(func.count())
                .select_from(Chunk)
                .where(Chunk.organization_id == organization_id)
            )

            assert derived_set_count == 0
            assert chunk_count == 0
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_build_chunk_derived_set_rejects_invalid_max_characters(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            for invalid_max_characters in (None, True, 0, -1, 1.5, "1200"):
                organization_id, created, configuration, _ = (
                    await _seed_build_inputs(
                        session,
                        max_characters=invalid_max_characters,
                    )
                )

                with pytest.raises(
                    ValueError,
                    match=(
                        "configuration max_characters must be a positive integer"
                    ),
                ):
                    await build_chunk_derived_set(
                        session,
                        organization_id=organization_id,
                        document_pk=created.document_pk,
                        document_version_id=created.document_version_id,
                        configuration_id=configuration.id,
                    )

                derived_set_count = await session.scalar(
                    select(func.count())
                    .select_from(DerivedSet)
                    .where(DerivedSet.organization_id == organization_id)
                )
                assert derived_set_count == 0
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_build_chunk_derived_set_rejects_inactive_configuration_before_writing(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, created, configuration, _ = (
                await _seed_build_inputs(
                    session,
                    configuration_status="retired",
                )
            )

            with pytest.raises(
                ValueError,
                match="derived configuration is not active",
            ):
                await build_chunk_derived_set(
                    session,
                    organization_id=organization_id,
                    document_pk=created.document_pk,
                    document_version_id=created.document_version_id,
                    configuration_id=configuration.id,
                )

            assert await session.scalar(
                select(func.count())
                .select_from(DerivedSet)
                .where(DerivedSet.organization_id == organization_id)
            ) == 0
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_build_chunk_derived_set_rejects_chunker_version_mismatch_before_writing(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, created, configuration, _ = (
                await _seed_build_inputs(
                    session,
                    chunking_version="chunk-v2",
                )
            )

            with pytest.raises(
                ValueError,
                match="does not match the active chunker",
            ):
                await build_chunk_derived_set(
                    session,
                    organization_id=organization_id,
                    document_pk=created.document_pk,
                    document_version_id=created.document_version_id,
                    configuration_id=configuration.id,
                )

            assert await session.scalar(
                select(func.count())
                .select_from(DerivedSet)
                .where(DerivedSet.organization_id == organization_id)
            ) == 0
        finally:
            await session.close()
            await transaction.rollback()

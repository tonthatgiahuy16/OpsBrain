from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError, IntegrityError

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


@pytest.mark.asyncio
async def test_chunk_can_be_persisted(migration_database_engine):
    organization_id = uuid4()
    principal_id = uuid4()
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")
    document = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            session.add_all(
                [
                    Organization(
                        id=organization_id,
                        organization_key=f"chunk-test-{organization_id}",
                        display_name="Chunk Test",
                        status="active",
                    ),
                    Principal(
                        id=principal_id,
                        organization_id=organization_id,
                        principal_key=f"chunk-test-user-{principal_id}",
                        principal_kind="user",
                        display_name="Chunk Test User",
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
                configuration_key="chunk-test-configuration",
                chunking_version="chunk-test-v1",
                configuration_payload={"chunk_size": 200},
                configuration_hash="a" * 64,
                status="active",
                created_by_principal_id=principal_id,
            )
            session.add(configuration)
            await session.flush()

            derived_set = DerivedSet(
                organization_id=organization_id,
                document_pk=created.document_pk,
                document_version_id=created.document_version_id,
                configuration_id=configuration.id,
                build_number=1,
                status="pending",
            )
            session.add(derived_set)
            await session.flush()

            chunk = Chunk(
                organization_id=organization_id,
                derived_set_id=derived_set.id,
                document_pk=created.document_pk,
                document_version_id=created.document_version_id,
                chunk_key="chunk-0000",
                chunk_index=0,
                chunking_version="chunk-test-v1",
                heading_path=["Overview"],
                content="Fraud service handles payment-risk decisions.",
                content_hash="b" * 64,
                start_offset=0,
                end_offset=45,
                token_count=6,
            )
            session.add(chunk)
            await session.flush()

            updated_document = validate_markdown_document(
                markdown + "\n\nUpdated chunk source body.",
                source_uri=SAMPLE_DOCUMENT.as_uri(),
            )
            updated = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=updated_document,
            )

            invalid_chunks = [
                (
                    Chunk(
                        organization_id=organization_id,
                        derived_set_id=derived_set.id,
                        document_pk=created.document_pk,
                        document_version_id=updated.document_version_id,
                        chunk_key="chunk-wrong-version",
                        chunk_index=1,
                        chunking_version="chunk-test-v1",
                        heading_path=[],
                        content="Wrong source version.",
                        content_hash="c" * 64,
                    ),
                    "chunk does not match derived set source",
                ),
                (
                    Chunk(
                        organization_id=organization_id,
                        derived_set_id=derived_set.id,
                        document_pk=created.document_pk,
                        document_version_id=created.document_version_id,
                        chunk_key="chunk-wrong-chunking-version",
                        chunk_index=2,
                        chunking_version="unexpected-v2",
                        heading_path=[],
                        content="Wrong chunking version.",
                        content_hash="d" * 64,
                    ),
                    "chunking version does not match derived configuration",
                ),
            ]

            for invalid_chunk, message in invalid_chunks:
                with pytest.raises(DBAPIError, match=message):
                    async with session.begin_nested():
                        session.add(invalid_chunk)
                        await session.flush()

            stored = await session.scalar(
                select(Chunk).where(Chunk.id == chunk.id)
            )
            assert stored is not None
            assert stored.derived_set_id == derived_set.id
            assert stored.chunk_index == 0
            assert stored.heading_path == ["Overview"]
            with pytest.raises(DBAPIError, match="chunks are immutable"):
                async with session.begin_nested():
                    await session.execute(
                        update(Chunk)
                        .where(Chunk.id == chunk.id)
                        .values(content="Mutated chunk content.")
                    )
            base_values = {
                "organization_id": organization_id,
                "derived_set_id": derived_set.id,
                "document_pk": created.document_pk,
                "document_version_id": created.document_version_id,
                "chunk_key": "chunk-base",
                "chunk_index": 10,
                "chunking_version": "chunk-test-v1",
                "heading_path": [],
                "content": "Valid chunk content.",
                "content_hash": "e" * 64,
                "start_offset": 0,
                "end_offset": 20,
                "token_count": 3,
            }

            duplicate_cases = [
                (
                    {
                        **base_values,
                        "chunk_key": "chunk-0000",
                        "chunk_index": 3,
                    },
                    "uq_chunks_organization_id_derived_set_id_chunk_key",
                ),
                (
                    {
                        **base_values,
                        "chunk_key": "chunk-duplicate-index",
                        "chunk_index": 0,
                    },
                    "uq_chunks_organization_id_derived_set_id_chunk_index",
                ),
            ]

            for values, constraint_name in duplicate_cases:
                with pytest.raises(IntegrityError, match=constraint_name):
                    async with session.begin_nested():
                        session.add(Chunk(**values))
                        await session.flush()

            invalid_cases = [
                ({"chunk_key": " "}, "ck_chunks_chunk_key_not_blank"),
                ({"chunk_index": -1}, "ck_chunks_chunk_index_non_negative"),
                ({"content": " "}, "ck_chunks_content_not_blank"),
                (
                    {"content_hash": "not-a-sha256"},
                    "ck_chunks_content_hash_sha256_hex",
                ),
                (
                    {"start_offset": -1},
                    "ck_chunks_start_offset_non_negative",
                ),
                (
                    {"end_offset": -1},
                    "ck_chunks_end_offset_non_negative",
                ),
                (
                    {"end_offset": 0},
                    "ck_chunks_offsets_ordered",
                ),
                (
                    {"token_count": -1},
                    "ck_chunks_token_count_non_negative",
                ),
            ]
            other_organization_id = uuid4()
            session.add(
                Organization(
                    id=other_organization_id,
                    organization_key=f"other-chunk-test-{other_organization_id}",
                    display_name="Other Chunk Test",
                    status="active",
                )
            )
            await session.flush()

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        Chunk(
                            organization_id=other_organization_id,
                            derived_set_id=derived_set.id,
                            document_pk=created.document_pk,
                            document_version_id=created.document_version_id,
                            chunk_key="cross-organization-chunk",
                            chunk_index=50,
                            chunking_version="chunk-test-v1",
                            heading_path=[],
                            content="Cross organization chunk.",
                            content_hash="f" * 64,
                        )
                    )
                    await session.flush()
            with pytest.raises(
                DBAPIError,
                match="derived set provenance is immutable after chunks exist",
            ):
                async with session.begin_nested():
                    await session.execute(
                        update(DerivedSet)
                        .where(DerivedSet.id == derived_set.id)
                        .values(
                            document_version_id=updated.document_version_id,
                        )
                    )
            for index, (overrides, constraint_name) in enumerate(
                invalid_cases,
                start=1,
            ):
                values = {
                    **base_values,
                    "chunk_key": f"invalid-{index}",
                    "chunk_index": 10 + index,
                }
                values.update(overrides)

                with pytest.raises(IntegrityError, match=constraint_name):
                    async with session.begin_nested():
                        session.add(Chunk(**values))
                        await session.flush()
        finally:
            await session.close()
            await transaction.rollback()

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.ingestion.canonicalization import canonical_content_hash
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
    Document,
    DocumentVersion,
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


def test_equivalent_document_representation_has_same_hash():
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")

    reordered_markdown = markdown.replace(
        (
            "document_id: fraud-service\n"
            "document_type: service\n"
            "title: Fraud Service"
        ),
        (
            "title: Fraud Service\n"
            "document_type: service\n"
            "document_id: fraud-service"
        ),
        1,
    )

    first = validate_markdown_document(
        markdown,
        source_uri="file:///vault/services/fraud-service.md",
    )
    equivalent = validate_markdown_document(
        reordered_markdown.replace("\n", "\r\n"),
        source_uri="file:///moved/fraud-service.md",
    )

    assert canonical_content_hash(first) == canonical_content_hash(
        equivalent
    )


def test_semantic_body_change_has_different_hash():
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")

    original = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )
    changed = validate_markdown_document(
        markdown.replace(
            "returns a risk decision",
            "returns a reviewed risk decision",
            1,
        ),
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )

    assert canonical_content_hash(original) != canonical_content_hash(
        changed
    )


@pytest.mark.asyncio
async def test_create_then_unchanged_replay_persists_one_version(
    database_engine: AsyncEngine,
):
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")
    validated_document = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )

    organization_id = uuid4()
    principal_id = uuid4()

    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
        )

        try:
            session.add(
                Organization(
                    id=organization_id,
                    organization_key=f"test-{organization_id}",
                    display_name="Ingestion Test Organization",
                    status="active",
                )
            )
            session.add(
                Principal(
                    id=principal_id,
                    organization_id=organization_id,
                    principal_key=f"test-user-{principal_id}",
                    principal_kind="user",
                    display_name="Ingestion Test User",
                    status="active",
                )
            )
            await session.flush()

            created = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=validated_document,
            )
            replayed = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=validated_document,
            )
            await session.flush()

            stored_document = (
                await session.scalars(
                    select(Document).where(
                        Document.organization_id
                        == organization_id,
                        Document.document_id
                        == "fraud-service",
                    )
                )
            ).one()

            version_count = await session.scalar(
                select(func.count())
                .select_from(DocumentVersion)
                .where(
                    DocumentVersion.organization_id
                    == organization_id,
                    DocumentVersion.document_pk
                    == stored_document.id,
                )
            )

            assert created.outcome == "created"
            assert replayed.outcome == "unchanged"
            assert created.document_pk == replayed.document_pk
            assert (
                created.document_version_id
                == replayed.document_version_id
            )
            assert created.version_number == 1
            assert replayed.version_number == 1
            assert stored_document.current_version_id == (
                created.document_version_id
            )
            assert version_count == 1
        finally:
            await session.close()
            await transaction.rollback()

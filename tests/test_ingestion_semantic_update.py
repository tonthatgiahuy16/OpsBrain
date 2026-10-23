from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
    Document,
    DocumentVersion,
    Organization,
    Principal,
    AuditEvent,
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
async def test_body_update_creates_version_two_and_preserves_history(
    database_engine: AsyncEngine,
):
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")
    changed_markdown = markdown.replace(
        "returns a risk decision",
        "returns a reviewed risk decision",
        1,
    )

    original_document = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )
    changed_document = validate_markdown_document(
        changed_markdown,
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
                    display_name="Semantic Update Test Organization",
                    status="active",
                )
            )
            session.add(
                Principal(
                    id=principal_id,
                    organization_id=organization_id,
                    principal_key=f"test-user-{principal_id}",
                    principal_kind="user",
                    display_name="Semantic Update Test User",
                    status="active",
                )
            )
            await session.flush()

            created = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=original_document,
            )
            updated = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=changed_document,
            )
            await session.flush()

            stored_document = await session.scalar(
                select(Document).where(
                    Document.organization_id == organization_id,
                    Document.document_id == "fraud-service",
                )
            )
            versions = (
                await session.scalars(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.organization_id
                        == organization_id,
                        DocumentVersion.document_pk
                        == stored_document.id,
                    )
                    .order_by(DocumentVersion.version_number)
                )
            ).all()

            update_events = (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.organization_id == organization_id,
                        AuditEvent.event_type == "ingestion.item.updated",
                    )
                )
            ).all()

            assert len(update_events) == 1

            update_event = update_events[0]
            assert update_event.actor_principal_id == principal_id
            assert update_event.document_pk == created.document_pk
            assert update_event.document_version_id == (
                updated.document_version_id
            )
            assert update_event.details == {}

            assert created.outcome == "created"
            assert updated.outcome == "updated"
            assert updated.document_pk == created.document_pk
            assert updated.document_version_id != (
                created.document_version_id
            )
            assert updated.version_number == 2

            assert len(versions) == 2

            original_version, updated_version = versions

            assert original_version.id == (
                created.document_version_id
            )
            assert original_version.version_number == 1
            assert original_version.parent_version_id is None
            assert original_version.markdown_body == (
                original_document.body
            )

            assert updated_version.id == (
                updated.document_version_id
            )
            assert updated_version.version_number == 2
            assert updated_version.parent_version_id == (
                original_version.id
            )
            assert updated_version.change_kind == "content_update"
            assert updated_version.review_state == "in_review"
            assert updated_version.markdown_body == (
                changed_document.body
            )
            assert stored_document.current_version_id == (
                updated_version.id
            )

            replayed = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=changed_document,
            )
            await session.flush()

            replayed_versions = (
                await session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.organization_id
                        == organization_id,
                        DocumentVersion.document_pk
                        == stored_document.id,
                    )
                )
            ).all()

            assert replayed.outcome == "unchanged"
            assert replayed.document_version_id == (
                updated_version.id
            )
            assert len(replayed_versions) == 2
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_metadata_only_update_creates_metadata_version(
    database_engine: AsyncEngine,
):
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")
    changed_markdown = markdown.replace(
        "title: Fraud Service",
        "title: Fraud Risk Service",
        1,
    )

    original_document = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )
    changed_document = validate_markdown_document(
        changed_markdown,
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
                    display_name="Metadata Update Test Organization",
                    status="active",
                )
            )
            session.add(
                Principal(
                    id=principal_id,
                    organization_id=organization_id,
                    principal_key=f"test-user-{principal_id}",
                    principal_kind="user",
                    display_name="Metadata Update Test User",
                    status="active",
                )
            )
            await session.flush()

            created = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=original_document,
            )
            updated = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=changed_document,
            )
            await session.flush()

            stored_document = await session.scalar(
                select(Document).where(
                    Document.organization_id == organization_id,
                    Document.document_id == "fraud-service",
                )
            )
            versions = (
                await session.scalars(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.organization_id
                        == organization_id,
                        DocumentVersion.document_pk
                        == stored_document.id,
                    )
                    .order_by(DocumentVersion.version_number)
                )
            ).all()

            assert updated.outcome == "updated"
            assert updated.document_pk == created.document_pk
            assert updated.version_number == 2
            assert len(versions) == 2

            original_version, updated_version = versions

            assert updated_version.parent_version_id == (
                original_version.id
            )
            assert updated_version.change_kind == "metadata_update"
            assert updated_version.title == "Fraud Risk Service"
            assert updated_version.markdown_body == (
                original_version.markdown_body
            )
            assert updated_version.content_hash != (
                original_version.content_hash
            )
            assert updated_version.review_state == "in_review"
            assert updated_version.frontmatter_snapshot["title"] == (
                "Fraud Risk Service"
            )
            assert stored_document.current_version_id == (
                updated_version.id
            )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_failed_update_preserves_current_version_and_history(
    database_engine: AsyncEngine,
):
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")
    changed_markdown = markdown.replace(
        "returns a risk decision",
        "returns a failed-update risk decision",
        1,
    )

    original_document = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )
    changed_document = validate_markdown_document(
        changed_markdown,
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
                    display_name="Failed Update Test Organization",
                    status="active",
                )
            )
            session.add(
                Principal(
                    id=principal_id,
                    organization_id=organization_id,
                    principal_key=f"test-user-{principal_id}",
                    principal_kind="user",
                    display_name="Failed Update Test User",
                    status="active",
                )
            )
            await session.flush()

            created = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=original_document,
            )

            stored_document = await session.scalar(
                select(Document).where(
                    Document.organization_id == organization_id,
                    Document.document_id == "fraud-service",
                )
            )

            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    await ingest_validated_document(
                        session,
                        organization_id=organization_id,
                        actor_principal_id=uuid4(),
                        document=changed_document,
                    )

            await session.refresh(stored_document)

            versions = (
                await session.scalars(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.organization_id
                        == organization_id,
                        DocumentVersion.document_pk
                        == stored_document.id,
                    )
                    .order_by(DocumentVersion.version_number)
                )
            ).all()

            audit_event_count = await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(
                    AuditEvent.organization_id == organization_id
                )
            )

            assert audit_event_count == 1

            assert stored_document.current_version_id == (
                created.document_version_id
            )
            assert len(versions) == 1
            assert versions[0].id == created.document_version_id
            assert versions[0].version_number == 1
            assert versions[0].markdown_body == original_document.body
        finally:
            await session.close()
            await transaction.rollback()

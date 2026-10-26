from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.exc import DBAPIError

from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import (
    delete_document,
    ingest_validated_document,
)
from src.models.storage import (
    AuditEvent,
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


@pytest.mark.asyncio
async def test_delete_creates_tombstone_and_audit(
    database_engine: AsyncEngine,
):
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")
    document = validate_markdown_document(
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
                    display_name="Tombstone Test Organization",
                    status="active",
                )
            )
            session.add(
                Principal(
                    id=principal_id,
                    organization_id=organization_id,
                    principal_key=f"test-user-{principal_id}",
                    principal_kind="user",
                    display_name="Tombstone Test User",
                    status="active",
                )
            )
            await session.flush()

            created = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=document,
            )

            await delete_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document_id="fraud-service",
            )
            await session.flush()
            await delete_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document_id="fraud-service",
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
                        == created.document_pk,
                    )
                    .order_by(DocumentVersion.version_number)
                )
            ).all()
            delete_events = (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.organization_id == organization_id,
                        AuditEvent.document_pk == created.document_pk,
                        AuditEvent.event_type == "document.deleted",
                    )
                )
            ).all()

            assert stored_document.record_state == "deleted"
            assert stored_document.deleted_at is not None
            assert stored_document.deleted_by_principal_id == principal_id

            assert len(versions) == 2
            original_version, tombstone = versions

            assert tombstone.version_number == 2
            assert tombstone.parent_version_id == original_version.id
            assert tombstone.change_kind == "delete"
            assert tombstone.document_status == "revoked"
            assert tombstone.content_hash == original_version.content_hash
            assert tombstone.markdown_body == original_version.markdown_body
            assert (
                tombstone.frontmatter_snapshot
                == original_version.frontmatter_snapshot
            )
            assert stored_document.current_version_id == tombstone.id

            assert len(delete_events) == 1
            delete_event = delete_events[0]
            assert delete_event.actor_principal_id == principal_id
            assert delete_event.document_pk == created.document_pk
            assert delete_event.document_version_id == tombstone.id
            assert delete_event.details == {}
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_delete_rejects_cross_organization_actor_without_partial_state(
    database_engine: AsyncEngine,
):
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8")
    document = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )

    organization_id = uuid4()
    owner_principal_id = uuid4()
    other_organization_id = uuid4()
    other_principal_id = uuid4()

    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            session.add_all(
                [
                    Organization(
                        id=organization_id,
                        organization_key=f"test-{organization_id}",
                        display_name="Owner Organization",
                        status="active",
                    ),
                    Organization(
                        id=other_organization_id,
                        organization_key=f"test-{other_organization_id}",
                        display_name="Other Organization",
                        status="active",
                    ),
                    Principal(
                        id=owner_principal_id,
                        organization_id=organization_id,
                        principal_key=f"owner-{owner_principal_id}",
                        principal_kind="user",
                        display_name="Owner User",
                        status="active",
                    ),
                    Principal(
                        id=other_principal_id,
                        organization_id=other_organization_id,
                        principal_key=f"other-{other_principal_id}",
                        principal_kind="user",
                        display_name="Other User",
                        status="active",
                    ),
                ]
            )
            await session.flush()

            created = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=owner_principal_id,
                document=document,
            )

            # DB constraint test: principal exists, but not in this organization.
            with pytest.raises(DBAPIError):
                async with session.begin_nested():
                    await delete_document(
                        session,
                        organization_id=organization_id,
                        actor_principal_id=other_principal_id,
                        document_id="fraud-service",
                    )

            stored_document = await session.scalar(
                select(Document).where(
                    Document.organization_id == organization_id,
                    Document.document_id == "fraud-service",
                )
            )
            versions = (
                await session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.organization_id == organization_id,
                        DocumentVersion.document_pk == created.document_pk,
                    )
                )
            ).all()
            delete_events = (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.organization_id == organization_id,
                        AuditEvent.event_type == "document.deleted",
                    )
                )
            ).all()

            assert stored_document.record_state == "active"
            assert stored_document.deleted_at is None
            assert stored_document.deleted_by_principal_id is None
            assert stored_document.current_version_id == created.document_version_id
            assert len(versions) == 1
            assert delete_events == []
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_ingest_deleted_document_creates_restore_version(
    database_engine: AsyncEngine,
    monkeypatch,
):
    markdown = SAMPLE_DOCUMENT.read_text(encoding="utf-8").replace(
        "review_state: in_review",
        "review_state: draft",
        1,
    )
    document = validate_markdown_document(
        markdown,
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )

    organization_id = uuid4()
    principal_id = uuid4()

    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            session.add_all(
                [
                    Organization(
                        id=organization_id,
                        organization_key=f"test-{organization_id}",
                        display_name="Restore Test Organization",
                        status="active",
                    ),
                    Principal(
                        id=principal_id,
                        organization_id=organization_id,
                        principal_key=f"test-user-{principal_id}",
                        principal_kind="user",
                        display_name="Restore Test User",
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

            await delete_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document_id="fraud-service",
            )

            runbook_path = (
                SAMPLE_DOCUMENT.parents[1]
                / "runbooks"
                / "payment-service-503-diagnosis.md"
            )
            wrong_type_markdown = runbook_path.read_text(
                encoding="utf-8"
            ).replace(
                "document_id: payment-service-503-diagnosis",
                "document_id: fraud-service",
                1,
            )
            wrong_type_document = validate_markdown_document(
                wrong_type_markdown,
                source_uri=runbook_path.as_uri(),
            )

            with pytest.raises(
                ValueError,
                match="document_type cannot change",
            ):
                await ingest_validated_document(
                    session,
                    organization_id=organization_id,
                    actor_principal_id=principal_id,
                    document=wrong_type_document,
                )

            after_rejection = await session.scalar(
                select(Document).where(Document.id == created.document_pk)
            )
            versions_after_rejection = (
                await session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.document_pk == created.document_pk
                    )
                )
            ).all()
            restore_event_after_rejection = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.document_pk == created.document_pk,
                    AuditEvent.event_type == "document.restored",
                )
            )

            assert after_rejection.record_state == "deleted"
            assert after_rejection.deleted_at is not None
            assert after_rejection.deleted_by_principal_id == principal_id
            assert len(versions_after_rejection) == 2
            assert restore_event_after_rejection is None

            tombstone_id = after_rejection.current_version_id

            async def fail_restore_audit(*args, **kwargs):
                raise RuntimeError("simulated audit failure")

            with monkeypatch.context() as patch:
                patch.setattr(
                    "src.ingestion.lifecycle._append_ingestion_audit_event",
                    fail_restore_audit,
                )

                with pytest.raises(RuntimeError, match="simulated audit failure"):
                    async with session.begin_nested():
                        await ingest_validated_document(
                            session,
                            organization_id=organization_id,
                            actor_principal_id=principal_id,
                            document=document,
                        )

            after_failure = await session.scalar(
                select(Document).where(Document.id == created.document_pk)
            )
            versions_after_failure = (
                await session.scalars(
                    select(DocumentVersion).where(
                        DocumentVersion.document_pk == created.document_pk
                    )
                )
            ).all()
            restore_event_after_failure = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.document_pk == created.document_pk,
                    AuditEvent.event_type == "document.restored",
                )
            )

            assert after_failure.record_state == "deleted"
            assert after_failure.current_version_id == tombstone_id
            assert after_failure.deleted_at is not None
            assert after_failure.deleted_by_principal_id == principal_id
            assert len(versions_after_failure) == 2
            assert restore_event_after_failure is None

            restored_result = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=document,
            )

            replayed = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=document,
            )

            stored_document = await session.scalar(
                select(Document).where(
                    Document.id == created.document_pk,
                )
            )
            versions = (
                await session.scalars(
                    select(DocumentVersion)
                    .where(
                        DocumentVersion.document_pk == created.document_pk,
                    )
                    .order_by(DocumentVersion.version_number)
                )
            ).all()
            restore_events = (
                await session.scalars(
                    select(AuditEvent).where(
                        AuditEvent.document_pk == created.document_pk,
                        AuditEvent.event_type == "document.restored",
                    )
                )
            ).all()

            assert len(versions) == 3
            original, tombstone, restored = versions

            assert replayed.outcome == "unchanged"
            assert replayed.document_version_id == restored.id

            assert original.id == created.document_version_id
            assert restored.version_number == 3
            assert restored.parent_version_id == tombstone.id
            assert restored.change_kind == "restore"
            assert restored.review_state == "in_review"
            assert restored_result.outcome == "updated"
            assert restored_result.document_version_id == restored.id

            assert stored_document.record_state == "active"
            assert stored_document.deleted_at is None
            assert stored_document.deleted_by_principal_id is None
            assert stored_document.current_version_id == restored.id

            assert len(restore_events) == 1
            assert restore_events[0].document_version_id == restored.id
        finally:
            await session.close()
            await transaction.rollback()

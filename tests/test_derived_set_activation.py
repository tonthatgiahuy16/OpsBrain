from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.derived_activation import activate_derived_set
from src.ingestion.derived_build import build_chunk_derived_set
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
    DerivedConfiguration,
    DerivedSet,
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


async def _seed_activation_inputs(
    session: AsyncSession,
    *,
    document_status: str = "active",
    effective_from: datetime | None = None,
    effective_until: datetime | None = None,
    embedding_model: str | None = None,
):
    organization_id = uuid4()
    principal_id = uuid4()
    source = validate_markdown_document(
        SAMPLE_DOCUMENT.read_text(encoding="utf-8"),
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )
    session.add_all(
        [
            Organization(
                id=organization_id,
                organization_key=f"activation-org-{organization_id}",
                display_name="Derived Activation Test",
                status="active",
            ),
            Principal(
                id=principal_id,
                organization_id=organization_id,
                principal_key=f"activation-user-{principal_id}",
                principal_kind="user",
                display_name="Activation Test User",
                status="active",
            ),
        ]
    )
    await session.flush()

    ingested = await ingest_validated_document(
        session,
        organization_id=organization_id,
        actor_principal_id=principal_id,
        document=source,
    )
    initial_version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.id == ingested.document_version_id
        )
    )
    assert initial_version is not None

    # The review workflow is outside this slice. Seed its persisted result as
    # an immutable review-transition version so activation sees a real source.
    approved_values = {
        column.key: getattr(initial_version, column.key)
        for column in DocumentVersion.__table__.columns
        if column.key not in {"id", "created_at", "version_number", "parent_version_id"}
    }
    approved_values.update(
        {
            "id": uuid4(),
            "version_number": initial_version.version_number + 1,
            "parent_version_id": initial_version.id,
            "document_status": document_status,
            "review_state": "approved",
            "effective_from": effective_from,
            "effective_until": effective_until,
            "change_kind": "review_transition",
        }
    )
    approved_values["frontmatter_snapshot"] = {
        **initial_version.frontmatter_snapshot,
        "document_status": document_status,
        "review_state": "approved",
        "effective_from": effective_from.isoformat() if effective_from else None,
        "effective_until": effective_until.isoformat() if effective_until else None,
    }
    approved_version = DocumentVersion(**approved_values)
    session.add(approved_version)
    await session.flush()

    stored_document = await session.scalar(
        select(Document).where(
            Document.organization_id == organization_id,
            Document.id == ingested.document_pk,
        )
    )
    assert stored_document is not None
    stored_document.current_version_id = approved_version.id
    await session.flush()

    configuration = DerivedConfiguration(
        organization_id=organization_id,
        configuration_key=f"activation-config-{organization_id}",
        chunking_version="chunk-v1",
        embedding_model=embedding_model,
        embedding_dimensions=384 if embedding_model else None,
        configuration_payload={"max_characters": 1200},
        configuration_hash="a" * 64,
        status="active",
        created_by_principal_id=principal_id,
    )
    session.add(configuration)
    await session.flush()

    derived_set = await build_chunk_derived_set(
        session,
        organization_id=organization_id,
        document_pk=ingested.document_pk,
        document_version_id=approved_version.id,
        configuration_id=configuration.id,
    )
    return (
        organization_id,
        ingested.document_pk,
        approved_version,
        configuration,
        derived_set,
    )


@pytest.mark.asyncio
async def test_activation_atomically_replaces_previous_active_set(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, document_pk, version, _, derived_set = (
                await _seed_activation_inputs(session)
            )

            activated = await activate_derived_set(
                session,
                organization_id=organization_id,
                derived_set_id=derived_set.id,
            )

            active_sets = list(
                (
                    await session.scalars(
                        select(DerivedSet).where(
                            DerivedSet.organization_id == organization_id,
                            DerivedSet.document_pk == document_pk,
                            DerivedSet.is_active.is_(True),
                        )
                    )
                ).all()
            )
            assert activated.id == derived_set.id
            assert activated.status == "ready"
            assert activated.is_active is True
            assert activated.activated_at is not None
            assert activated.document_version_id == version.id
            assert [active.id for active in active_sets] == [derived_set.id]
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_activation_rejects_non_current_version_without_changing_active_set(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, document_pk, version, configuration, old_set = (
                await _seed_activation_inputs(session)
            )
            await activate_derived_set(
                session,
                organization_id=organization_id,
                derived_set_id=old_set.id,
            )

            next_version_values = {
                column.key: getattr(version, column.key)
                for column in DocumentVersion.__table__.columns
                if column.key
                not in {"id", "created_at", "version_number", "parent_version_id"}
            }
            next_version_values.update(
                {
                    "id": uuid4(),
                    "version_number": version.version_number + 1,
                    "parent_version_id": version.id,
                    "change_kind": "review_transition",
                }
            )
            next_version = DocumentVersion(**next_version_values)
            session.add(next_version)
            await session.flush()
            stored_document = await session.scalar(
                select(Document).where(Document.id == document_pk)
            )
            assert stored_document is not None
            stored_document.current_version_id = next_version.id
            await session.flush()
            new_set = await build_chunk_derived_set(
                session,
                organization_id=organization_id,
                document_pk=document_pk,
                document_version_id=next_version.id,
                configuration_id=configuration.id,
            )

            with pytest.raises(ValueError, match="source version is not current"):
                await activate_derived_set(
                    session,
                    organization_id=organization_id,
                    derived_set_id=old_set.id,
                )

            assert old_set.is_active is True
            assert new_set.is_active is False

            activated_new_set = await activate_derived_set(
                session,
                organization_id=organization_id,
                derived_set_id=new_set.id,
            )
            active_sets = list(
                (
                    await session.scalars(
                        select(DerivedSet).where(
                            DerivedSet.organization_id == organization_id,
                            DerivedSet.document_pk == document_pk,
                            DerivedSet.is_active.is_(True),
                        )
                    )
                ).all()
            )
            assert activated_new_set.id == new_set.id
            assert old_set.is_active is False
            assert new_set.is_active is True
            assert [active.id for active in active_sets] == [new_set.id]
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_activation_rejects_expired_source_before_changing_active_set(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, document_pk, _, _, derived_set = (
                await _seed_activation_inputs(
                    session,
                    effective_until=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
            )
            with pytest.raises(
                ValueError,
                match="source document is not retrieval-eligible",
            ):
                await activate_derived_set(
                    session,
                    organization_id=organization_id,
                    derived_set_id=derived_set.id,
                )

            assert derived_set.is_active is False
            assert await session.scalar(
                select(func.count()).select_from(DerivedSet).where(
                    DerivedSet.organization_id == organization_id,
                    DerivedSet.document_pk == document_pk,
                    DerivedSet.is_active.is_(True),
                )
            ) == 0
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_activation_rejects_configuration_with_unstored_embedding_component(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, _, _, _, derived_set = await _seed_activation_inputs(
                session,
                embedding_model="test-embed-model",
            )
            with pytest.raises(
                ValueError,
                match="required derived components are incomplete",
            ):
                await activate_derived_set(
                    session,
                    organization_id=organization_id,
                    derived_set_id=derived_set.id,
                )

            assert derived_set.is_active is False
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize("document_status", ["deprecated", "revoked"])
async def test_activation_rejects_non_active_source_lifecycle(
    migration_database_engine,
    document_status,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, _, _, _, derived_set = await _seed_activation_inputs(
                session,
                document_status=document_status,
            )
            with pytest.raises(
                ValueError,
                match="source document is not retrieval-eligible",
            ):
                await activate_derived_set(
                    session,
                    organization_id=organization_id,
                    derived_set_id=derived_set.id,
                )

            assert derived_set.is_active is False
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_activation_rejects_disabled_organization(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, _, _, _, derived_set = await _seed_activation_inputs(
                session
            )
            await session.execute(
                update(Organization)
                .where(Organization.id == organization_id)
                .values(status="disabled")
            )

            with pytest.raises(
                ValueError,
                match="source document is not retrieval-eligible",
            ):
                await activate_derived_set(
                    session,
                    organization_id=organization_id,
                    derived_set_id=derived_set.id,
                )

            assert derived_set.is_active is False
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_activation_rejects_derived_set_that_is_not_ready(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)

        try:
            organization_id, _, _, _, derived_set = await _seed_activation_inputs(
                session
            )
            await session.execute(
                update(DerivedSet)
                .where(DerivedSet.id == derived_set.id)
                .values(status="stale")
            )

            with pytest.raises(ValueError, match="derived set is not ready"):
                await activate_derived_set(
                    session,
                    organization_id=organization_id,
                    derived_set_id=derived_set.id,
                )

            assert derived_set.is_active is False
        finally:
            await session.close()
            await transaction.rollback()

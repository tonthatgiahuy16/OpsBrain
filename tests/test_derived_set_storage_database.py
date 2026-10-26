from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
    DerivedConfiguration,
    DerivedSet,
    Organization,
    Principal,
)

from datetime import datetime, timezone

SAMPLE_DOCUMENT = (
    Path(__file__).parents[1]
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "fraud-service.md"
)


@pytest.mark.asyncio
async def test_derived_set_can_be_persisted(migration_database_engine):
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
            session.add_all([
                Organization(
                    id=organization_id,
                    organization_key=f"test-{organization_id}",
                    display_name="Derived Set Test",
                    status="active",
                ),
                Principal(
                    id=principal_id,
                    organization_id=organization_id,
                    principal_key=f"test-user-{principal_id}",
                    principal_kind="user",
                    display_name="Test User",
                    status="active",
                ),
            ])
            await session.flush()

            created = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=document,
            )

            configuration = DerivedConfiguration(
                organization_id=organization_id,
                configuration_key="test-configuration",
                chunking_version="test-v1",
                configuration_payload={"example": True},
                configuration_hash="a" * 64,
                status="active",
                created_by_principal_id=principal_id,
            )
            session.add(configuration)
            await session.flush()

            assert configuration.first_used_at is None

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

            await session.refresh(configuration)
            first_used_at = configuration.first_used_at
            assert first_used_at is not None

            stored = await session.scalar(
                select(DerivedSet).where(DerivedSet.id == derived_set.id)
            )
            assert stored is not None
            assert stored.document_version_id == created.document_version_id
            assert stored.configuration_id == configuration.id
            assert stored.status == "pending"
            assert stored.is_active is False
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        DerivedSet(
                            organization_id=organization_id,
                            document_pk=created.document_pk,
                            document_version_id=created.document_version_id,
                            configuration_id=configuration.id,
                            build_number=1,
                            status="pending",
                        )
                    )
                    await session.flush()
            invalid_cases = [
                (
                    {"build_number": 0},
                    "ck_derived_sets_build_number_positive",
                ),
                (
                    {"status": "unknown"},
                    "ck_derived_sets_status_allowed",
                ),
                (
                    {"status": "building"},
                    "ck_derived_sets_building_timestamps",
                ),
                (
                    {"status": "ready"},
                    "ck_derived_sets_terminal_completed_at",
                ),
                (
                    {
                        "status": "stale",
                        "is_active": True,
                        "completed_at": datetime.now(timezone.utc),
                        "activated_at": datetime.now(timezone.utc),
                    },
                    "ck_derived_sets_active_requires_ready",
                ),
                (
                    {
                        "status": "pending",
                        "started_at": datetime.now(timezone.utc),
                    },
                    "ck_derived_sets_pending_timestamps",
                ),
                (
                    {
                        "status": "failed",
                        "completed_at": datetime.now(timezone.utc),
                    },
                    "ck_derived_sets_failed_requires_code",
                ),
                (
                    {
                        "status": "ready",
                        "is_active": True,
                        "completed_at": datetime.now(timezone.utc),
                    },
                    "ck_derived_sets_active_requires_activated_at",
                ),
            ]

            for overrides, constraint_name in invalid_cases:
                values = {
                    "organization_id": organization_id,
                    "document_pk": created.document_pk,
                    "document_version_id": created.document_version_id,
                    "configuration_id": configuration.id,
                    "build_number": 2,
                    "status": "pending",
                }
                values.update(overrides)

                with pytest.raises(IntegrityError, match=constraint_name):
                    async with session.begin_nested():
                        session.add(DerivedSet(**values))
                        await session.flush()
            now = datetime.now(timezone.utc)

            session.add(
                DerivedSet(
                    organization_id=organization_id,
                    document_pk=created.document_pk,
                    document_version_id=created.document_version_id,
                    configuration_id=configuration.id,
                    build_number=2,
                    status="ready",
                    is_active=True,
                    completed_at=now,
                    activated_at=now,
                )
            )
            await session.flush()

            with pytest.raises(
                IntegrityError,
                match="uq_derived_sets_one_active_per_document",
            ):
                async with session.begin_nested():
                    session.add(
                        DerivedSet(
                            organization_id=organization_id,
                            document_pk=created.document_pk,
                            document_version_id=created.document_version_id,
                            configuration_id=configuration.id,
                            build_number=3,
                            status="ready",
                            is_active=True,
                            completed_at=now,
                            activated_at=now,
                        )
                    )
                    await session.flush()
            other_organization_id = uuid4()
            other_principal_id = uuid4()

            session.add_all([
                Organization(
                    id=other_organization_id,
                    organization_key=f"test-{other_organization_id}",
                    display_name="Other Organization",
                    status="active",
                ),
                Principal(
                    id=other_principal_id,
                    organization_id=other_organization_id,
                    principal_key=f"test-user-{other_principal_id}",
                    principal_kind="user",
                    display_name="Other User",
                    status="active",
                ),
            ])
            await session.flush()

            other_configuration = DerivedConfiguration(
                organization_id=other_organization_id,
                configuration_key="other-configuration",
                chunking_version="test-v1",
                configuration_payload={"example": True},
                configuration_hash="b" * 64,
                status="active",
                created_by_principal_id=other_principal_id,
            )
            session.add(other_configuration)
            await session.flush()

            with pytest.raises(
                IntegrityError,
                match="fk_derived_sets_configuration",
            ):
                async with session.begin_nested():
                    session.add(
                        DerivedSet(
                            organization_id=organization_id,
                            document_pk=created.document_pk,
                            document_version_id=created.document_version_id,
                            configuration_id=other_configuration.id,
                            build_number=1,
                            status="pending",
                        )
                    )
                    await session.flush()
            other_created = await ingest_validated_document(
                session,
                organization_id=other_organization_id,
                actor_principal_id=other_principal_id,
                document=document,
            )

            with pytest.raises(
                IntegrityError,
                match="ck_derived_sets_build_number_positive",
            ):
                async with session.begin_nested():
                    session.add(
                        DerivedSet(
                            organization_id=other_organization_id,
                            document_pk=other_created.document_pk,
                            document_version_id=other_created.document_version_id,
                            configuration_id=other_configuration.id,
                            build_number=0,
                            status="pending",
                        )
                    )
                    await session.flush()

            await session.refresh(other_configuration)
            assert other_configuration.first_used_at is None

            with pytest.raises(
                IntegrityError,
                match="fk_derived_sets_document_version",
            ):
                async with session.begin_nested():
                    session.add(
                        DerivedSet(
                            organization_id=organization_id,
                            document_pk=other_created.document_pk,
                            document_version_id=created.document_version_id,
                            configuration_id=configuration.id,
                            build_number=4,
                            status="pending",
                        )
                    )
                    await session.flush()

            second_path = SAMPLE_DOCUMENT.parent / "checkout-service.md"
            second_document = validate_markdown_document(
                second_path.read_text(encoding="utf-8"),
                source_uri=second_path.as_uri(),
            )
            second_created = await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=second_document,
            )

            with pytest.raises(
                IntegrityError,
                match="fk_derived_sets_document_version",
            ):
                async with session.begin_nested():
                    session.add(
                        DerivedSet(
                            organization_id=organization_id,
                            document_pk=second_created.document_pk,
                            document_version_id=created.document_version_id,
                            configuration_id=configuration.id,
                            build_number=5,
                            status="pending",
                        )
                    )
                    await session.flush()

            with pytest.raises(
                DBAPIError,
                match="used derived configuration is immutable",
            ):
                async with session.begin_nested():
                    await session.execute(
                        update(DerivedConfiguration)
                        .where(DerivedConfiguration.id == configuration.id)
                        .values(chunking_version="changed-v2")
                    )
            await session.refresh(configuration)
            assert configuration.chunking_version == "test-v1"

            await session.execute(
                update(DerivedConfiguration)
                .where(DerivedConfiguration.id == other_configuration.id)
                .values(chunking_version="updated-before-use")
            )
            await session.refresh(other_configuration)
            assert other_configuration.chunking_version == "updated-before-use"
            await session.execute(
                update(DerivedSet)
                .where(
                    DerivedSet.configuration_id == configuration.id,
                    DerivedSet.is_active.is_(True),
                )
                .values(status="stale", is_active=False)
            )

            await session.execute(
                delete(DerivedSet).where(
                    DerivedSet.configuration_id == configuration.id,
                    DerivedSet.is_active.is_(False),
                )
            )

            remaining_set = await session.scalar(
                select(DerivedSet.id).where(
                    DerivedSet.configuration_id == configuration.id
                )
            )
            assert remaining_set is None

            await session.refresh(configuration)
            assert configuration.first_used_at == first_used_at

            with pytest.raises(
                DBAPIError,
                match="used derived configuration is immutable",
            ):
                async with session.begin_nested():
                    await session.execute(
                        update(DerivedConfiguration)
                        .where(DerivedConfiguration.id == configuration.id)
                        .values(chunking_version="changed-after-cleanup")
                    )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_application_role_cannot_delete_derived_sets(database_engine):
    async with database_engine.connect() as connection:
        current_user = await connection.scalar(text("SELECT current_user"))
        assert current_user == "opsbrain_app"

        can_delete = await connection.scalar(
            select(
                text(
                    "has_table_privilege("
                    "current_user, 'derived_sets', 'DELETE'"
                    ")"
                )
            )
        )
        assert can_delete is False

        with pytest.raises(DBAPIError, match="permission denied"):
            await connection.execute(
                text("DELETE FROM derived_sets WHERE false")
            )

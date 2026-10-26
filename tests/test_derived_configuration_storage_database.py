from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.models.storage import (
    DerivedConfiguration,
    Organization,
    Principal,
)


@pytest.mark.asyncio
async def test_derived_configuration_can_be_persisted(
    database_engine: AsyncEngine,
):
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
                        display_name="Derived Configuration Test",
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
                ]
            )
            await session.flush()

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
            await session.refresh(configuration)

            assert configuration.id is not None
            assert configuration.organization_id == organization_id
            assert configuration.configuration_payload == {"example": True}
            assert configuration.created_by_principal_id == principal_id
            with pytest.raises(
                IntegrityError,
                match="uq_derived_configurations_organization_id_configuration_key",
            ):
                async with session.begin_nested():
                    session.add(
                        DerivedConfiguration(
                            organization_id=organization_id,
                            configuration_key="test-configuration",
                            chunking_version="test-v1",
                            configuration_payload={"example": True},
                            configuration_hash="b" * 64,
                            status="active",
                            created_by_principal_id=principal_id,
                        )
                    )
                    await session.flush()

            count = await session.scalar(
                select(func.count())
                .select_from(DerivedConfiguration)
                .where(
                    DerivedConfiguration.organization_id == organization_id,
                    DerivedConfiguration.configuration_key
                    == "test-configuration",
                )
            )
            assert count == 1
            other_organization_id = uuid4()
            other_principal_id = uuid4()

            session.add_all(
                [
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
                ]
            )
            await session.flush()

            with pytest.raises(
                IntegrityError,
                match="fk_derived_configurations_created_by_principal",
            ):
                async with session.begin_nested():
                    session.add(
                        DerivedConfiguration(
                            organization_id=organization_id,
                            configuration_key="cross-organization",
                            chunking_version="test-v1",
                            configuration_payload={"example": True},
                            configuration_hash="c" * 64,
                            status="active",
                            created_by_principal_id=other_principal_id,
                        )
                    )
                    await session.flush()

            cross_org_count = await session.scalar(
                select(func.count())
                .select_from(DerivedConfiguration)
                .where(
                    DerivedConfiguration.organization_id == organization_id,
                    DerivedConfiguration.configuration_key
                    == "cross-organization",
                )
            )
            assert cross_org_count == 0
            invalid_cases = [
                (
                    "invalid-hash",
                    {"configuration_hash": "not-a-sha256"},
                    "ck_derived_configurations_configuration_hash_sha256_hex",
                ),
                (
                    "invalid-payload",
                    {"configuration_payload": ["not", "an", "object"]},
                    "ck_derived_configurations_configuration_payload_object",
                ),
                (
                    "missing-dimensions",
                    {"embedding_model": "test-model"},
                    "ck_derived_configurations_embedding_model_requires_dimensions",
                ),
                (
                    "zero-dimensions",
                    {"embedding_dimensions": 0},
                    "ck_derived_configurations_embedding_dimensions_positive",
                ),
            ]

            for configuration_key, overrides, constraint_name in invalid_cases:
                values = {
                    "organization_id": organization_id,
                    "configuration_key": configuration_key,
                    "chunking_version": "test-v1",
                    "configuration_payload": {"example": True},
                    "configuration_hash": "d" * 64,
                    "status": "active",
                    "created_by_principal_id": principal_id,
                }
                values.update(overrides)

                with pytest.raises(IntegrityError, match=constraint_name):
                    async with session.begin_nested():
                        session.add(DerivedConfiguration(**values))
                        await session.flush()

            invalid_count = await session.scalar(
                select(func.count())
                .select_from(DerivedConfiguration)
                .where(
                    DerivedConfiguration.organization_id == organization_id,
                    DerivedConfiguration.configuration_key.in_(
                        [case[0] for case in invalid_cases]
                    ),
                )
            )
            assert invalid_count == 0
            other_configuration = DerivedConfiguration(
                organization_id=other_organization_id,
                configuration_key="test-configuration",
                chunking_version="test-v1",
                embedding_model="test-model",
                embedding_dimensions=3,
                configuration_payload={"example": True},
                configuration_hash="e" * 64,
                status="active",
                created_by_principal_id=other_principal_id,
            )
            session.add(other_configuration)
            await session.flush()

            same_key_count = await session.scalar(
                select(func.count())
                .select_from(DerivedConfiguration)
                .where(
                    DerivedConfiguration.configuration_key
                    == "test-configuration",
                    DerivedConfiguration.organization_id.in_(
                        [organization_id, other_organization_id]
                    ),
                )
            )
            assert same_key_count == 2
        finally:
            await session.close()
            await transaction.rollback()

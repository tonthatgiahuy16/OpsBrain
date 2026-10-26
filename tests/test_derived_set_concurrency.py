import asyncio

from sqlalchemy import insert, select, text, update
from sqlalchemy.exc import DBAPIError

import os
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
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


@pytest.fixture
def concurrency_url():
    url = os.environ.get("OPSBRAIN_CONCURRENCY_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated concurrency test database is not configured")

    assert make_url(url).database == "opsbrain_concurrency_test"
    return url


async def create_committed_fixture(engine):
    organization_id = uuid4()
    principal_id = uuid4()

    document = validate_markdown_document(
        SAMPLE_DOCUMENT.read_text(encoding="utf-8"),
        source_uri=SAMPLE_DOCUMENT.as_uri(),
    )

    async with AsyncSession(engine, expire_on_commit=False) as session:
        session.add_all([
            Organization(
                id=organization_id,
                organization_key=f"concurrency-{organization_id}",
                display_name="Concurrency Test",
                status="active",
            ),
            Principal(
                id=principal_id,
                organization_id=organization_id,
                principal_key=f"concurrency-user-{principal_id}",
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
            configuration_key="concurrency-configuration",
            chunking_version="test-v1",
            configuration_payload={"example": True},
            configuration_hash="c" * 64,
            status="active",
            created_by_principal_id=principal_id,
        )
        session.add(configuration)
        await session.flush()

        configuration_id = configuration.id
        await session.commit()

    return organization_id, created, configuration_id


@pytest.mark.asyncio
async def test_configuration_update_waits_for_derived_set(concurrency_url):
    engine = create_async_engine(concurrency_url)
    update_task = None

    try:
        organization_id, created, configuration_id = (
            await create_committed_fixture(engine)
        )

        async with engine.connect() as first, engine.connect() as second:
            first_transaction = await first.begin()
            await second.begin()

            first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
            second_pid = await second.scalar(text("SELECT pg_backend_pid()"))

            await first.execute(
                insert(DerivedSet).values(
                    id=uuid4(),
                    organization_id=organization_id,
                    document_pk=created.document_pk,
                    document_version_id=created.document_version_id,
                    configuration_id=configuration_id,
                    build_number=1,
                    status="pending",
                )
            )

            await second.execute(text("SET LOCAL lock_timeout = '10s'"))
            update_task = asyncio.create_task(
                second.execute(
                    update(DerivedConfiguration)
                    .where(DerivedConfiguration.id == configuration_id)
                    .values(chunking_version="changed-concurrently")
                )
            )

            async with engine.connect() as observer:
                blocked = False
                for _ in range(100):
                    blocked = await observer.scalar(
                        text(
                            "SELECT CAST(:first_pid AS integer) = ANY("
                            "pg_blocking_pids(CAST(:second_pid AS integer)))"
                        ),
                        {"first_pid": first_pid, "second_pid": second_pid},
                    )
                    if blocked or update_task.done():
                        break
                    await asyncio.sleep(0.05)

                assert blocked, "Configuration update did not wait for the first transaction"

            await first_transaction.commit()

            with pytest.raises(
                DBAPIError,
                match="used derived configuration is immutable",
            ):
                await asyncio.wait_for(update_task, timeout=10)

            await second.rollback()

            async with engine.connect() as observer:
                row = (
                    await observer.execute(
                        select(
                            DerivedConfiguration.chunking_version,
                            DerivedConfiguration.first_used_at,
                        ).where(DerivedConfiguration.id == configuration_id)
                    )
                ).one()

                assert row.chunking_version == "test-v1"
                assert row.first_used_at is not None
    finally:
        if update_task is not None and not update_task.done():
            update_task.cancel()
            try:
                await update_task
            except asyncio.CancelledError:
                pass
        await engine.dispose()

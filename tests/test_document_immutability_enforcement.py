"""Database enforcement for immutable source and derived records.

Contract references:
- storage-schema §6: a committed document version is immutable.
- storage-schema §7: chunks are immutable derived data.
- storage-schema §4: documents are retired with a tombstone version, never
  hard-deleted.

Two layers are asserted here: the trigger rejects every role, including the
table owner, and ``opsbrain_app`` no longer holds the privilege at all.
"""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.derived_build import build_chunk_derived_set
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
    Chunk,
    Document,
    DocumentVersion,
    Organization,
    Principal,
)
from tests.test_derived_build_persistence import _seed_build_inputs


SAMPLE_DOCUMENT = (
    Path(__file__).parents[1]
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "fraud-service.md"
)


async def _seed_document(session: AsyncSession):
    organization_id = uuid4()
    principal_id = uuid4()
    session.add_all(
        [
            Organization(
                id=organization_id,
                organization_key=f"immutability-org-{organization_id}",
                display_name="Immutability Test",
                status="active",
            ),
            Principal(
                id=principal_id,
                organization_id=organization_id,
                principal_key=f"immutability-user-{principal_id}",
                principal_kind="user",
                display_name="Immutability User",
                status="active",
            ),
        ]
    )
    await session.flush()

    created = await ingest_validated_document(
        session,
        organization_id=organization_id,
        actor_principal_id=principal_id,
        document=validate_markdown_document(
            SAMPLE_DOCUMENT.read_text(encoding="utf-8"),
            source_uri=SAMPLE_DOCUMENT.as_uri(),
        ),
    )
    return organization_id, principal_id, created


@pytest.mark.asyncio
async def test_application_role_holds_no_mutation_privilege(
    database_engine,
):
    """The request path must not be able to reach the immutability triggers."""
    async with database_engine.connect() as connection:
        expectations = [
            ("document_versions", "UPDATE", False),
            ("document_versions", "DELETE", False),
            ("chunks", "UPDATE", False),
            ("chunks", "DELETE", False),
            ("documents", "DELETE", False),
            # Required writes still work.
            ("document_versions", "INSERT", True),
            ("chunks", "INSERT", True),
            ("documents", "INSERT", True),
            ("documents", "UPDATE", True),
        ]
        for table, privilege, expected in expectations:
            actual = await connection.scalar(
                text(
                    "SELECT has_table_privilege(current_user, :table, :privilege)"
                ).bindparams(table=table, privilege=privilege)
            )
            assert actual is expected, (table, privilege, actual)


@pytest.mark.asyncio
async def test_application_role_write_attempt_fails_on_privilege(
    database_engine,
):
    async with database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            _, _, created = await _seed_document(session)

            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin_nested():
                    await session.execute(
                        update(DocumentVersion)
                        .where(DocumentVersion.id == created.document_version_id)
                        .values(title="tampered")
                    )

            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin_nested():
                    await session.execute(
                        delete(DocumentVersion).where(
                            DocumentVersion.id == created.document_version_id
                        )
                    )

            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin_nested():
                    await session.execute(
                        delete(Document).where(Document.id == created.document_pk)
                    )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_owner_cannot_delete_document_version(
    migration_database_engine,
):
    """Deletion is rejected for every role; there is no hard-delete path.

    ``UPDATE`` remains available to the owner connection as a repair hatch; the
    application role cannot reach it because the privilege is revoked.
    """
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            _, _, created = await _seed_document(session)

            with pytest.raises(
                DBAPIError, match="document versions are immutable"
            ):
                async with session.begin_nested():
                    await session.execute(
                        delete(DocumentVersion).where(
                            DocumentVersion.id == created.document_version_id
                        )
                    )

            stored = await session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.id == created.document_version_id
                )
            )
            assert stored is not None
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_owner_cannot_hard_delete_document(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            _, _, created = await _seed_document(session)

            with pytest.raises(
                DBAPIError,
                match="documents are retired with a tombstone, not hard deleted",
            ):
                async with session.begin_nested():
                    await session.execute(
                        delete(Document).where(Document.id == created.document_pk)
                    )

            stored = await session.scalar(
                select(Document).where(Document.id == created.document_pk)
            )
            assert stored is not None
            assert stored.record_state == "active"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_owner_cannot_delete_chunks(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            (
                organization_id,
                created,
                configuration,
                _,
            ) = await _seed_build_inputs(session)
            derived_set = await build_chunk_derived_set(
                session,
                organization_id=organization_id,
                document_pk=created.document_pk,
                document_version_id=created.document_version_id,
                configuration_id=configuration.id,
            )
            chunk_count = len(
                (
                    await session.scalars(
                        select(Chunk).where(Chunk.derived_set_id == derived_set.id)
                    )
                ).all()
            )
            assert chunk_count > 0

            with pytest.raises(DBAPIError, match="chunks are immutable"):
                async with session.begin_nested():
                    await session.execute(
                        delete(Chunk).where(Chunk.derived_set_id == derived_set.id)
                    )

            remaining = len(
                (
                    await session.scalars(
                        select(Chunk).where(Chunk.derived_set_id == derived_set.id)
                    )
                ).all()
            )
            assert remaining == chunk_count
        finally:
            await session.close()
            await transaction.rollback()

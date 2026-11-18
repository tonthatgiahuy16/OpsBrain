"""PostgreSQL enforcement for immutable ACL snapshots."""

from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import delete, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.models.storage import (
    DocumentVersion,
    DocumentVersionAclEntry,
    Organization,
    Principal,
    Role,
)
from tests.test_document_review_decisions import _seed


RESTRICTED = Path(__file__).parent / "fixtures" / "restricted-allowed.md"


async def _seed_restricted_version(
    session: AsyncSession,
    *,
    organization_id,
    author_id,
):
    role = Role(
        id=uuid4(),
        organization_id=organization_id,
        role_key=f"acl-reader-{uuid4()}",
        display_name="ACL reader",
        status="active",
    )
    session.add(role)
    await session.flush()
    document = validate_markdown_document(
        RESTRICTED.read_text(encoding="utf-8").replace(
            "incident-reader", role.role_key,
        ),
        source_uri=RESTRICTED.as_uri(),
    )
    ingested = await ingest_validated_document(
        session,
        organization_id=organization_id,
        actor_principal_id=author_id,
        document=document,
    )
    return ingested, role


@pytest.mark.asyncio
async def test_deferred_acl_scope_invariants_reject_invalid_snapshots(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, _, organization_document, _ = await _seed(session)

            with pytest.raises(
                DBAPIError,
                match="restricted document version requires ACL entry",
            ):
                async with session.begin_nested():
                    await session.execute(
                        update(DocumentVersion)
                        .where(DocumentVersion.id == organization_document.document_version_id)
                        .values(access_scope="restricted")
                    )
                    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))

            with pytest.raises(
                DBAPIError,
                match="organization-scoped document version cannot have ACL entries",
            ):
                async with session.begin_nested():
                    session.add(DocumentVersionAclEntry(
                        organization_id=organization_id,
                        document_pk=organization_document.document_pk,
                        document_version_id=organization_document.document_version_id,
                        user_principal_id=author_id,
                    ))
                    await session.flush()
                    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_acl_entries_are_immutable_and_reject_cross_organization_subjects(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, _, _, _ = await _seed(session)
            restricted, role = await _seed_restricted_version(
                session,
                organization_id=organization_id,
                author_id=author_id,
            )
            entry = await session.scalar(
                text(
                    "SELECT id FROM document_version_acl_entries "
                    "WHERE document_version_id = :version_id"
                ).bindparams(version_id=restricted.document_version_id)
            )

            with pytest.raises(DBAPIError, match="document version ACL entries are immutable"):
                async with session.begin_nested():
                    await session.execute(
                        update(DocumentVersionAclEntry)
                        .where(DocumentVersionAclEntry.id == entry)
                        .values(role_id=role.id)
                    )

            with pytest.raises(DBAPIError, match="document version ACL entries are immutable"):
                async with session.begin_nested():
                    await session.execute(
                        delete(DocumentVersionAclEntry)
                        .where(DocumentVersionAclEntry.id == entry)
                    )

            other_organization = Organization(
                id=uuid4(),
                organization_key=f"other-acl-org-{uuid4()}",
                display_name="Other ACL organization",
                status="active",
            )
            other_principal = Principal(
                id=uuid4(),
                organization_id=other_organization.id,
                principal_key=f"other-acl-principal-{uuid4()}",
                principal_kind="user",
                display_name="Other ACL principal",
                status="active",
            )
            session.add_all([other_organization, other_principal])
            await session.flush()
            with pytest.raises(DBAPIError, match="foreign key constraint"):
                async with session.begin_nested():
                    session.add(DocumentVersionAclEntry(
                        organization_id=organization_id,
                        document_pk=restricted.document_pk,
                        document_version_id=restricted.document_version_id,
                        user_principal_id=other_principal.id,
                    ))
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_application_role_cannot_mutate_acl_entries(database_engine):
    async with database_engine.connect() as connection:
        assert await connection.scalar(text(
            "SELECT has_table_privilege("
            "current_user, 'document_version_acl_entries', 'UPDATE')"
        )) is False
        assert await connection.scalar(text(
            "SELECT has_table_privilege("
            "current_user, 'document_version_acl_entries', 'DELETE')"
        )) is False

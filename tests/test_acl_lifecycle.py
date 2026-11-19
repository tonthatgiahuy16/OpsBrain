from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.ingestion.review import submit_review_decision
from src.models.storage import (
    DocumentVersionAclEntry,
    Principal,
    PrincipalRoleMembership,
    Role,
)
from src.authorization.acl import resolve_readable_document_pks
from tests.test_document_review_decisions import _seed


RESTRICTED = Path(__file__).parent / "fixtures" / "restricted-allowed.md"


@pytest.mark.asyncio
async def test_ingestion_materializes_acl_and_review_copies_it_to_approved_version(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, reviewer_id, _, configuration = await _seed(session)
            role = Role(
                id=uuid4(), organization_id=organization_id,
                role_key="incident-reader", display_name="Incident reader", status="active",
            )
            viewer = Principal(
                id=uuid4(), organization_id=organization_id,
                principal_key="restricted-viewer", principal_kind="user",
                display_name="Restricted viewer", status="active",
            )
            session.add_all([role, viewer])
            await session.flush()
            session.add(PrincipalRoleMembership(
                organization_id=organization_id, principal_id=viewer.id,
                role_id=role.id, status="active",
            ))
            # A reviewer must satisfy the current document ACL as well as the
            # approval capability, so grant the reviewer read access too.
            session.add(PrincipalRoleMembership(
                organization_id=organization_id, principal_id=reviewer_id,
                role_id=role.id, status="active",
            ))
            document = validate_markdown_document(
                RESTRICTED.read_text(encoding="utf-8"), source_uri=RESTRICTED.as_uri(),
            )
            ingested = await ingest_validated_document(
                session, organization_id=organization_id,
                actor_principal_id=author_id, document=document,
            )
            draft_entries = list((await session.scalars(select(DocumentVersionAclEntry).where(
                DocumentVersionAclEntry.document_version_id == ingested.document_version_id,
            ))).all())
            assert [entry.role_id for entry in draft_entries] == [role.id]

            reviewed = await submit_review_decision(
                session, organization_id=organization_id, document_pk=ingested.document_pk,
                expected_version_id=ingested.document_version_id,
                reviewer_principal_id=reviewer_id, decision="approve", reason="ACL review",
                request_id=f"acl-review-{uuid4()}", idempotency_key=f"acl-key-{uuid4()}",
                selected_configuration_id=configuration.id,
            )
            approved_entries = list((await session.scalars(select(DocumentVersionAclEntry).where(
                DocumentVersionAclEntry.document_version_id == reviewed.resulting_version.id,
            ))).all())
            assert [entry.role_id for entry in approved_entries] == [role.id]
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_acl_version_transition_does_not_inherit_old_role_grant(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, _, _, _ = await _seed(session)
            reader = Principal(
                id=uuid4(), organization_id=organization_id,
                principal_key="reader", principal_kind="user", display_name="Reader", status="active",
            )
            reader_role = Role(
                id=uuid4(), organization_id=organization_id,
                role_key="incident-reader", display_name="Incident reader", status="active",
            )
            admin_role = Role(
                id=uuid4(), organization_id=organization_id,
                role_key="payments-admin", display_name="Payments admin", status="active",
            )
            session.add_all([reader, reader_role, admin_role])
            await session.flush()
            session.add(PrincipalRoleMembership(
                organization_id=organization_id, principal_id=reader.id,
                role_id=reader_role.id, status="active",
            ))
            first = validate_markdown_document(
                RESTRICTED.read_text(encoding="utf-8"), source_uri=RESTRICTED.as_uri(),
            )
            v1 = await ingest_validated_document(
                session, organization_id=organization_id, actor_principal_id=author_id,
                document=first,
            )
            assert v1.document_pk in await resolve_readable_document_pks(
                session, organization_id=organization_id, principal_id=reader.id,
            )

            changed = validate_markdown_document(
                RESTRICTED.read_text(encoding="utf-8").replace(
                    "incident-reader", "payments-admin",
                ), source_uri=RESTRICTED.as_uri(),
            )
            v2 = await ingest_validated_document(
                session, organization_id=organization_id, actor_principal_id=author_id,
                document=changed,
            )
            assert v2.outcome == "updated"
            assert v1.document_pk not in await resolve_readable_document_pks(
                session, organization_id=organization_id, principal_id=reader.id,
            )
        finally:
            await session.close()
            await transaction.rollback()

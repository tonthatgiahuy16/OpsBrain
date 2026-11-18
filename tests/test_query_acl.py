from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import queries
from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.authorization.acl import resolve_readable_document_pks
from src.database import get_db
from src.models.storage import (
    DocumentVersionAclEntry,
    Organization,
    Principal,
    PrincipalRoleMembership,
    PrincipalTeamMembership,
    Role,
    Team,
)
from src.main import app
from tests.test_vector_search import _seed_embedded_document


FIXTURES = Path(__file__).parent / "fixtures"


async def _seed_acl_documents(session: AsyncSession):
    organization_id = uuid4()
    author = Principal(
        id=uuid4(), organization_id=organization_id,
        principal_key="acl-author", principal_kind="user",
        display_name="ACL author", status="active",
    )
    session.add_all([
        Organization(
            id=organization_id, organization_key=f"acl-org-{organization_id}",
            display_name="ACL test", status="active",
        ),
        author,
    ])
    role = Role(
        id=uuid4(), organization_id=organization_id,
        role_key="incident-reader", display_name="Incident reader", status="active",
    )
    blocked_role = Role(
        id=uuid4(), organization_id=organization_id,
        role_key="payments-admin", display_name="Payments admin", status="active",
    )
    session.add_all([role, blocked_role])
    await session.flush()
    allowed = await _seed_embedded_document(
        session, organization_id=organization_id,
        source_document=FIXTURES / "restricted-allowed.md",
    )
    blocked = await _seed_embedded_document(
        session,
        organization_id=allowed["organization_id"],
        source_document=FIXTURES / "restricted-blocked.md",
    )
    # This legacy embedding helper synthesizes its approved version directly
    # instead of calling review.py, so seed the equivalent immutable snapshots.
    session.add_all([
        DocumentVersionAclEntry(
            organization_id=organization_id, document_pk=allowed["document_pk"],
            document_version_id=allowed["version"].id, role_id=role.id,
        ),
        DocumentVersionAclEntry(
            organization_id=organization_id, document_pk=blocked["document_pk"],
            document_version_id=blocked["version"].id, role_id=blocked_role.id,
        ),
    ])
    viewer = Principal(
        id=uuid4(), organization_id=allowed["organization_id"],
        principal_key="incident-reader", principal_kind="user",
        display_name="Incident reader", status="active",
    )
    session.add(viewer)
    await session.flush()
    session.add_all([
        PrincipalRoleMembership(
            organization_id=allowed["organization_id"], principal_id=viewer.id,
            role_id=role.id, status="active",
        ),
    ])
    await session.flush()
    return allowed, blocked, viewer, role


@pytest.mark.asyncio
async def test_role_acl_filters_restricted_documents_and_revocation_takes_effect(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, blocked, viewer, role = await _seed_acl_documents(session)
            readable = await resolve_readable_document_pks(
                session, organization_id=allowed["organization_id"], principal_id=viewer.id,
            )
            assert readable == {allowed["document_pk"]}
            assert blocked["document_pk"] not in readable

            role.status = "disabled"
            await session.flush()
            assert await resolve_readable_document_pks(
                session, organization_id=allowed["organization_id"], principal_id=viewer.id,
            ) == set()
            role.status = "active"
            membership = await session.scalar(
                select(PrincipalRoleMembership).where(
                    PrincipalRoleMembership.organization_id == allowed["organization_id"],
                    PrincipalRoleMembership.principal_id == viewer.id,
                    PrincipalRoleMembership.role_id == role.id,
                )
            )
            membership.status = "revoked"
            await session.flush()
            assert await resolve_readable_document_pks(
                session, organization_id=allowed["organization_id"], principal_id=viewer.id,
            ) == set()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_query_endpoint_never_cites_restricted_document_without_active_grant(
    migration_database_engine, monkeypatch,
):
    class FakeEmbeddingClient:
        async def embed_texts(self, inputs):
            return [[1.0] + [0.0] * 383 for _ in inputs]

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, blocked, viewer, role = await _seed_acl_documents(session)

            async def fake_db():
                yield session

            async def fake_principal():
                return AuthenticatedPrincipal(id=viewer.id, organization_id=viewer.organization_id)

            async def fake_client(configuration):
                return FakeEmbeddingClient()

            async def fake_active_configuration(session, organization_id):
                return allowed["configuration"]

            app.dependency_overrides[get_db] = fake_db
            app.dependency_overrides[get_authenticated_principal] = fake_principal
            monkeypatch.setattr(queries, "get_embedding_client", fake_client)
            monkeypatch.setattr(queries, "_active_configuration", fake_active_configuration)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post("/api/v1/queries", json={"question": "restricted evidence", "top_k": 5})
            assert response.status_code == 200
            body = response.json()
            assert body["abstained"] is False
            assert {item["document_id"] for item in body["evidence"]} == {"restricted-allowed"}
            assert str(blocked["document_pk"]) not in str(body)

            membership = await session.scalar(
                select(PrincipalRoleMembership).where(
                    PrincipalRoleMembership.principal_id == viewer.id,
                    PrincipalRoleMembership.role_id == role.id,
                )
            )
            membership.status = "revoked"
            await session.flush()
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                revoked = await client.post("/api/v1/queries", json={"question": "restricted evidence"})
            assert revoked.status_code == 200
            assert revoked.json()["status"] == "abstained"
            assert revoked.json()["evidence"] == []
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_direct_user_and_active_team_grants_stop_when_team_is_disabled(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, viewer, role = await _seed_acl_documents(session)
            role_membership = await session.scalar(select(PrincipalRoleMembership).where(
                PrincipalRoleMembership.principal_id == viewer.id,
                PrincipalRoleMembership.role_id == role.id,
            ))
            role_membership.status = "revoked"
            session.add(DocumentVersionAclEntry(
                organization_id=allowed["organization_id"], document_pk=allowed["document_pk"],
                document_version_id=allowed["version"].id, user_principal_id=viewer.id,
            ))
            await session.flush()
            assert await resolve_readable_document_pks(
                session, organization_id=allowed["organization_id"], principal_id=viewer.id,
            ) == {allowed["document_pk"]}

            # Replace the direct grant with a team grant to test the team path.
            direct_grant = await session.scalar(select(DocumentVersionAclEntry).where(
                DocumentVersionAclEntry.document_version_id == allowed["version"].id,
                DocumentVersionAclEntry.user_principal_id == viewer.id,
            ))
            await session.delete(direct_grant)
            team = Team(
                id=uuid4(), organization_id=allowed["organization_id"],
                team_key="incident-response", display_name="Incident response", status="active",
            )
            session.add(team)
            await session.flush()
            session.add_all([
                PrincipalTeamMembership(
                    organization_id=allowed["organization_id"], principal_id=viewer.id,
                    team_id=team.id, status="active",
                ),
                DocumentVersionAclEntry(
                    organization_id=allowed["organization_id"], document_pk=allowed["document_pk"],
                    document_version_id=allowed["version"].id, team_id=team.id,
                ),
            ])
            await session.flush()
            assert await resolve_readable_document_pks(
                session, organization_id=allowed["organization_id"], principal_id=viewer.id,
            ) == {allowed["document_pk"]}
            team.status = "disabled"
            await session.flush()
            assert await resolve_readable_document_pks(
                session, organization_id=allowed["organization_id"], principal_id=viewer.id,
            ) == set()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_same_named_role_in_another_organization_cannot_read_document(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, _, _ = await _seed_acl_documents(session)
            other_org = Organization(
                id=uuid4(), organization_key=f"other-{uuid4()}",
                display_name="Other org", status="active",
            )
            other_principal = Principal(
                id=uuid4(), organization_id=other_org.id,
                principal_key="other-reader", principal_kind="user",
                display_name="Other reader", status="active",
            )
            same_named_role = Role(
                id=uuid4(), organization_id=other_org.id,
                role_key="incident-reader", display_name="Incident reader", status="active",
            )
            session.add_all([other_org, other_principal, same_named_role])
            await session.flush()
            session.add(PrincipalRoleMembership(
                organization_id=other_org.id, principal_id=other_principal.id,
                role_id=same_named_role.id, status="active",
            ))
            await session.flush()
            assert await resolve_readable_document_pks(
                session, organization_id=other_org.id, principal_id=other_principal.id,
            ) == set()
            assert allowed["document_pk"] not in await resolve_readable_document_pks(
                session, organization_id=other_org.id, principal_id=other_principal.id,
            )
        finally:
            await session.close()
            await transaction.rollback()

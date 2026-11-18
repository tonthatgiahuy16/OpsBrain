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
    Principal,
    PrincipalRoleMembership,
    Role,
)
from src.main import app
from tests.test_vector_search import _seed_embedded_document


FIXTURES = Path(__file__).parent / "fixtures"


async def _seed_acl_documents(session: AsyncSession):
    allowed = await _seed_embedded_document(
        session, source_document=FIXTURES / "restricted-allowed.md",
    )
    blocked = await _seed_embedded_document(
        session,
        organization_id=allowed["organization_id"],
        source_document=FIXTURES / "restricted-blocked.md",
    )
    viewer = Principal(
        id=uuid4(), organization_id=allowed["organization_id"],
        principal_key="incident-reader", principal_kind="user",
        display_name="Incident reader", status="active",
    )
    role = Role(
        id=uuid4(), organization_id=allowed["organization_id"],
        role_key="incident-reader", display_name="Incident reader", status="active",
    )
    blocked_role = Role(
        id=uuid4(), organization_id=allowed["organization_id"],
        role_key="payments-admin", display_name="Payments admin", status="active",
    )
    session.add_all([viewer, role, blocked_role])
    await session.flush()
    session.add_all([
        PrincipalRoleMembership(
            organization_id=allowed["organization_id"], principal_id=viewer.id,
            role_id=role.id, status="active",
        ),
        DocumentVersionAclEntry(
            organization_id=allowed["organization_id"],
            document_pk=allowed["document_pk"],
            document_version_id=allowed["version"].id, role_id=role.id,
        ),
        DocumentVersionAclEntry(
            organization_id=blocked["organization_id"],
            document_pk=blocked["document_pk"],
            document_version_id=blocked["version"].id, role_id=blocked_role.id,
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

"""HTTP contract tests for the read-only document state endpoint."""

from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.database import get_db
from src.main import app
from src.models.storage import Principal
from tests.support import grant_capability
from tests.test_document_review_decisions import _seed


def _configure_endpoint(session: AsyncSession, *, principal: Principal) -> None:
    async def fake_db():
        yield session

    async def fake_principal():
        return AuthenticatedPrincipal(
            id=principal.id,
            organization_id=principal.organization_id,
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_authenticated_principal] = fake_principal


async def _get_document(document_id: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(
            f"/api/v1/documents/{document_id}",
            headers={"Authorization": "Bearer ignored-by-test-override"},
        )


@pytest.mark.asyncio
async def test_document_state_returns_only_management_state_for_visible_document(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, _ = await _seed(session)
            await grant_capability(
                session,
                organization_id=organization_id,
                principal_id=reviewer_id,
                capability="document:read_state",
                role_key=f"state-reader-{uuid4()}",
            )
            principal = await session.get(Principal, reviewer_id)
            _configure_endpoint(session, principal=principal)

            response = await _get_document("fraud-service")

            assert response.status_code == 200
            body = response.json()
            assert body["request_id"]
            document = body["document"]
            assert document["document_id"] == "fraud-service"
            assert document["title"]
            assert document["current_version"] == ingested.version_number
            assert document["review_state"] == "in_review"
            assert document["retrieval_eligible"] is False
            assert document["ineligibility_reasons"] == ["review_state_not_approved"]
            assert document["index_status"] == "not_eligible"
            assert document["query_ready"] is False
            assert "markdown_body" not in document
            assert "acl" not in document
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_document_state_hides_missing_capability_and_unknown_document(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            _, _, reviewer_id, _, _ = await _seed(session)
            principal = await session.get(Principal, reviewer_id)
            _configure_endpoint(session, principal=principal)

            missing_capability = await _get_document("fraud-service")
            unknown_document = await _get_document("not-a-document")

            assert missing_capability.status_code == 404
            assert missing_capability.json()["error"]["code"] == "document_not_found"
            assert unknown_document.status_code == 404
            assert unknown_document.json()["error"]["code"] == "document_not_found"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()

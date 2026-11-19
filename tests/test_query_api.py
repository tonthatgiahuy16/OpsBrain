import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import queries
from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.config import Settings
from src.database import get_db
from src.models.storage import Principal
from src.main import app
from tests.support import grant_capability
from tests.test_vector_search import _seed_embedded_document


@pytest.mark.asyncio
async def test_query_endpoint_returns_authorized_evidence_and_citation(
    migration_database_engine, monkeypatch,
):
    class FakeEmbeddingClient:
        async def embed_texts(self, inputs):
            return [[1.0] + [0.0] * 383 for _ in inputs]

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_embedded_document(session)
            await grant_capability(
                session,
                organization_id=seeded["organization_id"],
                principal_id=seeded["configuration"].created_by_principal_id,
                capability="query:execute",
            )

            async def fake_db():
                yield session

            async def fake_principal():
                return AuthenticatedPrincipal(
                    id=seeded["configuration"].created_by_principal_id,
                    organization_id=seeded["organization_id"],
                )

            async def fake_client(configuration):
                return FakeEmbeddingClient()

            app.dependency_overrides[get_db] = fake_db
            app.dependency_overrides[get_authenticated_principal] = fake_principal
            monkeypatch.setattr(queries, "get_embedding_client", fake_client)
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored-by-test-override"},
                    json={"question": "What does the payment service do?", "top_k": 1},
                )

            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "evidence_returned"
            assert body["abstained"] is False
            assert body["evidence"][0]["document_id"] == "fraud-service"
            assert body["evidence"][0]["citation_id"] == "cit_1"
            assert body["configuration"]["embedding_model"] == "test-embed-model"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_query_endpoint_abstains_without_active_evidence(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_embedded_document(session)
            await grant_capability(
                session,
                organization_id=seeded["organization_id"],
                principal_id=seeded["configuration"].created_by_principal_id,
                capability="query:execute",
            )
            seeded["derived_set"].is_active = False
            await session.flush()

            async def fake_db():
                yield session

            async def fake_principal():
                return AuthenticatedPrincipal(
                    id=seeded["configuration"].created_by_principal_id,
                    organization_id=seeded["organization_id"],
                )

            app.dependency_overrides[get_db] = fake_db
            app.dependency_overrides[get_authenticated_principal] = fake_principal
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored-by-test-override"},
                    json={"question": "Does evidence exist?"},
                )

            assert response.status_code == 200
            assert response.json()["status"] == "abstained"
            assert response.json()["evidence"] == []
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_query_auth_rejects_missing_invalid_and_disabled_local_token(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_embedded_document(session)
            await grant_capability(
                session,
                organization_id=seeded["organization_id"],
                principal_id=seeded["configuration"].created_by_principal_id,
                capability="query:execute",
            )

            async def fake_db():
                yield session

            principal_id = seeded["configuration"].created_by_principal_id
            monkeypatch.setattr(
                queries,
                "get_settings",
                lambda: Settings(
                    LOCAL_QUERY_TOKEN_MAP_JSON=f"query-token={principal_id}",
                ),
            )
            app.dependency_overrides[get_db] = fake_db
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                missing = await client.post("/api/v1/queries", json={
                    "question": "What does the payment service do?",
                })
                invalid = await client.post("/api/v1/queries", headers={
                    "Authorization": "Bearer incorrect-token",
                }, json={"question": "What does the payment service do?"})
                accepted = await client.post("/api/v1/queries", headers={
                    "Authorization": "Bearer query-token",
                }, json={
                    "question": "What does the payment service do?",
                    "response_mode": "answer",
                })
                principal = await session.get(Principal, principal_id)
                principal.status = "disabled"
                await session.flush()
                disabled = await client.post("/api/v1/queries", headers={
                    "Authorization": "Bearer query-token",
                }, json={"question": "What does the payment service do?"})

            assert missing.status_code == 401
            assert invalid.status_code == 401
            assert accepted.status_code == 501
            assert disabled.status_code == 401
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()

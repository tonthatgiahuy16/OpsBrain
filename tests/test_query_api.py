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


@pytest.mark.asyncio
async def test_query_rejects_unsupported_strategy_and_validates_context_acl(
    migration_database_engine, monkeypatch,
):
    from pathlib import Path
    from src.ingestion.frontmatter import validate_markdown_document
    from src.ingestion.lifecycle import ingest_validated_document

    class FakeEmbeddingClient:
        async def embed_texts(self, inputs):
            return [[1.0] + [0.0] * 383 for _ in inputs]

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            seeded = await _seed_embedded_document(session)
            principal_id = seeded["configuration"].created_by_principal_id
            organization_id = seeded["organization_id"]
            await grant_capability(
                session,
                organization_id=organization_id,
                principal_id=principal_id,
                capability="query:execute",
            )
            from src.models.storage import Role

            session.add(
                Role(
                    organization_id=organization_id,
                    role_key="incident-reader",
                    display_name="Incident Reader",
                    status="active",
                )
            )
            await session.flush()
            restricted_path = Path(__file__).parent / "fixtures" / "restricted-allowed.md"
            restricted_doc = validate_markdown_document(
                restricted_path.read_text(encoding="utf-8"),
                source_uri=restricted_path.as_uri(),
            )
            await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=principal_id,
                document=restricted_doc,
            )

            async def fake_db():
                yield session

            async def fake_principal():
                return AuthenticatedPrincipal(
                    id=principal_id,
                    organization_id=organization_id,
                )

            async def fake_client(configuration):
                return FakeEmbeddingClient()

            app.dependency_overrides[get_db] = fake_db
            app.dependency_overrides[get_authenticated_principal] = fake_principal
            monkeypatch.setattr(queries, "get_embedding_client", fake_client)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                hybrid_top = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={"question": "Why 503?", "strategy": "hybrid"},
                )
                hybrid_nested = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={
                        "question": "Why 503?",
                        "retrieval": {"strategy": "hybrid_graph", "top_k": 3},
                    },
                )
                missing_ctx = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={
                        "question": "Why 503?",
                        "context": {"active_document_id": "does-not-exist"},
                    },
                )
                restricted_ctx = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={
                        "question": "Why 503?",
                        "context": {"active_document_id": "restricted-allowed"},
                    },
                )
                allowed_ctx = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={
                        "question": "What does fraud service do?",
                        "context": {"active_document_id": "fraud-service"},
                        "retrieval": {"strategy": "auto", "top_k": 1},
                    },
                )
                conflict_strategy = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={
                        "question": "Why 503?",
                        "strategy": "vector",
                        "retrieval": {"strategy": "hybrid", "top_k": 2},
                    },
                )
                conflict_top_k = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={
                        "question": "Why 503?",
                        "top_k": 4,
                        "retrieval": {"strategy": "auto", "top_k": 2},
                    },
                )

            assert hybrid_top.status_code == 422
            assert hybrid_top.json()["error"]["code"] == "unsupported_retrieval_strategy"
            assert hybrid_nested.status_code == 422
            assert hybrid_nested.json()["error"]["code"] == "unsupported_retrieval_strategy"
            assert conflict_strategy.status_code == 422
            assert conflict_strategy.json()["error"]["code"] == "validation_failed"
            assert conflict_top_k.status_code == 422
            assert conflict_top_k.json()["error"]["code"] == "validation_failed"
            assert missing_ctx.status_code == 404
            assert missing_ctx.json()["error"]["code"] == "context_document_not_found"
            assert restricted_ctx.status_code == 404
            assert restricted_ctx.json()["error"]["code"] == "context_document_not_found"
            assert allowed_ctx.status_code == 200
            assert allowed_ctx.json()["strategy_used"] == "vector"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_query_context_active_document_prioritizes_active_note(
    migration_database_engine, monkeypatch,
):
    from pathlib import Path

    class FakeEmbeddingClient:
        async def embed_texts(self, inputs):
            return [[1.0] + [0.0] * 383 for _ in inputs]

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            primary = await _seed_embedded_document(
                session,
                vector_for_chunk=lambda _c: [1.0] + [0.0] * 383,
            )
            organization_id = primary["organization_id"]
            principal_id = primary["configuration"].created_by_principal_id
            await _seed_embedded_document(
                session,
                organization_id=organization_id,
                source_document=Path(__file__).parents[1]
                / "data"
                / "sample-vault"
                / "payment-service"
                / "services"
                / "checkout-service.md",
                vector_for_chunk=lambda _c: [0.97, 0.243] + [0.0] * 382,
            )
            await grant_capability(
                session,
                organization_id=organization_id,
                principal_id=principal_id,
                capability="query:execute",
            )

            async def fake_db():
                yield session

            async def fake_principal():
                return AuthenticatedPrincipal(
                    id=principal_id,
                    organization_id=organization_id,
                )

            async def fake_client(configuration):
                return FakeEmbeddingClient()

            async def fake_active_configuration(_session, _organization_id):
                return primary["configuration"]

            app.dependency_overrides[get_db] = fake_db
            app.dependency_overrides[get_authenticated_principal] = fake_principal
            monkeypatch.setattr(queries, "get_embedding_client", fake_client)
            monkeypatch.setattr(queries, "_active_configuration", fake_active_configuration)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                without_ctx = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={"question": "Which service handles this?", "top_k": 1},
                )
                with_ctx = await client.post(
                    "/api/v1/queries",
                    headers={"Authorization": "Bearer ignored"},
                    json={
                        "question": "Which service handles this?",
                        "top_k": 1,
                        "context": {"active_document_id": "checkout-service"},
                    },
                )

            assert without_ctx.status_code == 200
            assert without_ctx.json()["evidence"][0]["document_id"] == "fraud-service"
            assert with_ctx.status_code == 200
            assert with_ctx.json()["evidence"][0]["document_id"] == "checkout-service"
            assert with_ctx.json()["evidence"][0]["scores"]["vector"] < 0.99
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()

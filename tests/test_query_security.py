"""Security boundary tests for the query endpoint (Milestone 5).

Covers the three independent gates on the read path:

1. capability ``query:execute`` decides whether the actor may query at all;
2. current document ACL decides which documents may influence ranking, scores,
   excerpts and citations;
3. abstention must look identical whether the corpus is empty or merely
   unreadable, so a denial cannot confirm that a protected document exists.
"""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.api import queries
from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.database import get_db
from src.main import app
from src.models.storage import (
    DerivedSet,
    DocumentVersion,
    Organization,
    Principal,
    PrincipalRoleMembership,
    Role,
    RoleCapability,
)
from tests.support import grant_capability
from tests.test_derived_set_activation import SAMPLE_DOCUMENT
from tests.test_query_acl import _seed_acl_documents
from tests.test_vector_search import OTHER_SAMPLE_DOCUMENT, _seed_embedded_document


class FakeEmbeddingClient:
    async def embed_texts(self, inputs):
        return [[1.0] + [0.0] * 383 for _ in inputs]


def _configure_endpoint(
    session, *, principal, configuration, monkeypatch,
) -> None:
    """Wire the endpoint for a test.

    ``configuration=None`` leaves the real ``_active_configuration`` lookup in
    place so org-level retrieval availability is exercised, not mocked.
    """

    async def fake_db():
        yield session

    async def fake_principal():
        return AuthenticatedPrincipal(
            id=principal.id, organization_id=principal.organization_id,
        )

    async def fake_client(configuration):
        return FakeEmbeddingClient()

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_authenticated_principal] = fake_principal
    monkeypatch.setattr(queries, "get_embedding_client", fake_client)
    if configuration is not None:
        async def fake_active_configuration(session, organization_id):
            return configuration

        monkeypatch.setattr(queries, "_active_configuration", fake_active_configuration)


async def _post(payload: dict | None = None):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            "/api/v1/queries",
            json=payload or {"question": "restricted evidence", "top_k": 5},
        )


async def _delete_query_capability(session: AsyncSession, *, organization_id) -> None:
    capability_roles = select(Role.id).where(
        Role.organization_id == organization_id,
        Role.role_key.like("grant-%"),
    )
    await session.execute(
        delete(RoleCapability).where(
            RoleCapability.organization_id == organization_id,
            RoleCapability.role_id.in_(capability_roles),
            RoleCapability.capability_key == "query:execute",
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_query_requires_execute_capability_even_with_readable_documents(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, blocked, viewer, _ = await _seed_acl_documents(session)
            # ACL stays intact; only the action capability is removed.
            await _delete_query_capability(
                session, organization_id=allowed["organization_id"],
            )
            _configure_endpoint(
                session, principal=viewer, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )

            response = await _post()
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "query_forbidden"
            assert "excerpt" not in str(response.json())
            assert str(blocked["document_pk"]) not in str(response.json())
            assert "restricted-allowed" not in str(response.json())
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_revoked_query_capability_is_enforced_without_caching(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, viewer, _ = await _seed_acl_documents(session)
            _configure_endpoint(
                session, principal=viewer, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )
            assert (await _post()).status_code == 200

            await _delete_query_capability(
                session, organization_id=allowed["organization_id"],
            )
            denied = await _post()
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "query_forbidden"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_capability_without_acl_abstains_and_shows_no_restricted_content(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, blocked, _, _ = await _seed_acl_documents(session)
            outsider = Principal(
                id=uuid4(), organization_id=allowed["organization_id"],
                principal_key=f"outsider-{uuid4()}", principal_kind="user",
                display_name="Outsider", status="active",
            )
            session.add(outsider)
            await session.flush()
            await grant_capability(
                session, organization_id=allowed["organization_id"],
                principal_id=outsider.id, capability="query:execute",
            )
            _configure_endpoint(
                session, principal=outsider, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )

            response = await _post()
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "abstained"
            assert body["abstained"] is True
            assert body["evidence"] == []
            assert str(blocked["document_pk"]) not in str(body)
            assert "restricted-allowed" not in str(body)
            assert "restricted-blocked" not in str(body)
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_abstention_does_not_reveal_whether_restricted_evidence_exists(
    migration_database_engine, monkeypatch,
):
    """The same denial response must be produced for an empty and a hidden corpus.

    Both principals belong to the *same configured organization*, so this
    isolates the document-level ACL gate. An organization without an active
    retrieval configuration is a different failure (503) and is covered by
    ``test_unconfigured_organization_returns_retrieval_unavailable``.
    """

    def normalized(body: dict) -> dict:
        return {
            key: value for key, value in body.items()
            if key not in {"request_id", "query_id", "latency_ms"}
        }

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, viewer, _ = await _seed_acl_documents(session)
            hidden_principal = Principal(
                id=uuid4(), organization_id=allowed["organization_id"],
                principal_key=f"hidden-{uuid4()}", principal_kind="user",
                display_name="Hidden", status="active",
            )
            session.add(hidden_principal)
            await session.flush()
            await grant_capability(
                session, organization_id=allowed["organization_id"],
                principal_id=hidden_principal.id, capability="query:execute",
            )

            # Hidden corpus: data exists, but the principal has no ACL grant.
            _configure_endpoint(
                session, principal=hidden_principal,
                configuration=allowed["configuration"], monkeypatch=monkeypatch,
            )
            hidden = await _post()

            # Empty corpus: the granted principal is looking at the same
            # organization with every derived set deactivated.
            await session.execute(
                update(DerivedSet)
                .where(DerivedSet.organization_id == allowed["organization_id"])
                .values(is_active=False)
            )
            await session.flush()
            _configure_endpoint(
                session, principal=viewer,
                configuration=allowed["configuration"], monkeypatch=monkeypatch,
            )
            empty = await _post()

            assert hidden.status_code == empty.status_code == 200
            hidden_body, empty_body = hidden.json(), empty.json()
            assert hidden_body["status"] == empty_body["status"] == "abstained"
            assert hidden_body["abstention_reason"] == empty_body["abstention_reason"]
            assert normalized(hidden_body) == normalized(empty_body)
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_unconfigured_organization_returns_retrieval_unavailable(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization = Organization(
                id=uuid4(), organization_key=f"unconfigured-{uuid4()}",
                display_name="Unconfigured org", status="active",
            )
            principal = Principal(
                id=uuid4(), organization_id=organization.id,
                principal_key=f"unconfigured-{uuid4()}", principal_kind="user",
                display_name="Unconfigured", status="active",
            )
            session.add_all([organization, principal])
            await session.flush()
            await grant_capability(
                session, organization_id=organization.id,
                principal_id=principal.id, capability="query:execute",
            )
            _configure_endpoint(
                session, principal=principal, configuration=None,
                monkeypatch=monkeypatch,
            )

            response = await _post()
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "retrieval_unavailable"
            assert "excerpt" not in str(response.json())
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_citation_is_bound_to_the_embedded_version_and_authorized_chunk(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, blocked, viewer, _ = await _seed_acl_documents(session)
            _configure_endpoint(
                session, principal=viewer, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )

            body = (await _post()).json()
            assert body["status"] == "evidence_returned"
            assert body["evidence"], "granted principal must receive evidence"
            chunks_by_id = {str(chunk.id): chunk for chunk in allowed["chunks"]}
            for item in body["evidence"]:
                assert item["chunk_id"] in chunks_by_id
                assert item["document_version"] == allowed["version"].version_number
                assert item["excerpt"] == chunks_by_id[item["chunk_id"]].content[:500]
                assert item["document_id"] == "restricted-allowed"
                assert 0.0 <= item["scores"]["vector"] <= 1.0
                assert item["relationship_path"] == []
            assert str(blocked["document_pk"]) not in str(body)
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


async def _seed_second_readable_document(session, *, allowed):
    """Add a second embedded organization-scoped document to the same org."""
    return await _seed_embedded_document(
        session,
        organization_id=allowed["organization_id"],
        source_document=SAMPLE_DOCUMENT,
    )


def _invalidate_after_ranking(monkeypatch, *, values, seen):
    """Wrap ranking so a hit becomes invalid between ranking and citation."""

    original = queries.vector_top_k

    async def wrapper(session, **kwargs):
        hits = await original(session, **kwargs)
        seen.extend(hits)
        if hits:
            await session.execute(
                update(DocumentVersion)
                .where(
                    DocumentVersion.id.in_(
                        {hit.document_version_id for hit in hits}
                    )
                )
                .values(**values)
            )
            await session.flush()
        return hits

    monkeypatch.setattr(queries, "vector_top_k", wrapper)


@pytest.mark.asyncio
async def test_hit_invalidated_between_ranking_and_citation_abstains(
    migration_database_engine, monkeypatch,
):
    """A hit ranked as approved must not be cited after review_state changes."""

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, viewer, _ = await _seed_acl_documents(session)
            _configure_endpoint(
                session, principal=viewer, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )
            ranked = []
            _invalidate_after_ranking(
                monkeypatch, values={"review_state": "rejected"}, seen=ranked,
            )

            response = await _post()
            assert ranked, "ranking must return a hit before it is invalidated"
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "abstained"
            assert body["abstained"] is True
            assert body["evidence"] == []
            assert "restricted-allowed" not in str(body)
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_evidence_window_expired_between_ranking_and_citation_abstains(
    migration_database_engine, monkeypatch,
):
    """Temporal validity is re-asserted, not trusted from the ranking step."""

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, viewer, _ = await _seed_acl_documents(session)
            _configure_endpoint(
                session, principal=viewer, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )
            ranked = []
            _invalidate_after_ranking(
                monkeypatch,
                values={
                    "effective_until": datetime.now(timezone.utc) - timedelta(seconds=1)
                },
                seen=ranked,
            )

            body = (await _post()).json()
            assert ranked, "ranking must return a hit before it expires"
            assert body["status"] == "abstained"
            assert body["evidence"] == []
            assert "restricted-allowed" not in str(body)
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_acl_revoked_during_embedding_call_drops_stale_hits(
    migration_database_engine, monkeypatch,
):
    """The allowlist is re-resolved after the external embedding call."""

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, viewer, role = await _seed_acl_documents(session)
            _configure_endpoint(
                session, principal=viewer, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )
            original = queries.vector_top_k
            ranked = []

            async def revoking_wrapper(session, **kwargs):
                hits = await original(session, **kwargs)
                ranked.extend(hits)
                # Revoke only the document-reader role; the query:execute
                # capability was already checked and is a separate grant.
                await session.execute(
                    update(PrincipalRoleMembership)
                    .where(
                        PrincipalRoleMembership.principal_id == viewer.id,
                        PrincipalRoleMembership.role_id == role.id,
                    )
                    .values(status="revoked")
                )
                await session.flush()
                return hits

            monkeypatch.setattr(queries, "vector_top_k", revoking_wrapper)

            body = (await _post()).json()
            assert ranked, "ranking must return hits before the ACL is revoked"
            assert body["status"] == "abstained"
            assert body["evidence"] == []
            assert "restricted-allowed" not in str(body)
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_partial_invalidation_renumbers_citations_and_drops_stale_hit(
    migration_database_engine, monkeypatch,
):
    """Dropping a stale hit must not leave a gap in citation numbering."""

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, viewer, _ = await _seed_acl_documents(session)
            second = await _seed_second_readable_document(
                session, allowed=allowed,
            )
            _configure_endpoint(
                session, principal=viewer, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )
            original = queries.vector_top_k
            ranked = []
            stale_version_id = second["version"].id

            async def invalidating_wrapper(session, **kwargs):
                hits = await original(session, **kwargs)
                ranked.extend(hits)
                await session.execute(
                    update(DocumentVersion)
                    .where(DocumentVersion.id == stale_version_id)
                    .values(review_state="rejected")
                )
                await session.flush()
                return hits

            monkeypatch.setattr(queries, "vector_top_k", invalidating_wrapper)

            body = (await _post({"question": "evidence", "top_k": 20})).json()
            ranked_documents = {hit.document_pk for hit in ranked}
            assert {allowed["document_pk"], second["document_pk"]} <= ranked_documents
            assert body["status"] == "evidence_returned"
            assert body["evidence"]
            assert {
                item["document_id"] for item in body["evidence"]
            } == {"restricted-allowed"}
            assert [item["citation_id"] for item in body["evidence"]] == [
                f"cit_{number}"
                for number in range(1, len(body["evidence"]) + 1)
            ]
            assert "restricted-team" not in str(body)
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_cross_organization_principal_cannot_query_or_read_other_org_documents(
    migration_database_engine, monkeypatch,
):
    """Endpoint must isolate both capabilities and organization-scoped corpus.

    Org A has a higher-scoring (1.0) document; Org B has a lower-scoring (0.8)
    query-ready document. With top_k=1, any cross-org filter leak would return
    Org A's document instead of Org B's positive control.
    """

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, _, _, _ = await _seed_acl_documents(session)
            org_a_doc = await _seed_embedded_document(
                session,
                organization_id=allowed["organization_id"],
                source_document=SAMPLE_DOCUMENT,
                vector_for_chunk=lambda _: [1.0] + [0.0] * 383,
            )
            org_b_doc = await _seed_embedded_document(
                session,
                source_document=OTHER_SAMPLE_DOCUMENT,
                vector_for_chunk=lambda _: [0.8, 0.6] + [0.0] * 382,
            )
            other_org_id = org_b_doc["organization_id"]

            other_principal = Principal(
                id=uuid4(), organization_id=other_org_id,
                principal_key=f"other-reader-{uuid4()}", principal_kind="user",
                display_name="Other reader", status="active",
            )
            same_named_role = Role(
                id=uuid4(), organization_id=other_org_id,
                role_key="incident-reader", display_name="Incident reader",
                status="active",
            )
            session.add_all([other_principal, same_named_role])
            await session.flush()

            session.add(
                PrincipalRoleMembership(
                    organization_id=other_org_id,
                    principal_id=other_principal.id,
                    role_id=same_named_role.id,
                    status="active",
                )
            )
            await session.flush()

            _configure_endpoint(
                session, principal=other_principal, configuration=None,
                monkeypatch=monkeypatch,
            )
            forbidden = await _post({"question": "evidence", "top_k": 1})
            assert forbidden.status_code == 403
            assert forbidden.json()["error"]["code"] == "query_forbidden"

            await grant_capability(
                session, organization_id=other_org_id,
                principal_id=other_principal.id, capability="query:execute",
            )
            response = await _post({"question": "evidence", "top_k": 1})
            assert response.status_code == 200
            body = response.json()
            assert body["status"] == "evidence_returned"
            assert len(body["evidence"]) == 1
            assert body["evidence"][0]["document_id"] == "checkout-service"
            assert body["evidence"][0]["scores"]["vector"] == pytest.approx(0.8, abs=1e-4)
            assert "restricted-allowed" not in str(body)
            assert str(allowed["document_pk"]) not in str(body)
            assert str(org_a_doc["document_pk"]) not in str(body)
            assert org_a_doc["version"].title not in str(body)
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()

"""HTTP contract tests for ``src/api/reindex.py``."""

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.database import get_db
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.main import app
from src.models.storage import (
    AuditEvent,
    Capability,
    DerivedConfiguration,
    Organization,
    Principal,
    PrincipalRoleMembership,
    Role,
    RoleCapability,
)

ORG_DOCUMENT = (
    Path(__file__).parent.parent
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "fraud-service.md"
)
RESTRICTED_DOCUMENT = Path(__file__).parent / "fixtures" / "restricted-allowed.md"


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


async def _post_reindex(
    *,
    payload: dict[str, object],
    idempotency_key: str | None = None,
):
    headers = {"Authorization": "Bearer ignored-by-test-override"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/reindex-jobs", headers=headers, json=payload)


async def _get_reindex(job_id: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(
            f"/api/v1/reindex-jobs/{job_id}",
            headers={"Authorization": "Bearer ignored-by-test-override"},
        )


async def _grant_in_session(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_principal_id: UUID,
    target_principal_id: UUID,
    role_key: str,
    capability_keys: tuple[str, ...],
) -> Role:
    role = Role(
        id=uuid4(),
        organization_id=organization_id,
        role_key=role_key,
        display_name=role_key,
        status="active",
    )
    session.add(role)
    await session.flush()
    session.add(
        PrincipalRoleMembership(
            organization_id=organization_id,
            principal_id=target_principal_id,
            role_id=role.id,
            status="active",
        )
    )
    for key in capability_keys:
        assert await session.get(Capability, key) is not None
        session.add(
            RoleCapability(
                organization_id=organization_id,
                role_id=role.id,
                capability_key=key,
                status="active",
                granted_by_principal_id=actor_principal_id,
                granted_at=datetime.now(timezone.utc),
            )
        )
    await session.flush()
    return role


async def _seed_reindex_context(session: AsyncSession):
    organization_id = uuid4()
    admin_id = uuid4()
    reader_id = uuid4()
    outsider_id = uuid4()
    session.add(
        Organization(
            id=organization_id,
            organization_key=f"reindex-org-{organization_id}",
            display_name="Reindex API Test",
            status="active",
        )
    )
    await session.flush()
    principals = {}
    for pid, key in (
        (admin_id, "admin"),
        (reader_id, "reader"),
        (outsider_id, "outsider"),
    ):
        principal = Principal(
            id=pid,
            organization_id=organization_id,
            principal_key=f"reindex-{key}-{pid}",
            principal_kind="user",
            display_name=key,
            status="active",
        )
        session.add(principal)
        principals[key] = principal
    await session.flush()

    await _grant_in_session(
        session,
        organization_id=organization_id,
        actor_principal_id=admin_id,
        target_principal_id=admin_id,
        role_key="reindex-admin",
        capability_keys=("index:rebuild", "index:read"),
    )
    await _grant_in_session(
        session,
        organization_id=organization_id,
        actor_principal_id=admin_id,
        target_principal_id=reader_id,
        role_key="reindex-reader",
        capability_keys=("index:read",),
    )
    # Give admin direct read access to restricted-allowed via role:incident-reader.
    await _grant_in_session(
        session,
        organization_id=organization_id,
        actor_principal_id=admin_id,
        target_principal_id=admin_id,
        role_key="incident-reader",
        capability_keys=(),
    )

    payload = {"max_characters": 1200}
    configuration = DerivedConfiguration(
        id=uuid4(),
        organization_id=organization_id,
        configuration_key=f"reindex-cfg-{organization_id}",
        chunking_version="v1",
        embedding_model="nomic-embed-text",
        embedding_dimensions=768,
        configuration_payload=payload,
        configuration_hash=sha256(repr(sorted(payload.items())).encode()).hexdigest(),
        status="active",
        created_by_principal_id=admin_id,
    )
    session.add(configuration)
    await session.flush()

    org_doc = validate_markdown_document(
        ORG_DOCUMENT.read_text(encoding="utf-8"),
        source_uri=ORG_DOCUMENT.as_uri(),
    )
    org_ingested = await ingest_validated_document(
        session,
        organization_id=organization_id,
        actor_principal_id=admin_id,
        document=org_doc,
    )
    restricted_doc = validate_markdown_document(
        RESTRICTED_DOCUMENT.read_text(encoding="utf-8"),
        source_uri=RESTRICTED_DOCUMENT.as_uri(),
    )
    restricted_ingested = await ingest_validated_document(
        session,
        organization_id=organization_id,
        actor_principal_id=admin_id,
        document=restricted_doc,
    )
    await session.flush()
    return principals, org_ingested, restricted_ingested


@pytest.mark.asyncio
async def test_missing_capability_returns_403_and_records_audit(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            principals, _, _ = await _seed_reindex_context(session)
            outsider = principals["outsider"]
            _configure_endpoint(session, principal=outsider)

            response = await _post_reindex(
                payload={
                    "scope": {"type": "organization"},
                    "targets": ["chunks"],
                    "reason": "manual",
                },
                idempotency_key=f"k-{uuid4()}",
            )

            assert response.status_code == 403
            assert response.json()["error"]["code"] == "reindex_forbidden"

            audit = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == outsider.organization_id,
                    AuditEvent.actor_principal_id == outsider.id,
                    AuditEvent.event_type == "reindex.denied",
                )
            )
            assert audit is not None
            assert audit.outcome == "denied"
            assert audit.reason_code == "reindex_forbidden"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_idempotency_key_required(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            principals, _, _ = await _seed_reindex_context(session)
            _configure_endpoint(session, principal=principals["admin"])

            response = await _post_reindex(
                payload={
                    "scope": {"type": "organization"},
                    "targets": ["chunks"],
                    "reason": "manual",
                },
                idempotency_key=None,
            )

            assert response.status_code == 422
            assert response.json()["error"]["code"] == "idempotency_key_required"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_create_and_replay_and_conflict(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            principals, _, _ = await _seed_reindex_context(session)
            _configure_endpoint(session, principal=principals["admin"])
            key = f"replay-{uuid4()}"

            first = await _post_reindex(
                payload={
                    "scope": {"type": "documents", "document_ids": ["fraud-service"]},
                    "targets": ["chunks"],
                    "reason": "manual",
                },
                idempotency_key=key,
            )
            assert first.status_code == 202
            first_body = first.json()
            assert first_body["request_id"] == first.headers["X-Request-Id"]
            assert first_body["status"] == "queued"
            assert first_body["scope_type"] == "documents"
            assert first_body["idempotency_replayed"] is False
            assert first_body["requested_document_count"] == 1
            job_id = first_body["job_id"]

            second = await _post_reindex(
                payload={
                    "scope": {"type": "documents", "document_ids": ["fraud-service"]},
                    "targets": ["chunks"],
                    "reason": "manual",
                },
                idempotency_key=key,
            )
            assert second.status_code == 202
            assert second.json()["job_id"] == job_id
            assert second.json()["idempotency_replayed"] is True
            assert second.json()["request_id"] == second.headers["X-Request-Id"]
            assert second.json()["request_id"] != first_body["request_id"]

            conflict = await _post_reindex(
                payload={
                    "scope": {"type": "documents", "document_ids": ["fraud-service"]},
                    "targets": ["chunks"],
                    "reason": "changed",
                },
                idempotency_key=key,
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "idempotency_conflict"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_restricted_document_returns_404(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            principals, _, _ = await _seed_reindex_context(session)
            reader = principals["reader"]
            await _grant_in_session(
                session,
                organization_id=reader.organization_id,
                actor_principal_id=principals["admin"].id,
                target_principal_id=reader.id,
                role_key="reader-can-rebuild",
                capability_keys=("index:rebuild",),
            )
            _configure_endpoint(session, principal=reader)

            response = await _post_reindex(
                payload={
                    "scope": {"type": "documents", "document_ids": ["restricted-allowed"]},
                    "targets": ["chunks"],
                    "reason": "manual",
                },
                idempotency_key=f"restricted-{uuid4()}",
            )

            assert response.status_code == 404
            assert response.json()["error"]["code"] == "document_not_found"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_get_job_filters_items_by_acl_and_enforces_index_read(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            principals, org_ingested, restricted_ingested = await _seed_reindex_context(session)
            _configure_endpoint(session, principal=principals["admin"])

            created = await _post_reindex(
                payload={
                    "scope": {
                        "type": "documents",
                        "document_ids": ["fraud-service", "restricted-allowed"],
                    },
                    "targets": ["chunks"],
                    "reason": "manual",
                },
                idempotency_key=f"acl-{uuid4()}",
            )
            assert created.status_code == 202
            assert created.json()["requested_document_count"] == 2
            job_id = created.json()["job_id"]

            # Reader has index:read and org-scoped access only -> restricted item and count must be filtered.
            _configure_endpoint(session, principal=principals["reader"])
            reader_view = await _get_reindex(job_id)
            assert reader_view.status_code == 200
            assert reader_view.json()["request_id"] == reader_view.headers["X-Request-Id"]
            assert reader_view.json()["requested_document_count"] == 1
            visible_pks = {item["document_pk"] for item in reader_view.json()["items"]}
            assert visible_pks == {str(org_ingested.document_pk)}
            assert str(restricted_ingested.document_pk) not in visible_pks

            # Outsider without index:read receives 404 without disclosing job existence.
            _configure_endpoint(session, principal=principals["outsider"])
            outsider_view = await _get_reindex(job_id)
            assert outsider_view.status_code == 404
            assert outsider_view.json()["error"]["code"] == "reindex_job_not_found"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "targets",
    [["full_text"], ["relationships"], ["chunks", "embeddings"]],
)
async def test_unsupported_and_multi_targets_return_422(
    migration_database_engine,
    targets: list[str],
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            principals, _, _ = await _seed_reindex_context(session)
            _configure_endpoint(session, principal=principals["admin"])

            response = await _post_reindex(
                payload={
                    "scope": {"type": "organization"},
                    "targets": targets,
                    "reason": "manual",
                },
                idempotency_key=f"target-{uuid4()}",
            )

            assert response.status_code == 422
            assert response.json()["error"]["code"] == "invalid_target"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_replay_filters_count_after_acl_revocation(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            principals, _, _ = await _seed_reindex_context(session)
            admin = principals["admin"]
            _configure_endpoint(session, principal=admin)
            key = f"revoke-replay-{uuid4()}"
            payload = {
                "scope": {
                    "type": "documents",
                    "document_ids": ["fraud-service", "restricted-allowed"],
                },
                "targets": ["chunks"],
                "reason": "manual",
            }

            first = await _post_reindex(payload=payload, idempotency_key=key)
            assert first.status_code == 202
            assert first.json()["requested_document_count"] == 2
            job_id = first.json()["job_id"]

            # Revoke the restricted-document role membership before replaying.
            incident_role = await session.scalar(
                select(Role).where(
                    Role.organization_id == admin.organization_id,
                    Role.role_key == "incident-reader",
                )
            )
            membership = await session.scalar(
                select(PrincipalRoleMembership).where(
                    PrincipalRoleMembership.organization_id == admin.organization_id,
                    PrincipalRoleMembership.principal_id == admin.id,
                    PrincipalRoleMembership.role_id == incident_role.id,
                )
            )
            membership.status = "revoked"
            await session.flush()

            replayed = await _post_reindex(payload=payload, idempotency_key=key)
            assert replayed.status_code == 202
            assert replayed.json()["job_id"] == job_id
            assert replayed.json()["idempotency_replayed"] is True
            assert replayed.json()["requested_document_count"] == 1
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_reindex_scope_validation_rules(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            principals, _, _ = await _seed_reindex_context(session)
            _configure_endpoint(session, principal=principals["admin"])

            empty_docs = await _post_reindex(
                payload={
                    "scope": {"type": "documents", "document_ids": []},
                    "targets": ["chunks"],
                    "reason": "manual",
                },
                idempotency_key=f"s1-{uuid4()}",
            )
            org_with_ids = await _post_reindex(
                payload={
                    "scope": {"type": "organization", "document_ids": ["fraud-service"]},
                    "targets": ["chunks"],
                    "reason": "manual",
                },
                idempotency_key=f"s2-{uuid4()}",
            )
            blank_reason = await _post_reindex(
                payload={
                    "scope": {"type": "organization"},
                    "targets": ["chunks"],
                    "reason": "   ",
                },
                idempotency_key=f"s3-{uuid4()}",
            )

            assert empty_docs.status_code == 422
            assert empty_docs.json()["error"]["code"] == "validation_failed"
            assert org_with_ids.status_code == 422
            assert org_with_ids.json()["error"]["code"] == "validation_failed"
            assert blank_reason.status_code == 422
            assert blank_reason.json()["error"]["code"] == "validation_failed"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()

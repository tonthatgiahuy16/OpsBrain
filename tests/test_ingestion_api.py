"""HTTP contract tests for queued ingestion boundary (``src/api/ingestions.py``)."""

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.database import get_db
from src.ingestion.ingestion_worker import process_next_ingestion_job
from src.main import app
from src.models.storage import (
    AuditEvent,
    Capability,
    Document,
    IngestionJob,
    IngestionJobItem,
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
PAYMENT_SERVICE_DOCUMENT = (
    Path(__file__).parent.parent
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "payment-service.md"
)
RESTRICTED_DOCUMENT = Path(__file__).parent / "fixtures" / "restricted-allowed.md"


def _configure_endpoint(session: AsyncSession, *, principal: Principal) -> None:
    principal_id = principal.id
    organization_id = principal.organization_id

    async def fake_db():
        yield session

    async def fake_principal():
        return AuthenticatedPrincipal(
            id=principal_id,
            organization_id=organization_id,
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_authenticated_principal] = fake_principal


async def _post_ingestion(
    *,
    payload: dict[str, object],
    idempotency_key: str | None = None,
):
    headers = {"Authorization": "Bearer ignored-by-test-override"}
    if idempotency_key is not None:
        headers["Idempotency-Key"] = idempotency_key
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post("/api/v1/ingestions", headers=headers, json=payload)


async def _get_ingestion(job_id: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.get(
            f"/api/v1/ingestions/{job_id}",
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


async def _seed_ingestion_context(session: AsyncSession):
    org_id = uuid4()
    other_org_id = uuid4()
    ingester_id = uuid4()
    reader_id = uuid4()
    outsider_id = uuid4()
    other_org_user_id = uuid4()

    session.add_all(
        [
            Organization(
                id=org_id,
                organization_key=f"ingest-org-{org_id}",
                display_name="Ingestion Org",
                status="active",
            ),
            Organization(
                id=other_org_id,
                organization_key=f"ingest-other-{other_org_id}",
                display_name="Other Org",
                status="active",
            ),
        ]
    )
    await session.flush()

    principals = {}
    for pid, oid, key in (
        (ingester_id, org_id, "ingester"),
        (reader_id, org_id, "reader"),
        (outsider_id, org_id, "outsider"),
        (other_org_user_id, other_org_id, "other_org_user"),
    ):
        principal = Principal(
            id=pid,
            organization_id=oid,
            principal_key=f"ingest-{key}-{pid}",
            principal_kind="user",
            display_name=key,
            status="active",
        )
        session.add(principal)
        principals[key] = principal
    await session.flush()

    await _grant_in_session(
        session,
        organization_id=org_id,
        actor_principal_id=ingester_id,
        target_principal_id=ingester_id,
        role_key="ingester-role",
        capability_keys=("document:ingest", "ingestion:read"),
    )
    await _grant_in_session(
        session,
        organization_id=org_id,
        actor_principal_id=ingester_id,
        target_principal_id=ingester_id,
        role_key="incident-reader",
        capability_keys=(),
    )
    await _grant_in_session(
        session,
        organization_id=org_id,
        actor_principal_id=ingester_id,
        target_principal_id=reader_id,
        role_key="ingestion-auditor",
        capability_keys=("ingestion:read",),
    )
    await _grant_in_session(
        session,
        organization_id=other_org_id,
        actor_principal_id=other_org_user_id,
        target_principal_id=other_org_user_id,
        role_key="other-org-reader",
        capability_keys=("ingestion:read",),
    )
    return principals


def _session_factory_on(connection):
    @asynccontextmanager
    async def _factory():
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()

    return _factory


@pytest.mark.asyncio
async def test_missing_capability_returns_403_and_safe_audit(
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
            principals = await _seed_ingestion_context(session)
            outsider = principals["outsider"]
            _configure_endpoint(session, principal=outsider)

            secret_markdown = "---\nsecret: should-not-be-logged\n---\n# Secret"
            response = await _post_ingestion(
                payload={
                    "documents": [
                        {
                            "source_uri": "obsidian://vault/secret.md",
                            "markdown_content": secret_markdown,
                        }
                    ]
                },
                idempotency_key=f"denied-{uuid4()}",
            )

            assert response.status_code == 403
            assert response.json()["error"]["code"] == "ingestion_forbidden"

            audit = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == outsider.organization_id,
                    AuditEvent.actor_principal_id == outsider.id,
                    AuditEvent.event_type == "ingestion.denied",
                )
            )
            assert audit is not None
            assert audit.outcome == "denied"
            assert audit.reason_code == "ingestion_forbidden"
            assert audit.request_id == response.json()["request_id"]
            assert audit.request_id == response.headers["X-Request-Id"]
            assert audit.details == {"required_capability": "document:ingest"}
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(IngestionJob)
                    .where(IngestionJob.organization_id == outsider.organization_id)
                )
                == 0
            )
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
            principals = await _seed_ingestion_context(session)
            _configure_endpoint(session, principal=principals["ingester"])

            response = await _post_ingestion(
                payload={
                    "documents": [
                        {
                            "source_uri": "obsidian://vault/fraud-service.md",
                            "markdown_content": ORG_DOCUMENT.read_text(encoding="utf-8"),
                        }
                    ]
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
async def test_post_queues_202_with_submitted_markdown_and_replays_cleanly(
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
            principals = await _seed_ingestion_context(session)
            ingester = principals["ingester"]
            _configure_endpoint(session, principal=ingester)
            key = f"ingest-{uuid4()}"
            raw_markdown = ORG_DOCUMENT.read_text(encoding="utf-8")
            payload = {
                "documents": [
                    {
                        "source_uri": "obsidian://vault/fraud-service.md",
                        "markdown_content": raw_markdown,
                    }
                ]
            }

            first = await _post_ingestion(payload=payload, idempotency_key=key)
            assert first.status_code == 202
            body = first.json()
            assert body["request_id"] == first.headers["X-Request-Id"]
            assert body["status"] == "queued"
            assert body["submitted_document_count"] == 1
            assert body["idempotency_replayed"] is False
            job_id = UUID(body["job_id"])

            # API must persist queued item + submitted_markdown without running lifecycle yet.
            queued_item = await session.scalar(
                select(IngestionJobItem).where(
                    IngestionJobItem.organization_id == ingester.organization_id,
                    IngestionJobItem.ingestion_job_id == job_id,
                )
            )
            assert queued_item is not None
            assert queued_item.processing_status == "queued"
            assert queued_item.submitted_markdown == raw_markdown
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(Document)
                    .where(Document.organization_id == ingester.organization_id)
                )
                == 0
            )
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.organization_id == ingester.organization_id,
                        AuditEvent.event_type == "ingestion.accepted",
                    )
                )
                == 1
            )

            replayed = await _post_ingestion(payload=payload, idempotency_key=key)
            assert replayed.status_code == 202
            assert replayed.json()["job_id"] == str(job_id)
            assert replayed.json()["idempotency_replayed"] is True
            assert replayed.json()["request_id"] == replayed.headers["X-Request-Id"]
            assert replayed.json()["request_id"] != body["request_id"]
            assert (
                await session.scalar(
                    select(func.count())
                    .select_from(AuditEvent)
                    .where(
                        AuditEvent.organization_id == ingester.organization_id,
                        AuditEvent.event_type == "ingestion.accepted",
                    )
                )
                == 1
            )

            conflict = await _post_ingestion(
                payload={
                    "documents": [
                        {
                            "source_uri": "obsidian://vault/other.md",
                            "markdown_content": raw_markdown,
                        }
                    ]
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
async def test_get_ingestion_enforces_org_scope_capability_and_acl_after_worker(
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
            principals = await _seed_ingestion_context(session)
            _configure_endpoint(session, principal=principals["ingester"])
            sentinel = "SECRET-SENTINEL-DO-NOT-LEAK"

            created = await _post_ingestion(
                payload={
                    "documents": [
                        {
                            "source_uri": "obsidian://vault/payment-service.md",
                            "markdown_content": PAYMENT_SERVICE_DOCUMENT.read_text(encoding="utf-8"),
                        },
                        {
                            "source_uri": "obsidian://vault/restricted-allowed.md",
                            "markdown_content": RESTRICTED_DOCUMENT.read_text(encoding="utf-8"),
                        },
                        {
                            "source_uri": "obsidian://vault/invalid-secret.md",
                            "markdown_content": (
                                f"---\ntitle: {sentinel}\ndocument_type: invalid_enum\n---\n"
                                f"# {sentinel}"
                            ),
                        },
                    ]
                },
                idempotency_key=f"acl-ingest-{uuid4()}",
            )
            assert created.status_code == 202
            job_id = created.json()["job_id"]

            reader_principal = principals["reader"]
            outsider_principal = principals["outsider"]
            other_org_principal = principals["other_org_user"]

            processed_job_id = await process_next_ingestion_job(
                _session_factory_on(connection)
            )
            assert str(processed_job_id) == job_id

            async with _session_factory_on(connection)() as read_session:
                # Reader has ingestion:read, but only org-scoped document access.
                _configure_endpoint(read_session, principal=reader_principal)
                reader_resp = await _get_ingestion(job_id)
                assert reader_resp.status_code == 200
                assert sentinel not in reader_resp.text
                reader_body = reader_resp.json()
                assert reader_body["request_id"] == reader_resp.headers["X-Request-Id"]
                assert reader_body["request_id"] != created.json()["request_id"]
                assert reader_body["status"] == "completed_with_errors"
                assert reader_body["summary"] == {
                    "submitted": 2,
                    "succeeded": 1,
                    "failed": 1,
                    "warnings": 1,
                }
                assert [doc["document_id"] for doc in reader_body["documents"]] == [
                    "payment-service",
                    None,
                ]
                assert reader_body["documents"][0]["version"] == 1
                assert reader_body["documents"][1]["version"] is None
                assert reader_body["documents"][1]["errors"] == [
                    {"code": "validation_error", "message": "document validation failed"}
                ]

                # Outsider in same org without ingestion:read gets 404.
                _configure_endpoint(read_session, principal=outsider_principal)
                outsider_resp = await _get_ingestion(job_id)
                assert outsider_resp.status_code == 404
                assert outsider_resp.json()["error"]["code"] == "ingestion_job_not_found"

                # Cross-org user with ingestion:read gets 404.
                _configure_endpoint(read_session, principal=other_org_principal)
                cross_org_resp = await _get_ingestion(job_id)
                assert cross_org_resp.status_code == 404
                assert cross_org_resp.json()["error"]["code"] == "ingestion_job_not_found"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()

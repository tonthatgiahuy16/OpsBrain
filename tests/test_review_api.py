"""HTTP contract tests for document review decisions."""

from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.queries import AuthenticatedPrincipal, get_authenticated_principal
from src.database import get_db
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.main import app
from src.models.storage import (
    AuditEvent,
    DocumentReviewDecision,
    DocumentVersion,
    Principal,
    Role,
    RoleCapability,
)
from tests.test_document_review_decisions import _seed


RESTRICTED = Path(__file__).parent / "fixtures" / "restricted-allowed.md"


async def _post_review(document_id: str, *, payload: dict, idempotency_key: str):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.post(
            f"/api/v1/documents/{document_id}/review-decisions",
            headers={
                "Authorization": "Bearer ignored-by-test-override",
                "Idempotency-Key": idempotency_key,
            },
            json=payload,
        )


def _configure_endpoint(session: AsyncSession, *, principal) -> None:
    async def fake_db():
        yield session

    async def fake_principal():
        return AuthenticatedPrincipal(
            id=principal.id, organization_id=principal.organization_id,
        )

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_authenticated_principal] = fake_principal


@pytest.mark.asyncio
async def test_review_endpoint_returns_approval_forbidden_for_visible_document_without_capability(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, _ = await _seed(session)
            await session.execute(
                delete(RoleCapability).where(
                    RoleCapability.organization_id == organization_id,
                    RoleCapability.capability_key == "document:approve",
                )
            )
            await session.flush()
            principal = await session.get(Principal, reviewer_id)
            _configure_endpoint(session, principal=principal)

            response = await _post_review(
                "fraud-service",
                payload={
                    "decision": "approve",
                    "expected_version": ingested.version_number,
                    "reason": "Review the submitted evidence.",
                },
                idempotency_key=f"review-{uuid4()}",
            )

            assert response.status_code == 403
            assert response.json()["detail"] == "approval_forbidden"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_endpoint_hides_unknown_document_as_not_found(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            _, _, reviewer_id, _, _ = await _seed(session)
            principal = await session.get(Principal, reviewer_id)
            _configure_endpoint(session, principal=principal)

            response = await _post_review(
                "does-not-exist",
                payload={
                    "decision": "approve",
                    "expected_version": 1,
                    "reason": "Review the submitted evidence.",
                },
                idempotency_key=f"review-{uuid4()}",
            )

            assert response.status_code == 404
            assert response.json()["detail"] == "document_not_found"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_endpoint_hides_existing_restricted_document_before_version_check(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, reviewer_id, _, _ = await _seed(session)
            session.add(Role(
                id=uuid4(), organization_id=organization_id,
                role_key="incident-reader", display_name="Incident reader",
                status="active",
            ))
            await session.flush()
            restricted = validate_markdown_document(
                RESTRICTED.read_text(encoding="utf-8"),
                source_uri=RESTRICTED.as_uri(),
            )
            await ingest_validated_document(
                session,
                organization_id=organization_id,
                actor_principal_id=author_id,
                document=restricted,
            )
            principal = await session.get(Principal, reviewer_id)
            _configure_endpoint(session, principal=principal)

            response = await _post_review(
                "restricted-allowed",
                payload={
                    "decision": "approve",
                    "expected_version": 999,
                    "reason": "Review the submitted evidence.",
                },
                idempotency_key=f"review-{uuid4()}",
            )

            assert response.status_code == 404
            assert response.json()["detail"] == "document_not_found"
            denied = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.event_type == "document.review.denied",
                )
            )
            assert denied is not None
            assert denied.reason_code == "document_not_found"
            assert denied.document_pk is None
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_endpoint_approves_current_visible_version(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            _, _, reviewer_id, ingested, _ = await _seed(session)
            principal = await session.get(Principal, reviewer_id)
            _configure_endpoint(session, principal=principal)

            response = await _post_review(
                "fraud-service",
                payload={
                    "decision": "approve",
                    "expected_version": ingested.version_number,
                    "reason": "Review the submitted evidence.",
                },
                idempotency_key=f"review-{uuid4()}",
            )

            assert response.status_code == 200
            body = response.json()
            assert body["document_id"] == "fraud-service"
            assert body["decision"] == "approve"
            assert body["previous_version"] == ingested.version_number
            assert body["current_version"] == ingested.version_number + 1
            assert body["review_state"] == "approved"
            assert body["retrieval_eligible"] is True
            assert body["query_ready"] is False
            assert body["index_status"] == "pending"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_endpoint_replays_same_idempotency_key_after_version_advances(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            _, _, reviewer_id, ingested, _ = await _seed(session)
            principal = await session.get(Principal, reviewer_id)
            _configure_endpoint(session, principal=principal)
            payload = {
                "decision": "approve",
                "expected_version": ingested.version_number,
                "reason": "Review the submitted evidence.",
            }
            key = f"review-replay-{uuid4()}"

            first = await _post_review(
                "fraud-service", payload=payload, idempotency_key=key,
            )
            replay = await _post_review(
                "fraud-service", payload=payload, idempotency_key=key,
            )

            assert first.status_code == replay.status_code == 200
            assert replay.json()["review_decision_id"] == first.json()["review_decision_id"]
            decisions = list((await session.scalars(
                select(DocumentReviewDecision).where(
                    DocumentReviewDecision.document_pk == ingested.document_pk,
                )
            )).all())
            versions = list((await session.scalars(
                select(DocumentVersion).where(
                    DocumentVersion.document_pk == ingested.document_pk,
                )
            )).all())
            assert len(decisions) == 1
            assert len(versions) == 2
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_endpoint_rejects_changed_payload_for_existing_idempotency_key(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            _, _, reviewer_id, ingested, _ = await _seed(session)
            principal = await session.get(Principal, reviewer_id)
            _configure_endpoint(session, principal=principal)
            key = f"review-conflict-{uuid4()}"
            payload = {
                "decision": "approve",
                "expected_version": ingested.version_number,
                "reason": "Review the submitted evidence.",
            }
            first = await _post_review(
                "fraud-service", payload=payload, idempotency_key=key,
            )
            conflict = await _post_review(
                "fraud-service",
                payload={**payload, "reason": "Changed review reason."},
                idempotency_key=key,
            )

            assert first.status_code == 200
            assert conflict.status_code == 409
            assert conflict.json()["detail"] == "idempotency_conflict"
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()

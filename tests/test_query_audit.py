"""Audit-policy tests for the query endpoint (Milestone 5, contract §12.6 step 10).

Every terminal outcome must append exactly one ``audit_events`` row, and that
row must not become a side channel for material the principal could not see.
"""

import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.main import app
from src.models.storage import AuditEvent, Principal
from tests.support import grant_capability
from tests.test_query_acl import _seed_acl_documents
from tests.test_query_security import (
    _configure_endpoint,
    _delete_query_capability,
    _post,
)


async def _audit_events(session: AsyncSession, *, organization_id):
    return list((await session.scalars(
        select(AuditEvent)
        .where(
            AuditEvent.organization_id == organization_id,
            AuditEvent.event_type.like("query.%"),
        )
        .order_by(AuditEvent.occurred_at)
    )).all())


@pytest.mark.asyncio
async def test_evidence_returned_writes_one_audit_event_without_content(
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
            events = await _audit_events(
                session, organization_id=allowed["organization_id"],
            )

            assert len(events) == 1
            event = events[0]
            assert event.event_type == "query.executed"
            assert event.outcome == "succeeded"
            assert event.reason_code is None
            assert event.actor_kind == "principal"
            assert event.actor_principal_id == viewer.id
            assert event.request_id == body["request_id"]
            assert event.document_pk is None
            assert event.document_version_id is None

            details = event.details
            assert details["strategy"] == "hybrid"
            assert details["response_mode"] == "evidence_only"
            assert details["top_k"] == 5
            assert details["retrieval_config_id"] == body["configuration"]["retrieval_config_id"]
            assert details["embedding_model"] == body["configuration"]["embedding_model"]
            assert details["evidence_count"] == len(body["evidence"])
            assert [item["chunk_id"] for item in details["evidence"]] == [
                item["chunk_id"] for item in body["evidence"]
            ]
            assert [item["vector_score"] for item in details["evidence"]] == [
                round(item["scores"]["vector"], 6) for item in body["evidence"]
            ]
            assert set(details["latency_ms"]) == {"retrieval", "generation", "total"}
            assert details["latency_ms"]["generation"] is None

            serialized = json.dumps(details)
            # Default question text, excerpts and the unauthorized document
            # must never reach the audit trail.
            assert "restricted evidence" not in serialized
            assert "excerpt" not in serialized
            assert "Restricted Allowed Runbook" not in serialized
            assert "restricted-blocked" not in serialized
            assert str(blocked["document_pk"]) not in serialized
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_abstention_writes_reason_without_document_or_question(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, blocked, _, _ = await _seed_acl_documents(session)
            outsider = Principal(
                id=uuid4(), organization_id=allowed["organization_id"],
                principal_key=f"audit-outsider-{uuid4()}", principal_kind="user",
                display_name="Audit outsider", status="active",
            )
            session.add(outsider)
            await session.flush()
            await grant_capability(
                session, organization_id=allowed["organization_id"],
                principal_id=outsider.id, capability="query:execute",
            )
            _configure_endpoint(
                session, principal=outsider,
                configuration=allowed["configuration"], monkeypatch=monkeypatch,
            )

            body = (await _post(
                {"question": "can I read the blocked runbook", "top_k": 3}
            )).json()
            assert body["status"] == "abstained"

            events = await _audit_events(
                session, organization_id=allowed["organization_id"],
            )
            assert len(events) == 1
            event = events[0]
            assert event.event_type == "query.abstained"
            assert event.outcome == "succeeded"
            assert event.reason_code == "no_eligible_evidence"
            assert event.request_id == body["request_id"]
            assert event.details["retrieval_config_id"] == body["configuration"]["retrieval_config_id"]
            assert event.details["embedding_model"] == body["configuration"]["embedding_model"]
            assert event.details["abstention_reason"] == "no_eligible_evidence"
            assert event.details["evidence_count"] == 0
            assert "evidence" not in event.details

            serialized = json.dumps(event.details)
            assert "can I read the blocked runbook" not in serialized
            assert "restricted-blocked" not in serialized
            assert str(blocked["document_pk"]) not in serialized
            assert str(allowed["document_pk"]) not in serialized
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_capability_denial_is_audited_and_durable(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, blocked, viewer, _ = await _seed_acl_documents(session)
            await _delete_query_capability(
                session, organization_id=allowed["organization_id"],
            )
            _configure_endpoint(
                session, principal=viewer, configuration=allowed["configuration"],
                monkeypatch=monkeypatch,
            )

            response = await _post({"question": "forbidden question text"})
            assert response.status_code == 403
            assert response.json()["error"]["code"] == "query_forbidden"

            # The denial commit happens before the exception aborts the
            # response, so the event survives the failed request.
            events = await _audit_events(
                session, organization_id=allowed["organization_id"],
            )
            assert len(events) == 1
            event = events[0]
            assert event.event_type == "query.denied"
            assert event.outcome == "denied"
            assert event.reason_code == "query_forbidden"
            assert event.actor_principal_id == viewer.id
            assert event.details == {"required_capability": "query:execute"}
            assert event.request_id
            assert event.request_id == response.json()["request_id"]
            assert event.request_id == response.headers["X-Request-Id"]

            serialized = json.dumps(event.details)
            assert "forbidden question text" not in serialized
            assert str(blocked["document_pk"]) not in serialized
            assert "restricted-allowed" not in serialized
        finally:
            app.dependency_overrides.clear()
            await session.close()
            await transaction.rollback()

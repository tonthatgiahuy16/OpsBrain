from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.ingestion.review import submit_review_decision
from src.models.storage import (
    AuditEvent,
    Document,
    DocumentReviewDecision,
    DocumentVersion,
    Organization,
    Principal,
)


SAMPLE = (
    Path(__file__).parents[1] / "data" / "sample-vault" / "payment-service"
    / "services" / "fraud-service.md"
)


async def _seed(session: AsyncSession):
    organization_id, author_id, reviewer_id = uuid4(), uuid4(), uuid4()
    session.add_all([
        Organization(
            id=organization_id,
            organization_key=f"review-org-{organization_id}",
            display_name="Review test", status="active",
        ),
        Principal(
            id=author_id, organization_id=organization_id,
            principal_key=f"review-author-{author_id}", principal_kind="user",
            display_name="Author", status="active",
        ),
        Principal(
            id=reviewer_id, organization_id=organization_id,
            principal_key=f"reviewer-{reviewer_id}", principal_kind="user",
            display_name="Reviewer", status="active",
        ),
    ])
    await session.flush()
    parsed = validate_markdown_document(
        SAMPLE.read_text(encoding="utf-8"), source_uri=SAMPLE.as_uri()
    )
    source = parsed.model_copy(update={
        "frontmatter": parsed.frontmatter.model_copy(
            update={"review_state": "in_review"}
        )
    })
    ingested = await ingest_validated_document(
        session, organization_id=organization_id,
        actor_principal_id=author_id, document=source,
    )
    return organization_id, author_id, reviewer_id, ingested


def _make_child_version(
    parent: DocumentVersion,
    *,
    created_by_principal_id,
    review_state: str,
    change_kind: str = "review_transition",
) -> DocumentVersion:
    values = {
        column.key: getattr(parent, column.key)
        for column in DocumentVersion.__table__.columns
        if column.key not in {"id", "created_at", "version_number", "parent_version_id"}
    }
    values.update({
        "id": uuid4(),
        "version_number": parent.version_number + 1,
        "parent_version_id": parent.id,
        "review_state": review_state,
        "change_kind": change_kind,
        "created_by_principal_id": created_by_principal_id,
        "frontmatter_snapshot": {
            **parent.frontmatter_snapshot, "review_state": review_state
        },
    })
    return DocumentVersion(**values)


def _review_decision_row(
    *, organization_id, document_pk, reviewed_version_id,
    resulting_version_id, reviewer_principal_id, decision="approve",
) -> DocumentReviewDecision:
    return DocumentReviewDecision(
        organization_id=organization_id,
        request_id=f"db-enforcement-{uuid4()}",
        idempotency_key_hash="a" * 64,
        request_payload_hash="b" * 64,
        document_pk=document_pk,
        reviewed_version_id=reviewed_version_id,
        resulting_version_id=resulting_version_id,
        decision=decision,
        reason="database enforcement fixture",
        reviewer_principal_id=reviewer_principal_id,
        decided_at=datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_approve_persists_one_version_decision_and_audit_and_replays(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested = await _seed(session)
            kwargs = dict(
                organization_id=organization_id,
                document_pk=ingested.document_pk,
                expected_version_id=ingested.document_version_id,
                reviewer_principal_id=reviewer_id,
                decision="approve", reason="Reviewed runbook evidence",
                request_id="review-request-1", idempotency_key="review-key-1",
            )
            first = await submit_review_decision(session, **kwargs)
            replay = await submit_review_decision(
                session, **{**kwargs, "request_id": "retry-request"}
            )
            assert first.replayed is False
            assert replay.replayed is True
            assert replay.decision.id == first.decision.id
            assert first.resulting_version.review_state == "approved"
            assert first.resulting_version.change_kind == "review_transition"
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.document_pk == ingested.document_pk
            )) == 2
            assert await session.scalar(select(func.count()).select_from(DocumentReviewDecision).where(
                DocumentReviewDecision.document_pk == ingested.document_pk
            )) == 1
            event = await session.scalar(select(AuditEvent).where(
                AuditEvent.review_decision_id == first.decision.id
            ))
            assert event.event_type == "document.review.approved"
            current = await session.scalar(select(Document).where(Document.id == ingested.document_pk))
            assert current.current_version_id == first.resulting_version.id
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_reject_persists_event_and_idempotency_conflict_is_rejected(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested = await _seed(session)
            rejected = await submit_review_decision(
                session, organization_id=organization_id,
                document_pk=ingested.document_pk,
                expected_version_id=ingested.document_version_id,
                reviewer_principal_id=reviewer_id, decision="reject",
                reason="Missing operational owner", request_id="review-request-2",
                idempotency_key="review-key-2",
            )
            assert rejected.resulting_version.review_state == "rejected"
            event = await session.scalar(select(AuditEvent).where(
                AuditEvent.review_decision_id == rejected.decision.id
            ))
            assert event.event_type == "document.review.rejected"
            with pytest.raises(ValueError, match="idempotency key conflict"):
                await submit_review_decision(
                    session, organization_id=organization_id,
                    document_pk=ingested.document_pk,
                    expected_version_id=ingested.document_version_id,
                    reviewer_principal_id=reviewer_id, decision="approve",
                    reason="Different payload", request_id="review-request-3",
                    idempotency_key="review-key-2",
                )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_reject_invalidates_a_previously_active_derived_set(
    migration_database_engine,
):
    from src.ingestion.derived_activation import activate_derived_set
    from src.models.storage import DerivedSet
    from tests.test_derived_set_activation import _seed_activation_inputs

    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, document_pk, prior_version, _, derived_set = (
                await _seed_activation_inputs(session)
            )
            await activate_derived_set(
                session, organization_id=organization_id,
                derived_set_id=derived_set.id,
            )
            author_id, reviewer_id = prior_version.created_by_principal_id, uuid4()
            session.add(Principal(
                id=reviewer_id, organization_id=organization_id,
                principal_key=f"reviewer-{reviewer_id}", principal_kind="user",
                display_name="Reviewer", status="active",
            ))
            await session.flush()
            values = {
                column.key: getattr(prior_version, column.key)
                for column in DocumentVersion.__table__.columns
                if column.key not in {"id", "created_at", "version_number", "parent_version_id"}
            }
            values.update({
                "id": uuid4(), "version_number": prior_version.version_number + 1,
                "parent_version_id": prior_version.id, "review_state": "in_review",
                "change_kind": "review_transition", "created_by_principal_id": author_id,
                "frontmatter_snapshot": {
                    **prior_version.frontmatter_snapshot, "review_state": "in_review"
                },
            })
            pending = DocumentVersion(**values)
            session.add(pending)
            await session.flush()
            document = await session.scalar(select(Document).where(Document.id == document_pk))
            document.current_version_id = pending.id
            await session.flush()

            await submit_review_decision(
                session, organization_id=organization_id,
                document_pk=document_pk, expected_version_id=pending.id,
                reviewer_principal_id=reviewer_id, decision="reject",
                reason="Not safe for operations", request_id="reject-active-set",
                idempotency_key="reject-active-set-key",
            )
            active_count = await session.scalar(select(func.count()).select_from(DerivedSet).where(
                DerivedSet.organization_id == organization_id,
                DerivedSet.document_pk == document_pk,
                DerivedSet.is_active.is_(True),
            ))
            assert active_count == 0
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_rejects_author_and_stale_version_without_writes(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, reviewer_id, ingested = await _seed(session)
            common = dict(
                organization_id=organization_id,
                document_pk=ingested.document_pk,
                expected_version_id=ingested.document_version_id,
                decision="approve", reason="Looks good", request_id="review-request-4",
            )
            with pytest.raises(ValueError, match="author cannot review"):
                await submit_review_decision(
                    session, **common, reviewer_principal_id=author_id,
                    idempotency_key="author-key",
                )
            with pytest.raises(ValueError, match="stale expected version"):
                await submit_review_decision(
                    session, **{**common, "expected_version_id": uuid4()},
                    reviewer_principal_id=reviewer_id, idempotency_key="stale-key",
                )
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.document_pk == ingested.document_pk
            )) == 1
            assert await session.scalar(select(func.count()).select_from(DocumentReviewDecision)) == 0
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_rejects_current_version_that_is_not_in_review(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested = await _seed(session)
            reviewed = await session.scalar(select(DocumentVersion).where(
                DocumentVersion.id == ingested.document_version_id
            ))
            non_review = DocumentVersion(
                id=uuid4(), organization_id=organization_id,
                document_pk=reviewed.document_pk,
                parent_version_id=reviewed.id,
                version_number=reviewed.version_number + 1,
                schema_version=reviewed.schema_version,
                canonicalization_version=reviewed.canonicalization_version,
                hash_algorithm=reviewed.hash_algorithm,
                content_hash=reviewed.content_hash, title=reviewed.title,
                owner_team=reviewed.owner_team, source_uri=reviewed.source_uri,
                source_refs=list(reviewed.source_refs), tags=list(reviewed.tags),
                markdown_body=reviewed.markdown_body,
                frontmatter_snapshot={
                    **reviewed.frontmatter_snapshot, "review_state": "draft"
                },
                document_status=reviewed.document_status, review_state="draft",
                provenance_type=reviewed.provenance_type,
                access_scope=reviewed.access_scope,
                effective_from=reviewed.effective_from,
                effective_until=reviewed.effective_until,
                change_kind="content_update",
                created_by_principal_id=reviewed.created_by_principal_id,
            )
            session.add(non_review)
            document = await session.scalar(select(Document).where(Document.id == ingested.document_pk))
            document.current_version_id = non_review.id
            await session.flush()
            with pytest.raises(ValueError, match="version is not in review"):
                await submit_review_decision(
                    session, organization_id=organization_id,
                    document_pk=ingested.document_pk,
                    expected_version_id=non_review.id,
                    reviewer_principal_id=reviewer_id, decision="approve",
                    reason="Attempt approval", request_id="non-review-request",
                    idempotency_key="non-review-key",
                )
            assert await session.scalar(select(func.count()).select_from(DocumentReviewDecision)) == 0
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.document_pk == ingested.document_pk
            )) == 2
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_failure_after_version_and_decision_rolls_back_everything(
    migration_database_engine, monkeypatch,
):
    from src.ingestion import review

    async def fail_audit(*args, **kwargs):
        raise RuntimeError("injected audit failure")

    monkeypatch.setattr(review, "_append_review_audit", fail_audit)
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested = await _seed(session)
            with pytest.raises(RuntimeError, match="injected audit failure"):
                await submit_review_decision(
                    session, organization_id=organization_id,
                    document_pk=ingested.document_pk,
                    expected_version_id=ingested.document_version_id,
                    reviewer_principal_id=reviewer_id, decision="approve",
                    reason="Reviewed", request_id="review-request-5",
                    idempotency_key="review-key-5",
                )
            assert await session.scalar(select(func.count()).select_from(DocumentVersion).where(
                DocumentVersion.document_pk == ingested.document_pk
            )) == 1
            assert await session.scalar(select(func.count()).select_from(DocumentReviewDecision)) == 0
            assert await session.scalar(select(func.count()).select_from(AuditEvent).where(
                AuditEvent.request_id == "review-request-5"
            )) == 0
            document = await session.scalar(select(Document).where(Document.id == ingested.document_pk))
            assert document.current_version_id == ingested.document_version_id
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_cross_organization_review_decision_and_audit_references_are_rejected(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            org_a, _, reviewer_a, ingested_a = await _seed(session)
            org_b, _, reviewer_b, ingested_b = await _seed(session)
            decision_a = await submit_review_decision(
                session, organization_id=org_a,
                document_pk=ingested_a.document_pk,
                expected_version_id=ingested_a.document_version_id,
                reviewer_principal_id=reviewer_a, decision="approve",
                reason="Approve fixture A", request_id="cross-org-a",
                idempotency_key="cross-org-key-a",
            )

            version_b = await session.scalar(select(DocumentVersion).where(
                DocumentVersion.id == ingested_b.document_version_id
            ))
            approved_b = _make_child_version(
                version_b, created_by_principal_id=reviewer_b,
                review_state="approved",
            )
            session.add(approved_b)
            await session.flush()
            cross_org_decision = _review_decision_row(
                organization_id=org_b,
                document_pk=ingested_b.document_pk,
                reviewed_version_id=ingested_b.document_version_id,
                resulting_version_id=approved_b.id,
                reviewer_principal_id=reviewer_a,
            )
            with pytest.raises(DBAPIError, match="fk_document_review_decisions_reviewer"):
                async with session.begin_nested():
                    session.add(cross_org_decision)
                    await session.flush()

            cross_org_event = AuditEvent(
                organization_id=org_b,
                event_type="document.review.approved",
                outcome="succeeded", actor_kind="system",
                request_id="cross-org-audit-reference", details={},
                review_decision_id=decision_a.decision.id,
                occurred_at=datetime.now(timezone.utc),
            )
            with pytest.raises(DBAPIError, match="fk_audit_events_review_decision"):
                async with session.begin_nested():
                    session.add(cross_org_event)
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_database_trigger_rejects_invalid_review_lineage_and_state(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            org_lineage, _, reviewer_lineage, ingested_lineage = await _seed(session)
            org_state, _, reviewer_state, ingested_state = await _seed(session)

            reviewed_lineage = await session.scalar(select(DocumentVersion).where(
                DocumentVersion.id == ingested_lineage.document_version_id
            ))
            wrong_lineage = _make_child_version(
                reviewed_lineage, created_by_principal_id=reviewer_lineage,
                review_state="approved", change_kind="content_update",
            )
            session.add(wrong_lineage)
            await session.flush()
            invalid_lineage = _review_decision_row(
                organization_id=org_lineage,
                document_pk=ingested_lineage.document_pk,
                reviewed_version_id=reviewed_lineage.id,
                resulting_version_id=wrong_lineage.id,
                reviewer_principal_id=reviewer_lineage,
            )
            with pytest.raises(DBAPIError, match="invalid document review decision lineage"):
                async with session.begin_nested():
                    session.add(invalid_lineage)
                    await session.flush()

            reviewed_state = await session.scalar(select(DocumentVersion).where(
                DocumentVersion.id == ingested_state.document_version_id
            ))
            draft = _make_child_version(
                reviewed_state, created_by_principal_id=reviewer_state,
                review_state="draft", change_kind="content_update",
            )
            approved_from_draft = _make_child_version(
                draft, created_by_principal_id=reviewer_state,
                review_state="approved",
            )
            session.add_all([draft, approved_from_draft])
            await session.flush()
            invalid_state = _review_decision_row(
                organization_id=org_state,
                document_pk=ingested_state.document_pk,
                reviewed_version_id=draft.id,
                resulting_version_id=approved_from_draft.id,
                reviewer_principal_id=reviewer_state,
            )
            with pytest.raises(DBAPIError, match="reviewed document version is not in review"):
                async with session.begin_nested():
                    session.add(invalid_state)
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_decision_trigger_blocks_owner_update_and_delete(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested = await _seed(session)
            result = await submit_review_decision(
                session, organization_id=organization_id,
                document_pk=ingested.document_pk,
                expected_version_id=ingested.document_version_id,
                reviewer_principal_id=reviewer_id, decision="approve",
                reason="Immutable fixture", request_id="immutable-decision",
                idempotency_key="immutable-decision-key",
            )
            with pytest.raises(DBAPIError, match="document_review_decisions are immutable"):
                async with session.begin_nested():
                    await session.execute(
                        update(DocumentReviewDecision)
                        .where(DocumentReviewDecision.id == result.decision.id)
                        .values(reason="changed")
                    )
            with pytest.raises(DBAPIError, match="document_review_decisions are immutable"):
                async with session.begin_nested():
                    await session.execute(
                        delete(DocumentReviewDecision)
                        .where(DocumentReviewDecision.id == result.decision.id)
                    )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_application_role_can_submit_but_cannot_update_or_delete_decision(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested = await _seed(session)
            await connection.execute(text("SET LOCAL ROLE opsbrain_app"))
            assert await connection.scalar(text("SELECT current_user")) == "opsbrain_app"

            result = await submit_review_decision(
                session, organization_id=organization_id,
                document_pk=ingested.document_pk,
                expected_version_id=ingested.document_version_id,
                reviewer_principal_id=reviewer_id, decision="approve",
                reason="Application role review", request_id="app-role-review",
                idempotency_key="app-role-review-key",
            )
            assert result.decision.id is not None
            assert await connection.scalar(text(
                "SELECT has_table_privilege(current_user, "
                "'document_review_decisions', 'UPDATE')"
            )) is False
            assert await connection.scalar(text(
                "SELECT has_table_privilege(current_user, "
                "'document_review_decisions', 'DELETE')"
            )) is False

            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin_nested():
                    await session.execute(
                        update(DocumentReviewDecision)
                        .where(DocumentReviewDecision.id == result.decision.id)
                        .values(reason="application update")
                    )
            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin_nested():
                    await session.execute(
                        delete(DocumentReviewDecision)
                        .where(DocumentReviewDecision.id == result.decision.id)
                    )
        finally:
            await session.close()
            await transaction.rollback()

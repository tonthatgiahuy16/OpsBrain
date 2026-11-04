from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select
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

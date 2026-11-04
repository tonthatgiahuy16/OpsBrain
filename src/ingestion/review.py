"""Transactional, idempotent document review decisions.

Reviewer identity must come from trusted backend context. ACL and capability
authorization are not implemented by this module and remain unproven.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.canonicalization import normalize_markdown_body
from src.models.storage import (
    AuditEvent,
    DerivedSet,
    Document,
    DocumentReviewDecision,
    DocumentVersion,
    Principal,
)


ReviewDecision = Literal["approve", "reject"]


@dataclass(frozen=True)
class ReviewResult:
    decision: DocumentReviewDecision
    resulting_version: DocumentVersion
    replayed: bool


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _payload_hash(
    *, document_pk: UUID, expected_version_id: UUID,
    decision: ReviewDecision, reason: str,
) -> str:
    payload = {
        "document_pk": str(document_pk),
        "expected_version_id": str(expected_version_id),
        "decision": decision,
        "reason": reason,
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return _sha256(serialized.encode("utf-8"))


async def _find_idempotent_result(
    session: AsyncSession,
    *, organization_id: UUID,
    reviewer_principal_id: UUID,
    key_hash: str,
    payload_hash: str,
) -> ReviewResult | None:
    existing = await session.scalar(
        select(DocumentReviewDecision).where(
            DocumentReviewDecision.organization_id == organization_id,
            DocumentReviewDecision.reviewer_principal_id == reviewer_principal_id,
            DocumentReviewDecision.idempotency_key_hash == key_hash,
        )
    )
    if existing is None:
        return None
    if existing.request_payload_hash != payload_hash:
        raise ValueError("idempotency key conflict")
    version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == organization_id,
            DocumentVersion.document_pk == existing.document_pk,
            DocumentVersion.id == existing.resulting_version_id,
        )
    )
    if version is None:
        raise RuntimeError("review result version is missing")
    return ReviewResult(existing, version, True)


async def _append_review_audit(
    session: AsyncSession, *, organization_id: UUID, reviewer_principal_id: UUID,
    request_id: str, correlation_id: str | None, document_pk: UUID,
    resulting_version_id: UUID, decision_id: UUID, reviewed_version_id: UUID,
    decision: ReviewDecision, occurred_at: datetime,
) -> None:
    session.add(AuditEvent(
        organization_id=organization_id,
        event_type=(
            "document.review.approved"
            if decision == "approve"
            else "document.review.rejected"
        ),
        outcome="succeeded", actor_kind="principal",
        actor_principal_id=reviewer_principal_id,
        request_id=request_id, correlation_id=correlation_id,
        document_pk=document_pk, document_version_id=resulting_version_id,
        review_decision_id=decision_id,
        reason_code=f"review_{decision}",
        details={"reviewed_version_id": str(reviewed_version_id), "decision": decision},
        occurred_at=occurred_at,
    ))
    await session.flush()


async def submit_review_decision(
    session: AsyncSession,
    *,
    organization_id: UUID,
    document_pk: UUID,
    expected_version_id: UUID,
    reviewer_principal_id: UUID,
    decision: ReviewDecision,
    reason: str,
    request_id: str,
    idempotency_key: str,
    correlation_id: str | None = None,
) -> ReviewResult:
    """Persist review version, decision, invalidation and audit atomically.

    The caller owns the outer transaction. A savepoint ensures failures after
    any insert leave no partial state if the caller handles the exception.
    """
    if decision not in ("approve", "reject"):
        raise ValueError("decision must be approve or reject")
    if not reason.strip():
        raise ValueError("reason must not be blank")
    if not request_id.strip():
        raise ValueError("request_id must not be blank")
    if not idempotency_key:
        raise ValueError("idempotency key must not be blank")

    key_hash = _sha256(idempotency_key.encode("utf-8"))
    payload_hash = _payload_hash(
        document_pk=document_pk,
        expected_version_id=expected_version_id,
        decision=decision,
        reason=reason,
    )

    # Serialize equal idempotency keys even when requests target different docs.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": f"{organization_id}:{reviewer_principal_id}:{key_hash}"},
    )
    replay = await _find_idempotent_result(
        session,
        organization_id=organization_id,
        reviewer_principal_id=reviewer_principal_id,
        key_hash=key_hash,
        payload_hash=payload_hash,
    )
    if replay is not None:
        return replay

    async with session.begin_nested():
        document = await session.scalar(
            select(Document)
            .where(
                Document.organization_id == organization_id,
                Document.id == document_pk,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if document is None or document.record_state != "active":
            raise ValueError("active document not found")
        # Recheck after acquiring the document lock for requests racing on it.
        replay = await _find_idempotent_result(
            session,
            organization_id=organization_id,
            reviewer_principal_id=reviewer_principal_id,
            key_hash=key_hash,
            payload_hash=payload_hash,
        )
        if replay is not None:
            return replay

        if document.current_version_id != expected_version_id:
            raise ValueError("stale expected version")
        reviewed = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.document_pk == document_pk,
                DocumentVersion.id == expected_version_id,
            )
        )
        if reviewed is None:
            raise ValueError("expected version not found")
        if reviewed.review_state != "in_review":
            raise ValueError("version is not in review")
        if reviewed.created_by_principal_id == reviewer_principal_id:
            raise ValueError("author cannot review own version")
        reviewer = await session.scalar(
            select(Principal).where(
                Principal.organization_id == organization_id,
                Principal.id == reviewer_principal_id,
                Principal.status == "active",
            )
        )
        if reviewer is None:
            raise ValueError("active reviewer principal not found")

        resulting_state = "approved" if decision == "approve" else "rejected"
        snapshot = dict(reviewed.frontmatter_snapshot)
        snapshot["review_state"] = resulting_state
        canonical_payload = {
            "frontmatter": snapshot,
            "markdown_body": normalize_markdown_body(reviewed.markdown_body),
        }
        content_hash = _sha256(json.dumps(
            canonical_payload, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8"))
        now = datetime.now(timezone.utc)
        resulting = DocumentVersion(
            id=uuid4(), organization_id=organization_id,
            document_pk=document_pk, parent_version_id=reviewed.id,
            version_number=reviewed.version_number + 1,
            schema_version=reviewed.schema_version,
            canonicalization_version=reviewed.canonicalization_version,
            hash_algorithm=reviewed.hash_algorithm, content_hash=content_hash,
            title=reviewed.title, owner_team=reviewed.owner_team,
            source_uri=reviewed.source_uri, source_refs=list(reviewed.source_refs),
            tags=list(reviewed.tags), markdown_body=reviewed.markdown_body,
            frontmatter_snapshot=snapshot, document_status=reviewed.document_status,
            review_state=resulting_state, provenance_type=reviewed.provenance_type,
            access_scope=reviewed.access_scope,
            effective_from=reviewed.effective_from,
            effective_until=reviewed.effective_until,
            change_kind="review_transition",
            created_by_principal_id=reviewer_principal_id,
            created_at=now,
        )
        session.add(resulting)
        await session.flush()

        decision_row = DocumentReviewDecision(
            id=uuid4(), organization_id=organization_id,
            request_id=request_id, idempotency_key_hash=key_hash,
            request_payload_hash=payload_hash, document_pk=document_pk,
            reviewed_version_id=reviewed.id, resulting_version_id=resulting.id,
            decision=decision, reason=reason,
            reviewer_principal_id=reviewer_principal_id, decided_at=now,
        )
        session.add(decision_row)
        document.current_version_id = resulting.id
        await session.execute(
            update(DerivedSet)
            .where(
                DerivedSet.organization_id == organization_id,
                DerivedSet.document_pk == document_pk,
                DerivedSet.is_active.is_(True),
            )
            .values(is_active=False)
        )
        await _append_review_audit(
            session, organization_id=organization_id,
            reviewer_principal_id=reviewer_principal_id,
            request_id=request_id, correlation_id=correlation_id,
            document_pk=document_pk, resulting_version_id=resulting.id,
            decision_id=decision_row.id, reviewed_version_id=reviewed.id,
            decision=decision, occurred_at=now,
        )

    return ReviewResult(decision_row, resulting, False)

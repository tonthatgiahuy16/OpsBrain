"""Negative tests for review capability enforcement.

These tests exercise the service boundary an attacker reaches first: calling
``submit_review_decision`` with a principal that must not be able to approve.
A denied attempt must leave no version, decision or success audit behind.
"""

from pathlib import Path
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from src.authorization.capabilities import (
    CapabilityDenied,
    DocumentNotVisible,
    principal_has_capability,
)
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
    PrincipalRoleMembership,
    Role,
    RoleCapability,
)
from tests.test_document_review_decisions import _seed


RESTRICTED = Path(__file__).parent / "fixtures" / "restricted-allowed.md"


async def _reviewer_role(session: AsyncSession, *, organization_id):
    return await session.scalar(
        select(Role)
        .join(
            RoleCapability,
            (RoleCapability.organization_id == Role.organization_id)
            & (RoleCapability.role_id == Role.id),
        )
        .where(
            Role.organization_id == organization_id,
            RoleCapability.capability_key == "document:approve",
        )
    )


async def _assert_nothing_written(session: AsyncSession, *, document_pk) -> None:
    assert await session.scalar(
        select(func.count()).select_from(DocumentReviewDecision)
        .where(DocumentReviewDecision.document_pk == document_pk)
    ) == 0
    assert await session.scalar(
        select(func.count()).select_from(DocumentVersion)
        .where(DocumentVersion.document_pk == document_pk)
    ) == 1
    assert await session.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.document_pk == document_pk,
            AuditEvent.event_type.in_(
                ("document.review.approved", "document.review.rejected")
            ),
        )
    ) == 0


def _review_call(*, organization_id, ingested, reviewer_id, configuration):
    return dict(
        organization_id=organization_id,
        document_pk=ingested.document_pk,
        expected_version_id=ingested.document_version_id,
        reviewer_principal_id=reviewer_id,
        decision="approve",
        reason="Attempt approval without authorization",
        request_id=f"denied-{uuid4()}",
        idempotency_key=f"denied-key-{uuid4()}",
        selected_configuration_id=configuration.id,
    )


@pytest.mark.asyncio
async def test_review_without_capability_is_denied_without_writes(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, configuration = await _seed(session)
            role = await _reviewer_role(session, organization_id=organization_id)
            await session.execute(
                delete(RoleCapability).where(
                    RoleCapability.organization_id == organization_id,
                    RoleCapability.role_id == role.id,
                )
            )
            await session.flush()

            call = _review_call(
                organization_id=organization_id, ingested=ingested,
                reviewer_id=reviewer_id, configuration=configuration,
            )
            with pytest.raises(CapabilityDenied, match="document:approve"):
                await submit_review_decision(session, **call)
            await _assert_nothing_written(session, document_pk=ingested.document_pk)
            denied_event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.request_id == call["request_id"],
                )
            )
            assert denied_event is not None
            assert denied_event.event_type == "document.review.denied"
            assert denied_event.outcome == "denied"
            assert denied_event.reason_code == "approval_forbidden"
            assert denied_event.document_pk == ingested.document_pk
            assert denied_event.details["required_capability"] == "document:approve"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_with_only_a_different_capability_is_denied(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, configuration = await _seed(session)
            role = await _reviewer_role(session, organization_id=organization_id)
            await session.execute(
                delete(RoleCapability).where(
                    RoleCapability.organization_id == organization_id,
                    RoleCapability.role_id == role.id,
                )
            )
            session.add(RoleCapability(
                organization_id=organization_id, role_id=role.id,
                capability_key="index:rebuild", status="active",
                granted_by_principal_id=reviewer_id,
                granted_at=datetime.now(timezone.utc),
            ))
            await session.flush()

            call = _review_call(
                organization_id=organization_id, ingested=ingested,
                reviewer_id=reviewer_id, configuration=configuration,
            )
            with pytest.raises(CapabilityDenied, match="document:approve"):
                await submit_review_decision(session, **call)
            await _assert_nothing_written(session, document_pk=ingested.document_pk)
            denied_event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.request_id == call["request_id"],
                )
            )
            assert denied_event is not None
            assert denied_event.event_type == "document.review.denied"
            assert denied_event.outcome == "denied"
            assert denied_event.reason_code == "approval_forbidden"
            assert denied_event.document_pk == ingested.document_pk
            assert denied_event.details["required_capability"] == "document:approve"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_denied_when_role_is_disabled(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, configuration = await _seed(session)
            role = await _reviewer_role(session, organization_id=organization_id)
            role.status = "disabled"
            await session.flush()

            call = _review_call(
                organization_id=organization_id, ingested=ingested,
                reviewer_id=reviewer_id, configuration=configuration,
            )
            with pytest.raises(CapabilityDenied, match="document:approve"):
                await submit_review_decision(session, **call)
            await _assert_nothing_written(session, document_pk=ingested.document_pk)
            denied_event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.request_id == call["request_id"],
                )
            )
            assert denied_event is not None
            assert denied_event.event_type == "document.review.denied"
            assert denied_event.outcome == "denied"
            assert denied_event.reason_code == "approval_forbidden"
            assert denied_event.document_pk == ingested.document_pk
            assert denied_event.details["required_capability"] == "document:approve"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_denied_when_membership_is_revoked(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, configuration = await _seed(session)
            membership = await session.scalar(
                select(PrincipalRoleMembership).where(
                    PrincipalRoleMembership.organization_id == organization_id,
                    PrincipalRoleMembership.principal_id == reviewer_id,
                    PrincipalRoleMembership.status == "active",
                )
            )
            membership.status = "revoked"
            await session.flush()

            call = _review_call(
                organization_id=organization_id, ingested=ingested,
                reviewer_id=reviewer_id, configuration=configuration,
            )
            with pytest.raises(CapabilityDenied, match="document:approve"):
                await submit_review_decision(session, **call)
            await _assert_nothing_written(session, document_pk=ingested.document_pk)
            denied_event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.request_id == call["request_id"],
                )
            )
            assert denied_event is not None
            assert denied_event.event_type == "document.review.denied"
            assert denied_event.outcome == "denied"
            assert denied_event.reason_code == "approval_forbidden"
            assert denied_event.document_pk == ingested.document_pk
            assert denied_event.details["required_capability"] == "document:approve"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_denied_when_capability_exists_but_document_is_not_visible(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, author_id, reviewer_id, _, configuration = await _seed(session)
            session.add(Role(
                id=uuid4(), organization_id=organization_id,
                role_key="incident-reader", display_name="Incident reader",
                status="active",
            ))
            await session.flush()
            document = validate_markdown_document(
                RESTRICTED.read_text(encoding="utf-8"), source_uri=RESTRICTED.as_uri(),
            )
            ingested = await ingest_validated_document(
                session, organization_id=organization_id,
                actor_principal_id=author_id, document=document,
            )

            call = _review_call(
                organization_id=organization_id, ingested=ingested,
                reviewer_id=reviewer_id, configuration=configuration,
            )
            with pytest.raises(DocumentNotVisible):
                await submit_review_decision(session, **call)
            await _assert_nothing_written(session, document_pk=ingested.document_pk)
            denied_event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.request_id == call["request_id"],
                )
            )
            assert denied_event is not None
            assert denied_event.event_type == "document.review.denied"
            assert denied_event.outcome == "denied"
            assert denied_event.reason_code == "document_not_found"
            assert denied_event.document_pk is None
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_application_role_can_read_but_cannot_grant_capabilities(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, _, _, _ = await _seed(session)
            role = await _reviewer_role(session, organization_id=organization_id)
            await connection.execute(text("SET LOCAL ROLE opsbrain_app"))
            assert await connection.scalar(text("SELECT current_user")) == "opsbrain_app"
            assert await connection.scalar(text(
                "SELECT has_table_privilege(current_user, 'role_capabilities', 'SELECT')"
            )) is True
            for privilege in ("INSERT", "UPDATE", "DELETE"):
                assert await connection.scalar(text(
                    "SELECT has_table_privilege(current_user, "
                    f"'role_capabilities', '{privilege}')"
                )) is False

            with pytest.raises(DBAPIError, match="permission denied"):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "INSERT INTO role_capabilities "
                            "(id, organization_id, role_id, capability_key, status, "
                            "granted_by_principal_id, granted_at) "
                            "VALUES (:id, :organization_id, :role_id, 'query:execute', "
                            "'active', :granted_by, now())"
                        ),
                        {
                            "id": uuid4(), "organization_id": organization_id,
                            "role_id": role.id, "granted_by": uuid4(),
                        },
                    )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_denied_when_principal_is_disabled(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, configuration = await _seed(session)
            reviewer = await session.scalar(
                select(Principal).where(Principal.id == reviewer_id)
            )
            reviewer.status = "disabled"
            await session.flush()

            assert await principal_has_capability(
                session,
                organization_id=organization_id,
                principal_id=reviewer_id,
                capability="document:approve",
            ) is False
            call = _review_call(
                organization_id=organization_id, ingested=ingested,
                reviewer_id=reviewer_id, configuration=configuration,
            )
            with pytest.raises(CapabilityDenied, match="document:approve"):
                await submit_review_decision(session, **call)
            await _assert_nothing_written(session, document_pk=ingested.document_pk)
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_review_denied_when_document_is_soft_deleted(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, configuration = await _seed(session)
            document = await session.scalar(
                select(Document).where(Document.id == ingested.document_pk)
            )
            document.record_state = "deleted"
            document.deleted_at = datetime.now(timezone.utc)
            document.deleted_by_principal_id = reviewer_id
            await session.flush()

            call = _review_call(
                organization_id=organization_id, ingested=ingested,
                reviewer_id=reviewer_id, configuration=configuration,
            )
            with pytest.raises(DocumentNotVisible):
                await submit_review_decision(session, **call)
            await _assert_nothing_written(session, document_pk=ingested.document_pk)
            denied_event = await session.scalar(
                select(AuditEvent).where(
                    AuditEvent.organization_id == organization_id,
                    AuditEvent.request_id == call["request_id"],
                )
            )
            assert denied_event is not None
            assert denied_event.event_type == "document.review.denied"
            assert denied_event.reason_code == "document_not_found"
            assert denied_event.document_pk is None
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_cross_organization_capability_grant_does_not_apply(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, reviewer_id, ingested, configuration = await _seed(session)
            role = await _reviewer_role(session, organization_id=organization_id)
            await session.execute(
                delete(RoleCapability).where(
                    RoleCapability.organization_id == organization_id,
                    RoleCapability.role_id == role.id,
                )
            )
            other_org_id = uuid4()
            other_actor_id = uuid4()
            other_role_id = uuid4()
            session.add_all([
                Organization(
                    id=other_org_id, organization_key=f"other-cap-{other_org_id}",
                    display_name="Other cap org", status="active",
                ),
                Principal(
                    id=other_actor_id, organization_id=other_org_id,
                    principal_key=f"other-cap-actor-{other_actor_id}",
                    principal_kind="user", display_name="Other actor", status="active",
                ),
                Role(
                    id=other_role_id, organization_id=other_org_id,
                    role_key="other-approver", display_name="Other approver",
                    status="active",
                ),
            ])
            await session.flush()
            session.add(RoleCapability(
                organization_id=other_org_id, role_id=other_role_id,
                capability_key="document:approve", status="active",
                granted_by_principal_id=other_actor_id,
                granted_at=datetime.now(timezone.utc),
            ))
            await session.flush()

            assert await principal_has_capability(
                session,
                organization_id=organization_id,
                principal_id=reviewer_id,
                capability="document:approve",
            ) is False
            with pytest.raises(CapabilityDenied, match="document:approve"):
                await submit_review_decision(session, **_review_call(
                    organization_id=organization_id, ingested=ingested,
                    reviewer_id=reviewer_id, configuration=configuration,
                ))
        finally:
            await session.close()
            await transaction.rollback()

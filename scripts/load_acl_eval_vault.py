"""Idempotently seed the isolated ACL-sensitive evaluation organization.

The gold dataset ``docs/evaluation/acl_sensitive_gold_questions_v0.jsonl`` needs
principals, role/team memberships and three restricted fixtures that do not
exist in the sample-vault organization. Seeding them into a dedicated
``acl-eval-org`` keeps the 12-note payment-service corpus untouched and stops
unrelated organization-scoped notes from reaching the three abstention cases.

Connection roles:

* identity setup (organization, principals, teams, roles, membership,
  capabilities, embedding configuration) uses the migration/owner DSN. The
  worker role is read-only on ``teams`` and ``principal_team_memberships``
  (migration ``b3e7c9a1d5f2`` grants it ``SELECT`` for ACL resolution), so it
  cannot create those rows, and bootstrap identity writes stay off the
  ingestion path.
* document ingestion and review approval use the worker DSN, matching
  ``load_sample_vault.py``. Approval queues reindex work; run
  ``scripts/run_reindex_workers.py`` afterwards to build chunks and embeddings.
  Embeddings are never fabricated when Ollama is offline.

Opaque tokens are local development values, not secrets. They are written to a
temporary directory outside the repository for the evaluator flags.
"""

import argparse
import asyncio
import json
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from src.config import get_settings
from src.database import WorkerAsyncSessionLocal
from src.ingestion.chunking import CHUNKING_VERSION
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.ingestion.review import submit_review_decision
from src.models.storage import (
    DerivedConfiguration,
    DocumentVersion,
    Organization,
    Principal,
    PrincipalRoleMembership,
    PrincipalTeamMembership,
    Role,
    RoleCapability,
    Team,
)


ORGANIZATION_KEY = "acl-eval-org"
FIXTURE_ROOT = Path(__file__).parents[1] / "tests" / "fixtures"
DOCUMENTS = ("restricted-allowed", "restricted-blocked", "restricted-team")

# principal_role in the gold dataset -> (principal_key, opaque token)
TOKEN_PRINCIPALS = {
    "incident-reader": ("acl-incident-reader", "acl-eval-incident-reader"),
    "incident-reader (revoked)": (
        "acl-incident-reader-revoked",
        "acl-eval-incident-reader-revoked",
    ),
    "incident-response-member": (
        "acl-incident-response-member",
        "acl-eval-incident-response-member",
    ),
    "risk-platform-member": (
        "acl-risk-platform-member",
        "acl-eval-risk-platform-member",
    ),
}


async def _organization(session) -> Organization:
    organization = await session.scalar(
        select(Organization).where(Organization.organization_key == ORGANIZATION_KEY)
    )
    if organization is None:
        organization = Organization(
            id=uuid4(),
            organization_key=ORGANIZATION_KEY,
            display_name="ACL Evaluation Organization",
            status="active",
        )
        session.add(organization)
        await session.flush()
    return organization


async def _principal(session, *, organization_id: UUID, key: str) -> Principal:
    stored = await session.scalar(
        select(Principal).where(
            Principal.organization_id == organization_id,
            Principal.principal_key == key,
        )
    )
    if stored is None:
        stored = Principal(
            id=uuid4(),
            organization_id=organization_id,
            principal_key=key,
            principal_kind="user",
            display_name=key,
            status="active",
        )
        session.add(stored)
        await session.flush()
    return stored


async def _team(session, *, organization_id: UUID, key: str) -> Team:
    stored = await session.scalar(
        select(Team).where(
            Team.organization_id == organization_id, Team.team_key == key
        )
    )
    if stored is None:
        stored = Team(
            id=uuid4(),
            organization_id=organization_id,
            team_key=key,
            display_name=key,
            status="active",
        )
        session.add(stored)
        await session.flush()
    return stored


async def _role(session, *, organization_id: UUID, key: str) -> Role:
    stored = await session.scalar(
        select(Role).where(
            Role.organization_id == organization_id, Role.role_key == key
        )
    )
    if stored is None:
        stored = Role(
            id=uuid4(),
            organization_id=organization_id,
            role_key=key,
            display_name=key,
            status="active",
        )
        session.add(stored)
        await session.flush()
    return stored


async def _role_membership(
    session, *, organization_id: UUID, principal_id: UUID, role_id: UUID,
    status: str,
) -> None:
    stored = await session.scalar(
        select(PrincipalRoleMembership).where(
            PrincipalRoleMembership.organization_id == organization_id,
            PrincipalRoleMembership.principal_id == principal_id,
            PrincipalRoleMembership.role_id == role_id,
        )
    )
    if stored is None:
        session.add(
            PrincipalRoleMembership(
                organization_id=organization_id,
                principal_id=principal_id,
                role_id=role_id,
                status=status,
            )
        )
    elif stored.status != status:
        stored.status = status
    await session.flush()


async def _team_membership(
    session, *, organization_id: UUID, principal_id: UUID, team_id: UUID,
    status: str,
) -> None:
    stored = await session.scalar(
        select(PrincipalTeamMembership).where(
            PrincipalTeamMembership.organization_id == organization_id,
            PrincipalTeamMembership.principal_id == principal_id,
            PrincipalTeamMembership.team_id == team_id,
        )
    )
    if stored is None:
        session.add(
            PrincipalTeamMembership(
                organization_id=organization_id,
                principal_id=principal_id,
                team_id=team_id,
                status=status,
            )
        )
    elif stored.status != status:
        stored.status = status
    await session.flush()


async def _grant(
    session, *, organization_id: UUID, role_id: UUID, capability_key: str,
    granted_by_principal_id: UUID,
) -> None:
    stored = await session.scalar(
        select(RoleCapability).where(
            RoleCapability.organization_id == organization_id,
            RoleCapability.role_id == role_id,
            RoleCapability.capability_key == capability_key,
        )
    )
    if stored is None:
        session.add(
            RoleCapability(
                organization_id=organization_id,
                role_id=role_id,
                capability_key=capability_key,
                status="active",
                granted_by_principal_id=granted_by_principal_id,
                granted_at=datetime.now(timezone.utc),
            )
        )
        await session.flush()
    elif stored.status != "active":
        stored.status = "active"
        stored.revoked_at = None
        stored.revoked_by_principal_id = None
        await session.flush()


async def _configuration(
    session, *, organization_id: UUID, principal_id: UUID, dimensions: int,
) -> DerivedConfiguration:
    key = "acl-eval-nomic-embed-text-v1"
    configuration = await session.scalar(
        select(DerivedConfiguration).where(
            DerivedConfiguration.organization_id == organization_id,
            DerivedConfiguration.configuration_key == key,
        )
    )
    if configuration is None:
        payload = {"max_characters": 1200}
        configuration = DerivedConfiguration(
            id=uuid4(),
            organization_id=organization_id,
            configuration_key=key,
            chunking_version=CHUNKING_VERSION,
            embedding_model="nomic-embed-text",
            embedding_dimensions=dimensions,
            configuration_payload=payload,
            configuration_hash=sha256(
                repr(sorted(payload.items())).encode()
            ).hexdigest(),
            status="active",
            created_by_principal_id=principal_id,
        )
        session.add(configuration)
        await session.flush()
    elif (
        configuration.embedding_model != "nomic-embed-text"
        or configuration.embedding_dimensions != dimensions
    ):
        raise ValueError(
            "acl-eval configuration conflicts with requested embedding "
            "model or dimensions"
        )
    return configuration


async def _seed_identities(*, dimensions: int) -> dict[str, UUID]:
    """Create organization, subjects, memberships, capabilities, configuration."""
    engine = create_async_engine(get_settings().MIGRATION_DATABASE_URL)
    try:
        async with AsyncSession(engine, expire_on_commit=False) as session:
            organization = await _organization(session)
            author = await _principal(
                session, organization_id=organization.id, key="acl-eval-author"
            )
            reviewer = await _principal(
                session, organization_id=organization.id, key="acl-eval-reviewer"
            )

            incident_reader = await _role(
                session, organization_id=organization.id, key="incident-reader"
            )
            payments_admin = await _role(
                session, organization_id=organization.id, key="payments-admin"
            )
            query_role = await _role(
                session, organization_id=organization.id, key="acl-eval-query"
            )
            reviewer_role = await _role(
                session, organization_id=organization.id, key="acl-eval-reviewer"
            )
            await _grant(
                session, organization_id=organization.id, role_id=query_role.id,
                capability_key="query:execute",
                granted_by_principal_id=author.id,
            )
            await _grant(
                session, organization_id=organization.id,
                role_id=reviewer_role.id, capability_key="document:approve",
                granted_by_principal_id=author.id,
            )
            await _grant(
                session, organization_id=organization.id,
                role_id=reviewer_role.id, capability_key="query:execute",
                granted_by_principal_id=author.id,
            )
            await _role_membership(
                session, organization_id=organization.id,
                principal_id=reviewer.id, role_id=reviewer_role.id,
                status="active",
            )

            incident_response = await _team(
                session, organization_id=organization.id, key="incident-response"
            )
            risk_platform = await _team(
                session, organization_id=organization.id, key="risk-platform"
            )

            # Approval re-checks the reviewer's readable set, so the reviewer
            # needs an explicit grant on every restricted fixture.
            await _role_membership(
                session, organization_id=organization.id,
                principal_id=reviewer.id, role_id=incident_reader.id,
                status="active",
            )
            await _role_membership(
                session, organization_id=organization.id,
                principal_id=reviewer.id, role_id=payments_admin.id,
                status="active",
            )
            await _team_membership(
                session, organization_id=organization.id,
                principal_id=reviewer.id, team_id=incident_response.id,
                status="active",
            )

            principals: dict[str, Principal] = {}
            for principal_key, _token in TOKEN_PRINCIPALS.values():
                principals[principal_key] = await _principal(
                    session, organization_id=organization.id, key=principal_key
                )
                await _role_membership(
                    session, organization_id=organization.id,
                    principal_id=principals[principal_key].id,
                    role_id=query_role.id, status="active",
                )

            # GQ-ACL-001: an active role membership grants restricted-allowed.
            await _role_membership(
                session, organization_id=organization.id,
                principal_id=principals["acl-incident-reader"].id,
                role_id=incident_reader.id, status="active",
            )
            # GQ-ACL-003: same role, but the membership itself is revoked.
            await _role_membership(
                session, organization_id=organization.id,
                principal_id=principals["acl-incident-reader-revoked"].id,
                role_id=incident_reader.id, status="revoked",
            )
            # GQ-ACL-004: team membership grants restricted-team.
            await _team_membership(
                session, organization_id=organization.id,
                principal_id=principals["acl-incident-response-member"].id,
                team_id=incident_response.id, status="active",
            )
            # GQ-ACL-005: a different team must not inherit the grant.
            await _team_membership(
                session, organization_id=organization.id,
                principal_id=principals["acl-risk-platform-member"].id,
                team_id=risk_platform.id, status="active",
            )

            configuration = await _configuration(
                session, organization_id=organization.id,
                principal_id=author.id, dimensions=dimensions,
            )
            await session.commit()

            seeded = {
                "organization_id": organization.id,
                "author_id": author.id,
                "reviewer_id": reviewer.id,
                "configuration_id": configuration.id,
            }
            seeded.update(
                {key: principal.id for key, principal in principals.items()}
            )
            return seeded
    finally:
        await engine.dispose()


async def _seed_documents(*, seeded: dict[str, UUID]) -> int:
    """Ingest and approve the three fixtures through the worker connection."""
    approved = 0
    async with WorkerAsyncSessionLocal() as session:
        for document_id in DOCUMENTS:
            path = FIXTURE_ROOT / f"{document_id}.md"
            parsed = validate_markdown_document(
                path.read_text(encoding="utf-8"),
                source_uri=f"obsidian://acl-eval/{document_id}.md",
            )
            source = parsed.model_copy(
                update={
                    "frontmatter": parsed.frontmatter.model_copy(
                        update={"review_state": "in_review"}
                    )
                }
            )
            ingested = await ingest_validated_document(
                session,
                organization_id=seeded["organization_id"],
                actor_principal_id=seeded["author_id"],
                document=source,
            )
            version = await session.scalar(
                select(DocumentVersion).where(
                    DocumentVersion.organization_id == seeded["organization_id"],
                    DocumentVersion.id == ingested.document_version_id,
                )
            )
            if version is None:
                raise RuntimeError(f"ingested version missing for {document_id}")
            if version.review_state != "approved":
                await submit_review_decision(
                    session,
                    organization_id=seeded["organization_id"],
                    document_pk=ingested.document_pk,
                    expected_version_id=ingested.document_version_id,
                    reviewer_principal_id=seeded["reviewer_id"],
                    decision="approve",
                    reason="Bootstrap ACL-sensitive evaluation fixtures",
                    request_id=f"acl-eval:{document_id}",
                    idempotency_key=f"acl-eval:{document_id}:{ingested.content_hash}",
                    selected_configuration_id=seeded["configuration_id"],
                )
            approved += 1
        await session.commit()
    return approved


async def load_acl_eval_vault(*, dimensions: int, token_dir: Path) -> dict[str, str]:
    """Seed the ACL evaluation organization and write local token files."""
    seeded = await _seed_identities(dimensions=dimensions)
    approved = await _seed_documents(seeded=seeded)

    token_dir.mkdir(parents=True, exist_ok=True)
    tokens_by_role: dict[str, str] = {}
    token_map: dict[str, str] = {}
    for principal_role, (principal_key, token) in TOKEN_PRINCIPALS.items():
        tokens_by_role[principal_role] = token
        token_map[token] = str(seeded[principal_key])
    (token_dir / "tokens.json").write_text(
        json.dumps(tokens_by_role, indent=2) + "\n", encoding="utf-8"
    )
    (token_dir / "token_map.json").write_text(
        json.dumps(token_map) + "\n", encoding="utf-8"
    )
    print(
        f"organization={ORGANIZATION_KEY} documents={approved} "
        f"tokens_dir={token_dir}"
    )
    return token_map


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dimensions", type=int, default=768)
    parser.add_argument(
        "--token-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "opsbrain-acl-eval",
    )
    args = parser.parse_args()
    if args.dimensions < 1:
        parser.error("--dimensions must be positive")
    asyncio.run(
        load_acl_eval_vault(
            dimensions=args.dimensions, token_dir=args.token_dir
        )
    )


if __name__ == "__main__":
    main()

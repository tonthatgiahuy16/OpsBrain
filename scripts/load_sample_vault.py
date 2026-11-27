"""Idempotently ingest and approve the synthetic sample vault.

This is a local bootstrap utility. It writes source documents and queues their
normal reindex work; it does not fabricate embeddings when Ollama is offline.
"""

import argparse
import asyncio
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from src.database import WorkerAsyncSessionLocal
from src.ingestion.chunking import CHUNKING_VERSION
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.ingestion.review import submit_review_decision
from src.models.storage import (
    DerivedConfiguration,
    Organization,
    Principal,
    PrincipalRoleMembership,
    Role,
    RoleCapability,
)


VAULT_ROOT = Path(__file__).parents[1] / "data" / "sample-vault"


async def _organization_and_principals(session):
    organization = await session.scalar(select(Organization).where(
        Organization.organization_key == "sample-vault"
    ))
    if organization is None:
        organization = Organization(
            id=uuid4(), organization_key="sample-vault",
            display_name="Synthetic Sample Vault", status="active",
        )
        session.add(organization)
        await session.flush()

    async def principal(key: str) -> Principal:
        stored = await session.scalar(select(Principal).where(
            Principal.organization_id == organization.id,
            Principal.principal_key == key,
        ))
        if stored is None:
            stored = Principal(
                id=uuid4(), organization_id=organization.id,
                principal_key=key, principal_kind="user", display_name=key,
                status="active",
            )
            session.add(stored)
            await session.flush()
        return stored

    return organization, await principal("sample-vault-author"), await principal(
        "sample-vault-reviewer"
    )


async def _grant_reviewer_capability(
    session, *, organization_id: UUID, reviewer_id: UUID, granted_by_principal_id: UUID,
) -> None:
    role = await session.scalar(select(Role).where(
        Role.organization_id == organization_id,
        Role.role_key == "sample-vault-reviewer",
    ))
    if role is None:
        role = Role(
            id=uuid4(), organization_id=organization_id,
            role_key="sample-vault-reviewer", display_name="Sample vault reviewer",
            status="active",
        )
        session.add(role)
        await session.flush()
    membership = await session.scalar(select(PrincipalRoleMembership).where(
        PrincipalRoleMembership.organization_id == organization_id,
        PrincipalRoleMembership.principal_id == reviewer_id,
        PrincipalRoleMembership.role_id == role.id,
    ))
    if membership is None:
        session.add(PrincipalRoleMembership(
            organization_id=organization_id, principal_id=reviewer_id,
            role_id=role.id, status="active",
        ))
    elif membership.status != "active":
        membership.status = "active"
    for capability_key in ("document:approve", "query:execute"):
        capability = await session.scalar(select(RoleCapability).where(
            RoleCapability.organization_id == organization_id,
            RoleCapability.role_id == role.id,
            RoleCapability.capability_key == capability_key,
        ))
        if capability is None:
            session.add(RoleCapability(
                organization_id=organization_id, role_id=role.id,
                capability_key=capability_key, status="active",
                granted_by_principal_id=granted_by_principal_id,
                granted_at=datetime.now(timezone.utc),
            ))
        elif capability.status != "active":
            capability.status = "active"
            capability.revoked_at = None
            capability.revoked_by_principal_id = None
    await session.flush()


async def _configuration(
    session,
    *,
    organization_id: UUID,
    principal_id: UUID,
    dimensions: int,
    full_text_configuration: str | None = None,
):
    key = (
        "sample-vault-nomic-embed-text-ft-v1"
        if full_text_configuration is not None
        else "sample-vault-nomic-embed-text-v1"
    )
    configuration = await session.scalar(select(DerivedConfiguration).where(
        DerivedConfiguration.organization_id == organization_id,
        DerivedConfiguration.configuration_key == key,
    ))
    if configuration is None:
        payload = {"max_characters": 1200}
        configuration = DerivedConfiguration(
            id=uuid4(), organization_id=organization_id, configuration_key=key,
            chunking_version=CHUNKING_VERSION, embedding_model="nomic-embed-text",
            embedding_dimensions=dimensions,
            full_text_configuration=full_text_configuration,
            configuration_payload=payload,
            configuration_hash=sha256(repr(sorted(payload.items())).encode()).hexdigest(),
            status="active", created_by_principal_id=principal_id,
        )
        session.add(configuration)
        await session.flush()
    elif (
        configuration.embedding_model != "nomic-embed-text"
        or configuration.embedding_dimensions != dimensions
        or configuration.full_text_configuration != full_text_configuration
    ):
        raise ValueError(
            "sample-vault configuration conflicts with requested embedding model, dimensions, or full_text_configuration"
        )
    return configuration


async def load_sample_vault(
    *, dimensions: int, full_text_configuration: str | None = None,
) -> int:
    notes = sorted(VAULT_ROOT.rglob("*.md"))
    if len(notes) != 12:
        raise RuntimeError(f"expected 12 sample notes, found {len(notes)}")
    async with WorkerAsyncSessionLocal() as session:
        organization, author, reviewer = await _organization_and_principals(session)
        await _grant_reviewer_capability(
            session, organization_id=organization.id, reviewer_id=reviewer.id,
            granted_by_principal_id=author.id,
        )
        configuration = await _configuration(
            session, organization_id=organization.id, principal_id=author.id,
            dimensions=dimensions,
            full_text_configuration=full_text_configuration,
        )
        queued = 0
        for path in notes:
            relative_path = path.relative_to(VAULT_ROOT).as_posix()
            parsed = validate_markdown_document(
                path.read_text(encoding="utf-8"),
                source_uri=f"obsidian://sample-vault/{relative_path}",
            )
            source = parsed.model_copy(update={
                "frontmatter": parsed.frontmatter.model_copy(update={"review_state": "in_review"})
            })
            ingested = await ingest_validated_document(
                session, organization_id=organization.id, actor_principal_id=author.id,
                document=source,
            )
            result = await submit_review_decision(
                session, organization_id=organization.id, document_pk=ingested.document_pk,
                expected_version_id=ingested.document_version_id,
                reviewer_principal_id=reviewer.id, decision="approve",
                reason="Bootstrap synthetic sample vault",
                request_id=f"sample-vault:{path.relative_to(VAULT_ROOT).as_posix()}",
                idempotency_key=f"sample-vault:{ingested.content_hash}",
                selected_configuration_id=configuration.id,
            )
            queued += int(result.reindex_job_id is not None)
        await session.commit()
    print(f"ingested={len(notes)} approved_or_replayed={len(notes)} reindex_jobs={queued}")
    return len(notes)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dimensions", type=int, default=768)
    parser.add_argument(
        "--full-text-configuration",
        choices=("english",),
        default=None,
    )
    args = parser.parse_args()
    if args.dimensions < 1:
        parser.error("--dimensions must be positive")
    asyncio.run(
        load_sample_vault(
            dimensions=args.dimensions,
            full_text_configuration=args.full_text_configuration,
        )
    )


if __name__ == "__main__":
    main()

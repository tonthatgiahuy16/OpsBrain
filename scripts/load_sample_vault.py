"""Idempotently ingest and approve the synthetic sample vault.

This is a local bootstrap utility. It writes source documents and queues their
normal reindex work; it does not fabricate embeddings when Ollama is offline.
"""

import argparse
import asyncio
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from src.database import AsyncSessionLocal
from src.ingestion.chunking import CHUNKING_VERSION
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.ingestion.review import submit_review_decision
from src.models.storage import DerivedConfiguration, Organization, Principal


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


async def _configuration(session, *, organization_id: UUID, principal_id: UUID, dimensions: int):
    key = "sample-vault-nomic-embed-text-v1"
    configuration = await session.scalar(select(DerivedConfiguration).where(
        DerivedConfiguration.organization_id == organization_id,
        DerivedConfiguration.configuration_key == key,
    ))
    if configuration is None:
        payload = {"max_characters": 1200}
        configuration = DerivedConfiguration(
            id=uuid4(), organization_id=organization_id, configuration_key=key,
            chunking_version=CHUNKING_VERSION, embedding_model="nomic-embed-text",
            embedding_dimensions=dimensions, configuration_payload=payload,
            configuration_hash=sha256(repr(sorted(payload.items())).encode()).hexdigest(),
            status="active", created_by_principal_id=principal_id,
        )
        session.add(configuration)
        await session.flush()
    elif (
        configuration.embedding_model != "nomic-embed-text"
        or configuration.embedding_dimensions != dimensions
    ):
        raise ValueError(
            "sample-vault configuration conflicts with requested embedding model or dimensions"
        )
    return configuration


async def load_sample_vault(*, dimensions: int) -> int:
    notes = sorted(VAULT_ROOT.rglob("*.md"))
    if len(notes) != 12:
        raise RuntimeError(f"expected 12 sample notes, found {len(notes)}")
    async with AsyncSessionLocal() as session:
        organization, author, reviewer = await _organization_and_principals(session)
        configuration = await _configuration(
            session, organization_id=organization.id, principal_id=author.id,
            dimensions=dimensions,
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
    args = parser.parse_args()
    if args.dimensions < 1:
        parser.error("--dimensions must be positive")
    asyncio.run(load_sample_vault(dimensions=args.dimensions))


if __name__ == "__main__":
    main()

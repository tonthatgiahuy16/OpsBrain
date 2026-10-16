from dataclasses import dataclass
from typing import Literal
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.canonicalization import (
    CANONICALIZATION_VERSION,
    HASH_ALGORITHM,
    canonical_content_hash,
)
from src.ingestion.frontmatter import ValidatedMarkdownDocument
from src.models.storage import Document, DocumentVersion


@dataclass(frozen=True)
class IngestionResult:
    outcome: Literal["created", "updated", "unchanged"]
    document_pk: UUID
    document_version_id: UUID
    version_number: int
    content_hash: str


async def ingest_validated_document(
    session: AsyncSession,
    *,
    organization_id: UUID,
    actor_principal_id: UUID,
    document: ValidatedMarkdownDocument,
) -> IngestionResult:
    """Persist one validated document inside the caller's transaction."""

    frontmatter = document.frontmatter

    if frontmatter.review_state not in {"draft", "in_review"}:
        raise ValueError(
            "ingestion cannot grant approved or rejected review state"
        )

    content_hash = canonical_content_hash(document)

    stored_document = await session.scalar(
        select(Document)
        .where(
            Document.organization_id == organization_id,
            Document.document_id == frontmatter.document_id,
        )
        .with_for_update()
    )

    if stored_document is not None:
        if stored_document.document_type != frontmatter.document_type:
            raise ValueError(
                "document_type cannot change for an existing document_id"
            )

        if stored_document.current_version_id is None:
            raise RuntimeError(
                "existing document has no current version"
            )

        current_version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.organization_id
                == organization_id,
                DocumentVersion.document_pk
                == stored_document.id,
                DocumentVersion.id
                == stored_document.current_version_id,
            )
        )

        if current_version is None:
            raise RuntimeError(
                "current document version could not be resolved"
            )

        if current_version.content_hash == content_hash:
            return IngestionResult(
                outcome="unchanged",
                document_pk=stored_document.id,
                document_version_id=current_version.id,
                version_number=current_version.version_number,
                content_hash=current_version.content_hash,
            )

        accepted_frontmatter = frontmatter.model_copy(
            update={"review_state": "in_review"},
        )
        accepted_document = ValidatedMarkdownDocument(
            source_uri=document.source_uri,
            frontmatter=accepted_frontmatter,
            body=document.body,
        )
        accepted_content_hash = canonical_content_hash(
            accepted_document
        )

        if accepted_content_hash == current_version.content_hash:
            return IngestionResult(
                outcome="unchanged",
                document_pk=stored_document.id,
                document_version_id=current_version.id,
                version_number=current_version.version_number,
                content_hash=current_version.content_hash,
            )

        change_kind = (
            "content_update"
            if accepted_document.body
            != current_version.markdown_body
            else "metadata_update"
        )

        version_id = uuid4()
        version_number = current_version.version_number + 1

        updated_version = DocumentVersion(
            id=version_id,
            organization_id=organization_id,
            document_pk=stored_document.id,
            parent_version_id=current_version.id,
            version_number=version_number,
            schema_version=accepted_frontmatter.schema_version,
            canonicalization_version=CANONICALIZATION_VERSION,
            hash_algorithm=HASH_ALGORITHM,
            content_hash=accepted_content_hash,
            title=accepted_frontmatter.title,
            owner_team=accepted_frontmatter.owner_team,
            source_uri=accepted_document.source_uri,
            source_refs=list(accepted_frontmatter.source_refs),
            tags=list(accepted_frontmatter.tags),
            markdown_body=accepted_document.body,
            frontmatter_snapshot=(
                accepted_frontmatter.model_dump(mode="json")
            ),
            document_status=accepted_frontmatter.document_status,
            review_state=accepted_frontmatter.review_state,
            provenance_type=accepted_frontmatter.provenance_type,
            access_scope=accepted_frontmatter.access_scope,
            effective_from=accepted_frontmatter.effective_from,
            effective_until=accepted_frontmatter.effective_until,
            change_kind=change_kind,
            created_by_principal_id=actor_principal_id,
        )
        session.add(updated_version)
        await session.flush()

        stored_document.current_source_uri = (
            accepted_document.source_uri
        )
        stored_document.current_version_id = version_id
        await session.flush()

        return IngestionResult(
            outcome="updated",
            document_pk=stored_document.id,
            document_version_id=version_id,
            version_number=version_number,
            content_hash=accepted_content_hash,
        )

    document_pk = uuid4()
    version_id = uuid4()

    stored_document = Document(
        id=document_pk,
        organization_id=organization_id,
        document_id=frontmatter.document_id,
        document_type=frontmatter.document_type,
        current_source_uri=document.source_uri,
        record_state="active",
        current_version_id=None,
    )
    session.add(stored_document)
    await session.flush()

    stored_version = DocumentVersion(
        id=version_id,
        organization_id=organization_id,
        document_pk=document_pk,
        parent_version_id=None,
        version_number=1,
        schema_version=frontmatter.schema_version,
        canonicalization_version=CANONICALIZATION_VERSION,
        hash_algorithm=HASH_ALGORITHM,
        content_hash=content_hash,
        title=frontmatter.title,
        owner_team=frontmatter.owner_team,
        source_uri=document.source_uri,
        source_refs=list(frontmatter.source_refs),
        tags=list(frontmatter.tags),
        markdown_body=document.body,
        frontmatter_snapshot=frontmatter.model_dump(mode="json"),
        document_status=frontmatter.document_status,
        review_state=frontmatter.review_state,
        provenance_type=frontmatter.provenance_type,
        access_scope=frontmatter.access_scope,
        effective_from=frontmatter.effective_from,
        effective_until=frontmatter.effective_until,
        change_kind="create",
        created_by_principal_id=actor_principal_id,
    )
    session.add(stored_version)
    await session.flush()

    stored_document.current_version_id = version_id
    await session.flush()

    return IngestionResult(
        outcome="created",
        document_pk=document_pk,
        document_version_id=version_id,
        version_number=1,
        content_hash=content_hash,
    )

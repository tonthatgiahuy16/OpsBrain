from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.storage import (
    Chunk,
    DerivedConfiguration,
    DerivedSet,
    Document,
    DocumentVersion,
    Organization,
)


async def activate_derived_set(
    session: AsyncSession,
    *,
    organization_id: UUID,
    derived_set_id: UUID,
) -> DerivedSet:
    """Atomically make a ready, current, chunk-only set active.

    The caller owns the surrounding transaction. Document-row locking
    serializes activation with version/lifecycle transitions. ACL evaluation
    remains mandatory at query time and is intentionally not performed here.
    """

    async with session.begin_nested():
        candidate = await session.scalar(
            select(DerivedSet).where(
                DerivedSet.organization_id == organization_id,
                DerivedSet.id == derived_set_id,
            )
        )
        if candidate is None:
            raise ValueError("derived set not found")

        document = await session.scalar(
            select(Document)
            .where(
                Document.organization_id == organization_id,
                Document.id == candidate.document_pk,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if document is None:
            raise ValueError("source document not found")

        organization = await session.scalar(
            select(Organization)
            .where(Organization.id == organization_id)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if organization is None or organization.status != "active":
            raise ValueError("source document is not retrieval-eligible")

        if document.record_state != "active":
            raise ValueError("source document is not retrieval-eligible")
        if document.current_version_id != candidate.document_version_id:
            raise ValueError("derived set source version is not current")

        source_version = await session.scalar(
            select(DocumentVersion).where(
                DocumentVersion.organization_id == organization_id,
                DocumentVersion.document_pk == document.id,
                DocumentVersion.id == document.current_version_id,
            )
        )
        now = datetime.now(timezone.utc)
        if (
            source_version is None
            or source_version.document_status != "active"
            or source_version.review_state != "approved"
            or (
                source_version.effective_from is not None
                and source_version.effective_from > now
            )
            or (
                source_version.effective_until is not None
                and source_version.effective_until <= now
            )
        ):
            raise ValueError("source document is not retrieval-eligible")

        derived_set = await session.scalar(
            select(DerivedSet)
            .where(
                DerivedSet.organization_id == organization_id,
                DerivedSet.id == derived_set_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if derived_set is None:
            raise ValueError("derived set not found")
        if derived_set.status != "ready":
            raise ValueError("derived set is not ready")

        configuration = await session.scalar(
            select(DerivedConfiguration)
            .where(
                DerivedConfiguration.organization_id == organization_id,
                DerivedConfiguration.id == derived_set.configuration_id,
            )
            .with_for_update()
        )
        if configuration is None or configuration.status != "active":
            raise ValueError("derived configuration is not active")

        # This slice supports chunk-only builds. Never expose a set as ready
        # when its recorded configuration requires storage we do not have yet.
        if any(
            value is not None
            for value in (
                configuration.embedding_model,
                configuration.full_text_configuration,
                configuration.relationship_extractor_version,
            )
        ):
            raise ValueError("required derived components are incomplete")

        chunk_count = await session.scalar(
            select(func.count())
            .select_from(Chunk)
            .where(
                Chunk.organization_id == organization_id,
                Chunk.derived_set_id == derived_set.id,
            )
        )
        if not chunk_count:
            raise ValueError("required derived components are incomplete")

        if derived_set.is_active:
            return derived_set

        await session.execute(
            update(DerivedSet)
            .where(
                DerivedSet.organization_id == organization_id,
                DerivedSet.document_pk == document.id,
                DerivedSet.is_active.is_(True),
                DerivedSet.id != derived_set.id,
            )
            .values(is_active=False)
        )
        derived_set.is_active = True
        derived_set.activated_at = now
        await session.flush()
        return derived_set

"""Tests for Milestone 6 full-text build, activation gating, RLS, and worker fencing."""

import os
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.authorization.rls import set_rls_context
from src.ingestion import full_text_build
from src.ingestion.canonicalization import normalize_markdown_body
from src.ingestion.derived_activation import activate_derived_set
from src.ingestion.derived_build import build_chunk_derived_set
from src.ingestion.embedding_build import persist_chunk_embeddings
from src.ingestion.frontmatter import validate_markdown_document
from src.ingestion.lifecycle import ingest_validated_document
from src.ingestion.ollama_embeddings import OllamaEmbeddingClient
from src.ingestion.reindex_worker import (
    MAX_ATTEMPTS,
    RetryableReindexError,
    process_next_chunk_reindex_item,
    process_next_embedding_reindex_item,
    process_next_full_text_reindex_item,
    reclaim_expired_chunk_reindex_item,
)
from src.models.storage import (
    AuditEvent,
    Chunk,
    ChunkFullTextIndex,
    DerivedConfiguration,
    DerivedSet,
    Document,
    DocumentVersion,
    Organization,
    Principal,
    ReindexJob,
    ReindexJobItem,
    ReindexJobItemAttempt,
    Role,
)

SAMPLE_DOC = (
    Path(__file__).parents[1]
    / "data"
    / "sample-vault"
    / "payment-service"
    / "services"
    / "checkout-service.md"
)
RESTRICTED_DOC = Path(__file__).parent / "fixtures" / "restricted-blocked.md"


class _FakeEmbeddingClient(OllamaEmbeddingClient):
    def __init__(self, *, dimensions: int = 4) -> None:
        self._dimensions = dimensions

    async def embed_texts(self, texts):
        return [[1.0] + [0.0] * (self._dimensions - 1) for _ in texts]


def _concurrency_url() -> str:
    url = os.environ.get("OPSBRAIN_CONCURRENCY_DATABASE_URL")
    if not url:
        pytest.skip("Dedicated concurrency test database is not configured")
    assert make_url(url).database == "opsbrain_concurrency_test"
    return url


async def _seed_approved_doc(
    session: AsyncSession,
    *,
    organization_id=None,
    principal_id=None,
    source_path: Path = SAMPLE_DOC,
    embedding_model: str | None = None,
    embedding_dimensions: int | None = None,
    full_text_configuration: str | None = "english",
):
    org_id = organization_id or uuid4()
    actor_id = principal_id or uuid4()
    if organization_id is None:
        session.add(
            Organization(
                id=org_id,
                organization_key=f"ft-org-{org_id}",
                display_name="Full Text Test Org",
                status="active",
            )
        )
        session.add(
            Principal(
                id=actor_id,
                organization_id=org_id,
                principal_key=f"ft-user-{actor_id}",
                principal_kind="user",
                display_name="FT User",
                status="active",
            )
        )
        session.add(
            Role(
                organization_id=org_id,
                role_key="payments-admin",
                display_name="Payments Admin",
                status="active",
            )
        )
        await session.flush()

    validated = validate_markdown_document(
        source_path.read_text(encoding="utf-8"),
        source_uri=source_path.as_uri(),
    )
    ingested = await ingest_validated_document(
        session,
        organization_id=org_id,
        actor_principal_id=actor_id,
        document=validated,
    )
    doc = await session.scalar(
        select(Document).where(
            Document.organization_id == org_id,
            Document.id == ingested.document_pk,
        )
    )
    version = await session.scalar(
        select(DocumentVersion).where(
            DocumentVersion.organization_id == org_id,
            DocumentVersion.id == doc.current_version_id,
        )
    )
    version.review_state = "approved"
    version.document_status = "active"
    await session.flush()

    payload = {"max_characters": 300}
    config = DerivedConfiguration(
        id=uuid4(),
        organization_id=org_id,
        configuration_key=f"ft-cfg-{uuid4()}",
        chunking_version="chunk-v1",
        embedding_model=embedding_model,
        embedding_dimensions=embedding_dimensions,
        full_text_configuration=full_text_configuration,
        configuration_payload=payload,
        configuration_hash=sha256(str(payload).encode("utf-8")).hexdigest(),
        status="active",
        created_by_principal_id=actor_id,
    )
    session.add(config)
    await session.flush()
    return org_id, actor_id, doc, version, config


async def _enqueue_reindex_job(
    session: AsyncSession,
    *,
    organization_id,
    principal_id,
    document_pk,
    version_id,
    configuration_id,
    targets: list[str],
) -> tuple[ReindexJob, ReindexJobItem]:
    job = ReindexJob(
        organization_id=organization_id,
        request_id=f"req-{uuid4()}",
        idempotency_key_hash=sha256(f"idem-{uuid4()}".encode()).hexdigest(),
        request_payload_hash=sha256(f"payload-{uuid4()}".encode()).hexdigest(),
        submitted_by_principal_id=principal_id,
        scope_type="documents",
        targets=targets,
        reason="full_text_test",
        configuration_id=configuration_id,
        requested_document_count=1,
        status="queued",
    )
    session.add(job)
    await session.flush()
    item = ReindexJobItem(
        organization_id=organization_id,
        reindex_job_id=job.id,
        document_pk=document_pk,
        source_document_version_id=version_id,
        processing_status="queued",
    )
    session.add(item)
    await session.flush()
    return job, item


@pytest.mark.asyncio
async def test_persist_chunk_full_text_indexes_is_idempotent_and_immutable(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org_id, _, doc, version, config = await _seed_approved_doc(session)
            derived_set = await build_chunk_derived_set(
                session,
                organization_id=org_id,
                document_pk=doc.id,
                document_version_id=version.id,
                configuration_id=config.id,
            )
            chunks = list(
                (
                    await session.scalars(
                        select(Chunk)
                        .where(
                            Chunk.organization_id == org_id,
                            Chunk.derived_set_id == derived_set.id,
                        )
                        .order_by(Chunk.chunk_index)
                    )
                ).all()
            )
            assert chunks

            first = await full_text_build.persist_chunk_full_text_indexes(
                session,
                organization_id=org_id,
                derived_set_id=derived_set.id,
            )
            replay = await full_text_build.persist_chunk_full_text_indexes(
                session,
                organization_id=org_id,
                derived_set_id=derived_set.id,
            )
            assert [row.id for row in first] == [row.id for row in replay]
            assert len(first) == len(chunks)
            for chunk, row in zip(chunks, first, strict=True):
                expected_hash = sha256(
                    normalize_markdown_body(chunk.content).encode("utf-8")
                ).hexdigest()
                assert row.full_text_configuration == "english"
                assert row.input_hash == expected_hash
                assert row.document_pk == doc.id
                assert row.document_version_id == version.id
                assert row.search_vector

            # DB trigger enforces immutability on existing rows.
            with pytest.raises(DBAPIError, match="chunk full-text indexes are immutable"):
                async with session.begin_nested():
                    await session.execute(
                        text(
                            "UPDATE chunk_full_text_indexes "
                            "SET input_hash = :h WHERE id = :id"
                        ),
                        {"h": "0" * 64, "id": first[0].id},
                    )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_activation_requires_complete_full_text_and_embeddings(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org_id, _, doc, version, config = await _seed_approved_doc(
                session,
                embedding_model="test-embed",
                embedding_dimensions=4,
                full_text_configuration="english",
            )
            derived_set = await build_chunk_derived_set(
                session,
                organization_id=org_id,
                document_pk=doc.id,
                document_version_id=version.id,
                configuration_id=config.id,
            )
            chunks = list(
                (
                    await session.scalars(
                        select(Chunk).where(
                            Chunk.organization_id == org_id,
                            Chunk.derived_set_id == derived_set.id,
                        )
                    )
                ).all()
            )
            await persist_chunk_embeddings(
                session,
                organization_id=org_id,
                derived_set_id=derived_set.id,
                vectors_by_chunk_id={c.id: [1.0, 0.0, 0.0, 0.0] for c in chunks},
            )

            # Embeddings present, FTS missing -> activation must fail.
            with pytest.raises(ValueError, match="required derived components are incomplete"):
                await activate_derived_set(
                    session, organization_id=org_id, derived_set_id=derived_set.id
                )

            # Now persist FTS -> activation succeeds.
            await full_text_build.persist_chunk_full_text_indexes(
                session, organization_id=org_id, derived_set_id=derived_set.id
            )
            activated = await activate_derived_set(
                session, organization_id=org_id, derived_set_id=derived_set.id
            )
            assert activated.is_active is True
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_full_text_rls_and_app_role_permissions(migration_database_engine):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org_id, actor_id, doc_public, ver_public, cfg_public = await _seed_approved_doc(
                session, source_path=SAMPLE_DOC
            )
            _, _, doc_restricted, ver_restricted, cfg_restricted = await _seed_approved_doc(
                session,
                organization_id=org_id,
                principal_id=actor_id,
                source_path=RESTRICTED_DOC,
            )
            ds_public = await build_chunk_derived_set(
                session,
                organization_id=org_id,
                document_pk=doc_public.id,
                document_version_id=ver_public.id,
                configuration_id=cfg_public.id,
            )
            ds_restricted = await build_chunk_derived_set(
                session,
                organization_id=org_id,
                document_pk=doc_restricted.id,
                document_version_id=ver_restricted.id,
                configuration_id=cfg_restricted.id,
            )
            public_rows = await full_text_build.persist_chunk_full_text_indexes(
                session, organization_id=org_id, derived_set_id=ds_public.id
            )
            await full_text_build.persist_chunk_full_text_indexes(
                session, organization_id=org_id, derived_set_id=ds_restricted.id
            )

            # Switch to opsbrain_app inside a savepoint so RLS and role privileges apply.
            async with session.begin_nested():
                await session.execute(text("SET LOCAL ROLE opsbrain_app"))
                assert (
                    await session.scalar(
                        select(func.count()).select_from(ChunkFullTextIndex)
                    )
                    == 0
                )
                await set_rls_context(
                    session, organization_id=org_id, principal_id=actor_id
                )
                visible = list(
                    (
                        await session.scalars(
                            select(ChunkFullTextIndex).where(
                                ChunkFullTextIndex.organization_id == org_id
                            )
                        )
                    ).all()
                )
                assert len(visible) == len(public_rows)
                assert {r.document_pk for r in visible} == {doc_public.id}

                with pytest.raises(ProgrammingError, match="permission denied"):
                    async with session.begin_nested():
                        await session.execute(
                            text("DELETE FROM chunk_full_text_indexes WHERE id = :id"),
                            {"id": public_rows[0].id},
                        )
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_chunks_worker_enqueues_embeddings_and_full_text_and_activates_last(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org_id, actor_id, doc, version, config = await _seed_approved_doc(
                session,
                embedding_model="test-embed",
                embedding_dimensions=4,
                full_text_configuration="english",
            )
            chunk_job, _ = await _enqueue_reindex_job(
                session,
                organization_id=org_id,
                principal_id=actor_id,
                document_pk=doc.id,
                version_id=version.id,
                configuration_id=config.id,
                targets=["chunks"],
            )
            await session.commit()

            assert await process_next_chunk_reindex_item(session) is not None
            ds = await session.scalar(
                select(DerivedSet).where(
                    DerivedSet.organization_id == org_id,
                    DerivedSet.document_pk == doc.id,
                )
            )
            assert ds is not None
            assert ds.is_active is False  # Neither embeddings nor FTS built yet.

            # Run embeddings first -> still not active because FTS is missing.
            assert (
                await process_next_embedding_reindex_item(
                    session, embedding_client=_FakeEmbeddingClient(dimensions=4)
                )
                is not None
            )
            await session.refresh(ds)
            assert ds.is_active is False

            # Run full_text last -> now all required components are complete, so it activates!
            assert await process_next_full_text_reindex_item(session) is not None
            await session.refresh(ds)
            assert ds.is_active is True

            ft_count = await session.scalar(
                select(func.count())
                .select_from(ChunkFullTextIndex)
                .where(
                    ChunkFullTextIndex.organization_id == org_id,
                    ChunkFullTextIndex.derived_set_id == ds.id,
                )
            )
            assert ft_count and ft_count > 0
            await session.refresh(chunk_job)
            assert chunk_job.status == "completed"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_full_text_worker_failure_rolls_back_and_retries_to_terminal(
    migration_database_engine, monkeypatch,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            org_id, actor_id, doc, version, config = await _seed_approved_doc(session)
            derived_set = await build_chunk_derived_set(
                session,
                organization_id=org_id,
                document_pk=doc.id,
                document_version_id=version.id,
                configuration_id=config.id,
            )
            derived_set_id = derived_set.id
            job, item = await _enqueue_reindex_job(
                session,
                organization_id=org_id,
                principal_id=actor_id,
                document_pk=doc.id,
                version_id=version.id,
                configuration_id=config.id,
                targets=["full_text"],
            )
            await session.commit()

            async def flaky_persist(*args, **kwargs):
                raise RetryableReindexError("temporary full-text failure")

            monkeypatch.setattr(
                "src.ingestion.reindex_worker.persist_chunk_full_text_indexes",
                flaky_persist,
            )

            for attempt_num in range(1, MAX_ATTEMPTS + 1):
                if attempt_num > 1:
                    item.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                    await session.commit()
                processed = await process_next_full_text_reindex_item(session)
                assert processed == item.id
                await session.refresh(item)
                await session.refresh(job)
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(ChunkFullTextIndex)
                        .where(ChunkFullTextIndex.derived_set_id == derived_set_id)
                    )
                    == 0
                )
                if attempt_num < MAX_ATTEMPTS:
                    assert item.processing_status == "queued"
                    assert item.next_retry_at is not None
                else:
                    assert item.processing_status == "failed"
                    assert job.status == "failed"
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_stale_full_text_worker_is_fenced_without_writing_rows(monkeypatch):
    url = _concurrency_url()
    engine = create_async_engine(url)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid4()
    try:
        async with session_factory() as setup_session:
            _, actor_id, doc, version, config = await _seed_approved_doc(
                setup_session, organization_id=None, principal_id=None
            )
            org_id = doc.organization_id
            derived_set = await build_chunk_derived_set(
                setup_session,
                organization_id=org_id,
                document_pk=doc.id,
                document_version_id=version.id,
                configuration_id=config.id,
            )
            _, item = await _enqueue_reindex_job(
                setup_session,
                organization_id=org_id,
                principal_id=actor_id,
                document_pk=doc.id,
                version_id=version.id,
                configuration_id=config.id,
                targets=["full_text"],
            )
            item_id = item.id
            await setup_session.commit()

        # Worker A claims attempt 1, then its lease expires and Worker B reclaims + succeeds.
        async with session_factory() as worker_a:
            from src.ingestion import reindex_worker

            claim_a = await reindex_worker.claim_next_full_text_reindex_item(worker_a)
            assert claim_a is not None

        async with session_factory() as worker_b:
            reclaimed = None
            while True:
                candidate = await reclaim_expired_chunk_reindex_item(
                    worker_b, now=claim_a.lease_expires_at + timedelta(seconds=1)
                )
                if candidate is None or candidate == item_id:
                    reclaimed = candidate
                    break
            assert reclaimed == item_id
            await worker_b.execute(
                text("UPDATE reindex_job_items SET next_retry_at = NULL WHERE id = :id"),
                {"id": item_id},
            )
            await worker_b.commit()
            processed = None
            while True:
                candidate = await process_next_full_text_reindex_item(worker_b)
                if candidate is None or candidate == item_id:
                    processed = candidate
                    break
            assert processed == item_id

        # Now stale Worker A tries to claim/lock and finalize; it must be fenced.
        async with session_factory() as stale_a:
            with pytest.raises(reindex_worker._FencedAttempt):
                await reindex_worker._lock_claim(
                    stale_a, claim_a, datetime.now(timezone.utc)
                )
            await stale_a.rollback()

        async with session_factory() as verify:
            item_row = await verify.scalar(
                select(ReindexJobItem).where(ReindexJobItem.id == item_id)
            )
            assert item_row.processing_status == "succeeded"
            assert item_row.outcome == "rebuilt"
            attempts = list(
                (
                    await verify.scalars(
                        select(ReindexJobItemAttempt)
                        .where(ReindexJobItemAttempt.reindex_job_item_id == item_id)
                        .order_by(ReindexJobItemAttempt.attempt_number)
                    )
                ).all()
            )
            assert [a.status for a in attempts] == ["failed", "succeeded"]
            ds = await verify.scalar(
                select(DerivedSet).where(DerivedSet.id == derived_set.id)
            )
            assert ds.is_active is True
    finally:
        await engine.dispose()

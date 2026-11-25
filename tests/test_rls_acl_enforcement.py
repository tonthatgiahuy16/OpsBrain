"""Negative database tests for ACL-aware RLS, independent of API predicates."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.authorization.rls import set_rls_context
from tests.test_query_acl import _seed_acl_documents


@pytest.mark.asyncio
async def test_acl_rls_hides_restricted_versions_chunks_and_embeddings(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed, blocked, viewer, _ = await _seed_acl_documents(session)
            await session.flush()
            await session.close()

            await connection.execute(text("SET LOCAL ROLE opsbrain_app"))
            # Fail closed before the API authentication dependency establishes
            # transaction-local organization/principal context.
            assert await connection.scalar(text("SELECT count(*) FROM document_versions")) == 0
            assert await connection.scalar(text("SELECT count(*) FROM chunks")) == 0
            assert await connection.scalar(text("SELECT count(*) FROM chunk_embeddings")) == 0

            await set_rls_context(
                connection,
                organization_id=allowed["organization_id"],
                principal_id=viewer.id,
            )
            assert await connection.scalar(text("SELECT count(*) FROM documents")) == 1
            assert await connection.scalar(text("SELECT count(*) FROM document_versions")) > 0
            assert await connection.scalar(text("SELECT count(*) FROM chunks")) == len(allowed["chunks"])
            assert await connection.scalar(text("SELECT count(*) FROM chunk_embeddings")) == len(allowed["chunks"])
            assert await connection.scalar(
                text("SELECT count(*) FROM document_versions WHERE document_pk = :document_pk"),
                {"document_pk": blocked["document_pk"]},
            ) == 0
            assert await connection.scalar(
                text("SELECT count(*) FROM documents WHERE id = :document_pk"),
                {"document_pk": blocked["document_pk"]},
            ) == 0
        finally:
            await session.close()
            await transaction.rollback()

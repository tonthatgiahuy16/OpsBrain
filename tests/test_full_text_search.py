import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import uuid4

from src.retrieval.full_text_search import full_text_top_k
from tests.test_vector_search import (
    OTHER_SAMPLE_DOCUMENT,
    SAMPLE_DOCUMENT,
    _seed_embedded_document,
)


@pytest.mark.asyncio
async def test_full_text_search_filters_acl_allowlist_before_rank_and_limit(
    migration_database_engine,
):
    """A denied exact-match document cannot consume the authorized top-k budget."""
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            allowed = await _seed_embedded_document(
                session, source_document=OTHER_SAMPLE_DOCUMENT,
            )
            denied = await _seed_embedded_document(
                session,
                organization_id=allowed["organization_id"],
                source_document=SAMPLE_DOCUMENT,
            )
            unrestricted = await full_text_top_k(
                session,
                organization_id=allowed["organization_id"],
                allowed_document_pks={allowed["document_pk"], denied["document_pk"]},
                question="fraud OR payment",
                full_text_configuration="english",
                top_k=1,
            )
            assert unrestricted
            assert unrestricted[0].document_pk == denied["document_pk"]

            authorized = await full_text_top_k(
                session,
                organization_id=allowed["organization_id"],
                allowed_document_pks={allowed["document_pk"]},
                question="fraud OR payment",
                full_text_configuration="english",
                top_k=10,
            )
            assert authorized
            assert {hit.document_pk for hit in authorized} == {allowed["document_pk"]}
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_full_text_search_rejects_unsupported_configuration():
    with pytest.raises(ValueError, match="unsupported full-text configuration"):
        await full_text_top_k(
            None,
            organization_id=uuid4(),
            allowed_document_pks={uuid4()},
            question="payment",
            full_text_configuration="simple",
            top_k=1,
        )

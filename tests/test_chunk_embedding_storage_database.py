import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import DBAPIError, IntegrityError

from src.models.storage import Chunk, ChunkEmbedding
from tests.test_derived_set_activation import _seed_activation_inputs


@pytest.mark.asyncio
async def test_chunk_embedding_persists_and_enforces_provenance(
    migration_database_engine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, expire_on_commit=False)
        try:
            organization_id, _, _, configuration, derived_set = (
                await _seed_activation_inputs(
                    session, embedding_model="test-embed-model",
                )
            )
            chunk = await session.scalar(select(Chunk).where(
                Chunk.organization_id == organization_id,
                Chunk.derived_set_id == derived_set.id,
            ))
            assert chunk is not None

            embedding = ChunkEmbedding(
                organization_id=organization_id,
                derived_set_id=derived_set.id,
                chunk_id=chunk.id,
                embedding_model=configuration.embedding_model,
                embedding_model_revision=configuration.embedding_model_revision,
                embedding_dimensions=configuration.embedding_dimensions,
                embedding_input_hash="b" * 64,
                embedding=[0.0] * configuration.embedding_dimensions,
            )
            session.add(embedding)
            await session.flush()

            stored = await session.scalar(select(ChunkEmbedding).where(
                ChunkEmbedding.id == embedding.id,
            ))
            assert stored is not None
            assert len(stored.embedding) == configuration.embedding_dimensions

            with pytest.raises(
                DBAPIError, match="chunk embeddings are immutable",
            ):
                async with session.begin_nested():
                    await session.execute(
                        update(ChunkEmbedding)
                        .where(ChunkEmbedding.id == embedding.id)
                        .values(embedding_input_hash="c" * 64)
                    )

            with pytest.raises(
                DBAPIError,
                match="embedding model does not match derived configuration",
            ):
                async with session.begin_nested():
                    session.add(ChunkEmbedding(
                        organization_id=organization_id,
                        derived_set_id=derived_set.id,
                        chunk_id=chunk.id,
                        embedding_model="wrong-model",
                        embedding_dimensions=configuration.embedding_dimensions,
                        embedding_input_hash="d" * 64,
                        embedding=[0.0] * configuration.embedding_dimensions,
                    ))
                    await session.flush()

            with pytest.raises(
                IntegrityError,
                match="ck_chunk_embeddings_embedding_dimensions_match_vector",
            ):
                async with session.begin_nested():
                    session.add(ChunkEmbedding(
                        organization_id=organization_id,
                        derived_set_id=derived_set.id,
                        chunk_id=chunk.id,
                        embedding_model=configuration.embedding_model,
                        embedding_dimensions=configuration.embedding_dimensions,
                        embedding_input_hash="e" * 64,
                        embedding=[0.0] * 3,
                    ))
                    await session.flush()
        finally:
            await session.close()
            await transaction.rollback()


@pytest.mark.asyncio
async def test_app_role_chunk_embedding_permissions(database_engine):
    async with database_engine.connect() as connection:
        for privilege in ("SELECT", "INSERT"):
            assert await connection.scalar(
                text("SELECT has_table_privilege(current_user, 'chunk_embeddings', :privilege)"),
                {"privilege": privilege},
            ) is True

        for statement in (
            "UPDATE chunk_embeddings SET embedding_model = embedding_model WHERE false",
            "DELETE FROM chunk_embeddings WHERE false",
        ):
            savepoint = await connection.begin_nested()
            try:
                with pytest.raises(DBAPIError, match="permission denied"):
                    await connection.execute(text(statement))
            finally:
                await savepoint.rollback()

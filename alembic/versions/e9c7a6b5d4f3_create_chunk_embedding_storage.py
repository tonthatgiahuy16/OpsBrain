"""create chunk embedding storage

Revision ID: e9c7a6b5d4f3
Revises: b719c0e4a216
Create Date: 2026-11-18
"""
from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa


revision: str = "e9c7a6b5d4f3"
down_revision: Union[str, Sequence[str], None] = "b719c0e4a216"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chunk_embeddings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("derived_set_id", sa.UUID(), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("embedding_model", sa.Text(), nullable=False),
        sa.Column("embedding_model_revision", sa.Text(), nullable=True),
        sa.Column("embedding_dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding_input_hash", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.CheckConstraint(
            "btrim(embedding_model) <> ''",
            name=op.f("ck_chunk_embeddings_embedding_model_not_blank"),
        ),
        sa.CheckConstraint(
            "embedding_dimensions > 0",
            name=op.f("ck_chunk_embeddings_embedding_dimensions_positive"),
        ),
        sa.CheckConstraint(
            "embedding_input_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_chunk_embeddings_embedding_input_hash_sha256_hex"),
        ),
        sa.CheckConstraint(
            "vector_dims(embedding) = embedding_dimensions",
            name=op.f("ck_chunk_embeddings_embedding_dimensions_match_vector"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "derived_set_id"],
            ["derived_sets.organization_id", "derived_sets.id"],
            name="fk_chunk_embeddings_derived_set", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "derived_set_id", "chunk_id"],
            ["chunks.organization_id", "chunks.derived_set_id", "chunks.id"],
            name="fk_chunk_embeddings_chunk", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_embeddings")),
        sa.UniqueConstraint(
            "organization_id", "derived_set_id", "chunk_id",
            name=op.f("uq_chunk_embeddings_organization_id_derived_set_id_chunk_id"),
        ),
    )
    op.execute(
        """
        CREATE FUNCTION enforce_chunk_embedding_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_model text;
            expected_revision text;
            expected_dimensions integer;
        BEGIN
            SELECT
                configuration.embedding_model,
                configuration.embedding_model_revision,
                configuration.embedding_dimensions
            INTO expected_model, expected_revision, expected_dimensions
            FROM derived_sets AS derived_set
            JOIN derived_configurations AS configuration
              ON configuration.organization_id = derived_set.organization_id
             AND configuration.id = derived_set.configuration_id
            WHERE derived_set.organization_id = NEW.organization_id
              AND derived_set.id = NEW.derived_set_id
            FOR UPDATE OF derived_set, configuration;

            IF NOT FOUND THEN
                RETURN NEW;
            END IF;
            IF expected_model IS NULL THEN
                RAISE EXCEPTION 'derived configuration does not enable embeddings';
            END IF;
            IF NEW.embedding_model IS DISTINCT FROM expected_model THEN
                RAISE EXCEPTION 'embedding model does not match derived configuration';
            END IF;
            IF NEW.embedding_model_revision IS DISTINCT FROM expected_revision THEN
                RAISE EXCEPTION 'embedding revision does not match derived configuration';
            END IF;
            IF NEW.embedding_dimensions IS DISTINCT FROM expected_dimensions THEN
                RAISE EXCEPTION 'embedding dimensions do not match derived configuration';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_chunk_embeddings_enforce_provenance
        BEFORE INSERT OR UPDATE OF
            organization_id,
            derived_set_id,
            chunk_id,
            embedding_model,
            embedding_model_revision,
            embedding_dimensions
        ON chunk_embeddings
        FOR EACH ROW
        EXECUTE FUNCTION enforce_chunk_embedding_provenance();
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_chunk_embedding_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'chunk embeddings are immutable';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_chunk_embeddings_prevent_mutation
        BEFORE UPDATE ON chunk_embeddings
        FOR EACH ROW
        EXECUTE FUNCTION prevent_chunk_embedding_mutation();
        """
    )
    op.execute("GRANT SELECT, INSERT ON TABLE chunk_embeddings TO opsbrain_app")
    op.execute("REVOKE UPDATE, DELETE ON TABLE chunk_embeddings FROM opsbrain_app")


def downgrade() -> None:
    op.execute("GRANT UPDATE, DELETE ON TABLE chunk_embeddings TO opsbrain_app")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_chunk_embeddings_prevent_mutation "
        "ON chunk_embeddings"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_chunk_embedding_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_chunk_embeddings_enforce_provenance "
        "ON chunk_embeddings"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_chunk_embedding_provenance()")
    op.drop_table("chunk_embeddings")

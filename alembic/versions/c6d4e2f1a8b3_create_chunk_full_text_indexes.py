"""create chunk full text indexes

Revision ID: c6d4e2f1a8b3
Revises: b3e7c9a1d5f2
Create Date: 2026-11-27
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "c6d4e2f1a8b3"
down_revision: Union[str, Sequence[str], None] = "b3e7c9a1d5f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _execute_statements(sql: str) -> None:
    """Execute simple DDL statements separately for asyncpg prepared calls."""
    for statement in sql.split(";"):
        if statement.strip():
            op.execute(statement)


def upgrade() -> None:
    op.create_table(
        "chunk_full_text_indexes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("derived_set_id", sa.UUID(), nullable=False),
        sa.Column("chunk_id", sa.UUID(), nullable=False),
        sa.Column("document_pk", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("full_text_configuration", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.Text(), nullable=False),
        sa.Column("search_vector", postgresql.TSVECTOR(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "full_text_configuration = 'english'",
            name=op.f("ck_chunk_full_text_indexes_full_text_configuration_allowed"),
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_chunk_full_text_indexes_input_hash_sha256_hex"),
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "derived_set_id"],
            ["derived_sets.organization_id", "derived_sets.id"],
            name="fk_chunk_full_text_indexes_derived_set",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "derived_set_id", "chunk_id"],
            ["chunks.organization_id", "chunks.derived_set_id", "chunks.id"],
            name="fk_chunk_full_text_indexes_chunk",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "document_pk", "document_version_id"],
            [
                "document_versions.organization_id",
                "document_versions.document_pk",
                "document_versions.id",
            ],
            name="fk_chunk_full_text_indexes_document_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunk_full_text_indexes")),
        sa.UniqueConstraint(
            "organization_id",
            "derived_set_id",
            "chunk_id",
            name=op.f("uq_chunk_full_text_indexes_organization_id_derived_set_id_chunk_id"),
        ),
    )

    op.create_index(
        "ix_chunk_full_text_indexes_lookup",
        "chunk_full_text_indexes",
        ["organization_id", "derived_set_id", "chunk_id"],
    )
    op.create_index(
        "ix_chunk_full_text_indexes_search_vector",
        "chunk_full_text_indexes",
        ["search_vector"],
        postgresql_using="gin",
    )

    op.execute(
        """
        CREATE FUNCTION enforce_chunk_full_text_provenance()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            expected_config text;
            chunk_doc_pk uuid;
            chunk_version_id uuid;
        BEGIN
            SELECT configuration.full_text_configuration
            INTO expected_config
            FROM derived_sets AS derived_set
            JOIN derived_configurations AS configuration
              ON configuration.organization_id = derived_set.organization_id
             AND configuration.id = derived_set.configuration_id
            WHERE derived_set.organization_id = NEW.organization_id
              AND derived_set.id = NEW.derived_set_id
            FOR UPDATE OF derived_set, configuration;

            IF FOUND THEN
                IF expected_config IS NULL THEN
                    RAISE EXCEPTION 'derived configuration does not enable full_text';
                END IF;
                IF NEW.full_text_configuration IS DISTINCT FROM expected_config THEN
                    RAISE EXCEPTION 'full_text_configuration does not match derived configuration';
                END IF;
            END IF;

            SELECT chunk.document_pk, chunk.document_version_id
            INTO chunk_doc_pk, chunk_version_id
            FROM chunks AS chunk
            WHERE chunk.organization_id = NEW.organization_id
              AND chunk.derived_set_id = NEW.derived_set_id
              AND chunk.id = NEW.chunk_id;

            IF FOUND THEN
                IF NEW.document_pk IS DISTINCT FROM chunk_doc_pk
                   OR NEW.document_version_id IS DISTINCT FROM chunk_version_id THEN
                    RAISE EXCEPTION 'chunk full-text provenance does not match chunk';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_chunk_full_text_indexes_enforce_provenance
        BEFORE INSERT OR UPDATE OF
            organization_id,
            derived_set_id,
            chunk_id,
            document_pk,
            document_version_id,
            full_text_configuration
        ON chunk_full_text_indexes
        FOR EACH ROW
        EXECUTE FUNCTION enforce_chunk_full_text_provenance();
        """
    )
    op.execute(
        """
        CREATE FUNCTION prevent_chunk_full_text_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'chunk full-text indexes are immutable';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_chunk_full_text_indexes_prevent_mutation
        BEFORE UPDATE ON chunk_full_text_indexes
        FOR EACH ROW
        EXECUTE FUNCTION prevent_chunk_full_text_mutation();
        """
    )

    op.execute(
        "GRANT SELECT, INSERT ON TABLE chunk_full_text_indexes TO opsbrain_app"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE chunk_full_text_indexes FROM opsbrain_app"
    )
    op.execute(
        "GRANT SELECT, INSERT ON TABLE chunk_full_text_indexes TO opsbrain_worker"
    )

    op.execute(
        "ALTER TABLE public.chunk_full_text_indexes ENABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE public.chunk_full_text_indexes FORCE ROW LEVEL SECURITY"
    )
    _execute_statements(
        """
        CREATE POLICY chunk_full_text_indexes_acl_select
        ON public.chunk_full_text_indexes
        FOR SELECT TO opsbrain_app
        USING (
            organization_id = NULLIF(current_setting('app.organization_id', true), '')::uuid
            AND public.app_can_read_document_version(document_pk, document_version_id)
        );
        CREATE POLICY chunk_full_text_indexes_org_write
        ON public.chunk_full_text_indexes
        FOR ALL TO opsbrain_app
        USING (false)
        WITH CHECK (false);
        """
    )


def downgrade() -> None:
    _execute_statements(
        """
        DROP POLICY IF EXISTS chunk_full_text_indexes_org_write ON public.chunk_full_text_indexes;
        DROP POLICY IF EXISTS chunk_full_text_indexes_acl_select ON public.chunk_full_text_indexes;
        """
    )
    op.execute(
        "ALTER TABLE public.chunk_full_text_indexes NO FORCE ROW LEVEL SECURITY"
    )
    op.execute(
        "ALTER TABLE public.chunk_full_text_indexes DISABLE ROW LEVEL SECURITY"
    )
    op.execute(
        "REVOKE SELECT, INSERT ON TABLE chunk_full_text_indexes FROM opsbrain_worker"
    )
    op.execute(
        "GRANT UPDATE, DELETE ON TABLE chunk_full_text_indexes TO opsbrain_app"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_chunk_full_text_indexes_prevent_mutation "
        "ON chunk_full_text_indexes"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_chunk_full_text_mutation()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_chunk_full_text_indexes_enforce_provenance "
        "ON chunk_full_text_indexes"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_chunk_full_text_provenance()")
    op.drop_index(
        "ix_chunk_full_text_indexes_search_vector",
        table_name="chunk_full_text_indexes",
    )
    op.drop_index(
        "ix_chunk_full_text_indexes_lookup",
        table_name="chunk_full_text_indexes",
    )
    op.drop_table("chunk_full_text_indexes")

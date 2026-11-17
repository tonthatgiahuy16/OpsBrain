from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from src.database import Base
import src.models.storage  # noqa: F401


def test_chunk_embedding_table_is_registered_with_provenance_constraints():
    table = Base.metadata.tables["chunk_embeddings"]

    assert {
        "id", "organization_id", "derived_set_id", "chunk_id",
        "embedding_model", "embedding_model_revision", "embedding_dimensions",
        "embedding_input_hash", "embedding", "created_at",
    } <= set(table.columns.keys())

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("organization_id", "derived_set_id", "chunk_id") in unique_columns

    foreign_keys = {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert (
        ("organization_id", "derived_set_id", "chunk_id"),
        ("chunks.organization_id", "chunks.derived_set_id", "chunks.id"),
    ) in foreign_keys

    check_names = {
        constraint.name for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_chunk_embeddings_embedding_model_not_blank",
        "ck_chunk_embeddings_embedding_dimensions_positive",
        "ck_chunk_embeddings_embedding_input_hash_sha256_hex",
        "ck_chunk_embeddings_embedding_dimensions_match_vector",
    } <= check_names

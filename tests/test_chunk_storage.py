from src.database import Base
import src.models.storage  # noqa: F401

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

def test_chunk_table_is_registered():
    assert "chunks" in Base.metadata.tables

    table = Base.metadata.tables["chunks"]

    assert {
        "id",
        "organization_id",
        "derived_set_id",
        "document_pk",
        "document_version_id",
        "chunk_key",
        "chunk_index",
        "chunking_version",
        "heading_path",
        "content",
        "content_hash",
        "start_offset",
        "end_offset",
        "token_count",
        "created_at",
    } <= set(table.columns.keys())


def test_chunk_identity_constraints_are_registered():
    table = Base.metadata.tables["chunks"]

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert {
        ("organization_id", "derived_set_id", "chunk_key"),
        ("organization_id", "derived_set_id", "chunk_index"),
        ("organization_id", "derived_set_id", "id"),
    } <= unique_columns

    foreign_keys = {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert (
        ("organization_id", "derived_set_id"),
        ("derived_sets.organization_id", "derived_sets.id"),
    ) in foreign_keys

    assert (
        ("organization_id", "document_pk", "document_version_id"),
        (
            "document_versions.organization_id",
            "document_versions.document_pk",
            "document_versions.id",
        ),
    ) in foreign_keys


def test_chunk_content_and_position_constraints_are_registered():
    table = Base.metadata.tables["chunks"]

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_chunks_chunk_key_not_blank",
        "ck_chunks_chunk_index_non_negative",
        "ck_chunks_chunking_version_not_blank",
        "ck_chunks_content_not_blank",
        "ck_chunks_content_hash_sha256_hex",
        "ck_chunks_start_offset_non_negative",
        "ck_chunks_end_offset_non_negative",
        "ck_chunks_offsets_ordered",
        "ck_chunks_token_count_non_negative",
    } <= check_names

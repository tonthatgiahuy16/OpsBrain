from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from src.database import Base
import src.models.storage  # noqa: F401


CORE_STORAGE_TABLES = {
    "organizations",
    "principals",
    "documents",
    "document_versions",
}


def unique_column_sets(table):
    return {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }


def foreign_key_signatures(table):
    return {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }


def index_column_sets(table):
    return {
        tuple(column.name for column in index.columns)
        for index in table.indexes
    }


def check_constraint_names(table):
    return {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }


def test_core_storage_tables_are_registered():
    missing_tables = CORE_STORAGE_TABLES.difference(Base.metadata.tables)

    assert not missing_tables, (
        f"Missing core storage tables: {sorted(missing_tables)}"
    )


def test_document_identity_constraints_are_registered():
    table = Base.metadata.tables["documents"]

    assert (
    "organization_id",
    "current_version_id",
) in index_column_sets(table)

    assert (
        "organization_id",
        "document_id",
    ) in unique_column_sets(table)

    assert (
        "organization_id",
        "id",
    ) in unique_column_sets(table)

    assert (
        (
            "organization_id",
            "id",
            "current_version_id",
        ),
        (
            "document_versions.organization_id",
            "document_versions.document_pk",
            "document_versions.id",
        ),
    ) in foreign_key_signatures(table)

    indexes = {index.name: index for index in table.indexes}
    active_source_index = indexes[
        "uq_documents_organization_id_current_source_uri_active"
    ]

    assert active_source_index.unique is True
    assert tuple(
        column.name for column in active_source_index.columns
    ) == (
        "organization_id",
        "current_source_uri",
    )


def test_document_version_constraints_are_registered():
    table = Base.metadata.tables["document_versions"]
    unique_sets = unique_column_sets(table)

    assert (
    "organization_id",
    "document_pk",
    "content_hash",
) in index_column_sets(table)

    assert (
        "organization_id",
        "document_pk",
        "version_number",
    ) in unique_sets

    assert (
        "organization_id",
        "document_pk",
        "id",
    ) in unique_sets

    assert (
        "organization_id",
        "document_pk",
        "parent_version_id",
    ) in unique_sets

    foreign_keys = foreign_key_signatures(table)

    assert (
        ("organization_id", "document_pk"),
        ("documents.organization_id", "documents.id"),
    ) in foreign_keys

    assert (
        (
            "organization_id",
            "document_pk",
            "parent_version_id",
        ),
        (
            "document_versions.organization_id",
            "document_versions.document_pk",
            "document_versions.id",
        ),
    ) in foreign_keys


def test_critical_document_checks_are_named():
    document_checks = check_constraint_names(
        Base.metadata.tables["documents"]
    )
    version_checks = check_constraint_names(
        Base.metadata.tables["document_versions"]
    )

    assert "ck_documents_delete_state_consistent" in document_checks
    assert (
        "ck_document_versions_content_hash_sha256_hex"
        in version_checks
    )
    assert (
        "ck_document_versions_frontmatter_snapshot_object"
        in version_checks
    )
    assert (
        "ck_document_versions_version_lineage_consistent"
        in version_checks
    )

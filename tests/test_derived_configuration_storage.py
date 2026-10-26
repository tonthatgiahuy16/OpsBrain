from src.database import Base
import src.models.storage  # noqa: F401

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

def test_derived_configuration_table_is_registered():
    assert "derived_configurations" in Base.metadata.tables

    table = Base.metadata.tables["derived_configurations"]

    assert {
        "id",
        "organization_id",
        "configuration_key",
        "chunking_version",
        "embedding_model",
        "embedding_model_revision",
        "embedding_dimensions",
        "full_text_configuration",
        "relationship_extractor_version",
        "configuration_payload",
        "configuration_hash",
        "status",
        "created_by_principal_id",
        "created_at",
    } <= set(table.columns.keys())


def test_derived_configuration_constraints_are_registered():
    table = Base.metadata.tables["derived_configurations"]

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert {
        ("organization_id", "configuration_key"),
        ("organization_id", "id"),
    } <= unique_columns

    foreign_keys = {
        (
            tuple(element.parent.name for element in constraint.elements),
            tuple(element.target_fullname for element in constraint.elements),
            constraint.ondelete,
        )
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert (
        ("organization_id", "created_by_principal_id"),
        ("principals.organization_id", "principals.id"),
        "RESTRICT",
    ) in foreign_keys

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_derived_configurations_configuration_key_not_blank",
        "ck_derived_configurations_chunking_version_not_blank",
        "ck_derived_configurations_configuration_hash_sha256_hex",
        "ck_derived_configurations_configuration_payload_object",
        "ck_derived_configurations_status_allowed",
        "ck_derived_configurations_embedding_dimensions_positive",
        "ck_derived_configurations_embedding_model_requires_dimensions",
    } <= check_names

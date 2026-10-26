from src.database import Base
import src.models.storage  # noqa: F401

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

def test_derived_set_table_is_registered():
    assert "derived_sets" in Base.metadata.tables

    table = Base.metadata.tables["derived_sets"]

    assert {
        "id",
        "organization_id",
        "document_pk",
        "document_version_id",
        "configuration_id",
        "build_number",
        "status",
        "is_active",
        "failure_code",
        "failure_message",
        "started_at",
        "completed_at",
        "activated_at",
        "created_at",
    } <= set(table.columns.keys())


def test_derived_set_identity_constraints_are_registered():
    table = Base.metadata.tables["derived_sets"]

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert {
        ("organization_id", "id"),
        (
            "organization_id",
            "document_version_id",
            "configuration_id",
            "build_number",
        ),
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
        ("organization_id", "document_pk", "document_version_id"),
        (
            "document_versions.organization_id",
            "document_versions.document_pk",
            "document_versions.id",
        ),
    ) in foreign_keys

    assert (
        ("organization_id", "configuration_id"),
        ("derived_configurations.organization_id", "derived_configurations.id"),
    ) in foreign_keys


def test_derived_set_build_constraints_are_registered():
    table = Base.metadata.tables["derived_sets"]

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }

    assert {
        "ck_derived_sets_build_number_positive",
        "ck_derived_sets_status_allowed",
        "ck_derived_sets_pending_timestamps",
        "ck_derived_sets_building_timestamps",
        "ck_derived_sets_terminal_completed_at",
        "ck_derived_sets_failed_requires_code",
        "ck_derived_sets_active_requires_ready",
        "ck_derived_sets_active_requires_activated_at",
    } <= check_names


def test_derived_set_has_unique_active_index():
    table = Base.metadata.tables["derived_sets"]

    index = next(
        index
        for index in table.indexes
        if index.name == "uq_derived_sets_one_active_per_document"
    )

    assert index.unique is True
    assert [column.name for column in index.columns] == [
        "organization_id",
        "document_pk",
    ]
    assert str(index.dialect_options["postgresql"]["where"]) == "is_active"

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    UniqueConstraint,
)

from src.database import Base
import src.models.storage  # noqa: F401


INGESTION_JOB_TABLES = {
    "ingestion_jobs",
    "ingestion_job_items",
    "ingestion_job_attempts",
}


def test_ingestion_job_tables_are_registered():
    missing_tables = INGESTION_JOB_TABLES.difference(
        Base.metadata.tables
    )

    assert not missing_tables, (
        f"Missing ingestion job tables: {sorted(missing_tables)}"
    )


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


def test_ingestion_job_constraints_are_registered():
    table = Base.metadata.tables["ingestion_jobs"]

    assert (
        "organization_id",
        "submitted_by_principal_id",
        "idempotency_key_hash",
    ) in unique_column_sets(table)

    assert (
        "organization_id",
        "id",
    ) in unique_column_sets(table)

    assert (
        (
            "organization_id",
            "submitted_by_principal_id",
        ),
        (
            "principals.organization_id",
            "principals.id",
        ),
    ) in foreign_key_signatures(table)

    assert (
        "organization_id",
        "submitted_by_principal_id",
        "submitted_at",
    ) in index_column_sets(table)

    assert (
        "status",
        "submitted_at",
    ) in index_column_sets(table)

    checks = check_constraint_names(table)

    assert (
        "ck_ingestion_jobs_idempotency_key_hash_sha256_hex"
        in checks
    )
    assert (
        "ck_ingestion_jobs_request_payload_hash_sha256_hex"
        in checks
    )
    assert (
        "ck_ingestion_jobs_status_timestamps_consistent"
        in checks
    )


def test_ingestion_job_item_constraints_are_registered():
    table = Base.metadata.tables["ingestion_job_items"]

    assert (
        "organization_id",
        "ingestion_job_id",
        "item_index",
    ) in unique_column_sets(table)

    foreign_keys = foreign_key_signatures(table)

    assert (
        (
            "organization_id",
            "ingestion_job_id",
        ),
        (
            "ingestion_jobs.organization_id",
            "ingestion_jobs.id",
        ),
    ) in foreign_keys

    assert (
        (
            "organization_id",
            "document_pk",
        ),
        (
            "documents.organization_id",
            "documents.id",
        ),
    ) in foreign_keys

    assert (
        (
            "organization_id",
            "document_pk",
            "document_version_id",
        ),
        (
            "document_versions.organization_id",
            "document_versions.document_pk",
            "document_versions.id",
        ),
    ) in foreign_keys

    checks = check_constraint_names(table)

    assert (
        "ck_ingestion_job_items_submitted_content_hash_sha256_hex"
        in checks
    )
    assert (
        "ck_ingestion_job_items_processing_state_consistent"
        in checks
    )
    assert "ck_ingestion_job_items_errors_array" in checks


def test_ingestion_job_attempt_constraints_are_registered():
    table = Base.metadata.tables["ingestion_job_attempts"]

    assert (
        "organization_id",
        "ingestion_job_id",
        "attempt_number",
    ) in unique_column_sets(table)

    assert (
        (
            "organization_id",
            "ingestion_job_id",
        ),
        (
            "ingestion_jobs.organization_id",
            "ingestion_jobs.id",
        ),
    ) in foreign_key_signatures(table)

    checks = check_constraint_names(table)

    assert (
        "ck_ingestion_job_attempts_attempt_number_positive"
        in checks
    )
    assert (
        "ck_ingestion_job_attempts_attempt_state_consistent"
        in checks
    )

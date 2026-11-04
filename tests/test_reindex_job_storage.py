from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

from src.database import Base
import src.models.storage  # noqa: F401


def _names(table, kind):
    return {constraint.name for constraint in table.constraints if isinstance(constraint, kind)}


def _unique_sets(table):
    return {tuple(column.name for column in c.columns) for c in table.constraints if isinstance(c, UniqueConstraint)}


def test_reindex_job_tables_and_audit_reference_are_registered():
    assert {"reindex_jobs", "reindex_job_items", "reindex_job_item_attempts"} <= set(Base.metadata.tables)
    audit_fks = _names(Base.metadata.tables["audit_events"], ForeignKeyConstraint)
    assert "fk_audit_events_reindex_job" in audit_fks
    assert "ck_audit_events_reindex_job_deferred_null" not in _names(Base.metadata.tables["audit_events"], CheckConstraint)


def test_reindex_job_metadata_enforces_contract_keys_states_and_indexes():
    job = Base.metadata.tables["reindex_jobs"]
    item = Base.metadata.tables["reindex_job_items"]
    attempt = Base.metadata.tables["reindex_job_item_attempts"]

    assert ("organization_id", "id") in _unique_sets(job)
    assert ("organization_id", "submitted_by_principal_id", "idempotency_key_hash") in _unique_sets(job)
    assert ("organization_id", "reindex_job_id", "document_pk") in _unique_sets(item)
    assert ("organization_id", "id") in _unique_sets(item)
    assert ("organization_id", "reindex_job_item_id", "attempt_number") in _unique_sets(attempt)
    assert ("organization_id", "reindex_job_item_id", "id") in _unique_sets(attempt)

    active_attempt_fk = {
        constraint.name
        for constraint in item.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert "fk_reindex_job_items_active_attempt" in active_attempt_fk

    assert {
        "ck_reindex_jobs_status_timestamps_consistent",
        "ck_reindex_jobs_targets_unique",
        "ck_reindex_job_items_processing_state_consistent",
        "ck_reindex_job_items_next_retry_requires_queued",
        "ck_reindex_job_items_active_attempt_matches_processing_status",
        "ck_reindex_job_item_attempts_attempt_state_consistent",
        "ck_reindex_job_item_attempts_lease_after_start",
    } <= (_names(job, CheckConstraint) | _names(item, CheckConstraint) | _names(attempt, CheckConstraint))
    assert {"ix_reindex_jobs_status_submitted_at"} <= {index.name for index in job.indexes}
    assert {"ix_reindex_job_items_job_processing_status"} <= {index.name for index in item.indexes}
    assert {"uq_reindex_job_item_attempts_running_item"} <= {index.name for index in attempt.indexes}

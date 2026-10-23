from src.database import Base
from src.models.storage import AuditEvent  # noqa: F401

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, UniqueConstraint

def test_audit_event_table_is_registered():
    table = Base.metadata.tables["audit_events"]

    assert {
        "id",
        "organization_id",
        "event_type",
        "outcome",
        "actor_kind",
        "actor_principal_id",
        "request_id",
        "correlation_id",
        "document_pk",
        "document_version_id",
        "ingestion_job_id",
        "reindex_job_id",
        "review_decision_id",
        "reason_code",
        "details",
        "occurred_at",
        "created_at",
    } <= set(table.columns.keys())

    assert "updated_at" not in table.columns


def test_audit_event_constraints_and_indexes_are_registered():
    table = Base.metadata.tables["audit_events"]

    check_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {
        "ck_audit_events_event_type_not_blank",
        "ck_audit_events_outcome_allowed",
        "ck_audit_events_actor_kind_allowed",
        "ck_audit_events_actor_kind_consistent",
        "ck_audit_events_request_id_not_blank_when_present",
        "ck_audit_events_correlation_id_not_blank_when_present",
        "ck_audit_events_reason_code_not_blank_when_present",
        "ck_audit_events_details_object",
        "ck_audit_events_trace_anchor_present",
        "ck_audit_events_document_version_requires_document",
        "ck_audit_events_deferred_refs_null",
    } <= check_names

    foreign_key_names = {
        constraint.name
        for constraint in table.constraints
        if isinstance(constraint, ForeignKeyConstraint)
    }
    assert {
        "fk_audit_events_actor_principal",
        "fk_audit_events_document",
        "fk_audit_events_document_version",
        "fk_audit_events_ingestion_job",
    } <= foreign_key_names

    unique_column_sets = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("organization_id", "id") in unique_column_sets

    assert {
        "ix_audit_events_org_occurred_at",
        "ix_audit_events_org_actor_occurred_at",
        "ix_audit_events_org_document_occurred_at",
        "ix_audit_events_request_id",
        "ix_audit_events_correlation_id",
    } <= {index.name for index in table.indexes}

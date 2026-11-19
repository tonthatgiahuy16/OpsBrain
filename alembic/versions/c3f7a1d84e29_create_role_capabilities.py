"""create capability catalog and role capabilities

Revision ID: c3f7a1d84e29
Revises: a4c8e1f6b902
Create Date: 2026-11-19

Capability grants live on roles, not on documents. ``capabilities`` is a
migration-managed catalog (storage contract §9.5); ``role_capabilities``
records one grant per role and capability with an explicit grant/revoke
lifecycle (§9.6). The application role may read both tables but must not write
them: a bug in the request path must not be able to grant itself
``document:approve``.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c3f7a1d84e29"
down_revision: Union[str, Sequence[str], None] = "a4c8e1f6b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CAPABILITIES = (
    (
        "document:approve",
        "Approve or reject a document version under current ACL",
    ),
    (
        "document:ingest",
        "Create or update documents through the ingestion lifecycle",
    ),
    (
        "document:read_state",
        "Read lifecycle, provenance and review state of a visible document",
    ),
    (
        "index:read",
        "Read reindex jobs created by another principal in the organization",
    ),
    (
        "index:rebuild",
        "Create or run derived-data reindex work",
    ),
    (
        "ingestion:read",
        "Read ingestion jobs created by another principal in the organization",
    ),
    (
        "query:execute",
        "Execute retrieval queries over authorized evidence",
    ),
)


def upgrade() -> None:
    op.create_table(
        "capabilities",
        sa.Column("capability_key", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("capability_key", name=op.f("pk_capabilities")),
        sa.CheckConstraint(
            "btrim(capability_key) <> ''",
            name=op.f("ck_capabilities_capability_key_not_blank"),
        ),
        sa.CheckConstraint(
            "btrim(description) <> ''",
            name=op.f("ck_capabilities_description_not_blank"),
        ),
    )
    capabilities_table = sa.table(
        "capabilities",
        sa.column("capability_key", sa.Text),
        sa.column("description", sa.Text),
    )
    op.bulk_insert(
        capabilities_table,
        [
            {"capability_key": key, "description": description}
            for key, description in CAPABILITIES
        ],
    )
    op.create_table(
        "role_capabilities",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("role_id", sa.UUID(), nullable=False),
        sa.Column("capability_key", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("granted_by_principal_id", sa.UUID(), nullable=False),
        sa.Column("revoked_by_principal_id", sa.UUID(), nullable=True),
        sa.Column("granted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["capability_key"], ["capabilities.capability_key"],
            name=op.f("fk_role_capabilities_capability_key_capabilities"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "granted_by_principal_id"],
            ["principals.organization_id", "principals.id"],
            name=op.f("fk_role_capabilities_granted_by_principal"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "revoked_by_principal_id"],
            ["principals.organization_id", "principals.id"],
            name=op.f("fk_role_capabilities_revoked_by_principal"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id", "role_id"],
            ["roles.organization_id", "roles.id"],
            name=op.f("fk_role_capabilities_organization_id_role_id_roles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_capabilities")),
        sa.UniqueConstraint(
            "organization_id", "id",
            name=op.f("uq_role_capabilities_organization_id_id"),
        ),
        sa.UniqueConstraint(
            "organization_id", "role_id", "capability_key",
            name=op.f(
                "uq_role_capabilities_organization_id_role_id_capability_key"
            ),
        ),
        sa.CheckConstraint(
            "status IN ('active', 'revoked')",
            name=op.f("ck_role_capabilities_status_allowed"),
        ),
        sa.CheckConstraint(
            "status <> 'active' OR "
            "(revoked_at IS NULL AND revoked_by_principal_id IS NULL)",
            name=op.f("ck_role_capabilities_active_grant_consistent"),
        ),
        sa.CheckConstraint(
            "status <> 'revoked' OR "
            "(revoked_at IS NOT NULL AND revoked_by_principal_id IS NOT NULL)",
            name=op.f("ck_role_capabilities_revoked_grant_consistent"),
        ),
        sa.CheckConstraint(
            "revoked_at IS NULL OR revoked_at >= granted_at",
            name=op.f("ck_role_capabilities_revocation_not_before_grant"),
        ),
    )
    op.create_index(
        "ix_role_capabilities_active_role",
        "role_capabilities",
        ["organization_id", "role_id"],
        unique=False,
        postgresql_where=sa.text("status = 'active'"),
    )
    # The bootstrap grants the application role DML on new tables by default.
    # Narrow both tables before they are reachable from a request path.
    op.execute("REVOKE INSERT, UPDATE, DELETE ON TABLE capabilities FROM opsbrain_app")
    op.execute("GRANT SELECT ON TABLE capabilities TO opsbrain_app")
    op.execute(
        "REVOKE INSERT, UPDATE, DELETE ON TABLE role_capabilities FROM opsbrain_app"
    )
    op.execute("GRANT SELECT ON TABLE role_capabilities TO opsbrain_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE role_capabilities FROM opsbrain_app")
    op.execute("REVOKE SELECT ON TABLE capabilities FROM opsbrain_app")
    op.drop_index("ix_role_capabilities_active_role", table_name="role_capabilities")
    op.drop_table("role_capabilities")
    op.drop_table("capabilities")

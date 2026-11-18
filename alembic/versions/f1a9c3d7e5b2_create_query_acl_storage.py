"""create query ACL storage

Revision ID: f1a9c3d7e5b2
Revises: e9c7a6b5d4f3
Create Date: 2026-11-18
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a9c3d7e5b2"
down_revision: Union[str, Sequence[str], None] = "e9c7a6b5d4f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for name, key in (("teams", "team_key"), ("roles", "role_key")):
        op.create_table(
            name,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("organization_id", sa.UUID(), nullable=False),
            sa.Column(key, sa.Text(), nullable=False),
            sa.Column("display_name", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "id"),
            sa.UniqueConstraint("organization_id", key),
            sa.CheckConstraint(f"btrim({key}) <> ''", name=op.f(f"ck_{name}_{key}_not_blank")),
            sa.CheckConstraint("btrim(display_name) <> ''", name=op.f(f"ck_{name}_display_name_not_blank")),
            sa.CheckConstraint("status IN ('active', 'disabled')", name=op.f(f"ck_{name}_status_allowed")),
        )
    for name, subject in (("principal_team_memberships", "team_id"), ("principal_role_memberships", "role_id")):
        target = "teams" if subject == "team_id" else "roles"
        op.create_table(
            name,
            sa.Column("id", sa.UUID(), nullable=False),
            sa.Column("organization_id", sa.UUID(), nullable=False),
            sa.Column("principal_id", sa.UUID(), nullable=False),
            sa.Column(subject, sa.UUID(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
            sa.ForeignKeyConstraint(["organization_id", "principal_id"], ["principals.organization_id", "principals.id"], ondelete="RESTRICT"),
            sa.ForeignKeyConstraint(["organization_id", subject], [f"{target}.organization_id", f"{target}.id"], ondelete="RESTRICT"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("organization_id", "principal_id", subject),
            sa.CheckConstraint("status IN ('active', 'revoked')", name=op.f(f"ck_{name}_status_allowed")),
        )
        op.create_index(
            f"ix_{name}_active_principal", name,
            ["organization_id", "principal_id"],
            postgresql_where=sa.text("status = 'active'"),
        )
    op.create_table(
        "document_version_acl_entries",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("organization_id", sa.UUID(), nullable=False),
        sa.Column("document_pk", sa.UUID(), nullable=False),
        sa.Column("document_version_id", sa.UUID(), nullable=False),
        sa.Column("user_principal_id", sa.UUID(), nullable=True),
        sa.Column("team_id", sa.UUID(), nullable=True),
        sa.Column("role_id", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id", "document_pk", "document_version_id"], ["document_versions.organization_id", "document_versions.document_pk", "document_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "user_principal_id"], ["principals.organization_id", "principals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "team_id"], ["teams.organization_id", "teams.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["organization_id", "role_id"], ["roles.organization_id", "roles.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("num_nonnulls(user_principal_id, team_id, role_id) = 1", name=op.f("ck_document_version_acl_entries_exactly_one_subject")),
    )
    for column in ("user_principal_id", "team_id", "role_id"):
        op.create_index(
            f"uq_document_version_acl_{column}", "document_version_acl_entries",
            ["organization_id", "document_version_id", column], unique=True,
            postgresql_where=sa.text(f"{column} IS NOT NULL"),
        )
    op.create_index("ix_document_version_acl_entries_document", "document_version_acl_entries", ["organization_id", "document_version_id"])
    op.execute("GRANT SELECT ON TABLE teams, roles, principal_team_memberships, principal_role_memberships, document_version_acl_entries TO opsbrain_app")


def downgrade() -> None:
    op.execute("REVOKE SELECT ON TABLE document_version_acl_entries, principal_role_memberships, principal_team_memberships, roles, teams FROM opsbrain_app")
    op.drop_table("document_version_acl_entries")
    op.drop_table("principal_role_memberships")
    op.drop_table("principal_team_memberships")
    op.drop_table("roles")
    op.drop_table("teams")

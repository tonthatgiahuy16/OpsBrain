"""enforce role capability allowlist

Revision ID: d8a4f6b2c1e7
Revises: c3f7a1d84e29
Create Date: 2026-11-19

The capability catalog remains migration-managed metadata, but a catalog row
alone must not make a new operation grantable. This check is the durable
allowlist at the role-grant boundary.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "d8a4f6b2c1e7"
down_revision: Union[str, Sequence[str], None] = "c3f7a1d84e29"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_GRANTABLE_CAPABILITIES = (
    "'document:approve', 'document:ingest', 'document:read_state', "
    "'index:read', 'index:rebuild', 'ingestion:read', 'query:execute'"
)


def upgrade() -> None:
    op.create_check_constraint(
        op.f("ck_role_capabilities_capability_key_allowlist"),
        "role_capabilities",
        f"capability_key IN ({_GRANTABLE_CAPABILITIES})",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_role_capabilities_capability_key_allowlist"),
        "role_capabilities",
        type_="check",
    )

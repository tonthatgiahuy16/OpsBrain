"""restrict derived set deletion

Revision ID: d32cc82d29d5
Revises: d31e30d8807e
Create Date: 2026-10-26 19:21:36.274879

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd32cc82d29d5'
down_revision: Union[str, Sequence[str], None] = 'd31e30d8807e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("REVOKE DELETE ON TABLE derived_sets FROM opsbrain_app")


def downgrade() -> None:
    op.execute("GRANT DELETE ON TABLE derived_sets TO opsbrain_app")

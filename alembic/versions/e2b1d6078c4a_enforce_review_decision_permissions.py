"""enforce review decision application permissions

Revision ID: e2b1d6078c4a
Revises: c7f1a2b9d401
Create Date: 2026-11-04

"""
from typing import Sequence, Union

from alembic import op


revision: str = "e2b1d6078c4a"
down_revision: Union[str, Sequence[str], None] = "c7f1a2b9d401"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT, INSERT ON TABLE document_review_decisions TO opsbrain_app"
    )
    op.execute(
        "REVOKE UPDATE, DELETE ON TABLE document_review_decisions FROM opsbrain_app"
    )


def downgrade() -> None:
    op.execute(
        "GRANT UPDATE, DELETE ON TABLE document_review_decisions TO opsbrain_app"
    )

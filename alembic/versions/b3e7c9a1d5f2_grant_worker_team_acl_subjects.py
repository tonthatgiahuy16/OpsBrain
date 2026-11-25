"""Grant the ingestion worker read access to ACL team subjects.

Revision ID: b3e7c9a1d5f2
Revises: f5a8d1e3b704
Create Date: 2026-11-26

The ingestion worker resolves document ACLs on its own session:

* ``materialize_version_acl`` looks up ``allowed_teams`` in ``teams`` when a
  restricted version is published.
* Review approval re-checks the reviewer's readable set through
  ``readable_document_clause``, which joins ``teams`` and
  ``principal_team_memberships``.

Both tables were created after the explicit ``_WORKER_TABLES`` grant in
``c2d8e7a4b901`` and only received app-role grants, so these paths failed with
``permission denied``. ``ALTER DEFAULT PRIVILEGES`` only affects tables created
later, so existing tables need an explicit grant. The worker stays read-only on
team subjects; it never writes them.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "b3e7c9a1d5f2"
down_revision: Union[str, Sequence[str], None] = "f5a8d1e3b704"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "GRANT SELECT ON TABLE public.teams, public.principal_team_memberships "
        "TO opsbrain_worker"
    )


def downgrade() -> None:
    op.execute(
        "REVOKE SELECT ON TABLE public.teams, public.principal_team_memberships "
        "FROM opsbrain_worker"
    )

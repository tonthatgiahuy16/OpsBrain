"""Privileged capability grant/revoke command.

The API role cannot write ``capabilities`` or ``role_capabilities``. This
command is the supported operator path; it must run with ``MIGRATION_DATABASE_URL``
(the owner role) and refuses to run with the application role.

Examples (PowerShell, inside the compose network or against a published port):

    python -m scripts.manage_capability grant `
        --organization-key opsbrain-sample `
        --actor-principal-key sample-author `
        --role-key sample-vault-reviewer `
        --capability query:execute `
        --ensure-role `
        --assign-principal-key sample-reviewer

    python -m scripts.manage_capability revoke `
        --organization-key opsbrain-sample `
        --actor-principal-key sample-author `
        --role-key sample-vault-reviewer `
        --capability query:execute

Every actual transition appends an ``audit_events`` row with actor, role and
capability. Re-running a command is idempotent and reports ``unchanged`` or
``already_revoked`` without a second audit row.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import get_settings
from src.models.storage import Organization, Principal
from src.operations.capabilities import (
    grant_capability,
    privileged_dsn,
    revoke_capability,
)


async def _resolve_context(
    session: AsyncSession,
    *,
    organization_key: str,
    actor_principal_key: str,
) -> tuple[UUID, UUID]:
    organization = await session.scalar(
        select(Organization).where(
            Organization.organization_key == organization_key
        )
    )
    if organization is None:
        raise ValueError("organization not found")
    actor = await session.scalar(
        select(Principal).where(
            Principal.organization_id == organization.id,
            Principal.principal_key == actor_principal_key,
        )
    )
    if actor is None:
        raise ValueError("actor principal not found in organization")
    return organization.id, actor.id


async def _resolve_principal_id(
    session: AsyncSession,
    *,
    organization_id: UUID,
    principal_key: str,
) -> UUID:
    principal = await session.scalar(
        select(Principal).where(
            Principal.organization_id == organization_id,
            Principal.principal_key == principal_key,
        )
    )
    if principal is None:
        raise ValueError("principal to assign not found in organization")
    return principal.id


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Privileged, audited capability administration for OpsBrain.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_common(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--organization-key", required=True)
        subparser.add_argument("--actor-principal-key", required=True)
        subparser.add_argument("--role-key", required=True)
        subparser.add_argument("--capability", required=True)

    grant = subparsers.add_parser("grant", help="grant a capability to a role")
    add_common(grant)
    grant.add_argument(
        "--ensure-role",
        action="store_true",
        help="create the role when it does not exist yet",
    )
    grant.add_argument(
        "--assign-principal-key",
        help="also make this principal an active member of the role",
    )

    revoke = subparsers.add_parser("revoke", help="revoke an active capability")
    add_common(revoke)

    return parser


async def _run(args: argparse.Namespace) -> int:
    settings = get_settings()
    engine = create_async_engine(privileged_dsn(settings.MIGRATION_DATABASE_URL))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            async with session.begin():
                organization_id, actor_id = await _resolve_context(
                    session,
                    organization_key=args.organization_key,
                    actor_principal_key=args.actor_principal_key,
                )
                if args.command == "grant":
                    assign_id = None
                    if args.assign_principal_key:
                        assign_id = await _resolve_principal_id(
                            session,
                            organization_id=organization_id,
                            principal_key=args.assign_principal_key,
                        )
                    change = await grant_capability(
                        session,
                        organization_id=organization_id,
                        actor_principal_id=actor_id,
                        role_key=args.role_key,
                        capability_key=args.capability,
                        ensure_role=args.ensure_role,
                        assign_principal_id=assign_id,
                    )
                else:
                    change = await revoke_capability(
                        session,
                        organization_id=organization_id,
                        actor_principal_id=actor_id,
                        role_key=args.role_key,
                        capability_key=args.capability,
                    )
            print(json.dumps(asdict(change), default=str, sort_keys=True))
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    args = _parser().parse_args()
    try:
        return asyncio.run(_run(args))
    except ValueError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

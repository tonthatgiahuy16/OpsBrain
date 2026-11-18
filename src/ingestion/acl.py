"""Materialize validated frontmatter ACL subjects into immutable version grants."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.ingestion.frontmatter import CommonFrontmatter
from src.models.storage import (
    DocumentVersionAclEntry,
    Principal,
    Role,
    Team,
)


async def materialize_version_acl(
    session: AsyncSession, *, organization_id: UUID, document_pk: UUID,
    document_version_id: UUID, frontmatter: CommonFrontmatter,
) -> None:
    """Resolve restricted allowlist keys before publishing a document version."""
    if frontmatter.access_scope == "organization":
        return

    users = list((await session.scalars(select(Principal).where(
        Principal.organization_id == organization_id,
        Principal.principal_key.in_(frontmatter.allowed_users),
        Principal.principal_kind == "user",
        Principal.status == "active",
    ))).all())
    teams = list((await session.scalars(select(Team).where(
        Team.organization_id == organization_id,
        Team.team_key.in_(frontmatter.allowed_teams), Team.status == "active",
    ))).all())
    roles = list((await session.scalars(select(Role).where(
        Role.organization_id == organization_id,
        Role.role_key.in_(frontmatter.allowed_roles), Role.status == "active",
    ))).all())
    if (
        {row.principal_key for row in users} != set(frontmatter.allowed_users)
        or {row.team_key for row in teams} != set(frontmatter.allowed_teams)
        or {row.role_key for row in roles} != set(frontmatter.allowed_roles)
    ):
        raise ValueError("restricted ACL subject is not active in organization")
    session.add_all([
        *[
            DocumentVersionAclEntry(
                organization_id=organization_id, document_pk=document_pk,
                document_version_id=document_version_id, user_principal_id=row.id,
            ) for row in users
        ],
        *[
            DocumentVersionAclEntry(
                organization_id=organization_id, document_pk=document_pk,
                document_version_id=document_version_id, team_id=row.id,
            ) for row in teams
        ],
        *[
            DocumentVersionAclEntry(
                organization_id=organization_id, document_pk=document_pk,
                document_version_id=document_version_id, role_id=row.id,
            ) for row in roles
        ],
    ])
    await session.flush()


async def copy_version_acl(
    session: AsyncSession, *, organization_id: UUID, document_pk: UUID,
    source_version_id: UUID, target_version_id: UUID,
) -> None:
    """Copy a version ACL snapshot for a review-only transition."""
    entries = list((await session.scalars(select(DocumentVersionAclEntry).where(
        DocumentVersionAclEntry.organization_id == organization_id,
        DocumentVersionAclEntry.document_pk == document_pk,
        DocumentVersionAclEntry.document_version_id == source_version_id,
    ))).all())
    session.add_all([
        DocumentVersionAclEntry(
            organization_id=organization_id, document_pk=document_pk,
            document_version_id=target_version_id,
            user_principal_id=entry.user_principal_id,
            team_id=entry.team_id, role_id=entry.role_id,
        )
        for entry in entries
    ])
    await session.flush()

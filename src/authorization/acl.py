"""Resolve current document visibility before retrieval can rank candidates."""

from uuid import UUID

from sqlalchemy import exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.storage import (
    Document,
    DocumentVersion,
    DocumentVersionAclEntry,
    PrincipalRoleMembership,
    PrincipalTeamMembership,
    Role,
    Team,
)


async def resolve_readable_document_pks(
    session: AsyncSession, *, organization_id: UUID, principal_id: UUID,
) -> set[UUID]:
    """Return current documents readable by a principal in one organization.

    Organization-scoped documents are visible to an authenticated principal in
    the organization. Restricted documents require an exact direct grant or an
    active membership in an active team or role granted on the current version.
    The result is an allowlist intended for candidate filtering before ranking.
    """
    active_team_ids = (
        select(PrincipalTeamMembership.team_id)
        .join(Team, Team.id == PrincipalTeamMembership.team_id)
        .where(
            PrincipalTeamMembership.organization_id == organization_id,
            PrincipalTeamMembership.principal_id == principal_id,
            PrincipalTeamMembership.status == "active",
            Team.organization_id == organization_id,
            Team.status == "active",
        )
    )
    active_role_ids = (
        select(PrincipalRoleMembership.role_id)
        .join(Role, Role.id == PrincipalRoleMembership.role_id)
        .where(
            PrincipalRoleMembership.organization_id == organization_id,
            PrincipalRoleMembership.principal_id == principal_id,
            PrincipalRoleMembership.status == "active",
            Role.organization_id == organization_id,
            Role.status == "active",
        )
    )
    matching_restricted_grant = exists(
        select(DocumentVersionAclEntry.id).where(
            DocumentVersionAclEntry.organization_id == organization_id,
            DocumentVersionAclEntry.document_pk == Document.id,
            DocumentVersionAclEntry.document_version_id == DocumentVersion.id,
            or_(
                DocumentVersionAclEntry.user_principal_id == principal_id,
                DocumentVersionAclEntry.team_id.in_(active_team_ids),
                DocumentVersionAclEntry.role_id.in_(active_role_ids),
            ),
        )
    )
    return set((await session.scalars(
        select(Document.id)
        .join(DocumentVersion, DocumentVersion.id == Document.current_version_id)
        .where(
            Document.organization_id == organization_id,
            DocumentVersion.organization_id == organization_id,
            or_(
                DocumentVersion.access_scope == "organization",
                (DocumentVersion.access_scope == "restricted") & matching_restricted_grant,
            ),
        )
    )).all())

"""Database-level organization isolation for the first RLS slice."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from src.authorization.rls import set_rls_context


async def _seed_organizations(connection):
    organization_a_id = uuid4()
    organization_b_id = uuid4()
    principal_a_id = uuid4()
    disabled_principal_id = uuid4()
    disabled_organization_principal_id = uuid4()
    disabled_organization_id = uuid4()
    await connection.execute(
        text(
            """
            INSERT INTO organizations (id, organization_key, display_name, status)
            VALUES
              (:organization_a_id, :organization_a_key, 'Organization A', 'active'),
              (:organization_b_id, :organization_b_key, 'Organization B', 'active'),
              (:disabled_organization_id, :disabled_organization_key, 'Disabled', 'disabled')
            """
        ),
        {
            "organization_a_id": organization_a_id,
            "organization_a_key": f"rls-a-{organization_a_id}",
            "organization_b_id": organization_b_id,
            "organization_b_key": f"rls-b-{organization_b_id}",
            "disabled_organization_id": disabled_organization_id,
            "disabled_organization_key": f"rls-disabled-{disabled_organization_id}",
        },
    )
    await connection.execute(
        text(
            """
            INSERT INTO principals (
                id, organization_id, principal_key, principal_kind, display_name, status
            ) VALUES
              (:principal_a_id, :organization_a_id, :principal_a_key, 'user', 'Principal A', 'active'),
              (:disabled_principal_id, :organization_a_id, :disabled_principal_key, 'user', 'Disabled principal', 'disabled'),
              (:disabled_organization_principal_id, :disabled_organization_id, :disabled_organization_principal_key, 'user', 'Disabled organization principal', 'active')
            """
        ),
        {
            "principal_a_id": principal_a_id,
            "organization_a_id": organization_a_id,
            "principal_a_key": f"rls-principal-a-{principal_a_id}",
            "disabled_principal_id": disabled_principal_id,
            "disabled_principal_key": f"rls-principal-disabled-{disabled_principal_id}",
            "disabled_organization_id": disabled_organization_id,
            "disabled_organization_principal_id": disabled_organization_principal_id,
            "disabled_organization_principal_key": f"rls-principal-disabled-org-{disabled_organization_principal_id}",
        },
    )
    for organization_id, suffix in ((organization_a_id, "a"), (organization_b_id, "b")):
        await connection.execute(
            text(
                """
                INSERT INTO documents (
                    id, organization_id, document_id, document_type, current_source_uri, record_state
                ) VALUES (:id, :organization_id, :document_id, 'runbook', :source_uri, 'active')
                """
            ),
            {
                "id": uuid4(),
                "organization_id": organization_id,
                "document_id": f"rls-document-{suffix}-{organization_id}",
                "source_uri": f"file:///rls/{suffix}/{organization_id}.md",
            },
        )
    return (
        organization_a_id,
        organization_b_id,
        principal_a_id,
        disabled_principal_id,
        disabled_organization_id,
        disabled_organization_principal_id,
    )


@pytest.mark.asyncio
async def test_document_rls_is_fail_closed_and_organization_scoped(
    migration_database_engine: AsyncEngine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        committed_seed = False
        try:
            organization_a_id, organization_b_id, principal_a_id, *_ = await _seed_organizations(connection)
            await connection.execute(text("SET LOCAL ROLE opsbrain_app"))

            assert await connection.scalar(text("SELECT count(*) FROM documents")) == 0
            with pytest.raises(DBAPIError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO documents (
                                id, organization_id, document_id, document_type, current_source_uri, record_state
                            ) VALUES (:id, :organization_id, :document_id, 'runbook', :source_uri, 'active')
                            """
                        ),
                        {
                            "id": uuid4(), "organization_id": organization_a_id,
                            "document_id": f"no-context-{uuid4()}",
                            "source_uri": f"file:///rls/no-context-{uuid4()}.md",
                        },
                    )

            await set_rls_context(
                connection, organization_id=organization_a_id, principal_id=principal_a_id
            )
            # The seed documents are pending in this same transaction.  This
            # narrow visibility is required for ORM INSERT ... RETURNING; a
            # committed document is always ACL-gated by its current version.
            assert await connection.scalar(text("SELECT count(*) FROM documents")) == 1
            assert await connection.scalar(
                text("SELECT count(*) FROM documents WHERE organization_id = :organization_id"),
                {"organization_id": organization_b_id},
            ) == 0
            with pytest.raises(DBAPIError):
                async with connection.begin_nested():
                    await connection.execute(
                        text(
                            """
                            INSERT INTO documents (
                                id, organization_id, document_id, document_type, current_source_uri, record_state
                            ) VALUES (:id, :organization_id, :document_id, 'runbook', :source_uri, 'active')
                            """
                        ),
                        {
                            "id": uuid4(), "organization_id": organization_b_id,
                            "document_id": f"cross-org-{uuid4()}",
                            "source_uri": f"file:///rls/cross-org-{uuid4()}.md",
                        },
                    )
        finally:
            await transaction.rollback()


@pytest.mark.asyncio
async def test_auth_function_filters_inactive_principals_and_worker_bypasses_rls(
    migration_database_engine: AsyncEngine,
    worker_database_engine: AsyncEngine,
):
    async with migration_database_engine.connect() as connection:
        transaction = await connection.begin()
        committed_seed = False
        try:
            (
                organization_a_id,
                organization_b_id,
                principal_a_id,
                disabled_principal_id,
                disabled_organization_id,
                disabled_organization_principal_id,
            ) = await _seed_organizations(connection)
            await connection.execute(text("SET LOCAL ROLE opsbrain_app"))
            assert await connection.scalar(
                text("SELECT id FROM app_authenticate_principal(:principal_id)"),
                {"principal_id": principal_a_id},
            ) == principal_a_id
            assert await connection.scalar(
                text("SELECT id FROM app_authenticate_principal(:principal_id)"),
                {"principal_id": disabled_principal_id},
            ) is None
            assert await connection.scalar(
                text("SELECT id FROM app_authenticate_principal(:principal_id)"),
                {"principal_id": disabled_organization_principal_id},
            ) is None
            await connection.execute(text("SELECT set_config('app.worker_mode', 'true', true)"))
            assert await connection.scalar(text("SELECT count(*) FROM documents")) == 0
            await set_rls_context(
                connection, organization_id=organization_a_id, principal_id=principal_a_id
            )
            assert await connection.scalar(text("SELECT count(*) FROM documents")) == 1
            await transaction.commit()
            committed_seed = True

            async with worker_database_engine.connect() as worker_connection:
                assert await worker_connection.scalar(
                    text(
                        "SELECT count(*) FROM documents "
                        "WHERE organization_id IN (:organization_a_id, :organization_b_id)"
                    ),
                    {
                        "organization_a_id": organization_a_id,
                        "organization_b_id": organization_b_id,
                    },
                ) == 2

            # Transaction-local context must not survive a request boundary.
            transaction = await connection.begin()
            await connection.execute(text("SET LOCAL ROLE opsbrain_app"))
            assert await connection.scalar(text("SELECT count(*) FROM documents")) == 0
        finally:
            await transaction.rollback()
            if committed_seed:
                async with migration_database_engine.begin() as cleanup:
                    # These rows were committed solely to prove a separately
                    # connected worker can read them. Existing immutable-
                    # document triggers intentionally forbid normal deletion.
                    await cleanup.execute(text("SET LOCAL session_replication_role = replica"))
                    await cleanup.execute(
                        text("DELETE FROM documents WHERE organization_id = :organization_id"),
                        {"organization_id": organization_a_id},
                    )
                    await cleanup.execute(
                        text("DELETE FROM documents WHERE organization_id = :organization_id"),
                        {"organization_id": organization_b_id},
                    )
                    await cleanup.execute(
                        text("DELETE FROM principals WHERE organization_id = :organization_id"),
                        {"organization_id": organization_a_id},
                    )
                    await cleanup.execute(
                        text("DELETE FROM principals WHERE organization_id = :organization_id"),
                        {"organization_id": disabled_organization_id},
                    )
                    await cleanup.execute(
                        text("DELETE FROM organizations WHERE id IN (:organization_a_id, :organization_b_id, :disabled_organization_id)"),
                        {
                            "organization_a_id": organization_a_id,
                            "organization_b_id": organization_b_id,
                            "disabled_organization_id": disabled_organization_id,
                        },
                    )

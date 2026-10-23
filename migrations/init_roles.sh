#!/usr/bin/env bash
set -Eeuo pipefail

: "${OPSBRAIN_APP_USER:?OPSBRAIN_APP_USER must be set}"
: "${OPSBRAIN_APP_PASSWORD:?OPSBRAIN_APP_PASSWORD must be set}"

psql \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=ON_ERROR_STOP=1 \
  --set=owner_role="$POSTGRES_USER" \
  --set=app_role="$OPSBRAIN_APP_USER" \
  --set=app_password="$OPSBRAIN_APP_PASSWORD" <<'SQL'
SELECT format(
    'CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT PASSWORD %L',
    :'app_role',
    :'app_password'
)
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_roles
    WHERE rolname = :'app_role'
)
\gexec

SELECT format(
    'GRANT CONNECT ON DATABASE %I TO %I',
    current_database(),
    :'app_role'
)
\gexec

GRANT USAGE ON SCHEMA public TO :"app_role";
REVOKE CREATE ON SCHEMA public FROM :"app_role";

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO %I',
    :'owner_role',
    :'app_role'
)
\gexec

SELECT format(
    'ALTER DEFAULT PRIVILEGES FOR ROLE %I IN SCHEMA public '
    'GRANT USAGE, SELECT, UPDATE ON SEQUENCES TO %I',
    :'owner_role',
    :'app_role'
)
\gexec
SQL

#!/usr/bin/env bash
# =============================================================================
# provision-db.sh — OPTIONAL hardening. The app creates its own `expenses` schema on
# start, using whichever database user it is given (normally TeslaMate's). Run this
# instead to give it a least-privilege role (set SKIP_MIGRATE=1 on the app): the role
# can write expenses and read only `cars` and `positions(car_id, date, odometer)`.
#
# Idempotent: safe on every deploy. Runs psql *inside* the database container
# over its local socket, so no database superuser password is needed here, and
# the app role's password is sent on stdin (never in argv, where `ps` could see
# it).
#
#   EXPENSES_DB_PASS=$(openssl rand -hex 24) ./scripts/provision-db.sh
#
# Environment:
#   EXPENSES_DB_PASS   required, [A-Za-z0-9_-], 16+ chars
#   EXPENSES_DB_USER   app role name              (default expenses_app)
#   DB_CONTAINER       TeslaMate database container (default teslamate-database-1)
#   DB_USER / DB_NAME  superuser + database; default to the container's own
#                      POSTGRES_USER / POSTGRES_DB
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DB_CONTAINER="${DB_CONTAINER:-teslamate-database-1}"
APP_ROLE="${EXPENSES_DB_USER:-expenses_app}"
PASS="${EXPENSES_DB_PASS:-}"

die() { echo "provision-db: $*" >&2; exit 1; }

[[ -n "$PASS" ]] || die "EXPENSES_DB_PASS is not set (generate one: openssl rand -hex 24)"
[[ "$PASS" =~ ^[A-Za-z0-9_-]{16,}$ ]] || die "EXPENSES_DB_PASS must be 16+ chars of [A-Za-z0-9_-]"
[[ "$APP_ROLE" =~ ^[a-z_][a-z0-9_]*$ ]] || die "EXPENSES_DB_USER must be a plain lowercase identifier"

# Same defaults as the postgres image: user "postgres", database named after the user.
DB_USER="${DB_USER:-$(docker exec "$DB_CONTAINER" printenv POSTGRES_USER 2>/dev/null || echo postgres)}"
DB_NAME="${DB_NAME:-$(docker exec "$DB_CONTAINER" printenv POSTGRES_DB 2>/dev/null || echo "$DB_USER")}"

{
    printf "\\\\set pw '%s'\n" "$PASS"
    cat <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L CONNECTION LIMIT 10', :'app_role', :'pw')
 WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'app_role') \gexec
SELECT format('ALTER ROLE %I LOGIN PASSWORD %L', :'app_role', :'pw') \gexec
SQL
    cat "$ROOT/garagebooks/schema.sql" "$ROOT/db/grants.sql"
} | docker exec -i "$DB_CONTAINER" \
        psql -X -q -1 -v ON_ERROR_STOP=1 -v "app_role=$APP_ROLE" -U "$DB_USER" -d "$DB_NAME" >/dev/null

echo "provision-db: schema 'expenses' and role '$APP_ROLE' ready in $DB_NAME@$DB_CONTAINER"

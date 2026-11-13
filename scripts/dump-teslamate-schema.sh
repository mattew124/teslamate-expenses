#!/usr/bin/env bash
# Prints the structure (no rows) of the TeslaMate tables this project reads, as defined by the
# Postgres container named in $1: a live TeslaMate database, for the demo stack. Read-only, and
# the output is for a local throwaway database, never committed.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
container="${1:?usage: dump-teslamate-schema.sh CONTAINER}"
cat <<'HEADER'
-- The tables this project reads, as defined by the TeslaMate database dumped. Foreign keys
-- to tables that aren't copied are removed; see scripts/filter-schema-dump.awk.

-- Some TeslaMate databases revoke this from PUBLIC. Doing the same here means
-- db/grants.sql can't quietly depend on the stock Postgres default.
REVOKE ALL ON SCHEMA public FROM PUBLIC;

HEADER
docker exec "$container" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --schema-only --no-owner --no-privileges -t public.cars -t public.positions -t public.charging_processes -t public.schema_migrations' \
    | awk -f scripts/filter-schema-dump.awk

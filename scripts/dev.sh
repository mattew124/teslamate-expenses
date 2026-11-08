#!/usr/bin/env bash
# =============================================================================
# dev.sh — run the dev stack on its own test database.
#
#   scripts/dev.sh setup             first run in a new checkout: venv + local files check
#   scripts/dev.sh info              this checkout's stack: name, URL path, ports
#   scripts/dev.sh up [--image REF]  start; first run creates and seeds the test DB.
#                                    --image runs a released image instead of
#                                    building this working tree (test a candidate)
#   scripts/dev.sh refresh           re-copy TeslaMate data, keeping test expenses
#   scripts/dev.sh reset             wipe the test DB, test expenses included
#   scripts/dev.sh down              stop, keeping the test DB
#   scripts/dev.sh destroy           remove this checkout's stack, test DB and image
#   scripts/dev.sh list              every dev stack on this machine
#   scripts/dev.sh test-browser      run the browser tests against the dev stack
#   scripts/dev.sh schema            regenerate db/teslamate-schema.sql from TeslaMate
#   scripts/dev.sh psql [args]       psql on the test database
#   scripts/dev.sh logs              follow the app's logs
#
# Settings come from ./dev.env (gitignored; see dev.env.example).
#
# Each git worktree gets its own stack, test DB and URL path
# (/expenses-dev/<worktree>/), so parallel sessions can't replace each other's
# running code. The main checkout keeps /expenses-dev/. DEV_INSTANCE overrides
# the name.
#
# The seed only READS TeslaMate (pg_dump, COPY TO STDOUT). It copies the full
# cars and charging_processes tables and one positions row per hour, with
# latitude, longitude and elevation blanked, so no location history reaches the
# test database. Throwaway passwords live in .dev/env (gitignored, 0600).
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [ -f dev.env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./dev.env
    set +a
fi
export APP_TZ="${APP_TZ:-UTC}"
SEED_FROM="${SEED_FROM:-teslamate-database-1}"
SCHEMA=db/teslamate-schema.sql
ENVF=.dev/env

# ---- which dev stack this checkout owns ----------------------------------------

instance_name() {
    if [ -n "${DEV_INSTANCE:-}" ]; then
        printf '%s' "$DEV_INSTANCE"
    elif [ "$(git rev-parse --git-dir 2>/dev/null)" != "$(git rev-parse --git-common-dir 2>/dev/null)" ]; then
        basename "$ROOT"   # a linked worktree: named after its folder
    fi
}

INSTANCE="$(instance_name | tr 'A-Z_' 'a-z-' | tr -c 'a-z0-9-' '-' | sed 's/^-*//; s/-*$//' | cut -c1-30)"
if [ -n "$(instance_name)" ] && { [ -z "$INSTANCE" ] || [ "$INSTANCE" = grafana ]; }; then
    echo "dev: can't derive a usable stack name from '$(instance_name)'; set DEV_INSTANCE" >&2
    exit 2
fi
if [ -z "$INSTANCE" ]; then
    PROJECT=teslamate-expenses-dev
    export DEV_BASE_PATH=/expenses-dev DEV_ROUTER_SUFFIX= DEV_TAG=dev
    export DEV_UI_PRIORITY=110 DEV_GRAFANA_PRIORITY=120
    export DEV_UI_PORT="${DEV_UI_PORT:-8089}" DEV_GRAFANA_PORT="${DEV_GRAFANA_PORT:-8090}"
else
    PROJECT="teslamate-expenses-dev-$INSTANCE"
    export DEV_BASE_PATH="/expenses-dev/$INSTANCE" DEV_ROUTER_SUFFIX="-$INSTANCE" DEV_TAG="dev-$INSTANCE"
    # Above the main stack's routes: /expenses-dev/ is a prefix of this path.
    export DEV_UI_PRIORITY=130 DEV_GRAFANA_PRIORITY=140
    # A stable pair of loopback ports per name, clear of the main stack's.
    slot=$(( $(printf '%s' "$INSTANCE" | cksum | cut -d' ' -f1) % 400 ))
    export DEV_UI_PORT=$((18100 + 2 * slot)) DEV_GRAFANA_PORT=$((18101 + 2 * slot))
fi

compose() {
    local files=(-f docker-compose.dev.yml)
    # PUBLIC_HOST set: publish through the reverse proxy instead of on loopback.
    [ -z "${PUBLIC_HOST:-}" ] || files+=(-f docker-compose.dev.traefik.yml)
    docker compose -p "$PROJECT" "${files[@]}" --env-file "$ENVF" "$@"
}
dev_psql() { compose exec -T dev-db psql -X -q -v ON_ERROR_STOP=1 -U teslamate -d teslamate "$@"; }

ensure_env() {
    mkdir -p .dev/dashboards .dev/screenshots
    if [ ! -f "$ENVF" ]; then
        ( umask 077
          printf 'DEV_APP_DB_PASS=%s\nDEV_ADMIN_DB_PASS=%s\nDEV_GRAFANA_PASS=%s\n' \
              "$(openssl rand -hex 24)" "$(openssl rand -hex 24)" "$(openssl rand -hex 24)" > "$ENVF" )
    fi
    # Dashboard copy with the app link kept inside dev and the timezone defaulted.
    sed -e "s#\"url\": \"/expenses/\"#\"url\": \"$DEV_BASE_PATH/\"#" \
        -e "s#\"\(query\|text\|value\)\": \"UTC\"#\"\1\": \"$APP_TZ\"#" \
        grafana/cost-of-ownership.json > .dev/dashboards/cost-of-ownership.json
}

# ---- reading TeslaMate (never writes) ----------------------------------------

source_psql() {
    docker exec -e "SQL=$1" "$SEED_FROM" \
        sh -c 'psql -X -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$SQL"'
}

require_source() {
    docker inspect "$SEED_FROM" >/dev/null 2>&1 || {
        echo "dev: TeslaMate database container '$SEED_FROM' not found (set SEED_FROM in dev.env)" >&2
        exit 1
    }
}

dump_schema() {
    {
        cat <<'HEADER'
-- GENERATED by `scripts/dev.sh schema`: do not edit by hand.
-- The tables this project reads, as defined by TeslaMate
-- (https://github.com/teslamate-org/teslamate, AGPL-3.0). Foreign keys to tables
-- that aren't copied are removed; see scripts/filter-schema-dump.awk.

-- Some TeslaMate databases revoke this from PUBLIC. Doing the same here means
-- db/grants.sql can't quietly depend on the stock Postgres default.
REVOKE ALL ON SCHEMA public FROM PUBLIC;

HEADER
        docker exec "$SEED_FROM" sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --schema-only --no-owner --no-privileges -t public.cars -t public.positions -t public.charging_processes' \
            | awk -f scripts/filter-schema-dump.awk
    }
}

warn_if_schema_drifted() {
    if ! dump_schema | cmp -s - "$SCHEMA"; then
        echo "dev: WARNING: TeslaMate's tables differ from $SCHEMA." >&2
        echo "dev:          Run 'scripts/dev.sh schema', review the diff, then 'scripts/dev.sh reset'." >&2
    fi
}

columns_of() {
    source_psql "SELECT string_agg(quote_ident(column_name), ',' ORDER BY ordinal_position)
                 FROM information_schema.columns
                 WHERE table_schema = 'public' AND table_name = '$1'"
}

# Same columns, with everything that reveals a location blanked.
position_select_list() {
    source_psql "SELECT string_agg(CASE column_name
                     WHEN 'latitude'  THEN '0'
                     WHEN 'longitude' THEN '0'
                     WHEN 'elevation' THEN 'NULL'
                     ELSE quote_ident(column_name) END, ',' ORDER BY ordinal_position)
                 FROM information_schema.columns
                 WHERE table_schema = 'public' AND table_name = 'positions'"
}

# ---- loading the test database -------------------------------------------------

copy_data() {
    require_source
    local car_cols charge_cols position_cols position_select
    car_cols="$(columns_of cars)"
    charge_cols="$(columns_of charging_processes)"
    position_cols="$(columns_of positions)"
    position_select="$(position_select_list)"
    echo "dev: copying TeslaMate data from $SEED_FROM (read-only)..."
    # One transaction: if any step fails, EOF without COMMIT rolls it all back.
    # cars is merged, not truncated: test expenses reference it.
    {
        cat <<SQL
BEGIN;
TRUNCATE public.positions, public.charging_processes;
CREATE TEMP TABLE incoming_cars (LIKE public.cars) ON COMMIT DROP;
COPY incoming_cars ($car_cols) FROM STDIN;
SQL
        source_psql "COPY (SELECT $car_cols FROM cars) TO STDOUT"
        cat <<SQL
\\.
INSERT INTO public.cars SELECT * FROM incoming_cars ON CONFLICT (id) DO NOTHING;
COPY public.charging_processes ($charge_cols) FROM STDIN;
SQL
        source_psql "COPY (SELECT $charge_cols FROM charging_processes) TO STDOUT"
        cat <<SQL
\\.
COPY public.positions ($position_cols) FROM STDIN;
SQL
        source_psql "COPY (SELECT DISTINCT ON (car_id, date_trunc('hour', date)) $position_select
                           FROM positions WHERE odometer IS NOT NULL
                           ORDER BY car_id, date_trunc('hour', date), date) TO STDOUT"
        cat <<SQL
\\.
DO \$\$ BEGIN
    IF EXISTS (SELECT 1 FROM public.positions
               WHERE latitude <> 0 OR longitude <> 0 OR elevation IS NOT NULL) THEN
        RAISE EXCEPTION 'location data reached the test database; rolling back';
    END IF;
END \$\$;
COMMIT;
SQL
    } | dev_psql
    dev_psql -At -c "SELECT 'dev: test DB has ' || (SELECT count(*) FROM public.cars) || ' car(s), '
        || (SELECT count(*) FROM public.charging_processes) || ' charges, '
        || (SELECT count(*) FROM public.positions) || ' hourly odometer readings, '
        || (SELECT count(*) FROM expenses.expense) || ' test expense(s)'"
}

prepare_database() {
    compose up -d --wait dev-db
    local first_run=no pass
    if [ "$(dev_psql -At -c "SELECT to_regclass('public.cars') IS NULL")" = t ]; then
        first_run=yes
        dev_psql < "$SCHEMA"
    fi
    # shellcheck disable=SC1090
    pass="$(. "$ENVF"; printf '%s' "$DEV_APP_DB_PASS")"
    DB_CONTAINER="$(compose ps -q dev-db)" EXPENSES_DB_PASS="$pass" scripts/provision-db.sh
    [ "$first_run" = no ] || copy_data
}

start() {
    local image="${1:-}"
    ensure_env
    prepare_database
    if [ -n "$image" ]; then
        export EXPENSES_IMAGE="$image"
        compose pull expenses-dev
        compose up -d --no-build --wait
    else
        compose up -d --build --wait
    fi
    if docker inspect "$SEED_FROM" >/dev/null 2>&1; then warn_if_schema_drifted; fi
    urls
}

urls() {
    local base
    if [ -n "${PUBLIC_HOST:-}" ]; then base="https://$PUBLIC_HOST"; else base="http://localhost:$DEV_UI_PORT"; fi
    echo "dev: UI      $base$DEV_BASE_PATH/"
    [ -n "${PUBLIC_HOST:-}" ] || base="http://localhost:$DEV_GRAFANA_PORT"
    echo "dev: Grafana $base$DEV_BASE_PATH/grafana/"
}

info() {
    cat <<INFO
project=$PROJECT
instance=${INSTANCE:-main}
base_path=$DEV_BASE_PATH
ui_port=$DEV_UI_PORT
grafana_port=$DEV_GRAFANA_PORT
image=${EXPENSES_IMAGE:-teslamate-expenses:$DEV_TAG}
router_suffix=${DEV_ROUTER_SUFFIX:-none}
ui_priority=$DEV_UI_PRIORITY
grafana_priority=$DEV_GRAFANA_PRIORITY
INFO
}

setup() {
    if [ ! -x .venv/bin/python ]; then
        python3 -m venv .venv
        .venv/bin/pip install -q -r requirements-dev.txt
    fi
    echo "dev: .venv ready"
    [ -f dev.env ] || echo "dev: no dev.env, so this stack is on localhost with defaults (see dev.env.example)"
    [ -e CLAUDE.local.md ] || echo "dev: no CLAUDE.local.md here (optional, local notes for Claude)"
    urls
}

test_browser() {
    ensure_env
    docker build -q -t teslamate-expenses:browser-tests tests/browser >/dev/null
    # Straight to the containers on the dev network, past the proxy's login.
    docker run --rm --network "${PROJECT}_dev" --user "$(id -u):$(id -g)" \
        -e HOME=/tmp -e OUT=/out -e "BASE_PATH=$DEV_BASE_PATH" \
        -v "$ROOT/tests/browser:/tests:ro" -v "$ROOT/.dev/screenshots:/out" \
        teslamate-expenses:browser-tests python3 /tests/run.py
}

case "${1:-}" in
    up)
        if [ "${2:-}" = --image ]; then start "${3:?--image needs an image reference}"
        else start ""; fi ;;
    refresh) ensure_env; warn_if_schema_drifted; copy_data ;;
    reset) ensure_env; compose down -v; start "" ;;
    down) ensure_env; compose down ;;
    destroy)
        ensure_env
        compose down -v --remove-orphans
        docker image rm "teslamate-expenses:$DEV_TAG" >/dev/null 2>&1 || true
        echo "dev: removed $PROJECT" ;;
    list) docker compose ls -a --filter name=teslamate-expenses-dev ;;
    setup) setup ;;
    info) info ;;
    test-browser) test_browser ;;
    schema)
        require_source
        tmp="$(mktemp)"
        dump_schema > "$tmp"
        mv "$tmp" "$SCHEMA"
        chmod 644 "$SCHEMA"
        git --no-pager diff --stat -- "$SCHEMA" || true ;;
    psql) ensure_env; shift; dev_psql "$@" ;;
    logs) ensure_env; compose logs -f expenses-dev ;;
    *) sed -n '3,17p' "$0" >&2; exit 2 ;;
esac

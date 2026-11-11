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
#   scripts/dev.sh sample-expenses [clear]
#                                    add made-up expenses to the test DB to try the app with
#                                    (re-run to refresh them; `clear` removes only those)
#   scripts/dev.sh destroy           remove this checkout's stack, test DB, image and .dev folder
#   scripts/dev.sh list              every dev stack on this machine
#   scripts/dev.sh test-browser [--onedrive]
#                                    run the browser tests against the dev stack;
#                                    --onedrive fails if the real OneDrive checks can't run
#   scripts/dev.sh onedrive-save     keep this stack's OneDrive sign-in in a file outside
#                                    the repository (ONEDRIVE_DEV_FILE)
#   scripts/dev.sh onedrive-link     load that sign-in into this stack, pointed at the
#                                    /Expenses test folder
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

# CLAUDE_LOCAL_SOURCE (dev.env): keep CLAUDE.local.md a plain copy of a file kept
# elsewhere. A copy, not a symlink, so .worktreeinclude carries it into new
# worktrees and it loads there at session start.
sync_local_notes() {
    local src="${CLAUDE_LOCAL_SOURCE:-}"
    [ -n "$src" ] || return 0
    [ -f "$src" ] || { echo "dev: CLAUDE_LOCAL_SOURCE $src not found" >&2; return 0; }
    if [ -L CLAUDE.local.md ] || ! cmp -s "$src" CLAUDE.local.md; then
        rm -f CLAUDE.local.md
        cp "$src" CLAUDE.local.md
        echo "dev: CLAUDE.local.md refreshed from $src"
    fi
}

ensure_env() {
    sync_local_notes
    mkdir -p .dev/screenshots
    if [ ! -f "$ENVF" ]; then
        ( umask 077
          printf 'DEV_ADMIN_DB_PASS=%s\nDEV_GRAFANA_PASS=%s\n' \
              "$(openssl rand -hex 24)" "$(openssl rand -hex 24)" > "$ENVF" )
    fi
    # Added later than the passwords, so older .dev/env files get it too.
    # (Renamed from DEV_ONEDRIVE_TOKEN_KEY: keep the old value so saved dev sign-ins still decrypt.)
    grep -q '^DEV_ENCRYPTION_KEY=' "$ENVF" || {
        old=$(sed -n 's/^DEV_ONEDRIVE_TOKEN_KEY=//p' "$ENVF")
        printf 'DEV_ENCRYPTION_KEY=%s\n' "${old:-$(openssl rand -base64 32 | tr '+/' '-_')}" >> "$ENVF"
    }
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

# TeslaMate's own tables, as TeslaMate would have created them. Our schema is not
# loaded here: the app creates it when it starts, exactly as it does in production.
# Sets FIRST_RUN for seed_data.
prepare_database() {
    compose up -d --wait dev-db
    FIRST_RUN=no
    if [ "$(dev_psql -At -c "SELECT to_regclass('public.cars') IS NULL")" = t ]; then
        FIRST_RUN=yes
        dev_psql < "$SCHEMA"
    fi
}

seed_data() {
    [ "$FIRST_RUN" = yes ] || return 0
    copy_data
    seed_settings
}

# What you would type on the app's Settings page, for this stack's own addresses.
seed_settings() {
    local app graf
    if [ -n "${PUBLIC_HOST:-}" ]; then
        app="https://$PUBLIC_HOST$DEV_BASE_PATH/"; graf="https://$PUBLIC_HOST$DEV_BASE_PATH/grafana/"
    else
        app="http://localhost:$DEV_UI_PORT$DEV_BASE_PATH/"
        graf="http://localhost:$DEV_GRAFANA_PORT$DEV_BASE_PATH/grafana/"
    fi
    dev_psql -q -v "app=$app" -v "graf=$graf" <<'SQL'
UPDATE expenses.setting SET expenses_url = :'app', grafana_url = :'graf' WHERE id = 1;
SQL
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
    seed_data
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
    sync_local_notes
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
    local require_onedrive="${1:-0}"
    ensure_env
    docker build -q -t teslamate-expenses:browser-tests tests/browser >/dev/null
    # Straight to the containers on the dev network, past the proxy's login.
    docker run --rm --network "${PROJECT}_dev" --user "$(id -u):$(id -g)" \
        -e HOME=/tmp -e OUT=/out -e "BASE_PATH=$DEV_BASE_PATH" -e "REQUIRE_ONEDRIVE=$require_onedrive" \
        -v "$ROOT/tests/browser:/tests:ro" -v "$ROOT/.dev/screenshots:/out" \
        teslamate-expenses:browser-tests python3 /tests/run.py
}

# ---- a real OneDrive for dev stacks ---------------------------------------------

# One saved sign-in, in a file any worktree can load, so no other stack has to stay around.
# It holds the encryption key beside what it encrypts, so it opens the real account: keep it
# private (0600) and out of the repository.
ONEDRIVE_DEV_FILE="${ONEDRIVE_DEV_FILE:-$HOME/.config/teslamate-expenses/onedrive-dev}"

onedrive_file_check() {
    local repo
    repo="$(dirname "$(git rev-parse --path-format=absolute --git-common-dir)")"
    case "$ONEDRIVE_DEV_FILE" in
        "$repo"/*|"$ROOT"/*)
            echo "dev: ONEDRIVE_DEV_FILE must be outside the repository: $ONEDRIVE_DEV_FILE" >&2
            exit 1 ;;
    esac
}

# Sign-in only, never the car folders: a stack's folder may hold real invoices.
onedrive_save() {
    onedrive_file_check
    ensure_env
    [ -n "$(compose ps -q dev-db)" ] || { echo "dev: start this stack first: scripts/dev.sh up" >&2; exit 1; }
    local row key
    row="$(dev_psql -At -c "COPY (SELECT client_id, client_secret, secret_expires, account, refresh_token FROM expenses.onedrive_account WHERE refresh_token IS NOT NULL) TO STDOUT")"
    [ -n "$row" ] || { echo "dev: this stack isn't signed in: connect it on its OneDrive page first" >&2; exit 1; }
    key="$(sed -n 's/^DEV_ENCRYPTION_KEY=//p' "$ENVF")"
    ( umask 077
      mkdir -p "$(dirname "$ONEDRIVE_DEV_FILE")"
      printf 'DEV_ENCRYPTION_KEY=%s\nONEDRIVE_ROW=%s\n' "$key" "$row" > "$ONEDRIVE_DEV_FILE.new"
      mv "$ONEDRIVE_DEV_FILE.new" "$ONEDRIVE_DEV_FILE" )
    echo "dev: OneDrive sign-in saved to $ONEDRIVE_DEV_FILE"
}

# Then points every car here at the test folder. Secrets stay encrypted and are never printed.
onedrive_link() {
    onedrive_file_check
    ensure_env
    [ -f "$ONEDRIVE_DEV_FILE" ] || {
        echo "dev: no saved sign-in at $ONEDRIVE_DEV_FILE: connect a stack on its OneDrive page, then run onedrive-save there" >&2
        exit 1
    }
    local app key row current
    app="$(compose ps -q expenses-dev)"
    [ -n "$app" ] || { echo "dev: start this stack first: scripts/dev.sh up" >&2; exit 1; }
    key="$(sed -n 's/^DEV_ENCRYPTION_KEY=//p' "$ONEDRIVE_DEV_FILE")"
    row="$(sed -n 's/^ONEDRIVE_ROW=//p' "$ONEDRIVE_DEV_FILE")"
    [ -n "$key" ] && [ -n "$row" ] || { echo "dev: $ONEDRIVE_DEV_FILE is incomplete: run onedrive-save again" >&2; exit 1; }
    # The row is encrypted with that key, so this stack uses the same one.
    current="$(sed -n 's/^DEV_ENCRYPTION_KEY=//p' "$ENVF")"
    if [ "$key" != "$current" ]; then
        ( umask 077
          grep -v '^DEV_ENCRYPTION_KEY=' "$ENVF" > "$ENVF.new"
          printf 'DEV_ENCRYPTION_KEY=%s\n' "$key" >> "$ENVF.new"
          mv "$ENVF.new" "$ENVF" )
        # Same image (it may be `up --image`), new key.
        EXPENSES_IMAGE="$(docker inspect -f '{{.Config.Image}}' "$app")"
        export EXPENSES_IMAGE
        compose up -d --no-build --wait expenses-dev
    fi
    {
        cat <<'SQL'
BEGIN;
DELETE FROM expenses.car_folder;
CREATE TEMP TABLE incoming (client_id text, client_secret text, secret_expires date, account text, refresh_token text) ON COMMIT DROP;
COPY incoming FROM STDIN;
SQL
        printf '%s\n' "$row"
        cat <<'SQL'
\.
INSERT INTO expenses.onedrive_account (id, client_id, client_secret, secret_expires, account, refresh_token)
    SELECT true, client_id, client_secret, secret_expires, account, refresh_token FROM incoming
    ON CONFLICT (id) DO UPDATE SET client_id = EXCLUDED.client_id, client_secret = EXCLUDED.client_secret,
        secret_expires = EXCLUDED.secret_expires, account = EXCLUDED.account, refresh_token = EXCLUDED.refresh_token,
        auth_state = NULL, auth_expires_at = NULL, updated_at = now();
COMMIT;
SQL
    } | dev_psql
    compose exec -T expenses-dev python - < scripts/onedrive-test-folder.py
    onedrive_save >/dev/null   # Microsoft replaced the refresh token: keep the newest
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
        # Last: `compose down` above needs the passwords in .dev/env. This also drops the screenshots.
        # The checkout itself (a worktree's own folder) stays; remove that with `git worktree remove`.
        rm -rf -- "$ROOT/.dev"
        echo "dev: removed $PROJECT" ;;
    list) docker compose ls -a --filter name=teslamate-expenses-dev ;;
    setup) setup ;;
    info) info ;;
    test-browser)
        if [ "${2:-}" = --onedrive ]; then test_browser 1; else test_browser 0; fi ;;
    onedrive-save) onedrive_save ;;
    onedrive-link) onedrive_link ;;
    schema)
        require_source
        tmp="$(mktemp)"
        dump_schema > "$tmp"
        mv "$tmp" "$SCHEMA"
        chmod 644 "$SCHEMA"
        git --no-pager diff --stat -- "$SCHEMA" || true ;;
    sample-expenses)
        ensure_env
        if [ "${2:-}" = clear ]; then
            dev_psql -c "DELETE FROM expenses.expense WHERE notes LIKE 'Sample data%'"
        else
            dev_psql -v "tz=$APP_TZ" < scripts/dev-sample-expenses.sql
            echo "dev: sample expenses added to the test database"
        fi ;;
    psql) ensure_env; shift; dev_psql "$@" ;;
    logs) ensure_env; compose logs -f expenses-dev ;;
    *) sed -n '3,27p' "$0" >&2; exit 2 ;;
esac

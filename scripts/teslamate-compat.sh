#!/usr/bin/env bash
# =============================================================================
# teslamate-compat.sh — test this project against a real TeslaMate release.
#
#   scripts/teslamate-compat.sh [VERSION|latest] [--record]
#
# Starts a throwaway Postgres, lets the real teslamate/teslamate:VERSION image migrate it
# (the image's own entrypoint does that), then:
#   1. compares the migrated tables with db/teslamate-schema.sql;
#   2. runs the whole test suite against that database (TEST_TESLAMATE_IMAGE);
#   3. compares TeslaMate's newest migration with tmexpenses/compat.py.
# Never touches a real TeslaMate database. Needs docker and .venv (scripts/dev.sh setup).
#
# Exit status: 0 = tested and nothing to change; 1 = the tests failed or the tables we read
# changed; 2 = tests pass but TeslaMate is newer than compat.py records (run with --record).
# --record, after a pass, regenerates db/teslamate-schema.sql and updates compat.py to VERSION.
# =============================================================================
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

version="" record=0
for arg in "$@"; do
    case "$arg" in
        --record) record=1 ;;
        *) version="$arg" ;;
    esac
done
tested="$(.venv/bin/python -c 'from tmexpenses import compat; print(compat.TESTED_TESLAMATE)')"
recorded="$(.venv/bin/python -c 'from tmexpenses import compat; print(compat.TESTED_MIGRATION)')"
version="${version:-$tested}"
if [ "$version" = latest ]; then
    version="$(curl -fsSL https://api.github.com/repos/teslamate-org/teslamate/releases/latest \
        | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')"
fi
image="teslamate/teslamate:$version"
echo "compat: TeslaMate $version (this release was tested with $tested)"

name="tmexpenses-compat-$$"
trap 'docker rm -f "$name" >/dev/null 2>&1 || true' EXIT
pass="$(openssl rand -hex 16)"
docker run -d --rm --name "$name" -e POSTGRES_USER=teslamate -e POSTGRES_DB=teslamate \
    -e "POSTGRES_PASSWORD=$pass" postgres:18-trixie >/dev/null
until docker exec "$name" pg_isready -q -U teslamate -d teslamate; do sleep 1; done
sleep 2   # initdb restarts the server once

docker pull -q "$image" >/dev/null
# The entrypoint waits for the database and applies TeslaMate's migrations, then runs the
# command: a no-op here, so TeslaMate itself never starts (no Tesla login, no MQTT).
docker run --rm --network "container:$name" -e DATABASE_HOST=127.0.0.1 -e DATABASE_USER=teslamate \
    -e "DATABASE_PASS=$pass" -e DATABASE_NAME=teslamate -e ENCRYPTION_KEY=compat-test-only -e DISABLE_MQTT=true \
    "$image" /bin/dash -c true

newest="$(docker exec "$name" psql -X -At -U teslamate -d teslamate -c 'SELECT max(version) FROM schema_migrations')"
echo "compat: TeslaMate $version's newest migration is $newest (recorded: $recorded)"

status=0
scripts/dump-teslamate-schema.sh "$name" > .compat-schema.sql
# pg_dump may order independent statements differently from one run or machine to the next, so
# compare the statements (blank-line separated blocks) sorted, not the file byte for byte.
canon() { python3 -I -c 'import sys; print("\n\n".join(sorted(b.strip() for b in sys.stdin.read().split("\n\n") if b.strip())))' < "$1"; }
canon db/teslamate-schema.sql > .compat-have.sql
canon .compat-schema.sql > .compat-new.sql
if ! diff -u --label db/teslamate-schema.sql --label "TeslaMate $version" .compat-have.sql .compat-new.sql; then
    echo "compat: the tables we read differ from db/teslamate-schema.sql (diff above)" >&2
    if [ "$record" = 1 ]; then
        # Some tests build their database from this file, so it is replaced before the suite runs.
        # If the suite then fails, `git checkout db/teslamate-schema.sql` puts it back.
        cp .compat-schema.sql db/teslamate-schema.sql
        echo "compat: --record: db/teslamate-schema.sql replaced"
    else
        status=1
    fi
fi

echo "compat: running the test suite against TeslaMate $version's own schema..."
TEST_TESLAMATE_IMAGE="$image" .venv/bin/python -m pytest -q -p no:cacheprovider || status=1

if [ "$status" = 0 ] && [ "$newest" -gt "$recorded" ]; then
    echo "compat: tests pass, but TeslaMate $version is newer than the recorded $tested."
    echo "compat: run again with --record to make this release claim it." >&2
    status=2
fi
if [ "$record" = 1 ] && { [ "$status" = 0 ] || [ "$status" = 2 ]; }; then
    sed -i -E "s/^TESTED_TESLAMATE = .*/TESTED_TESLAMATE = \"$version\"/; s/^TESTED_MIGRATION = .*/TESTED_MIGRATION = $newest/" \
        tmexpenses/compat.py
    echo "compat: recorded TeslaMate $version (migration $newest). Review 'git diff', update the docs and CHANGELOG."
    status=0
fi
rm -f .compat-schema.sql .compat-have.sql .compat-new.sql
exit "$status"

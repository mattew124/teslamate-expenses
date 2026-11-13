#!/usr/bin/env bash
# =============================================================================
# teslamate-compat.sh — test this project against a real TeslaMate release.
#
#   scripts/teslamate-compat.sh [VERSION|latest] [--record]
#
# Starts a throwaway Postgres, lets the real teslamate/teslamate:VERSION image migrate it
# (the image's own entrypoint does that), then:
#   1. runs the whole test suite against that database (TEST_TESLAMATE_IMAGE);
#   2. compares TeslaMate's newest migration with garagebooks/compat.py.
# Never touches a real TeslaMate database. Needs docker and .venv (scripts/dev.sh setup).
#
# Exit status: 0 = tested and nothing to change; 1 = the tests failed; 2 = the tests pass but
# TeslaMate is newer than compat.py records (run with --record).
# --record, after a pass, updates compat.py to VERSION.
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
tested="$(.venv/bin/python -c 'from garagebooks import compat; print(compat.TESTED_TESLAMATE)')"
recorded="$(.venv/bin/python -c 'from garagebooks import compat; print(compat.TESTED_MIGRATION)')"
version="${version:-$tested}"
if [ "$version" = latest ]; then
    version="$(curl -fsSL https://api.github.com/repos/teslamate-org/teslamate/releases/latest \
        | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["tag_name"].lstrip("v"))')"
fi
image="teslamate/teslamate:$version"
echo "compat: TeslaMate $version (this release was tested with $tested)"

name="garagebooks-compat-$$"
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

echo "compat: running the test suite against TeslaMate $version's own schema..."
TEST_TESLAMATE_IMAGE="$image" .venv/bin/python -m pytest -q -p no:cacheprovider || status=1

if [ "$status" = 0 ] && [ "$newest" -gt "$recorded" ]; then
    echo "compat: tests pass, but TeslaMate $version is newer than the recorded $tested."
    echo "compat: run again with --record to make this release claim it." >&2
    status=2
fi
if [ "$record" = 1 ] && { [ "$status" = 0 ] || [ "$status" = 2 ]; }; then
    sed -i -E "s/^TESTED_TESLAMATE = .*/TESTED_TESLAMATE = \"$version\"/; s/^TESTED_MIGRATION = .*/TESTED_MIGRATION = $newest/" \
        garagebooks/compat.py
    echo "compat: recorded TeslaMate $version (migration $newest). Review 'git diff', update the docs and CHANGELOG."
    status=0
fi
exit "$status"

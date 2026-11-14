"""Which TeslaMate this release of the app has been tested against, and the check that
refuses to run against a newer one.

TeslaMate has no stable database schema (its FAQ says so), so a newer TeslaMate may have
renamed or retyped something we read. Its database does not record the release, only its
Ecto migrations, so the gate compares the newest migration in `public.schema_migrations`
with TESTED_MIGRATION: the newest one in TESTED_TESLAMATE. Both are updated together, by
`scripts/teslamate-compat.sh VERSION --record`, only after the test suite passes against
that TeslaMate."""

import logging

import psycopg
from psycopg.rows import tuple_row

TESTED_TESLAMATE = "4.4.0"
TESTED_MIGRATION = 20261014120000

log = logging.getLogger(__name__)


class UntestedTeslaMate(RuntimeError):
    pass


def newest_migration(conn):
    """The newest TeslaMate migration applied, or None if it can't be read."""
    try:
        with conn.cursor(row_factory=tuple_row) as cur:   # whatever row factory the caller's connection has
            return cur.execute("SELECT max(version) FROM public.schema_migrations").fetchone()[0]
    except (psycopg.errors.UndefinedTable, psycopg.errors.InsufficientPrivilege) as exc:
        log.warning("Cannot read TeslaMate's schema_migrations, so its version is unchecked (%s). "
                    "Tested with TeslaMate %s.", type(exc).__name__, TESTED_TESLAMATE)
        return None


def check(conn, allow_untested=False):
    """Raise UntestedTeslaMate if the database is from a TeslaMate newer than the tested one,
    unless allow_untested. Returns the newest migration found."""
    newest = newest_migration(conn)
    if newest is None or newest <= TESTED_MIGRATION:
        if newest is not None:
            log.info("TeslaMate schema is within what was tested (TeslaMate %s)", TESTED_TESLAMATE)
        return newest
    message = (f"This TeslaMate database is newer than garagebooks has been tested with "
               f"(TeslaMate {TESTED_TESLAMATE}, migration {TESTED_MIGRATION}; this database is at "
               f"{newest}). TeslaMate's tables can change without notice, so the expense list or "
               f"dashboard may be wrong or fail. Upgrade garagebooks to a release that lists "
               f"a newer TeslaMate, or set ALLOW_UNTESTED_TESLAMATE=1 to run anyway.")
    if allow_untested:
        log.error("ALLOW_UNTESTED_TESLAMATE=1: running anyway. %s", message)
        return newest
    raise UntestedTeslaMate(message)

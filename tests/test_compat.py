"""The app refuses to run against a TeslaMate newer than the one it was tested with."""

import psycopg
import pytest

from tmexpenses import compat, create_app


def set_newest(url, version):
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("DELETE FROM schema_migrations")
        if version is not None:
            conn.execute("INSERT INTO schema_migrations (version, inserted_at) VALUES (%s, now())", (version,))


@pytest.fixture()
def env(blank_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", blank_db)
    monkeypatch.delenv("ALLOW_UNTESTED_TESLAMATE", raising=False)
    return blank_db


def test_the_tested_release_and_its_migration_are_recorded():
    assert compat.TESTED_TESLAMATE.count(".") == 2
    assert compat.TESTED_MIGRATION > 20200101000000


def test_starts_on_the_tested_teslamate(env):
    create_app()


def test_starts_on_an_older_teslamate(env):
    set_newest(env, compat.TESTED_MIGRATION - 1)
    create_app()


def test_a_newer_teslamate_stops_the_app_and_says_what_to_do(env):
    set_newest(env, compat.TESTED_MIGRATION + 1)
    with pytest.raises(compat.UntestedTeslaMate) as err:
        create_app()
    text = str(err.value)
    assert compat.TESTED_TESLAMATE in text and str(compat.TESTED_MIGRATION + 1) in text
    assert "ALLOW_UNTESTED_TESLAMATE=1" in text


def test_it_is_checked_even_when_the_schema_is_left_alone(env, monkeypatch):
    set_newest(env, compat.TESTED_MIGRATION + 1)
    monkeypatch.setenv("SKIP_MIGRATE", "1")
    with pytest.raises(compat.UntestedTeslaMate):
        create_app()


def test_the_override_runs_anyway_and_logs_an_error(env, monkeypatch, caplog):
    set_newest(env, compat.TESTED_MIGRATION + 1)
    monkeypatch.setenv("ALLOW_UNTESTED_TESLAMATE", "1")
    create_app()
    assert any(r.levelname == "ERROR" and "newer than" in r.getMessage() for r in caplog.records)


def test_an_unreadable_version_warns_and_does_not_stop_the_app(env, caplog):
    with psycopg.connect(env, autocommit=True) as conn:
        conn.execute("DROP TABLE schema_migrations")
    create_app()
    assert any(r.levelname == "WARNING" and "schema_migrations" in r.getMessage() for r in caplog.records)


def test_the_least_privilege_role_can_read_the_version(pg):
    with psycopg.connect(pg.app_url) as conn:
        newest = compat.newest_migration(conn)   # older than recorded when run on an older TeslaMate
    assert newest is not None and newest <= compat.TESTED_MIGRATION

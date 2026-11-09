"""The app writes its own Grafana dashboard into DASHBOARD_DIR on start, so Grafana can
provision it from a shared volume: the dashboard always matches the running version."""
import json
import logging
import os

from tests.test_dev_stack import ROOT
from tmexpenses import create_app, dashboard

SOURCE = json.loads((ROOT / "grafana" / "cost-of-ownership.json").read_text())


def var(dash, name):
    return next(v for v in dash["templating"]["list"] if v["name"] == name)


def test_render_sets_timezone_and_app_link():
    dash = dashboard.render("Australia/Brisbane", "/expenses")
    tz = var(dash, "tz")
    assert tz["query"] == "Australia/Brisbane"
    assert tz["current"]["value"] == "Australia/Brisbane" and tz["current"]["text"] == "Australia/Brisbane"
    assert [o["value"] for o in tz["options"]] == ["Australia/Brisbane"]
    q = var(dash, "expenses_url")["query"]
    assert "'/expenses/'" in q and "expenses.setting" in q
    assert var(dash, "expenses_url")["definition"] == q
    # nothing else changes
    assert dash["uid"] == SOURCE["uid"] and dash["panels"] == SOURCE["panels"]


def test_render_handles_no_base_path_and_quotes():
    assert "'/'" in var(dashboard.render("UTC", ""), "expenses_url")["query"]
    q = var(dashboard.render("UTC", "/it's"), "expenses_url")["query"]
    assert "'/it''s/'" in q                      # quote doubled, so the SQL stays valid


def test_export_writes_atomically_and_only_when_changed(tmp_path):
    assert dashboard.export(tmp_path, "UTC", "/expenses") is True
    target = tmp_path / dashboard.FILENAME
    assert json.loads(target.read_text())["uid"] == SOURCE["uid"]
    assert [p.name for p in tmp_path.iterdir()] == [dashboard.FILENAME]   # no temp file left
    before = target.stat().st_mtime_ns
    assert dashboard.export(tmp_path, "UTC", "/expenses") is False         # unchanged: not rewritten
    assert target.stat().st_mtime_ns == before
    assert dashboard.export(tmp_path, "Australia/Brisbane", "/expenses") is True


def test_app_exports_on_start_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_TZ", "Australia/Brisbane")
    monkeypatch.setenv("BASE_PATH", "/expenses")
    monkeypatch.setenv("DASHBOARD_DIR", str(tmp_path))
    create_app(connect=lambda: None)
    dash = json.loads((tmp_path / dashboard.FILENAME).read_text())
    assert var(dash, "tz")["query"] == "Australia/Brisbane"


def test_app_does_nothing_without_dashboard_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("DASHBOARD_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    create_app(connect=lambda: None)
    assert list(tmp_path.iterdir()) == []


def test_unwritable_dashboard_dir_warns_but_the_app_starts(tmp_path, monkeypatch, caplog):
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    monkeypatch.setenv("DASHBOARD_DIR", str(locked))
    try:
        if os.access(locked, os.W_OK):
            return                                   # running as root: can't make a dir unwritable
        caplog.set_level(logging.WARNING, logger="tmexpenses")
        create_app(connect=lambda: None)
        assert any("dashboard" in m.lower() for m in caplog.messages)
    finally:
        locked.chmod(0o700)


def test_render_starts_the_time_range_at_the_earliest_data():
    from datetime import datetime, timezone
    dash = dashboard.render("UTC", "", start=datetime(2023, 11, 14, 9, 30, tzinfo=timezone.utc))
    assert dash["time"] == {"from": "2023-11-01T00:00:00.000Z", "to": "now"}
    assert dashboard.render("UTC", "")["time"] == SOURCE["time"]


def test_period_variable_drives_the_bucketed_charts():
    assert [o["value"] for o in var(SOURCE, "period")["options"]] == ["week", "month", "year"]
    for p in SOURCE["panels"]:
        if p["title"].startswith(("Running costs per", "Cumulative")):
            assert "date_trunc('$period'" in p["targets"][0]["rawSql"]

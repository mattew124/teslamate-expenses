"""Logging: LOG_LEVEL picks the level, events are logged at info, and nothing secret is."""
import logging

import pytest

from tmexpenses import logs
from tmexpenses import create_app

from tests.test_app import ORIGIN, client, form  # noqa: F401  (shared fixtures)


def test_parse_level():
    assert logs.parse_level(None) == logging.INFO and logs.parse_level("  ") == logging.INFO
    assert logs.parse_level("DEBUG") == logging.DEBUG
    assert logs.parse_level(" warning ") == logging.WARNING
    assert logs.parse_level("error") == logging.ERROR
    for bad in ("verbose", "trace", "10", "loud"):
        with pytest.raises(ValueError, match="LOG_LEVEL"):
            logs.parse_level(bad)


def test_app_refuses_an_unknown_level(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "verbose")
    with pytest.raises(ValueError, match="LOG_LEVEL"):
        create_app(connect=lambda: None)


def test_level_is_applied_to_the_app_logger(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    create_app(connect=lambda: None)
    assert logging.getLogger("tmexpenses").level == logging.DEBUG
    monkeypatch.delenv("LOG_LEVEL")
    create_app(connect=lambda: None)
    assert logging.getLogger("tmexpenses").level == logging.INFO


def test_expense_changes_are_logged_at_info(client, admin, caplog):
    caplog.set_level(logging.INFO, logger="tmexpenses")
    assert client.post("/new", data=form(admin), headers=ORIGIN).status_code == 303
    assert any("expense 1 saved" in m for m in caplog.messages)
    client.post("/1/edit", data=form(admin), headers=ORIGIN)
    assert any("expense 1 updated" in m for m in caplog.messages)
    client.post("/1/delete", headers=ORIGIN)
    assert any("expense 1 deleted" in m for m in caplog.messages)


def test_info_level_hides_debug_detail(client, admin, caplog):
    caplog.set_level(logging.INFO, logger="tmexpenses")
    client.get("/")
    assert not [r for r in caplog.records if r.levelno < logging.INFO]


def test_notes_and_vendor_are_never_logged(client, admin, caplog):
    caplog.set_level(logging.DEBUG, logger="tmexpenses")
    client.post("/new", data=form(admin, vendor="Secret Vendor Ltd", notes="private note"), headers=ORIGIN)
    text = "\n".join(caplog.messages)
    assert "Secret Vendor" not in text and "private note" not in text

"""Reading the OneDrive encryption key straight from HashiCorp Vault (KV v2)."""
import json
import logging

import pytest
from cryptography.fernet import Fernet

from tmexpenses import create_app, vault

KEY = Fernet.generate_key().decode()
BASE = {"VAULT_ADDR": "https://vault.example:8200/", "VAULT_MOUNT": "general",
        "VAULT_SECRET_PATH": "teslamate-expenses-onedrive"}


class FakeVault:
    def __init__(self, data=None, login_status=200, read_status=200):
        self.data = {"encryption_key": KEY} if data is None else data
        self.login_status, self.read_status = login_status, read_status
        self.calls = []

    def __call__(self, method, url, data, headers):
        self.calls.append((method, url, data and json.loads(data), dict(headers)))
        if url.endswith("/v1/auth/approle/login"):
            return self.login_status, {"auth": {"client_token": "s.approle"}}
        return self.read_status, {"data": {"data": self.data}}


def test_not_configured_returns_none():
    assert vault.read_key({}, FakeVault()) is None
    assert vault.read_key({"VAULT_ADDR": "https://v"}, FakeVault()) is None   # no path


def test_reads_kv2_field_with_token():
    fake = FakeVault()
    assert vault.read_key({**BASE, "VAULT_TOKEN": "s.tok"}, fake) == KEY
    method, url, _, headers = fake.calls[0]
    assert method == "GET" and url == "https://vault.example:8200/v1/general/data/teslamate-expenses-onedrive"
    assert headers["X-Vault-Token"] == "s.tok"


def test_token_file_and_namespace_and_field(tmp_path):
    f = tmp_path / "token"
    f.write_text("s.fromfile\n")
    fake = FakeVault({"other": "abc"})
    env = {**BASE, "VAULT_TOKEN_FILE": str(f), "VAULT_NAMESPACE": "team", "VAULT_SECRET_FIELD": "other"}
    assert vault.read_key(env, fake) == "abc"
    headers = fake.calls[0][3]
    assert headers["X-Vault-Token"] == "s.fromfile" and headers["X-Vault-Namespace"] == "team"


def test_approle_login_then_read():
    fake = FakeVault()
    env = {**BASE, "VAULT_ROLE_ID": "rid", "VAULT_SECRET_ID": "sid"}
    assert vault.read_key(env, fake) == KEY
    assert fake.calls[0][1].endswith("/v1/auth/approle/login")
    assert fake.calls[0][2] == {"role_id": "rid", "secret_id": "sid"}
    assert fake.calls[1][3]["X-Vault-Token"] == "s.approle"


def test_default_mount_is_secret():
    env = {"VAULT_ADDR": "https://v", "VAULT_SECRET_PATH": "a/b", "VAULT_TOKEN": "t"}
    fake = FakeVault()
    vault.read_key(env, fake)
    assert fake.calls[0][1] == "https://v/v1/secret/data/a/b"


@pytest.mark.parametrize("env,fake,match", [
    ({}, FakeVault(), "VAULT_TOKEN"),                                        # no credentials
    ({"VAULT_TOKEN": "t"}, FakeVault(read_status=403), "403"),
    ({"VAULT_TOKEN": "t"}, FakeVault(read_status=404), "404"),
    ({"VAULT_TOKEN": "t"}, FakeVault({}), "encryption_key"),                  # field missing
    ({"VAULT_ROLE_ID": "r", "VAULT_SECRET_ID": "s"}, FakeVault(login_status=400), "AppRole"),
])
def test_failures_name_the_problem_and_never_the_secret(env, fake, match):
    with pytest.raises(vault.VaultError, match=match) as exc:
        vault.read_key({**BASE, **env}, fake)
    assert KEY not in str(exc.value)


def test_app_uses_vault_key(pg, admin, monkeypatch):
    import psycopg
    from psycopg.rows import dict_row
    for k, v in {**BASE, "VAULT_TOKEN": "t"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EXPENSES_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("APP_TZ", "Australia/Brisbane")
    app = create_app(connect=lambda: psycopg.connect(pg.app_url, row_factory=dict_row),
                     vault_transport=FakeVault())
    assert app.extensions["onedrive"] is not None


def test_explicit_key_wins_over_vault(monkeypatch):
    for k, v in {**BASE, "VAULT_TOKEN": "t"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("EXPENSES_ENCRYPTION_KEY", KEY)
    fake = FakeVault()
    create_app(connect=lambda: None, vault_transport=fake)
    assert fake.calls == []


def test_vault_failure_leaves_onedrive_off_and_logs(monkeypatch, caplog):
    for k, v in {**BASE, "VAULT_TOKEN": "t"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("EXPENSES_ENCRYPTION_KEY", raising=False)
    caplog.set_level(logging.ERROR, logger="tmexpenses")
    create_app(connect=lambda: None, vault_transport=FakeVault(read_status=403))
    assert any("Vault" in m and "403" in m for m in caplog.messages)

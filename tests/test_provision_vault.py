"""scripts/provision-vault.sh against a fake Vault: what it creates, what it refuses to overwrite,
and that the token never leaves the request header."""
import json
import os
import stat
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tmexpenses import vault

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "provision-vault.sh"
TOKEN = "s.admin-token-for-tests"


class FakeVault(BaseHTTPRequestHandler):
    state = None

    def log_message(self, *args):
        pass

    def reply(self, status, body=None):
        raw = json.dumps(body or {}).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def handle_any(self):
        st = self.state
        path = self.path.removeprefix("/v1/")
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"{}")
        st["requests"].append((self.command, path, self.headers.get("X-Vault-Token"), body))
        if self.headers.get("X-Vault-Token") != TOKEN:
            return self.reply(403, {"errors": ["permission denied"]})
        key = (self.command, path)
        if key == ("GET", "sys/auth"):
            return self.reply(200, {"data": {f"{m}/": {} for m in st["auth"]}})
        if key == ("POST", "sys/auth/approle"):
            st["auth"].add("approle")
            return self.reply(204)
        if self.command in ("PUT", "POST") and path.startswith(("sys/policies/acl/", "auth/approle/role/")) \
                and not path.endswith("secret-id"):
            st["written"][path] = body
            return self.reply(204)
        if key == ("GET", "auth/approle/role/teslamate-expenses/role-id"):
            return self.reply(200, {"data": {"role_id": "role-123"}})
        if key == ("POST", "auth/approle/role/teslamate-expenses/secret-id"):
            return self.reply(200, {"data": {"secret_id": "secret-456"}})
        if path == "secret/data/teslamate-expenses-onedrive":
            if self.command == "GET":
                if st["secret"] is None:
                    return self.reply(404)
                return self.reply(200, {"data": {"data": st["secret"]}})
            if st["secret"] is not None:
                return self.reply(400, {"errors": ["check-and-set parameter did not match"]})
            st["secret"] = body["data"]
            return self.reply(200)
        return self.reply(404, {"errors": ["no handler for route"]})

    do_GET = do_POST = do_PUT = handle_any


@pytest.fixture()
def server():
    FakeVault.state = {"auth": set(), "written": {}, "secret": None, "requests": []}
    httpd = HTTPServer(("127.0.0.1", 0), FakeVault)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}", FakeVault.state
    httpd.shutdown()


def run(addr, tmp_path, token=TOKEN, **extra):
    env = {**os.environ, "VAULT_ADDR": addr, "OUT_FILE": str(tmp_path / "out.env"), **extra}
    return subprocess.run([str(SCRIPT)], input=token + "\n", env=env, capture_output=True, text=True, check=False)


def test_provisions_everything_and_writes_credentials(server, tmp_path):
    addr, st = server
    r = run(addr, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "approle" in st["auth"]
    policy = st["written"]["sys/policies/acl/teslamate-expenses"]["policy"]
    assert 'path "secret/data/teslamate-expenses-onedrive"' in policy and "read" in policy
    assert "create" not in policy and "update" not in policy
    role = st["written"]["auth/approle/role/teslamate-expenses"]
    assert role["token_policies"] == ["teslamate-expenses"]
    assert len(st["secret"]["encryption_key"]) == 44
    out = tmp_path / "out.env"
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    lines = out.read_text().splitlines()
    assert f"VAULT_ADDR={addr}" in lines and "VAULT_ROLE_ID=role-123" in lines and "VAULT_SECRET_ID=secret-456" in lines
    # Nothing secret on screen: neither the key, the secret ID nor the token.
    shown = r.stdout + r.stderr
    assert st["secret"]["encryption_key"] not in shown and "secret-456" not in shown and TOKEN not in shown


def test_what_it_writes_is_what_the_app_reads(server, tmp_path):
    addr, st = server
    assert run(addr, tmp_path).returncode == 0
    env = dict(line.split("=", 1) for line in (tmp_path / "out.env").read_text().splitlines())
    # Simple login check: the app's reader finds the field the script wrote.
    def transport(method, url, data, headers):
        if url.endswith("/login"):
            return 200, {"auth": {"client_token": TOKEN}}
        return 200, {"data": {"data": st["secret"]}}
    assert vault.read_key(env, transport) == st["secret"]["encryption_key"]


def test_existing_key_is_never_overwritten(server, tmp_path):
    addr, st = server
    st["secret"] = {"encryption_key": "keep-me"}
    r = run(addr, tmp_path)
    assert r.returncode == 0, r.stderr
    assert st["secret"] == {"encryption_key": "keep-me"} and "left as it is" in r.stdout
    assert not [q for q in st["requests"] if q[0] == "POST" and q[1].startswith("secret/")]


def test_rerun_reuses_approle_auth(server, tmp_path):
    addr, st = server
    st["auth"].add("approle")
    assert run(addr, tmp_path).returncode == 0
    assert ("POST", "sys/auth/approle") not in [(q[0], q[1]) for q in st["requests"]]


def test_refuses_to_overwrite_the_credentials_file(server, tmp_path):
    addr, _ = server
    (tmp_path / "out.env").write_text("old")
    r = run(addr, tmp_path)
    assert r.returncode != 0 and "already exists" in r.stderr
    assert (tmp_path / "out.env").read_text() == "old"


def test_bad_token_stops_with_a_message(server, tmp_path):
    addr, st = server
    r = run(addr, tmp_path, token="wrong")
    assert r.returncode != 0 and "sys/auth" in r.stderr and "wrong" not in r.stderr
    assert not (tmp_path / "out.env").exists()


def test_needs_address_and_token(server, tmp_path):
    addr, _ = server
    env = {k: v for k, v in os.environ.items() if k != "VAULT_ADDR"}
    r = subprocess.run([str(SCRIPT)], input="x\n", env=env, capture_output=True, text=True, check=False)
    assert r.returncode != 0 and "VAULT_ADDR" in r.stderr
    assert run(addr, tmp_path, token="").returncode != 0


def test_token_travels_in_a_descriptor_not_an_argument():
    text = SCRIPT.read_text()
    assert "-H \"X-Vault-Token" not in text and "VAULT_TOKEN" not in text.split("set -euo pipefail")[1]
    assert "--config <(printf 'header = \"X-Vault-Token" in text

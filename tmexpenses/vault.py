"""Reads the OneDrive encryption key straight from HashiCorp Vault (KV version 2).

An alternative to putting EXPENSES_ENCRYPTION_KEY in the environment, so no agent has to
render it into a file. The key is read once, at start, and held in memory only.

Settings (all environment variables):

    VAULT_ADDR           https://vault.example:8200      (required)
    VAULT_SECRET_PATH    path under the mount            (required)
    VAULT_MOUNT          KV v2 mount, default "secret"
    VAULT_SECRET_FIELD   field holding the key, default "encryption_key"
    VAULT_TOKEN          a token, or VAULT_TOKEN_FILE: a file holding one
    VAULT_ROLE_ID, VAULT_SECRET_ID   AppRole login, when there is no token
    VAULT_APPROLE_MOUNT  default "approle"
    VAULT_NAMESPACE      Enterprise namespace, if any
    VAULT_CACERT         CA bundle for a private certificate authority

Every HTTP call goes through `transport(method, url, data, headers) -> (status, json)`,
which tests replace with a fake.
"""

import json
import logging
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

log = logging.getLogger("tmexpenses.vault")

RETRY_WAITS = (1, 3)    # seconds between tries when Vault can't be reached (it may still be starting)


class VaultError(Exception):
    """Says what went wrong; never carries a secret."""


def make_transport(cacert=None):
    context = ssl.create_default_context(cafile=cacert) if cacert else None

    def transport(method, url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=10, context=context) as resp:   # nosec B310 - the address is operator configuration
                status, body = resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            status, body = exc.code, exc.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ConnectionError(str(exc)) from None
        try:
            return status, json.loads(body or b"{}")
        except ValueError:
            return status, {}
    return transport


def configured(env):
    return bool(env.get("VAULT_ADDR") and env.get("VAULT_SECRET_PATH"))


def _call(transport, method, url, headers, body=None):
    data = json.dumps(body).encode() if body is not None else None
    if data is not None:
        headers = {**headers, "Content-Type": "application/json"}
    for wait in (*RETRY_WAITS, None):
        try:
            return transport(method, url, data, headers)
        except ConnectionError as exc:
            if wait is None:
                raise VaultError(f"Couldn't reach Vault: {exc}") from None
            log.info("Vault not reachable (%s); trying again in %ss", exc, wait)
            time.sleep(wait)


def _token(env, transport, base, headers):
    if env.get("VAULT_TOKEN"):
        return env["VAULT_TOKEN"].strip()
    if env.get("VAULT_TOKEN_FILE"):
        try:
            token = Path(env["VAULT_TOKEN_FILE"]).read_text().strip()
        except OSError as exc:
            raise VaultError(f"Couldn't read VAULT_TOKEN_FILE: {exc.strerror}") from None
        if token:
            return token
    if env.get("VAULT_ROLE_ID") and env.get("VAULT_SECRET_ID"):
        mount = quote(env.get("VAULT_APPROLE_MOUNT", "approle").strip("/"), safe="/")
        status, body = _call(transport, "POST", f"{base}/v1/auth/{mount}/login", headers,
                             {"role_id": env["VAULT_ROLE_ID"], "secret_id": env["VAULT_SECRET_ID"]})
        token = (body.get("auth") or {}).get("client_token") if isinstance(body, dict) else None
        if status != 200 or not token:
            raise VaultError(f"Vault AppRole login failed (HTTP {status})")
        return token
    raise VaultError("Vault needs VAULT_TOKEN, VAULT_TOKEN_FILE, or VAULT_ROLE_ID with VAULT_SECRET_ID")


def read_key(env, transport=None):
    """The encryption key from Vault, or None when Vault isn't configured. Raises VaultError."""
    if not configured(env):
        return None
    transport = transport or make_transport(env.get("VAULT_CACERT") or None)
    base = env["VAULT_ADDR"].rstrip("/")
    headers = {"X-Vault-Namespace": env["VAULT_NAMESPACE"]} if env.get("VAULT_NAMESPACE") else {}
    headers = {**headers, "X-Vault-Token": _token(env, transport, base, headers)}
    mount = quote(env.get("VAULT_MOUNT", "secret").strip("/"), safe="/")
    path = quote(env["VAULT_SECRET_PATH"].strip("/"), safe="/")
    status, body = _call(transport, "GET", f"{base}/v1/{mount}/data/{path}", headers)
    if status != 200:
        raise VaultError(f"Vault refused or couldn't find {mount}/{path} (HTTP {status})")
    field = env.get("VAULT_SECRET_FIELD", "encryption_key")
    data = ((body.get("data") or {}).get("data") or {}) if isinstance(body, dict) else {}
    value = data.get(field)
    if not value or not isinstance(value, str):
        raise VaultError(f"Vault secret {mount}/{path} has no field {field!r}")
    return value.strip()

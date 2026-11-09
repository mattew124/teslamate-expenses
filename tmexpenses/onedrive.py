"""Uploading invoice PDFs to a personal OneDrive through Microsoft Graph.

Sign-in is the standard web flow (OAuth 2.0 authorisation code with PKCE):
Connect sends the browser to Microsoft, which sends it back to /onedrive/callback
with a code the app swaps for tokens. Microsoft only redirects to addresses
registered on the app, and every installation has its own address, so each
installer registers their own Entra app and enters its client ID and secret on
the OneDrive page. Personal Microsoft accounts can't use app-only (client
credentials) access to OneDrive, so a delegated sign-in is the only way in.

The refresh token is the key to the whole drive (Files.ReadWrite), so it and the
client secret are encrypted with EXPENSES_ENCRYPTION_KEY before they reach the
database: a backup of the TeslaMate database on its own can't open the drive.

Every HTTP call goes through `transport(method, url, data, headers) -> (status, json)`,
which tests replace with a fake.
"""

import base64
import hashlib
import json
import secrets
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken

AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "Files.ReadWrite offline_access"
MAX_PAGES = 20   # of 200 children each, when listing a folder


class OneDriveError(Exception):
    """Carries a message fit to show the user."""


class SignInExpired(OneDriveError):
    pass


class NotConnected(OneDriveError):
    def __init__(self):
        super().__init__("Connect OneDrive first (OneDrive page), then try again.")


def http(method, url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status, body = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise OneDriveError(f"Couldn't reach Microsoft: {exc}") from None
    try:
        return status, json.loads(body or b"{}")
    except ValueError:
        return status, {}


def token_box(key):
    """EXPENSES_ENCRYPTION_KEY -> Fernet. Raises ValueError naming the variable."""
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "EXPENSES_ENCRYPTION_KEY must be 32 random bytes, url-safe base64 encoded "
            "(see README.md, OneDrive)") from None


def graph_error(status, body):
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"{err.get('message') or err.get('code') or 'error'} (HTTP {status})"
    return f"HTTP {status}"


def new_pkce():
    """-> (state, verifier, challenge) for one sign-in."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return secrets.token_urlsafe(32), verifier, challenge


def display_path(item):
    """Graph item -> '/Cars/Model 3' (the drive root is '/')."""
    parent = (item.get("parentReference") or {}).get("path")
    if not parent:
        return "/"
    _, _, under_root = parent.partition("root:")
    return f"{under_root.rstrip('/')}/{item['name']}"


class OneDrive:
    def __init__(self, key, transport=None):
        self.box = token_box(key)
        self.transport = transport or http

    def encrypt(self, text):
        return self.box.encrypt(text.encode()).decode()

    def decrypt(self, token):
        try:
            return self.box.decrypt(token.encode()).decode()
        except InvalidToken:
            raise SignInExpired(
                "The saved OneDrive details can't be read (was EXPENSES_ENCRYPTION_KEY changed?). "
                "Enter the client secret and connect again on the OneDrive page.") from None

    # ---- sign-in --------------------------------------------------------------

    @staticmethod
    def authorize_url(client_id, redirect_uri, state, challenge):
        return f"{AUTHORITY}/authorize?" + urlencode({
            "client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
            "response_mode": "query", "scope": SCOPES, "state": state,
            "code_challenge": challenge, "code_challenge_method": "S256",
            "prompt": "select_account",
        })

    def _token(self, client_id, secret, fields):
        data = urlencode({"client_id": client_id, "client_secret": secret, **fields}).encode()
        return self.transport("POST", f"{AUTHORITY}/token", data,
                              {"Content-Type": "application/x-www-form-urlencoded"})

    def redeem_code(self, client_id, secret, code, redirect_uri, verifier):
        status, body = self._token(client_id, secret, {
            "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri,
            "code_verifier": verifier, "scope": SCOPES,
        })
        if status == 200 and "refresh_token" in body:
            return body
        raise OneDriveError("Microsoft didn't complete the sign-in: "
                            + (body.get("error_description") or body.get("error") or f"HTTP {status}"))

    def refresh(self, client_id, secret, refresh_token):
        """-> {access_token, refresh_token}. Microsoft rotates the refresh token:
        the caller must store the new one."""
        status, body = self._token(client_id, secret, {
            "grant_type": "refresh_token", "refresh_token": refresh_token, "scope": SCOPES,
        })
        if status == 200 and "access_token" in body:
            return body
        if body.get("error") in ("invalid_grant", "interaction_required"):
            raise SignInExpired("The OneDrive sign-in has expired: sign in again on the OneDrive page.")
        if body.get("error") in ("invalid_client", "unauthorized_client"):
            raise SignInExpired("Microsoft refused the client secret (expired?): enter a new one "
                                "on the OneDrive page.")
        raise OneDriveError("Couldn't refresh the OneDrive sign-in: "
                            + (body.get("error_description") or f"HTTP {status}"))

    # ---- Graph ----------------------------------------------------------------

    def _get(self, access_token, url):
        status, body = self.transport("GET", url, None, {"Authorization": f"Bearer {access_token}"})
        if status != 200:
            raise OneDriveError("OneDrive said no: " + graph_error(status, body))
        return body

    def account_name(self, access_token):
        try:
            user = (self._get(access_token, f"{GRAPH}/me/drive").get("owner") or {}).get("user") or {}
        except OneDriveError:
            return None
        return user.get("displayName") or user.get("email")

    def folder(self, access_token, folder_id=None):
        """-> {id, name, path, parent_id} of a folder (the drive root when None)."""
        url = f"{GRAPH}/me/drive/items/{quote(folder_id, safe='')}" if folder_id else f"{GRAPH}/me/drive/root"
        item = self._get(access_token, url)
        if "folder" not in item:
            raise OneDriveError("That isn't a folder.")
        return {"id": item["id"], "name": item["name"], "path": display_path(item),
                "parent_id": (item.get("parentReference") or {}).get("id")}

    def subfolders(self, access_token, folder_id=None):
        """Folders directly inside a folder, by name: [{id, name}]."""
        base = f"{GRAPH}/me/drive/items/{quote(folder_id, safe='')}" if folder_id else f"{GRAPH}/me/drive/root"
        url = f"{base}/children?$top=200&$select=id,name,folder"
        found = []
        for _ in range(MAX_PAGES):
            body = self._get(access_token, url)
            found += [{"id": c["id"], "name": c["name"]} for c in body.get("value", []) if "folder" in c]
            url = body.get("@odata.nextLink")
            if not url or not url.startswith(GRAPH + "/"):
                break
        return sorted(found, key=lambda f: f["name"].lower())

    def create_folder(self, access_token, parent_id, name):
        """New folder inside parent_id (the drive root when None). Fails rather
        than renaming if the name is taken. -> {id, name, path, parent_id}"""
        base = f"{GRAPH}/me/drive/items/{quote(parent_id, safe='')}" if parent_id else f"{GRAPH}/me/drive/root"
        body = json.dumps({"name": name, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"}).encode()
        status, item = self.transport("POST", f"{base}/children", body, {
            "Authorization": f"Bearer {access_token}", "Content-Type": "application/json"})
        if status == 409:
            raise OneDriveError(f"A folder called “{name}” already exists here.")
        if status not in (200, 201) or "id" not in item:
            raise OneDriveError("OneDrive didn't create the folder: " + graph_error(status, item))
        return {"id": item["id"], "name": item["name"], "path": display_path(item),
                "parent_id": (item.get("parentReference") or {}).get("id")}

    def upload(self, access_token, folder_id, filename, content):
        """Simple upload (fine to 250 MB) into a folder, by id. A name that
        already exists gets a suffix rather than being overwritten.
        -> the file's OneDrive web URL."""
        url = (f"{GRAPH}/me/drive/items/{quote(folder_id, safe='')}:/{quote(filename, safe='')}:/content"
               "?@microsoft.graph.conflictBehavior=rename")
        status, body = self.transport("PUT", url, content, {
            "Authorization": f"Bearer {access_token}", "Content-Type": "application/pdf",
        })
        if status not in (200, 201) or not str(body.get("webUrl", "")).startswith("https://"):
            raise OneDriveError("OneDrive refused the upload: " + graph_error(status, body))
        return body["webUrl"]

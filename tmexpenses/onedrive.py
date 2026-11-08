"""Uploading invoice PDFs to a personal OneDrive through Microsoft Graph.

Sign-in uses the OAuth device code flow: the app shows a short code, you enter it
at microsoft.com/devicelogin, and the app gets a refresh token. There is no
redirect URI to register, which suits an app that sits behind forward-auth on
whatever hostname. Personal Microsoft accounts can't use app-only (client
credentials) access to OneDrive, so a delegated sign-in is the only way in.

The refresh token is the key to the whole drive (Files.ReadWrite), so it is
encrypted with ONEDRIVE_TOKEN_KEY before it reaches the database: a backup of
the TeslaMate database on its own can't open the drive.

Every HTTP call goes through `transport(method, url, data, headers) -> (status, json)`,
which tests replace with a fake.
"""

import json
import urllib.error
import urllib.request
from urllib.parse import quote, urlencode

from cryptography.fernet import Fernet, InvalidToken

AUTHORITY = "https://login.microsoftonline.com/consumers/oauth2/v2.0"
GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = "Files.ReadWrite offline_access"


class OneDriveError(Exception):
    """Carries a message fit to show the user."""


class SignInPending(OneDriveError):
    pass


class SignInExpired(OneDriveError):
    pass


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
    """ONEDRIVE_TOKEN_KEY -> Fernet. Raises ValueError naming the variable."""
    try:
        return Fernet(key.encode() if isinstance(key, str) else key)
    except (ValueError, TypeError, AttributeError):
        raise ValueError(
            "ONEDRIVE_TOKEN_KEY must be 32 random bytes, url-safe base64 encoded "
            "(see README.md, OneDrive)") from None


def graph_error(status, body):
    err = body.get("error") if isinstance(body, dict) else None
    if isinstance(err, dict):
        return f"{err.get('message') or err.get('code') or 'error'} (HTTP {status})"
    return f"HTTP {status}"


class OneDrive:
    def __init__(self, client_id, key, transport=None):
        self.client_id = client_id
        self.box = token_box(key)
        self.transport = transport or http

    def encrypt(self, text):
        return self.box.encrypt(text.encode()).decode()

    def decrypt(self, token):
        try:
            return self.box.decrypt(token.encode()).decode()
        except InvalidToken:
            raise SignInExpired(
                "The saved OneDrive sign-in can't be read (was ONEDRIVE_TOKEN_KEY changed?). "
                "Connect OneDrive again.") from None

    def _token_request(self, path, fields):
        data = urlencode({"client_id": self.client_id, **fields}).encode()
        return self.transport("POST", f"{AUTHORITY}/{path}", data,
                              {"Content-Type": "application/x-www-form-urlencoded"})

    def start_sign_in(self):
        """-> {device_code, user_code, verification_uri, expires_in, ...}"""
        status, body = self._token_request("devicecode", {"scope": SCOPES})
        if status != 200 or "device_code" not in body:
            raise OneDriveError("Microsoft didn't start the sign-in: "
                                + (body.get("error_description") or f"HTTP {status}"))
        if not str(body.get("verification_uri", "")).startswith("https://"):
            body["verification_uri"] = "https://microsoft.com/devicelogin"   # it becomes an href
        return body

    def finish_sign_in(self, device_code):
        """-> tokens once the user has entered the code; SignInPending until then."""
        status, body = self._token_request("token", {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "device_code": device_code,
        })
        if status == 200 and "refresh_token" in body:
            return body
        error = body.get("error")
        if error in ("authorization_pending", "slow_down"):
            raise SignInPending("Microsoft hasn't finished signing in yet. Enter the code there, then check again.")
        if error == "expired_token":
            raise SignInExpired("That code has expired. Start again.")
        raise OneDriveError("Sign-in failed: " + (body.get("error_description") or error or f"HTTP {status}"))

    def refresh(self, refresh_token):
        """-> {access_token, refresh_token}. Microsoft rotates the refresh token:
        the caller must store the new one."""
        status, body = self._token_request("token", {
            "grant_type": "refresh_token", "refresh_token": refresh_token, "scope": SCOPES,
        })
        if status == 200 and "access_token" in body:
            return body
        if body.get("error") in ("invalid_grant", "interaction_required"):
            raise SignInExpired("The OneDrive sign-in has expired: sign in again on the OneDrive page.")
        raise OneDriveError("Couldn't refresh the OneDrive sign-in: "
                            + (body.get("error_description") or f"HTTP {status}"))

    def account_name(self, access_token):
        status, body = self.transport("GET", f"{GRAPH}/me/drive", None,
                                      {"Authorization": f"Bearer {access_token}"})
        if status != 200:
            return None
        user = (body.get("owner") or {}).get("user") or {}
        return user.get("displayName") or user.get("email")

    def upload(self, access_token, folder, filename, content):
        """Simple upload (fine to 250 MB). Missing folders are created; a name
        that already exists gets a suffix rather than being overwritten.
        -> the file's OneDrive web URL."""
        path = quote(f"{folder}/{filename}", safe="/")
        url = f"{GRAPH}/me/drive/root:/{path}:/content?@microsoft.graph.conflictBehavior=rename"
        status, body = self.transport("PUT", url, content, {
            "Authorization": f"Bearer {access_token}", "Content-Type": "application/pdf",
        })
        if status not in (200, 201) or not str(body.get("webUrl", "")).startswith("https://"):
            raise OneDriveError("OneDrive refused the upload: " + graph_error(status, body))
        return body["webUrl"]

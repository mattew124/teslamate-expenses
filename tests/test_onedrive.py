"""Uploading invoice PDFs to OneDrive: sign-in, per-car folders, the upload itself.

Microsoft is replaced by FakeMicrosoft, handed to create_app as its HTTP transport,
so these run offline and can see exactly what would have been sent.
"""

import io
import json
from urllib.parse import parse_qs, unquote, urlparse

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.rows import dict_row

from tmexpenses import create_app
from tests.test_app import ORIGIN, category_id, form, rows
from tests.test_validation import PDF

KEY = Fernet.generate_key().decode()
WEB_URL = "https://onedrive.live.com/?id=ABC123"


class FakeMicrosoft:
    """Just enough of the Microsoft identity platform and Graph for the app."""

    def __init__(self):
        self.calls = []
        self.signed_in = False        # flips when the user "enters the code"
        self.refresh_ok = True
        self.upload_status = 201
        self.refresh_count = 0

    def __call__(self, method, url, data=None, headers=None):
        self.calls.append((method, url, data, headers or {}))
        path = urlparse(url).path
        form = parse_qs(data.decode()) if data and path.endswith(("/devicecode", "/token")) else {}
        if path.endswith("/devicecode"):
            assert form["client_id"] == ["test-client"]
            assert "offline_access" in form["scope"][0] and "Files.ReadWrite" in form["scope"][0]
            return 200, {"device_code": "DEVICE-SECRET", "user_code": "ABCD-EFGH",
                         "verification_uri": "https://microsoft.com/devicelogin",
                         "expires_in": 900, "interval": 5}
        if path.endswith("/token"):
            grant = form["grant_type"][0]
            if grant.endswith("device_code"):
                assert form["device_code"] == ["DEVICE-SECRET"]
                if not self.signed_in:
                    return 400, {"error": "authorization_pending"}
                return 200, {"access_token": "AT-1", "refresh_token": "RT-1"}
            assert grant == "refresh_token"
            if not self.refresh_ok:
                return 400, {"error": "invalid_grant", "error_description": "expired"}
            self.refresh_count += 1
            n = self.refresh_count + 1
            return 200, {"access_token": f"AT-{n}", "refresh_token": f"RT-{n}"}
        if path.endswith("/me/drive") and method == "GET":
            return 200, {"owner": {"user": {"displayName": "Test Owner"}}}
        if method == "PUT" and path.endswith(":/content"):
            if self.upload_status >= 400:
                return self.upload_status, {"error": {"code": "accessDenied", "message": "Nope"}}
            return 201, {"id": "ABC123", "webUrl": WEB_URL}
        raise AssertionError(f"unexpected call {method} {url}")

    def uploads(self):
        return [c for c in self.calls if c[0] == "PUT"]


@pytest.fixture()
def ms():
    return FakeMicrosoft()


@pytest.fixture()
def app_for(pg, admin, monkeypatch):
    monkeypatch.delenv("BASE_PATH", raising=False)
    monkeypatch.setenv("APP_TZ", "Australia/Brisbane")
    admin.execute("TRUNCATE expenses.car_folder, expenses.onedrive_account")

    def make(transport, client_id="test-client", key=KEY):
        for name, value in (("ONEDRIVE_CLIENT_ID", client_id), ("ONEDRIVE_TOKEN_KEY", key)):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
        app = create_app(connect=lambda: psycopg.connect(pg.app_url, row_factory=dict_row),
                         onedrive_transport=transport)
        app.testing = True
        return app.test_client()
    return make


@pytest.fixture()
def client(app_for, ms):
    return app_for(ms)


def sign_in(client, ms):
    assert client.post("/onedrive/connect", headers=ORIGIN).status_code == 303
    ms.signed_in = True
    assert client.post("/onedrive/connect/check", headers=ORIGIN).status_code == 303


def set_folder(client, folder, car_id=1):
    return client.post("/onedrive/folders", data={f"folder_{car_id}": folder}, headers=ORIGIN)


def with_pdf(data, content=PDF, name="invoice.pdf"):
    return {**data, "invoice_file": (io.BytesIO(content), name, "application/pdf")}


def account(admin):
    return admin.execute("SELECT * FROM expenses.onedrive_account").fetchone()


# ---- not configured -------------------------------------------------------------

def test_hidden_when_not_configured(app_for, ms, admin):
    c = app_for(ms, client_id=None, key=None)
    assert b'name="invoice_file"' not in c.get("/new").data
    assert b"/onedrive" not in c.get("/").data
    page = c.get("/onedrive").data.decode()
    assert "ONEDRIVE_CLIENT_ID" in page and "Connect OneDrive" not in page
    assert c.post("/onedrive/connect", headers=ORIGIN).status_code == 404
    # an uploaded file is ignored, not silently lost: the form doesn't offer it
    assert c.post("/new", data=with_pdf(form(admin)), headers=ORIGIN).status_code == 303
    assert ms.calls == []


def test_client_id_without_key_refuses_to_start(app_for, ms):
    with pytest.raises(ValueError, match="ONEDRIVE_TOKEN_KEY"):
        app_for(ms, key=None)
    with pytest.raises(ValueError, match="ONEDRIVE_TOKEN_KEY"):
        app_for(ms, key="not-a-fernet-key")


# ---- sign-in --------------------------------------------------------------------

def test_device_code_sign_in(client, ms, admin):
    page = client.get("/onedrive").data.decode()
    assert "Not connected" in page and "Connect OneDrive" in page

    client.post("/onedrive/connect", headers=ORIGIN)
    page = client.get("/onedrive").data.decode()
    assert "ABCD-EFGH" in page and 'href="https://microsoft.com/devicelogin"' in page
    assert "DEVICE-SECRET" not in page

    # Checking before the user has entered the code: still waiting, nothing saved.
    resp = client.post("/onedrive/connect/check", headers=ORIGIN, follow_redirects=True)
    assert b"finished signing in yet" in resp.data
    assert account(admin)["refresh_token"] is None

    ms.signed_in = True
    resp = client.post("/onedrive/connect/check", headers=ORIGIN, follow_redirects=True)
    assert b"Connected as <strong>Test Owner</strong>" in resp.data and b"ABCD-EFGH" not in resp.data
    row = account(admin)
    assert row["account"] == "Test Owner" and row["device_code"] is None


def test_tokens_are_encrypted_at_rest(client, ms, admin):
    client.post("/onedrive/connect", headers=ORIGIN)
    assert "DEVICE-SECRET" not in json.dumps(account(admin), default=str)
    ms.signed_in = True
    client.post("/onedrive/connect/check", headers=ORIGIN)
    row = account(admin)
    assert "RT-1" not in json.dumps(row, default=str)
    assert Fernet(KEY.encode()).decrypt(row["refresh_token"].encode()) == b"RT-1"


def test_disconnect_forgets_the_token(client, ms, admin):
    sign_in(client, ms)
    resp = client.post("/onedrive/disconnect", headers=ORIGIN, follow_redirects=True)
    assert b"Not connected" in resp.data
    assert account(admin) is None


def test_sign_in_posts_are_csrf_guarded(client, ms):
    assert client.post("/onedrive/connect").status_code == 403
    assert client.post("/onedrive/folders", data={"folder_1": "x"},
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert ms.calls == []


# ---- folders --------------------------------------------------------------------

def test_folder_per_car(client, admin):
    assert set_folder(client, " /Cars/Test car/ ").status_code == 303
    assert admin.execute("SELECT car_id, onedrive_folder FROM expenses.car_folder").fetchall() == [
        {"car_id": 1, "onedrive_folder": "Cars/Test car"}]
    assert 'value="Cars/Test car"' in client.get("/onedrive").data.decode()
    bad = set_folder(client, "Cars/../x")
    assert bad.status_code == 400 and b"folder" in bad.data.lower()
    assert set_folder(client, "").status_code == 303     # blank clears it
    assert admin.execute("SELECT count(*) AS n FROM expenses.car_folder").fetchone()["n"] == 0


def test_folder_for_unknown_car_is_ignored(client, admin):
    assert set_folder(client, "x", car_id=99).status_code == 303
    assert admin.execute("SELECT count(*) AS n FROM expenses.car_folder").fetchone()["n"] == 0


# ---- upload ---------------------------------------------------------------------

def test_upload_on_create(client, ms, admin):
    sign_in(client, ms)
    set_folder(client, "Cars/Test car/Invoices")
    assert b'name="invoice_file"' in client.get("/new").data
    resp = client.post("/new", data=with_pdf(form(admin, invoice_url="")), headers=ORIGIN)
    assert resp.status_code == 303
    (put,) = ms.uploads()
    method, url, body, headers = put
    assert unquote(urlparse(url).path).endswith(
        "/me/drive/root:/Cars/Test car/Invoices/2026-11-07 Tyres - Tyre Shop.pdf:/content")
    assert "conflictBehavior=rename" in url    # never overwrite an existing invoice
    assert body == PDF and headers["Content-Type"] == "application/pdf"
    assert headers["Authorization"].startswith("Bearer AT-")
    (row,) = rows(admin)
    assert row["invoice_url"] == WEB_URL


def test_refresh_token_rotation_is_saved(client, ms, admin):
    sign_in(client, ms)
    set_folder(client, "Cars")
    client.post("/new", data=with_pdf(form(admin)), headers=ORIGIN)
    client.post("/new", data=with_pdf(form(admin)), headers=ORIGIN)
    sent = [parse_qs(c[2].decode()).get("refresh_token") for c in ms.calls if c[1].endswith("/token")]
    assert sent[-2:] == [["RT-1"], ["RT-2"]]   # each upload uses the newest refresh token
    stored = Fernet(KEY.encode()).decrypt(account(admin)["refresh_token"].encode())
    assert stored == b"RT-3"


def test_upload_replaces_link_on_edit(client, ms, admin):
    client.post("/new", data=form(admin), headers=ORIGIN)
    sign_in(client, ms)
    set_folder(client, "Cars")
    assert client.post("/1/edit", data=with_pdf(form(admin)), headers=ORIGIN).status_code == 303
    assert rows(admin)[0]["invoice_url"] == WEB_URL
    # editing without a new file keeps the link
    assert client.post("/1/edit", data=form(admin, invoice_url=WEB_URL), headers=ORIGIN).status_code == 303
    assert len(ms.uploads()) == 1


@pytest.mark.parametrize("setup,expected", [
    ("no-sign-in", "Connect OneDrive"),
    ("no-folder", "folder for this car"),
])
def test_upload_needs_sign_in_and_folder(client, ms, admin, setup, expected):
    if setup == "no-folder":
        sign_in(client, ms)
    else:
        set_folder(client, "Cars")
    resp = client.post("/new", data=with_pdf(form(admin, vendor="Keep me")), headers=ORIGIN)
    assert resp.status_code == 400
    assert expected in resp.data.decode() and b"Keep me" in resp.data
    assert rows(admin) == [] and ms.uploads() == []


def test_not_a_pdf_is_refused_before_upload(client, ms, admin):
    sign_in(client, ms)
    set_folder(client, "Cars")
    resp = client.post("/new", data=with_pdf(form(admin), content=b"\xff\xd8\xff", name="x.jpg"),
                       headers=ORIGIN)
    assert resp.status_code == 400 and b"PDF" in resp.data
    assert rows(admin) == [] and ms.uploads() == []


def test_other_errors_mean_no_upload(client, ms, admin):
    sign_in(client, ms)
    set_folder(client, "Cars")
    resp = client.post("/new", data=with_pdf(form(admin, amount="abc")), headers=ORIGIN)
    assert resp.status_code == 400 and b"choose the PDF again" in resp.data
    assert ms.uploads() == []


def test_upload_failure_saves_nothing(client, ms, admin):
    sign_in(client, ms)
    set_folder(client, "Cars")
    ms.upload_status = 403
    resp = client.post("/new", data=with_pdf(form(admin)), headers=ORIGIN)
    assert resp.status_code == 400 and b"OneDrive" in resp.data and b"Nope" in resp.data
    assert rows(admin) == []


def test_expired_sign_in_is_explained(client, ms, admin):
    sign_in(client, ms)
    set_folder(client, "Cars")
    ms.refresh_ok = False
    resp = client.post("/new", data=with_pdf(form(admin)), headers=ORIGIN)
    assert resp.status_code == 400 and b"sign in again" in resp.data
    assert rows(admin) == []


def test_huge_upload_is_refused(client, ms, admin):
    from tmexpenses.validation import MAX_PDF_BYTES
    sign_in(client, ms)
    resp = client.post("/new", data=with_pdf(form(admin), content=PDF + b"0" * (MAX_PDF_BYTES * 2)),
                       headers=ORIGIN)
    assert resp.status_code == 413 and b"20 MB" in resp.data
    assert ms.uploads() == []


def test_app_role_can_use_onedrive_tables_only(pg, admin):
    with psycopg.connect(pg.app_url, autocommit=True) as conn:
        conn.execute("SELECT * FROM expenses.car_folder")
        conn.execute("SELECT * FROM expenses.onedrive_account")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("DROP TABLE expenses.car_folder")

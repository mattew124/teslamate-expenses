"""Uploading invoice PDFs to OneDrive: setup, sign-in, folder per car, the upload itself.

Microsoft is replaced by FakeMicrosoft, handed to create_app as its HTTP transport,
so these run offline and can see exactly what would have been sent. The browser
side of the sign-in (Microsoft's own login page) is played by following the
redirect's parameters by hand.
"""

import base64
import hashlib
import html
import re
import io
import json
from datetime import date, timedelta
from urllib.parse import parse_qs, unquote, urlparse

import psycopg
import pytest
from cryptography.fernet import Fernet
from psycopg.rows import dict_row

from tmexpenses import create_app, onedrive
from tests.test_app import ORIGIN, form, rows
from tests.test_validation import PDF

KEY = Fernet.generate_key().decode()
CLIENT_ID = "11111111-2222-3333-4444-555555555555"
SECRET = "s3cret~value"
WEB_URL = "https://onedrive.live.com/?id=FILE1"
CALLBACK = "http://localhost/onedrive/callback"

# A small drive: root -> Cars -> Test car -> Invoices
FOLDERS = {
    "ROOT": {"name": "root", "path": None, "parent": None},
    "CARS": {"name": "Cars", "path": "/drive/root:", "parent": "ROOT"},
    "TESTCAR": {"name": "Test car", "path": "/drive/root:/Cars", "parent": "CARS"},
    "INV": {"name": "Invoices", "path": "/drive/root:/Cars/Test car", "parent": "TESTCAR"},
}
CHILDREN = {"ROOT": ["CARS"], "CARS": ["TESTCAR"], "TESTCAR": ["INV"], "INV": []}


def item(key):
    f = FOLDERS[key]
    out = {"id": key, "name": f["name"], "folder": {"childCount": len(CHILDREN[key])}}
    if f["path"]:
        out["parentReference"] = {"path": f["path"], "id": f["parent"]}
    return out


class FakeMicrosoft:
    """Just enough of the Microsoft identity platform and Graph for the app."""

    def __init__(self):
        self.calls = []
        self.refresh_ok = True
        self.upload_status = 201
        self.refresh_count = 0
        self.challenge = None
        self.created = []

    def __call__(self, method, url, data=None, headers=None, raw=False):
        headers = headers or {}
        self.calls.append((method, url, data, headers))
        path = unquote(urlparse(url).path)
        if path.endswith("/oauth2/v2.0/token"):
            form = parse_qs(data.decode())
            assert form["client_id"] == [CLIENT_ID] and form["client_secret"] == [SECRET]
            grant = form["grant_type"][0]
            if grant == "authorization_code":
                if form["code"] != ["AUTH-CODE"]:
                    return 400, {"error": "invalid_grant", "error_description": "bad code"}
                assert form["redirect_uri"] == [CALLBACK]
                verifier = form["code_verifier"][0].encode()
                expected = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode()
                assert expected == self.challenge, "PKCE verifier doesn't match the challenge"
                return 200, {"access_token": "AT-1", "refresh_token": "RT-1"}
            assert grant == "refresh_token"
            if not self.refresh_ok:
                return 400, {"error": "invalid_grant", "error_description": "expired"}
            self.refresh_count += 1
            n = self.refresh_count + 1
            return 200, {"access_token": f"AT-{n}", "refresh_token": f"RT-{n}"}
        assert headers["Authorization"].startswith("Bearer AT-")
        if method == "GET" and path.endswith("/me/drive"):
            return 200, {"owner": {"user": {"displayName": "Test Owner"}}}
        if method == "GET" and path.endswith("/children"):
            parent = "ROOT" if path.endswith("/me/drive/root/children") else path.split("/items/")[1].split("/")[0]
            files = [{"id": "F1", "name": "receipt.pdf", "file": {}}]
            return 200, {"value": [item(k) for k in CHILDREN[parent]] + files}
        if method == "GET" and path.endswith("/me/drive/root"):
            return 200, item("ROOT")
        if method == "GET" and "/me/drive/items/" in path:
            key = path.rsplit("/", 1)[1]
            return (200, item(key)) if key in FOLDERS else (404, {"error": {"code": "itemNotFound"}})
        if method == "POST" and path.endswith("/children"):
            parent = "ROOT" if path.endswith("/me/drive/root/children") else path.split("/items/")[1].split("/")[0]
            assert headers["Content-Type"] == "application/json"
            req = json.loads(data)
            assert req["folder"] == {} and req["@microsoft.graph.conflictBehavior"] == "fail"
            if any(FOLDERS[k]["name"].lower() == req["name"].lower() for k in CHILDREN[parent]):
                return 409, {"error": {"code": "nameAlreadyExists", "message": "Name already exists"}}
            key = f"NEW{len(FOLDERS)}"
            parent_path = "/drive/root:" + ("" if parent == "ROOT" else
                                            item(parent)["parentReference"]["path"].partition("root:")[2]
                                            + "/" + FOLDERS[parent]["name"])
            FOLDERS[key] = {"name": req["name"], "path": parent_path, "parent": parent}
            CHILDREN[key] = []
            CHILDREN[parent].append(key)
            self.created.append((parent, req["name"]))
            return 201, item(key)
        if method == "PUT" and path.endswith(":/content"):
            if self.upload_status >= 400:
                return self.upload_status, {"error": {"code": "accessDenied", "message": "Nope"}}
            return 201, {"id": "FILE1", "webUrl": WEB_URL}
        raise AssertionError(f"unexpected call {method} {url}")

    def uploads(self):
        return [c for c in self.calls if c[0] == "PUT"]


@pytest.fixture()
def ms():
    snapshot = ({k: dict(v) for k, v in FOLDERS.items()}, {k: list(v) for k, v in CHILDREN.items()})
    yield FakeMicrosoft()
    FOLDERS.clear(); FOLDERS.update(snapshot[0])      # undo folders a test created
    CHILDREN.clear(); CHILDREN.update(snapshot[1])


@pytest.fixture()
def app_for(pg, admin, monkeypatch):
    monkeypatch.delenv("BASE_PATH", raising=False)
    monkeypatch.setenv("APP_TZ", "Australia/Brisbane")

    def make(transport, key=KEY):
        if key is None:
            monkeypatch.delenv("EXPENSES_ENCRYPTION_KEY", raising=False)
        else:
            monkeypatch.setenv("EXPENSES_ENCRYPTION_KEY", key)
        app = create_app(connect=lambda: psycopg.connect(pg.app_url, row_factory=dict_row),
                         onedrive_transport=transport)
        app.testing = True
        return app.test_client()
    return make


@pytest.fixture()
def client(app_for, ms):
    return app_for(ms)


def save_credentials(client, client_id=CLIENT_ID, secret=SECRET, expires=""):
    return client.post("/onedrive/credentials", headers=ORIGIN, data={
        "client_id": client_id, "client_secret": secret, "secret_expires": expires})


def start_sign_in(client, ms):
    """Press Connect -> the page that sends the browser on to Microsoft (a link,
    followed by app.js: a plain navigation, which form-action doesn't police).
    Returns the sign-in address's query parameters."""
    resp = client.post("/onedrive/connect", headers=ORIGIN)
    assert resp.status_code == 200
    page = resp.data.decode()
    href = html.unescape(re.search(r'<a id="continue-sign-in" href="([^"]+)"', page).group(1))
    loc = urlparse(href)
    assert loc.scheme == "https" and loc.netloc == "login.microsoftonline.com"
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    ms.challenge = q.get("code_challenge")
    return q


def sign_in(client, ms):
    save_credentials(client)
    q = start_sign_in(client, ms)
    resp = client.get(f"/onedrive/callback?code=AUTH-CODE&state={q['state']}")
    assert resp.status_code == 303 and "msg=connected" in resp.headers["Location"]


def choose_folder(client, folder_id="INV", car_id=1):
    return client.post(f"/onedrive/folders/{car_id}", data={"folder_id": folder_id}, headers=ORIGIN)


def with_pdf(data, content=PDF, name="invoice.pdf"):
    return {**data, "invoice_file": (io.BytesIO(content), name, "application/pdf")}


def account(admin):
    return admin.execute("SELECT * FROM expenses.onedrive_account").fetchone()


# ---- not configured -------------------------------------------------------------

def test_hidden_without_token_key(app_for, ms, admin):
    c = app_for(ms, key=None)
    assert b'name="invoice_file"' not in c.get("/new").data
    assert b"/onedrive" not in c.get("/").data
    page = c.get("/onedrive").data.decode()
    assert "EXPENSES_ENCRYPTION_KEY" in page and "Connect OneDrive" not in page
    assert save_credentials(c).status_code == 404
    assert c.post("/onedrive/connect", headers=ORIGIN).status_code == 404
    assert c.post("/new", data=with_pdf(form(admin)), headers=ORIGIN).status_code == 303
    assert ms.calls == []


def test_bad_token_key_refuses_to_start(app_for, ms):
    with pytest.raises(ValueError, match="EXPENSES_ENCRYPTION_KEY"):
        app_for(ms, key="not-a-fernet-key")


# ---- setup ----------------------------------------------------------------------

def test_setup_page_shows_the_redirect_uri_to_register(client):
    page = client.get("/onedrive").data.decode()
    assert CALLBACK in page and 'name="client_secret"' in page
    assert "Connect OneDrive</button>" not in page   # nothing to connect with yet


def test_redirect_uri_follows_the_proxy(app_for, ms, monkeypatch):
    monkeypatch.setenv("BASE_PATH", "/expenses")
    c = app_for(ms)
    page = c.get("/expenses/onedrive", headers={"X-Forwarded-Proto": "https",
                                                "X-Forwarded-Host": "cars.example.org"}).data.decode()
    assert "https://cars.example.org/expenses/onedrive/callback" in page


def test_credentials_are_validated_and_secret_encrypted(client, admin):
    bad = save_credentials(client, client_id="not-a-guid")
    assert bad.status_code == 400 and b"Application (client) ID" in bad.data
    assert save_credentials(client, secret="").status_code == 400
    assert save_credentials(client, expires="soon").status_code == 400
    assert account(admin) is None
    assert save_credentials(client, expires="2027-06-30").status_code == 303
    row = account(admin)
    assert row["client_id"] == CLIENT_ID and row["secret_expires"] == date(2027, 6, 30)
    assert SECRET not in json.dumps(row, default=str)
    assert Fernet(KEY.encode()).decrypt(row["client_secret"].encode()).decode() == SECRET
    page = client.get("/onedrive").data.decode()
    assert SECRET not in page and CLIENT_ID in page and "Connect OneDrive" in page


def test_secret_expiry_warning(client):
    save_credentials(client, expires=(date.today() + timedelta(days=10)).isoformat())
    assert b"secret expires" in client.get("/onedrive").data
    save_credentials(client, expires=(date.today() + timedelta(days=200)).isoformat())
    assert b"secret expires" not in client.get("/onedrive").data


def test_new_credentials_drop_the_old_sign_in(client, ms, admin):
    sign_in(client, ms)
    save_credentials(client)
    assert account(admin)["refresh_token"] is None


# ---- sign-in --------------------------------------------------------------------

def test_web_sign_in(client, ms, admin):
    save_credentials(client)
    q = start_sign_in(client, ms)
    assert q["client_id"] == CLIENT_ID and q["redirect_uri"] == CALLBACK
    assert q["response_type"] == "code" and q["code_challenge_method"] == "S256"
    assert "offline_access" in q["scope"] and "Files.ReadWrite" in q["scope"]
    assert len(q["state"]) >= 32
    resp = client.get(f"/onedrive/callback?code=AUTH-CODE&state={q['state']}", follow_redirects=True)
    assert b"Connected as <strong>Test Owner</strong>" in resp.data
    row = account(admin)
    assert row["account"] == "Test Owner" and row["auth_state"] is None
    assert "RT-1" not in json.dumps(row, default=str)
    assert Fernet(KEY.encode()).decrypt(row["refresh_token"].encode()) == b"RT-1"


def test_callback_with_wrong_state_is_refused(client, ms, admin):
    save_credentials(client)
    start_sign_in(client, ms)
    resp = client.get("/onedrive/callback?code=AUTH-CODE&state=forged")
    assert resp.status_code == 400 and account(admin)["refresh_token"] is None


def test_callback_from_another_browser_is_refused(client, app_for, ms, admin):
    """The state is also tied to a cookie, so a link to the callback can't finish
    a sign-in in a browser that didn't start it (login CSRF)."""
    save_credentials(client)
    q = start_sign_in(client, ms)
    other = app_for(ms)
    resp = other.get(f"/onedrive/callback?code=AUTH-CODE&state={q['state']}")
    assert resp.status_code == 400 and account(admin)["refresh_token"] is None


def test_callback_without_starting_is_refused(client, ms):
    save_credentials(client)
    assert client.get("/onedrive/callback?code=AUTH-CODE&state=x").status_code == 400


def test_sign_in_cancelled_at_microsoft(client, ms, admin):
    save_credentials(client)
    q = start_sign_in(client, ms)
    resp = client.get(f"/onedrive/callback?error=access_denied&error_description=User+cancelled&state={q['state']}")
    assert resp.status_code == 400 and b"User cancelled" in resp.data
    assert account(admin)["refresh_token"] is None


def test_disconnect_forgets_the_sign_in_but_keeps_credentials(client, ms, admin):
    sign_in(client, ms)
    resp = client.post("/onedrive/disconnect", headers=ORIGIN, follow_redirects=True)
    assert b"Connect OneDrive" in resp.data
    row = account(admin)
    assert row["refresh_token"] is None and row["client_id"] == CLIENT_ID


def test_posts_are_csrf_guarded(client, ms):
    assert client.post("/onedrive/connect").status_code == 403
    assert client.post("/onedrive/credentials", data={"client_id": CLIENT_ID}).status_code == 403
    assert client.post("/onedrive/folders/1", data={"folder_id": "INV"},
                       headers={"Origin": "https://evil.example"}).status_code == 403
    assert ms.calls == []


# ---- folders --------------------------------------------------------------------

def test_browse_and_choose_folder(client, ms, admin):
    sign_in(client, ms)
    page = client.get("/onedrive/browse/1").data.decode()
    assert "Cars" in page and "receipt.pdf" not in page         # folders only
    page = client.get("/onedrive/browse/1?folder=TESTCAR").data.decode()
    assert "/Cars/Test car" in page and "Invoices" in page
    assert choose_folder(client).status_code == 303
    assert admin.execute("SELECT car_id, folder_id, folder_path FROM expenses.car_folder").fetchall() == [
        {"car_id": 1, "folder_id": "INV", "folder_path": "/Cars/Test car/Invoices"}]
    assert "/Cars/Test car/Invoices" in client.get("/onedrive").data.decode()


def test_browse_needs_sign_in_and_a_real_car(client, ms):
    save_credentials(client)
    resp = client.get("/onedrive/browse/1")
    assert resp.status_code == 303 and resp.headers["Location"].endswith("/onedrive")
    sign_in(client, ms)
    assert client.get("/onedrive/browse/99").status_code == 404
    assert choose_folder(client, car_id=99).status_code == 404
    assert choose_folder(client, folder_id="NOPE").status_code == 400


def test_folder_can_be_cleared(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    assert client.post("/onedrive/folders/1/clear", headers=ORIGIN).status_code == 303
    assert admin.execute("SELECT count(*) AS n FROM expenses.car_folder").fetchone()["n"] == 0


# ---- upload ---------------------------------------------------------------------

def test_upload_name_follows_the_date_format_setting(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    admin.execute("UPDATE expenses.setting SET invoice_date_format = 'yyyy-mm-dd'")
    client.post("/new", data=with_pdf(form(admin, invoice_url="")), headers=ORIGIN)
    (put,) = ms.uploads()
    assert unquote(urlparse(put[1]).path).endswith(":/2026-11-07 Tyres - Tyre Shop.pdf:/content")


def test_upload_name_follows_the_name_template(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    admin.execute("UPDATE expenses.setting SET invoice_name_format = '%date - %category - %vendor'")
    client.post("/new", data=with_pdf(form(admin, invoice_url="")), headers=ORIGIN)
    (put,) = ms.uploads()
    assert unquote(urlparse(put[1]).path).endswith(":/261107 - Tyres - Tyre Shop.pdf:/content")


def test_upload_on_create(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    assert b'name="invoice_file"' in client.get("/new").data
    resp = client.post("/new", data=with_pdf(form(admin, invoice_url="")), headers=ORIGIN)
    assert resp.status_code == 303
    (put,) = ms.uploads()
    method, url, body, headers = put
    # by folder id, so renaming or moving the folder in OneDrive doesn't break uploads; into the
    # category's subfolder (Maintenance), which was made inside the car's folder (INV)
    assert ms.created == [("INV", "Maintenance")]
    assert re.search(r"/me/drive/items/NEW\d+:/261107 Tyres - Tyre Shop.pdf:/content$", unquote(urlparse(url).path))
    assert "conflictBehavior=rename" in url    # never overwrite an existing invoice
    assert body == PDF and headers["Content-Type"] == "application/pdf"
    (row,) = rows(admin)
    assert row["invoice_url"] == WEB_URL


def test_refresh_token_rotation_is_saved(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    before = len([c for c in ms.calls if c[1].endswith("/token")])
    client.post("/new", data=with_pdf(form(admin)), headers=ORIGIN)
    client.post("/new", data=with_pdf(form(admin, date="2027-03-01")), headers=ORIGIN)   # a different expense
    sent = [parse_qs(c[2].decode()).get("refresh_token") for c in ms.calls if c[1].endswith("/token")]
    used = sent[before:]
    # each refresh spends the newest token: no token is ever sent twice
    assert len(used) == len({u[0] for u in used}) >= 2
    stored = Fernet(KEY.encode()).decrypt(account(admin)["refresh_token"].encode())
    assert stored == f"RT-{ms.refresh_count + 1}".encode()


def test_upload_replaces_link_on_edit(client, ms, admin):
    client.post("/new", data=form(admin), headers=ORIGIN)
    sign_in(client, ms)
    choose_folder(client)
    assert client.post("/1/edit", data=with_pdf(form(admin)), headers=ORIGIN).status_code == 303
    assert rows(admin)[0]["invoice_url"] == WEB_URL
    assert client.post("/1/edit", data=form(admin, invoice_url=WEB_URL), headers=ORIGIN).status_code == 303
    assert len(ms.uploads()) == 1


@pytest.mark.parametrize("setup,expected", [
    ("no-sign-in", "Connect OneDrive"),
    ("no-folder", "folder for this car"),
])
def test_upload_needs_sign_in_and_folder(client, ms, admin, setup, expected):
    if setup == "no-folder":
        sign_in(client, ms)
    resp = client.post("/new", data=with_pdf(form(admin, vendor="Keep me")), headers=ORIGIN)
    assert resp.status_code == 400
    assert expected in resp.data.decode() and b"Keep me" in resp.data
    assert rows(admin) == [] and ms.uploads() == []


def test_not_a_pdf_is_refused_before_upload(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    resp = client.post("/new", data=with_pdf(form(admin), content=b"\xff\xd8\xff", name="x.jpg"),
                       headers=ORIGIN)
    assert resp.status_code == 400 and b"PDF" in resp.data
    assert rows(admin) == [] and ms.uploads() == []


def test_other_errors_mean_no_upload(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    resp = client.post("/new", data=with_pdf(form(admin, amount="abc")), headers=ORIGIN)
    assert resp.status_code == 400 and b"choose the PDF again" in resp.data
    assert ms.uploads() == []


def test_upload_failure_saves_nothing(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    ms.upload_status = 403
    resp = client.post("/new", data=with_pdf(form(admin)), headers=ORIGIN)
    assert resp.status_code == 400 and b"OneDrive" in resp.data and b"Nope" in resp.data
    assert rows(admin) == []


def test_onedrive_events_are_logged_without_secrets(client, ms, admin, caplog):
    import logging
    caplog.set_level(logging.DEBUG, logger="tmexpenses")
    sign_in(client, ms)
    choose_folder(client)
    client.post("/new", data=with_pdf(form(admin, invoice_url="")), headers=ORIGIN)
    text = "\n".join(caplog.messages)
    assert "OneDrive connected" in text and "invoice uploaded" in text
    for secret in (SECRET, "AUTH-CODE", "AT-", "RT-"):
        assert secret not in text, secret
    # File names can hold the vendor, and appear at debug only.
    assert "Tyre Shop" not in "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.INFO)
    ms.upload_status = 403
    client.post("/new", data=with_pdf({**form(admin), "confirm_duplicate": "1"}), headers=ORIGIN)
    assert any(r.levelno == logging.WARNING and "upload" in r.getMessage() for r in caplog.records)
    client.post("/onedrive/disconnect", headers=ORIGIN)
    assert "OneDrive disconnected" in "\n".join(caplog.messages)


def test_old_variable_name_is_explained(monkeypatch, caplog):
    import logging
    monkeypatch.delenv("EXPENSES_ENCRYPTION_KEY", raising=False)
    monkeypatch.setenv("ONEDRIVE_TOKEN_KEY", Fernet.generate_key().decode())
    caplog.set_level(logging.WARNING, logger="tmexpenses")
    from tmexpenses import create_app
    create_app(connect=lambda: None)
    assert any("ONEDRIVE_TOKEN_KEY" in m and "EXPENSES_ENCRYPTION_KEY" in m for m in caplog.messages)


def test_expired_sign_in_is_explained(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
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


def test_setup_page_has_the_full_registration_walkthrough(client):
    page = client.get("/onedrive").data.decode()
    for text in ("New registration", "Personal Microsoft accounts only", "Web",
                 "Allow public client flows", "AADSTS90023", "Files.ReadWrite",
                 "Secret ID", "24 months", "Application (client) ID"):
        assert text in page, text
    assert f'data-copy="{CALLBACK}"' in page        # one-click copy of the exact address


def test_setup_page_warns_that_a_new_redirect_uri_takes_a_few_minutes(client):
    """A freshly added redirect URI can be refused ("invalid request") for a few minutes."""
    page = client.get("/onedrive").data.decode()
    assert "few minutes" in page and "invalid request" in page.lower()


def test_connect_never_relies_on_a_form_redirect(client, ms):
    """Chrome applies form-action to the redirect after a form POST, so Connect
    hands the browser a link instead, and the CSP stays same-origin everywhere."""
    save_credentials(client)
    resp = client.post("/onedrive/connect", headers=ORIGIN)
    assert resp.status_code == 200 and "Location" not in resp.headers
    assert 'data-auto-follow' in resp.data.decode()
    for r in (resp, client.get("/onedrive"), client.get("/new")):
        form_action = next(d for d in r.headers["Content-Security-Policy"].split(";")
                           if d.strip().startswith("form-action"))
        assert form_action.strip() == "form-action 'self'"


# ---- new folder -----------------------------------------------------------------

def new_folder(client, name, parent="TESTCAR", car_id=1):
    return client.post(f"/onedrive/browse/{car_id}/new-folder", headers=ORIGIN,
                       data={"parent_id": parent, "name": name})


def test_new_folder_opens_it_ready_to_use(client, ms):
    sign_in(client, ms)
    assert 'name="name"' in client.get("/onedrive/browse/1?folder=TESTCAR").data.decode()
    resp = new_folder(client, "  2027 invoices ")
    assert resp.status_code == 303
    assert ms.created == [("TESTCAR", "2027 invoices")]
    new_id = parse_qs(urlparse(resp.headers["Location"]).query)["folder"][0]
    page = client.get(resp.headers["Location"]).data.decode()
    assert "/Cars/Test car/2027 invoices" in page
    assert choose_folder(client, folder_id=new_id).status_code == 303


def test_new_folder_at_the_top_of_the_drive(client, ms):
    sign_in(client, ms)
    assert new_folder(client, "Car invoices", parent="").status_code == 303
    assert ms.created == [("ROOT", "Car invoices")]


@pytest.mark.parametrize("name,expected", [
    ("", "Enter a name"), ("a/b", "can't contain"), ("bad:name", "can't contain"),
    ("trailing.", "can't end"), ("x" * 256, "too long"), ("Invoices", "already exists"),
])
def test_new_folder_names_are_checked(client, ms, name, expected):
    sign_in(client, ms)
    resp = new_folder(client, name, parent="TESTCAR")   # TESTCAR already holds "Invoices"
    page = html.unescape(resp.data.decode())
    assert resp.status_code == 400 and expected in page
    assert ms.created == []
    assert "/Cars/Test car" in page                      # still browsing the same folder


def test_new_folder_is_guarded(client, ms):
    sign_in(client, ms)
    assert client.post("/onedrive/browse/1/new-folder", data={"parent_id": "", "name": "x"}).status_code == 403
    assert new_folder(client, "x", car_id=99).status_code == 404
    assert ms.created == []


# ---- the PDF comes first on the Add form -------------------------------------------------

def test_the_pdf_field_follows_the_car_choice_and_leads_the_rest(client, ms, admin):
    sign_in(client, ms)
    page = client.get("/new").data.decode()
    assert page.index('name="car_id"') < page.index('name="invoice_file"') < page.index('name="category_id"')
    assert page.index('name="invoice_file"') < page.index('name="amount"')
    assert 'id="prefill-hint"' in page and "filled in from what can be read" in page


def test_the_edit_page_has_the_pdf_field_too_but_doesnt_promise_prefill(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    client.post("/new", data=form(admin), headers=ORIGIN)
    page = client.get("/1/edit").data.decode()
    assert 'name="invoice_file"' in page and 'id="prefill-hint"' not in page and "data-prefill-url" not in page
    assert page.index('name="car_id"') < page.index('name="invoice_file"') < page.index('name="category_id"')


def test_without_onedrive_the_add_form_has_no_pdf_field(app_for, ms):
    page = app_for(ms, key=None).get("/new").data.decode()
    assert 'name="invoice_file"' not in page


# ---- what a real drive does that the happy path doesn't ----------------------

def graph_of(*replies):
    """A OneDrive whose Graph answers come from a list, recording the URLs asked for."""
    asked, queue = [], list(replies)

    def transport(method, url, data=None, headers=None, raw=False):
        asked.append(url)
        return queue.pop(0)
    return onedrive.OneDrive(KEY, transport), asked


NEXT = onedrive.GRAPH + "/me/drive/items/X/children?$skiptoken=2"


def test_folder_listing_follows_every_page():
    drive, asked = graph_of(
        (200, {"value": [{"id": "A", "name": "Alpha", "folder": {}}], "@odata.nextLink": NEXT}),
        (200, {"value": [{"id": "B", "name": "Beta", "folder": {}}]}))
    assert [f["name"] for f in drive.subfolders("AT-1", "X")] == ["Alpha", "Beta"]
    assert asked[1] == NEXT


def test_pdf_listing_follows_every_page():
    pdf = {"file": {}, "webUrl": "https://onedrive.live.com/f", "size": 1}
    drive, _ = graph_of(
        (200, {"value": [{"id": "1", "name": "a.pdf", **pdf}], "@odata.nextLink": NEXT}),
        (200, {"value": [{"id": "2", "name": "b.pdf", **pdf}]}))
    assert [f["name"] for f in drive.pdfs("AT-1", "X")] == ["a.pdf", "b.pdf"]


def test_a_next_page_off_graph_is_not_followed():
    """The access token goes with each page request, so only Microsoft Graph may be asked."""
    drive, asked = graph_of((200, {"value": [], "@odata.nextLink": "https://example.com/steal"}))
    assert drive.subfolders("AT-1", "X") == [] and len(asked) == 1


@pytest.mark.parametrize("busy", [429, 503])
def test_a_busy_reply_is_retried(busy, monkeypatch):
    monkeypatch.setattr(onedrive, "RETRY_WAITS", (0, 0))
    drive, asked = graph_of((busy, {}), (busy, {}), (200, {"value": [{"id": "A", "name": "Alpha", "folder": {}}]}))
    assert [f["name"] for f in drive.subfolders("AT-1", "X")] == ["Alpha"] and len(asked) == 3


def test_staying_busy_is_explained(monkeypatch):
    monkeypatch.setattr(onedrive, "RETRY_WAITS", (0, 0))
    drive, asked = graph_of((429, {}), (429, {}), (429, {}))
    with pytest.raises(onedrive.OneDriveError, match="busy"):
        drive.subfolders("AT-1", "X")
    assert len(asked) == 3


def test_a_refusal_is_not_retried(monkeypatch):
    monkeypatch.setattr(onedrive, "RETRY_WAITS", (0, 0))
    drive, asked = graph_of((403, {"error": {"code": "accessDenied", "message": "Nope"}}))
    with pytest.raises(onedrive.OneDriveError, match="Nope"):
        drive.subfolders("AT-1", "X")
    assert len(asked) == 1


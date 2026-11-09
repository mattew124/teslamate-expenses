"""Importing invoices already sitting in a car's OneDrive folder: scan, preview, confirm.

PDF text is canned (the bytes name the document) so these don't need poppler; the
real extraction is exercised on its own in test_invoices.
"""

import pytest

from tmexpenses import invoices
from tests.test_app import ORIGIN, rows
from tests.test_onedrive import FakeMicrosoft, app_for, choose_folder, sign_in  # noqa: F401  (fixtures)

TYRES_TEXT = "Invoice Date: 24/10/2026\n  Odometer 45,120 km\n  Example Tyres Pty Ltd\n  Total   $1,234.50\n"
FILES = {  # id -> (name, parent, pdf bytes)
    "P1": ("261024 - Tyres.pdf", "INV", b"%PDF-tyres"),
    "P2": ("scan.pdf", "INV", b"%PDF-scan"),
    "P3": ("250105 - Insurer cert.pdf", "SUB", b"%PDF-insurer"),
}
TEXT = {b"%PDF-tyres": TYRES_TEXT, b"%PDF-scan": "", b"%PDF-insurer": "Total premium  $980.40\n"}


class ImportDrive(FakeMicrosoft):
    def __call__(self, method, url, data=None, headers=None, raw=False):
        from urllib.parse import unquote, urlparse
        path = unquote(urlparse(url).path)
        if url.startswith("https://cdn.example/"):
            assert "Authorization" not in (headers or {}), "the CDN link must be fetched without the token"
            self.calls.append((method, url, data, headers or {}))
            return 200, FILES[path.rsplit("/", 1)[1]][2]
        if method == "GET" and path.endswith("/children") and path.split("/")[-2] in ("INV", "SUB"):
            self.calls.append((method, url, data, headers))
            parent = path.split("/")[-2]
            kids = [{"id": i, "name": n, "file": {}, "size": 10, "webUrl": f"https://od.example/{i}"}
                    for i, (n, p, _) in FILES.items() if p == parent]
            if parent == "INV":
                kids.append({"id": "SUB", "name": "Insurance", "folder": {}})
            return 200, {"value": kids}
        if method == "GET" and path.rsplit("/", 1)[-1] in FILES:
            self.calls.append((method, url, data, headers))
            i = path.rsplit("/", 1)[1]
            return 200, {"id": i, "size": 10, "@microsoft.graph.downloadUrl": f"https://cdn.example/{i}"}
        return super().__call__(method, url, data, headers, raw)


@pytest.fixture()
def ms():
    return ImportDrive()


@pytest.fixture()
def client(app_for, ms, monkeypatch):  # noqa: F811
    monkeypatch.setattr(invoices, "extract_text", lambda pdf: TEXT[pdf])
    c = app_for(ms)
    sign_in(c, ms)
    choose_folder(c)
    return c


def cat(admin, name):
    return str(admin.execute("SELECT id FROM expenses.category WHERE name = %s", (name,)).fetchone()["id"])


def post_rows(admin, **over):
    data = {"count": "3",
            "include_0": "on", "url_0": "https://od.example/P3", "name_0": "250105 - Insurer cert.pdf",
            "date_0": "2025-01-05", "category_id_0": cat(admin, "Insurance"), "amount_0": "980.40",
            "vendor_0": "Insurer", "odometer_km_0": "",
            "include_1": "on", "url_1": "https://od.example/P1", "name_1": "261024 - Tyres.pdf",
            "date_1": "2026-10-24", "category_id_1": cat(admin, "Tyres"), "amount_1": "1,234.50",
            "vendor_1": "Example Tyres", "odometer_km_1": "45120",
            "url_2": "https://od.example/P2", "name_2": "scan.pdf"}  # not ticked
    data.update(over)
    return data


def test_preview_proposes_and_writes_nothing(client, admin):
    page = client.get("/onedrive/import/1").data.decode()
    assert "261024 - Tyres.pdf" in page and "250105 - Insurer cert.pdf" in page and "scan.pdf" in page
    assert 'value="2026-10-24"' in page and 'value="1234.50"' in page and 'value="45120"' in page
    assert "Example Tyres Pty Ltd" in page and "Couldn&#39;t read the date, category, amount" in page
    assert rows(admin) == []


def test_only_complete_guesses_are_ticked(client):
    page = client.get("/onedrive/import/1").data.decode()
    assert page.count(" checked>") == 2   # tyres + insurer; the unreadable scan is left for the owner


def test_confirm_imports_ticked_rows_only(client, admin):
    resp = client.post("/onedrive/import/1", data=post_rows(admin), headers=ORIGIN)
    assert resp.status_code == 303 and "msg=imported" in resp.headers["Location"]
    got = rows(admin)
    assert [(r["invoice_url"], str(r["amount"]), r["vendor"]) for r in got] == [
        ("https://od.example/P3", "980.40", "Insurer"), ("https://od.example/P1", "1234.50", "Example Tyres")]
    assert got[1]["car_id"] == 1 and str(got[1]["odometer_km"]) == "45120.0"
    assert got[1]["occurred_at"].astimezone().strftime("%Y-%m-%d") in ("2026-10-24", "2026-10-23")


def test_already_imported_files_are_left_out(client, admin):
    client.post("/onedrive/import/1", data=post_rows(admin), headers=ORIGIN)
    page = client.get("/onedrive/import/1").data.decode()
    assert "scan.pdf" in page and "Tyres.pdf" not in page and "Insurer" not in page


def test_double_submit_imports_once(client, admin):
    for _ in range(2):
        client.post("/onedrive/import/1", data=post_rows(admin), headers=ORIGIN)
    assert len(rows(admin)) == 2


def test_bad_row_rejects_everything_and_keeps_what_was_typed(client, admin):
    resp = client.post("/onedrive/import/1", data=post_rows(admin, amount_1="", vendor_0="Keep me"), headers=ORIGIN)
    assert resp.status_code == 400
    page = resp.data.decode()
    assert "Enter the amount paid." in page and "Keep me" in page
    assert rows(admin) == []


def test_nothing_ticked_is_refused(client, admin):
    data = {"count": "1", "url_0": "https://od.example/P2", "name_0": "scan.pdf"}
    assert client.post("/onedrive/import/1", data=data, headers=ORIGIN).status_code == 400


def test_bad_link_refused(client, admin):
    resp = client.post("/onedrive/import/1", data=post_rows(admin, url_1="javascript:alert(1)"), headers=ORIGIN)
    assert resp.status_code == 400 and rows(admin) == []


def test_works_in_batches(client, admin, monkeypatch):
    monkeypatch.setattr("tmexpenses.IMPORT_BATCH", 1)
    page = client.get("/onedrive/import/1").data.decode()
    assert "2 more waiting" in page


def test_needs_a_chosen_folder(client, admin):
    client.post("/onedrive/folders/1/clear", headers=ORIGIN)
    resp = client.get("/onedrive/import/1")
    assert resp.status_code == 303 and resp.headers["Location"].endswith("/onedrive")


def test_post_needs_same_origin(client, admin):
    assert client.post("/onedrive/import/1", data=post_rows(admin)).status_code == 403

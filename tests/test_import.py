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
    rename_status = 200
    download_status = 200

    def __call__(self, method, url, data=None, headers=None, raw=False):
        from urllib.parse import unquote, urlparse
        path = unquote(urlparse(url).path)
        if method == "PATCH" and path.rsplit("/", 1)[-1] in FILES:
            self.calls.append((method, url, data, headers))
            if self.rename_status != 200:
                return self.rename_status, {"error": {"code": "nameAlreadyExists", "message": "exists"}}
            i = path.rsplit("/", 1)[1]
            return 200, {"id": i, "webUrl": f"https://od.example/{i}-renamed"}
        if url.startswith("https://cdn.example/"):
            assert "Authorization" not in (headers or {}), "the CDN link must be fetched without the token"
            self.calls.append((method, url, data, headers or {}))
            if self.download_status != 200:
                return self.download_status, b""
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
    from tests.test_onedrive import CHILDREN, FOLDERS
    snapshot = ({k: dict(v) for k, v in FOLDERS.items()}, {k: list(v) for k, v in CHILDREN.items()})
    yield ImportDrive()
    FOLDERS.clear(); FOLDERS.update(snapshot[0])      # undo folders a test created
    CHILDREN.clear(); CHILDREN.update(snapshot[1])


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
    data = {"count": "3", "id_0": "P3", "id_1": "P1", "id_2": "P2",
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
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert "261024 - Tyres.pdf" in page and "250105 - Insurer cert.pdf" in page and "scan.pdf" in page
    assert 'value="2026-10-24"' in page and 'value="1234.50"' in page and 'value="45120"' in page
    assert "Example Tyres Pty Ltd" in page and "Couldn&#39;t read the date, category, amount" in page
    assert rows(admin) == []


def test_only_complete_guesses_are_ticked(client):
    page = client.get("/onedrive/import/1?sync=1").data.decode()
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
    page = client.get("/onedrive/import/1?sync=1").data.decode()
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
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert "2 more waiting" in page


def test_needs_a_chosen_folder(client, admin):
    client.post("/onedrive/folders/1/clear", headers=ORIGIN)
    resp = client.get("/onedrive/import/1")
    assert resp.status_code == 303 and resp.headers["Location"].endswith("/onedrive")


def test_post_needs_same_origin(client, admin):
    assert client.post("/onedrive/import/1", data=post_rows(admin)).status_code == 403


# ---- odometer from TeslaMate, renaming, prefill --------------------------------------

def test_missing_odometer_is_filled_from_teslamate(client, admin):
    # P3 is dated 2025-01-05 (file name) and its text has no odometer; TeslaMate does.
    admin.execute("INSERT INTO positions (latitude, longitude, date, odometer, car_id) VALUES "
                  "(0, 0, '2025-01-04 00:00', 40000.5, 1), (0, 0, '2025-02-01 00:00', 41000, 1)")
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert 'value="40000.5"' in page and "from TeslaMate" in page


def test_a_pdf_odometer_beats_teslamate(client, admin):
    admin.execute("INSERT INTO positions (latitude, longitude, date, odometer, car_id) VALUES (0, 0, '2026-10-01', 1, 1)")
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert 'value="45120"' in page          # the tyre invoice's own odometer


def test_odometer_stays_blank_when_teslamate_has_nothing(client, admin):
    admin.execute("TRUNCATE positions")
    assert 'name="odometer_km_2"' in client.get("/onedrive/import/1?sync=1").data.decode()


@pytest.fixture()
def dashed(admin):
    admin.execute("UPDATE expenses.setting SET invoice_name_format = '%date - %category - %vendor'")


def renames(ms):
    import json
    return [(c[1], json.loads(c[2])) for c in ms.calls if c[0] == "PATCH"]


def test_preview_offers_the_new_name_without_renaming(client, ms, admin, dashed):
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert 'name="rename_0"' in page and "261024 - Tyres - Example Tyres Pty Ltd.pdf" in page
    assert renames(ms) == []


def test_ticked_rename_renames_and_stores_the_new_link(client, ms, admin, dashed):
    resp = client.post("/onedrive/import/1", data=post_rows(admin, rename_1="on"), headers=ORIGIN)
    assert resp.status_code == 303 and "renamed=1" in resp.headers["Location"]
    ((url, body),) = renames(ms)
    assert url.endswith("/me/drive/items/P1") and body["name"] == "261024 - Tyres - Example Tyres.pdf"
    assert body["@microsoft.graph.conflictBehavior"] == "fail"       # never clobber another file
    got = {r["vendor"]: r["invoice_url"] for r in rows(admin)}
    assert got["Example Tyres"] == "https://od.example/P1-renamed"
    assert got["Insurer"] == "https://od.example/P3"                 # not ticked: untouched


def test_unticked_rename_does_nothing(client, ms, admin):
    client.post("/onedrive/import/1", data=post_rows(admin), headers=ORIGIN)
    assert renames(ms) == []


def test_a_failed_rename_still_imports_with_the_old_link(client, ms, admin):
    ms.rename_status = 409
    resp = client.post("/onedrive/import/1", data=post_rows(admin, rename_1="on"), headers=ORIGIN)
    assert resp.status_code == 303 and "rename_failed=1" in resp.headers["Location"]
    assert {r["invoice_url"] for r in rows(admin)} == {"https://od.example/P1", "https://od.example/P3"}
    assert "couldn" in client.get(resp.headers["Location"]).data.decode().lower()


def test_a_file_already_named_right_isnt_renamed(client, ms, admin, dashed):
    data = post_rows(admin, rename_1="on", vendor_1="")      # -> "261024 - Tyres.pdf", the file's own name
    client.post("/onedrive/import/1", data=data, headers=ORIGIN)
    assert renames(ms) == []


def test_prefill_reads_an_uploaded_pdf(client, admin, monkeypatch):
    import io
    r = client.post("/api/prefill", headers=ORIGIN, data={
        "invoice_file": (io.BytesIO(b"%PDF-tyres"), "261024 - Tyres.pdf", "application/pdf")})
    got = r.get_json()
    assert r.status_code == 200
    assert got["date"] == "2026-10-24" and got["amount"] == "1234.50" and got["odometer_km"] == "45120"
    assert got["vendor"] == "Example Tyres Pty Ltd" and got["category_id"] == int(cat(admin, "Tyres"))


def test_prefill_refuses_non_pdfs_and_empty_requests(client, admin):
    import io
    assert client.post("/api/prefill", headers=ORIGIN, data={
        "invoice_file": (io.BytesIO(b"hello"), "x.pdf", "application/pdf")}).status_code == 400
    assert client.post("/api/prefill", headers=ORIGIN, data={}).status_code == 400
    assert client.post("/api/prefill", data={}).status_code == 403


def test_add_form_wires_up_prefill(client):
    assert b"data-prefill-url" in client.get("/new").data


def test_rename_follows_the_settings_template_by_default(client, ms, admin):
    client.post("/onedrive/import/1", data=post_rows(admin, rename_1="on"), headers=ORIGIN)
    ((_, body),) = renames(ms)
    assert body["name"] == "261024 Tyres - Example Tyres.pdf"      # the default template


def test_rename_is_offered_even_when_nothing_could_be_guessed(client):
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert page.count('name="rename_') == 3          # every row, including the unreadable scan
    assert "yymmdd - category - vendor" in page


def test_rename_uses_the_values_the_owner_typed(client, ms, admin, dashed):
    data = {"count": "1", "include_0": "on", "rename_0": "on", "id_0": "P2", "url_0": "https://od.example/P2",
            "name_0": "scan.pdf", "date_0": "2026-11-09", "category_id_0": cat(admin, "Tyres"),
            "amount_0": "1.00", "vendor_0": "Typed Vendor"}
    client.post("/onedrive/import/1", data=data, headers=ORIGIN)
    ((_, body),) = renames(ms)
    assert body["name"] == "261109 - Tyres - Typed Vendor.pdf"


def test_a_slow_round_stops_early_and_defers_the_rest(client, monkeypatch):
    """OCR can take 10+ s a file; gunicorn kills a request at 120 s. Past the time budget the
    page stops reading files and leaves the rest for the next round."""
    monkeypatch.setattr("tmexpenses.IMPORT_BUDGET_SECONDS", -1)      # already used up
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert page.count('name="include_') == 1          # always at least one file per round
    assert "2 more waiting" in page


# ---- the page shows progress instead of making the browser wait ---------------------------

def downloads(ms):
    return [c for c in ms.calls if c[1].startswith("https://cdn.example/")]


def test_the_import_page_loads_at_once_and_reads_nothing(client, ms):
    before = len(ms.calls)
    page = client.get("/onedrive/import/1").data.decode()
    assert len(ms.calls) == before            # no OneDrive traffic: the reading happens after the page is up
    assert 'id="import-status"' in page and "data-scan-url" in page and "data-row-url" in page
    assert "<progress" in page and "?sync=1" in page            # and a way in without JavaScript
    assert 'name="include_' not in page


def test_the_import_form_cant_be_sent_before_reading_finishes(client):
    page = client.get("/onedrive/import/1").data.decode()
    assert 'name="count" value="0"' in page and "disabled" in page.split("Import ticked")[0].split("<button")[-1]


def test_scan_lists_the_files_without_reading_them(client, ms):
    data = client.get("/onedrive/import/1/scan").get_json()
    assert (data["listed"], data["already_imported"], data["remaining"]) == (3, 0, 0)
    assert [f["name"] for f in data["files"]] == ["261024 - Tyres.pdf", "scan.pdf", "250105 - Insurer cert.pdf"]
    assert {"id", "name", "folder", "url", "size"} <= set(data["files"][0])
    assert downloads(ms) == []


def test_scan_leaves_out_what_is_imported_and_batches(client, admin, monkeypatch):
    client.post("/onedrive/import/1", data=post_rows(admin), headers=ORIGIN)      # imports P3 and P1
    monkeypatch.setattr("tmexpenses.IMPORT_BATCH", 1)
    data = client.get("/onedrive/import/1/scan").get_json()
    assert (data["listed"], data["already_imported"], data["remaining"]) == (3, 2, 0)
    assert [f["name"] for f in data["files"]] == ["scan.pdf"]


def test_scan_reports_what_is_in_the_way(client, ms):
    client.post("/onedrive/disconnect", headers=ORIGIN)
    resp = client.get("/onedrive/import/1/scan")
    assert resp.status_code == 409 and "Connect OneDrive" in resp.get_json()["error"]


def read(client, **over):
    data = {"n": "4", "id": "P1", "name": "261024 - Tyres.pdf", "folder": "", "url": "https://od.example/P1", "size": "10"}
    data.update(over)
    return client.post("/onedrive/import/1/read", headers=ORIGIN, data=data)


def test_read_returns_one_row_for_the_page_to_add(client):
    resp = read(client)
    html = resp.data.decode()
    assert resp.status_code == 200 and html.lstrip().startswith("<tr") and "<html" not in html
    assert 'name="amount_4"' in html and 'value="1234.50"' in html and 'name="id_4" value="P1"' in html


def test_an_unreadable_file_becomes_a_row_with_a_reason_not_an_error(client, ms):
    ms.download_status = 500
    resp = read(client)
    assert resp.status_code == 200 and "<tr" in resp.data.decode() and "261024 - Tyres.pdf" in resp.data.decode()
    assert "HTTP 500" in resp.data.decode()


def test_read_checks_origin_and_input(client):
    assert client.post("/onedrive/import/1/read", data={"id": "P1"}).status_code == 403
    assert read(client, id="").status_code == 400
    assert read(client, n="x").status_code == 400
    assert client.post("/onedrive/import/99/read", headers=ORIGIN, data={"id": "P1", "n": "0"}).status_code == 404


def test_the_slow_way_still_works_without_javascript(client):
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert page.count('name="include_') == 3 and "<progress" not in page


# ---- invoices that offer several amounts (6 or 12 months): the Settings choice ----------

OPTIONS = "Total Due          $384.30                  $749.05\n"


def test_the_options_choice_applies_to_the_import_preview(client, admin, monkeypatch):
    monkeypatch.setitem(TEXT, b"%PDF-scan", OPTIONS)
    pick = lambda: client.get("/onedrive/import/1?sync=1").data.decode()
    assert 'value="749.05"' in pick()                                      # default: the largest
    admin.execute("UPDATE expenses.setting SET multi_amount = 'min'")
    assert 'value="384.30"' in pick() and 'value="749.05"' not in pick()
    admin.execute("UPDATE expenses.setting SET multi_amount = 'blank'")
    assert 'value="749.05"' not in pick() and 'value="384.30"' not in pick()


def test_the_options_choice_applies_to_the_add_page_prefill(client, admin, monkeypatch):
    import io
    monkeypatch.setitem(TEXT, b"%PDF-scan", OPTIONS)
    send = lambda: client.post("/api/prefill", headers=ORIGIN, data={
        "invoice_file": (io.BytesIO(b"%PDF-scan"), "x.pdf", "application/pdf")}).get_json()["amount"]
    assert send() == "749.05"
    admin.execute("UPDATE expenses.setting SET multi_amount = 'min'")
    assert send() == "384.30"


# ---- a file that looks like an expense already recorded ----------------------------------

def test_the_preview_flags_a_file_that_looks_like_an_existing_expense(client, admin):
    existing = admin.execute(
        "INSERT INTO expenses.expense (car_id, category_id, occurred_at, amount, vendor) VALUES (1, %s, '2026-10-23 02:00+00', 1234.50, 'Other') RETURNING id",
        (cat(admin, "Tyres"),)).fetchone()["id"]
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    tyres_row = page.split('name="include_0"')[1].split("</tr>")[0]
    assert "Possible duplicate" in tyres_row and f"/{existing}/edit" in tyres_row
    assert " checked>" not in tyres_row                      # left unticked: the owner decides
    assert page.count("Possible duplicate") == 1             # only the matching file


def test_the_per_file_read_flags_it_too(client, admin):
    admin.execute("INSERT INTO expenses.expense (car_id, category_id, occurred_at, amount) VALUES (1, %s, '2026-10-24 02:00+00', 1234.50)",
                  (cat(admin, "Tyres"),))
    assert "Possible duplicate" in read(client).data.decode()


def test_sign_in_expiring_before_a_scan_is_explained(client, ms):
    ms.refresh_ok = False
    resp = client.get("/onedrive/import/1?sync=1")
    assert resp.status_code < 500 and b"sign in again" in resp.data


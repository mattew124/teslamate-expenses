"""Invoices kept in a folder on the server (INVOICES_DIR) instead of OneDrive: a folder per car,
uploads into the category's subfolder, the app serving the PDFs, and the same import as OneDrive.

The folder is a pytest tmp_path. PDF text is canned for the import tests, as in test_import.
"""

import re

import psycopg
import pytest
from psycopg.rows import dict_row

from tmexpenses import create_app, invoices, storage
from tests.test_app import ORIGIN, category_id, form, rows
from tests.test_import import TEXT, TYRES_TEXT
from tests.test_onedrive import KEY, choose_folder, ms, sign_in, with_pdf  # noqa: F401  (ms is a fixture)
from tests.test_validation import PDF


@pytest.fixture()
def root(tmp_path):
    (tmp_path / "invoices" / "Cars" / "Test car").mkdir(parents=True)
    return tmp_path / "invoices"


@pytest.fixture()
def app_for(pg, admin, monkeypatch, root):
    monkeypatch.delenv("BASE_PATH", raising=False)
    monkeypatch.setenv("APP_TZ", "Australia/Brisbane")

    def make(folder=root, transport=None):
        """`folder` None: the feature is off. `transport`: OneDrive is on as well."""
        monkeypatch.delenv("INVOICES_DIR", raising=False)
        monkeypatch.delenv("EXPENSES_ENCRYPTION_KEY", raising=False)
        if folder is not None:
            monkeypatch.setenv("INVOICES_DIR", str(folder))
        if transport is not None:
            monkeypatch.setenv("EXPENSES_ENCRYPTION_KEY", KEY)
        app = create_app(connect=lambda: psycopg.connect(pg.app_url, row_factory=dict_row),
                         onedrive_transport=transport)
        app.testing = True
        return app.test_client()
    return make


@pytest.fixture()
def client(app_for):
    return app_for()


def choose(client, folder="/Cars/Test car", car_id=1):
    return client.post(f"/local/folders/{car_id}", data={"folder_id": folder}, headers=ORIGIN)


def upload(client, admin, content=PDF, **over):
    return client.post("/new", data=with_pdf(form(admin, invoice_url="", **over), content), headers=ORIGIN)


def files(root):
    """Invoices in the folder: the app's hidden marker isn't one."""
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file() and p.name != storage.MARKER)


# ---- off unless INVOICES_DIR is set ------------------------------------------------

def test_off_without_invoices_dir(app_for, admin):
    c = app_for(folder=None)
    assert b"/local" not in c.get("/").data and b'name="invoice_file"' not in c.get("/new").data
    page = c.get("/storage").data.decode()
    assert "INVOICES_DIR" in page and "Invoices are kept in" not in page and "Choose folder" not in page
    assert c.get("/local/browse/1").status_code == 404
    assert choose(c).status_code == 404
    assert c.get("/local/import/1").status_code == 404
    assert c.get("/api/local/browse/1").status_code == 404
    assert c.get("/local/file?path=/a.pdf").status_code == 404


def test_on_it_adds_a_page_to_the_header_and_the_upload_box_to_the_form(client):
    assert b'href="/storage"' in client.get("/").data
    page = client.get("/new").data.decode()
    assert 'name="invoice_file"' in page and "Choose from the invoice folder" in page
    assert "data-local-browse-url" in page and "OneDrive" not in re.sub(r"<small>.*?</small>", "", page, flags=re.S)


# ---- a folder per car ---------------------------------------------------------------

def test_the_page_shows_the_mount_and_each_cars_folder(client, root):
    page = client.get("/storage").data.decode()
    assert f"<code>{root}</code>" in page and "as the app's container sees it" in page
    assert "No folder chosen" in page and "/local/browse/1" in page and "Choose folder" in page
    assert choose(client).status_code == 303
    page = client.get("/storage").data.decode()
    assert f"<code>{root}/Cars/Test car</code>" in page and "Import existing invoices" in page


def test_the_page_names_the_folder_on_the_server_when_it_is_told_it(app_for, monkeypatch, root):
    """Inside the container the folder is only ever /invoices: INVOICES_HOST_DIR says where it really is."""
    monkeypatch.setenv("INVOICES_HOST_DIR", "/mnt/nas/cars/invoices/")
    c = app_for()
    choose(c)
    page = c.get("/storage").data.decode()
    assert "<code>/mnt/nas/cars/invoices</code>" in page and f"at <code>{root}</code>" in page
    assert "<code>/mnt/nas/cars/invoices/Cars/Test car</code>" in page


def test_one_page_sets_up_every_storage(both):
    c, _ = both
    page = c.get("/storage").data.decode()
    assert page.index('id="local"') < page.index('id="onedrive"') < page.index('id="cars"')
    assert "Connected as <strong>Test Owner</strong>" in page
    assert "Choose a server folder" in page and "Choose a OneDrive folder" in page


def test_browsing_goes_down_and_up_and_uses_this_folder(client, admin):
    top = client.get("/local/browse/1").data.decode()
    assert "📁 Cars" in top and "⬑ Up" not in top and 'name="folder_id" value=""' in top
    down = client.get("/local/browse/1?folder=/Cars/Test car").data.decode()
    assert "<code>/Cars/Test car</code>" in down and "⬑ Up" in down and "folder=/Cars" in down
    assert "No folders in here." in down and 'name="folder_id" value="/Cars/Test car"' in down
    assert choose(client).status_code == 303
    row = admin.execute("SELECT * FROM expenses.car_folder").fetchone()
    assert (row["backend"], row["folder_id"], row["folder_path"]) == ("local", "/Cars/Test car", "/Cars/Test car")


def test_a_new_folder_is_made_where_you_are_and_opened(client, root):
    resp = client.post("/local/browse/1/new-folder", data={"parent_id": "/Cars", "name": "Model Y"}, headers=ORIGIN)
    assert resp.status_code == 303 and resp.headers["Location"].endswith("/local/browse/1?folder=/Cars/Model+Y")
    assert (root / "Cars" / "Model Y").is_dir()
    again = client.post("/local/browse/1/new-folder", data={"parent_id": "/Cars", "name": "Model Y"}, headers=ORIGIN)
    assert again.status_code == 400 and b"already exists" in again.data
    bad = client.post("/local/browse/1/new-folder", data={"parent_id": "/Cars", "name": "a/b"}, headers=ORIGIN)
    assert bad.status_code == 400 and not (root / "Cars" / "a").exists()


@pytest.mark.parametrize("bad", ["/../outside", "/Cars/../..", "/nowhere", "Cars"])
def test_a_folder_outside_the_mount_or_missing_cant_be_browsed_or_chosen(client, admin, bad):
    assert client.get(f"/local/browse/1?folder={bad}").status_code == 502
    resp = choose(client, bad)
    assert resp.status_code == 400 and b"Couldn&#39;t use that folder" in resp.data
    assert admin.execute("SELECT count(*) AS n FROM expenses.car_folder").fetchone()["n"] == 0


def test_the_app_starts_while_the_database_is_still_down(monkeypatch, root):
    """Checking the folder at start asks the database whether a car uses it: that must not stop the start."""
    monkeypatch.setenv("INVOICES_DIR", str(root))
    monkeypatch.delenv("EXPENSES_ENCRYPTION_KEY", raising=False)

    def down():
        raise psycopg.OperationalError("connection refused")
    assert create_app(connect=down) is not None


def test_choosing_a_cars_folder_marks_the_invoice_folder_as_the_apps(client, root):
    assert not (root / storage.MARKER).exists()
    assert choose(client).status_code == 303
    assert (root / storage.MARKER).is_file()


def test_a_share_that_did_not_mount_is_reported_and_nothing_lands_on_the_servers_disk(app_for, admin, tmp_path):
    """The mount point is there but empty, as when the NAS share isn't mounted: a car already uses it."""
    bare = tmp_path / "mount-point"
    bare.mkdir()
    c = app_for(folder=bare)
    admin.execute("INSERT INTO expenses.car_folder (car_id, folder_id, folder_path, backend) "
                  "VALUES (1, '/Cars/Test car', '/Cars/Test car', 'local')")
    page = c.get("/storage").data.decode()
    assert "isn't there" in page and "mounted" in page
    resp = upload(c, admin)
    assert resp.status_code == 400 and b"mounted" in resp.data and rows(admin) == []
    assert c.get("/local/browse/1").status_code == 502
    made = c.post("/local/browse/1/new-folder", data={"parent_id": "", "name": "Model Y"}, headers=ORIGIN)
    assert made.status_code in (400, 502)
    assert choose(c, "/").status_code == 400            # can't be chosen again while it looks unmounted
    assert c.get("/local/import/1/scan").status_code == 502
    assert list(bare.iterdir()) == []


def test_clear_forgets_the_folder_and_keeps_the_files(client, admin, root):
    choose(client)
    upload(client, admin)
    assert client.post("/local/folders/1/clear", headers=ORIGIN).status_code == 303
    assert admin.execute("SELECT count(*) AS n FROM expenses.car_folder").fetchone()["n"] == 0
    assert len(files(root)) == 1


def test_folder_changes_need_the_same_origin_and_a_real_car(client):
    assert client.post("/local/folders/1", data={"folder_id": "/Cars"}).status_code == 403
    assert choose(client, car_id=99).status_code == 404


# ---- saving an invoice with an expense ----------------------------------------------

def test_an_uploaded_pdf_is_saved_in_the_categorys_subfolder_and_linked(client, admin, root):
    choose(client)
    assert upload(client, admin).status_code == 303
    assert files(root) == ["Cars/Test car/Maintenance/261107 Tyres - Tyre Shop.pdf"]
    (row,) = rows(admin)
    assert row["invoice_path"] == "/Cars/Test car/Maintenance/261107 Tyres - Tyre Shop.pdf"
    assert row["invoice_url"] is None
    assert f'href="/invoice/{row["id"]}"' in client.get("/").data.decode()


def test_a_second_invoice_with_the_same_name_is_kept_beside_the_first(client, admin, root):
    choose(client)
    upload(client, admin)
    upload(client, admin, content=PDF + b"second", confirm_duplicate="1")
    assert files(root) == ["Cars/Test car/Maintenance/261107 Tyres - Tyre Shop (1).pdf",
                           "Cars/Test car/Maintenance/261107 Tyres - Tyre Shop.pdf"]
    assert (root / "Cars/Test car/Maintenance/261107 Tyres - Tyre Shop.pdf").read_bytes() == PDF
    assert [r["invoice_path"].rsplit("/", 1)[1] for r in rows(admin)] == [
        "261107 Tyres - Tyre Shop.pdf", "261107 Tyres - Tyre Shop (1).pdf"]


def test_without_a_folder_for_the_car_the_expense_is_saved_and_the_notice_says_the_pdf_wasnt(client, admin, root):
    resp = upload(client, admin)
    assert resp.status_code == 303 and "not_uploaded" in resp.headers["Location"]
    assert b"Invoice storage page" in client.get(resp.headers["Location"]).data
    (row,) = rows(admin)
    assert row["invoice_path"] is None and files(root) == []


def test_a_folder_that_has_gone_is_an_error_not_recreated(client, admin, root):
    choose(client)
    (root / "Cars" / "Test car").rmdir()
    resp = upload(client, admin)
    assert resp.status_code == 400 and b"choose the car&#39;s folder again" in resp.data
    assert rows(admin) == [] and not (root / "Cars" / "Test car").exists()


def test_an_unmounted_folder_is_reported_everywhere_and_never_created(app_for, admin, tmp_path):
    gone = tmp_path / "not-mounted"
    c = app_for(folder=gone)
    admin.execute("INSERT INTO expenses.car_folder (car_id, folder_id, folder_path, backend) "
                  "VALUES (1, '/Cars', '/Cars', 'local')")
    page = c.get("/storage")
    assert page.status_code == 200 and b"isn't there" in page.data and b"Choose folder" not in page.data
    resp = upload(c, admin)
    assert resp.status_code == 400 and b"INVOICES_DIR" in resp.data and rows(admin) == []
    assert c.get("/local/browse/1").status_code == 502
    assert c.get("/local/import/1/scan").status_code == 502
    assert c.get("/local/file?path=/a.pdf").status_code == 404
    assert not gone.exists()


def test_a_non_pdf_is_refused_before_anything_is_written(client, admin, root):
    choose(client)
    resp = upload(client, admin, content=b"<html>not a pdf</html>")
    assert resp.status_code == 400 and files(root) == [] and rows(admin) == []


# ---- the app serves the PDF -----------------------------------------------------------

def saved(client, admin):
    choose(client)
    upload(client, admin)
    return rows(admin)[0]


def test_the_invoice_opens_in_the_browser_as_a_pdf(client, admin):
    row = saved(client, admin)
    resp = client.get(f"/invoice/{row['id']}")
    assert resp.status_code == 200 and resp.data == PDF
    assert resp.mimetype == "application/pdf"
    assert resp.headers["Content-Disposition"].startswith("inline")
    assert "261107 Tyres - Tyre Shop.pdf" in resp.headers["Content-Disposition"]
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in resp.headers["Content-Security-Policy"]
    assert resp.headers["Cache-Control"] == "private, no-cache"


def test_download_sends_it_as_a_file_instead(client, admin):
    row = saved(client, admin)
    resp = client.get(f"/invoice/{row['id']}?download=1")
    assert resp.data == PDF and resp.headers["Content-Disposition"].startswith("attachment")


def test_an_expense_without_a_stored_invoice_has_nothing_to_serve(client, admin):
    client.post("/new", data=form(admin), headers=ORIGIN)       # a pasted link only
    assert client.get(f"/invoice/{rows(admin)[0]['id']}").status_code == 404
    assert client.get("/invoice/999").status_code == 404
    assert b'href="https://1drv.ms/b/abc"' in client.get("/").data


def test_a_file_deleted_from_the_folder_is_explained(client, admin, root):
    row = saved(client, admin)
    (root / row["invoice_path"].lstrip("/")).unlink()
    resp = client.get(f"/invoice/{row['id']}")
    assert resp.status_code == 404 and b"Invoice not found" in resp.data


@pytest.mark.parametrize("path", ["/../outside.pdf", "/Cars/../../outside.pdf", "outside.pdf", "/etc/passwd",
                                  "/notes.txt", "/Cars", "", "/link.pdf"])
def test_only_pdfs_inside_the_folder_are_ever_served(client, root, path):
    (root.parent / "outside.pdf").write_bytes(PDF)
    (root / "notes.txt").write_text("private")
    (root / "link.pdf").symlink_to(root.parent / "outside.pdf")
    assert client.get("/local/file", query_string={"path": path}).status_code == 404


def test_a_path_that_leaves_the_folder_cant_be_saved_on_an_expense(client, admin):
    for bad in ("/../outside.pdf", "relative.pdf", "/Cars/./x.pdf", "/Cars/x.txt"):
        resp = client.post("/new", data=form(admin, invoice_url="", invoice_path=bad), headers=ORIGIN)
        assert resp.status_code == 400, bad
    assert rows(admin) == []


# ---- editing ------------------------------------------------------------------------

def test_editing_keeps_the_stored_invoice_and_shows_it(client, admin):
    row = saved(client, admin)
    page = client.get(f"/{row['id']}/edit").data.decode()
    assert f'name="invoice_path" value="{row["invoice_path"]}"' in page
    assert "261107 Tyres - Tyre Shop.pdf</a>" in page and 'name="remove_invoice"' in page
    data = form(admin, invoice_url="", invoice_path=row["invoice_path"], vendor="Renamed Shop")
    assert client.post(f"/{row['id']}/edit", data=data, headers=ORIGIN).status_code == 303
    (after,) = rows(admin)
    assert after["vendor"] == "Renamed Shop" and after["invoice_path"] == row["invoice_path"]


def test_removing_it_unlinks_the_expense_and_leaves_the_file(client, admin, root):
    row = saved(client, admin)
    data = form(admin, invoice_url="", invoice_path=row["invoice_path"], remove_invoice="1")
    assert client.post(f"/{row['id']}/edit", data=data, headers=ORIGIN).status_code == 303
    assert rows(admin)[0]["invoice_path"] is None and len(files(root)) == 1


def test_a_new_upload_replaces_the_link_and_keeps_the_old_file(client, admin, root):
    row = saved(client, admin)
    data = with_pdf(form(admin, invoice_url="", invoice_path=row["invoice_path"], vendor="Other Shop"), PDF + b"new")
    assert client.post(f"/{row['id']}/edit", data=data, headers=ORIGIN).status_code == 303
    assert rows(admin)[0]["invoice_path"] == "/Cars/Test car/Maintenance/261107 Tyres - Other Shop.pdf"
    assert len(files(root)) == 2


# ---- the Add form's file picker --------------------------------------------------------

def test_the_picker_starts_in_the_cars_folder_and_goes_up(client, admin, root):
    choose(client)
    upload(client, admin)
    (root / "Cars" / "Test car" / "loose.pdf").write_bytes(PDF)
    (root / "Cars" / "Test car" / "notes.txt").write_text("x")
    data = client.get("/api/local/browse/1").get_json()
    assert data["path"] == "/Cars/Test car" and data["parent_id"] == "/Cars" and data["is_root"] is False
    assert data["folders"] == [{"id": "/Cars/Test car/Maintenance", "name": "Maintenance"}]
    assert data["files"] == [{"id": "/Cars/Test car/loose.pdf", "name": "loose.pdf", "size": len(PDF),
                              "web_url": "/Cars/Test car/loose.pdf"}]
    top = client.get("/api/local/browse/1?folder=root").get_json()
    assert top["is_root"] is True and top["parent_id"] is None and [f["name"] for f in top["folders"]] == ["Cars"]
    assert client.get("/api/local/browse/1?folder=/../x").status_code == 502


class LeakyError(storage.StorageError):
    """An error whose str() carries internals, as a library's might: only user_message may be shown."""

    def __str__(self):
        return "Traceback (most recent call last): /srv/tmexpenses/secret.py line 1"


@pytest.mark.parametrize("call", ["picker", "scan", "prefill"])
def test_an_error_reply_shows_only_the_text_written_for_the_user(client, root, monkeypatch, call):
    choose(client)
    for name in ("contents", "pdfs", "download"):
        monkeypatch.setattr(storage.LocalFolder, name,
                            lambda *a, **k: (_ for _ in ()).throw(LeakyError("The share is offline.")))
    resp = {"picker": lambda: client.get("/api/local/browse/1"),
            "scan": lambda: client.get("/local/import/1/scan"),
            "prefill": lambda: client.post("/api/prefill", headers=ORIGIN,
                                           data={"local_id": "/Cars/Test car/a.pdf", "name": "a.pdf"})}[call]()
    assert resp.status_code == 502 and resp.get_json() == {"error": "The share is offline."}


def test_the_picker_needs_a_folder_for_the_car(client):
    resp = client.get("/api/local/browse/1")
    assert resp.status_code == 409 and "Invoice storage page" in resp.get_json()["error"]
    assert client.get("/api/local/browse/99").status_code == 404


def test_a_picked_file_is_read_to_fill_the_form_and_saved_as_the_invoice(client, admin, root, monkeypatch):
    choose(client)
    (root / "Cars" / "Test car" / "loose.pdf").write_bytes(PDF)
    monkeypatch.setattr(invoices, "extract_text", lambda pdf: TYRES_TEXT)
    resp = client.post("/api/prefill", headers=ORIGIN, data={"local_id": "/Cars/Test car/loose.pdf", "name": "loose.pdf"})
    assert resp.status_code == 200 and resp.get_json()["amount"] == "1234.50"
    gone = client.post("/api/prefill", headers=ORIGIN, data={"local_id": "/Cars/Test car/gone.pdf", "name": "gone.pdf"})
    assert gone.status_code == 502
    data = form(admin, invoice_url="", invoice_path="/Cars/Test car/loose.pdf")
    assert client.post("/new", data=data, headers=ORIGIN).status_code == 303
    assert rows(admin)[0]["invoice_path"] == "/Cars/Test car/loose.pdf"
    assert files(root) == ["Cars/Test car/loose.pdf"]            # linked where it is, not copied


# ---- importing what is already in the folder --------------------------------------------

@pytest.fixture()
def stocked(client, root, monkeypatch):
    """The same three files as test_import's OneDrive folder, in the car's folder on the server."""
    car = root / "Cars" / "Test car"
    (car / "Insurance").mkdir()
    (car / "261024 - Tyres.pdf").write_bytes(b"%PDF-tyres")
    (car / "scan.pdf").write_bytes(b"%PDF-scan")
    (car / "Insurance" / "250105 - Insurer cert.pdf").write_bytes(b"%PDF-insurer")
    monkeypatch.setattr(invoices, "extract_text", lambda pdf: TEXT[pdf])
    choose(client)
    return client


TYRES, SCAN, INSURER = ("/Cars/Test car/261024 - Tyres.pdf", "/Cars/Test car/scan.pdf",
                        "/Cars/Test car/Insurance/250105 - Insurer cert.pdf")


def post_rows(admin, **over):
    data = {"count": "3",
            "include_0": "on", "id_0": TYRES, "url_0": TYRES, "name_0": "261024 - Tyres.pdf",
            "date_0": "2026-10-24", "category_id_0": str(category_id(admin, "Tyres")), "amount_0": "1,234.50",
            "vendor_0": "Example Tyres", "odometer_km_0": "45120",
            "include_1": "on", "id_1": INSURER, "url_1": INSURER, "name_1": "250105 - Insurer cert.pdf",
            "folder_1": "Insurance", "date_1": "2025-01-05", "category_id_1": str(category_id(admin, "Insurance")),
            "amount_1": "980.40", "vendor_1": "Insurer", "odometer_km_1": "",
            "id_2": SCAN, "url_2": SCAN, "name_2": "scan.pdf"}  # not ticked
    data.update(over)
    return data


def test_the_import_page_needs_a_folder(client):
    resp = client.get("/local/import/1")
    assert resp.status_code == 303 and resp.headers["Location"].endswith("/storage")


def test_scan_lists_what_has_no_expense_yet(stocked, admin):
    page = stocked.get("/local/import/1").data.decode()
    assert "/local/import/1/scan" in page and "the car&#39;s invoice folder" in page
    scan = stocked.get("/local/import/1/scan").get_json()
    assert scan["listed"] == 3 and [f["url"] for f in scan["files"]] == [TYRES, SCAN, INSURER]
    stocked.post("/local/import/1", data=post_rows(admin), headers=ORIGIN)
    scan = stocked.get("/local/import/1/scan").get_json()
    assert scan["already_imported"] == 2 and [f["name"] for f in scan["files"]] == ["scan.pdf"]


def test_preview_proposes_from_the_pdf_and_links_to_it(stocked, admin):
    page = stocked.get("/local/import/1?sync=1").data.decode()
    assert 'value="2026-10-24"' in page and 'value="1234.50"' in page and 'value="45120"' in page
    assert "Example Tyres Pty Ltd" in page and page.count(" checked>") == 2
    assert 'href="/local/file?path=/Cars/Test+car/261024+-+Tyres.pdf"' in page
    assert "Rename the file to" in page and rows(admin) == []
    row = stocked.post("/local/import/1/read", headers=ORIGIN, data={
        "n": "0", "id": TYRES, "url": TYRES, "name": "261024 - Tyres.pdf", "folder": ""}).data.decode()
    assert 'value="1234.50"' in row and "/local/file?path=" in row
    assert stocked.get("/local/file", query_string={"path": TYRES}).data == b"%PDF-tyres"


def test_a_forged_size_cant_add_a_line_to_the_log(stocked, caplog):
    caplog.set_level("DEBUG", logger="tmexpenses")      # the app's own logger, where the read is logged
    stocked.post("/local/import/1/read", headers=ORIGIN, data={
        "n": "0", "id": TYRES, "url": TYRES, "name": "261024 - Tyres.pdf", "folder": "",
        "size": "10\n2026-11-13 [ERROR] forged entry"})
    logged = [r.getMessage() for r in caplog.records if "import file=" in r.getMessage()]
    assert logged, "the read should be logged at debug"
    assert not any("forged" in m for m in logged)


def test_confirm_imports_ticked_rows_with_their_paths_and_only_once(stocked, admin):
    resp = stocked.post("/local/import/1", data=post_rows(admin), headers=ORIGIN)
    assert resp.status_code == 303 and "msg=imported" in resp.headers["Location"]
    got = rows(admin)
    assert [(r["invoice_path"], r["invoice_url"], str(r["amount"])) for r in got] == [
        (TYRES, None, "1234.50"), (INSURER, None, "980.40")]
    stocked.post("/local/import/1", data=post_rows(admin), headers=ORIGIN)
    assert len(rows(admin)) == 2
    assert f'href="/invoice/{got[0]["id"]}"' in stocked.get("/").data.decode()


def test_a_ticked_rename_moves_the_file_and_stores_the_new_path(stocked, admin, root):
    resp = stocked.post("/local/import/1", data=post_rows(admin, rename_0="on"), headers=ORIGIN, follow_redirects=True)
    assert b"Renamed 1." in resp.data
    assert rows(admin)[0]["invoice_path"] == "/Cars/Test car/261024 Tyres - Example Tyres.pdf"
    assert (root / "Cars/Test car/261024 Tyres - Example Tyres.pdf").read_bytes() == b"%PDF-tyres"
    assert not (root / "Cars/Test car/261024 - Tyres.pdf").exists()


def test_a_rename_onto_a_taken_name_imports_with_the_old_path(stocked, admin, root):
    (root / "Cars/Test car/261024 Tyres - Example Tyres.pdf").write_bytes(b"%PDF-scan")
    resp = stocked.post("/local/import/1", data=post_rows(admin, rename_0="on"), headers=ORIGIN)
    assert "rename_failed=1" in resp.headers["Location"]
    assert rows(admin)[0]["invoice_path"] == TYRES
    assert (root / "Cars/Test car/261024 Tyres - Example Tyres.pdf").read_bytes() == b"%PDF-scan"


def test_skipped_files_stop_being_offered_until_restored(stocked, admin):
    stocked.post("/local/import/1", data=post_rows(admin, include_0="", include_1="", skip_2="on"), headers=ORIGIN)
    assert [f["name"] for f in stocked.get("/local/import/1/scan").get_json()["files"]] == [
        "261024 - Tyres.pdf", "250105 - Insurer cert.pdf"]
    stocked.post("/local/import/1/unskip", headers=ORIGIN)
    assert len(stocked.get("/local/import/1/scan").get_json()["files"]) == 3


def test_a_path_outside_the_folder_cant_be_imported(stocked, admin):
    resp = stocked.post("/local/import/1", data=post_rows(admin, url_0="/../outside.pdf"), headers=ORIGIN)
    assert resp.status_code == 400 and rows(admin) == []


def test_corrections_are_learned_from_local_files_too(stocked, admin):
    """The owner changes the proposed amount: the label beside the figure they typed is remembered."""
    data = post_rows(admin, amount_0="1,100.00", guess_amount_0="1234.50", include_1="")
    TEXT[b"%PDF-tyres"] = TYRES_TEXT + "  Amount payable   $1,100.00\n"
    try:
        stocked.post("/local/import/1", data=data, headers=ORIGIN)
    finally:
        TEXT[b"%PDF-tyres"] = TYRES_TEXT
    learned = admin.execute("SELECT field, label FROM expenses.learned_label").fetchall()
    assert {"field": "amount", "label": "amount payable"} in learned
    assert admin.execute("SELECT vendor FROM expenses.learned_vendor").fetchone()["vendor"] == "example tyres"


# ---- beside OneDrive: each car uses one or the other ---------------------------------

@pytest.fixture()
def both(app_for, ms):      # `ms` puts back the fake drive's folders, which an upload adds to
    c = app_for(transport=ms)
    sign_in(c, ms)
    return c, ms


def test_a_car_on_the_local_folder_uploads_there_and_microsoft_hears_nothing(both, admin, root):
    c, ms = both
    choose_folder(c)                    # OneDrive first...
    assert choose(c).status_code == 303  # ...then the folder on the server takes the car over
    assert admin.execute("SELECT backend FROM expenses.car_folder").fetchone()["backend"] == "local"
    before = len(ms.calls)
    assert upload(c, admin).status_code == 303
    assert len(ms.calls) == before and len(files(root)) == 1
    assert rows(admin)[0]["invoice_path"] and rows(admin)[0]["invoice_url"] is None
    page = c.get("/storage").data.decode()
    assert f"Folder on this server: <code>{root}/Cars/Test car</code>" in page and "Move to a OneDrive folder" in page


def test_a_car_on_onedrive_still_uploads_to_onedrive(both, admin, root):
    c, ms = both
    choose_folder(c)
    assert upload(c, admin).status_code == 303
    assert len(ms.uploads()) == 1 and files(root) == []
    assert rows(admin)[0]["invoice_url"].startswith("https://") and rows(admin)[0]["invoice_path"] is None
    page = c.get("/storage").data.decode()
    assert "OneDrive: <code>/Cars/Test car/Invoices</code>" in page and "Move to a server folder" in page
    assert "/onedrive/import/1" in page and "/local/import/1" not in page
    form_page = c.get("/new").data.decode()
    assert 'data-storage="onedrive"' in form_page and "data-local-browse-url" in form_page


def test_clearing_on_one_page_leaves_a_folder_chosen_on_the_other(both, admin):
    c, _ = both
    choose(c)
    c.post("/onedrive/folders/1/clear", headers=ORIGIN)
    assert admin.execute("SELECT backend FROM expenses.car_folder").fetchone()["backend"] == "local"
    assert c.get("/onedrive/import/1").status_code == 303     # not this car's storage: nothing to import here


def test_with_both_and_no_folder_the_notice_says_where_to_choose_one(both, admin):
    c, ms = both
    resp = upload(c, admin)
    assert resp.status_code == 303 and b"Invoice storage page" in c.get(resp.headers["Location"]).data
    assert ms.uploads() == []


def test_the_notice_after_saving_says_what_happened_to_the_pdf(client, admin):
    choose(client)
    first = upload(client, admin)
    assert "saved_uploaded" in first.headers["Location"]
    assert b"saved in the car&#39;s folder" in client.get(first.headers["Location"]).data
    row = rows(admin)[0]
    data = with_pdf(form(admin, invoice_url="", invoice_path=row["invoice_path"], vendor="Other Shop"), PDF + b"new")
    again = client.post(f"/{row['id']}/edit", data=data, headers=ORIGIN)
    assert "updated_replaced" in again.headers["Location"]

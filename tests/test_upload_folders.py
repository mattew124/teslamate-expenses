"""Uploaded invoices go into a subfolder of the car's folder, chosen per category."""

import re

import pytest
from urllib.parse import unquote, urlparse

from tests.test_app import ORIGIN, category_id, form, rows
from tests.test_onedrive import (FOLDERS, FakeMicrosoft, app_for, choose_folder, client,  # noqa: F401
                                 ms, sign_in, with_pdf)


@pytest.fixture(autouse=True)
def keep_mapping(admin):
    """Tests edit the seeded mapping; put it back."""
    saved = admin.execute("SELECT id, upload_folder FROM expenses.category").fetchall()
    yield
    for r in saved:
        admin.execute("UPDATE expenses.category SET upload_folder = %s WHERE id = %s", (r["upload_folder"], r["id"]))


def upload(client, admin, category="Tyres", **over):
    data = form(admin, invoice_url="", category_id=str(category_id(admin, category)), **over)
    return client.post("/new", data=with_pdf(data), headers=ORIGIN)


def put_folder(ms):
    (put,) = ms.uploads()[-1:]
    return re.search(r"/items/([^:]+):/", unquote(urlparse(put[1]).path)).group(1)


def set_folder(admin, category, folder):
    admin.execute("UPDATE expenses.category SET upload_folder = %s WHERE name = %s", (folder, category))


def test_standard_categories_ship_with_a_sensible_mapping(admin):
    got = {r["name"]: r["upload_folder"] for r in admin.execute(
        "SELECT name, upload_folder FROM expenses.category WHERE sort_order < 100")}
    assert got["Tyres"] == got["Wheel alignment"] == got["Air filter"] == "Maintenance"
    assert got["Insurance"] == "Insurance" and got["Registration"] == "Registration"
    assert got["Initial purchase"] is None


def test_upload_goes_into_the_mapped_subfolder_created_on_demand(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    assert upload(client, admin).status_code == 303
    assert ms.created == [("INV", "Maintenance")]
    assert put_folder(ms) == next(k for k, f in FOLDERS.items() if f["name"] == "Maintenance")
    (row,) = rows(admin)
    assert row["invoice_url"].startswith("https://")


def test_existing_subfolder_is_reused_whatever_its_case(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    client.post("/onedrive/browse/1/new-folder", data={"parent_id": "INV", "name": "maintenance"}, headers=ORIGIN)
    ms.created.clear()
    upload(client, admin)
    upload(client, admin, category="Wheel alignment")
    assert ms.created == []
    assert len(ms.uploads()) == 2


def test_unmapped_category_gets_a_folder_named_after_it(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    upload(client, admin, category="Initial purchase")
    assert ms.created == [("INV", "Initial purchase")]


def test_mapping_is_edited_on_the_categories_page(client, ms, admin):
    cid = category_id(admin, "Tyres")
    assert "Maintenance" in client.get("/categories").data.decode()
    resp = client.post(f"/categories/{cid}/folder", data={"upload_folder": " Tyres & wheels "}, headers=ORIGIN)
    assert resp.status_code == 303
    assert admin.execute("SELECT upload_folder FROM expenses.category WHERE id = %s", (cid,)).fetchone()[
        "upload_folder"] == "Tyres & wheels"
    sign_in(client, ms)
    choose_folder(client)
    upload(client, admin)
    assert ms.created == [("INV", "Tyres & wheels")]


def test_blank_mapping_falls_back_to_the_category_name(client, ms, admin):
    cid = category_id(admin, "Tyres")
    client.post(f"/categories/{cid}/folder", data={"upload_folder": ""}, headers=ORIGIN)
    sign_in(client, ms)
    choose_folder(client)
    upload(client, admin)
    assert ms.created == [("INV", "Tyres")]


def test_folder_names_onedrive_refuses_are_rejected(client, admin):
    cid = category_id(admin, "Tyres")
    for bad in ("a/b", "a:b", "x" * 300, "trailing."):
        resp = client.post(f"/categories/{cid}/folder", data={"upload_folder": bad}, headers=ORIGIN)
        assert resp.status_code == 400, bad
    assert admin.execute("SELECT upload_folder FROM expenses.category WHERE id = %s", (cid,)).fetchone()[
        "upload_folder"] == "Maintenance"


def test_unknown_category_404s(client):
    assert client.post("/categories/9999/folder", data={"upload_folder": "x"}, headers=ORIGIN).status_code == 404


def test_a_folder_that_cant_be_made_fails_the_save_without_a_row(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    ms.upload_status = 500
    resp = upload(client, admin)
    assert resp.status_code == 400 and rows(admin) == []

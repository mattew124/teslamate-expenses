"""The importer learns labels from what the owner types, and trusts them after two confirmations."""

import pytest

from tmexpenses import invoices
from tests.test_app import ORIGIN, rows
from tests.test_import import TEXT, app_for, client, cat, ms  # noqa: F401  (fixtures)

GARAGE = ("Example Garage Pty Ltd\nDate of issue:   09/02/2024\nSum charged:   $77.50\n"
          "Odometer reading:   45,120 km\n")


@pytest.fixture(autouse=True)
def garage_scan(monkeypatch):
    monkeypatch.setitem(TEXT, b"%PDF-scan", GARAGE)


def confirm(client, admin, guess=None, **over):
    data = {"count": "1", "include_0": "on", "id_0": "P2", "url_0": "https://od.example/P2",
            "name_0": "scan.pdf", "date_0": "2024-02-09", "category_id_0": cat(admin, "Tyres"),
            "amount_0": "77.50", "vendor_0": "Example Garage Pty Ltd", "odometer_km_0": "45120"}
    data.update({f"guess_{k}_0": v for k, v in (guess or {}).items()})
    data.update(over)
    return client.post("/onedrive/import/1", data=data, headers=ORIGIN)


def labels(admin):
    return {(r["field"], r["label"]): r["hits"] for r in admin.execute("SELECT * FROM expenses.learned_label")}


def test_corrected_fields_teach_their_labels_and_the_vendor(client, admin):
    assert confirm(client, admin).status_code == 303
    assert labels(admin) == {("amount", "sum charged"): 1, ("date", "date of issue"): 1,
                             ("odometer_km", "odometer reading"): 1}
    (v,) = admin.execute("SELECT vendor, hits FROM expenses.learned_vendor").fetchall()
    assert v["vendor"] == "example garage pty ltd" and v["hits"] == 1


def test_fields_the_owner_left_as_proposed_teach_nothing(client, admin):
    confirm(client, admin, guess={"amount": "77.50", "date": "2024-02-09", "odometer_km": "45120"})
    assert labels(admin) == {}


def test_only_the_corrected_field_is_learned(client, admin):
    confirm(client, admin, guess={"amount": "70.00", "date": "2024-02-09", "odometer_km": "45120"})
    assert set(labels(admin)) == {("amount", "sum charged")}


def test_no_values_are_stored_only_labels(client, admin):
    confirm(client, admin)
    stored = " ".join(r["label"] for r in admin.execute("SELECT label FROM expenses.learned_label"))
    assert not any(ch.isdigit() for ch in stored) and "77" not in stored


def test_a_value_not_found_in_the_pdf_teaches_nothing(client, admin):
    confirm(client, admin, amount_0="12.34")
    assert ("amount", "sum charged") not in labels(admin)


def test_an_already_imported_file_isnt_counted_twice(client, admin):
    confirm(client, admin)
    confirm(client, admin)
    assert labels(admin)[("amount", "sum charged")] == 1


def test_a_label_is_trusted_only_after_two_confirmations(client, admin):
    confirm(client, admin)
    admin.execute("DELETE FROM expenses.expense")
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert 'name="amount_1"' in page and 'value="77.50"' not in page
    admin.execute("UPDATE expenses.learned_label SET hits = %s", (invoices.LEARN_AFTER,))
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    assert 'value="77.50"' in page and 'value="2024-02-09"' in page and 'value="45120"' in page


def test_a_learned_vendor_preselects_the_category(client, admin):
    confirm(client, admin)
    admin.execute("DELETE FROM expenses.expense")
    admin.execute("UPDATE expenses.learned_vendor SET hits = %s", (invoices.LEARN_AFTER,))
    page = client.get("/onedrive/import/1?sync=1").data.decode()
    select = page.split('name="category_id_1"')[1].split("</select>")[0]
    assert f'<option value="{cat(admin, "Tyres")}" selected>' in select


def test_a_failure_while_learning_never_blocks_the_import(client, admin, monkeypatch):
    monkeypatch.setattr(invoices, "find_labels", lambda *a: 1 / 0)
    assert confirm(client, admin).status_code == 303
    assert len(rows(admin)) == 1


def test_learned_page_lists_and_deletes(client, admin):
    confirm(client, admin)
    page = client.get("/learned").data.decode()
    assert "sum charged" in page and "example garage pty ltd" in page
    assert client.post("/learned/label/delete", data={"field": "amount", "label": "sum charged"},
                       headers=ORIGIN).status_code == 303
    assert ("amount", "sum charged") not in labels(admin)
    assert client.post("/learned/vendor/delete", data={"vendor": "example garage pty ltd"},
                       headers=ORIGIN).status_code == 303
    assert admin.execute("SELECT count(*) AS n FROM expenses.learned_vendor").fetchone()["n"] == 0
    assert client.post("/learned/label/delete", data={"field": "amount", "label": "sum charged"}).status_code == 403

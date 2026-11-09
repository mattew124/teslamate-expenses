"""Warn before saving an expense that looks like one already recorded."""

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from tmexpenses import db
from tests.test_app import ORIGIN, category_id, form, rows
from tests.test_onedrive import choose_folder, client, ms, sign_in, with_pdf, app_for  # noqa: F401  (fixtures)

WHEN = datetime(2026, 11, 7, 2, 0, tzinfo=timezone.utc)


def seed(admin, date="2026-11-05 02:00+00", amount="1200.50", car=1, category="Tyres", vendor="Tyre Shop"):
    return admin.execute(
        "INSERT INTO expenses.expense (car_id, category_id, occurred_at, amount, vendor) VALUES (%s, %s, %s, %s, %s) RETURNING id",
        (car, category_id(admin, category), date, amount, vendor)).fetchone()["id"]


def found(admin, **kw):
    kw.setdefault("amount", Decimal("1200.50"))
    return db.find_duplicates(admin, 1, kw.pop("when", WHEN), **kw)


# ---- the lookup -----------------------------------------------------------------------

def test_same_car_amount_and_nearby_date_is_a_possible_duplicate(admin):
    expected = seed(admin)
    assert [r["id"] for r in found(admin)] == [expected]


def test_the_window_is_14_days_either_side(admin):
    inside = seed(admin, "2026-10-24 02:00+00")      # 14 days before
    seed(admin, "2026-10-23 02:00+00")               # 15 days before
    later = seed(admin, "2026-11-21 02:00+00")       # 14 days after
    seed(admin, "2026-11-22 02:00+00")               # 15 days after
    assert sorted(r["id"] for r in found(admin)) == sorted([inside, later])


def test_a_different_amount_or_car_is_not_a_duplicate(admin):
    seed(admin, amount="1200.51")
    admin.execute("INSERT INTO cars (id, eid, vid, vin, settings_id, name, inserted_at, updated_at) "
                  "VALUES (2, 2, 2, 'OTHERVIN', 2, 'Other car', now(), now())")
    try:
        seed(admin, car=2)
        assert found(admin) == []
    finally:
        admin.execute("DELETE FROM expenses.expense WHERE car_id = 2")
        admin.execute("DELETE FROM cars WHERE id = 2")


def test_the_closest_match_comes_first_and_the_expense_being_edited_is_left_out(admin):
    far = seed(admin, "2026-10-30 02:00+00")
    near = seed(admin, "2026-11-06 02:00+00")
    assert [r["id"] for r in found(admin)] == [near, far]
    assert [r["id"] for r in found(admin, exclude_id=near)] == [far]


# ---- the Add form ---------------------------------------------------------------------

def test_a_likely_duplicate_asks_first_and_saves_nothing(client, admin):
    existing = seed(admin)
    resp = client.post("/new", data=form(admin), headers=ORIGIN)
    page = resp.data.decode()
    assert resp.status_code == 409 and len(rows(admin)) == 1
    assert "already" in page.lower() and f"/{existing}/edit" in page and "Save anyway" in page
    assert 'name="confirm_duplicate"' in page
    assert 'value="1,200.50"' in page and 'value="Tyre Shop"' in page          # what was typed is kept


def test_save_anyway_saves(client, admin):
    seed(admin)
    resp = client.post("/new", data={**form(admin), "confirm_duplicate": "1"}, headers=ORIGIN)
    assert resp.status_code == 303 and len(rows(admin)) == 2


def test_nothing_is_uploaded_to_onedrive_before_the_owner_has_decided(client, ms, admin):
    sign_in(client, ms)
    choose_folder(client)
    seed(admin)
    resp = client.post("/new", data=with_pdf(form(admin, invoice_url="")), headers=ORIGIN)
    assert resp.status_code == 409 and ms.uploads() == []
    assert "choose the pdf again" in resp.data.decode().lower()                  # a file input can't be refilled
    ok = client.post("/new", data=with_pdf({**form(admin, invoice_url=""), "confirm_duplicate": "1"}), headers=ORIGIN)
    assert ok.status_code == 303 and len(ms.uploads()) == 1


def test_no_prompt_when_nothing_matches(client, admin):
    seed(admin, "2026-09-01 02:00+00")
    assert client.post("/new", data=form(admin), headers=ORIGIN).status_code == 303


def test_a_mistyped_form_shows_its_errors_not_the_duplicate_prompt(client, admin):
    seed(admin)
    resp = client.post("/new", data=form(admin, amount="abc"), headers=ORIGIN)
    import re
    page = resp.data.decode()
    assert resp.status_code == 400 and '<li>' not in page.split('id="duplicate-list"')[1].split("</ul>")[0]
    assert re.search(r'id="duplicate-warning"[^>]*\shidden', page)               # the warning stays out of sight


def test_editing_an_expense_doesnt_ask(client, admin):
    seed(admin)
    other = seed(admin, "2026-12-30 02:00+00", amount="99.00")
    resp = client.post(f"/{other}/edit", data=form(admin, amount="1200.50", date="2026-11-07"), headers=ORIGIN)
    assert resp.status_code == 303


# ---- the inline check the Add page runs before submitting -------------------------------

def test_the_duplicates_api_lists_matches(client, admin):
    existing = seed(admin)
    got = client.get("/api/duplicates?car_id=1&date=2026-11-07&time=12:00&amount=1,200.50").get_json()
    (m,) = got["matches"]
    assert m["edit_url"].endswith(f"/{existing}/edit") and m["category"] == "Tyres"
    assert m["vendor"] == "Tyre Shop" and m["amount"] == "1200.50" and "Nov" in m["date"]


def test_the_duplicates_api_is_empty_or_refuses_bad_input(client, admin):
    assert client.get("/api/duplicates?car_id=1&date=2026-11-07&time=12:00&amount=5").get_json() == {"matches": []}
    assert client.get("/api/duplicates?car_id=1&date=2026-11-07&time=12:00&amount=abc").status_code == 400
    assert client.get("/api/duplicates?date=2026-11-07&time=12:00&amount=5").status_code == 400


def test_the_add_page_knows_where_to_check(client):
    assert b"data-duplicates-url" in client.get("/new").data

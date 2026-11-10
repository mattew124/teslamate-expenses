from datetime import datetime

import psycopg
import pytest
from psycopg.rows import dict_row

from tmexpenses import create_app

ORIGIN = {"Origin": "http://localhost"}


@pytest.fixture()
def client(pg, admin, monkeypatch):
    monkeypatch.delenv("BASE_PATH", raising=False)
    monkeypatch.setenv("APP_TZ", "Australia/Brisbane")
    app = create_app(connect=lambda: psycopg.connect(pg.app_url, row_factory=dict_row))
    app.testing = True
    return app.test_client()


def category_id(admin, name):
    return admin.execute("SELECT id FROM expenses.category WHERE name=%s", (name,)).fetchone()["id"]


def form(admin, **over):
    data = {"car_id": "1", "category_id": str(category_id(admin, "Tyres")), "date": "2026-11-07",
            "time": "14:05", "amount": "1,200.50", "odometer_km": "57000", "vendor": "Tyre Shop",
            "invoice_url": "https://1drv.ms/b/abc", "notes": ""}
    data.update(over)
    return data


def rows(admin):
    return admin.execute("SELECT * FROM expenses.expense ORDER BY id").fetchall()


def test_seeded_categories(admin):
    names = [r["name"] for r in admin.execute("SELECT name FROM expenses.category ORDER BY sort_order")]
    assert names[:8] == ["Initial purchase", "Insurance", "Registration", "Tyres",
                         "Cabin air filter", "Air filter", "Wheel alignment", "Tyre rotation"]


def test_refund_or_rebate_is_stored_as_a_negative_amount(client, admin):
    resp = client.post("/new", data=form(admin, amount="-3,000", vendor="State rebate"), headers=ORIGIN)
    assert resp.status_code == 303
    (row,) = rows(admin)
    assert str(row["amount"]) == "-3000.00"
    assert "-$3,000.00" in client.get("/").data.decode()


def test_empty_index(client):
    resp = client.get("/")
    assert resp.status_code == 200 and b"No expenses yet" in resp.data


def test_create_stores_brisbane_time_as_utc(client, admin):
    resp = client.post("/new", data=form(admin), headers=ORIGIN)
    assert resp.status_code == 303
    (row,) = rows(admin)
    assert row["occurred_at"].strftime("%Y-%m-%d %H:%M") == "2026-11-07 04:05"  # UTC
    assert str(row["amount"]) == "1200.50" and row["odometer_km"] == 57000
    html = client.get("/").data.decode()
    assert "07 Nov 2026 14:05" in html and "$1,200.50" in html and "57,000 km" in html


def test_invoice_link_is_rendered_safely(client, admin):
    client.post("/new", data=form(admin), headers=ORIGIN)
    html = client.get("/").data.decode()
    assert 'href="https://1drv.ms/b/abc" target="_blank" rel="noopener noreferrer"' in html


def test_javascript_link_is_refused(client, admin):
    resp = client.post("/new", data=form(admin, invoice_url="javascript:alert(1)"), headers=ORIGIN)
    assert resp.status_code == 400 and b"https://" in resp.data
    assert rows(admin) == []


def test_validation_error_keeps_input_and_saves_nothing(client, admin):
    resp = client.post("/new", data=form(admin, amount="abc", vendor="Keep me"), headers=ORIGIN)
    assert resp.status_code == 400
    assert b"Amount must be a number" in resp.data and b"Keep me" in resp.data
    assert rows(admin) == []


def test_edit_and_delete(client, admin):
    client.post("/new", data=form(admin), headers=ORIGIN)
    page = client.get("/1/edit").data.decode()
    assert 'value="1200.50"' in page and 'value="14:05"' in page
    resp = client.post("/1/edit", data=form(admin, amount="999", time="09:00"), headers=ORIGIN)
    assert resp.status_code == 303
    (row,) = rows(admin)
    assert str(row["amount"]) == "999.00" and row["occurred_at"].hour == 23  # 09:00 AEST = 23:00 UTC prev day
    assert client.post("/1/delete", headers=ORIGIN).status_code == 303
    assert rows(admin) == []


def test_missing_expense_is_404(client):
    assert client.get("/999/edit").status_code == 404
    assert client.post("/999/delete", headers=ORIGIN).status_code == 404
    assert client.post("/999/edit", data={}, headers=ORIGIN).status_code in (400, 404)


def test_cross_site_post_is_blocked(client, admin):
    assert client.post("/new", data=form(admin)).status_code == 403
    assert client.post("/new", data=form(admin), headers={"Origin": "https://evil.example"}).status_code == 403
    assert client.post("/1/delete", headers={"Referer": "https://evil.example/x"}).status_code == 403
    assert rows(admin) == []
    # a referer from our own host is fine
    assert client.post("/new", data=form(admin), headers={"Referer": "http://localhost/new"}).status_code == 303


def test_category_filter_and_totals(client, admin):
    client.post("/new", data=form(admin), headers=ORIGIN)
    client.post("/new", data=form(admin, category_id=str(category_id(admin, "Insurance")), amount="500"),
                headers=ORIGIN)
    html = client.get(f"/?category={category_id(admin, 'Insurance')}").data.decode()
    assert "$500.00" in html and "$1,200.50" not in html.split("<tbody>")[1]
    assert "1 item" in html
    assert "$1,700.50" in client.get("/").data.decode()


def test_add_category(client, admin):
    assert client.post("/categories", data={"name": "Car wash"}, headers=ORIGIN).status_code == 303
    assert b"Car wash" in client.get("/new").data
    dup = client.post("/categories", data={"name": "car WASH"}, headers=ORIGIN)
    assert dup.status_code == 400 and b"already exists" in dup.data
    assert client.post("/categories", data={"name": " "}, headers=ORIGIN).status_code == 400
    admin.execute("DELETE FROM expenses.category WHERE name='Car wash'")  # leave seed list intact


def test_odometer_lookup_uses_utc_positions(client, admin):
    # positions are naive UTC. 10:00 AEST on 7 Nov is 00:00 UTC on 7 Nov.
    admin.execute("""INSERT INTO positions (latitude, longitude, date, odometer, car_id) VALUES
        (0, 0, '2026-11-06 23:59:00', 1000, 1), (0, 0, '2026-11-07 00:01:00', 1010, 1),
        (0, 0, '2026-11-07 05:00:00', NULL, 1)""")
    q = lambda t: client.get(f"/api/odometer?car_id=1&date=2026-11-07&time={t}").get_json()
    assert q("10:00") == {"odometer_km": 1000}   # 00:00 UTC: before the 00:01 position
    assert q("10:02") == {"odometer_km": 1010}
    assert q("20:00") == {"odometer_km": 1010}   # NULL odometer rows are skipped
    assert client.get("/api/odometer?car_id=1&date=2020-01-01&time=10:00").get_json() == {"odometer_km": None}
    assert client.get("/api/odometer?date=2026-11-07&time=10:00").status_code == 400
    assert client.get("/api/odometer?car_id=1&date=bad&time=10:00").status_code == 400


def test_base_path(pg, admin, monkeypatch):
    monkeypatch.setenv("BASE_PATH", "/expenses")
    c = create_app(connect=lambda: psycopg.connect(pg.app_url, row_factory=dict_row)).test_client()
    page = c.get("/expenses/")
    assert page.status_code == 200
    assert b'href="/expenses/new"' in page.data and b"/expenses/static/style.css" in page.data
    assert c.get("/expenses/static/app.js").status_code == 200
    assert c.get("/healthz").status_code == 200
    assert c.get("/").status_code == 404 and c.get("/expensesx/").status_code == 404


def test_security_headers(client):
    h = client.get("/").headers
    assert "script-src 'self'" in h["Content-Security-Policy"] and "unsafe-inline" not in h["Content-Security-Policy"]
    assert h["X-Content-Type-Options"] == "nosniff"


def test_site_icon(client):
    """The page names its icons, and the CSP lets the browser load them (default-src is 'none')."""
    page = client.get("/")
    assert b'rel="icon"' in page.data and b'/static/icon.svg' in page.data
    assert b'rel="apple-touch-icon"' in page.data and b'/static/icon-180.png' in page.data
    assert "img-src 'self'" in page.headers["Content-Security-Policy"]
    assert client.get("/static/icon.svg").status_code == 200
    # the header's home link carries the icon, and keeps its "Expenses" text
    import re
    brand = re.search(rb'<a class="brand"[^>]*>(.*?)</a>', page.data, re.S).group(1)
    assert b"/static/icon.svg" in brand and b"Expenses" in brand
    assert client.get("/static/icon-180.png").mimetype == "image/png"


def test_app_role_is_least_privilege(pg, admin):
    with psycopg.connect(pg.app_url, autocommit=True) as conn:
        conn.execute("SELECT * FROM public.cars")
        conn.execute("SELECT car_id, date, odometer FROM public.positions")
        for sql in ("SELECT * FROM public.charging_processes",
                    "SELECT id FROM public.positions",
                    "UPDATE public.cars SET name = 'x'",
                    "DROP TABLE expenses.expense",
                    "UPDATE expenses.category SET kind='purchase'",
                    "DELETE FROM expenses.category",
                    "SELECT * FROM expenses.ownership"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(sql)


def test_provision_is_idempotent(pg, admin):
    assert pg.provision().returncode == 0
    assert pg.provision().returncode == 0
    n = admin.execute("SELECT count(*) AS n FROM expenses.category WHERE sort_order < 100").fetchone()["n"]
    assert n == 8


def test_provision_rejects_weak_or_missing_password(pg):
    import os, subprocess
    from tests.conftest import ROOT
    for pw in ("", "short", "has space in it 1234567890", "quote'inside1234567890"):
        env = {**os.environ, "DB_CONTAINER": pg.container, "EXPENSES_DB_PASS": pw}
        r = subprocess.run([str(ROOT / "scripts" / "provision-db.sh")], env=env, capture_output=True, text=True)
        assert r.returncode != 0 and "EXPENSES_DB_PASS" in r.stderr


def test_ownership_view(admin):
    purchase = category_id(admin, "Initial purchase")
    insurance = category_id(admin, "Insurance")
    admin.execute("""INSERT INTO positions (latitude, longitude, date, odometer, car_id) VALUES
        (0, 0, '2025-01-10 00:00', 2123, 1), (0, 0, '2026-11-01 00:00', 57197, 1)""")
    admin.execute("INSERT INTO charging_processes (start_date, position_id, cost, car_id) VALUES (now(), 1, 100.25, 1), (now(), 1, NULL, 1), (now(), 1, 50, 1)")
    admin.execute("""INSERT INTO expenses.expense (car_id, category_id, occurred_at, amount, odometer_km) VALUES
        (1, %s, '2024-01-01 00:00+10', 60000, 1500), (1, %s, '2025-06-01 00:00+10', 1000, 5000)""",
                  (purchase, insurance))
    r = admin.execute("SELECT * FROM expenses.ownership").fetchone()
    assert (r["purchase_total"], r["running_total"], r["charging_total"], r["tco"]) == (60000, 1000, 150.25, 61150.25)
    assert (r["start_km"], r["current_km"], r["distance_km"]) == (1500, 57197, 55697)   # purchase odometer wins
    assert r["owned_since"] == datetime.fromisoformat("2024-01-01T00:00:00+10:00")


def test_ownership_view_without_purchase_or_data(admin):
    admin.execute("INSERT INTO positions (latitude, longitude, date, odometer, car_id) VALUES (0, 0, '2025-01-10 00:00', 2000, 1), (0, 0, '2025-02-10', 2600, 1)")
    r = admin.execute("SELECT * FROM expenses.ownership").fetchone()
    assert (r["tco"], r["start_km"], r["distance_km"]) == (0, 2000, 600)
    admin.execute("TRUNCATE positions")
    r = admin.execute("SELECT * FROM expenses.ownership").fetchone()
    assert r["distance_km"] is None and r["owned_since"] is None


def test_settings_page_saves_urls_and_header_links_to_dashboards(client, admin):
    """Like TeslaMate's own Settings page: the URLs are set in this app's UI, not in compose."""
    admin.execute("UPDATE expenses.setting SET expenses_url = NULL, grafana_url = NULL")
    assert b"Dashboards" not in client.get("/").data          # nothing set, so no dead link
    assert b'name="grafana_url"' in client.get("/settings").data
    ok = client.post("/settings", data={"expenses_url": "https://tm.example.test/expenses/",
                                        "grafana_url": " https://tm.example.test/grafana/d/tco "},
                     headers=ORIGIN)
    assert ok.status_code == 303
    row = admin.execute("SELECT expenses_url, grafana_url FROM expenses.setting").fetchone()
    assert row == {"expenses_url": "https://tm.example.test/expenses/",
                   "grafana_url": "https://tm.example.test/grafana/d/tco"}
    assert b'href="https://tm.example.test/grafana/d/tco"' in client.get("/").data
    # blank clears a URL
    client.post("/settings", data={"expenses_url": "", "grafana_url": ""}, headers=ORIGIN)
    assert b"Dashboards" not in client.get("/").data


def test_settings_rejects_non_http_urls(client, admin):
    for bad in ("javascript:alert(1)", "ftp://x.test/", "not a url", "https://a b.test/"):
        r = client.post("/settings", data={"expenses_url": "", "grafana_url": bad}, headers=ORIGIN)
        assert r.status_code == 400 and b"http" in r.data, bad
    assert admin.execute("SELECT grafana_url FROM expenses.setting").fetchone()["grafana_url"] is None


def test_settings_invoice_name_format(client, admin):
    q = lambda: admin.execute("SELECT invoice_name_format AS f FROM expenses.setting").fetchone()["f"]
    base = {"expenses_url": "", "grafana_url": "", "invoice_date_format": ""}
    assert q() is None
    ok = client.post("/settings", data={**base, "invoice_name_format": " %date - %category - %vendor "}, headers=ORIGIN)
    assert ok.status_code == 303 and q() == "%date - %category - %vendor"
    bad = client.post("/settings", data={**base, "invoice_name_format": "%category"}, headers=ORIGIN)
    assert bad.status_code == 400 and b"%date" in bad.data and q() == "%date - %category - %vendor"
    client.post("/settings", data={**base, "invoice_name_format": ""}, headers=ORIGIN)
    assert q() is None


def test_settings_multi_amount_choice(client, admin):
    q = lambda: admin.execute("SELECT multi_amount AS f FROM expenses.setting").fetchone()["f"]
    base = {"expenses_url": "", "grafana_url": "", "invoice_date_format": "", "invoice_name_format": ""}
    assert q() is None
    page = client.get("/settings").data.decode()
    assert 'name="multi_amount"' in page and "largest" in page and "smallest" in page and "blank" in page.lower()
    assert '<option value="max" selected>' in page                      # the default, until a choice is saved
    for choice in ("min", "blank", "max"):
        assert client.post("/settings", data={**base, "multi_amount": choice}, headers=ORIGIN).status_code == 303
        assert q() == choice
        assert f'<option value="{choice}" selected>' in client.get("/settings").data.decode()
    assert client.post("/settings", data={**base, "multi_amount": "median"}, headers=ORIGIN).status_code == 400
    assert q() == "max"


def test_settings_page_describes_every_alias(client):
    page = client.get("/settings").data.decode()
    for alias in ("%date", "%category", "%vendor", "%car", "%amount", "%%"):
        assert alias in page
    assert "261107 Tyres - Tyre Shop.pdf" in page          # live example of the current setting


def test_settings_example_follows_the_saved_template(client, admin):
    admin.execute("UPDATE expenses.setting SET invoice_name_format = '%date - %category - %vendor', "
                  "invoice_date_format = 'yyyy-mm-dd'")
    assert "2026-11-07 - Tyres - Tyre Shop.pdf" in client.get("/settings").data.decode()


def test_settings_invoice_date_format(client, admin):
    q = lambda: admin.execute("SELECT invoice_date_format AS f FROM expenses.setting").fetchone()["f"]
    assert q() is None and b"yymmdd" in client.get("/settings").data      # default is shown
    ok = client.post("/settings", data={"expenses_url": "", "grafana_url": "", "invoice_date_format": " YYYY-MM-DD "},
                     headers=ORIGIN)
    assert ok.status_code == 303 and q() == "yyyy-mm-dd"
    assert b'value="yyyy-mm-dd"' in client.get("/settings").data
    bad = client.post("/settings", data={"expenses_url": "", "grafana_url": "", "invoice_date_format": "yy/mm/dd"},
                      headers=ORIGIN)
    assert bad.status_code == 400 and q() == "yyyy-mm-dd"
    client.post("/settings", data={"expenses_url": "", "grafana_url": "", "invoice_date_format": ""}, headers=ORIGIN)
    assert q() is None                                                   # blank = back to the default


# ---- the app sets up its own schema on start ---------------------------------

@pytest.fixture()
def blank_db(pg):
    """A TeslaMate database as TeslaMate leaves it: its own tables, no `expenses` schema."""
    import secrets
    from tests.conftest import STUBS
    name = f"blank_{secrets.token_hex(4)}"
    with pg.admin() as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    url = f"postgresql://postgres:{pg.admin_pass}@127.0.0.1:{pg.port}/{name}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(STUBS)
    yield url
    with pg.admin() as conn:
        conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def tables(url):
    with psycopg.connect(url) as conn:
        return {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'expenses'")}


def test_first_start_creates_the_schema(blank_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", blank_db)
    monkeypatch.delenv("SKIP_MIGRATE", raising=False)
    assert tables(blank_db) == set()
    client = create_app().test_client()           # no scripts, no injected connection
    assert {"expense", "category", "setting", "ownership"} <= tables(blank_db)
    assert client.get("/").status_code == 200 and client.get("/settings").status_code == 200


def test_restart_keeps_data_and_is_idempotent(blank_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", blank_db)
    create_app()
    with psycopg.connect(blank_db, autocommit=True) as conn:
        conn.execute("INSERT INTO expenses.category (name) VALUES ('Car wash')")
        conn.execute("UPDATE expenses.setting SET grafana_url = 'https://g.example.test/'")
    create_app()
    with psycopg.connect(blank_db) as conn:
        assert conn.execute("SELECT count(*) FROM expenses.category WHERE name = 'Car wash'").fetchone()[0] == 1
        assert conn.execute("SELECT grafana_url FROM expenses.setting").fetchone()[0] == "https://g.example.test/"
        assert conn.execute("SELECT count(*) FROM expenses.category WHERE sort_order < 100").fetchone()[0] == 8


def test_skip_migrate_for_a_restricted_role(blank_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", blank_db)
    monkeypatch.setenv("SKIP_MIGRATE", "1")
    create_app()
    assert tables(blank_db) == set()


def test_migration_failure_is_explained(pg, blank_db, monkeypatch):
    """A role that cannot create the schema gets a message saying what to do, not a stack trace."""
    with pg.admin() as conn:
        conn.execute("DROP ROLE IF EXISTS cannot_create")
        conn.execute("CREATE ROLE cannot_create LOGIN PASSWORD 'cannot-create-1234'")
    monkeypatch.setenv("DATABASE_URL", blank_db.replace("postgres:" + pg.admin_pass, "cannot_create:cannot-create-1234"))
    with pytest.raises(RuntimeError, match="SKIP_MIGRATE"):
        create_app()


# ---- first-start URLs come from FQDN_TM, like TeslaMate's own compose variable --

def stored_urls(url):
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT expenses_url, grafana_url FROM expenses.setting").fetchone()


def test_first_start_seeds_urls_from_fqdn(blank_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", blank_db)
    monkeypatch.setenv("FQDN_TM", "tm.example.test")
    monkeypatch.setenv("BASE_PATH", "/expenses")
    create_app()
    assert stored_urls(blank_db) == ("https://tm.example.test/expenses/",
                                     "https://tm.example.test/grafana/d/tm-expenses-tco")


def test_fqdn_seeds_only_once_so_clearing_a_url_sticks(blank_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", blank_db)
    monkeypatch.setenv("FQDN_TM", "tm.example.test")
    create_app()
    with psycopg.connect(blank_db, autocommit=True) as conn:
        conn.execute("UPDATE expenses.setting SET grafana_url = NULL, expenses_url = 'https://other.test/x/'")
    create_app()                                   # a restart
    assert stored_urls(blank_db) == ("https://other.test/x/", None)


def test_grafana_path_can_be_changed(blank_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", blank_db)
    monkeypatch.setenv("FQDN_TM", "tm.example.test")
    monkeypatch.setenv("GRAFANA_PATH", "/dash")
    create_app()
    assert stored_urls(blank_db)[1] == "https://tm.example.test/dash/d/tm-expenses-tco"


def test_no_fqdn_or_a_bad_one_leaves_urls_empty(blank_db, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", blank_db)
    monkeypatch.delenv("FQDN_TM", raising=False)
    create_app()
    assert stored_urls(blank_db) == (None, None)
    with psycopg.connect(blank_db, autocommit=True) as conn:
        conn.execute("DROP SCHEMA expenses CASCADE")
    for bad in ("evil.test/x y", "http://tm.example.test", "tm example"):
        monkeypatch.setenv("FQDN_TM", bad)
        create_app()                               # must not crash
        assert stored_urls(blank_db) == (None, None), bad
        with psycopg.connect(blank_db, autocommit=True) as conn:
            conn.execute("DROP SCHEMA expenses CASCADE")


def test_settings_file_name_defaults_are_real_text_you_can_edit(client, admin):
    """A placeholder vanishes as soon as you type, so the defaults are filled in as values."""
    page = client.get("/settings").data.decode()
    assert 'name="invoice_date_format" maxlength="20" value="yymmdd"' in page
    assert 'name="invoice_name_format" maxlength="100" value="%date %category - %vendor"' in page


def test_saving_the_default_file_name_keeps_following_the_default(client, admin):
    """The text is the default, so it's stored as 'default' (NULL) rather than frozen as a copy."""
    data = {"expenses_url": "", "grafana_url": "", "invoice_date_format": "yymmdd",
            "invoice_name_format": "%date %category - %vendor"}
    assert client.post("/settings", data=data, headers=ORIGIN).status_code == 303
    row = admin.execute("SELECT invoice_date_format AS d, invoice_name_format AS n FROM expenses.setting").fetchone()
    assert row["d"] is None and row["n"] is None
    data["invoice_name_format"] = "%date - %category"
    client.post("/settings", data=data, headers=ORIGIN)
    assert admin.execute("SELECT invoice_name_format AS n FROM expenses.setting").fetchone()["n"] == "%date - %category"



def test_saving_a_purchase_before_the_first_data_moves_the_dashboard_start(pg, admin, monkeypatch, tmp_path):
    """The initial purchase is almost always older than TeslaMate's first record."""
    import json
    monkeypatch.delenv("BASE_PATH", raising=False)
    monkeypatch.setenv("APP_TZ", "Australia/Brisbane")
    monkeypatch.setenv("DASHBOARD_DIR", str(tmp_path))
    app = create_app(connect=lambda: psycopg.connect(pg.app_url, row_factory=dict_row))
    app.testing = True
    target = tmp_path / "cost-of-ownership.json"
    before = json.loads(target.read_text())["time"]["from"]
    resp = app.test_client().post("/new", headers=ORIGIN, data=form(
        admin, category_id=str(category_id(admin, "Initial purchase")), date="2019-03-05", amount="60,000"))
    assert resp.status_code == 303
    after = json.loads(target.read_text())["time"]["from"]
    assert after == "2019-03-01T00:00:00.000Z" and after < before

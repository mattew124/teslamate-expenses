"""All SQL lives here. One short-lived connection per request — traffic is one
person entering the odd invoice, a pool would be machinery for nothing."""

import logging
import os
import re
import time
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row

from . import validation


def dsn():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    return make_conninfo(
        host=os.environ.get("DATABASE_HOST", "database"),
        port=os.environ.get("DATABASE_PORT", "5432"),
        dbname=os.environ["DATABASE_NAME"],
        user=os.environ["DATABASE_USER"],
        password=os.environ["DATABASE_PASS"],
    )


DASHBOARD_UID = "tm-expenses-tco"   # uid in grafana/cost-of-ownership.json


def default_urls(env=os.environ):
    """What to put on the Settings page the first time, from FQDN_TM: the same compose
    variable TeslaMate uses for its own address. (None, None) if it is unset or not a bare
    host name. Only ever used once; after that the Settings page is the source of truth."""
    fqdn = (env.get("FQDN_TM") or "").strip()
    if not fqdn or not re.fullmatch(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?", fqdn):
        return None, None
    base = "/" + env.get("BASE_PATH", "").strip("/")
    graf = "/" + env.get("GRAFANA_PATH", "/grafana").strip("/")
    try:
        return (validation.parse_url_setting(f"https://{fqdn}{base.rstrip('/')}/"),
                validation.parse_url_setting(f"https://{fqdn}{graf}/d/{DASHBOARD_UID}"))
    except ValueError:
        return None, None


SCHEMA_SQL = Path(__file__).with_name("schema.sql")
log = logging.getLogger(__name__)   # tmexpenses.db: inherits the app's level and handlers
MIGRATE_LOCK = 7_265_171   # arbitrary; stops two workers starting at once from racing


def migrate(wait=60):
    """Create or update the `expenses` schema. schema.sql is idempotent, so this is safe on
    every start. Waits for the database to come up (TeslaMate may start after us), but
    gives up straight away on a permissions problem and says what to do about it."""
    deadline = time.monotonic() + wait
    while True:
        try:
            conn = psycopg.connect(dsn(), connect_timeout=5, autocommit=True)
            break
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                log.error("database still unreachable after %ss; giving up", wait)
                raise
            log.debug("database not ready yet, retrying")
            time.sleep(2)
    try:
        with conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATE_LOCK,))
            try:
                with conn.transaction():
                    fresh = conn.execute("SELECT to_regclass('expenses.setting') IS NULL").fetchone()[0]
                    conn.execute(SCHEMA_SQL.read_text())
                    log.info("schema %s", "created" if fresh else "checked and up to date")
                    if fresh:   # first start on this database: pre-fill from FQDN_TM
                        expenses_url, grafana_url = default_urls()
                        conn.execute("UPDATE expenses.setting SET expenses_url = %s, grafana_url = %s "
                                     "WHERE id = 1", (expenses_url, grafana_url))
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATE_LOCK,))
    except psycopg.errors.InsufficientPrivilege as exc:
        log.error("the database user cannot create the 'expenses' schema")
        raise RuntimeError(
            "The database user cannot create the 'expenses' schema. Use TeslaMate's own database "
            "user, or create the schema yourself (scripts/provision-db.sh) and set SKIP_MIGRATE=1. "
            f"({exc})") from exc


def connect():
    return psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=5)


def cars(conn):
    return conn.execute("SELECT id, name FROM public.cars ORDER BY id").fetchall()


def get_settings(conn):
    row = conn.execute("SELECT expenses_url, grafana_url, invoice_date_format, invoice_name_format, multi_amount "
                       "FROM expenses.setting WHERE id = 1").fetchone()
    return row or {"expenses_url": None, "grafana_url": None, "invoice_date_format": None,
                   "invoice_name_format": None, "multi_amount": None}


def save_settings(conn, expenses_url, grafana_url, invoice_date_format, invoice_name_format=None, multi_amount=None):
    conn.execute("UPDATE expenses.setting SET expenses_url = %s, grafana_url = %s, "
                 "invoice_date_format = %s, invoice_name_format = %s, multi_amount = %s WHERE id = 1",
                 (expenses_url, grafana_url, invoice_date_format, invoice_name_format, multi_amount))


def categories(conn):
    return conn.execute(
        "SELECT id, name, kind, upload_folder FROM expenses.category ORDER BY sort_order, name"
    ).fetchall()


def add_category(conn, name):
    """Returns the id, or None if the name already exists (case-insensitive)."""
    existing = conn.execute(
        "SELECT id FROM expenses.category WHERE lower(name) = lower(%s)", (name,)
    ).fetchone()
    if existing:
        return None
    return conn.execute(
        "INSERT INTO expenses.category (name, kind) VALUES (%s, 'running') "
        "ON CONFLICT (name) DO NOTHING RETURNING id",
        (name,),
    ).fetchone()["id"]


def set_upload_folder(conn, category_id, folder):
    """Returns False when there is no such category."""
    return conn.execute("UPDATE expenses.category SET upload_folder = %s WHERE id = %s",
                        (folder, category_id)).rowcount == 1


_SELECT = """
    SELECT e.id, e.car_id, c.name AS car_name, e.category_id, cat.name AS category,
           cat.kind, e.occurred_at, e.amount, e.odometer_km, e.vendor, e.notes,
           e.invoice_url
    FROM expenses.expense e
    JOIN public.cars c ON c.id = e.car_id
    JOIN expenses.category cat ON cat.id = e.category_id
"""


# What the list can be sorted by: the name in the address -> the SQL it means. Never built from user text.
SORTS = {"date": "e.occurred_at", "category": "cat.name", "vendor": "NULLIF(lower(e.vendor), '')",
         "amount": "e.amount", "odometer": "e.odometer_km"}


def list_expenses(conn, category_id=None, limit=1000, sort="date", direction="desc"):
    """Sorted either way. Blank vendors and odometers always come last, whichever way round."""
    order = SORTS.get(sort, SORTS["date"])
    way = "ASC" if direction == "asc" else "DESC"
    where, params = "", []
    if category_id:
        where = "WHERE e.category_id = %s"
        params.append(category_id)
    params.append(limit)
    return conn.execute(
        f"{_SELECT} {where} ORDER BY {order} {way} NULLS LAST, e.occurred_at {way}, e.id {way} LIMIT %s", params
    ).fetchall()


def totals(conn, category_id=None):
    where, params = "", []
    if category_id:
        where = "WHERE category_id = %s"
        params.append(category_id)
    return conn.execute(
        f"SELECT COALESCE(SUM(amount), 0) AS total, COUNT(*) AS n FROM expenses.expense {where}",
        params,
    ).fetchone()


def first_activity(conn):
    """The earliest thing the dashboard can show: first expense (the purchase, usually) or TeslaMate position.
    Reads only what the app may read, so it works with the least-privilege role too."""
    return conn.execute(
        "SELECT LEAST((SELECT MIN(date AT TIME ZONE 'UTC') FROM public.positions),"
        " (SELECT MIN(occurred_at) FROM expenses.expense)) AS first"
    ).fetchone()["first"]


def get_expense(conn, expense_id):
    return conn.execute(f"{_SELECT} WHERE e.id = %s", (expense_id,)).fetchone()


_FIELDS = ("car_id", "category_id", "occurred_at", "amount", "odometer_km",
           "vendor", "notes", "invoice_url")


def insert_expense(conn, v):
    return conn.execute(
        f"INSERT INTO expenses.expense ({', '.join(_FIELDS)}) "
        f"VALUES ({', '.join('%(' + f + ')s' for f in _FIELDS)}) RETURNING id",
        v,
    ).fetchone()["id"]


def learned(conn, after):
    """What the importer trusts: labels confirmed `after`+ times, and for each vendor
    its clear-cut category. -> {"labels": {field: {label}}, "vendors": {vendor: category}}."""
    labels = {}
    for r in conn.execute("SELECT field, label FROM expenses.learned_label WHERE hits >= %s", (after,)):
        labels.setdefault(r["field"], set()).add(r["label"])
    vendors, seen = {}, {}
    for r in conn.execute(
            "SELECT l.vendor, c.name, l.hits FROM expenses.learned_vendor l "
            "JOIN expenses.category c ON c.id = l.category_id WHERE l.hits >= %s "
            "ORDER BY l.vendor, l.hits DESC", (after,)):
        if r["vendor"] not in seen:
            seen[r["vendor"]] = r["hits"]
            vendors[r["vendor"]] = r["name"]
        elif seen[r["vendor"]] == r["hits"]:
            vendors.pop(r["vendor"], None)   # two categories tied: no clear answer
    return {"labels": labels, "vendors": vendors}


def record_label(conn, field, label):
    conn.execute("INSERT INTO expenses.learned_label (field, label) VALUES (%s, %s) "
                 "ON CONFLICT (field, label) DO UPDATE SET hits = expenses.learned_label.hits + 1",
                 (field, label))


def record_vendor(conn, vendor, category_id):
    conn.execute("INSERT INTO expenses.learned_vendor (vendor, category_id) VALUES (%s, %s) "
                 "ON CONFLICT (vendor, category_id) DO UPDATE SET hits = expenses.learned_vendor.hits + 1",
                 (vendor, category_id))


def learned_rows(conn):
    return (conn.execute("SELECT field, label, hits FROM expenses.learned_label ORDER BY field, label").fetchall(),
            conn.execute("SELECT l.vendor, c.name AS category, l.hits FROM expenses.learned_vendor l "
                         "JOIN expenses.category c ON c.id = l.category_id ORDER BY l.vendor, c.name").fetchall())


def delete_label(conn, field, label):
    conn.execute("DELETE FROM expenses.learned_label WHERE field = %s AND label = %s", (field, label))


def delete_vendor(conn, vendor):
    conn.execute("DELETE FROM expenses.learned_vendor WHERE vendor = %s", (vendor,))


DUPLICATE_WINDOW_DAYS = 14


def find_duplicates(conn, car_id, occurred_at, amount, window_days=DUPLICATE_WINDOW_DAYS, exclude_id=None):
    """Expenses that look like this one: same car, exactly the same amount, within `window_days`
    either side. Closest first, at most five. Category and vendor are shown, not required to match:
    the same bill is often filed under a different category or vendor spelling."""
    return conn.execute(
        f"{_SELECT} WHERE e.car_id = %(car)s AND e.amount = %(amount)s "
        "AND e.occurred_at BETWEEN %(when)s - make_interval(days => %(days)s) AND %(when)s + make_interval(days => %(days)s) "
        "AND (%(skip)s::bigint IS NULL OR e.id <> %(skip)s) "
        "ORDER BY abs(extract(epoch FROM e.occurred_at - %(when)s)), e.id LIMIT 5",
        {"car": car_id, "amount": amount, "when": occurred_at, "days": window_days, "skip": exclude_id}).fetchall()


def known_invoice_urls(conn, urls):
    """Which of these invoice links an expense already has."""
    if not urls:
        return set()
    rows = conn.execute("SELECT DISTINCT invoice_url FROM expenses.expense WHERE invoice_url = ANY(%s)",
                        (list(urls),)).fetchall()
    return {r["invoice_url"] for r in rows}


def skipped_import_urls(conn, car_id):
    return {r["url"] for r in conn.execute("SELECT url FROM expenses.import_skip WHERE car_id = %s", (car_id,))}


def skip_imports(conn, car_id, urls):
    for url in urls:
        conn.execute("INSERT INTO expenses.import_skip (car_id, url) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                     (car_id, url))


def clear_skipped_imports(conn, car_id):
    conn.execute("DELETE FROM expenses.import_skip WHERE car_id = %s", (car_id,))


def update_expense(conn, expense_id, v):
    sets = ", ".join(f"{f} = %({f})s" for f in _FIELDS)
    cur = conn.execute(
        f"UPDATE expenses.expense SET {sets}, updated_at = now() WHERE id = %(id)s",
        {**v, "id": expense_id},
    )
    return cur.rowcount == 1


def delete_expense(conn, expense_id):
    return conn.execute("DELETE FROM expenses.expense WHERE id = %s", (expense_id,)).rowcount == 1


def odometer_at(conn, car_id, when):
    """Odometer (km) from the last TeslaMate position at or before `when`.

    positions.date is a naive UTC timestamp, so the aware `when` is converted
    to UTC first — comparing it as local time would be off by the UTC offset.
    Returns None when TeslaMate hadn't started tracking the car yet.
    """
    row = conn.execute(
        "SELECT odometer FROM public.positions "
        "WHERE car_id = %s AND odometer IS NOT NULL AND date <= (%s AT TIME ZONE 'UTC') "
        "ORDER BY date DESC LIMIT 1",
        (car_id, when),
    ).fetchone()
    return None if row is None else round(row["odometer"], 1)


# ---- OneDrive ------------------------------------------------------------------

def car_folders(conn):
    """-> {car_id: {folder_id, folder_path}}"""
    return {r["car_id"]: r for r in
            conn.execute("SELECT car_id, folder_id, folder_path FROM expenses.car_folder")}


def set_car_folder(conn, car_id, folder_id, folder_path):
    conn.execute(
        "INSERT INTO expenses.car_folder (car_id, folder_id, folder_path) VALUES (%s, %s, %s) "
        "ON CONFLICT (car_id) DO UPDATE SET folder_id = EXCLUDED.folder_id, "
        "folder_path = EXCLUDED.folder_path",
        (car_id, folder_id, folder_path))


def clear_car_folder(conn, car_id):
    conn.execute("DELETE FROM expenses.car_folder WHERE car_id = %s", (car_id,))


def onedrive_account(conn, lock=False):
    """The single sign-in row, or None. lock=True holds it until commit, so two
    uploads at once can't both spend the same (single-use) refresh token."""
    return conn.execute(
        "SELECT * FROM expenses.onedrive_account" + (" FOR UPDATE" if lock else "")
    ).fetchone()


def save_onedrive_account(conn, **fields):
    """Upsert the given columns of the sign-in row."""
    cols = ", ".join(fields)
    vals = ", ".join(f"%({f})s" for f in fields)
    sets = ", ".join(f"{f} = EXCLUDED.{f}" for f in fields)
    conn.execute(
        f"INSERT INTO expenses.onedrive_account (id, {cols}) VALUES (true, {vals}) "
        f"ON CONFLICT (id) DO UPDATE SET {sets}, updated_at = now()",
        fields)


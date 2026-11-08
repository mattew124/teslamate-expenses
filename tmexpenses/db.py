"""All SQL lives here. One short-lived connection per request — traffic is one
person entering the odd invoice, a pool would be machinery for nothing."""

import os
import time
from pathlib import Path

import psycopg
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row


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


SCHEMA_SQL = Path(__file__).with_name("schema.sql")
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
                raise
            time.sleep(2)
    try:
        with conn:
            conn.execute("SELECT pg_advisory_lock(%s)", (MIGRATE_LOCK,))
            try:
                with conn.transaction():
                    conn.execute(SCHEMA_SQL.read_text())
            finally:
                conn.execute("SELECT pg_advisory_unlock(%s)", (MIGRATE_LOCK,))
    except psycopg.errors.InsufficientPrivilege as exc:
        raise RuntimeError(
            "The database user cannot create the 'expenses' schema. Use TeslaMate's own database "
            "user, or create the schema yourself (scripts/provision-db.sh) and set SKIP_MIGRATE=1. "
            f"({exc})") from exc


def connect():
    return psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=5)


def cars(conn):
    return conn.execute("SELECT id, name FROM public.cars ORDER BY id").fetchall()


def get_settings(conn):
    row = conn.execute("SELECT expenses_url, grafana_url FROM expenses.setting WHERE id = 1").fetchone()
    return row or {"expenses_url": None, "grafana_url": None}


def save_settings(conn, expenses_url, grafana_url):
    conn.execute("UPDATE expenses.setting SET expenses_url = %s, grafana_url = %s WHERE id = 1",
                 (expenses_url, grafana_url))


def categories(conn):
    return conn.execute(
        "SELECT id, name, kind FROM expenses.category ORDER BY sort_order, name"
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


_SELECT = """
    SELECT e.id, e.car_id, c.name AS car_name, e.category_id, cat.name AS category,
           cat.kind, e.occurred_at, e.amount, e.odometer_km, e.vendor, e.notes,
           e.invoice_url
    FROM expenses.expense e
    JOIN public.cars c ON c.id = e.car_id
    JOIN expenses.category cat ON cat.id = e.category_id
"""


def list_expenses(conn, category_id=None, limit=1000):
    where, params = "", []
    if category_id:
        where = "WHERE e.category_id = %s"
        params.append(category_id)
    params.append(limit)
    return conn.execute(
        f"{_SELECT} {where} ORDER BY e.occurred_at DESC, e.id DESC LIMIT %s", params
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

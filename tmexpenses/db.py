"""All SQL lives here. One short-lived connection per request — traffic is one
person entering the odd invoice, a pool would be machinery for nothing."""

import os

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


def connect():
    return psycopg.connect(dsn(), row_factory=dict_row, connect_timeout=5)


def cars(conn):
    return conn.execute("SELECT id, name FROM public.cars ORDER BY id").fetchall()


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


# ---- OneDrive ------------------------------------------------------------------

def car_folders(conn):
    """-> {car_id: folder}"""
    return {r["car_id"]: r["onedrive_folder"] for r in
            conn.execute("SELECT car_id, onedrive_folder FROM expenses.car_folder")}


def set_car_folder(conn, car_id, folder):
    if folder is None:
        conn.execute("DELETE FROM expenses.car_folder WHERE car_id = %s", (car_id,))
    else:
        conn.execute(
            "INSERT INTO expenses.car_folder (car_id, onedrive_folder) VALUES (%s, %s) "
            "ON CONFLICT (car_id) DO UPDATE SET onedrive_folder = EXCLUDED.onedrive_folder",
            (car_id, folder))


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


def forget_onedrive_account(conn):
    conn.execute("DELETE FROM expenses.onedrive_account")

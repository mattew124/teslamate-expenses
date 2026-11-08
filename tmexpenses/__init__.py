"""TeslaMate expenses: a small web UI for recording what the car costs to own.

Data lives in the `expenses` schema of the TeslaMate database; the Grafana
dashboard in grafana/ turns it (plus TeslaMate's own charging costs) into total
cost of ownership.
"""

import os
from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from . import db, validation

MESSAGES = {
    "saved": "Expense saved.",
    "updated": "Expense updated.",
    "deleted": "Expense deleted.",
    "category": "Category added.",
    "settings": "Settings saved.",
}


class PrefixMiddleware:
    """Serve under BASE_PATH (e.g. /expenses) without the proxy stripping it.
    /healthz stays reachable unprefixed for the container healthcheck."""

    def __init__(self, wsgi, prefix):
        self.wsgi, self.prefix = wsgi, prefix.rstrip("/")

    def __call__(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        if not self.prefix or path == "/healthz":
            return self.wsgi(environ, start_response)
        if path == self.prefix or path.startswith(self.prefix + "/"):
            environ["SCRIPT_NAME"] = self.prefix
            environ["PATH_INFO"] = path[len(self.prefix):] or "/"
            return self.wsgi(environ, start_response)
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"Not found\n"]


def create_app(connect=None):
    """`connect` is injectable so tests can hand in a connection to a scratch DB."""
    app = Flask(__name__)
    tz = os.environ.get("APP_TZ", "UTC")
    connect = connect or db.connect
    base_path = os.environ.get("BASE_PATH", "")
    if base_path:
        app.wsgi_app = PrefixMiddleware(app.wsgi_app, base_path)

    # ---- helpers --------------------------------------------------------------

    @app.template_filter("money")
    def money(value):
        return f"${value:,.2f}"

    @app.template_filter("km")
    def km(value):
        return "—" if value is None else f"{float(value):,.0f} km"

    @app.template_filter("when")
    def when(value):
        return value.astimezone(ZoneInfo(tz)).strftime("%d %b %Y %H:%M")

    @app.after_request
    def security_headers(resp):
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'self'; script-src 'self'; "
            "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.before_request
    def same_origin_posts():
        """CSRF guard: a state-changing request must come from this site's own
        pages. Behind a forward-auth gate the browser's cookies are sent
        automatically, so without this any other site could POST on the user's
        behalf."""
        if request.method in ("GET", "HEAD", "OPTIONS"):
            return
        source = request.headers.get("Origin") or request.headers.get("Referer")
        if not source or urlparse(source).netloc != request.host:
            abort(403)

    @app.context_processor
    def dashboards_link():
        """The header's link to Grafana. A failure to read it must not take the page down."""
        try:
            with connect() as conn:
                return {"grafana_url": db.get_settings(conn)["grafana_url"]}
        except Exception:
            return {"grafana_url": None}

    def form_context(values=None, errors=None, expense_id=None):
        with connect() as conn:
            return {
                "cars": db.cars(conn),
                "categories": db.categories(conn),
                "values": values or {},
                "errors": errors or {},
                "expense_id": expense_id,
            }

    def parse(form):
        with connect() as conn:
            car_ids = {c["id"] for c in db.cars(conn)}
            cat_ids = {c["id"] for c in db.categories(conn)}
        return validation.parse_expense_form(form, tz, car_ids, cat_ids)

    def form_values(form):
        """Echo the raw submission back into the form after a validation error."""
        return {k: form.get(k, "") for k in (
            "car_id", "category_id", "date", "time", "amount", "odometer_km",
            "vendor", "notes", "invoice_url")}

    # ---- routes ---------------------------------------------------------------

    @app.get("/healthz")
    def healthz():
        return "ok\n"

    @app.get("/")
    def index():
        category_id = request.args.get("category", type=int)
        with connect() as conn:
            ctx = {
                "expenses": db.list_expenses(conn, category_id),
                "totals": db.totals(conn, category_id),
                "categories": db.categories(conn),
                "category_id": category_id,
                "message": MESSAGES.get(request.args.get("msg", "")),
            }
        return render_template("index.html", **ctx)

    @app.get("/new")
    def new():
        now = datetime.now(ZoneInfo(tz))
        values = {"date": now.strftime("%Y-%m-%d"), "time": now.strftime("%H:%M")}
        ctx = form_context(values)
        if len(ctx["cars"]) == 1:
            values["car_id"] = str(ctx["cars"][0]["id"])
        return render_template("form.html", **ctx)

    @app.post("/new")
    def create():
        values, errors = parse(request.form)
        if errors:
            return render_template("form.html", **form_context(form_values(request.form), errors)), 400
        with connect() as conn:
            db.insert_expense(conn, values)
        return redirect(url_for("index", msg="saved"), 303)

    def load_or_404(conn, expense_id):
        row = db.get_expense(conn, expense_id)
        if row is None:
            abort(404)
        return row

    @app.get("/<int:expense_id>/edit")
    def edit(expense_id):
        with connect() as conn:
            row = load_or_404(conn, expense_id)
        local = row["occurred_at"].astimezone(ZoneInfo(tz))
        values = {
            "car_id": str(row["car_id"]), "category_id": str(row["category_id"]),
            "date": local.strftime("%Y-%m-%d"), "time": local.strftime("%H:%M"),
            "amount": f"{row['amount']:.2f}",
            "odometer_km": "" if row["odometer_km"] is None else f"{row['odometer_km']:.1f}",
            "vendor": row["vendor"] or "", "notes": row["notes"] or "",
            "invoice_url": row["invoice_url"] or "",
        }
        return render_template("form.html", **form_context(values, expense_id=expense_id))

    @app.post("/<int:expense_id>/edit")
    def update(expense_id):
        values, errors = parse(request.form)
        if errors:
            ctx = form_context(form_values(request.form), errors, expense_id)
            return render_template("form.html", **ctx), 400
        with connect() as conn:
            if not db.update_expense(conn, expense_id, values):
                abort(404)
        return redirect(url_for("index", msg="updated"), 303)

    @app.post("/<int:expense_id>/delete")
    def delete(expense_id):
        with connect() as conn:
            if not db.delete_expense(conn, expense_id):
                abort(404)
        return redirect(url_for("index", msg="deleted"), 303)

    @app.route("/categories", methods=["GET", "POST"])
    def categories():
        error = None
        if request.method == "POST":
            try:
                name = validation.parse_category_name(request.form.get("name"))
                with connect() as conn:
                    if db.add_category(conn, name) is None:
                        raise ValueError(f"“{name}” already exists.")
                return redirect(url_for("categories", msg="category"), 303)
            except ValueError as exc:
                error = str(exc)
        with connect() as conn:
            cats = db.categories(conn)
        return render_template("categories.html", categories=cats, error=error,
                               message=MESSAGES.get(request.args.get("msg", ""))), (400 if error else 200)

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        values, errors = {}, {}
        if request.method == "POST":
            for field in ("expenses_url", "grafana_url"):
                raw = request.form.get(field, "")
                values[field] = raw
                try:
                    values[field] = validation.parse_url_setting(raw)
                except ValueError as exc:
                    errors[field] = str(exc)
            if not errors:
                with connect() as conn:
                    db.save_settings(conn, values["expenses_url"], values["grafana_url"])
                return redirect(url_for("settings", msg="settings"), 303)
        else:
            with connect() as conn:
                values = db.get_settings(conn)
        return render_template("settings.html", values=values, errors=errors,
                               message=MESSAGES.get(request.args.get("msg", ""))), (400 if errors else 200)

    @app.get("/api/odometer")
    def api_odometer():
        """Odometer at a given local date/time — drives the form's auto-fill."""
        car_id = request.args.get("car_id", type=int)
        if car_id is None:
            return jsonify(error="car_id required"), 400
        try:
            when = validation.parse_local_datetime(
                request.args.get("date"), request.args.get("time"), tz)
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        with connect() as conn:
            return jsonify(odometer_km=db.odometer_at(conn, car_id, when))

    return app

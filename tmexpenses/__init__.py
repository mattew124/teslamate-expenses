"""TeslaMate expenses: a small web UI for recording what the car costs to own.

Data lives in the `expenses` schema of the TeslaMate database; the Grafana
dashboard in grafana/ turns it (plus TeslaMate's own charging costs) into total
cost of ownership.
"""

import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, redirect, render_template, request, url_for

from werkzeug.exceptions import RequestEntityTooLarge

from . import db, onedrive, validation

MESSAGES = {
    "saved": "Expense saved.",
    "updated": "Expense updated.",
    "deleted": "Expense deleted.",
    "category": "Category added.",
    "folders": "Folders saved.",
    "connected": "OneDrive connected.",
    "disconnected": "OneDrive disconnected.",
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


def create_app(connect=None, onedrive_transport=None):
    """`connect` is injectable so tests can hand in a connection to a scratch DB,
    and `onedrive_transport` so they can stand in for Microsoft."""
    app = Flask(__name__)
    tz = os.environ.get("APP_TZ", "UTC")
    connect = connect or db.connect
    # Room for the largest PDF plus the rest of the form.
    app.config["MAX_CONTENT_LENGTH"] = validation.MAX_PDF_BYTES + 1024 * 1024
    drive = None
    if os.environ.get("ONEDRIVE_CLIENT_ID"):
        if not os.environ.get("ONEDRIVE_TOKEN_KEY"):
            raise ValueError("ONEDRIVE_CLIENT_ID is set but ONEDRIVE_TOKEN_KEY isn't (see README.md, OneDrive)")
        drive = onedrive.OneDrive(os.environ["ONEDRIVE_CLIENT_ID"], os.environ["ONEDRIVE_TOKEN_KEY"],
                                  onedrive_transport)
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

    @app.context_processor
    def onedrive_flag():
        return {"onedrive_enabled": drive is not None}

    @app.errorhandler(RequestEntityTooLarge)
    def too_large(_exc):
        return render_template("error.html", title="File too large",
                               text="Invoice PDF is too large (max 20 MB). Go back and choose a smaller file."), 413

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

    def form_context(values=None, errors=None, expense_id=None):
        with connect() as conn:
            account = db.onedrive_account(conn) if drive else None
            return {
                "cars": db.cars(conn),
                "categories": db.categories(conn),
                "values": values or {},
                "errors": errors or {},
                "expense_id": expense_id,
                "onedrive_connected": bool(account and account["refresh_token"]),
            }

    def parse(form):
        with connect() as conn:
            car_ids = {c["id"] for c in db.cars(conn)}
            cat_ids = {c["id"] for c in db.categories(conn)}
        values, errors = validation.parse_expense_form(form, tz, car_ids, cat_ids)
        if drive is None:
            return values, errors
        upload = request.files.get("invoice_file")
        try:
            pdf = validation.parse_invoice_pdf(upload and upload.filename, upload.read() if upload else b"")
        except ValueError as exc:
            errors["invoice_file"] = str(exc)
            return values, errors
        if pdf is None:
            return values, errors
        if errors:
            errors["invoice_file"] = "Fix the fields above, then choose the PDF again."
            return values, errors
        try:
            values["invoice_url"] = upload_invoice(values, pdf)
        except onedrive.OneDriveError as exc:
            errors["invoice_file"] = str(exc)
        return values, errors

    def access_token():
        """Spend the stored refresh token and store the one Microsoft hands back."""
        with connect() as conn:
            account = db.onedrive_account(conn, lock=True)
            if not account or not account["refresh_token"]:
                raise onedrive.OneDriveError("Connect OneDrive first (OneDrive page), then try again.")
            tokens = drive.refresh(drive.decrypt(account["refresh_token"]))
            if tokens.get("refresh_token"):
                db.save_onedrive_account(conn, refresh_token=drive.encrypt(tokens["refresh_token"]))
        return tokens["access_token"]

    def upload_invoice(values, pdf):
        with connect() as conn:
            folder = db.car_folders(conn).get(values["car_id"])
            category = next(c["name"] for c in db.categories(conn) if c["id"] == values["category_id"])
            connected = (db.onedrive_account(conn) or {}).get("refresh_token")
        if not connected:
            raise onedrive.OneDriveError("Connect OneDrive first (OneDrive page), then try again.")
        if not folder:
            raise onedrive.OneDriveError("Set a OneDrive folder for this car on the OneDrive page first.")
        name = validation.invoice_filename(values["occurred_at"], category, values["vendor"])
        return drive.upload(access_token(), folder, name, pdf)

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

    # ---- OneDrive ---------------------------------------------------------------

    def need_drive():
        if drive is None:
            abort(404)

    @app.get("/onedrive")
    def onedrive_page():
        return render_onedrive()

    def render_onedrive(errors=None, status=200, notice=None):
        message = MESSAGES.get(request.args.get("msg", ""))
        if drive is None:
            return render_template("onedrive.html", message=message)
        with connect() as conn:
            account = db.onedrive_account(conn)
            ctx = {"cars": db.cars(conn), "folders": db.car_folders(conn)}
        pending = None
        if account and account["device_code"] and account["device_expires_at"] > datetime.now(timezone.utc):
            pending = account
        ctx.update(
            account=account if account and account["refresh_token"] else None,
            pending=pending, errors=errors or {}, message=message, notice=notice)
        if errors:
            ctx["folders"] = {c["id"]: request.form.get(f"folder_{c['id']}", "") for c in ctx["cars"]}
        return render_template("onedrive.html", **ctx), status

    @app.post("/onedrive/connect")
    def onedrive_connect():
        need_drive()
        try:
            flow = drive.start_sign_in()
        except onedrive.OneDriveError as exc:
            return render_onedrive(notice=str(exc), status=502)
        expires = datetime.now(timezone.utc) + timedelta(seconds=int(flow.get("expires_in", 900)))
        with connect() as conn:
            db.save_onedrive_account(
                conn, device_code=drive.encrypt(flow["device_code"]), user_code=flow["user_code"],
                verification_uri=flow["verification_uri"], device_expires_at=expires)
        return redirect(url_for("onedrive_page"), 303)

    @app.post("/onedrive/connect/check")
    def onedrive_check():
        need_drive()
        with connect() as conn:
            account = db.onedrive_account(conn)
        if not account or not account["device_code"]:
            return redirect(url_for("onedrive_page"), 303)
        try:
            tokens = drive.finish_sign_in(drive.decrypt(account["device_code"]))
        except onedrive.SignInPending as exc:
            return render_onedrive(notice=str(exc))
        except onedrive.OneDriveError as exc:
            with connect() as conn:
                db.save_onedrive_account(conn, device_code=None, user_code=None,
                                         verification_uri=None, device_expires_at=None)
            return render_onedrive(notice=str(exc), status=400)
        name = drive.account_name(tokens["access_token"]) or "your Microsoft account"
        with connect() as conn:
            db.save_onedrive_account(
                conn, refresh_token=drive.encrypt(tokens["refresh_token"]), account=name,
                device_code=None, user_code=None, verification_uri=None, device_expires_at=None)
        return redirect(url_for("onedrive_page", msg="connected"), 303)

    @app.post("/onedrive/disconnect")
    def onedrive_disconnect():
        need_drive()
        with connect() as conn:
            db.forget_onedrive_account(conn)
        return redirect(url_for("onedrive_page", msg="disconnected"), 303)

    @app.post("/onedrive/folders")
    def onedrive_folders():
        need_drive()
        with connect() as conn:
            cars = db.cars(conn)
        parsed, errors = {}, {}
        for car in cars:
            try:
                parsed[car["id"]] = validation.parse_onedrive_folder(request.form.get(f"folder_{car['id']}"))
            except ValueError as exc:
                errors[car["id"]] = str(exc)
        if errors:
            return render_onedrive(errors, 400)
        with connect() as conn:
            for car_id, folder in parsed.items():
                db.set_car_folder(conn, car_id, folder)
        return redirect(url_for("onedrive_page", msg="folders"), 303)

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

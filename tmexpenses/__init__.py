"""TeslaMate expenses: a small web UI for recording what the car costs to own.

Data lives in the `expenses` schema of the TeslaMate database; the Grafana
dashboard in grafana/ turns it (plus TeslaMate's own charging costs) into total
cost of ownership.
"""

import hmac
import os
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, url_for

from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db, invoices, onedrive, validation

MESSAGES = {
    "saved": "Expense saved.",
    "updated": "Expense updated.",
    "deleted": "Expense deleted.",
    "category": "Category added.",
    "settings": "Settings saved.",
    "folders": "Folders saved.",
    "connected": "OneDrive connected.",
    "credentials": "App registration saved. Now connect OneDrive.",
    "disconnected": "OneDrive disconnected.",
    "imported": "Invoices imported.",
    "category_folder": "OneDrive folder saved.",
}

# PDFs read per page: each is downloaded and text-extracted (OCR for scans) while
# the request waits, so a big folder is worked through in rounds.
IMPORT_BATCH = 10



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
    if connect is None:
        connect = db.connect
        if os.environ.get("SKIP_MIGRATE") != "1":
            db.migrate()
    # Room for the largest PDF plus the rest of the form.
    app.config["MAX_CONTENT_LENGTH"] = validation.MAX_PDF_BYTES + 1024 * 1024
    # ONEDRIVE_TOKEN_KEY turns OneDrive on; everything else is set up in the UI.
    drive = None
    if os.environ.get("ONEDRIVE_TOKEN_KEY"):
        drive = onedrive.OneDrive(os.environ["ONEDRIVE_TOKEN_KEY"], onedrive_transport)
    base_path = os.environ.get("BASE_PATH", "")
    if base_path:
        app.wsgi_app = PrefixMiddleware(app.wsgi_app, base_path)
    # The proxy's scheme and host, so the OneDrive callback address is the
    # public one. The app is only reachable through the proxy (or on loopback).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=1)

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
                raise onedrive.NotConnected()
            tokens = drive.refresh(account["client_id"], drive.decrypt(account["client_secret"]),
                                   drive.decrypt(account["refresh_token"]))
            if tokens.get("refresh_token"):
                db.save_onedrive_account(conn, refresh_token=drive.encrypt(tokens["refresh_token"]))
        return tokens["access_token"]

    def upload_invoice(values, pdf):
        with connect() as conn:
            folder = db.car_folders(conn).get(values["car_id"])
            category_row = next(c for c in db.categories(conn) if c["id"] == values["category_id"])
            category = category_row["name"]
            connected = (db.onedrive_account(conn) or {}).get("refresh_token")
            date_pattern = db.get_settings(conn)["invoice_date_format"]
        if not connected:
            raise onedrive.NotConnected()
        if not folder:
            raise onedrive.OneDriveError("Choose a OneDrive folder for this car on the OneDrive page first.")
        name = validation.invoice_filename(values["occurred_at"], category, values["vendor"], date_pattern)
        token = access_token()
        subfolder = validation.safe_folder_name(category_row["upload_folder"] or category)
        return drive.upload(token, drive.ensure_subfolder(token, folder["folder_id"], subfolder), name, pdf)

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

    @app.post("/categories/<int:category_id>/folder")
    def category_folder(category_id):
        try:
            folder = validation.parse_upload_folder(request.form.get("upload_folder"))
        except ValueError as exc:
            with connect() as conn:
                cats = db.categories(conn)
            return render_template("categories.html", categories=cats, error=None,
                                   folder_error=(category_id, str(exc)), message=None), 400
        with connect() as conn:
            if not db.set_upload_folder(conn, category_id, folder):
                abort(404)
        return redirect(url_for("categories", msg="category_folder"), 303)

    # ---- OneDrive ---------------------------------------------------------------

    STATE_COOKIE = "onedrive_sign_in"
    SIGN_IN_MINUTES = 10

    def need_drive():
        if drive is None:
            abort(404)

    def callback_url():
        return url_for("onedrive_callback", _external=True)

    def car_or_404(car_id):
        with connect() as conn:
            car = next((c for c in db.cars(conn) if c["id"] == car_id), None)
        if car is None:
            abort(404)
        return car

    @app.get("/onedrive")
    def onedrive_page():
        return render_onedrive()

    def render_onedrive(errors=None, status=200, notice=None):
        message = MESSAGES.get(request.args.get("msg", ""))
        if drive is None:
            return render_template("onedrive.html", message=message), status
        with connect() as conn:
            account = db.onedrive_account(conn)
            cars, folders = db.cars(conn), db.car_folders(conn)
        expires = account and account["secret_expires"]
        ctx = {
            "account": account, "cars": cars, "folders": folders,
            "connected": bool(account and account["refresh_token"]),
            "callback_url": callback_url(), "errors": errors or {},
            "values": request.form if errors else {}, "message": message, "notice": notice,
            "secret_days_left": (expires - date.today()).days if expires else None,
        }
        return render_template("onedrive.html", **ctx), status

    @app.post("/onedrive/credentials")
    def onedrive_credentials():
        need_drive()
        errors, fields = {}, {}
        for field, fn in (("client_id", validation.parse_client_id),
                          ("secret_expires", validation.parse_optional_date)):
            try:
                fields[field] = fn(request.form.get(field))
            except ValueError as exc:
                errors[field] = str(exc)
        secret = (request.form.get("client_secret") or "").strip()
        if not secret:
            errors["client_secret"] = "Paste the client secret's Value (not its Secret ID)."
        if errors:
            return render_onedrive(errors, 400)
        with connect() as conn:
            # A new registration can't use the old one's sign-in.
            db.save_onedrive_account(conn, client_secret=drive.encrypt(secret), refresh_token=None,
                                     account=None, auth_state=None, auth_expires_at=None, **fields)
        return redirect(url_for("onedrive_page", msg="credentials"), 303)

    @app.post("/onedrive/connect")
    def onedrive_connect():
        need_drive()
        with connect() as conn:
            account = db.onedrive_account(conn)
        if not account or not account["client_id"]:
            return redirect(url_for("onedrive_page"), 303)
        state, verifier, challenge = onedrive.new_pkce()
        with connect() as conn:
            db.save_onedrive_account(
                conn, auth_state=drive.encrypt(f"{state} {verifier}"),
                auth_expires_at=datetime.now(timezone.utc) + timedelta(minutes=SIGN_IN_MINUTES))
        # Not a redirect: Chrome applies form-action to a form POST's redirect.
        # A link (followed by app.js) is a plain navigation, as n8n does it.
        resp = make_response(render_template(
            "connecting.html",
            sign_in_url=drive.authorize_url(account["client_id"], callback_url(), state, challenge)))
        # Ties the sign-in to this browser: the callback must come back to the one that started it.
        resp.set_cookie(STATE_COOKIE, state, max_age=SIGN_IN_MINUTES * 60, httponly=True,
                        samesite="Lax", secure=request.is_secure, path=request.script_root or "/")
        return resp

    @app.get("/onedrive/callback")
    def onedrive_callback():
        need_drive()
        with connect() as conn:
            account = db.onedrive_account(conn)
        state = request.args.get("state", "")
        try:
            pending = account and account["auth_state"] and drive.decrypt(account["auth_state"]).split(" ")
        except onedrive.SignInExpired:
            pending = None
        if (not pending or account["auth_expires_at"] < datetime.now(timezone.utc)
                or not hmac.compare_digest(pending[0], state)
                or not hmac.compare_digest(request.cookies.get(STATE_COOKIE, ""), state)):
            return render_onedrive(status=400, notice=(
                "This sign-in wasn't started from this browser, or took longer than "
                f"{SIGN_IN_MINUTES} minutes. Press Connect OneDrive to try again."))
        with connect() as conn:
            db.save_onedrive_account(conn, auth_state=None, auth_expires_at=None)
        if request.args.get("error"):
            return render_onedrive(status=400, notice="Microsoft didn't connect OneDrive: "
                                   + (request.args.get("error_description") or request.args["error"]))
        try:
            tokens = drive.redeem_code(account["client_id"], drive.decrypt(account["client_secret"]),
                                       request.args.get("code", ""), callback_url(), pending[1])
        except onedrive.OneDriveError as exc:
            return render_onedrive(status=400, notice=str(exc))
        name = drive.account_name(tokens["access_token"]) or "your Microsoft account"
        with connect() as conn:
            db.save_onedrive_account(conn, refresh_token=drive.encrypt(tokens["refresh_token"]), account=name)
        resp = redirect(url_for("onedrive_page", msg="connected"), 303)
        resp.delete_cookie(STATE_COOKIE, path=request.script_root or "/")
        return resp

    @app.post("/onedrive/disconnect")
    def onedrive_disconnect():
        need_drive()
        with connect() as conn:
            db.save_onedrive_account(conn, refresh_token=None, account=None)
        return redirect(url_for("onedrive_page", msg="disconnected"), 303)

    @app.get("/onedrive/browse/<int:car_id>")
    def onedrive_browse(car_id):
        need_drive()
        return render_browse(car_or_404(car_id), request.args.get("folder") or None)

    def render_browse(car, folder_id, error=None, status=200):
        try:
            token = access_token()
            here = drive.folder(token, folder_id)
            children = drive.subfolders(token, folder_id)
        except onedrive.NotConnected:
            return redirect(url_for("onedrive_page"), 303)
        except onedrive.SignInExpired as exc:
            return render_onedrive(status=400, notice=str(exc))
        except onedrive.OneDriveError as exc:
            return render_onedrive(status=502, notice=str(exc))
        return render_template("browse.html", car=car, here=here, children=children,
                               is_root=folder_id is None, error=error,
                               new_name=request.form.get("name", "") if error else ""), status

    @app.post("/onedrive/browse/<int:car_id>/new-folder")
    def onedrive_new_folder(car_id):
        need_drive()
        car = car_or_404(car_id)
        parent_id = request.form.get("parent_id") or None
        try:
            name = validation.parse_folder_name(request.form.get("name"))
            created = drive.create_folder(access_token(), parent_id, name)
        except onedrive.NotConnected:
            return redirect(url_for("onedrive_page"), 303)
        except (ValueError, onedrive.OneDriveError) as exc:
            return render_browse(car, parent_id, error=str(exc), status=400)
        return redirect(url_for("onedrive_browse", car_id=car_id, folder=created["id"]), 303)

    @app.post("/onedrive/folders/<int:car_id>")
    def onedrive_choose_folder(car_id):
        need_drive()
        car_or_404(car_id)
        try:
            chosen = drive.folder(access_token(), request.form.get("folder_id") or None)
        except onedrive.OneDriveError as exc:
            return render_onedrive(status=400, notice=f"Couldn't use that folder: {exc}")
        with connect() as conn:
            db.set_car_folder(conn, car_id, chosen["id"], chosen["path"])
        return redirect(url_for("onedrive_page", msg="folders"), 303)

    @app.post("/onedrive/folders/<int:car_id>/clear")
    def onedrive_clear_folder(car_id):
        need_drive()
        car_or_404(car_id)
        with connect() as conn:
            db.clear_car_folder(conn, car_id)
        return redirect(url_for("onedrive_page", msg="folders"), 303)

    # ---- import existing invoices from the car's OneDrive folder --------------

    def import_row(n, **fields):
        return {"n": n, "include": False, "date": "", "category_id": "", "vendor": "",
                "amount": "", "odometer_km": "", "url": "", "name": "", "folder": "",
                "note": "", **fields}

    def render_import(car, rows, remaining=0, errors=None, status=200, notice=None):
        with connect() as conn:
            categories = db.categories(conn)
        return render_template("import.html", car=car, rows=rows, remaining=remaining,
                               errors=errors or {}, categories=categories, notice=notice), status

    @app.get("/onedrive/import/<int:car_id>")
    def onedrive_import(car_id):
        need_drive()
        car = car_or_404(car_id)
        with connect() as conn:
            folder = db.car_folders(conn).get(car_id)
            categories = db.categories(conn)
        if not folder:
            return redirect(url_for("onedrive_page"), 303)
        by_name = {c["name"]: c["id"] for c in categories}
        try:
            token = access_token()
            files = drive.pdfs(token, folder["folder_id"])
            with connect() as conn:
                known = db.known_invoice_urls(conn, [f["web_url"] for f in files])
            pending = [f for f in files if f["web_url"] not in known]
            rows = []
            for n, f in enumerate(pending[:IMPORT_BATCH]):
                row = import_row(n, url=f["web_url"], name=f["name"], folder=f["folder"])
                try:
                    guess = invoices.propose(f["folder"], f["name"], invoices.extract_text(
                        drive.download(token, f["id"])), list(by_name))
                except onedrive.OneDriveError as exc:
                    row["note"] = str(exc)
                    rows.append(row)
                    continue
                row.update(
                    date=guess["date"].isoformat() if guess["date"] else "",
                    category_id=str(by_name.get(guess["category"], "")),
                    vendor=guess["vendor"] or "", odometer_km=guess["odometer_km"] or "",
                    amount=f"{guess['amount']:.2f}" if guess["amount"] is not None else "",
                    include=not guess["missing"],
                    note=("Couldn't read the " + ", ".join(guess["missing"]) + ": fill in to import."
                          if guess["missing"] else ""))
                rows.append(row)
        except onedrive.NotConnected:
            return redirect(url_for("onedrive_page"), 303)
        except onedrive.SignInExpired as exc:
            return render_onedrive(status=400, notice=str(exc))
        except onedrive.OneDriveError as exc:
            return render_onedrive(status=502, notice=str(exc))
        return render_import(car, rows, remaining=len(pending) - len(rows), status=200,
                             notice=None if files else "No PDFs found in that folder.")

    @app.post("/onedrive/import/<int:car_id>")
    def onedrive_import_confirm(car_id):
        need_drive()
        car = car_or_404(car_id)
        with connect() as conn:
            car_ids = {c["id"] for c in db.cars(conn)}
            cat_ids = {c["id"] for c in db.categories(conn)}
        count = min(request.form.get("count", type=int) or 0, 200)
        rows, chosen, errors = [], [], {}
        for n in range(count):
            f = lambda key: request.form.get(f"{key}_{n}", "")
            row = import_row(n, include=bool(f("include")), date=f("date"), category_id=f("category_id"),
                             vendor=f("vendor"), amount=f("amount"), odometer_km=f("odometer_km"),
                             url=f("url"), name=f("name"), folder=f("folder"))
            rows.append(row)
            if not row["include"]:
                continue
            values, row_errors = validation.parse_expense_form({
                "car_id": car_id, "category_id": row["category_id"], "date": row["date"], "time": "12:00",
                "amount": row["amount"], "odometer_km": row["odometer_km"], "vendor": row["vendor"],
                "invoice_url": row["url"]}, tz, car_ids, cat_ids)
            if not row["url"]:
                row_errors["invoice_url"] = "Missing link to the PDF."
            if row_errors:
                errors[n] = " ".join(row_errors.values())
            else:
                chosen.append(values)
        if errors or not chosen:
            return render_import(car, rows, errors=errors, status=400,
                                 notice=None if errors else "Tick at least one invoice to import.")
        with connect() as conn:
            known = db.known_invoice_urls(conn, [v["invoice_url"] for v in chosen])
            for values in chosen:
                if values["invoice_url"] not in known:  # a double submit imports once
                    db.insert_expense(conn, values)
        return redirect(url_for("index", msg="imported"), 303)

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
            raw = request.form.get("invoice_date_format", "")
            values["invoice_date_format"] = raw
            try:
                values["invoice_date_format"] = validation.parse_date_pattern(raw)
            except ValueError as exc:
                errors["invoice_date_format"] = str(exc)
            if not errors:
                with connect() as conn:
                    db.save_settings(conn, values["expenses_url"], values["grafana_url"],
                                     values["invoice_date_format"])
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

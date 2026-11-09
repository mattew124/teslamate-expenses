"""TeslaMate expenses: a small web UI for recording what the car costs to own.

Data lives in the `expenses` schema of the TeslaMate database; the Grafana
dashboard in grafana/ turns it (plus TeslaMate's own charging costs) into total
cost of ownership.
"""

import hmac
import logging
import os
import time
from decimal import Decimal
from datetime import date, datetime, time as dtime, timedelta, timezone
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from flask import Flask, abort, jsonify, make_response, redirect, render_template, request, url_for

from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from . import dashboard, db, docinfo, invoices, logs, onedrive, updates, validation

log = logging.getLogger("tmexpenses.app")

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
    "learned_deleted": "Removed.",
}

# PDFs read per page: each is downloaded and text-extracted (OCR for scans) while
# the request waits, so a big folder is worked through in rounds.
TOKEN_MARGIN_SECONDS = 120   # a kept access token is dropped this long before it expires
IMPORT_BATCH = 10
# ...and stop reading more files once a round has taken this long (OCR can take 10+ seconds a file,
# and a request that outlasts gunicorn's 120 s timeout is killed), leaving the rest for the next round.
IMPORT_BUDGET_SECONDS = 75
# Fields of an import row compared with what the owner submits, to learn from corrections.
LEARN_FIELDS = ("date", "category_id", "vendor", "amount", "odometer_km")



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


def create_app(connect=None, onedrive_transport=None, release_fetch=None):
    """`connect` is injectable so tests can hand in a connection to a scratch DB,
    `onedrive_transport` so they can stand in for Microsoft, and `release_fetch` for GitHub."""
    app = Flask(__name__)
    logs.configure(app.logger, os.environ.get("LOG_LEVEL"))
    log = app.logger
    tz = os.environ.get("APP_TZ", "UTC")
    if connect is None:
        connect = db.connect
        if os.environ.get("SKIP_MIGRATE") != "1":
            db.migrate()
        else:
            log.info("SKIP_MIGRATE=1: not touching the schema")
    dashboard_dir = os.environ.get("DASHBOARD_DIR")
    if dashboard_dir:
        try:
            wrote = dashboard.export(dashboard_dir, tz, os.environ.get("BASE_PATH", "").rstrip("/"))
            log.info("Grafana dashboard %s in %s", "written" if wrote else "already up to date", dashboard_dir)
        except OSError as exc:
            log.warning("Could not write the Grafana dashboard to %s: %s", dashboard_dir, exc)
    # Room for the largest PDF plus the rest of the form.
    app.config["MAX_CONTENT_LENGTH"] = validation.MAX_PDF_BYTES + 1024 * 1024
    # EXPENSES_ENCRYPTION_KEY turns OneDrive on; everything else is set up in the UI.
    drive = None
    if os.environ.get("EXPENSES_ENCRYPTION_KEY"):
        drive = onedrive.OneDrive(os.environ["EXPENSES_ENCRYPTION_KEY"], onedrive_transport)
    elif os.environ.get("ONEDRIVE_TOKEN_KEY"):
        log.warning("ONEDRIVE_TOKEN_KEY was renamed EXPENSES_ENCRYPTION_KEY; OneDrive stays off until it is set.")
    # The image is built with its release version in APP_VERSION; anything else is a development build.
    version = os.environ.get("APP_VERSION", "")
    update_check = updates.UpdateCheck(version, fetch=release_fetch,
                                       enabled=os.environ.get("UPDATE_CHECK", "1").strip() != "0")
    app.extensions["update_check"] = update_check
    base_path = os.environ.get("BASE_PATH", "")
    if base_path:
        app.wsgi_app = PrefixMiddleware(app.wsgi_app, base_path)
    # The proxy's scheme and host, so the OneDrive callback address is the
    # public one. The app is only reachable through the proxy (or on loopback).
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=1)
    log.info("started: version=%s tz=%s base_path=%s onedrive=%s update_check=%s", version or "dev", tz,
             base_path or "/", "on" if drive else "off", "on" if update_check.active else "off")
    update_check.status()    # starts the first check now, so the footer knows by the first page

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
            "img-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.context_processor
    def onedrive_flag():
        # `storage` is the provider the app is set up with: its name and icon for the pages. Another
        # provider (Google Drive, say) is another entry here, not another edit to every template.
        storage = {"name": "OneDrive", "icon": url_for("static", filename="provider-onedrive.svg")} if drive else None
        return {"onedrive_enabled": drive is not None, "storage": storage}

    @app.context_processor
    def footer():
        """The docs link, the running version and whether a newer release exists (never waits for GitHub)."""
        return {"docs_url": updates.DOCS_URL, "about": update_check.status()}

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

    def dup_view(matches):
        """Possible duplicates as the page and the JSON endpoint show them."""
        return [{"id": m["id"], "date": m["occurred_at"].astimezone(ZoneInfo(tz)).strftime("%d %b %Y"),
                 "category": m["category"], "vendor": m["vendor"] or "", "amount": f"{m['amount']:.2f}",
                 "amount_text": f"${m['amount']:,.2f}", "edit_url": url_for("edit", expense_id=m["id"])}
                for m in matches]

    def parse(form, check_duplicates=False):
        """-> (values, errors). With check_duplicates, a likely duplicate is reported as
        errors["_duplicates"] (before anything is uploaded) unless the owner has said to save anyway."""
        with connect() as conn:
            car_ids = {c["id"] for c in db.cars(conn)}
            cat_ids = {c["id"] for c in db.categories(conn)}
        values, errors = validation.parse_expense_form(form, tz, car_ids, cat_ids)
        if check_duplicates and not errors and not form.get("confirm_duplicate"):
            with connect() as conn:
                matches = db.find_duplicates(conn, values["car_id"], values["occurred_at"], values["amount"])
            if matches:
                log.debug("duplicate check: %d possible duplicate(s) of car=%d, held for confirmation", len(matches),
                          values["car_id"])
                return values, {"_duplicates": dup_view(matches)}
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
            log.info("invoice uploaded to OneDrive (%d bytes)", len(pdf))
        except onedrive.OneDriveError as exc:
            log.warning("invoice upload to OneDrive failed: %s", exc)
            errors["invoice_file"] = str(exc)
        return values, errors

    kept = {}   # this worker's access token: {"key": the stored refresh token it came with, "token", "until"}

    def access_token():
        """An access token for Graph. Microsoft's last about an hour, so one is kept and reused until
        shortly before it expires (a refresh costs most of a second). It is only reused while the stored
        refresh token is the one it came with: disconnecting, signing in again or new credentials all
        change that, so none of them leaves a kept token in use. Otherwise: spend the stored refresh token
        and store the one Microsoft hands back."""
        with connect() as conn:
            account = db.onedrive_account(conn, lock=True)
            if not account or not account["refresh_token"]:
                kept.clear()
                raise onedrive.NotConnected()
            if kept.get("key") == account["refresh_token"] and kept["until"] > time.monotonic():
                return kept["token"]
            tokens = drive.refresh(account["client_id"], drive.decrypt(account["client_secret"]),
                                   drive.decrypt(account["refresh_token"]))
            key = account["refresh_token"]
            if tokens.get("refresh_token"):
                key = drive.encrypt(tokens["refresh_token"])
                db.save_onedrive_account(conn, refresh_token=key)
        lifetime = tokens.get("expires_in", 0) - TOKEN_MARGIN_SECONDS
        kept.clear()
        if lifetime > 0:
            kept.update(key=key, token=tokens["access_token"], until=time.monotonic() + lifetime)
        return tokens["access_token"]

    def upload_invoice(values, pdf):
        with connect() as conn:
            folder = db.car_folders(conn).get(values["car_id"])
            category_row = next(c for c in db.categories(conn) if c["id"] == values["category_id"])
            category = category_row["name"]
            connected = (db.onedrive_account(conn) or {}).get("refresh_token")
            settings_row = db.get_settings(conn)
            car_name = next((c["name"] for c in db.cars(conn) if c["id"] == values["car_id"]), None)
        if not connected:
            raise onedrive.NotConnected()
        if not folder:
            raise onedrive.OneDriveError("Choose a OneDrive folder for this car on the OneDrive page first.")
        name = validation.invoice_filename(
            values["occurred_at"], category, values["vendor"], settings_row["invoice_date_format"],
            settings_row["invoice_name_format"], car=car_name, amount=values["amount"])
        timer = docinfo.Timer()
        token = access_token()
        subfolder = validation.safe_folder_name(category_row["upload_folder"] or category)
        link = drive.upload(token, drive.ensure_subfolder(token, folder["folder_id"], subfolder), name, pdf)
        log.debug("upload name=%s subfolder=%s bytes=%d sha256=%s category=%r ms=%d",
                  docinfo.quote(name), subfolder, len(pdf), docinfo.sha12(pdf), category, timer.ms)
        return link

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
        if request.args.get("msg") == "imported":
            ctx["message"] = import_message()
        return render_template("index.html", **ctx)

    def import_message():
        n, renamed, failed = (request.args.get(k, 0, type=int) for k in ("n", "renamed", "rename_failed"))
        text = f"Imported {n} invoice{'' if n == 1 else 's'}." if n else MESSAGES["imported"]
        if renamed:
            text += f" Renamed {renamed} in OneDrive."
        if failed:
            text += f" Couldn't rename {failed} (a file with that name may already exist): imported with the old name."
        return text

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
        values, errors = parse(request.form, check_duplicates=True)
        if "_duplicates" in errors:
            upload = request.files.get("invoice_file")
            ctx = form_context(form_values(request.form), {})
            return render_template("form.html", **ctx, duplicates=errors["_duplicates"],
                                   had_file=bool(upload and upload.filename)), 409
        if errors:
            return render_template("form.html", **form_context(form_values(request.form), errors)), 400
        with connect() as conn:
            expense_id = db.insert_expense(conn, values)
        log.info("expense %s saved (car %s, category %s)", expense_id, values["car_id"], values["category_id"])
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
        log.info("expense %s updated", expense_id)
        return redirect(url_for("index", msg="updated"), 303)

    @app.post("/<int:expense_id>/delete")
    def delete(expense_id):
        with connect() as conn:
            if not db.delete_expense(conn, expense_id):
                abort(404)
        log.info("expense %s deleted", expense_id)
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
                log.info("category added")
                return redirect(url_for("categories", msg="category"), 303)
            except ValueError as exc:
                error = str(exc)
        with connect() as conn:
            cats = db.categories(conn)
        return render_template("categories.html", categories=cats, error=error,
                               message=MESSAGES.get(request.args.get("msg", ""))), (400 if error else 200)

    @app.get("/learned")
    def learned_page():
        with connect() as conn:
            label_rows, vendor_rows = db.learned_rows(conn)
        return render_template("learned.html", labels=label_rows, vendors=vendor_rows, after=invoices.LEARN_AFTER,
                               message=MESSAGES.get(request.args.get("msg", "")))

    @app.post("/learned/label/delete")
    def learned_label_delete():
        with connect() as conn:
            db.delete_label(conn, request.form.get("field", ""), request.form.get("label", ""))
        return redirect(url_for("learned_page", msg="learned_deleted"), 303)

    @app.post("/learned/vendor/delete")
    def learned_vendor_delete():
        with connect() as conn:
            db.delete_vendor(conn, request.form.get("vendor", ""))
        return redirect(url_for("learned_page", msg="learned_deleted"), 303)

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
            settings_row = db.get_settings(conn)
        expires = account and account["secret_expires"]
        ctx = {
            "account": account, "cars": cars, "folders": folders,
            "connected": bool(account and account["refresh_token"]),
            "callback_url": callback_url(), "errors": errors or {}, "name_example": name_example(settings_row),
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
            log.warning("Microsoft didn't connect OneDrive: %s", request.args["error"])
            return render_onedrive(status=400, notice="Microsoft didn't connect OneDrive: "
                                   + (request.args.get("error_description") or request.args["error"]))
        try:
            tokens = drive.redeem_code(account["client_id"], drive.decrypt(account["client_secret"]),
                                       request.args.get("code", ""), callback_url(), pending[1])
        except onedrive.OneDriveError as exc:
            log.warning("OneDrive sign-in failed: %s", exc)
            return render_onedrive(status=400, notice=str(exc))
        name = drive.account_name(tokens["access_token"]) or "your Microsoft account"
        with connect() as conn:
            db.save_onedrive_account(conn, refresh_token=drive.encrypt(tokens["refresh_token"]), account=name)
        log.info("OneDrive connected")
        resp = redirect(url_for("onedrive_page", msg="connected"), 303)
        resp.delete_cookie(STATE_COOKIE, path=request.script_root or "/")
        return resp

    @app.post("/onedrive/disconnect")
    def onedrive_disconnect():
        need_drive()
        with connect() as conn:
            db.save_onedrive_account(conn, refresh_token=None, account=None)
        log.info("OneDrive disconnected")
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
            log.warning("OneDrive sign-in has expired: %s", exc)
            return render_onedrive(status=400, notice=str(exc))
        except onedrive.OneDriveError as exc:
            log.warning("OneDrive folder browse failed: %s", exc)
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
            log.warning("OneDrive folder choice failed: %s", exc)
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
                "note": "", "id": "", "guess": {}, "new_name": "", "rename": False, "odo_note": "", "duplicates": [], **fields}

    def render_import(car, rows, remaining=0, errors=None, status=200, notice=None):
        with connect() as conn:
            categories = db.categories(conn)
        return render_template("import.html", car=car, rows=rows, remaining=remaining,
                               errors=errors or {}, categories=categories, notice=notice,
                               learn_after=invoices.LEARN_AFTER), status

    def import_context(car_id):
        """What reading a file needs: the folder, category names, learned labels and settings."""
        with connect() as conn:
            return {"folder": db.car_folders(conn).get(car_id), "categories": db.categories(conn),
                    "learned": db.learned(conn, invoices.LEARN_AFTER), "settings": db.get_settings(conn),
                    "connected": bool((db.onedrive_account(conn) or {}).get("refresh_token"))}

    def pending_files(token, ctx):
        """-> (all PDFs in the folder, those with no expense yet)."""
        files = drive.pdfs(token, ctx["folder"]["folder_id"])
        with connect() as conn:
            known = db.known_invoice_urls(conn, [f["web_url"] for f in files])
        return files, [f for f in files if f["web_url"] not in known]

    def read_file(token, car, ctx, n, f):
        """Download one PDF and propose its expense: an import row. A file that can't be fetched
        becomes a row saying why, never an error for the whole page."""
        by_name = {c["name"]: c["id"] for c in ctx["categories"]}
        row = import_row(n, url=f["url"], name=f["name"], folder=f["folder"], id=f["id"])
        timer = docinfo.Timer()
        try:
            pdf = drive.download(token, f["id"])
            guess = invoices.propose(f["folder"], f["name"], invoices.extract_text(pdf), list(by_name), ctx["learned"],
                                     ctx["settings"]["multi_amount"])
        except onedrive.OneDriveError as exc:
            log.warning("import file=%s folder=%s skipped: %s", docinfo.quote(f["name"]), docinfo.quote(f["folder"]), exc)
            row["note"] = str(exc)
            return row
        have, missing = docinfo.found(guess)
        log.debug("import file=%s folder=%s size=%s sha256=%s found=%s missing=%s ms=%d", docinfo.quote(f["name"]),
                  docinfo.quote(f["folder"]), f["size"], docinfo.sha12(pdf), have, missing, timer.ms)
        row.update(
            date=guess["date"].isoformat() if guess["date"] else "",
            category_id=str(by_name.get(guess["category"], "")),
            vendor=guess["vendor"] or "", odometer_km=guess["odometer_km"] or "",
            amount=f"{guess['amount']:.2f}" if guess["amount"] is not None else "",
            include=not guess["missing"],
            note=("Couldn't read the " + ", ".join(guess["missing"]) + ": fill in to import."
                  if guess["missing"] else ""))
        if guess["date"] and not row["odometer_km"]:
            with connect() as conn:
                reading = db.odometer_at(conn, car["id"], datetime.combine(guess["date"], dtime(12), ZoneInfo(tz)))
            if reading is not None:
                row["odometer_km"] = f"{reading:.1f}".rstrip("0").rstrip(".")
                row["odo_note"] = "from TeslaMate"
        if guess["date"] and guess["amount"] is not None:
            with connect() as conn:
                same = db.find_duplicates(conn, car["id"], datetime.combine(guess["date"], dtime(12), ZoneInfo(tz)),
                                          guess["amount"])
            if same:
                row["duplicates"] = dup_view(same)
                row["include"] = False          # the owner decides, not the default
        if guess["date"] and guess["category"]:
            target = validation.invoice_filename(
                datetime.combine(guess["date"], dtime(12)), guess["category"], guess["vendor"],
                ctx["settings"]["invoice_date_format"], ctx["settings"]["invoice_name_format"],
                car=car["name"], amount=guess["amount"])
            row["new_name"] = "" if target == f["name"] else target
        # What was proposed, sent back with the form so confirming can tell what the owner changed.
        row["guess"] = {k: row[k] for k in LEARN_FIELDS}
        return row

    def file_entry(f):
        return {"id": f["id"], "name": f["name"], "folder": f["folder"], "url": f["web_url"], "size": f["size"]}

    @app.get("/onedrive/import/<int:car_id>")
    def onedrive_import(car_id):
        """The page comes up at once, with a progress bar: its script then asks for the file list
        (/scan) and for each file in turn (/read), so nothing waits on OneDrive or OCR. With
        ?sync=1 (no JavaScript) everything is read before the page is sent."""
        need_drive()
        car = car_or_404(car_id)
        ctx = import_context(car_id)
        if not ctx["folder"] or not ctx["connected"]:
            return redirect(url_for("onedrive_page"), 303)
        if not request.args.get("sync"):
            return render_template("import.html", car=car, rows=[], remaining=0, errors={}, scanning=True,
                                   categories=ctx["categories"], notice=None, learn_after=invoices.LEARN_AFTER)
        try:
            token = access_token()
            files, pending = pending_files(token, ctx)
            log.debug("import scan car=%d listed=%d already_imported=%d pending=%d batch=%d", car_id, len(files),
                      len(files) - len(pending), len(pending), min(len(pending), IMPORT_BATCH))
            rows, round_timer = [], docinfo.Timer()
            for n, f in enumerate(pending[:IMPORT_BATCH]):
                if n and round_timer.ms > IMPORT_BUDGET_SECONDS * 1000:
                    log.debug("import scan stopped after %d files: over the %ds budget", n, IMPORT_BUDGET_SECONDS)
                    break
                rows.append(read_file(token, car, ctx, n, file_entry(f)))
        except onedrive.NotConnected:
            return redirect(url_for("onedrive_page"), 303)
        except onedrive.SignInExpired as exc:
            return render_onedrive(status=400, notice=str(exc))
        except onedrive.OneDriveError as exc:
            return render_onedrive(status=502, notice=str(exc))
        return render_import(car, rows, remaining=len(pending) - len(rows), status=200,
                             notice=None if files else "No PDFs found in that folder.")

    @app.get("/onedrive/import/<int:car_id>/scan")
    def onedrive_import_scan(car_id):
        """The files still to import, listed but not read: {listed, already_imported, remaining, files}."""
        need_drive()
        car_or_404(car_id)
        ctx = import_context(car_id)
        if not ctx["folder"]:
            return jsonify(error="Choose a OneDrive folder for this car on the OneDrive page first.",
                           url=url_for("onedrive_page")), 409
        try:
            files, pending = pending_files(access_token(), ctx)
        except onedrive.NotConnected as exc:
            return jsonify(error=str(exc), url=url_for("onedrive_page")), 409
        except onedrive.SignInExpired as exc:
            return jsonify(error=str(exc), url=url_for("onedrive_page")), 400
        except onedrive.OneDriveError as exc:
            log.warning("import scan failed: %s", exc)
            return jsonify(error=str(exc)), 502
        batch = pending[:IMPORT_BATCH]
        log.debug("import scan car=%d listed=%d already_imported=%d pending=%d batch=%d", car_id, len(files),
                  len(files) - len(pending), len(pending), len(batch))
        return jsonify(listed=len(files), already_imported=len(files) - len(pending),
                       remaining=len(pending) - len(batch), files=[file_entry(f) for f in batch])

    @app.post("/onedrive/import/<int:car_id>/read")
    def onedrive_import_read(car_id):
        """One file's row (an HTML fragment) for the page to add to the table."""
        need_drive()
        car = car_or_404(car_id)
        n = request.form.get("n", type=int)
        f = {k: request.form.get(k, "") for k in ("id", "name", "folder", "url")}
        f["size"] = request.form.get("size", "")
        if n is None or n < 0 or not f["id"] or not f["name"]:
            abort(400)
        ctx = import_context(car_id)
        try:
            row = read_file(access_token(), car, ctx, n, f)
        except onedrive.NotConnected as exc:
            row = import_row(n, name=f["name"], folder=f["folder"], url=f["url"], note=str(exc))
        except onedrive.OneDriveError as exc:
            row = import_row(n, name=f["name"], folder=f["folder"], url=f["url"], note=str(exc))
        return render_template("_import_row.html", r=row, n=n, categories=ctx["categories"], errors={})

    def learn_from(imported):
        """Remember what the owner's corrections show: the label printed beside a value they
        changed, and which category a vendor's invoices get. Best effort: whatever goes
        wrong here must never undo or block the import that has just been saved."""
        try:
            token = None
            with connect() as conn:
                for row, values in imported:
                    if row["vendor"]:
                        db.record_vendor(conn, row["vendor"].strip().lower()[:100], values["category_id"])
                    typed = {"amount": values["amount"], "odometer_km": values["odometer_km"],
                             "date": values["occurred_at"].astimezone(ZoneInfo(tz)).date()}
                    guess = row["guess"]
                    changed = [f for f in invoices.LEARNABLE if typed[f] is not None and not same_value(f, typed[f], guess.get(f))]
                    if not changed or not row["id"]:
                        continue
                    token = token or access_token()
                    text = invoices.extract_text(drive.download(token, row["id"]))
                    for field in changed:
                        for label in invoices.find_labels(text, field, typed[field]):
                            db.record_label(conn, field, label)
                            log.debug("learned field=%s label=%r file=%s", field, label, docinfo.quote(row["name"]))
        except Exception:
            log.warning("learning from the import failed", exc_info=True)

    def same_value(field, typed, guessed):
        """Did the owner keep what was proposed? (guess is the form text it was shown as)"""
        try:
            if field == "amount":
                return Decimal(guessed) == typed
            if field == "date":
                return guessed == typed.isoformat()
            return float(guessed) == float(typed)
        except (ArithmeticError, ValueError, TypeError):
            return False

    @app.post("/onedrive/import/<int:car_id>")
    def onedrive_import_confirm(car_id):
        need_drive()
        car = car_or_404(car_id)
        with connect() as conn:
            car_ids = {c["id"] for c in db.cars(conn)}
            cat_rows = db.categories(conn)
            cat_ids = {c["id"] for c in cat_rows}
            settings_row = db.get_settings(conn)
        cat_names = {c["id"]: c["name"] for c in cat_rows}
        count = min(request.form.get("count", type=int) or 0, 200)
        rows, chosen, errors = [], [], {}
        for n in range(count):
            def f(key, n=n):
                return request.form.get(f"{key}_{n}", "")

            row = import_row(n, include=bool(f("include")), rename=bool(f("rename")), date=f("date"), category_id=f("category_id"),
                             vendor=f("vendor"), amount=f("amount"), odometer_km=f("odometer_km"),
                             url=f("url"), name=f("name"), folder=f("folder"), id=f("id"),
                             guess={k: f(f"guess_{k}") for k in LEARN_FIELDS})
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
                chosen.append((row, values))
        if errors or not chosen:
            return render_import(car, rows, errors=errors, status=400,
                                 notice=None if errors else "Tick at least one invoice to import.")
        imported, renamed, rename_failed = [], 0, 0
        with connect() as conn:
            known = db.known_invoice_urls(conn, [v["invoice_url"] for _, v in chosen])
        token = None
        for row, values in chosen:
            if values["invoice_url"] in known:   # a double submit imports once
                continue
            target = validation.invoice_filename(
                values["occurred_at"].astimezone(ZoneInfo(tz)), cat_names[values["category_id"]], values["vendor"],
                settings_row["invoice_date_format"], settings_row["invoice_name_format"],
                car=car["name"], amount=values["amount"])
            if row["rename"] and row["id"] and target != row["name"]:
                try:
                    token = token or access_token()
                    values["invoice_url"] = drive.rename(token, row["id"], target)
                    renamed += 1
                except onedrive.OneDriveError as exc:
                    rename_failed += 1           # import it anyway, still pointing at the old name
                    log.warning("import rename item=%s to=%s failed: %s", row["id"], docinfo.quote(target), exc)
            with connect() as conn:
                db.insert_expense(conn, values)
            imported.append((row, values))
        log.debug("import confirm car=%d rows=%d imported=%d skipped_known=%d renamed=%d rename_failed=%d", car_id,
                  len(chosen), len(imported), len(chosen) - len(imported), renamed, rename_failed)
        learn_from(imported)
        return redirect(url_for("index", msg="imported", n=len(imported), renamed=renamed,
                                rename_failed=rename_failed), 303)

    def name_example(values):
        """What an uploaded file would be called under the saved (or just-typed, if valid) settings."""
        try:
            date_pattern = validation.parse_date_pattern(values.get("invoice_date_format"))
            template = validation.parse_name_format(values.get("invoice_name_format"))
        except ValueError:
            date_pattern = template = None
        return validation.invoice_filename(datetime(2026, 11, 7, 12, 0), "Tyres", "Tyre Shop", date_pattern, template,
                                           car="Model Y", amount=Decimal("1234.50"))

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
            raw = request.form.get("invoice_name_format", "")
            values["invoice_name_format"] = raw
            try:
                values["invoice_name_format"] = validation.parse_name_format(raw)
            except ValueError as exc:
                errors["invoice_name_format"] = str(exc)
            raw = request.form.get("multi_amount", "max")
            values["multi_amount"] = raw
            try:
                values["multi_amount"] = validation.parse_multi_amount(raw)
            except ValueError as exc:
                errors["multi_amount"] = str(exc)
            if not errors:
                # The fields show the defaults as real text, so saving them unchanged means "default".
                date_pattern = values["invoice_date_format"]
                name_format = values["invoice_name_format"]
                with connect() as conn:
                    db.save_settings(conn, values["expenses_url"], values["grafana_url"],
                                     None if date_pattern == validation.DEFAULT_DATE_PATTERN else date_pattern,
                                     None if name_format == validation.DEFAULT_NAME_FORMAT else name_format,
                                     values["multi_amount"])
                log.info("settings saved")
                return redirect(url_for("settings", msg="settings"), 303)
        else:
            with connect() as conn:
                values = db.get_settings(conn)
        return render_template("settings.html", values=values, errors=errors, aliases=validation.NAME_ALIASES,
                               date_default=validation.DEFAULT_DATE_PATTERN, name_default=validation.DEFAULT_NAME_FORMAT,
                               multi_choices=validation.MULTI_AMOUNT_CHOICES, example=name_example(values),
                               message=MESSAGES.get(request.args.get("msg", ""))), (400 if errors else 200)

    @app.get("/api/onedrive/browse/<int:car_id>")
    def api_onedrive_browse(car_id):
        """One folder for the Add form's file picker. Starts in the car's folder; ?folder=<id> goes
        elsewhere (up included) and ?folder=root is the top of the drive."""
        need_drive()
        car_or_404(car_id)
        wanted = request.args.get("folder")
        with connect() as conn:
            car_folder = db.car_folders(conn).get(car_id)
            connected = (db.onedrive_account(conn) or {}).get("refresh_token")
        if not connected:
            return jsonify(error=str(onedrive.NotConnected()), url=url_for("onedrive_page")), 409
        if not wanted and not car_folder:
            return jsonify(error="Choose a OneDrive folder for this car on the OneDrive page first.",
                           url=url_for("onedrive_page")), 409
        folder_id = None if wanted == "root" else (wanted or car_folder["folder_id"])
        try:
            token = access_token()
            here, folders, files = drive.contents(token, folder_id)
        except onedrive.SignInExpired as exc:
            return jsonify(error=str(exc), url=url_for("onedrive_page")), 409
        except onedrive.OneDriveError as exc:
            log.warning("OneDrive file picker failed: %s", exc)
            return jsonify(error=str(exc)), 502
        return jsonify(id=here["id"], path=here["path"], parent_id=here["parent_id"], is_root=here["is_root"],
                       folders=folders, files=files)

    @app.post("/api/prefill")
    def api_prefill():
        """Read an invoice PDF and propose the form's fields, for the Add page: the one just chosen
        on the computer, or (onedrive_id and name) one already in OneDrive."""
        timer = docinfo.Timer()
        upload = request.files.get("invoice_file")
        filename = upload and upload.filename
        data = upload.read() if upload else b""
        if not upload and request.form.get("onedrive_id") and drive is not None:
            filename = request.form.get("name", "")
            try:
                data = drive.download(access_token(), request.form["onedrive_id"])
            except onedrive.OneDriveError as exc:
                log.warning("prefill from OneDrive failed: %s", exc)
                return jsonify(error=str(exc)), 502
        try:
            pdf = validation.parse_invoice_pdf(filename, data)
        except ValueError as exc:
            log.debug("prefill file=%s refused: %s", docinfo.quote(filename), exc)
            return jsonify(error=str(exc)), 400
        if pdf is None:
            log.debug("prefill refused: no file sent")
            return jsonify(error="Choose a PDF first."), 400
        with connect() as conn:
            categories = db.categories(conn)
            learned = db.learned(conn, invoices.LEARN_AFTER)
            multi_amount = db.get_settings(conn)["multi_amount"]
        guess = invoices.propose("", filename, invoices.extract_text(pdf), [c["name"] for c in categories], learned,
                                 multi_amount)
        have, missing = docinfo.found(guess)
        log.debug("prefill file=%s bytes=%d sha256=%s found=%s missing=%s ms=%d", docinfo.quote(filename), len(pdf),
                  docinfo.sha12(pdf), have, missing, timer.ms)
        by_name = {c["name"]: c["id"] for c in categories}
        return jsonify(
            date=guess["date"].isoformat() if guess["date"] else None,
            category_id=by_name.get(guess["category"]), vendor=guess["vendor"],
            amount=f"{guess['amount']:.2f}" if guess["amount"] is not None else None,
            odometer_km=guess["odometer_km"])

    @app.get("/api/duplicates")
    def api_duplicates():
        """Likely duplicates of the expense being entered: the Add page asks before it submits."""
        car_id = request.args.get("car_id", type=int)
        if car_id is None:
            return jsonify(error="car_id required"), 400
        try:
            when = validation.parse_local_datetime(request.args.get("date"), request.args.get("time"), tz)
            amount = validation.parse_amount(request.args.get("amount"))
        except ValueError as exc:
            return jsonify(error=str(exc)), 400
        with connect() as conn:
            return jsonify(matches=dup_view(db.find_duplicates(conn, car_id, when, amount)))

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

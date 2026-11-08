"""Turn the submitted form into clean values, or a dict of per-field errors.

Pure functions, no database or Flask — everything the DB would otherwise reject
with an opaque constraint error is caught here with a message a human can act on.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

MAX_AMOUNT = Decimal("9999999999.99")  # numeric(12,2)
MAX_ODOMETER = 9_999_999.9  # numeric(9,1)
MAX_URL_LEN = 2048
MAX_TEXT_LEN = 500
MAX_NOTES_LEN = 2000
MAX_CATEGORY_LEN = 60


def parse_amount(raw):
    """'$1,234.5' -> Decimal('1234.50'). Raises ValueError with a user message."""
    cleaned = (raw or "").replace("$", "").replace(",", "").replace(" ", "")
    if not cleaned:
        raise ValueError("Enter the amount paid.")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError("Amount must be a number, e.g. 1234.56.") from None
    if not value.is_finite():
        raise ValueError("Amount must be a number, e.g. 1234.56.")
    if value < 0:
        raise ValueError("Amount can't be negative.")
    if value > MAX_AMOUNT:  # before quantize(), which overflows on huge exponents
        raise ValueError("Amount is too large.")
    value = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value > MAX_AMOUNT:
        raise ValueError("Amount is too large.")
    return value


def parse_odometer(raw):
    """Optional. '57,197.3' -> 57197.3; blank -> None."""
    cleaned = (raw or "").replace(",", "").replace("km", "").strip()
    if not cleaned:
        return None
    try:
        value = float(cleaned)
    except ValueError:
        raise ValueError("Odometer must be a number of km.") from None
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError("Odometer must be a number of km.")
    if value < 0 or value > MAX_ODOMETER:
        raise ValueError("Odometer is out of range.")
    return round(value, 1)


def parse_invoice_url(raw):
    """Optional. Must be an https link: it is rendered as a clickable href, so
    javascript:, data: and plain http: are all refused."""
    url = (raw or "").strip()
    if not url:
        return None
    if len(url) > MAX_URL_LEN:
        raise ValueError("Link is too long.")
    if any(ch.isspace() for ch in url):
        raise ValueError("Link must not contain spaces. Copy the share link from your storage.")
    try:
        parts = urlparse(url)
    except ValueError:
        raise ValueError("That doesn't look like a valid link.") from None
    if parts.scheme.lower() != "https" or not parts.hostname:
        raise ValueError("Link must start with https:// (use your storage's Share → Copy link).")
    return url


def parse_local_datetime(date_raw, time_raw, tz):
    """'2026-11-07' + '14:05' in tz -> aware datetime."""
    date_raw, time_raw = (date_raw or "").strip(), (time_raw or "").strip()
    if not date_raw:
        raise ValueError("Enter the date.")
    if not time_raw:
        raise ValueError("Enter the time.")
    try:
        parsed = datetime.strptime(f"{date_raw} {time_raw[:5]}", "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError("Date or time isn't valid.") from None
    return parsed.replace(tzinfo=ZoneInfo(tz))


def _text(raw, limit, label):
    value = (raw or "").strip()
    if len(value) > limit:
        raise ValueError(f"{label} is too long (max {limit} characters).")
    return value or None


def parse_category_name(raw):
    name = " ".join((raw or "").split())
    if not name:
        raise ValueError("Enter a category name.")
    if len(name) > MAX_CATEGORY_LEN:
        raise ValueError(f"Category name is too long (max {MAX_CATEGORY_LEN} characters).")
    return name


def parse_url_setting(raw):
    """A blank clears the setting (None). Otherwise an http(s) URL with no spaces."""
    url = (raw or "").strip()
    if not url:
        return None
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.netloc or any(c.isspace() for c in url):
        raise ValueError("Enter a full address starting with http:// or https://")
    if len(url) > MAX_URL_LEN:
        raise ValueError("That address is too long.")
    return url


def parse_expense_form(form, tz, valid_car_ids, valid_category_ids):
    """-> (values, errors). `values` is only meaningful when `errors` is empty."""
    values, errors = {}, {}

    def attempt(field, fn, *args):
        try:
            values[field] = fn(*args)
        except ValueError as exc:
            errors[field] = str(exc)

    def pick(field, raw, valid, message):
        try:
            number = int(raw)
        except (TypeError, ValueError):
            number = None
        if number in valid:
            values[field] = number
        else:
            errors[field] = message

    pick("car_id", form.get("car_id"), valid_car_ids, "Choose a car.")
    pick("category_id", form.get("category_id"), valid_category_ids, "Choose a category.")
    attempt("amount", parse_amount, form.get("amount"))
    attempt("odometer_km", parse_odometer, form.get("odometer_km"))
    attempt("invoice_url", parse_invoice_url, form.get("invoice_url"))
    attempt("vendor", _text, form.get("vendor"), MAX_TEXT_LEN, "Vendor")
    attempt("notes", _text, form.get("notes"), MAX_NOTES_LEN, "Notes")

    try:
        values["occurred_at"] = parse_local_datetime(form.get("date"), form.get("time"), tz)
    except ValueError as exc:
        errors["date"] = str(exc)
    return values, errors

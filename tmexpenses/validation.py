"""Turn the submitted form into clean values, or a dict of per-field errors.

Pure functions, no database or Flask — everything the DB would otherwise reject
with an opaque constraint error is caught here with a message a human can act on.
"""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

MAX_AMOUNT = Decimal("9999999999.99")  # numeric(12,2)
MAX_ODOMETER = 9_999_999.9  # numeric(9,1)
MAX_URL_LEN = 2048
MAX_TEXT_LEN = 500
MAX_NOTES_LEN = 2000
MAX_CATEGORY_LEN = 60
MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_FILENAME_LEN = 120
MAX_FOLDER_NAME_LEN = 255
GUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Characters OneDrive refuses in file and folder names.
ONEDRIVE_FORBIDDEN = set('"*:<>?/\\|')


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


def parse_invoice_pdf(filename, data):
    """Optional upload. -> the bytes, or None when no file was chosen. Checks the
    content, not just the name: a PDF has its %PDF- header in the first 1 KiB."""
    if not filename and not data:
        return None
    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("Invoice PDF is too large (max 20 MB).")
    if b"%PDF-" not in data[:1024]:
        raise ValueError("Only PDF invoices can be uploaded.")
    return data


def parse_client_id(raw):
    """An Entra Application (client) ID: a GUID."""
    value = (raw or "").strip().lower()
    if not GUID.fullmatch(value):
        raise ValueError("Paste the Application (client) ID from the app's Overview page: "
                         "it looks like 1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f.")
    return value


def parse_optional_date(raw):
    value = (raw or "").strip()
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Date isn't valid.") from None


def parse_folder_name(raw):
    """A new OneDrive folder's name, checked against what OneDrive refuses."""
    name = (raw or "").strip()
    if not name:
        raise ValueError("Enter a name for the new folder.")
    if len(name) > MAX_FOLDER_NAME_LEN:
        raise ValueError(f"Folder name is too long (max {MAX_FOLDER_NAME_LEN} characters).")
    if any(ch in ONEDRIVE_FORBIDDEN or ord(ch) < 32 for ch in name):
        raise ValueError('Folder names can\'t contain \\ / : * ? " < > | or tabs.')
    if name.endswith("."):
        raise ValueError("Folder names can't end with a dot.")
    return name


def parse_upload_folder(raw):
    """The OneDrive subfolder for a category's invoices. Blank -> None (named after the category)."""
    name = (raw or "").strip()
    return parse_folder_name(name) if name else None


def safe_folder_name(name):
    """A category name as a legal OneDrive folder name (for categories with odd characters)."""
    cleaned = "".join("-" if ch in ONEDRIVE_FORBIDDEN or ord(ch) < 32 else ch for ch in name)
    return cleaned[:MAX_FOLDER_NAME_LEN].strip(" .") or "Invoices"


MAX_DATE_PATTERN_LEN = 20
_DATE_TOKENS = (("yyyy", "%Y"), ("yy", "%y"), ("mm", "%m"), ("dd", "%d"))
DEFAULT_DATE_PATTERN = "yymmdd"


def parse_date_pattern(raw):
    """The date part of uploaded invoice names, as typed on the Settings page: yyyy, yy, mm
    and dd with - _ . or a space between. Blank means the default (None)."""
    pattern = (raw or "").strip().lower()
    if not pattern:
        return None
    rest = pattern
    for token, _ in _DATE_TOKENS:
        rest = rest.replace(token, "")
    if (len(pattern) > MAX_DATE_PATTERN_LEN or rest.strip("-_. ") or len(rest) == len(pattern)
            or pattern == rest):
        raise ValueError("Use yyyy, yy, mm and dd, with - _ . or a space between, e.g. yymmdd or yyyy-mm-dd.")
    return pattern


# The file name template: %aliases for what is known about an expense, anything else as typed.
NAME_ALIASES = {
    "date": "the invoice date, in the date format above",
    "category": "the category, e.g. Tyres",
    "vendor": "the vendor or shop (left out when blank)",
    "car": "the car's name",
    "amount": "the amount paid, e.g. 1234.50",
}
DEFAULT_NAME_FORMAT = "%date %category - %vendor"
MAX_NAME_FORMAT_LEN = 100
_NAME_TOKEN = re.compile(r"(%%|%[a-z]+|%)")
_SEPARATOR_ONLY = re.compile(r"[\s\-_.,()]*")


def parse_name_format(raw):
    """The Settings 'Invoice file name' line. Blank -> None (the default). Raises
    ValueError saying what is wrong, naming the aliases that are allowed."""
    text = (raw or "").strip()
    if not text:
        return None
    allowed = ", ".join("%" + a for a in NAME_ALIASES)
    if len(text) > MAX_NAME_FORMAT_LEN:
        raise ValueError(f"Keep it under {MAX_NAME_FORMAT_LEN} characters.")
    used = set()
    for part in _NAME_TOKEN.split(text):
        if part == "%":
            raise ValueError("A lone % isn't allowed: write %% for a percent sign, or use an alias (" + allowed + ").")
        if part.startswith("%") and part != "%%":
            if part[1:] not in NAME_ALIASES:
                raise ValueError(f"Unknown alias {part}. Use {allowed}.")
            used.add(part[1:])
        elif not part.startswith("%") and any(ch in ONEDRIVE_FORBIDDEN or ord(ch) < 32 for ch in part):
            raise ValueError('File names can\'t contain \\ / : * ? " < > | here: remove "'
                             + next(ch for ch in part if ch in ONEDRIVE_FORBIDDEN or ord(ch) < 32) + '".')
    if "date" not in used:
        raise ValueError("Include %date so files sort by date.")
    if not used & {"category", "vendor"}:
        raise ValueError("Include %category or %vendor so files can be told apart.")
    return text


def invoice_filename(occurred_at, category, vendor, date_pattern=None, name_format=None, car=None, amount=None):
    """'261107 Tyres - Tyre Shop.pdf' by default; the Settings template (`name_format`, see
    parse_name_format) otherwise. A blank alias is dropped together with the separator just
    before it, so '%date - %category - %vendor' without a vendor is '261107 - Tyres.pdf'. Values
    are made legal OneDrive names whatever they hold. `date_pattern` is as parse_date_pattern."""
    fmt = date_pattern or DEFAULT_DATE_PATTERN
    for token, code in _DATE_TOKENS:
        fmt = fmt.replace(token, code)
    values = {"date": occurred_at.strftime(fmt), "category": category or "", "vendor": vendor or "",
              "car": car or "", "amount": "" if amount is None else f"{amount:.2f}"}
    parts = _NAME_TOKEN.split(name_format or DEFAULT_NAME_FORMAT)   # literal, token, literal, ...
    out = []
    for i, part in enumerate(parts):
        if part == "%%":
            out.append("%")
        elif part.startswith("%") and part[1:] in values:
            value = values[part[1:]]
            if not value:
                while out and out[-1] == "":
                    out.pop()
                if out and _SEPARATOR_ONLY.fullmatch(out[-1]):
                    out.pop()        # the separator before an empty alias goes with it
                elif not out and i + 1 < len(parts) and _SEPARATOR_ONLY.fullmatch(parts[i + 1]):
                    parts[i + 1] = ""    # ...or the one after it, when it comes first
            out.append(value)
        else:
            out.append(part)
    stem = "".join(out)
    stem = "".join("-" if ch in ONEDRIVE_FORBIDDEN or ord(ch) < 32 else ch for ch in stem)
    stem = stem[:MAX_FILENAME_LEN - 4].rstrip(" .")
    return stem + ".pdf"


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

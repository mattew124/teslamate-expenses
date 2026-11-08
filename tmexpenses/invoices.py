"""Guess an expense from an invoice PDF already in OneDrive.

Only ever a *proposal*: the import page shows it for the owner to correct and
confirm. The file's name and folder are the most reliable source (the owner
chose them), the PDF text second, and an amount is never guessed when there is
no clear "Total" line: a wrong cost silently skews the whole TCO.
"""

import logging
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .logs import Timer

log = logging.getLogger("tmexpenses.invoices")

TEXT_TIMEOUT = 30
OCR_TIMEOUT = 120
OCR_MAX_PAGES = 3
# Fewer characters than this per document means a scan with no text layer.
MIN_TEXT_CHARS = 40

# A label must be confirmed by the owner this many times before it is trusted.
LEARN_AFTER = 2
LEARNABLE = ("amount", "date", "odometer_km")
_LABEL_OK = re.compile(r"[a-z][a-z &/'()-]{1,38}[a-z)]")
_DATE_FORMATS = ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y")

FILENAME_DATE = re.compile(r"^\s*(?:(\d{4})-(\d{2})-(\d{2})|(\d{4})(\d{2})(\d{2})|(\d{2})(\d{2})(\d{2}))(?!\d)")
# Only dates the document labels as its own (never "expiry date", "due date", "next ... date").
_DATE_VALUE = r"(?:(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{4})|(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4}))"
TEXT_DATE = re.compile(
    r"(?:(?:tax\s+)?invoice\s+date|date\s+of\s+issue|issue\s+date|date\s+issued|statement\s+date|payment\s+date"
    r"|date\s+paid|receipt\s+date|period\s+of\s+insurance\s*:?\s*from|policy\s+start(?:\s+date)?|^\s*date)"
    r"\s*:?\s*" + _DATE_VALUE, re.I | re.M)
FILENAME_TRAILING_DATE = re.compile(r"(?:^|[\s_\-])(\d{2})(\d{2})(\d{2})(?=\.pdf$)", re.I)
TOTAL_LINE = re.compile(r"^\W*(?:grand\s+)?total(?:\s+(?:amount|due|paid|inc\.?\s*gst|incl\.?\s*gst|\(inc[^)]*\)))?\b[^\d\n$]*\$?\s*([\d,]+\.\d{2})\s*$", re.I)
# "Annual premium  $1,380.36" or "Amount paid:  $55.00": a label and its figure in
# adjacent layout columns (insurers' certificates don't say "Total").
PAID_LABEL = re.compile(r"(?:annual premium|amount paid|pay annually)\s*:?", re.I)
MONEY = re.compile(r"\$\s*([\d,]+\.\d{2})")
ODOMETER = re.compile(r"odometer\s*:?\s*([\d,]+)(?:\s*km)?", re.I)
_NAME_JOINERS = {"&", "and", "of", "the", "t/as"}
COMPANY = re.compile(r"([A-Z][\w&'.\- ]{2,60}?\s(?:Pty\.?\s+Ltd\.?|Pty\.?\s+Limited|Limited|Ltd\.?))\b")


def extract_text(pdf):
    """Text of a PDF via poppler's pdftotext, falling back to OCR (pdftoppm +
    tesseract) for scans. '' when nothing can be read or the tools are missing:
    the caller then just has less to propose, never an error (the log says why)."""
    timer = Timer()
    # The container's root is read-only; /dev/shm is its one writable (in-memory) place.
    scratch = "/dev/shm" if os.access("/dev/shm", os.W_OK) else None
    method, ocr_pages = "text", 0
    with tempfile.TemporaryDirectory(prefix="tmexp-", dir=scratch) as tmp:
        path = Path(tmp) / "in.pdf"
        path.write_bytes(pdf)
        text = _run(["pdftotext", "-layout", str(path), "-"], TEXT_TIMEOUT)
        if len(text.strip()) < MIN_TEXT_CHARS:
            if shutil.which("pdftoppm") and shutil.which("tesseract"):
                stem = str(Path(tmp) / "page")
                _run(["pdftoppm", "-r", "200", "-l", str(OCR_MAX_PAGES), "-png", str(path), stem], OCR_TIMEOUT)
                pages = sorted(Path(tmp).glob("page*.png"))
                ocr_pages = len(pages)
                text = "\n".join(_run(["tesseract", str(png), "-", "--psm", "6"], OCR_TIMEOUT) for png in pages)
                method = "ocr" if text.strip() else "none"
            else:
                method = "none"
                log.warning("extract: no text layer and OCR isn't installed (pdftoppm, tesseract)")
    log.debug("extract method=%s chars=%d ocr_pages=%d bytes=%d ms=%d", method, len(text.strip()), ocr_pages,
              len(pdf), timer.ms)
    return text


def _run(cmd, timeout):
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError:
        log.warning("extract: %s isn't installed", cmd[0])
        return ""
    except subprocess.TimeoutExpired:
        log.warning("extract: %s timed out after %ds", cmd[0], timeout)
        return ""
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("extract: %s failed: %s", cmd[0], exc)
        return ""
    if done.returncode != 0:
        log.warning("extract: %s exited %d: %s", cmd[0], done.returncode,
                    done.stderr.decode("utf-8", "replace").strip()[:200])
        return ""
    return done.stdout.decode("utf-8", "replace")


def _valid(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_from_filename(name):
    m = FILENAME_DATE.match(name)
    if m:
        g = m.groups()
        if g[0]:
            return _valid(int(g[0]), int(g[1]), int(g[2]))
        if g[3]:
            return _valid(int(g[3]), int(g[4]), int(g[5]))
        return _valid(2000 + int(g[6]), int(g[7]), int(g[8]))
    m = FILENAME_TRAILING_DATE.search(name)       # 'Registration_Renewal_Notice_251010.pdf'
    return _valid(2000 + int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def _date_from_text(text):
    for m in TEXT_DATE.finditer(text):
        if m.group(1):
            found = _valid(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        else:
            found = _parse_date(f"{m.group(4)} {m.group(5)} {m.group(6)}")
        if found:
            return found
    return None


def _category_in(haystack, categories):
    low = haystack.lower()
    hits = [c for c in categories if c.lower() in low]
    return max(hits, key=len) if hits else None


def _category_from_text(text, categories):
    """The category the document keeps naming (at least three times, and clearly the most),
    for files whose name and folder say nothing. Longer names are counted first and removed,
    so 'Cabin air filter' isn't also counted as 'Air filter'."""
    low, counts = text.lower(), {}
    for name in sorted(categories, key=len, reverse=True):
        pattern = re.compile(r"\b" + re.escape(name.lower()) + r"\b")
        counts[name] = len(pattern.findall(low))
        low = pattern.sub(" ", low)
    ranked = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)
    if ranked and ranked[0][1] >= 3 and (len(ranked) == 1 or ranked[0][1] > ranked[1][1]):
        return ranked[0][0]
    return None


def _table_totals(text):
    """Amounts under a 'Total...' heading when the headings sit on one line and the figures on
    the next ('Annual Premium  GST  Total Annual Premium' / '$100.00  $10.00  $110.00')."""
    rows = [[c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()] for line in text.splitlines()]
    found = []
    for i, headers in enumerate(rows):
        if len(headers) < 2 or any("$" in h for h in headers):
            continue
        for below in rows[i + 1:i + 3]:
            money = [c for c in below if MONEY.fullmatch(c)]
            if len(money) == len(headers):
                for head, figure in zip(headers, money):
                    if head.lower().startswith("total"):
                        found.append(Decimal(MONEY.fullmatch(figure).group(1).replace(",", "")))
                break
    return found


def _amount(text):
    totals = []
    for line in text.splitlines():
        m = TOTAL_LINE.match(line.strip())
        if m:
            try:
                totals.append(Decimal(m.group(1).replace(",", "")))
            except InvalidOperation:
                pass
        columns = [c.strip() for c in re.split(r"\s{2,}", line.strip())]
        for label, figure in zip(columns, columns[1:]):
            m = MONEY.fullmatch(figure)
            if m and PAID_LABEL.fullmatch(label):
                totals.append(Decimal(m.group(1).replace(",", "")))
    totals += _table_totals(text)
    # "Total" also heads sub-totals; the grand total is the largest.
    return max(totals) if totals else None


def _vendor(name, category, text):
    stem = re.sub(r"\.pdf$", "", name, flags=re.I)
    parts = [p.strip() for p in stem.split(" - ")]
    if category:
        for i, part in enumerate(parts):
            if part.lower() == category.lower() and i + 1 < len(parts):
                return parts[i + 1][:100]
    for column in re.split(r"\s{2,}|\n", text):  # layout text puts columns side by side
        m = COMPANY.match(column.strip())
        name = " ".join(m.group(1).split()) if m else ""
        # A name is capitalised words ("Example Insurer Limited"); a sentence has lower-case ones.
        if name and all(w[0].isupper() or w[0].isdigit() or w.lower() in _NAME_JOINERS for w in name.split()):
            return name[:100]
    return None


def norm_label(raw):
    """A printed label as stored: lower case, no colon. None for anything that isn't
    plainly a label: digits (dates, policy numbers), or a lone word without a colon
    (too ambiguous to trust)."""
    text = " ".join(raw.lower().replace("\u00a0", " ").split())
    colon = text.endswith(":")
    text = text.rstrip(":").strip(" -\u2013\u2014.")
    if not _LABEL_OK.fullmatch(text) or (not colon and " " not in text):
        return None
    return text


def _parse_date(text):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            pass
    return None


def _parse_value(field, text):
    text = text.strip()
    if field == "amount":
        m = MONEY.fullmatch(text) or re.fullmatch(r"([\d,]+\.\d{2})", text)
        return Decimal(m.group(1).replace(",", "")) if m else None
    if field == "date":
        return _parse_date(text)
    m = re.fullmatch(r"([\d,]+)(?:\s*km)?", text, re.I)
    return float(m.group(1).replace(",", "")) if m else None


def _variants(field, value):
    """Every way the PDF might print this value."""
    if field == "amount":
        return {f"{value:,.2f}", f"{value:.2f}"}
    if field == "date":
        d = value
        return {d.strftime(f) for f in ("%d/%m/%Y", "%d/%m/%y", "%d-%m-%Y", "%d.%m.%Y", "%Y-%m-%d", "%d %b %Y", "%d %B %Y")} | {
            f"{d.day}/{d.month}/{d.year}", f"{d.day} {d.strftime('%b %Y')}", f"{d.day} {d.strftime('%B %Y')}"}
    n = int(round(float(value)))
    return {f"{n:,}", str(n)}


def find_labels(text, field, value):
    """Labels printed next to `value` in the PDF text: the previous layout column, or
    the words before it in the same column. Only labels, never values, are returned."""
    variants = {v.lower() for v in _variants(field, value)}
    found = set()
    for line in text.splitlines():
        columns = [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]
        for i, col in enumerate(columns):
            bare = re.sub(r"\s*km$", "", col.lower()).strip()
            bare = bare.lstrip("$ ") if field == "amount" and bare.startswith("$") else bare
            if bare in variants:
                label = norm_label(columns[i - 1]) if i else None
            else:
                label = None
                for v in variants:
                    if bare.endswith(v) and len(bare) > len(v):
                        label = norm_label(bare[:-len(v)].rstrip("$ "))
                        break
            if label:
                found.add(label)
    return found


def _by_labels(text, field, labels):
    """-> ('none' | 'one' | 'many', value). The value(s) that follow any learned label."""
    values = set()
    for line in text.splitlines():
        columns = [c.strip() for c in re.split(r"\s{2,}", line.strip()) if c.strip()]
        for i, col in enumerate(columns):
            low = " ".join(col.lower().split())
            for label in labels:
                if norm_label(low) == label and i + 1 < len(columns):
                    got = _parse_value(field, columns[i + 1])
                elif low.startswith(label) and len(low) > len(label) and low[len(label):].lstrip(": ")[:1]:
                    rest = col[len(label):] if col.lower().startswith(label) else ""
                    got = _parse_value(field, rest.lstrip(": "))
                else:
                    continue
                if got is not None:
                    values.add(got)
    if not values:
        return "none", None
    return ("one", next(iter(values))) if len(values) == 1 else ("many", None)


def propose(folder, name, text, categories, learned=None):
    """-> {date, category, vendor, amount, odometer_km, missing}. `missing` names the
    required fields (date, category, amount) that couldn't be worked out. `learned` is
    {"labels": {field: {label}}, "vendors": {vendor lower-case: category}}: what the
    owner's earlier corrections taught (see db.learned)."""
    labels = (learned or {}).get("labels", {})
    vendors = (learned or {}).get("vendors", {})

    def learned_value(field):
        return _by_labels(text, field, labels[field]) if labels.get(field) else ("none", None)

    # Date: the owner's file name, then a learned label, then the built-in text rules.
    when = _date_from_filename(name)
    if when is None:
        kind, value = learned_value("date")
        when = value if kind != "none" else _date_from_text(text)

    kind, amount = learned_value("amount")
    if kind == "none":
        amount = _amount(text)

    kind, odo_value = learned_value("odometer_km")
    if kind == "none":
        odo = ODOMETER.search(text)
        odometer = odo.group(1).replace(",", "") if odo else None
    else:
        odometer = str(int(odo_value)) if odo_value is not None else None

    # Category: a name in the file name, else the vendor the owner has taught, else the folder.
    category = _category_in(name, categories)
    vendor = _vendor(name, category, text)
    if category is None and vendor and vendors.get(vendor.lower()) in categories:
        category = vendors[vendor.lower()]
    if category is None:
        category = _category_in(folder, categories)
    if category is None:
        category = _category_from_text(text, categories)

    out = {"date": when, "category": category, "vendor": vendor, "amount": amount, "odometer_km": odometer}
    out["missing"] = [f for f in ("date", "category", "amount") if out[f] is None]
    return out

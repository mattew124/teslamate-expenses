"""Guess an expense from an invoice PDF already in OneDrive.

Only ever a *proposal*: the import page shows it for the owner to correct and
confirm. The file's name and folder are the most reliable source (the owner
chose them), the PDF text second, and an amount is never guessed when there is
no clear "Total" line: a wrong cost silently skews the whole TCO.
"""

import os
import re
import shutil
import subprocess
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

TEXT_TIMEOUT = 30
OCR_TIMEOUT = 120
OCR_MAX_PAGES = 3
# Fewer characters than this per document means a scan with no text layer.
MIN_TEXT_CHARS = 40

FILENAME_DATE = re.compile(r"^\s*(?:(\d{4})-(\d{2})-(\d{2})|(\d{4})(\d{2})(\d{2})|(\d{2})(\d{2})(\d{2}))(?!\d)")
TEXT_DATE = re.compile(r"(?:invoice|tax invoice|issue|issued|statement|payment)?\s*date\s*:?\s*(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})", re.I)
TOTAL_LINE = re.compile(r"^\W*(?:grand\s+)?total(?:\s+(?:amount|due|paid|inc\.?\s*gst|incl\.?\s*gst|\(inc[^)]*\)))?\b[^\d\n$]*\$?\s*([\d,]+\.\d{2})\s*$", re.I)
# "Annual premium  $1,380.36" or "Amount paid:  $55.00": a label and its figure in
# adjacent layout columns (insurers' certificates don't say "Total").
PAID_LABEL = re.compile(r"(?:annual premium|amount paid|pay annually)\s*:?", re.I)
MONEY = re.compile(r"\$\s*([\d,]+\.\d{2})")
ODOMETER = re.compile(r"odometer\s*:?\s*([\d,]+)(?:\s*km)?", re.I)
COMPANY = re.compile(r"([A-Z][\w&'.\- ]{2,60}?\s(?:Pty\.?\s+Ltd\.?|Pty\.?\s+Limited|Limited|Ltd\.?))\b")


def extract_text(pdf):
    """Text of a PDF via poppler's pdftotext, falling back to OCR (pdftoppm +
    tesseract) for scans. '' when nothing can be read or the tools are missing:
    the caller then just has less to propose, never an error."""
    # The container's root is read-only; /dev/shm is its one writable (in-memory) place.
    scratch = "/dev/shm" if os.access("/dev/shm", os.W_OK) else None
    with tempfile.TemporaryDirectory(prefix="tmexp-", dir=scratch) as tmp:
        path = Path(tmp) / "in.pdf"
        path.write_bytes(pdf)
        text = _run(["pdftotext", "-layout", str(path), "-"], TEXT_TIMEOUT)
        if len(text.strip()) >= MIN_TEXT_CHARS or not (shutil.which("pdftoppm") and shutil.which("tesseract")):
            return text
        stem = str(Path(tmp) / "page")
        _run(["pdftoppm", "-r", "200", "-l", str(OCR_MAX_PAGES), "-png", str(path), stem], OCR_TIMEOUT)
        return "\n".join(_run(["tesseract", str(png), "-", "--psm", "6"], OCR_TIMEOUT)
                         for png in sorted(Path(tmp).glob("page*.png")))


def _run(cmd, timeout):
    try:
        done = subprocess.run(cmd, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.decode("utf-8", "replace") if done.returncode == 0 else ""


def _valid(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _date_from_filename(name):
    m = FILENAME_DATE.match(name)
    if not m:
        return None
    g = m.groups()
    if g[0]:
        return _valid(int(g[0]), int(g[1]), int(g[2]))
    if g[3]:
        return _valid(int(g[3]), int(g[4]), int(g[5]))
    return _valid(2000 + int(g[6]), int(g[7]), int(g[8]))


def _date_from_text(text):
    m = TEXT_DATE.search(text)
    return _valid(int(m.group(3)), int(m.group(2)), int(m.group(1))) if m else None


def _category(folder, name, categories):
    """The longest category name found in the file name, else in the folder path."""
    for haystack in (name, folder):
        low = haystack.lower()
        hits = [c for c in categories if c.lower() in low]
        if hits:
            return max(hits, key=len)
    return None


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
        for label, figure in zip(columns, columns[1:], strict=False):
            m = MONEY.fullmatch(figure)
            if m and PAID_LABEL.fullmatch(label):
                totals.append(Decimal(m.group(1).replace(",", "")))
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
        m = COMPANY.fullmatch(column.strip())
        if m:
            return " ".join(m.group(1).split())[:100]
    return None


def propose(folder, name, text, categories):
    """-> {date, category, vendor, amount, odometer_km, missing}. `missing` names the
    required fields (date, category, amount) that couldn't be worked out."""
    when = _date_from_filename(name) or _date_from_text(text)
    category = _category(folder, name, categories)
    odo = ODOMETER.search(text)
    out = {
        "date": when,
        "category": category,
        "vendor": _vendor(name, category, text),
        "amount": _amount(text),
        "odometer_km": odo.group(1).replace(",", "") if odo else None,
    }
    out["missing"] = [f for f in ("date", "category", "amount") if out[f] is None]
    return out

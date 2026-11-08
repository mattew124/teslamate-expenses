from datetime import timezone
from decimal import Decimal

import pytest

from tmexpenses import validation as v


@pytest.mark.parametrize("raw,expected", [
    ("1234.56", "1234.56"), ("$1,234.5", "1234.50"), (" 99 ", "99.00"),
    ("0", "0.00"), ("10.005", "10.01"),
])
def test_amount_ok(raw, expected):
    assert v.parse_amount(raw) == Decimal(expected)


@pytest.mark.parametrize("raw", ["", "abc", "-1", "NaN", "Infinity", "1e99", "99999999999"])
def test_amount_rejected(raw):
    with pytest.raises(ValueError):
        v.parse_amount(raw)


def test_odometer():
    assert v.parse_odometer("") is None
    assert v.parse_odometer(None) is None
    assert v.parse_odometer("57,197.34 km") == 57197.3
    for bad in ("x", "-5", "nan", "inf", "99999999"):
        with pytest.raises(ValueError):
            v.parse_odometer(bad)


def test_invoice_url():
    assert v.parse_invoice_url("") is None
    ok = "https://1drv.ms/b/s!AbCdEf?e=xyz"
    assert v.parse_invoice_url(f"  {ok} ") == ok
    for bad in ("javascript:alert(1)", "data:text/html,x", "http://1drv.ms/x",
                "https://", "https://a b", "ftp://x/y", "//evil.example/x",
                "https://x/" + "a" * 2100):
        with pytest.raises(ValueError):
            v.parse_invoice_url(bad)


def test_local_datetime_is_brisbane_not_utc():
    dt = v.parse_local_datetime("2026-11-07", "14:05", "Australia/Brisbane")
    assert dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M") == "2026-11-07 04:05"
    for date, time in (("", "10:00"), ("2026-11-07", ""), ("2026-13-01", "10:00"), ("x", "y")):
        with pytest.raises(ValueError):
            v.parse_local_datetime(date, time, "Australia/Brisbane")


def test_category_name():
    assert v.parse_category_name("  Car   wash ") == "Car wash"
    for bad in ("", "   ", "x" * 61):
        with pytest.raises(ValueError):
            v.parse_category_name(bad)


def test_expense_form_collects_every_error():
    values, errors = v.parse_expense_form(
        {"car_id": "9", "category_id": "x", "amount": "", "date": "", "time": "",
         "invoice_url": "http://x"}, "Australia/Brisbane", {1}, {1})
    assert set(errors) == {"car_id", "category_id", "amount", "invoice_url", "date"}


def test_expense_form_ok():
    values, errors = v.parse_expense_form(
        {"car_id": "1", "category_id": "2", "amount": "$450", "date": "2026-01-02",
         "time": "09:30", "odometer_km": "12,000", "vendor": " Bob ", "notes": "",
         "invoice_url": ""}, "Australia/Brisbane", {1}, {2})
    assert errors == {}
    assert values["vendor"] == "Bob" and values["notes"] is None
    assert values["odometer_km"] == 12000.0 and values["amount"] == Decimal("450.00")


# ---- OneDrive ------------------------------------------------------------------

PDF = b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n1 0 obj\n<<>>\nendobj\n%%EOF\n"


def test_invoice_pdf():
    assert v.parse_invoice_pdf("", b"") is None          # no file chosen
    assert v.parse_invoice_pdf("inv.PDF", PDF) == PDF
    assert v.parse_invoice_pdf("scan", b"\r\n" + PDF) == b"\r\n" + PDF   # junk before the header is legal
    for name, data, msg in (("inv.pdf", b"", "empty"),
                            ("inv.pdf", b"<html>not a pdf", "PDF"),
                            ("photo.jpg", b"\xff\xd8\xff\xe0", "PDF"),
                            ("big.pdf", PDF + b"0" * v.MAX_PDF_BYTES, "20 MB")):
        with pytest.raises(ValueError, match=msg):
            v.parse_invoice_pdf(name, data)


def test_client_id():
    guid = "11111111-2222-3333-4444-555555555555"
    assert v.parse_client_id(f"  {guid.upper()} ") == guid
    for bad in ("", "abc", guid + "0", "{" + guid + "}"):
        with pytest.raises(ValueError, match="Application"):
            v.parse_client_id(bad)


def test_optional_date():
    from datetime import date
    assert v.parse_optional_date("") is None
    assert v.parse_optional_date("2027-06-30") == date(2027, 6, 30)
    with pytest.raises(ValueError):
        v.parse_optional_date("30/06/2027")


def test_invoice_filename():
    from datetime import datetime
    when = datetime(2026, 11, 7, 14, 5)
    assert v.invoice_filename(when, "Tyres", "Tyre Shop") == "2026-11-07 Tyres - Tyre Shop.pdf"
    assert v.invoice_filename(when, "Insurance", None) == "2026-11-07 Insurance.pdf"
    assert v.invoice_filename(when, "Tyres", 'A/B: "C"*?<>|\\ ') == "2026-11-07 Tyres - A-B- -C-------.pdf"
    long = v.invoice_filename(when, "Tyres", "x" * 500)
    assert len(long) <= 120 and long.endswith(".pdf")


def test_folder_name():
    assert v.parse_folder_name("  2027 invoices ") == "2027 invoices"
    for bad in ("", "  ", "a/b", "a\\b", "a:b", 'a"b', "a*b", "a?b", "a<b", "a>b", "a|b",
                "end.", "..", "x" * 256, "tab\there"):
        with pytest.raises(ValueError):
            v.parse_folder_name(bad)

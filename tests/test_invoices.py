"""Guessing an expense from an invoice PDF already in OneDrive: pure functions, no I/O."""

from datetime import date
from decimal import Decimal

from tmexpenses import invoices

CATS = ["Initial purchase", "Insurance", "Registration", "Tyres", "Cabin air filter",
        "Air filter", "Wheel alignment", "Tyre rotation"]

TYRES = """
   TAX INVOICE
   Invoice No      INV12345                       Example Tyres Pty Ltd
   Invoice Date:   24/10/2026                      ABN 11 111 111 111
   Fitting Date:   24/10/2026                      Odometer 45,120 km
   Notes:                                          GST      $112.23
                                                   Total    $1,234.50
"""


def test_date_from_filename_prefix():
    for name, want in (("261024 - Tyres.pdf", date(2026, 10, 24)),
                       ("20261024 Tyres.pdf", date(2026, 10, 24)),
                       ("2026-10-24 Tyres.pdf", date(2026, 10, 24)),
                       ("231122 - Insurer_CERT.PDF", date(2023, 11, 22))):
        assert invoices.propose("Maintenance", name, "", CATS)["date"] == want, name


def test_date_falls_back_to_the_invoice_date_in_the_text():
    got = invoices.propose("", "mycar - 998IH3 Invoice No -060931053.pdf", TYRES, CATS)
    assert got["date"] == date(2026, 10, 24)


def test_no_date_anywhere_is_none_and_flagged():
    got = invoices.propose("", "scan.pdf", "", CATS)
    assert got["date"] is None and "date" in got["missing"]


def test_impossible_filename_date_is_ignored():
    assert invoices.propose("", "269999 - Tyres.pdf", "", CATS)["date"] is None


def test_category_from_filename_then_folder():
    assert invoices.propose("Maintenance", "261024 - Tyres.pdf", "", CATS)["category"] == "Tyres"
    assert invoices.propose("Maintenance", "261030 - Wheel Alignment - Alignment Co.pdf", "", CATS)["category"] == "Wheel alignment"
    assert invoices.propose("Insurance", "250105 - Insurer_CERT.pdf", "", CATS)["category"] == "Insurance"
    assert invoices.propose("Registration", "231121 - Certificate.pdf", "", CATS)["category"] == "Registration"


def test_longest_category_name_wins():
    # "Cabin air filter" contains "air filter": the more specific one must win.
    assert invoices.propose("", "260101 - Cabin air filter.pdf", "", CATS)["category"] == "Cabin air filter"


def test_unknown_category_is_none_and_flagged():
    got = invoices.propose("Misc", "260101 - Something.pdf", "", CATS)
    assert got["category"] is None and "category" in got["missing"]


def test_amount_is_the_total_line():
    got = invoices.propose("Maintenance", "261024 - Tyres.pdf", TYRES, CATS)
    assert got["amount"] == Decimal("1234.50")


def test_amount_never_guessed_without_a_total():
    got = invoices.propose("", "x.pdf", "Premium  $120.00\nExcess $800.00", CATS)
    assert got["amount"] is None and "amount" in got["missing"]


def test_odometer_and_vendor_from_text():
    got = invoices.propose("Maintenance", "261024 - Tyres.pdf", TYRES, CATS)
    assert got["odometer_km"] == "45120"
    assert got["vendor"] == "Example Tyres Pty Ltd"


def test_vendor_from_filename_after_the_category():
    got = invoices.propose("", "261030 - Wheel Alignment - Alignment Co.pdf", "", CATS)
    assert got["vendor"] == "Alignment Co"


def test_complete_proposal_has_nothing_missing():
    assert invoices.propose("Maintenance", "261024 - Tyres.pdf", TYRES, CATS)["missing"] == []


def test_extract_text_survives_garbage():
    assert invoices.extract_text(b"not a pdf") == ""


INSURER = """
   19 Example Street                                 Annual                   $1,380.36
   SOMETOWN QLD 4000                                 premium:
                                                     Pay annually:            $1,380.36
   Annual premium             $1,380.36
   Excess                     $800.00
"""


def test_amount_from_premium_labels():
    assert invoices.propose("Insurance", "240212 - cert.pdf", INSURER, CATS)["amount"] == Decimal("1380.36")


def test_amount_from_amount_paid():
    assert invoices.propose("", "x.pdf", "  Amount paid:        $55.00\n  Excess  $800.00", CATS)["amount"] == Decimal("55.00")


def test_excess_alone_is_not_an_amount():
    assert invoices.propose("", "x.pdf", "Excess   $800.00\nSum insured $60,000.00", CATS)["amount"] is None

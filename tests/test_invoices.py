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


# ---- learning labels from what the owner typed ----------------------------------

from datetime import date as _date  # noqa: E402


def test_find_labels_for_an_amount_in_the_next_column():
    assert invoices.find_labels(INSURER, "amount", Decimal("1380.36")) == {"annual premium", "pay annually"}


def test_find_labels_when_label_and_value_share_a_column():
    assert invoices.find_labels("  Amount paid: $55.00\n", "amount", Decimal("55.00")) == {"amount paid"}


def test_find_labels_for_a_date_in_any_common_format():
    for text in ("Date of issue:   09/02/2024", "Date of issue:   9 Feb 2024", "Date of issue   2024-02-09",
                 "Date of issue:   09 February 2024"):
        assert invoices.find_labels(text, "date", _date(2024, 2, 9)) == {"date of issue"}, text


def test_find_labels_for_an_odometer():
    assert invoices.find_labels("Odometer reading:   45,120 km", "odometer_km", 45120.0) == {"odometer reading"}


def test_find_labels_ignores_labels_with_digits_or_a_lone_word():
    assert invoices.find_labels("Issued at 8.08pm on 09/02/2024\nDue   09/02/2024", "date", _date(2024, 2, 9)) == set()


def test_nothing_is_learned_when_the_value_isnt_in_the_text():
    assert invoices.find_labels(INSURER, "amount", Decimal("12.34")) == set()


LEARNED = {"labels": {"amount": {"sum charged"}, "date": {"date of issue"}, "odometer_km": {"odometer reading"}},
           "vendors": {"example garage pty ltd": "Tyres"}}
GARAGE = "Example Garage Pty Ltd\nDate of issue:   09/02/2024\nSum charged:   $77.50\nOdometer reading:   45,120 km\n"


def test_learned_labels_fill_what_built_in_rules_miss():
    got = invoices.propose("", "scan.pdf", GARAGE, CATS, LEARNED)
    assert got["amount"] == Decimal("77.50") and got["date"] == _date(2024, 2, 9) and got["odometer_km"] == "45120"


def test_learned_labels_are_unused_when_not_passed():
    assert invoices.propose("", "scan.pdf", GARAGE, CATS)["amount"] is None


def test_a_learned_label_with_two_different_values_leaves_the_field_blank():
    got = invoices.propose("", "scan.pdf", GARAGE + "Sum charged:   $99.00\n", CATS, LEARNED)
    assert got["amount"] is None and "amount" in got["missing"]


def test_a_learned_label_beats_the_filename_date():
    assert invoices.propose("", "261024 - x.pdf", GARAGE, CATS, LEARNED)["date"] == _date(2024, 2, 9)


def test_learned_vendor_sets_the_category_over_the_folder_and_the_filename():
    assert invoices.propose("Registration", "scan.pdf", GARAGE, CATS, LEARNED)["category"] == "Tyres"
    assert invoices.propose("Registration", "261024 - Insurance.pdf", GARAGE, CATS, LEARNED)["category"] == "Tyres"


# ---- documents that real-world testing showed the first rules missed ------------------

SCHEDULE = """
Example Cover
Policy Schedule & Tax Invoice
   Period of insurance                              From 9 February 2026 to 9 February 2027 at 4:00pm
   Basic excess                                     $1,500
Example Insurer Australia Limited ABN 11 111 111 111, AFS Licence No 123456            01 Feb 2026,22:42:09 (AEST)
   Total for Example Cover       $XXX        $XXX         $XXX          $XXX
                                       Annual Premium                       GST                Stamp Duty   Total Annual Premium
        Example Cover                      $1,342.03                     $134.20                $132.86           $1,609.09
Your insurance is arranged by us. The insurance product covers the vehicle. Insurance excludes wear.
"""

RENEWAL = """
   VEHICLE REGISTRATION                                  Due date:            20 November 2025
   Amount due                         6 months                      12 months
   ON or BEFORE due date              $384.30                       $749.05
   Next registration expiry date                                    20/05/2026              20/11/2026
   Total Due          $384.30                  $749.05
"""


def test_trailing_filename_date():
    got = invoices.propose("", "Registration_Renewal_Notice_998IH3_251010.pdf", "", CATS)
    assert got["date"] == _date(2025, 10, 10) and got["category"] == "Registration"


def test_leading_filename_date_comes_before_a_trailing_one():
    assert invoices.propose("", "261024 - x_251010.pdf", "", CATS)["date"] == _date(2026, 10, 24)


def test_expiry_and_due_dates_are_not_the_invoice_date():
    assert invoices.propose("", "notice.pdf", RENEWAL, CATS)["date"] is None


def test_long_month_issue_dates_are_read():
    assert invoices.propose("", "x.pdf", "Date of issue: 9 February 2026", CATS)["date"] == _date(2026, 2, 9)
    assert invoices.propose("", "x.pdf", "Invoice Date   05/12/2023", CATS)["date"] == _date(2023, 12, 5)


def test_the_start_of_the_period_of_insurance_is_the_date():
    assert invoices.propose("", "Policy.pdf", SCHEDULE, CATS)["date"] == _date(2026, 2, 9)


def test_total_from_a_table_with_headers_above_the_figures():
    assert invoices.propose("", "Policy.pdf", SCHEDULE, CATS)["amount"] == Decimal("1609.09")


def test_placeholder_figures_are_not_amounts():
    assert invoices.propose("", "x.pdf", "Total for Example   $XXX   $XXX", CATS)["amount"] is None


def test_several_options_on_a_total_line_default_to_the_largest():
    # 6 months or 12 months: the 12-month figure, not the late-payment surcharge lines
    assert invoices.propose("", "notice.pdf", RENEWAL, CATS)["amount"] == Decimal("749.05")


def test_the_option_choice_can_be_smallest_or_blank():
    amount = lambda choice: invoices.propose("", "notice.pdf", RENEWAL, CATS, multi_amount=choice)["amount"]
    assert amount("max") == Decimal("749.05") and amount(None) == Decimal("749.05")
    assert amount("min") == Decimal("384.30")
    assert amount("blank") is None


def test_the_option_choice_beats_other_bigger_totals():
    text = RENEWAL + "   Total premium including levies         $2,000.00\n"
    assert invoices.propose("", "n.pdf", text, CATS, multi_amount="min")["amount"] == Decimal("384.30")


def test_a_single_figure_total_ignores_the_option_choice():
    for choice in ("max", "min", "blank"):
        assert invoices.propose("Maintenance", "261024 - Tyres.pdf", TYRES, CATS, multi_amount=choice)["amount"] == Decimal("1234.50")


def test_figures_on_a_line_that_is_not_a_total_are_not_options():
    text = "   ON or BEFORE due date   $384.30   $749.05\n   AFTER due date   $458.45   $823.20\n"
    assert invoices.propose("", "n.pdf", text, CATS)["amount"] is None


def test_category_from_what_the_document_keeps_talking_about():
    assert invoices.propose("", "Policy.pdf", SCHEDULE, CATS)["category"] == "Insurance"
    assert invoices.propose("", "x.pdf", "One mention of insurance only.", CATS)["category"] is None


def test_folder_beats_the_wording_of_the_text():
    assert invoices.propose("Registration", "x.pdf", SCHEDULE, CATS)["category"] == "Registration"


def test_vendor_is_the_company_at_the_start_of_a_long_line():
    assert invoices.propose("", "Policy.pdf", SCHEDULE, CATS)["vendor"] == "Example Insurer Australia Limited"


def test_a_sentence_is_not_a_vendor():
    text = "Example CTP Insurance is provided by Example Insurer Limited\n"
    assert invoices.propose("", "x.pdf", text, CATS)["vendor"] is None
    assert invoices.propose("", "x.pdf", "Example Insurer Limited and more\n", CATS)["vendor"] == "Example Insurer Limited"


# ---- trading names: "Legal Pty Ltd ABN ... trading as Brand Name" --------------------------

LEGAL = "Example Insurer Limited"


def vendor(text, name="x.pdf"):
    return invoices.propose("", name, text, CATS)["vendor"]


def test_the_trading_name_beats_the_legal_entity():
    text = f"Page 1 of 8     {LEGAL} ABN 11 111 111 111 AFSL 123456 trading as Brand Cover        MOTREN Issued at 02:17AM\n"
    assert vendor(text) == "Brand Cover"


def test_trading_as_at_the_end_of_a_line_and_with_a_full_stop():
    assert vendor(f"      {LEGAL} ABN 11 111 111 111 trading as Brand Cover\n") == "Brand Cover"
    assert vendor(f"{LEGAL} trading as Brand Cover.\n") == "Brand Cover"


def test_other_ways_of_writing_it():
    assert vendor("Example Garage Pty Ltd t/as Corner Garage\n") == "Corner Garage"
    assert vendor("Example Garage Pty Ltd T/A Corner Garage and Tyres\n") == "Corner Garage and Tyres"
    assert vendor("Example Garage Pty Ltd Trading As Corner Garage\n") == "Corner Garage"


def test_the_name_stops_before_registration_numbers():
    assert vendor("Example Pty Ltd trading as Brand Cover ABN 11 111 111 111\n") == "Brand Cover"
    assert vendor("Example Pty Ltd trading as Brand Cover AFSL 123456\n") == "Brand Cover"


def test_the_trading_name_wins_even_when_the_legal_name_comes_first():
    text = f"{LEGAL}\nSome heading\n   {LEGAL} ABN 11 111 111 111 trading as Brand Cover\n"
    assert vendor(text) == "Brand Cover"


def test_the_pdfs_trading_name_beats_a_file_name_vendor():
    assert invoices.propose("", "261030 - Tyres - Bogus.pdf", f"{LEGAL} trading as Brand Cover\n", CATS)["vendor"] == "Brand Cover"


def test_prose_that_says_trading_as_is_not_a_vendor():
    assert vendor("These terms apply when trading as a sole trader.\n") is None
    assert vendor(f"{LEGAL} ABN 1\nWhen trading as a sole trader the policy differs.\n") == LEGAL


def test_without_a_trading_name_the_legal_company_is_used_as_before():
    assert vendor(f"      {LEGAL} ABN 11 111 111 111 AFSL 123456\n") == LEGAL


def test_a_footnote_naming_a_third_party_is_not_the_vendor():
    text = ("Department of Example Transport\n"
            "Brand CTP Insurance is provided by Other Insurer Limited (ACN 005297807) trading as Brand Insurance.\n")
    assert vendor(text) is None or "Brand" not in vendor(text)


def test_the_legal_name_before_trading_as_may_carry_registration_numbers():
    assert vendor("Example Insurer Limited (ACN 005 297 807) ABN 11 000 016 722 AFSL 227681 trading as Brand Cover\n") == "Brand Cover"


def test_trading_as_with_nothing_before_it_is_not_enough():
    assert vendor("trading as Brand Cover\n") is None


# ---- trust the PDF: the file name is only a fallback ---------------------------------------

def test_the_pdf_date_beats_the_filename_date():
    assert invoices.propose("", "261024 - x.pdf", "Invoice Date   05/12/2023\n", CATS)["date"] == _date(2023, 12, 5)


def test_the_filename_date_is_used_when_the_pdf_has_none():
    assert invoices.propose("", "261024 - x.pdf", "Nothing dated here\n", CATS)["date"] == _date(2026, 10, 24)
    assert invoices.propose("", "Notice_251010.pdf", "", CATS)["date"] == _date(2025, 10, 10)


def test_what_the_pdf_keeps_naming_beats_a_misleading_filename():
    text = "Fitting of new tyres\nTyres: 4 x tyres supplied\nBalance of tyres\n"
    assert invoices.propose("", "Insurance_stuff.pdf", text, CATS)["category"] == "Tyres"
    assert invoices.propose("Misc", "261024 - Insurance.pdf", text, CATS)["category"] == "Tyres"


def test_the_folder_the_owner_filed_it_in_still_beats_the_wording_of_the_text():
    text = "Fitting of new tyres\nTyres: 4 x tyres supplied\nBalance of tyres\n"
    assert invoices.propose("Insurance", "x.pdf", text, CATS)["category"] == "Insurance"


def test_a_passing_mention_does_not_override_the_filename_or_folder():
    # a registration notice mentions CTP insurance, but it says registration much more often
    text = "REGISTRATION RENEWAL\n" + "Your registration is due. " * 6 + "CTP insurance. Insurance insurer. insurance.\n"
    assert invoices.propose("", "whatever.pdf", text, CATS)["category"] == "Registration"
    assert invoices.propose("Registration", "x.pdf", "One mention of insurance only.", CATS)["category"] == "Registration"


def test_the_filename_and_folder_are_the_fallback_for_category():
    assert invoices.propose("Maintenance", "261024 - Tyres.pdf", "", CATS)["category"] == "Tyres"
    assert invoices.propose("Insurance", "x.pdf", "", CATS)["category"] == "Insurance"


def test_the_pdf_company_beats_a_filename_vendor_which_is_the_fallback():
    text = "Real Garage Pty Ltd\nTotal   $50.00\n"
    assert invoices.propose("", "261030 - Tyres - Bogus Name.pdf", text, CATS)["vendor"] == "Real Garage Pty Ltd"
    assert invoices.propose("", "261030 - Tyres - Bogus Name.pdf", "no company here\n", CATS)["vendor"] == "Bogus Name"


def test_a_category_in_the_heading_beats_what_the_body_mentions_most():
    # a registration notice with a long CTP insurer section: insurance is said more often, but the title says registration
    text = ("   VEHICLE REGISTRATION                      Due date:  1 Jan 2030\n\n   RENEWAL NOTICE\n   Department of Example Transport\n"
            + "   Insurer nomination: your insurance is provided by an insurer. Insurance applies.\n" * 8)
    assert invoices.propose("", "scan.pdf", text, CATS)["category"] == "Registration"


def test_when_the_heading_names_nothing_the_most_mentioned_wins():
    assert invoices.propose("", "Policy.pdf", SCHEDULE, CATS)["category"] == "Insurance"


def test_a_heading_naming_two_categories_falls_back_to_the_counts():
    text = "REGISTRATION and INSURANCE summary\n" + "insurance premium. " * 5 + "registration. " * 2
    assert invoices.propose("", "x.pdf", text, CATS)["category"] == "Insurance"


def test_a_filename_the_pdf_contradicts_supplies_no_vendor():
    # the name says Tyres - Nonsense; the PDF is clearly a registration notice with no company named
    got = invoices.propose("", "261224 - Tyres - Nonsense.pdf", RENEWAL, CATS)
    assert got["category"] == "Registration" and got["vendor"] is None


def test_a_filename_the_pdf_agrees_with_still_supplies_the_vendor():
    got = invoices.propose("", "261030 - Wheel Alignment - Tesla.pdf", "Wheel alignment report\nWheel alignment done\nWheel alignment\n", CATS)
    assert got["category"] == "Wheel alignment" and got["vendor"] == "Tesla"
    assert invoices.propose("", "261030 - Wheel Alignment - Tesla.pdf", "", CATS)["vendor"] == "Tesla"

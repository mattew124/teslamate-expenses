"""Our entry on TeslaMate's "Projects using TeslaMate" page must keep describing the app."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("teslamate_listing", ROOT / "scripts" / "teslamate_listing.py")
listing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(listing)

UPSTREAM = """---
title: Projects using TeslaMate
---

Intro.

## [Other one](https://example.test/a)

Other text.

LINK: [a](https://example.test/a)

## [Last one](https://example.test/b)

Last text.
"""


def test_the_entry_was_reviewed_against_the_current_feature_list():
    recorded, _ = listing.entry_parts()
    assert recorded == listing.feature_hash(), (
        "The feature list in README.md changed. Check .github/teslamate-listing.md still describes the "
        "app (storage options, dashboard, how it reads TeslaMate), edit it if not, then set its first "
        f"line to <!-- readme-features: {listing.feature_hash()} -->. Step 7 of "
        "RELEASING.md then proposes the new text to TeslaMate (scripts/teslamate-listing.sh).")


def test_a_changed_feature_changes_the_hash():
    readme = (ROOT / "README.md").read_text()
    assert listing.feature_hash(readme) != listing.feature_hash(readme.replace("OneDrive", "Local"))
    assert listing.feature_hash(readme) == listing.feature_hash(readme.replace("\n  ", "\n      "))   # indentation is not content


def test_the_entry_is_one_section_that_fits_the_page():
    _, entry = listing.entry_parts()
    assert entry.startswith("## [TeslaMate Expenses](https://github.com/mattew124/teslamate-expenses)")
    assert entry.count("\n## ") == 0 and "LINK: [github.com/mattew124/teslamate-expenses]" in entry


def test_the_entry_says_what_the_app_does_and_how_it_reads_teslamate():
    _, entry = listing.entry_parts()
    for word in ("expense", "invoice", "OneDrive", "Grafana", "directly", "MQTT"):
        assert word in entry, word
    assert (ROOT / "tmexpenses" / "onedrive.py").exists()   # the OneDrive claim is still true


def test_nothing_private_is_in_the_entry():
    text = listing.ENTRY.read_text().lower()
    assert not any(word in text for word in ("womensfit", "homelab", "21508"))


def test_merge_appends_when_the_page_has_no_entry():
    _, entry = listing.entry_parts()
    merged = listing.merge(UPSTREAM, entry)
    assert merged.startswith(UPSTREAM.rstrip("\n")) and merged.endswith(entry.strip() + "\n")
    assert merged.count("TeslaMate Expenses](https://github.com/mattew124") == 1


def test_merge_replaces_only_our_section_and_is_idempotent():
    _, entry = listing.entry_parts()
    middle = UPSTREAM.replace("## [Last one]", entry.strip() + "\n\n## [Last one]")   # our entry in the middle
    new = entry.replace("Expense tracking", "Expense tracking and local folders")
    replaced = listing.merge(middle, new)
    assert "and local folders" in replaced and replaced.count("## [TeslaMate Expenses]") == 1
    assert "Other text." in replaced and "Last text." in replaced and "## [Last one]" in replaced
    assert listing.merge(replaced, new) == replaced

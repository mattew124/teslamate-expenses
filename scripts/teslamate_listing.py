#!/usr/bin/env python3
"""Keeps our entry in TeslaMate's "Projects using TeslaMate" page (website/docs/projects.md in
teslamate-org/teslamate) in step with this app.

  teslamate_listing.py hash              the hash of the README's feature list
  teslamate_listing.py merge UPSTREAM    UPSTREAM's projects.md with our entry added or replaced

The entry itself is .github/teslamate-listing.md. Its first line records the hash of the README
feature list it was written against (`hash`), so a pull request that changes a feature fails the
tests until the entry has been reviewed. scripts/teslamate-listing.sh checks it against
TeslaMate's page and opens the pull request there.
"""
import hashlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENTRY = ROOT / ".github" / "teslamate-listing.md"
HEADING = re.compile(r"^## \[TeslaMate Expenses\]\(https://github\.com/mattew124/teslamate-expenses\)\s*$", re.M)
FEATURE = re.compile(r"^- \*\*.*?(?=^\S|\Z)", re.M | re.S)   # a bullet and its indented lines


def features(readme):
    """The README's bold-led feature bullets, whitespace-normalised."""
    return [" ".join(m.group(0).split()) for m in FEATURE.finditer(readme)]


def feature_hash(readme=None):
    readme = (ROOT / "README.md").read_text() if readme is None else readme
    return hashlib.sha256("\n".join(features(readme)).encode()).hexdigest()[:12]


def entry_parts(text=None):
    """(recorded hash or None, the entry to publish)."""
    text = ENTRY.read_text() if text is None else text
    found = re.match(r"<!-- readme-features: (\S+) -->\n", text)
    return (found.group(1) if found else None), text[found.end():] if found else text


def merge(upstream, entry):
    """upstream with our section replaced by entry, or appended if it isn't there yet."""
    entry = entry.strip() + "\n"
    start = HEADING.search(upstream)
    if not start:
        return upstream.rstrip("\n") + "\n\n" + entry
    following = re.compile(r"^## ", re.M).search(upstream, start.end())
    end = following.start() if following else len(upstream)
    return upstream[:start.start()] + entry + ("\n" + upstream[end:] if following else "")


if __name__ == "__main__":
    if sys.argv[1:] == ["hash"]:
        print(feature_hash())
    elif len(sys.argv) == 3 and sys.argv[1] == "merge":
        sys.stdout.write(merge(Path(sys.argv[2]).read_text(), entry_parts()[1]))
    else:
        sys.exit(__doc__)

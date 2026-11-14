"""The documentation site: every page reachable, and no real data in a public repo."""
import re
import tomllib

from tests.test_dev_stack import ROOT

DOCS = ROOT / "docs"
SITE = "https://mattew124.github.io/garagebooks/"
IMAGE_TYPES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}


def nav_pages():
    with (ROOT / "zensical.toml").open("rb") as f:
        nav = tomllib.load(f)["project"]["nav"]
    return {page for entry in nav for page in entry.values()}


def test_every_page_is_in_the_navigation():
    assert {p.relative_to(DOCS).as_posix() for p in DOCS.rglob("*.md")} == nav_pages()


def test_screenshots_live_in_docs_images_and_every_one_is_used():
    """Screenshots come from the demo stack's invented data (`scripts/dev.sh demo screenshots`)."""
    # docs/assets holds the project's own logo and icons, not screenshots
    images = [p for p in DOCS.rglob("*") if p.suffix.lower() in IMAGE_TYPES and DOCS / "assets" not in p.parents]
    assert all(p.parent == DOCS / "images" for p in images), images
    pages = "".join(p.read_text() for p in DOCS.rglob("*.md")) + (ROOT / "README.md").read_text()
    assert [p.name for p in images if p.name not in pages] == []


def test_a_marker_names_and_describes_its_screenshot():
    markers = [line for page in DOCS.rglob("*.md") for line in page.read_text().splitlines()
               if line.startswith("<!-- screenshot:")]
    assert markers
    for line in markers:
        assert re.fullmatch(r"<!-- screenshot: [a-z0-9-]+ - .+ -->", line), line


def test_the_readme_links_to_the_site():
    assert SITE in (ROOT / "README.md").read_text()


def test_the_app_points_at_the_site_not_the_readme():
    for name in ("garagebooks/onedrive.py", "garagebooks/templates/_storage_onedrive.html"):
        text = (ROOT / name).read_text()
        assert f"{SITE}onedrive/" in text and "README" not in text, name

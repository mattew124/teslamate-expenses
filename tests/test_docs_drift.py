"""Cheap, deterministic checks that the docs still describe the code.

They catch the mechanical drift (a setting or a dev command nobody wrote down). Whether the prose
still says the right thing is for the pull-request docs workflow and `/docs-sync`.
"""
import re

from tests.test_dev_stack import ROOT

DOCS = ROOT / "docs"

# Read by the app but set by the container, the platform or the tests, never by an installer.
INTERNAL = {"PATH_INFO", "SCRIPT_NAME", "APP_VERSION", "DATABASE_URL", "SKIP_MIGRATE", "ONEDRIVE_TOKEN_KEY"}


def docs_text():
    return "\n".join(page.read_text() for page in DOCS.rglob("*.md"))


def test_every_setting_the_app_reads_is_documented():
    code = "\n".join(p.read_text() for p in (ROOT / "garagebooks").rglob("*.py"))
    names = set(re.findall(r'environ(?:\.get)?[(\[]\s*"([A-Z][A-Z0-9_]+)"', code)) - INTERNAL
    documented = docs_text() + (ROOT / ".env.example").read_text() + (ROOT / "README.md").read_text()
    assert [n for n in sorted(names) if n not in documented] == []


def test_every_dev_command_is_documented():
    script = (ROOT / "scripts/dev.sh").read_text()
    case = script[script.rindex('case "${1'):] if 'case "${1' in script else script
    commands = set(re.findall(r"^\s{4}([a-z][a-z-]+)\)", case, re.M))
    assert commands, "dev.sh's command list moved; update this test"
    documented = (DOCS / "development.md").read_text() + (ROOT / "CLAUDE.md").read_text()
    assert [c for c in sorted(commands) if f"dev.sh {c}" not in documented] == []

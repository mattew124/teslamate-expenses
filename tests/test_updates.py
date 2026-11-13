"""The footer: a link to the docs, the installed version, and whether a newer release exists.

The check asks GitHub, so it must never slow a page down, never run in a development build, never
run when switched off, and never put anything GitHub sent into a page except a version it parsed.
"""
import json
import threading
import urllib.request

import psycopg
import pytest
from psycopg.rows import dict_row

from tests.test_dev_stack import ROOT
from tmexpenses import create_app, updates

RELEASES = "https://github.com/mattew124/teslamate-expenses/releases"


def check(installed, tags, **kw):
    kw.setdefault("background", False)
    return updates.UpdateCheck(installed, fetch=lambda: list(tags), **kw)


# ---- versions -------------------------------------------------------------------

def test_versions_compare_as_numbers_and_a_candidate_precedes_its_release():
    order = ["0.2.0-rc1", "0.2.0-rc2", "0.2.0", "0.9.0", "0.10.0", "1.0.0"]
    assert sorted(order, key=updates.parse) == order
    assert updates.parse("v0.2.0") == updates.parse("0.2.0")


@pytest.mark.parametrize("text", ["", "dev", "0.2", "latest", "v1.0.0/../x", "1.0.0 ", "1.0.0+build"])
def test_anything_else_is_not_a_version(text):
    assert updates.parse(text) is None


# ---- the verdict ----------------------------------------------------------------

def test_a_newer_release_is_offered_with_its_release_notes():
    s = check("0.3.0", ["v0.3.0", "v0.4.0", "v0.1.0"]).status()
    assert (s["state"], s["latest"]) == ("update", "0.4.0")
    assert s["notes_url"] == f"{RELEASES}/tag/v0.4.0"


def test_the_latest_release_is_up_to_date_and_links_to_its_own_notes():
    s = check("0.4.0", ["v0.4.0", "v0.3.0"]).status()
    assert (s["state"], s["notes_url"]) == ("current", f"{RELEASES}/tag/v0.4.0")


def test_newer_than_every_release_claims_nothing():
    s = check("0.5.0", ["v0.4.0"]).status()
    assert s["state"] == "ahead" and s["notes_url"] is None


def test_a_stable_install_is_not_offered_a_candidate():
    assert check("0.3.0", ["v0.4.0-rc1", "v0.3.0"]).status()["state"] == "current"


def test_a_candidate_install_follows_candidates_and_the_release_that_replaces_them():
    assert check("0.4.0-rc1", ["v0.4.0-rc2", "v0.3.0"]).status()["latest"] == "0.4.0-rc2"
    assert check("0.4.0-rc2", ["v0.4.0", "v0.4.0-rc2"]).status()["state"] == "update"


def test_tags_that_are_not_versions_are_ignored_and_never_reach_a_link():
    s = check("0.3.0", ["nightly", "v9.9.9/../../x", "<script>", "v0.3.1"]).status()
    assert s["latest"] == "0.3.1" and "<" not in str(s) and ".." not in str(s)


def test_no_releases_yet_is_unknown_not_an_error():
    s = check("0.3.0", []).status()
    assert (s["state"], s["latest"], s["notes_url"]) == ("unknown", None, None)


# ---- when it asks ---------------------------------------------------------------

def must_not_ask():
    raise AssertionError("asked GitHub")


@pytest.mark.parametrize("installed", ["dev", "", "not-a-version"])
def test_a_build_without_a_release_version_never_asks(installed):
    s = updates.UpdateCheck(installed, fetch=must_not_ask, background=False).status()
    assert s["state"] == "dev"


def test_it_can_be_switched_off():
    s = updates.UpdateCheck("0.3.0", fetch=must_not_ask, enabled=False, background=False).status()
    assert s["state"] == "off" and s["installed"] == "0.3.0"


def test_the_answer_is_kept_until_it_is_stale():
    now, calls = [0.0], []
    c = updates.UpdateCheck("0.3.0", fetch=lambda: calls.append(1) or ["v0.3.0"], background=False,
                            ttl=100, clock=lambda: now[0])
    c.status(), c.status()
    now[0] = 99
    c.status()
    assert len(calls) == 1
    now[0] = 101
    c.status()
    assert len(calls) == 2


def test_a_failure_is_not_retried_on_every_page_but_is_retried_later():
    now, calls = [0.0], []

    def down():
        calls.append(1)
        raise OSError("offline")
    c = updates.UpdateCheck("0.3.0", fetch=down, background=False, ttl=1000, retry=60, clock=lambda: now[0])
    assert c.status()["state"] == "unknown"
    c.status(), c.status()
    assert len(calls) == 1
    now[0] = 61
    c.status()
    assert len(calls) == 2


def test_a_page_never_waits_for_github():
    release, started = threading.Event(), threading.Event()

    def slow():
        started.set()
        assert release.wait(5)
        return ["v0.4.0"]
    c = updates.UpdateCheck("0.3.0", fetch=slow)          # background is the default
    assert c.status()["state"] == "unknown"               # returned while the fetch is still blocked
    assert started.wait(5)
    release.set()
    c.wait(5)
    assert c.status()["state"] == "update"


# ---- the request ----------------------------------------------------------------

class Reply:
    def __init__(self, body):
        self.body = body

    def read(self, n=-1):
        return self.body if n < 0 else self.body[:n]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_it_asks_one_fixed_address_with_a_timeout_and_names_itself(monkeypatch):
    seen = {}

    def urlopen(req, timeout=None):
        seen.update(url=req.full_url, ua=req.get_header("User-agent"), timeout=timeout, data=req.data)
        return Reply(json.dumps([{"tag_name": "v0.4.0"}, {"tag_name": "v0.5.0", "draft": True}]).encode())
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert updates.fetch_tags("0.3.0") == ["v0.4.0"]            # a draft is never a release
    assert seen["url"].startswith("https://api.github.com/repos/mattew124/teslamate-expenses/releases")
    assert seen["ua"] == "teslamate-expenses/0.3.0" and seen["data"] is None and 0 < seen["timeout"] <= 10


@pytest.mark.parametrize("body", [b"not json", b'{"message": "rate limited"}', b'[{"tag_name": 5}]',
                                  b"[" + b" " * (2 * 1024 * 1024) + b"]"])
def test_an_unexpected_reply_is_a_failure_not_a_page_error(monkeypatch, body):
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: Reply(body))
    with pytest.raises(updates.CheckFailed):
        updates.fetch_tags("0.3.0")


# ---- the footer -----------------------------------------------------------------

@pytest.fixture()
def page(pg, admin, monkeypatch):
    monkeypatch.delenv("BASE_PATH", raising=False)
    monkeypatch.delenv("UPDATE_CHECK", raising=False)

    def get(version="0.3.0", tags=("v0.4.0",), env=None):
        for k, v in (env or {}).items():
            monkeypatch.setenv(k, v)
        if version is None:
            monkeypatch.delenv("APP_VERSION", raising=False)
        else:
            monkeypatch.setenv("APP_VERSION", version)
        app = create_app(connect=lambda: psycopg.connect(pg.app_url, row_factory=dict_row),
                         release_fetch=tags if callable(tags) else (lambda: list(tags)))
        app.testing = True
        client = app.test_client()
        client.get("/")                      # the first page starts the check
        app.extensions["update_check"].wait(5)
        return client.get("/").data.decode()
    return get


def test_every_page_links_to_the_docs(page):
    html = page()
    assert 'href="https://mattew124.github.io/teslamate-expenses/"' in html


def test_the_footer_shows_the_version_and_offers_the_update_with_its_notes(page):
    html = page("0.3.0", ["v0.4.0"])
    assert "0.3.0" in html and "0.4.0" in html
    assert f'href="{RELEASES}/tag/v0.4.0"' in html and "release notes" in html.lower()


def test_the_footer_says_when_this_is_the_latest(page):
    html = page("0.4.0", ["v0.4.0"])
    assert "up to date" in html.lower() and f'href="{RELEASES}/tag/v0.4.0"' in html


def test_a_development_build_says_so_and_asks_nothing(page):
    html = page(None, must_not_ask)
    assert "development build" in html.lower() and "release notes" not in html.lower()


def test_update_check_zero_turns_it_off_but_keeps_the_version(page):
    html = page("0.3.0", must_not_ask, env={"UPDATE_CHECK": "0"})
    assert "0.3.0" in html and "release notes" not in html.lower() and "up to date" not in html.lower()


def test_a_github_failure_leaves_the_page_working(page):
    def down():
        raise updates.CheckFailed("offline")
    html = page("0.3.0", down)
    assert "0.3.0" in html and "available" not in html.lower()


# ---- the release plumbing: without it a released image would say "development build" -------

def test_the_image_is_given_its_version_by_the_release_workflow():
    docker = (ROOT / "Dockerfile").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert "ARG VERSION" in docker and "APP_VERSION=$VERSION" in docker
    assert "VERSION=" in release and "build-args" in release


def test_a_release_publishes_github_release_notes_for_the_tag():
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert "gh release create" in release and "--generate-notes" in release and "--prerelease" in release
    assert "contents: write" in release


def test_the_setting_is_documented():
    for name in ("docs/reference.md", ".env.example"):
        assert "UPDATE_CHECK" in (ROOT / name).read_text(), name


# ---- release notes: the changelog entry first, then the pull requests in groups ----------------

def test_release_notes_group_pull_requests_by_label():
    config = (ROOT / ".github/release.yml").read_text()
    for label in ("enhancement", "bug", "documentation", "dependencies", "internal"):
        assert label in config, label
    assert "changelog:" in config and "categories:" in config
    assert '"*"' in config  # whatever is unlabelled still appears, under "Other changes"


def test_the_changelog_entry_for_a_tag_opens_its_release_notes():
    release = (ROOT / ".github/workflows/release.yml").read_text()
    assert "scripts/changelog-entry.sh" in release and "--notes-file" in release
    assert "--generate-notes" in release


def test_changelog_entry_prints_one_section_without_its_heading():
    import subprocess
    out = subprocess.run([str(ROOT / "scripts/changelog-entry.sh"), "v0.2.0-rc5"], cwd=ROOT,
                         check=True, capture_output=True, text=True).stdout
    assert out.startswith("The fifth candidate for 0.2.0")
    assert "## 0.2.0-rc5" not in out and "0.2.0-rc4 - " not in out


def test_changelog_entry_is_empty_for_a_tag_without_one():
    import subprocess
    out = subprocess.run([str(ROOT / "scripts/changelog-entry.sh"), "v9.9.9"], cwd=ROOT,
                         check=True, capture_output=True, text=True).stdout
    assert out == ""


def test_the_latest_changelog_entry_opens_with_what_it_means_for_the_reader():
    text = (ROOT / "CHANGELOG.md").read_text()
    first = text.split("\n## ")[1].split("\n", 1)[1].lstrip()
    assert not first.startswith("###"), "start the entry with a paragraph saying why it matters"

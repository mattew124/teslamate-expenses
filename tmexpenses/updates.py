"""Which version is running, and is there a newer release?

The image is built with its version (the release tag) in APP_VERSION. Released images ask GitHub
which releases exist: one anonymous GET of the public releases list, at most every few hours, from a
background thread so a page never waits for it. Nothing is sent but the request itself (GitHub sees
the server's address and a User-Agent naming this app and its version). A build with no version (a
local build, the dev stack, the tests) never asks, and UPDATE_CHECK=0 switches it off.

Only a version this module has parsed from a tag ever reaches a page or a link. Whatever else GitHub
sends is discarded.
"""

import json
import logging
import re
import threading
import time
import urllib.error
import urllib.request

REPO = "mattew124/teslamate-expenses"
DOCS_URL = "https://mattew124.github.io/teslamate-expenses/"
RELEASES_URL = f"https://github.com/{REPO}/releases"
API_URL = f"https://api.github.com/repos/{REPO}/releases?per_page=20"

TTL_SECONDS = 6 * 3600       # how long an answer is kept
RETRY_SECONDS = 15 * 60      # how long before asking again after a failure (offline installs are normal)
TIMEOUT_SECONDS = 5
MAX_BYTES = 1024 * 1024      # a releases page is a few kB; anything bigger is not what we asked for

log = logging.getLogger("tmexpenses.updates")

_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z]+(?:\.[0-9A-Za-z]+)*))?")


class CheckFailed(Exception):
    """GitHub could not be asked, or answered with something other than a list of releases."""


def parse(text):
    """'v0.3.0' or '0.3.0-rc1' -> a sortable key, or None when it is not a release version.

    A candidate sorts before the release it leads to (0.3.0-rc1 < 0.3.0), and rc2 after rc1.
    """
    m = _VERSION.fullmatch(text or "")
    if not m:
        return None
    major, minor, patch, pre = m.groups()
    # rc2 < rc10: split each identifier into its numbers and letters, and compare numbers as numbers.
    parts = tuple(tuple((0, int(c)) if c.isdigit() else (1, c) for c in re.findall(r"\d+|\D+", ident))
                  for ident in pre.split(".")) if pre else ()
    return (int(major), int(minor), int(patch), pre is None, parts)


def _is_candidate(key):
    return not key[3]


def fetch_tags(installed):
    """The tag names of GitHub's published releases (drafts excluded). Raises CheckFailed."""
    req = urllib.request.Request(API_URL, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"teslamate-expenses/{installed}",
    })
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:  # nosec B310 - fixed https address
            body = resp.read(MAX_BYTES)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise CheckFailed(f"couldn't reach GitHub: {exc}") from None
    try:
        releases = json.loads(body)
        if not isinstance(releases, list):
            raise ValueError("not a list")
        for release in releases:    # exactly a list of release objects, or it isn't what we asked for
            if not isinstance(release, dict) or not isinstance(release.get("tag_name"), str):
                raise ValueError("not a release")
        return [r["tag_name"] for r in releases if not r.get("draft")]
    except ValueError:
        raise CheckFailed("unexpected reply from GitHub") from None


class UpdateCheck:
    """`status()` is cheap and never blocks: it reports what is known and starts a refresh when stale."""

    def __init__(self, installed, fetch=None, enabled=True, background=True,
                 ttl=TTL_SECONDS, retry=RETRY_SECONDS, clock=time.monotonic):
        self.installed = installed or ""
        self._key = parse(self.installed)
        self._enabled = enabled
        self._fetch = fetch or (lambda: fetch_tags(self.installed))
        self._background, self._ttl, self._retry, self._clock = background, ttl, retry, clock
        self._lock = threading.Lock()
        self._thread = None
        self._tags = None                    # None until an answer arrives
        self._valid_until = float("-inf")    # when the answer (or the failure) is stale

    @property
    def active(self):
        """Whether it will ever ask GitHub: a release version, and not switched off."""
        return self._key is not None and self._enabled

    def status(self):
        if self._key is None:
            return self._result("dev")
        if not self._enabled:
            return self._result("off")
        self._refresh_if_stale()
        with self._lock:
            tags = self._tags
        return self._verdict(tags) if tags is not None else self._result("unknown")

    def wait(self, timeout=None):
        """Block until a refresh in progress finishes (for tests; pages never call this)."""
        thread = self._thread
        if thread:
            thread.join(timeout)

    # ---- internals --------------------------------------------------------------

    def _refresh_if_stale(self):
        with self._lock:
            if self._clock() < self._valid_until or (self._thread and self._thread.is_alive()):
                return
            if self._background:
                self._thread = threading.Thread(target=self._refresh, name="update-check", daemon=True)
                self._thread.start()
                return
        self._refresh()

    def _refresh(self):
        try:
            tags = self._fetch()
            ok = True
        except Exception as exc:    # noqa: BLE001 - a failed check must never reach a page
            log.info("update check failed: %s", exc)
            tags, ok = None, False
        with self._lock:
            if ok:
                self._tags = list(tags)
                self._valid_until = self._clock() + self._ttl
            else:
                self._valid_until = self._clock() + self._retry
        if ok:
            log.info("update check: installed=%s releases=%d", self.installed, len(self._tags))

    def _verdict(self, tags):
        # A candidate install follows candidates too; a stable install is only offered stable releases.
        keys = {t.removeprefix("v"): k for t in tags if (k := parse(t)) is not None
                and (_is_candidate(self._key) or not _is_candidate(k))}
        if not keys:
            return self._result("unknown")
        latest = max(keys, key=keys.get)
        if keys[latest] > self._key:
            return self._result("update", latest, notes_for=latest)
        if keys[latest] == self._key:
            return self._result("current", latest, notes_for=latest)
        return self._result("ahead", latest)

    def _result(self, state, latest=None, notes_for=None):
        return {"state": state, "installed": self.installed or "dev", "latest": latest,
                "notes_url": f"{RELEASES_URL}/tag/v{notes_for}" if notes_for else None}

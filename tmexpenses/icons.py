"""The storage provider's icon, fetched from its public source instead of being shipped in this repository.

Logos belong to their owners, so the app asks for the picture when it is first needed: one anonymous
GET of a pinned release of the Simple Icons set (CC0 drawings) on jsDelivr, from the server, never from
the browser. The server keeps it in memory (the container has no writable disk) and hands it to
browsers, which cache it too. Nothing is sent but the request itself (the server's address and a
User-Agent naming this app).

What comes back is checked before it is used: it must be a plain drawing (svg, title and path elements
with a short list of attributes) and small. Anything else, or any failure to ask, means the bundled
drawing is used and the question is asked again later (offline installs are normal).
PROVIDER_ICONS=0 switches the request off.
"""

import logging
import re
import threading
import time
import urllib.error
import urllib.request

log = logging.getLogger("tmexpenses.icons")

# A pinned release, so the picture never changes underneath an installation. 12.4.0 is the last Simple Icons
# release that has this drawing: later ones dropped it, so do not move the pin without checking the file exists.
SOURCES = {
    "onedrive": {
        "url": "https://cdn.jsdelivr.net/npm/simple-icons@12.4.0/icons/microsoftonedrive.svg",
        "colour": "#0078D4",
    },
}

TTL_SECONDS = 24 * 3600     # how long a fetched icon is kept
RETRY_SECONDS = 15 * 60     # before asking again after a failure
TIMEOUT_SECONDS = 5
MAX_BYTES = 16 * 1024       # a one-glyph drawing is about 1 kB

SVG_NS = "http://www.w3.org/2000/svg"
ALLOWED = {                  # element -> the attributes it may have
    "svg": {"role", "viewBox", "xmlns", "width", "height"},
    "title": set(),
    "path": {"d", "fill-rule", "clip-rule"},
}
_TAG = re.compile(r"<(/?)([A-Za-z][\w-]*)((?:\s+[\w:-]+=\"[^\"]*\")*)\s*(/?)>")
_ATTR = re.compile(r"([\w:-]+)=\"([^\"]*)\"")
_TEXT = re.compile(r"[A-Za-z0-9 .,()-]*")      # a <title>'s words


class IconUnavailable(Exception):
    """The source could not be asked, or sent something that is not a plain drawing."""


def fetch_url(url):
    if not url.startswith("https://"):
        raise IconUnavailable("not an https address")
    req = urllib.request.Request(url, headers={"User-Agent": "teslamate-expenses (icon)"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:    # nosec B310 - https only, checked above
            return resp.read(MAX_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise IconUnavailable(str(exc)) from None


def clean(body, colour):
    """Raw bytes from the source -> the SVG to serve, filled with `colour`. Raises IconUnavailable
    unless it is a small, plain drawing."""
    if not body or len(body) > MAX_BYTES:
        raise IconUnavailable("empty or too big")
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise IconUnavailable("not text") from None
    stack, pos, root = [], 0, None
    for m in _TAG.finditer(text):
        between = text[pos:m.start()]
        if between.strip() and not (stack and stack[-1] == "title" and _TEXT.fullmatch(between)):
            raise IconUnavailable("unexpected content")
        pos = m.end()
        closing, name, attrs, selfclose = m.groups()
        if name not in ALLOWED:
            raise IconUnavailable(f"element <{name}> is not allowed")
        if closing:
            if not stack or stack.pop() != name:
                raise IconUnavailable("unbalanced")
            continue
        found = _ATTR.findall(attrs)
        if any(k not in ALLOWED[name] for k, _ in found):
            raise IconUnavailable(f"an attribute of <{name}> is not allowed")
        if any(re.search(r"[&<>]|javascript|url\(", v, re.I) for _, v in found):
            raise IconUnavailable("an attribute value is not allowed")
        if name == "svg":
            if root or dict(found).get("xmlns") != SVG_NS:
                raise IconUnavailable("not an svg")
            root = m
        elif not stack or stack[0] != "svg":
            raise IconUnavailable("outside the svg")
        if not selfclose:
            stack.append(name)
    if text[pos:].strip() or stack or root is None or root.start() != 0:
        raise IconUnavailable("not a single svg")
    return (text[:root.start()] + f'<svg fill="{colour}"' + text[root.start() + 4:]).encode()


class IconCache:
    def __init__(self, fetch=None, enabled=True, clock=time.monotonic):
        self.fetch = fetch or fetch_url
        self.enabled = enabled
        self.clock = clock
        self.lock = threading.Lock()
        self.kept = {}          # name -> (svg bytes or None, when to ask again)

    def get(self, name):
        """The provider's icon as SVG bytes, or None (use the bundled one)."""
        source = SOURCES.get(name)
        if not source or not self.enabled:
            return None
        with self.lock:
            svg, until = self.kept.get(name, (None, 0))
            if self.clock() < until and name in self.kept:
                return svg
            try:
                svg = clean(self.fetch(source["url"]), source["colour"])
                self.kept[name] = (svg, self.clock() + TTL_SECONDS)
                log.info("provider icon %s fetched (%d bytes)", name, len(svg))
            except IconUnavailable as exc:
                svg = None
                self.kept[name] = (None, self.clock() + RETRY_SECONDS)
                log.warning("provider icon %s unavailable, using the bundled drawing: %s", name, exc)
            return svg

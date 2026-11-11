"""Other products' icons (the storage provider's, Grafana's), fetched from their public source instead of
being shipped in this repository.

Logos belong to their owners, so the app asks for the picture when it is first needed: one anonymous
GET of a pinned commit of the selfh.st/icons collection (CC BY 4.0) on jsDelivr, from the server, never
from the browser. The server keeps it in memory (the container has no writable disk) and hands it to
browsers, which cache it too. Nothing is sent but the request itself (the server's address and a
User-Agent naming this app).

What comes back is checked before it is used: it must be a plain drawing (svg, title, g, path and
gradient elements, each attribute on a short list and matching its expected shape) and small. The SVG
that is served is rebuilt from the parts that passed. Anything else, or any failure to ask, means the bundled
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

# Pinned to an exact commit, so the picture never changes underneath an installation. `colour` fills a
# single-colour drawing; a logo that carries its own colours has None. selfh.st/icons is CC BY 4.0: the
# credit is in the docs and on the OneDrive page (see CREDIT).
SOURCES = {
    # `storage`: the storage provider's logo, served only when uploads are set up.
    "onedrive": {
        "url": "https://cdn.jsdelivr.net/gh/selfhst/icons@7ffe1c720c0ff64e2769197fa52631b9f980c135"
               "/svg/microsoft-onedrive.svg",
        "colour": None,
        "storage": True,
    },
    # Beside the header's Dashboards link.
    "grafana": {
        "url": "https://cdn.jsdelivr.net/gh/selfhst/icons@7ffe1c720c0ff64e2769197fa52631b9f980c135"
               "/svg/grafana.svg",
        "colour": None,
        "storage": False,
    },
}
CREDIT = {"text": "selfh.st/icons", "url": "https://selfh.st/icons", "licence": "CC BY 4.0",
          "licence_url": "https://creativecommons.org/licenses/by/4.0/"}

TTL_SECONDS = 24 * 3600     # how long a fetched icon is kept
RETRY_SECONDS = 15 * 60     # before asking again after a failure
TIMEOUT_SECONDS = 5
MAX_BYTES = 16 * 1024       # a one-glyph drawing is about 1 kB

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"
_ID = r"[A-Za-z][\w-]*"
_NUM = r"-?(?:\d+\.?\d*|\.\d+)(?:[eE]-?\d+)?"
_COLOUR = r"#[0-9A-Fa-f]{3,8}"
_PAINT = rf"{_COLOUR}|url\(#{_ID}\)"
_LENGTH = rf"{_NUM}(?:px)?"
_TRANSFORM = r"(?:(?:matrix|rotate|scale|translate)\([\d.\s,eE+-]+\)\s*)+"
_GRADIENT = {"id": _ID, "gradientTransform": _TRANSFORM, "gradientUnits": r"userSpaceOnUse|objectBoundingBox"}
# element -> {attribute: the only shape its value may have}. A `style` attribute is not copied: the
# few declarations allowed below are turned into the plain attributes they stand for, because the
# app's content security policy has no inline styles.
ALLOWED = {
    "svg": {"role": r"img", "viewBox": rf"(?:{_NUM}[\s,]*){{4}}", "xmlns": re.escape(SVG_NS),
            "xmlns:xlink": re.escape(XLINK_NS), "width": _LENGTH, "height": _LENGTH, "version": r"[\d.]+",
            "preserveAspectRatio": r"[A-Za-z\s]+", "xml:space": r"preserve|default"},
    "g": {},
    "title": {},
    "linearGradient": {**_GRADIENT, **{k: _NUM for k in ("x1", "x2", "y1", "y2")}},
    "radialGradient": {**_GRADIENT, **{k: _NUM for k in ("cx", "cy", "r", "fx", "fy")}},
    "stop": {"offset": rf"{_NUM}%?", "stop-color": _COLOUR, "stop-opacity": _NUM},
    "path": {"d": r"[0-9A-Za-z\s.,+-]*", "fill": _PAINT, "fill-opacity": _NUM, "fill-rule": r"evenodd|nonzero",
             "clip-rule": r"evenodd|nonzero"},
}
_STYLE_PROPERTIES = {"fill", "fill-opacity", "stop-color", "stop-opacity"}
_PROLOG = re.compile(r'<\?xml version="[\d.]+"(?: encoding="[\w-]+")?(?: standalone="\w+")?\s*\?>\s*')
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


def _declarations(name, style):
    """`fill:url(#a);fill-opacity:.4` -> [("fill", "url(#a)"), ...], each one allowed for the element."""
    out = []
    for part in filter(None, (p.strip() for p in style.split(";"))):
        prop, _, value = part.partition(":")
        prop, value = prop.strip(), value.strip()
        if prop not in _STYLE_PROPERTIES or prop not in ALLOWED[name] or not re.fullmatch(ALLOWED[name][prop], value):
            raise IconUnavailable(f"a style on <{name}> is not allowed")
        out.append((prop, value))
    return out


def clean(body, colour=None):
    """Raw bytes from the source -> the SVG to serve, rebuilt from what was checked (with `fill` set to
    `colour` on a single-colour drawing). Raises IconUnavailable unless it is a small, plain drawing."""
    if not body or len(body) > MAX_BYTES:
        raise IconUnavailable("empty or too big")
    try:
        text = body.decode("utf-8").strip()
    except UnicodeDecodeError:
        raise IconUnavailable("not text") from None
    text = _PROLOG.sub("", text, count=1) if text.startswith("<?xml") else text
    out, stack, ids, used, pos, root = [], [], set(), set(), 0, None
    for m in _TAG.finditer(text):
        between = text[pos:m.start()]
        in_title = bool(stack) and stack[-1] == "title"
        if between.strip() and not (in_title and _TEXT.fullmatch(between)):
            raise IconUnavailable("unexpected content")
        out.append(between.strip() if in_title else "")
        pos = m.end()
        closing, name, attrs, selfclose = m.groups()
        if name not in ALLOWED:
            raise IconUnavailable(f"element <{name}> is not allowed")
        if closing:
            if not stack or stack.pop() != name:
                raise IconUnavailable("unbalanced")
            out.append(f"</{name}>")
            continue
        pairs = []
        for key, value in _ATTR.findall(attrs):
            if key == "style" and name in ("path", "stop"):
                pairs += _declarations(name, value)
            elif key in ALLOWED[name] and re.fullmatch(ALLOWED[name][key], value):
                pairs.append((key, value))
            else:
                raise IconUnavailable(f"an attribute of <{name}> is not allowed")
        keys = [k for k, _ in pairs]
        if len(keys) != len(set(keys)):
            raise IconUnavailable(f"an attribute of <{name}> is given twice")
        values = dict(pairs)
        if "id" in values:
            if values["id"] in ids:
                raise IconUnavailable("an id is used twice")
            ids.add(values["id"])
        used.update(re.findall(r"url\(#([^)]+)\)", " ".join(values.values())))
        if name == "svg":
            if root is not None or values.get("xmlns") != SVG_NS:
                raise IconUnavailable("not an svg")
            root = m
            if colour:
                pairs.insert(0, ("fill", colour))
        elif not stack or stack[0] != "svg":
            raise IconUnavailable("outside the svg")
        elif name == "stop" and stack[-1] not in ("linearGradient", "radialGradient"):
            raise IconUnavailable("a stop outside a gradient")
        out.append(f"<{name}" + "".join(f' {k}="{v}"' for k, v in pairs) + ("/>" if selfclose else ">"))
        if not selfclose:
            stack.append(name)
    if text[pos:].strip() or stack or root is None or root.start() != 0:
        raise IconUnavailable("not a single svg")
    if used - ids:
        raise IconUnavailable("a reference to something not in the drawing")
    return "".join(out).encode()


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

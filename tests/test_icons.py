"""The storage provider's icon: fetched from its public source, checked, kept in memory, with the
bundled drawing as the fallback. Nothing here touches the network: the fetch is a fake."""

import re

import pytest

from tmexpenses import icons

GOOD = b'<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Microsoft OneDrive</title><path d="M1 1h2v2z"/></svg>'


# Shaped like a real multi-colour logo file (prolog, group, filled paths, xlink namespace), but not one.
COLOURED = b'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="256px" height="165px" viewBox="0 0 256 165" version="1.1" xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" preserveAspectRatio="xMidYMid">
    <title>Example</title>
    <g>
        <path d="M0,0 L10.5,20 Z" fill="#0364B8"></path>
        <path d="M1,1 L2,2 Z" fill="#28A8EA"></path>
    </g>
</svg>
'''


# Shaped like the current multi-colour logo (gradients, styles, ids), but not it.
GRADIENT = b'''<svg xmlns="http://www.w3.org/2000/svg" xml:space="preserve" viewBox="0 0 512 512"><radialGradient id="a" cx="-645.6" cy="809.09" r="11.852" gradientTransform="matrix(8.7 10.4 17.3 -14.4 -8386 18614)" gradientUnits="userSpaceOnUse"><stop offset="0" style="stop-color:#4894fe"/><stop offset=".695" style="stop-color:#0934b3;stop-opacity:.5"/></radialGradient><linearGradient id="b" x1="326.9" x2="326.9" y1="88.0" y2="320.1" gradientUnits="userSpaceOnUse"><stop offset="0" style="stop-color:#fff"/><stop offset="1" style="stop-color:#fff;stop-opacity:0"/></linearGradient><path d="M141.5 137.9C63.2 137.9 4.7 202 .3 273.4z" style="fill:url(#a)"/><path d="M1 1h2v2z" style="fill:url(#b);fill-opacity:.4"/></svg>'''


class Fetch:
    def __init__(self, body=GOOD, error=None):
        self.body, self.error, self.urls = body, error, []

    def __call__(self, url):
        self.urls.append(url)
        if self.error:
            raise self.error
        return self.body


def cache(fetch, enabled=True, clock=None):
    return icons.IconCache(fetch=fetch, enabled=enabled, clock=clock or (lambda: 0))


def test_the_source_is_pinned_to_an_exact_commit_over_https():
    url = icons.SOURCES["onedrive"]["url"]
    assert re.fullmatch(r"https://cdn\.jsdelivr\.net/gh/selfhst/icons@[0-9a-f]{40}/svg/microsoft-onedrive\.svg", url)


def test_a_fetched_icon_is_kept():
    fetch = Fetch()
    c = cache(fetch)
    svg = c.get("onedrive")
    assert svg.startswith(b"<svg") and b"<path" in svg and b"fill=" not in svg.split(b">")[0]
    assert c.get("onedrive") == svg and len(fetch.urls) == 1        # asked once


def test_gradients_are_accepted_and_styles_become_plain_attributes():
    svg = cache(Fetch(GRADIENT)).get("onedrive")
    assert svg and b"<radialGradient" in svg and b"<linearGradient" in svg and b"<stop" in svg
    assert b"style=" not in svg                                      # the CSP has no inline styles
    assert b'stop-color="#4894fe"' in svg and b'stop-opacity=".5"' in svg
    assert b'fill="url(#a)"' in svg and b'fill-opacity=".4"' in svg


def test_a_single_colour_drawing_can_be_filled_in():
    assert icons.clean(GOOD, "#0078D4").startswith(b'<svg fill="#0078D4" role="img"')


def test_a_multi_colour_logo_file_is_accepted_as_it_is():
    svg = cache(Fetch(COLOURED)).get("onedrive")
    assert svg and b'fill="#0364B8"' in svg and b"<g>" in svg and not svg.startswith(b"<?xml")


@pytest.mark.parametrize("body", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><path d="M0 0"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><p>hi</p></foreignObject></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x.png"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(https://example.com)</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="https://example.com"><path d="M0 0"/></a></svg>',
    b"<html><body>not an icon</body></html>",
    COLOURED.replace(b'fill="#0364B8"', b'fill="url(#missing)"'),
    GRADIENT.replace(b"fill:url(#a)", b"fill:url(https://example.com/x.svg#a)"),
    GRADIENT.replace(b"fill:url(#a)", b"background:url(#a)"),
    GRADIENT.replace(b"fill:url(#a)", b"fill:url(#a);behavior:url(x.htc)"),
    GRADIENT.replace(b'id="b"', b'id="a"'),                               # an id used twice
    GRADIENT.replace(b'<path d="M1 1h2v2z" style="fill:url(#b);fill-opacity:.4"/>', b'<stop offset="1"/>'),   # a stop outside a gradient
    GRADIENT.replace(b'gradientTransform="matrix(8.7 10.4 17.3 -14.4 -8386 18614)"', b'gradientTransform="javascript:x"'),
    GRADIENT.replace(b'style="stop-color:#4894fe"', b'style="stop-color:#4894fe" fill="red" style="x"'),
    COLOURED.replace(b'fill="#0364B8"', b'fill="javascript:alert(1)"'),
    COLOURED.replace(b'fill="#0364B8"', b'fill="red" onclick="x()"'),
    COLOURED.replace(b'<svg ', b'<!DOCTYPE svg SYSTEM "x.dtd"><svg ', 1),
    COLOURED.replace(b"<g>", b"<g><use href='#x'/>", 1),
    COLOURED.replace(b'xmlns:xlink="http://www.w3.org/1999/xlink"', b'xmlns:xlink="https://example.com/"', 1),
    b"",
    b"<svg" + b" " * 40000 + b"></svg>",
])
def test_anything_but_a_plain_drawing_is_refused(body):
    assert cache(Fetch(body)).get("onedrive") is None


def test_a_failed_fetch_is_not_retried_at_once():
    now = [0]
    fetch = Fetch(error=icons.IconUnavailable("offline"))
    c = cache(fetch, clock=lambda: now[0])
    assert c.get("onedrive") is None and c.get("onedrive") is None
    assert len(fetch.urls) == 1
    now[0] = icons.RETRY_SECONDS + 1
    c.get("onedrive")
    assert len(fetch.urls) == 2


def test_a_kept_icon_is_refreshed_after_its_time():
    now = [0]
    fetch = Fetch()
    c = cache(fetch, clock=lambda: now[0])
    c.get("onedrive")
    now[0] = icons.TTL_SECONDS + 1
    c.get("onedrive")
    assert len(fetch.urls) == 2


def test_switched_off_never_asks():
    fetch = Fetch()
    assert cache(fetch, enabled=False).get("onedrive") is None and fetch.urls == []


def test_an_unknown_provider_has_no_icon():
    assert cache(Fetch()).get("dropbox") is None

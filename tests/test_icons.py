"""The storage provider's icon: fetched from its public source, checked, kept in memory, with the
bundled drawing as the fallback. Nothing here touches the network: the fetch is a fake."""

import pytest

from tmexpenses import icons

GOOD = b'<svg role="img" viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><title>Microsoft OneDrive</title><path d="M1 1h2v2z"/></svg>'


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


def test_the_source_is_a_pinned_https_address():
    src = icons.SOURCES["onedrive"]
    assert src["url"].startswith("https://cdn.jsdelivr.net/npm/simple-icons@") and "@latest" not in src["url"]
    assert src["url"].endswith("/icons/microsoftonedrive.svg")


def test_a_fetched_icon_is_coloured_and_kept():
    fetch = Fetch()
    c = cache(fetch)
    svg = c.get("onedrive")
    assert svg.startswith(b"<svg") and b'fill="#0078D4"' in svg and b"<path" in svg
    assert c.get("onedrive") == svg and len(fetch.urls) == 1        # asked once


@pytest.mark.parametrize("body", [
    b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"><path d="M0 0"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><foreignObject><p>hi</p></foreignObject></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><image href="https://example.com/x.png"/></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><style>@import url(https://example.com)</style></svg>',
    b'<svg xmlns="http://www.w3.org/2000/svg"><a href="https://example.com"><path d="M0 0"/></a></svg>',
    b"<html><body>not an icon</body></html>",
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

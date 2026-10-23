"""Third-party network policy shared by the Playwright browser tests.

The browser suites serve the site from a local HTTP server and must not depend on whether
the machine running them can reach third-party hosts. Until 2026-10-23 they did: in the
development sandbox every outside host is refused at once, while on GitHub's runners the
pages really fetched the DSS2 thumbnails from CDS HiPS2FITS, the live providers and the
relay, so a slow or changed third party could fail (or hang) the deploy's browser step
with no change to the site or the data.

``isolate_browser(browser, base_url)`` makes every page and context the browser opens
abort requests to any origin other than ``base_url``'s (``data:``, ``blob:`` and
``about:`` URLs are allowed). It is installed as a context route, and Playwright gives
page routes precedence over context routes, so a test that exercises an external fetch
keeps doing so by routing that host itself with a stubbed response (``page.route(...,
route.fulfill(...))``), exactly as before. Blocked requests fail with
``net::ERR_BLOCKED_BY_CLIENT`` (``BLOCKED``), which the suites' console-error filters treat
as a third-party load failure, never as a first-party error.
"""
from __future__ import annotations

from urllib.parse import urlsplit

BLOCKED = "ERR_BLOCKED_BY_CLIENT"
# Load failures of third-party resources, as a console message names them: BLOCKED here, and
# the development sandbox's proxy refusal (kept so a run there reads the same).
THIRD_PARTY_LOAD_FAILURES = (BLOCKED, "ERR_TUNNEL_CONNECTION_FAILED")
LOCAL_SCHEMES = ("data:", "blob:", "about:")


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}".lower()


def third_party(base_url: str):
    """A predicate for Playwright's route matcher: True for a URL outside the test server."""
    own = origin_of(base_url)

    def is_third_party(url: str) -> bool:
        return not (url.startswith(LOCAL_SCHEMES) or origin_of(url) == own)

    return is_third_party


def is_third_party_load_failure(text: str) -> bool:
    return any(code in (text or "") for code in THIRD_PARTY_LOAD_FAILURES)


def _block(route) -> None:
    route.abort("blockedbyclient")


def isolate_browser(browser, base_url: str):
    """Abort third-party requests in every context ``browser`` opens from now on (see above)."""
    predicate = third_party(base_url)
    new_context, new_page = browser.new_context, browser.new_page

    def guarded_context(*args, **kwargs):
        context = new_context(*args, **kwargs)
        context.route(predicate, _block)
        return context

    def guarded_page(*args, **kwargs):
        page = new_page(*args, **kwargs)
        page.context.route(predicate, _block)
        return page

    browser.new_context = guarded_context
    browser.new_page = guarded_page
    return browser

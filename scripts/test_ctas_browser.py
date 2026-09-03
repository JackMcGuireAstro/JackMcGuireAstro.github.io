#!/usr/bin/env python3
"""Real-browser checks for the published CTAS page.

These load the generated static release exactly as a reader would: over HTTP,
in Chromium, with JavaScript running. They assert the properties that only a
browser can prove — that the first screen does not download the complete
catalog, that the celestial sphere is painted without any interaction, that a
dossier opens with its source matrix rebuilt from the shared patterns, that the
complete catalog arrives only on request, and that no tested viewport scrolls
sideways.

Skipped, not failed, when Playwright or a generated release is unavailable, so
a publisher without a browser stack still reports honestly instead of blocking.

    python3 scripts/test_ctas_browser.py
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import socket
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "ctas" / "data"
VIEWPORTS = ((320, 568), (390, 844), (768, 1024), (1280, 720), (1440, 900))
# Requests to hosts the page does not own must never decide whether the page
# works; a blocked analytics beacon is not a CTAS defect.
THIRD_PARTY_HOSTS = ("googletagmanager.com", "google-analytics.com")


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):  # noqa: D102 - silence the test server
        return


@unittest.skipUnless((DATA / "live-summary.json").exists(),
                     "no generated release in ctas/data; run the exporter first")
class BrowserTests(unittest.TestCase):
    server = None
    thread = None
    browser = None
    playwright = None

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover - environment owns this
            raise unittest.SkipTest(f"playwright is not installed: {exc}") from exc
        port = _free_port()
        handler = functools.partial(_QuietHandler, directory=str(ROOT))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{port}/ctas.html"
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover - environment owns this
            cls.playwright.stop()
            cls.server.shutdown()
            raise unittest.SkipTest(f"no Chromium available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        if cls.browser:
            cls.browser.close()
        if cls.playwright:
            cls.playwright.stop()
        if cls.server:
            cls.server.shutdown()
            cls.server.server_close()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.console = []
        self.requests = []
        self.page.on("console", lambda m: self.console.append((m.type, m.text)))
        self.page.on("pageerror", lambda e: self.console.append(("pageerror", str(e))))
        self.page.on("response", self._record)
        self.page.goto(self.url, wait_until="networkidle", timeout=60000)
        self.page.wait_for_timeout(1200)

    def tearDown(self):
        self.page.close()

    def _record(self, response):
        if "/ctas/data/" in response.url:
            self.requests.append(response.url.split("/ctas/data/")[-1].split("?")[0])

    def test_page_runs_without_first_party_script_errors(self):
        errors = [
            entry for entry in self.console
            if entry[0] in {"error", "pageerror"}
            and not any(host in entry[1] for host in THIRD_PARTY_HOSTS)
            and "ERR_TUNNEL_CONNECTION_FAILED" not in entry[1]
        ]
        self.assertEqual(errors, [])

    def test_first_screen_does_not_download_the_complete_catalog(self):
        self.assertIn("live-summary.json", self.requests)
        forbidden = [
            name for name in self.requests
            if name.startswith("catalog-index") or name.startswith("catalog-pages/")
            or name.startswith("candidate-chunks/manifest")
        ]
        self.assertEqual(forbidden, [], "the first screen must not fetch complete-catalog artifacts")

    def test_celestial_sphere_is_painted_without_any_interaction(self):
        canvas = self.page.locator("#ctas-sky-canvas")
        self.assertTrue(canvas.count() and canvas.first.is_visible())
        painted = self.page.evaluate(
            "() => {const c=document.getElementById('ctas-sky-canvas');"
            "const g=c.getContext('2d');const d=g.getImageData(0,0,c.width,c.height).data;"
            "let n=0;for(let i=3;i<d.length;i+=4){if(d[i]>0)n++;}return n;}"
        )
        self.assertGreater(painted, 1000, "the sphere must be drawn before anything is clicked")

    def test_default_catalog_shows_records_immediately(self):
        self.assertGreater(self.page.locator("#ctas-results table tbody tr").count(), 0)

    def test_dossier_opens_with_its_source_matrix_rebuilt(self):
        self.page.locator("#ctas-results [data-open-event]").first.click()
        # The matrix lives behind a disclosure, so wait for it to exist rather
        # than to be visible: progressive disclosure is the intended design.
        self.page.wait_for_selector(
            "#candidate-workspace .ctas-source-matrix", state="attached", timeout=45000
        )
        rows = self.page.locator("#candidate-workspace .ctas-source-matrix tbody tr").count()
        self.assertGreater(rows, 10, "the shared no-evidence pattern must expand in the browser")
        self.assertIn("source-matrix-patterns.json", self.requests)

    def test_complete_catalog_arrives_only_on_request(self):
        self.page.locator("#ctas-load-complete").click()
        self.page.wait_for_function(
            "() => /Complete catalog loaded/.test("
            "document.getElementById('ctas-complete-status').textContent)",
            timeout=180000,
        )
        pages = [name for name in self.requests if name.startswith("catalog-pages/")]
        self.assertGreater(len(pages), 1)
        status = self.page.locator("#ctas-complete-status").inner_text()
        self.assertRegex(status, r"Complete catalog loaded: [\d,]+ retained records")

    def test_no_viewport_scrolls_sideways(self):
        for width, height in VIEWPORTS:
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.wait_for_timeout(350)
                overflow = self.page.evaluate(
                    "() => document.documentElement.scrollWidth"
                    " - document.documentElement.clientWidth"
                )
                self.assertLessEqual(overflow, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)

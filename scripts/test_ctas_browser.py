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

    def test_the_ctas_bar_is_not_covered_by_the_site_header(self):
        """Both are sticky. They must stack, not occupy the same band."""
        for width, height in ((1280, 900), (390, 844)):
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.evaluate("() => window.scrollTo(0, 1400)")
                self.page.wait_for_timeout(400)
                geometry = self.page.evaluate(
                    "() => {const g = document.querySelector('.site-header');"
                    "const c = document.querySelector('.ctas-navigation');"
                    "if (!g || !c) return null;"
                    "const gr = g.getBoundingClientRect(), cr = c.getBoundingClientRect();"
                    "const mid = document.elementFromPoint(cr.left + 40, cr.top + cr.height / 2);"
                    "return {overlap: Math.max(0, Math.min(gr.bottom, cr.bottom)"
                    " - Math.max(gr.top, cr.top)),"
                    " reachable: !!(mid && mid.closest('.ctas-navigation'))};}"
                )
                self.assertIsNotNone(geometry)
                self.assertLessEqual(geometry["overlap"], 1, "the sticky layers overlap")
                self.assertTrue(geometry["reachable"], "the CTAS bar is not clickable")

    def test_the_page_has_one_h1_and_an_unbroken_heading_outline(self):
        headings = self.page.evaluate(
            "() => [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')]"
            ".map(h => [h.tagName, (h.textContent || '').trim().slice(0, 48)])"
        )
        self.assertEqual(sum(tag == "H1" for tag, _ in headings), 1)
        self.assertGreaterEqual(sum(tag == "H2" for tag, _ in headings), 6,
                                "major CTAS sections must carry a section heading")
        previous = 0
        skips = []
        for tag, text_ in headings:
            level = int(tag[1])
            if previous and level > previous + 1:
                skips.append((previous, tag, text_))
            previous = level
        self.assertEqual(skips, [], f"skipped heading levels: {skips}")

    def test_no_javascript_still_reaches_every_published_artifact(self):
        context = self.browser.new_context(java_script_enabled=False,
                                           viewport={"width": 1280, "height": 900})
        page = context.new_page()
        try:
            page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
            links = page.evaluate(
                "() => [...document.querySelectorAll('.ctas-noscript a[href]')]"
                ".map(a => a.getAttribute('href'))"
            )
            self.assertGreaterEqual(len(links), 8,
                                    "the no-JavaScript fallback must list the static artifacts")
            for href in links:
                if href.startswith(("http", "mailto:", "#")):
                    continue
                with self.subTest(href=href):
                    response = page.request.get(self.url.rsplit("/", 1)[0] + "/" + href)
                    self.assertEqual(response.status, 200)
        finally:
            context.close()

    def test_axe_reports_no_wcag_violation(self):
        axe = next(
            (path for path in (ROOT / "node_modules" / "axe-core" / "axe.min.js",
                               ROOT.parent / "node_modules" / "axe-core" / "axe.min.js")
             if path.exists()),
            None,
        )
        if axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        self.page.add_script_tag(content=axe.read_text())
        result = self.page.evaluate(
            "async () => await axe.run(document, {runOnly: {type: 'tag',"
            " values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})"
        )
        summary = [
            f"{v['id']} ({len(v['nodes'])} nodes): {v['help']}" for v in result["violations"]
        ]
        self.assertEqual(summary, [])

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

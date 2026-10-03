#!/usr/bin/env python3
"""The barium dwarf census page (research/barium-dwarfs/) in a real browser: it renders the
diagram, histogram and tables without script errors, sorting/filtering work, no sideways
scroll from 320 px, and axe-core's WCAG 2.1 AA rules pass in light and dark mode."""
from __future__ import annotations

import contextlib
import functools
import http.server
import socket
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = "/research/barium-dwarfs/"


def _port() -> int:
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


class CensusBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright is not installed: {exc}") from exc
        port = _port()
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_Quiet, directory=str(ROOT)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{port}"
        cls.pw = sync_playwright().start()
        try:
            cls.browser = cls.pw.chromium.launch()
        except Exception as exc:  # pragma: no cover
            cls.pw.stop(); cls.server.shutdown()
            raise unittest.SkipTest(f"no Chromium: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.server.shutdown(); cls.server.server_close()

    def open(self, scheme="light", width=1280):
        page = self.browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(self.base) else route.abort())
        page.goto(self.base + PAGE, wait_until="load")
        page.wait_for_selector("#cat-table tbody tr")
        return page, errors

    def test_renders_and_interacts(self):
        page, errors = self.open()
        self.assertEqual(page.locator("#cmd circle.pt-ba").count(), 71)
        self.assertGreater(page.locator("#cmd circle.pt-wd").count(), 70)
        self.assertEqual(page.locator("#cand-table tbody tr").count(), 7)
        self.assertEqual(page.locator("#cat-table tbody tr").count(), 152)
        page.locator("#cmd circle.pt-ba").first.focus(); page.keyboard.press("Enter")
        self.assertIn("Barium dwarf", page.inner_text("#picked"))
        page.select_option("#sample", "ba"); page.fill("#maxdist", "30")
        self.assertEqual(page.locator("#cat-table tbody tr").count(), 5)
        page.click("button[data-sort=teffK]")
        self.assertEqual(page.get_attribute("#cat-table thead th:nth-child(3)", "aria-sort"), "ascending")
        self.assertTrue(page.get_attribute("#csv", "href").startswith("data:text/csv"))
        page.uncheck("#show-wd")
        self.assertEqual(page.locator("#cmd circle.pt-wd").count(), 0)
        self.assertEqual(errors, [])
        page.close()

    def test_no_sideways_scroll(self):
        page, _ = self.open()
        for width in (320, 390, 768, 1280):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 900}); page.wait_for_timeout(150)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
        page.close()

    def test_axe(self):
        axe = next((p for p in (ROOT / "node_modules/axe-core/axe.min.js", ROOT.parent / "node_modules/axe-core/axe.min.js") if p.exists()), None)
        if axe is None:
            self.skipTest("axe-core is not installed")
        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                page, _ = self.open(scheme)
                page.add_script_tag(content=axe.read_text())
                result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})")
                self.assertEqual([f"{v['id']}: {[n['target'] for n in v['nodes'][:3]]}" for v in result["violations"]], [])
                page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
"""The Tonight's sky planner (tonight/) in a real browser.

It checks that the page computes a night and a target table from the published CTAS
and WorldsIndex catalogs without script errors, draws an altitude chart, never scrolls
sideways from 320 px wide up, passes axe-core's WCAG 2.1 AA rules (when axe-core is
installed) in light and dark mode, and that the homepage links to it. The "Transits
tonight" list reads worldsindex/data/transit-ephemerides.json.gz, which these tests replace
with a small file built from the astropy cases in tests/fixtures/tonight-transits.json.
"""
from __future__ import annotations

import contextlib
import functools
import gzip
import http.server
import json
import socket
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWPORTS = ((320, 568), (390, 844), (768, 1024), (1280, 720))
TRANSIT_FIXTURE = ROOT / "tests" / "fixtures" / "tonight-transits.json"
TRANSIT_COLUMNS = ["objectId", "name", "host", "raDeg", "decDeg", "status", "periodDays", "periodErrDays", "t0Bjd", "t0ErrDays",
                   "durationHours", "depthPpt", "hostMag", "hostMagBand", "timeSystem", "sourceId", "sourceTable", "sourceRecordId",
                   "reference", "referenceUrl", "matchedVia"]


def _transit_artifact() -> bytes:
    cases = json.loads(TRANSIT_FIXTURE.read_text())["transits"]
    rows = []
    for case in cases:
        e = {**case["ephemeris"], "hostMag": 9.8, "hostMagBand": "TESS", "timeSystem": "BJD_TDB", "sourceTable": "toi",
             "sourceRecordId": "fixture:" + case["ephemeris"]["objectId"], "reference": None, "referenceUrl": None, "matchedVia": None}
        rows.append([e.get(c) for c in TRANSIT_COLUMNS])
    rows[1][5] = "CANDIDATE"  # one candidate, hidden unless asked for
    payload = {"schemaVersion": "worldsindex-transit-ephemerides.v1", "timeStandard": "BJD_TDB", "count": len(rows),
               "columns": TRANSIT_COLUMNS, "rows": rows}
    return gzip.compress(json.dumps(payload).encode())
THIRD_PARTY_HOSTS = ("googletagmanager.com", "google-analytics.com")


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


@unittest.skipUnless((ROOT / "ctas" / "data" / "live-summary.json").exists()
                     and (ROOT / "worldsindex" / "data" / "catalog-index.json.gz").exists(),
                     "the published catalogs are not in this checkout")
class TonightBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright is not installed: {exc}") from exc
        port = _free_port()
        handler = functools.partial(_QuietHandler, directory=str(ROOT))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{port}"
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch()
        except Exception as exc:  # pragma: no cover
            cls.playwright.stop()
            cls.server.shutdown()
            raise unittest.SkipTest(f"no Chromium available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def open(self, scheme="light", width=1280, height=900, transits: bytes | None = None, transit_status=200):
        page = self.browser.new_page(viewport={"width": width, "height": height},
                                     color_scheme=scheme, timezone_id="America/Denver")
        errors = []
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        body = transits if transits is not None else _transit_artifact()
        page.route("**/worldsindex/data/transit-ephemerides.json.gz", lambda route: route.fulfill(
            status=transit_status, content_type="application/gzip" if transit_status == 200 else "text/plain",
            body=body if transit_status == 200 else "Not found"))
        page.goto(f"{self.base}/tonight/", wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#results table, #results p", timeout=30000)
        return page, errors

    def first_party(self, errors):
        return [e for e in errors if not any(h in e for h in THIRD_PARTY_HOSTS)]

    def test_ctas_night_table_and_chart(self):
        page, errors = self.open()
        self.assertIn("Sunset", page.text_content("#night"))
        self.assertGreater(page.locator("#results tbody tr").count(), 0)
        page.locator("#results tbody tr").first.click()
        page.wait_for_selector("#chart svg path.target")
        href = page.locator("#results tbody tr a").first.get_attribute("href")
        self.assertTrue(href.startswith("../ctas.html?event="), href)
        self.assertEqual(self.first_party(errors), [])
        page.close()

    def test_worldsindex_systems(self):
        page, errors = self.open()
        page.select_option("#list", "worlds")
        page.click("button[type=submit]")
        page.wait_for_function("document.querySelector('#results p')?.textContent.includes('planetary systems')",
                               timeout=30000)
        self.assertGreater(page.locator("#results tbody tr").count(), 0)
        href = page.locator("#results tbody tr a").first.get_attribute("href")
        self.assertTrue(href.startswith("../worldsindex/?object="), href)
        self.assertEqual(self.first_party(errors), [])
        page.close()

    def show_transits(self, page, candidates=False):
        page.select_option("#site", "custom")
        page.fill("#lat", "41.3114"); page.fill("#lon", "-105.5911"); page.fill("#tz", "America/Denver")
        page.fill("#date", "2026-10-03")
        page.select_option("#list", "transits")
        if candidates:
            page.check("#withcandidates")
        page.click("button[type=submit]")
        page.wait_for_function("document.querySelector('#results p')?.textContent.includes('overlap the dark hours')", timeout=30000)

    def test_transits_tonight(self):
        page, errors = self.open()
        page.select_option("#list", "transits")
        self.assertTrue(page.is_visible("#sigmamax"))
        self.assertTrue(page.is_visible("#fullonly"))
        self.assertTrue(page.is_visible("#withcandidates"))
        self.assertFalse(page.is_visible("#maglimit"))
        self.show_transits(page)
        names = page.locator("#results tbody th").all_inner_texts()
        self.assertIn("Fixture Full b", names)
        self.assertIn("Fixture Setting b", names)
        self.assertFalse(any(n.startswith("Fixture Dusk b") for n in names), "candidates are hidden unless asked for")
        self.assertFalse(any("Daytime" in n or "Polar" in n for n in names))
        row = page.locator("#results tbody tr", has_text="Fixture Full b")
        self.assertIn("Full", row.inner_text())
        self.assertEqual(row.locator("a").get_attribute("href"), "../worldsindex/?object=fixture-fixture-full-b&section=object")
        self.assertIn("Partial", page.locator("#results tbody tr", has_text="Fixture Setting b").inner_text())
        self.assertIn("about 8 minutes", page.text_content("#results .bjd-note"))
        row.click()
        page.wait_for_selector("#chart svg rect.transit")
        self.assertIn("transit", page.text_content("#chart .legend"))
        self.assertIn("barycentric time", page.text_content("#chart"))
        page.check("#withcandidates")
        page.click("button[type=submit]")
        page.wait_for_function("[...document.querySelectorAll('#results tbody th')].some(th => th.textContent.includes('Fixture Dusk b'))", timeout=30000)
        page.check("#fullonly")
        page.click("button[type=submit]")
        page.wait_for_function("!document.querySelector('#results tbody')?.textContent.includes('Partial')", timeout=30000)
        self.assertIn("hidden by the full-transit", page.text_content("#results p"))
        self.assertEqual(self.first_party(errors), [])
        page.close()

    def test_transit_list_not_published_yet(self):
        page, _ = self.open(transit_status=404)
        page.select_option("#list", "transits")
        page.click("button[type=submit]")
        page.wait_for_function("document.querySelector('#status').textContent.includes('not published yet')", timeout=30000)
        page.close()

    def test_custom_site_and_blank_magnitude(self):
        page, _ = self.open()
        page.select_option("#site", "custom")
        self.assertTrue(page.is_visible("#lat"))
        page.fill("#lat", "-30.24"); page.fill("#lon", "-70.74"); page.fill("#tz", "America/Santiago")
        page.fill("#maglimit", "")
        page.click("button[type=submit]")
        page.wait_for_function("document.querySelector('#status').textContent === ''", timeout=30000)
        self.assertIn("America/Santiago", page.inner_text("#night"))
        page.close()

    def test_no_viewport_scrolls_sideways(self):
        page, _ = self.open()
        page.locator("#results tbody tr").first.click()
        for width, height in VIEWPORTS:
            with self.subTest(viewport=f"{width}x{height}"):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(250)
                overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1)
        self.show_transits(page)
        page.locator("#results tbody tr").first.click()
        page.wait_for_selector("#chart svg rect.transit")
        for width, height in VIEWPORTS:
            with self.subTest(viewport=f"transits {width}x{height}"):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(250)
                overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1)
        page.close()

    def test_axe_reports_no_wcag_violation(self):
        axe = next((p for p in (ROOT / "node_modules" / "axe-core" / "axe.min.js",
                                ROOT.parent / "node_modules" / "axe-core" / "axe.min.js") if p.exists()), None)
        if axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        for scheme in ("light", "dark"):
            with self.subTest(scheme=scheme):
                page, _ = self.open(scheme)
                page.locator("#results tbody tr").first.click()
                page.wait_for_selector("#chart svg")
                page.add_script_tag(content=axe.read_text())
                result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag',"
                                       " values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
                self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']}" for v in result["violations"]], [])
                self.show_transits(page, candidates=True)
                page.locator("#results tbody tr").first.click()
                page.wait_for_selector("#chart svg rect.transit")
                result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag',"
                                       " values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
                self.assertEqual([f"transits: {v['id']} ({len(v['nodes'])}): {v['help']} {[n['target'] for n in v['nodes']][:3]}"
                                  for v in result["violations"]], [])
                page.close()

    def test_homepage_links_to_the_planner(self):
        page = self.browser.new_page()
        page.goto(f"{self.base}/index.html", wait_until="domcontentloaded")
        self.assertGreaterEqual(page.locator('a[href="tonight/"]').count(), 3)  # nav, tool card, footer
        page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

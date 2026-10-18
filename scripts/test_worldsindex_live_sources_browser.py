#!/usr/bin/env python3
"""The WorldsIndex Evidence tab's "Live from the archives" panel, in a real browser.

The relay and SIMBAD are replaced by local stand-ins (no network). The test checks that
opening a dossier contacts no provider, that "Check all sources now" is keyboard-reachable
and fills every row with a fetched time, a summary and a release comparison, that the
second press bypasses only the browser cache, that nothing scrolls sideways at phone
width, that no first-party script error occurs, and that axe-core finds no WCAG 2.1 AA
violation in the tab (when installed).
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
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "worldsindex" / "data" / "catalog-index.json.gz"
RELAY = "https://relay.test"


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


def _planet_object() -> str:
    with gzip.open(CATALOG, "rt", encoding="utf-8") as stream:
        objects = json.load(stream)["objects"]
    preferred = [o for o in objects if o["objectId"] == "object-wasp-39-b"]
    pool = preferred or [o for o in objects if o.get("primarySourceId") == "nasa-ps" and o.get("identityState") == "CANONICAL"]
    return pool[0]["objectId"]


@unittest.skipUnless(CATALOG.exists(), "the published WorldsIndex catalog is not in this checkout")
class LiveSourcesBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright is not installed: {exc}") from exc
        port = _free_port()
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_QuietHandler, directory=str(ROOT)))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{port}"
        cls.object_id = _planet_object()
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

    def open(self, width=1280, relay=RELAY):
        page = self.browser.new_page(viewport={"width": width, "height": 900})
        errors, self.calls = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)

        def answer(route):
            url = route.request.url
            self.calls.append({"url": url, "cache": route.request.headers.get("cache-control", "")})
            target = unquote(unquote(url.split("?url=", 1)[1])) if "?url=" in url else url
            headers = {"Access-Control-Allow-Origin": "*"}
            if "simbad" in url:
                body = {"metadata": [{"name": "main_id"}, {"name": "otype"}, {"name": "ra"}, {"name": "dec"}], "data": [["WASP-39", "PM*", 217.3, -3.4]]}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body), headers=headers)
            elif "from ps " in target:
                rows = [{"pl_name": "x", "pl_refname": "<a href=https://ui.adsabs.harvard.edu/abs/2026A/abstract>New 2026</a>", "default_flag": 1, "rowupdate": "2026-10-15", "pl_pubdate": "2026-10"}] * 20
                route.fulfill(status=200, content_type="application/json", body=json.dumps(rows), headers=headers)
            elif "from pscomppars" in target:
                route.fulfill(status=200, content_type="application/json", body=json.dumps([{"pl_name": "x", "gaia_dr3_id": "Gaia DR3 3643098875811934208", "rowupdate": "2026-01-01"}]), headers=headers)
            elif "from stellarhosts" in target or "from spectra" in target:
                route.fulfill(status=200, content_type="application/json", body="[]", headers=headers)
            elif "gaiadr3" in target:
                route.fulfill(status=200, content_type="text/csv", body="Source,RA_ICRS,DE_ICRS,Gmag,Plx,PM\n3643098875811934208,217.3,-3.4,11.9,4.6,30\n", headers=headers)
            elif "sweetcat" in target:
                route.fulfill(status=200, content_type="text/csv", body="Name,Teff\n", headers=headers)
            elif "adsabs" in target:
                route.fulfill(status=503, content_type="text/plain", body="key", headers=headers)
            else:
                route.fulfill(status=404, content_type="text/plain", body="", headers=headers)

        page.route("**/live-config.json", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"relay": relay} if relay else {})))
        page.route("https://simbad.cds.unistra.fr/**", answer)
        page.route(f"{RELAY}/**", answer)
        page.goto(f"{self.base}/worldsindex/?object={self.object_id}&section=object", wait_until="networkidle", timeout=90000)
        page.click("#object-tab-sources")
        page.wait_for_selector("[data-live-check]", timeout=30000)
        return page, errors

    def test_check_all_sources_fills_every_row_and_marks_what_is_new(self):
        page, errors = self.open()
        self.assertEqual(self.calls, [], "opening the Evidence tab contacts no provider")
        rows = page.locator("[data-live-row]")
        self.assertGreaterEqual(rows.count(), 12)
        self.assertIn("not checked yet", rows.first.inner_text())
        self.assertIn("release holds", page.locator("[data-live-row='nasa-ps']").inner_text(), "the release baseline is shown before any check")
        page.focus("[data-live-check]")
        self.assertEqual(page.evaluate("document.activeElement?.dataset?.liveCheck"), "", "the control takes keyboard focus")
        page.keyboard.press("Enter")
        page.wait_for_function("document.querySelector('[data-live-progress]').textContent.startsWith('Checked all')", timeout=60000)
        self.assertTrue(self.calls, "providers are contacted only after the request")
        self.assertTrue(all(c["url"].startswith((RELAY + "/?url=", "https://simbad.cds.unistra.fr/")) for c in self.calls))
        ps = page.locator("[data-live-row='nasa-ps']")
        self.assertEqual(ps.get_attribute("data-live-status"), "ok")
        self.assertIn("20 published rows", ps.inner_text())
        self.assertIn("fetched", ps.inner_text())
        self.assertIn("New since the release", ps.inner_text())
        self.assertIn("updated upstream 2026-10-15", ps.inner_text())
        self.assertIn("needs-key", page.locator("[data-live-row='ads']").get_attribute("data-live-status"))
        self.assertIn("NASA ADS key", page.locator("[data-live-row='ads']").inner_text())
        self.assertEqual(page.locator("[data-live-row='simbad']").get_attribute("data-live-status"), "ok")
        self.assertIn("source id from PSCompPars", page.locator("[data-live-row='gaia-dr3']").inner_text())
        self.assertIn("Link only", page.locator("[data-live-row='exoplanet-eu']").inner_text())
        self.assertIn("something new since the release", page.locator("[data-live-progress]").inner_text())
        self.assertIn("Planetary Systems rows", page.locator("[data-live-extras-out]").inner_text())
        # the second press re-runs with the browser cache bypassed, never a changed relay address
        first = len(self.calls)
        button = page.locator("[data-live-check]")
        self.assertIn("Check again", button.inner_text())
        button.click()
        page.wait_for_function("document.querySelector('[data-live-progress]').textContent.startsWith('Checked all')", timeout=60000)
        again = self.calls[first:]
        self.assertTrue(again)
        self.assertTrue(all("fresh=" not in c["url"] for c in again))
        self.assertEqual(sorted(c["url"] for c in again), sorted(c["url"] for c in self.calls[:first]), "the same addresses are asked again (the fresh flag lives in the fetch cache mode, not the URL)")
        self.assertEqual(errors, [])
        page.close()

    def test_without_a_relay_rows_say_so_and_nothing_scrolls_sideways(self):
        for width in (320, 390):
            with self.subTest(width=width):
                page, errors = self.open(width=width, relay="")
                page.click("[data-live-check]")
                page.wait_for_function("document.querySelector('[data-live-progress]').textContent.startsWith('Checked all')", timeout=60000)
                self.assertEqual(page.locator("[data-live-row='nasa-ps']").get_attribute("data-live-status"), "no-relay")
                self.assertIn("relay", page.locator("[data-live-row='nasa-ps']").inner_text())
                self.assertEqual(page.locator("[data-live-row='simbad']").get_attribute("data-live-status"), "ok", "direct sources still run")
                page.wait_for_timeout(250)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
                self.assertEqual(errors, [])
                page.close()

    def test_axe_reports_no_wcag_violation(self):
        axe = next((p for p in (ROOT / "node_modules" / "axe-core" / "axe.min.js",
                                ROOT.parent / "node_modules" / "axe-core" / "axe.min.js") if p.exists()), None)
        if axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        page, _ = self.open()
        page.click("[data-live-check]")
        page.wait_for_function("document.querySelector('[data-live-progress]').textContent.startsWith('Checked all')", timeout=60000)
        page.add_script_tag(content=axe.read_text())
        result = page.evaluate("async () => await axe.run('#tab-sources', {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
        self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']} {[n['target'] for n in v['nodes']][:3]}" for v in result["violations"]], [])
        page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

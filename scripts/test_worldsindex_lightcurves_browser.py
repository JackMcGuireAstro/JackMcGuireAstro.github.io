#!/usr/bin/env python3
"""The WorldsIndex light-curve tab with live MAST light curves, in a real browser.

MAST's search API and the site's relay are replaced by local stand-ins (no network): the
search answers with one TESS 2-minute and one TESS-SPOC full-frame observation, and the relay
serves the synthetic TESS light curve from tests/fixtures/worldsindex-lightcurves. The test
loads a file, checks the raw and folded views and the plain-word errors, that nothing scrolls
sideways at phone widths, and that axe-core finds no WCAG 2.1 AA violation (when installed).
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
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "worldsindex" / "data" / "catalog-index.json.gz"
FITS = ROOT / "tests" / "fixtures" / "worldsindex-lightcurves" / "tess-spoc-synthetic_lc.fits"
RELAY = "https://relay.test"
OBSERVATIONS = [
    {"obsid": 1, "obs_collection": "TESS", "target_name": "16740101", "sequence_number": 20, "t_min": 58842},
    {"obsid": 2, "obs_collection": "HLSP", "provenance_name": "TESS-SPOC", "target_name": "16740101", "sequence_number": 45, "t_min": 59500},
    {"obsid": 3, "obs_collection": "HLSP", "provenance_name": "QLP", "target_name": "16740101", "sequence_number": 45},
]
PRODUCTS = [
    {"parent_obsid": 1, "dataURI": "mast:TESS/product/tess2019357164649-s0020-0000000016740101-0165-s_lc.fits", "size": 1900000},
    {"parent_obsid": 1, "dataURI": "mast:TESS/product/tess2019357164649-s0020-0000000016740101-0165-s_tp.fits", "size": 40000000},
    {"parent_obsid": 2, "dataURI": "mast:HLSP/tess-spoc/s0045/target/0000/0000/1674/0101/hlsp_tess-spoc_tess_phot_0000000016740101-s0045_tess_v1_lc.fits", "size": 300000},
]


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


def _toi_object() -> str:
    with gzip.open(CATALOG, "rt", encoding="utf-8") as stream:
        objects = json.load(stream)["objects"]
    preferred = [o for o in objects if o["objectId"].startswith("candidate-toi-1150-01")]
    pool = preferred or [o for o in objects if o.get("primarySourceId") == "nasa-toi" and o.get("normalizedStatus") in ("CONFIRMED", "CANDIDATE")]
    return pool[0]["objectId"]


@unittest.skipUnless(CATALOG.exists(), "the published WorldsIndex catalog is not in this checkout")
class LightcurveBrowserTests(unittest.TestCase):
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
        cls.object_id = _toi_object()
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

    def open(self, scheme="dark", width=1280, mast_status=200, relay_status=None, object_id=None, release_rows=None):
        page = self.browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
        errors, self.mast_calls, self.relay_urls = [], [], []
        page.on("pageerror", lambda e: errors.append(str(e)))

        def mast(route):
            request = json.loads(parse_qs(route.request.post_data)["request"][0])
            self.mast_calls.append(request)
            data = OBSERVATIONS if request["service"].startswith("Mast.Caom.Filtered") else PRODUCTS
            route.fulfill(status=mast_status, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                          body=json.dumps({"status": "COMPLETE", "data": data}))

        def relay(route):
            self.relay_urls.append(route.request.url)
            if "TAP%2Fsync" in route.request.url:  # TIC lookup in the NASA Exoplanet Archive
                route.fulfill(status=200, headers={"Access-Control-Allow-Origin": "*"}, content_type="application/json",
                              body=json.dumps([{"tic_id": "TIC 86396382"}]))
                return
            hlsp = "hlsp_tess-spoc" in route.request.url
            status = relay_status if relay_status is not None else (400 if hlsp else 200)
            route.fulfill(status=status, headers={"Access-Control-Allow-Origin": "*"}, content_type="application/fits" if status == 200 else "text/plain",
                          body=FITS.read_bytes() if status == 200 else "That address is not on the relay's list of public sources.\n")

        page.route("**/live-config.json", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"relay": RELAY})))
        page.route("https://mast.stsci.edu/api/v0/invoke", mast)
        page.route(f"{RELAY}/**", relay)
        columns = ["objectId", "name", "host", "raDeg", "decDeg", "status", "periodDays", "periodErrDays", "t0Bjd", "t0ErrDays", "durationHours",
                   "depthPpt", "hostMag", "hostMagBand", "timeSystem", "sourceId", "sourceTable", "sourceRecordId", "reference", "referenceUrl", "matchedVia"]
        release = gzip.compress(json.dumps({"schemaVersion": "worldsindex-transit-ephemerides.v1", "columns": columns,
                                            "rows": [[row.get(c) for c in columns] for row in (release_rows or [])]}).encode())
        page.route("**/worldsindex/data/transit-ephemerides.json.gz", lambda route: route.fulfill(
            status=200 if release_rows is not None else 404, content_type="application/gzip", body=release if release_rows is not None else b""))
        page.goto(f"{self.base}/worldsindex/?object={object_id or self.object_id}&section=object", wait_until="networkidle", timeout=90000)
        page.click("#object-tab-lightcurves")
        page.wait_for_selector("#lc-live-search", timeout=30000)
        return page, errors

    def load_first(self, page):
        page.click("#lc-live-search")
        page.wait_for_selector("#lc-live-load", timeout=20000)
        page.click("#lc-live-load")
        page.wait_for_function("document.querySelector('#lc-live-note').textContent.startsWith('Loaded')", timeout=20000)

    def test_identifier_search_load_and_fold(self):
        page, errors = self.open()
        self.assertIn("TIC ", page.text_content("#lc-live-note"))
        page.click("#lc-live-search")
        page.wait_for_selector("#lc-live-load", timeout=20000)
        self.assertEqual(self.mast_calls[0]["service"], "Mast.Caom.Filtered", "searched by identifier, not by position")
        self.assertEqual(self.mast_calls[0]["params"]["filters"][0]["paramName"], "target_name")
        groups = page.eval_on_selector_all("#lc-live-product optgroup", "els => els.map(e => e.label)")
        self.assertEqual(groups, ["TESS 2-minute (SPOC)", "TESS full-frame images (TESS-SPOC)"])
        self.assertEqual(page.locator("#lc-live-product option").count(), 2, "pixel files and other pipelines are not offered")
        page.click("#lc-live-load")
        page.wait_for_function("document.querySelector('#lc-live-note').textContent.startsWith('Loaded')", timeout=20000)
        self.assertIn("Download%2Ffile%3Furi%3Dmast%3ATESS%2Fproduct%2F", self.relay_urls[0])
        self.assertIn("532 measurements kept", page.text_content("#lc-live-note"))
        self.assertIn("file header names TIC 25155310", page.text_content("#lc-live-note"), "a header naming another star is pointed out")
        self.assertIn("TESS SPOC via MAST (live)", page.text_content("#lc-coverage"))
        self.assertFalse(page.eval_on_selector("#lc-fold", "e => e.hidden"))
        self.assertIn("measurements folded on P =", page.text_content("#lc-fold-summary"))
        self.assertGreater(page.eval_on_selector("#lc-fold-canvas", "c => c.width"), 0)
        page.select_option("#lc-fold-window", "full")
        self.assertIn("532 of 532", page.text_content("#lc-fold-summary"))
        self.assertEqual(errors, [])
        page.close()

    def test_planet_page_folds_on_the_release_ephemeris(self):
        # A NASA planet whose own rows carry no mid-transit epoch: the fold uses the release's
        # chosen row (here a TOI row matched by TIC and period), and the TIC comes from the
        # rows or, for older releases, from the NASA Exoplanet Archive.
        release = [{"objectId": "object-wasp-12-b", "name": "WASP-12 b", "host": "WASP-12", "raDeg": 97.636645, "decDeg": 29.6722662, "status": "CONFIRMED",
                    "periodDays": 1.0914304, "periodErrDays": 2e-7, "t0Bjd": 2458842.997159, "t0ErrDays": 5.6e-5, "durationHours": 3.05, "depthPpt": 15.4,
                    "sourceId": "nasa-toi", "sourceTable": "toi", "sourceRecordId": "toi:849", "matchedVia": "tic+period"}]
        page, errors = self.open(object_id="object-wasp-12-b", release_rows=release)
        page.click("#lc-live-search")
        page.wait_for_selector("#lc-live-load", timeout=20000)
        self.assertRegex(page.text_content("#lc-live-note"), r"found by identifier \(TIC 86396382(, from the NASA Exoplanet Archive)?\)")
        self.assertIn("86396382", self.mast_calls[0]["params"]["filters"][0]["values"])
        page.click("#lc-live-load")
        page.wait_for_function("document.querySelector('#lc-live-note').textContent.startsWith('Loaded')", timeout=20000)
        page.wait_for_function("!document.querySelector('#lc-fold').hidden", timeout=20000)
        self.assertIn("release choice", page.eval_on_selector("#lc-fold-eph", "s => s.options[s.selectedIndex].textContent"))
        self.assertIn("TOI matched by TIC and period", page.text_content("#lc-fold-eph"))
        self.assertIn("P = 1.0914304 d", page.text_content("#lc-fold-summary"))
        self.assertEqual(errors, [])
        page.close()

    def test_relay_refusal_and_search_failure_are_plain_words(self):
        page, _ = self.open()
        page.click("#lc-live-search")
        page.wait_for_selector("#lc-live-load", timeout=20000)
        page.select_option("#lc-live-product", "1")
        page.click("#lc-live-load")
        page.wait_for_function("document.querySelector('#lc-live-note').textContent.startsWith('Could not load')", timeout=20000)
        self.assertIn("relay does not yet pass TESS-SPOC full-frame light curves", page.text_content("#lc-live-note"))
        page.close()
        page, _ = self.open(mast_status=503)
        page.click("#lc-live-search")
        page.wait_for_function("document.querySelector('#lc-live-note').textContent.includes('unavailable')", timeout=20000)
        self.assertIn("HTTP 503", page.text_content("#lc-live-note"))
        self.assertTrue(page.is_enabled("#lc-live-search"), "the search can be retried")
        page.close()

    def test_no_sideways_scroll_at_phone_widths(self):
        page, _ = self.open(width=390)
        self.load_first(page)
        for width in (320, 390, 768):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 800})
                page.wait_for_timeout(250)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
        page.close()

    def test_axe_reports_no_wcag_violation(self):
        axe = next((p for p in (ROOT / "node_modules" / "axe-core" / "axe.min.js",
                                ROOT.parent / "node_modules" / "axe-core" / "axe.min.js") if p.exists()), None)
        if axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        for scheme in ("dark", "light"):
            with self.subTest(scheme=scheme):
                page, _ = self.open(scheme)
                self.load_first(page)
                page.add_script_tag(content=axe.read_text())
                result = page.evaluate("async () => await axe.run('#tab-lightcurves', {runOnly: {type: 'tag',"
                                       " values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
                self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']} {[n['target'] for n in v['nodes']][:3]}"
                                  for v in result["violations"]], [])
                page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

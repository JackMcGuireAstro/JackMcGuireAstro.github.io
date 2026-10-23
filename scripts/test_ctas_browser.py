#!/usr/bin/env python3
"""Real-browser checks for the published CTAS page.

These load the generated static release exactly as a reader would: over HTTP,
in Chromium, with JavaScript running. They assert the properties that only a
browser can prove — that the first screen does not download the complete
catalog, that the celestial sphere is painted without any interaction, that a
dossier opens with its source matrix rebuilt from the shared patterns, that the
complete catalog arrives only on request, and that no tested viewport scrolls
sideways. The Track 1 part C tests add the sky tools (finder chart, SAMP, pinned Aladin
atlas, localisation region, report link), the lazy changelog panel, the first-screen
budget line, and ctas/status.html.

Skipped, not failed, when Playwright or a generated release is unavailable, so
a publisher without a browser stack still reports honestly instead of blocking.

    python3 scripts/test_ctas_browser.py
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import json
import re
import socket
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_test_net import isolate_browser, is_third_party_load_failure  # noqa: E402  (third-party requests are blocked)

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "ctas" / "data"
SPECTRUM_FIXTURES = ROOT / "tests" / "fixtures" / "spectra"
TNS_TEXT_FILE = re.compile(r"^https://www\.wis-tns\.org/system/files/uploaded/[A-Za-z0-9_\-./%+]+$")
WISEREP_LISTING = re.compile(r"^https://www\.wiserep\.org/search/spectra\?(?=.*(?:^|&)public=yes(?:&|$))(?=.*(?:^|&)format=(?:json|tsv|csv)(?:&|$))[A-Za-z0-9_\-=&.%+]*$")
VIEWPORTS = ((320, 568), (390, 844), (768, 1024), (1280, 720), (1440, 900))
# Requests to hosts the page does not own must never decide whether the page
# works; a blocked analytics beacon is not a CTAS defect.
THIRD_PARTY_HOSTS = ("googletagmanager.com", "google-analytics.com")


@functools.lru_cache(maxsize=None)
def _catalog_rows() -> tuple[dict, ...]:
    index = json.loads((DATA / "catalog-index.json").read_text())
    columns = index["candidate_columns"]
    return tuple(dict(zip(columns, row)) for row in index["candidate_rows"])


@functools.lru_cache(maxsize=64)
def _detail_chunk(path: str) -> tuple[dict, ...]:
    sys.path.insert(0, str(ROOT / "scripts"))
    from ctas_chunks import decode_chunk  # noqa: PLC0415 - only needed by dossier tests
    root = ROOT / "ctas" / "data" / path
    document = decode_chunk(root.read_bytes(), lambda relative: (ROOT / relative).read_bytes())
    return tuple(document["candidates"])


def _published_dossier(row_filter, detail_filter, limit=40):
    """The first published dossier whose compact row and full record both qualify.

    The release changes every half hour, so tests pick a qualifying record from the
    data they run against instead of naming one."""
    checked = 0
    for row in _catalog_rows():
        if not row_filter(row):
            continue
        for candidate in _detail_chunk(row["detail_chunk"]):
            if candidate["event_id"] == row["event_id"] and detail_filter(candidate):
                return candidate
        checked += 1
        if checked >= limit:
            break
    return None


def _tns_text_spectrum(candidate):
    for index, spectrum in enumerate((candidate.get("follow_up") or {}).get("spectra") or []):
        url = spectrum.get("public_download_url") or ""
        if TNS_TEXT_FILE.match(url) and not re.search(r"\.(fits?|fts|fz|gz|zip|tar|pdf|png|jpe?g)$", url, re.I):
            return index, spectrum
    return None


def _wiserep_spectrum(candidate):
    """A WISeREP record: its public listing address, record page, spectrum id and file name."""
    for index, spectrum in enumerate((candidate.get("follow_up") or {}).get("spectra") or []):
        url = spectrum.get("public_download_url") or ""
        if WISEREP_LISTING.match(url) and spectrum.get("file_name"):
            return index, spectrum
    return None


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
            cls.browser = isolate_browser(cls.playwright.chromium.launch(), cls.url)
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
            and not is_third_party_load_failure(entry[1])
        ]
        self.assertEqual(errors, [])

    def test_a_blocked_dss2_thumbnail_becomes_a_graceful_placeholder(self):
        """Third-party requests are blocked in these tests (scripts/browser_test_net.py), as they
        may fail for readers: a leaderboard row's 96-pixel HiPS2FITS thumbnail then shows the
        "No image" placeholder (ctas/presentation.js), never a broken image or a script error."""
        if self.page.locator("img[data-ctas-thumbnail]").count() == 0 and self.page.locator(".ctas-thumb--missing").count() == 0:
            self.skipTest("no thumbnail on the first screen of this release")
        self.page.wait_for_function(
            "() => [...document.querySelectorAll('.ctas-thumb.ctas-thumb--missing')].some(t => t.textContent === 'No image')",
            timeout=20000)
        placeholder = self.page.locator(".ctas-thumb.ctas-thumb--missing", has_text="No image").first
        self.assertEqual(placeholder.get_attribute("title"), "Archival provider image unavailable")
        self.assertEqual(placeholder.locator("img").count(), 0)
        self.assertEqual([e for e in self.console if e[0] == "pageerror"], [])

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

    def test_live_source_data_is_fetched_only_on_request_and_labelled(self):
        """The live panel contacts providers only when asked, draws what they return
        with the retained-data renderer, and says the values are outside the snapshot."""
        import json as _json
        calls = []
        lightcurve = {"detections": [{"mjd": 61200.5 + i, "fid": 1 + i % 2, "magpsf": 18 + i / 10,
                                      "sigmapsf": 0.05, "isdiffpos": "t"} for i in range(6)],
                      "non_detections": [{"mjd": 61199.5, "fid": 1, "diffmaglim": 20.3}]}

        def provider(route):
            url = route.request.url
            calls.append(url)
            if url.endswith("/lightcurve"):
                body = lightcurve
            elif "/ztf/v1/objects/?" in url:
                from urllib.parse import urlparse, parse_qs
                q = parse_qs(urlparse(url).query)
                body = {"items": [{"oid": "ZTF26testobj", "meanra": float(q["ra"][0]), "meandec": float(q["dec"][0])}]}
            elif "fink-portal" in url:
                body = []
            else:
                body = {}
            route.fulfill(status=200, content_type="application/json", body=_json.dumps(body),
                          headers={"Access-Control-Allow-Origin": "*"})
        # No relay configured, so the relay-only sources must say so (live-config.json on the
        # real site names the relay, whose answers this offline test cannot rely on).
        self.page.route("**/live-config.json", lambda route: route.fulfill(
            status=200, content_type="application/json", body="{}"))
        self.page.route("https://api.alerce.online/**", provider)
        self.page.route("https://api.lsst.fink-portal.org/**", provider)
        self.page.locator("#ctas-results [data-open-event]").first.click()
        self.page.wait_for_selector("#candidate-workspace [data-live-panel]", state="attached", timeout=45000)
        self.assertEqual(calls, [], "opening a dossier must not contact live providers")
        self.page.evaluate("""() => { document.querySelector('[data-live-panel]').open = true;
          document.querySelector('[data-live-fetch]').dispatchEvent(new MouseEvent('click', {bubbles: true})); }""")
        self.page.wait_for_function(
            "() => /Fetched /.test(document.querySelector('[data-live-results]').textContent)", timeout=60000)
        results = self.page.locator("[data-live-results]").inner_text()
        self.assertIn("ZTF via ALeRCE", results)
        self.assertIn("7 measurements", results, "six detections and one limit from the stubbed light curve")
        self.assertEqual(self.page.locator("[data-live-results] .ctas-lightcurve svg").count(), 1)
        self.assertIn("Rubin via Fink", results)
        self.assertIn("relay", results, "relay-only sources say they need the relay rather than failing silently")
        self.assertTrue(calls, "providers are contacted after the request")
        self.assertTrue(all(u.startswith(("https://api.alerce.online/", "https://api.lsst.fink-portal.org/")) for u in calls))

    def test_check_all_sources_now_fills_every_row_and_marks_what_is_new(self):
        """One keyboard-reachable control runs every live source; each row gets a fetched
        time, a summary and a comparison with the release; axe passes afterwards."""
        import json as _json
        from urllib.parse import unquote, urlparse, parse_qs
        calls = []

        def direct(route):
            url = route.request.url
            calls.append(url)
            if url.endswith("/lightcurve"):
                body = {"detections": [{"mjd": 61500.5 + i, "fid": 1 + i % 2, "magpsf": 18 + i / 10, "sigmapsf": 0.05, "isdiffpos": "t"} for i in range(4)], "non_detections": []}
            elif "/ztf/v1/objects/?" in url:
                q = parse_qs(urlparse(url).query)
                body = {"items": [{"oid": "ZTF26testobj", "meanra": float(q["ra"][0]), "meandec": float(q["dec"][0])}]}
            elif "simbad" in url:
                body = {"metadata": [{"name": "main_id"}, {"name": "otype"}, {"name": "sep"}], "data": [["NGC 1", "G", 0.001]]}
            else:
                body = []
            route.fulfill(status=200, content_type="application/json", body=_json.dumps(body), headers={"Access-Control-Allow-Origin": "*"})

        def relay(route):
            calls.append(route.request.url)
            target = unquote(route.request.url.split("?url=", 1)[1])
            h = {"Access-Control-Allow-Origin": "*"}
            if "alerts.csv" in target:
                route.fulfill(status=200, content_type="text/csv", body="#Name,Date,RaDeg,DecDeg,AlertMag\nGaia26abc,2026-10-01 00:00:00,0,0,18.0\n", headers=h)
            elif "ned.ipac" in target:
                route.fulfill(status=200, content_type="text/csv", body="prefname,pretype,ra,dec,z\nWISEA J1,G,1,2,0.1\n", headers=h)
            elif "heasarc" in target:
                route.fulfill(status=200, content_type="text/csv", body="name,obsid\n", headers=h)
            elif "adsabs" in target:
                route.fulfill(status=200, content_type="application/json", body=_json.dumps({"response": {"numFound": 40, "docs": [{"bibcode": "2026TNSAN.1....1K", "title": ["A note"], "author": ["A"], "pubdate": "2026-10-00", "doctype": "circular"}]}}), headers=h)
            elif "wiserep" in target:
                route.fulfill(status=200, content_type="application/json", body=_json.dumps([{"Spec. ID": 1}, {"Spec. ID": 2}, {"Spec. ID": 3}, {"Spec. ID": 4}, {"Spec. ID": 5}]), headers=h)
            elif "aavso" in target:
                route.fulfill(status=200, content_type="application/json", body=_json.dumps({"VSXObjects": ""}), headers=h)
            elif "lasair" in target:
                route.fulfill(status=503, content_type="text/plain", body="key", headers=h)
            elif "panstarrs" in target:
                route.fulfill(status=200, content_type="application/json", body=_json.dumps({"data": []}), headers=h)
            else:
                route.fulfill(status=200, content_type="text/csv", body="", headers=h)

        self.page.route("**/live-config.json", lambda route: route.fulfill(
            status=200, content_type="application/json", body=_json.dumps({"relay": "https://relay.test"})))
        for host in ("https://api.alerce.online/**", "https://api.lsst.fink-portal.org/**", "https://simbad.cds.unistra.fr/**"):
            self.page.route(host, direct)
        self.page.route("https://relay.test/**", relay)
        self.page.locator("#ctas-results [data-open-event]").first.click()
        self.page.wait_for_selector("#candidate-workspace [data-live-panel]", state="attached", timeout=45000)
        self.page.evaluate("() => { document.querySelector('[data-live-panel]').open = true; }")
        rows = self.page.locator("[data-live-row]")
        self.assertGreaterEqual(rows.count(), 16, "every registered source has a row before any check")
        self.assertIn("not checked yet", rows.first.inner_text())
        self.assertRegex(self.page.locator("[data-live-rows]").inner_text(), r"release (last asked|never asked)", "rows say when the release last asked each source")
        self.assertEqual(calls, [], "opening the panel contacts no provider")
        button = self.page.locator("[data-live-fetch]")
        self.assertEqual(button.inner_text().strip(), "Check all sources now")
        button.focus()
        self.assertEqual(self.page.evaluate("() => document.activeElement && document.activeElement.hasAttribute('data-live-fetch')"), True, "the control takes keyboard focus")
        self.page.keyboard.press("Enter")
        self.page.wait_for_function("() => /^Checked all/.test(document.querySelector('[data-live-progress]').textContent)", timeout=60000)
        self.assertTrue(calls)
        self.assertTrue(all(u.startswith(("https://relay.test/?url=", "https://api.alerce.online/", "https://api.lsst.fink-portal.org/", "https://simbad.cds.unistra.fr/")) for u in calls),
                        "direct where CORS allows, otherwise through the relay")
        text = self.page.locator("[data-live-rows]").inner_text()
        self.assertNotIn("checking…", text, "every row has settled")
        self.assertNotIn("not checked yet", text)
        self.assertRegex(text, r"fetched \d\d:\d\d:\d\d UTC", "each row carries its fetched time")
        alerce = self.page.locator("[data-live-row='alerce']")
        self.assertEqual(alerce.get_attribute("data-live-status"), "ok")
        self.assertIn("4 measurements", alerce.inner_text())
        self.assertIn("New since the release", alerce.inner_text(), "measurements dated after anything retained are marked")
        self.assertEqual(self.page.locator("[data-live-row='lasair']").get_attribute("data-live-status"), "needs-key")
        self.assertIn("Lasair key", self.page.locator("[data-live-row='lasair']").inner_text())
        self.assertEqual(self.page.locator("[data-live-row='simbad']").get_attribute("data-live-status"), "ok")
        self.assertIn("NGC 1", self.page.locator("[data-live-row='simbad']").inner_text())
        self.assertIn("5 public spectra", self.page.locator("[data-live-row='wiserep']").inner_text())
        self.assertIn("Link only", self.page.locator("[data-live-row='tns-page']").inner_text())
        self.assertIn("something new since the release", self.page.locator("[data-live-progress]").inner_text())
        self.assertEqual(self.page.locator("[data-live-results] .ctas-lightcurve svg").count(), 1, "the live measurements are drawn with the retained-data renderer")
        self.assertIn("Fetched ", self.page.locator("[data-live-extras]").inner_text())
        remembered = self.page.evaluate("() => Object.keys(localStorage).filter(k => k.startsWith('ctas-live-seen:')).length")
        self.assertEqual(remembered, 1, "the summary is remembered in this browser only")
        # second press: same addresses, browser cache bypassed, relay address unchanged
        first = len(calls)
        self.assertIn("Check again", button.inner_text())
        button.click()
        self.page.wait_for_function("() => /^Checked all/.test(document.querySelector('[data-live-progress]').textContent)", timeout=60000)
        self.assertTrue(all("fresh=" not in u for u in calls[first:]))
        self.assertEqual(sorted(calls[first:]), sorted(calls[:first]))
        errors = [e for e in self.console if e[0] in {"error", "pageerror"} and "Failed to load resource" not in e[1]
                  and not any(host in e[1] for host in THIRD_PARTY_HOSTS)]
        self.assertEqual(errors, [])
        for width, height in ((320, 568), (390, 844)):
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.wait_for_timeout(300)
                overflow = self.page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1)
        self.assertEqual(self._axe_violations(), [])

    # ------------------------------------------------------------ live TNS spectra
    def _open_dossier(self, event_id, page=None):
        page = page or self.page
        page.goto(self.url + "?event=" + event_id, wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#candidate-workspace #dossier", state="attached", timeout=60000)

    def _spectrum_dossier(self):
        candidate = _published_dossier(lambda row: (row.get("n_spectra") or 0) > 0,
                                       lambda c: _tns_text_spectrum(c) is not None)
        if candidate is None:
            self.skipTest("this release has no dossier with a public TNS text spectrum")
        return candidate

    def _route_relay(self, answer, page=None):
        """Point live-config.json at a test relay and answer its requests with `answer`."""
        page = page or self.page
        calls = []
        page.route("**/live-config.json", lambda route: route.fulfill(
            status=200, content_type="application/json", body=json.dumps({"relay": "https://relay.test"})))

        def relay(route):
            calls.append(route.request.url)
            answer(route)
        page.route("https://relay.test/**", relay)
        return calls

    def _plot_first_spectrum(self, page=None, index=None):
        page = page or self.page
        page.evaluate("""() => { const panel = document.querySelector('[data-dossier-view="spectra"]'); panel.open = true;
          panel.querySelectorAll('details.ctas-spectrum-record').forEach((d) => { d.open = true; }); }""")
        button = page.locator("[data-plot-spectrum]" if index is None else f"[data-plot-spectrum='{index}']").first
        button.scroll_into_view_if_needed()
        button.click()

    def _status_of(self, page, index):
        return page.locator(f"[data-spectrum-live]:has([data-plot-spectrum='{index}']) [data-spectrum-status]").first

    def test_tns_spectrum_is_plotted_on_request_through_the_relay(self):
        candidate = self._spectrum_dossier()
        index, spectrum = _tns_text_spectrum(candidate)
        fixture = (SPECTRUM_FIXTURES / "sedm-ztf.ascii").read_text()
        calls = self._route_relay(lambda route: route.fulfill(
            status=200, content_type="text/plain", body=fixture, headers={"Access-Control-Allow-Origin": "*"}))
        self._open_dossier(candidate["event_id"])
        self.assertEqual(calls, [], "opening a dossier must not fetch spectrum files")
        live = self.page.locator(f"[data-spectrum-live] [data-plot-spectrum='{index}']")
        self.assertEqual(live.count(), 1)
        self._plot_first_spectrum(index=index)
        self.page.wait_for_selector("[data-spectrum-svg] svg", timeout=30000)
        from urllib.parse import quote
        self.assertEqual(calls, ["https://relay.test/?url=" + quote(spectrum["public_download_url"], safe="")])
        status = self._status_of(self.page, index).inner_text()
        self.assertIn("Plotted 137 points", status)
        svg = self.page.locator("[data-spectrum-svg] svg").first
        self.assertEqual(svg.get_attribute("role"), "img")
        title_id, desc_id = svg.get_attribute("aria-labelledby").split()
        self.assertIn("Spectrum", self.page.locator(f"#{title_id}").text_content())
        self.assertIn("normalised flux", self.page.locator(f"#{desc_id}").text_content())
        figure = self.page.locator(".ctas-spectrum-live__figure").first
        self.assertEqual(figure.locator("figcaption a").get_attribute("href"), spectrum["public_download_url"])
        self.assertIn("not part of the verified snapshot", figure.locator("figcaption").inner_text())
        self.assertGreater(figure.locator(".ctas-spectrum-marker").count(), 3, "common line markers are drawn")
        self.assertIn("Observed wavelength", svg.text_content())
        redshift = candidate.get("redshift")
        if isinstance(redshift, (int, float)) and redshift > 0:
            figure.locator("[data-spectrum-frame][value='rest']").check()
            self.assertIn("Rest-frame wavelength", figure.locator("[data-spectrum-svg] svg").text_content())
        figure.locator("[data-spectrum-lines]").uncheck()
        self.assertEqual(figure.locator(".ctas-spectrum-marker").count(), 0, "markers can be hidden")
        self.assertGreater(figure.locator(".ctas-spectrum-table tbody tr").count(), 10, "a table view of the plot exists")

    def test_fits_spectrum_is_plotted_with_its_header_keywords(self):
        candidate = self._spectrum_dossier()
        index, spectrum = _tns_text_spectrum(candidate)
        fixture = (SPECTRUM_FIXTURES / "linear-1d.fits").read_bytes()
        calls = self._route_relay(lambda route: route.fulfill(
            status=200, content_type="application/fits", body=fixture, headers={"Access-Control-Allow-Origin": "*"}))
        self._open_dossier(candidate["event_id"])
        self._plot_first_spectrum(index=index)
        self.page.wait_for_selector("[data-spectrum-svg] svg", timeout=30000)
        self.assertEqual(len(calls), 1)
        status = self._status_of(self.page, index).inner_text()
        self.assertIn("Plotted 398 points", status)
        self.assertIn("(FITS)", status)
        figure = self.page.locator(".ctas-spectrum-live__figure").first
        self.assertGreater(figure.locator(".ctas-spectrum-marker").count(), 3, "the same line markers as an ASCII plot")
        facts = figure.locator(".ctas-spectrum-fits").inner_text()
        for keyword, value in (("OBJECT", "SN 2026fit"), ("DATE-OBS", "2026-09-12T10:29:45"), ("TELESCOP", "P60"),
                               ("INSTRUME", "SEDM"), ("EXPTIME", "2700"), ("BUNIT", "erg/cm2/s/A")):
            self.assertIn(keyword, facts)
            self.assertIn(value, facts)
        self.assertIn("Air", facts, "air wavelengths are labelled, not converted")
        caption = figure.locator("figcaption").inner_text()
        self.assertIn("FITS file read from the primary HDU", caption)
        self.assertIn("398 pixels read (2 blank or non-finite pixels ignored)", caption)
        self.assertIn("not part of the verified snapshot", caption)
        self.assertEqual(figure.locator("figcaption a").first.get_attribute("href"), spectrum["public_download_url"])
        self.assertEqual(self._axe_violations(), [], "the header-keyword list and plot pass axe")

    def _wiserep_dossier(self):
        candidate = _published_dossier(lambda row: (row.get("n_spectra") or 0) > 0,
                                       lambda c: _wiserep_spectrum(c) is not None)
        if candidate is None:
            self.skipTest("this release has no dossier with a WISeREP spectrum record")
        return candidate

    def test_wiserep_public_file_is_found_through_the_listing_and_plotted(self):
        candidate = self._wiserep_dossier()
        index, spectrum = _wiserep_spectrum(candidate)
        from urllib.parse import quote, unquote
        file_url = "https://www.wiserep.org/system/files/uploaded/TNS/" + quote(spectrum["file_name"])
        listing = json.dumps([{"Spec. ID": str(spectrum.get("provider_spectrum_id") or ""), "Obj. Name": candidate["name"],
                               "Ascii file": spectrum["file_name"], "FITS file": "", "Groups": "TNS", "WL Medium": "Air", "Public": "Yes"}])
        fixture = (SPECTRUM_FIXTURES / "sedm-ztf.ascii").read_text()

        def answer(route):
            target = unquote(route.request.url.split("?url=", 1)[1])
            if target == spectrum["public_download_url"]:
                route.fulfill(status=200, content_type="application/json", body=listing, headers={"Access-Control-Allow-Origin": "*"})
            else:
                route.fulfill(status=200, content_type="text/plain", body=fixture, headers={"Access-Control-Allow-Origin": "*"})
        calls = self._route_relay(answer)
        self._open_dossier(candidate["event_id"])
        self.assertEqual(calls, [], "opening a dossier asks WISeREP nothing")
        box = self.page.locator(f"[data-spectrum-live][data-spectrum-provider='WISeREP']:has([data-plot-spectrum='{index}'])")
        self.assertEqual(box.count(), 1)
        self.assertIn("proprietary", self._status_of(self.page, index).text_content(), "the control says proprietary files are not fetched")
        self._plot_first_spectrum(index=index)
        self.page.wait_for_selector("[data-spectrum-svg] svg", timeout=30000)
        self.assertEqual(calls, ["https://relay.test/?url=" + quote(spectrum["public_download_url"], safe=""),
                                 "https://relay.test/?url=" + quote(file_url, safe="")],
                         "the public listing first, then exactly the file it names")
        self.assertIn("Plotted 137 points", self._status_of(self.page, index).inner_text())
        figure = box.locator(".ctas-spectrum-live__figure").first
        self.assertIn("Fetched from WISeREP", figure.locator("figcaption").inner_text())
        self.assertEqual(figure.locator("figcaption a").first.get_attribute("href"), file_url)
        self.assertEqual(figure.locator("figcaption a").nth(1).get_attribute("href"), spectrum.get("source_url") or spectrum["public_download_url"])
        self.assertTrue(all("password" not in url.lower() and "token" not in url.lower() for url in calls))

    def test_wiserep_proprietary_spectrum_is_reported_not_fetched(self):
        candidate = self._wiserep_dossier()
        index, spectrum = _wiserep_spectrum(candidate)
        from urllib.parse import unquote
        listing = json.dumps([{"Spec. ID": str(spectrum.get("provider_spectrum_id") or ""), "Ascii file": spectrum["file_name"], "Groups": "TNS", "Public": "Yes"}])
        login = "<!DOCTYPE html><html><head><title>WISeREP | Log in</title></head><body><form><input name='pass' type='password'></form></body></html>"

        def listing_is_login(route):
            route.fulfill(status=200, content_type="text/html", body=login, headers={"Access-Control-Allow-Origin": "*"})

        def file_is_refused(route):
            target = unquote(route.request.url.split("?url=", 1)[1])
            if target == spectrum["public_download_url"]:
                route.fulfill(status=200, content_type="application/json", body=listing, headers={"Access-Control-Allow-Origin": "*"})
            else:
                route.fulfill(status=403, content_type="text/plain", body="Forbidden", headers={"Access-Control-Allow-Origin": "*"})

        def file_is_login(route):
            target = unquote(route.request.url.split("?url=", 1)[1])
            if target == spectrum["public_download_url"]:
                route.fulfill(status=200, content_type="application/json", body=listing, headers={"Access-Control-Allow-Origin": "*"})
            else:
                route.fulfill(status=200, content_type="text/html", body=login, headers={"Access-Control-Allow-Origin": "*"})
        for answer in (listing_is_login, file_is_refused, file_is_login):
            with self.subTest(case=answer.__name__):
                page = self.browser.new_page(viewport={"width": 1280, "height": 900})
                try:
                    calls = self._route_relay(answer, page)
                    self._open_dossier(candidate["event_id"], page)
                    self._plot_first_spectrum(page, index=index)
                    page.wait_for_function(
                        "() => document.querySelector('[data-spectrum-live].is-proprietary [data-spectrum-status].is-problem')", timeout=30000)
                    status = self._status_of(page, index).inner_text()
                    self.assertIn("proprietary on WISeREP", status)
                    self.assertIn("request access", status)
                    self.assertEqual(page.locator("[data-spectrum-svg] svg").count(), 0)
                    self.assertLessEqual(len(calls), 2, "nothing is retried or asked for with credentials")
                finally:
                    page.close()

    def test_spectrum_problems_are_explained_in_plain_words(self):
        candidate = self._spectrum_dossier()
        index, _spectrum = _tns_text_spectrum(candidate)
        cases = (
            (lambda route: route.fulfill(status=404, body="Not Found", headers={"Access-Control-Allow-Origin": "*"}),
             "no file at this address"),
            (lambda route: route.fulfill(status=200, content_type="text/html", headers={"Access-Control-Allow-Origin": "*"},
                                         body="<!DOCTYPE html><html><body>Please log in</body></html>"),
             "web page instead of the data file"),
            (lambda route: route.fulfill(status=200, content_type="text/plain", body="4000 1\n4001 2\n",
                                         headers={"Access-Control-Allow-Origin": "*"}),
             "too few to plot"),
            (lambda route: route.abort("connectionrefused"), "could not be reached"),
        )
        for answer, expected in cases:
            with self.subTest(expected=expected):
                page = self.browser.new_page(viewport={"width": 1280, "height": 900})
                try:
                    self._route_relay(answer, page)
                    self._open_dossier(candidate["event_id"], page)
                    self._plot_first_spectrum(page, index=index)
                    page.wait_for_function(
                        "() => document.querySelector('[data-spectrum-status].is-problem')", timeout=30000)
                    self.assertIn(expected, self._status_of(page, index).inner_text())
                    self.assertEqual(page.locator("[data-spectrum-svg] svg").count(), 0)
                finally:
                    page.close()

    def test_spectrum_without_a_relay_says_so(self):
        candidate = self._spectrum_dossier()
        self.page.route("**/live-config.json", lambda route: route.fulfill(
            status=200, content_type="application/json", body="{}"))
        index, _spectrum = _tns_text_spectrum(candidate)
        self._open_dossier(candidate["event_id"])
        self._plot_first_spectrum(index=index)
        self.page.wait_for_function("() => document.querySelector('[data-spectrum-status].is-problem')", timeout=30000)
        self.assertIn("relay is not configured", self._status_of(self.page, index).inner_text())

    def test_plotted_spectrum_is_accessible_and_fits_a_phone(self):
        candidate = self._spectrum_dossier()
        fixture = (SPECTRUM_FIXTURES / "longslit-nm.dat").read_text()
        self._route_relay(lambda route: route.fulfill(
            status=200, content_type="text/plain", body=fixture, headers={"Access-Control-Allow-Origin": "*"}))
        index, _spectrum = _tns_text_spectrum(candidate)
        for width, height in ((320, 568), (390, 844)):
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self._open_dossier(candidate["event_id"])
                self._plot_first_spectrum(index=index)
                self.page.wait_for_selector("[data-spectrum-svg] svg", timeout=30000)
                self.page.wait_for_timeout(300)
                overflow = self.page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1)
        self.assertEqual(self._axe_violations(), [])

    # ------------------------------------------------------------ light curves
    def _photometry_dossier(self):
        def rich(row):
            outcomes = row.get("photometry_outcomes") or {}
            return (outcomes.get("detections") or 0) >= 8 and (outcomes.get("forced") or 0) > 0 and (outcomes.get("limits") or 0) > 0
        candidate = _published_dossier(rich, lambda c: bool((c.get("follow_up") or {}).get("observations")))
        if candidate is None:
            self.skipTest("this release has no dossier with detections, limits and forced photometry")
        return candidate

    def _open_photometry(self, candidate, page=None):
        page = page or self.page
        self._open_dossier(candidate["event_id"], page)
        page.evaluate("() => { document.querySelector('[data-phot-panel]').open = true; }")
        page.wait_for_selector("[data-phot-panel] .ctas-lightcurve svg", timeout=30000)

    def test_dossier_light_curve_is_one_accessible_combined_plot(self):
        candidate = self._photometry_dossier()
        self._open_photometry(candidate)
        panel = self.page.locator("[data-phot-panel]")
        self.assertEqual(panel.locator(".ctas-lightcurve svg").count(), 1, "one combined plot")
        svg = panel.locator(".ctas-lightcurve svg")
        self.assertEqual(svg.get_attribute("role"), "img")
        title_id, desc_id = svg.get_attribute("aria-labelledby").split()
        self.assertIn("Light curve of " + candidate["name"], self.page.locator(f"#{title_id}").text_content())
        self.assertRegex(self.page.locator(f"#{desc_id}").text_content(), r"[\d,]+ detections and [\d,]+ upper limits")
        self.assertIn("Days since", svg.text_content())
        self.assertGreater(svg.locator("circle.ctas-lc-point, rect.ctas-lc-point").count(), 0, "detections are filled points")
        self.assertGreater(svg.locator("rect.ctas-lc-point").count(), 0, "forced photometry is drawn as squares")
        self.assertGreater(svg.locator("path.ctas-lc-limit").count(), 0, "limits are downward triangles")
        self.assertGreater(panel.locator(".ctas-lc-legend li").count(), 0, "a band legend")
        self.assertIn("forced-photometry detection", panel.locator(".ctas-lc-key").inner_text())
        table = panel.locator(".ctas-lc-table")
        self.assertIn("Show the plotted points as a table", table.locator("summary").inner_text())
        table.locator("summary").click()
        self.assertGreater(table.locator("tbody tr").count(), 8)
        estimates = panel.locator(".ctas-lc-estimates")
        self.assertIn("Simple estimates", estimates.inner_text())
        self.assertIn("not model fits", estimates.inner_text())
        for label in ("Observed rise", "Early decline", "g − r near peak"):
            self.assertIn(label, estimates.inner_text())
        self.assertGreater(estimates.locator(".ctas-lc-peaks tbody tr").count(), 0, "a peak per band with detections")
        toggle = panel.locator("[data-lc-log]")
        if toggle.count():
            before = toggle.is_checked()
            toggle.click()
            label = panel.locator(".ctas-lightcurve svg").text_content()
            self.assertEqual("logarithmic" in label, not before, "the time axis switches scale")
            # the choice survives the band filter re-rendering the panel
            self.page.locator("[data-phot-band]").select_option(index=1)
            self.page.wait_for_selector("[data-phot-panel] .ctas-lightcurve svg", timeout=10000)
            rebuilt = self.page.locator("[data-phot-panel] [data-lc-log]")
            if rebuilt.count():
                self.assertEqual(rebuilt.is_checked(), not before)

    def test_light_curve_and_estimates_fit_a_phone_and_pass_axe(self):
        candidate = self._photometry_dossier()
        for width, height in ((320, 568), (390, 844)):
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self._open_photometry(candidate)
                self.page.evaluate("() => document.querySelector('.ctas-lc-table').open = true")
                self.page.wait_for_timeout(300)
                overflow = self.page.evaluate(
                    "() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1)
                self.assertEqual(self.page.locator(".ctas-lightcurve svg.is-narrow").count(), 1, "phone-width drawing")
        self.assertEqual(self._axe_violations(), [])

    # ------------------------------------------------------------ suggested follow-up
    def _followup_dossier(self):
        candidate = _published_dossier(
            lambda row: row.get("record_role") == "follow-up-target-candidate" and row.get("discovery_magnitude") is not None
            and row.get("ra_deg") is not None and (row.get("status") or "") == "candidate",
            lambda c: True)
        if candidate is None:
            self.skipTest("this release has no optical follow-up target with a magnitude")
        return candidate

    def test_dossier_shows_a_suggested_follow_up_card_for_both_telescopes(self):
        """The card renders with the dossier, completes with the exposure model, names both
        telescopes with tonight's window and exposure times, carries one placeholder badge
        and the claim boundary, links to the planner and calculator, and fits a phone."""
        candidate = self._followup_dossier()
        self._open_dossier(candidate["event_id"])
        self.page.wait_for_selector("#candidate-workspace [data-followup-card]", state="attached", timeout=30000)
        self.page.wait_for_selector("[data-followup-card] [data-followup-telescope='rbo']", state="attached", timeout=30000)
        card = self.page.locator("[data-followup-card]")
        self.assertEqual(card.locator("h4").inner_text(), "Suggested follow-up")
        self.assertIn(card.get_attribute("data-followup-source"), {"published", "browser"})
        text_ = card.inner_text()
        for phrase in ("Objectives", "Cadence:", "Latest magnitude:", "WIRO 2.3 m", "Red Buttes 0.6 m",
                       "not an exposure-time calculation or observing request", "requests telescope time"):
            self.assertIn(phrase.upper(), text_.upper(), phrase)  # section headings render in capitals
        self.assertRegex(text_, r"(\d+(\.\d)? h above 30°|Not above 30°)")
        self.assertRegex(text_, r"Exposure for S/N 20 at [\d.]+ mag")
        self.assertGreaterEqual(card.locator(".ctas-followup__exposures tbody tr").count(), 10, "five filters on each telescope")
        self.assertIn("Spectroscopy:", text_)
        badge = card.locator("[data-followup-placeholder-badge]")
        self.assertEqual(badge.count(), 1, "exactly one placeholder badge per card")
        self.assertEqual(badge.get_attribute("href"), "tools/exposure/#placeholder-note")
        hrefs = card.locator("a").evaluate_all("els => els.map(e => e.getAttribute('href'))")
        self.assertTrue(any(h.startswith("tonight/?site=wiro") and "ra=" in h for h in hrefs), hrefs)
        self.assertTrue(any(h.startswith("tools/exposure/?telescope=rbo") and "mag=" in h for h in hrefs), hrefs)
        for link in ("tonight/?site=wiro", "tools/exposure/?telescope=rbo", "tools/exposure/#placeholder-note"):
            href = next(h for h in hrefs if h.startswith(link))
            response = self.page.request.get(self.url.rsplit("/", 1)[0] + "/" + href.split("#")[0].split("?")[0])
            self.assertEqual(response.status, 200, href)
        badge.focus()
        self.assertEqual(self.page.evaluate("() => document.activeElement.getAttribute('data-followup-placeholder-badge')"), "", "the badge is keyboard reachable")
        self.assertEqual(self._axe_violations(), [])
        errors = [entry for entry in self.console if entry[0] in {"error", "pageerror"}
                  and not any(host in entry[1] for host in THIRD_PARTY_HOSTS) and not is_third_party_load_failure(entry[1])]
        self.assertEqual(errors, [])
        for width, height in ((320, 568), (390, 844)):
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.wait_for_timeout(300)
                overflow = self.page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1)

    def _axe_violations(self, page=None):
        page = page or self.page
        axe = next(
            (path for path in (ROOT / "node_modules" / "axe-core" / "axe.min.js",
                               ROOT.parent / "node_modules" / "axe-core" / "axe.min.js")
             if path.exists()),
            None,
        )
        if axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        page.add_script_tag(content=axe.read_text())
        result = page.evaluate(
            "async () => await axe.run(document, {runOnly: {type: 'tag',"
            " values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})"
        )
        return [f"{v['id']} ({len(v['nodes'])} nodes): {v['help']}" for v in result["violations"]]

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

    # ------------------------------------------------------------ Track 1 part C: sky tools
    def _first_party_first_load(self):
        """Requests and bytes of the first screen, printed so the budget can be read from the log."""
        rows = self.page.evaluate(
            "() => performance.getEntriesByType('resource').concat(performance.getEntriesByType('navigation'))"
            ".filter(e => e.name.startsWith(location.origin)).map(e => [e.name.replace(location.origin + '/', ''), e.transferSize || 0, e.decodedBodySize || 0])")
        total = sum(row[2] for row in rows)
        print(f"\nfirst screen: {len(rows)} first-party requests, {total:,} decoded bytes ({total / 1024:.0f} KiB)")
        return rows, total

    def test_first_screen_budget_has_no_new_data_requests_and_no_external_sky_runtime(self):
        """The sky tools add two script files and nothing else before a panel is opened: no
        Aladin runtime, no cutout image, no feed or changelog file is requested on load."""
        rows, total = self._first_party_first_load()
        names = [row[0].split("?")[0] for row in rows]
        self.assertIn("ctas/sky.js", names)
        self.assertIn("ctas/changelog.js", names)
        # The budget covers the page's own code, not the catalog data: the data grows with the
        # catalog (2.4 MB on the real 2026-10-22 release against 1.1 MB on the 24-event
        # fixture) and is guarded separately by the summary-only first-screen test. A whole-page
        # cap calibrated on the fixture refused every real deploy from 2026-10-21 to 2026-10-22.
        code = sum(row[2] for row in rows if not row[0].split("?")[0].startswith("ctas/data/"))
        added = sum(row[2] for row in rows if row[0].split("?")[0] in ("ctas/sky.js", "ctas/changelog.js"))
        print(f"first screen: {code:,} decoded bytes of code; sky and changelog scripts {added:,}")
        self.assertLess(code, 1_250_000, "first-screen decoded bytes of code")
        self.assertLess(added, 64 * 1024, "the two Track 1 scripts loaded before any panel opens")
        data_requests = sorted(name for name in self.requests)
        self.assertEqual([n for n in data_requests if n.startswith("feeds/") or n in ("changelog.json", "source-accounting.json")], [],
                         "feeds, changelog and source accounting load only when a panel or the status page asks")
        external = self.page.evaluate("() => performance.getEntriesByType('resource').map(e => e.name).filter(n => !n.startsWith(location.origin))")
        # (the 96-pixel HiPS2FITS thumbnails of the follow-up examples predate this batch)
        self.assertEqual([u for u in external if "aladin" in u or "legacysurvey" in u or "ps1images" in u or ("hips2fits" in u and "width=96" not in u)], [])

    def _coordinate_dossier(self):
        candidate = _published_dossier(lambda row: row.get("ra_deg") is not None and row.get("dec_deg") is not None, lambda c: True)
        if candidate is None:
            self.skipTest("this release has no dossier with coordinates")
        return candidate

    ALADIN_STUB = """
      window.__aladinCalls = [];
      window.A = {
        init: Promise.resolve(),
        aladin: function (el, options) { window.__aladinCalls.push(['aladin', options]); el.innerHTML = '<div class="aladin-stub"><a href="https://aladin.cds.unistra.fr/">Aladin Lite</a></div>';
          return {setBaseImageLayer: function (layer) { window.__aladinCalls.push(['layer', typeof layer === 'string' ? layer : layer.id]); },
                  addCatalog: function (cat) { window.__aladinCalls.push(['catalog', cat.name, cat.sources.length]); },
                  addOverlay: function () { window.__aladinCalls.push(['overlay']); },
                  removeLayer: function () { window.__aladinCalls.push(['removeLayer']); }}; },
        HiPS: function (id, options) { return {id: id, options: options}; },
        catalog: function (options) { return {name: options.name, sources: [], addSources: function (rows) { this.sources = this.sources.concat(rows); }}; },
        marker: function (ra, dec, options) { return {ra: ra, dec: dec, marker: true, options: options}; },
        source: function (ra, dec, data) { return {ra: ra, dec: dec, data: data}; },
        graphicOverlay: function (options) { return {options: options, shapes: [], add: function (shape) { this.shapes.push(shape); window.__aladinCalls.push(['circle', shape.radius]); }, show: function () {}, hide: function () {}}; },
        circle: function (ra, dec, radius, options) { return {ra: ra, dec: dec, radius: radius, options: options}; }
      };
    """

    def _stub_sky_network(self):
        """Serve the pinned Aladin runtime as a recording stub and every cutout as a tiny
        image, so the panels run offline and nothing real is fetched."""
        calls = []
        png = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c636000020000050001e2"
                            "26059b0000000049454e44ae426082")

        def aladin(route):
            calls.append(route.request.url)
            route.fulfill(status=200, content_type="application/javascript", body=self.ALADIN_STUB)

        def image(route):
            calls.append(route.request.url)
            route.fulfill(status=200, content_type="image/png", body=png)

        def listing(route):
            calls.append(route.request.url)
            route.fulfill(status=200, content_type="text/plain", headers={"Access-Control-Allow-Origin": "*"},
                          body="projcell,subcell,ra,dec,filter,mjd,type,filename,shortname\n1,1,1,1,g,1,stack,/x/g.fits,g\n1,1,1,1,i,1,stack,/x/i.fits,i\n1,1,1,1,y,1,stack,/x/y.fits,y\n")
        self.page.route("https://aladin.cds.unistra.fr/**", aladin)
        self.page.route("https://www.legacysurvey.org/**", image)
        self.page.route("https://alasky.cds.unistra.fr/**", image)
        self.page.route("https://ps1images.stsci.edu/cgi-bin/ps1filenames.py**", listing)
        self.page.route("https://ps1images.stsci.edu/cgi-bin/fitscut.cgi**", image)
        return calls

    def test_dossier_sky_panel_carries_finder_chart_samp_and_report_link(self):
        """Every dossier with coordinates gets the finder chart (fetched only when opened, from
        the Legacy Surveys viewer first), the SAMP button with the Safari note, the pinned Aladin
        atlas with four layers, and a prefilled data-problem issue link; axe stays clean."""
        candidate = self._coordinate_dossier()
        calls = self._stub_sky_network()
        self._open_dossier(candidate["event_id"])
        self.page.wait_for_selector("#candidate-workspace [data-sky-tools]", state="attached", timeout=30000)
        # the dossier's own 900-pixel DSS2 preview and the example thumbnails predate this batch
        self.assertEqual([u for u in calls if "hips2fits" not in u], [], "opening a dossier fetches no runtime and no finder cutout")
        tools = self.page.locator("[data-sky-tools]")
        self.assertIn("does not work in Safari", tools.inner_text())
        self.assertEqual(tools.locator("[data-samp-point]").count(), 1)
        report = self.page.locator("#candidate-workspace [data-report-problem]")
        href = report.get_attribute("href")
        from urllib.parse import urlparse, parse_qs, unquote
        parsed = urlparse(href)
        self.assertEqual(parsed.netloc + parsed.path, "github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/issues/new")
        query = parse_qs(parsed.query)
        self.assertEqual(query["title"], [f"{candidate['name']} ({candidate['event_id']})"])
        self.assertEqual(query["labels"], ["data-problem"])
        body = unquote(query["body"][0])
        self.assertIn("Stable event id: " + candidate["event_id"], body)
        self.assertIn("Release checksum (content_release_id): ", body)
        self.assertIn("Panel: ", body)
        # finder chart: opened on request, 2' and 10' fields, Legacy Surveys DR10 first
        finder = self.page.locator("[data-finder]")
        self.assertEqual(finder.count(), 1)
        finder.locator("summary").click()
        self.page.wait_for_function("() => document.querySelectorAll('[data-finder-field][data-finder-state=\"loaded\"]').length === 2", timeout=30000)
        fields = self.page.locator("[data-finder-field]")
        self.assertEqual([fields.nth(i).get_attribute("data-finder-field") for i in range(2)], ["2", "10"])
        self.assertEqual([fields.nth(i).get_attribute("data-finder-source-id") for i in range(2)], ["ls-dr10", "ls-dr10"])
        self.assertTrue(all("layer=ls-dr10" in u for u in calls if "legacysurvey" in u), calls)
        svg = finder.locator("svg.ctas-finder__overlay").first
        self.assertIn("N", svg.text_content()); self.assertIn("E", svg.text_content())
        self.assertIn("epoch J2000", finder.inner_text())
        self.assertIn("north up, east left", finder.inner_text())
        with self.page.expect_popup() as popup_info:
            finder.locator("[data-finder-print]").click()
        popup = popup_info.value
        popup.wait_for_load_state()
        self.assertIn("Finder chart: " + candidate["name"], popup.title())
        self.assertEqual(popup.locator("figure").count(), 2)
        self.assertIn("north up, east left", popup.locator("figcaption").first.inner_text())
        popup.close()
        # the atlas: pinned runtime, marker, layer buttons, fallback logo link kept
        self.page.locator("[data-load-aladin]").click()
        self.page.wait_for_function("() => window.__aladinCalls && window.__aladinCalls.some(c => c[0] === 'catalog')", timeout=30000)
        self.assertTrue(any(u == "https://aladin.cds.unistra.fr/AladinLite/api/v3/3.6.1/aladin.js" for u in calls), calls)
        layers = self.page.locator("#candidate-workspace [data-sky-atlas-tools] [data-sky-layer]")
        self.assertEqual(layers.count(), 4)
        self.assertEqual([layers.nth(i).get_attribute("data-sky-layer") for i in range(4)], ["dss2", "ls-dr10", "ps1", "2mass"])
        layers.nth(1).click()
        self.assertEqual(self.page.evaluate("() => window.__aladinCalls.filter(c => c[0] === 'layer').pop()[1]"), "CDS/P/DESI-Legacy-Surveys/DR10/color")
        self.assertEqual(layers.nth(1).get_attribute("aria-pressed"), "true")
        self.assertIn("pinned 3.6.1", self.page.locator("[data-aladin-status]").inner_text())
        self.assertEqual(self.page.locator(".aladin-stub a[href*='aladin.cds.unistra.fr']").count(), 1, "the Aladin logo link stays")
        self.assertEqual(self._axe_violations(), [])
        errors = [e for e in self.console if e[0] in {"error", "pageerror"} and not any(h in e[1] for h in THIRD_PARTY_HOSTS) and not is_third_party_load_failure(e[1])]
        self.assertEqual(errors, [])

    def test_finder_chart_falls_back_through_the_image_sources(self):
        """When the Legacy Surveys viewer answers nothing usable the chart moves on to
        Pan-STARRS (north of Dec -30) and finally HiPS2FITS DSS2, and says which answered."""
        candidate = self._coordinate_dossier()
        calls = self._stub_sky_network()
        self.page.unroute("https://www.legacysurvey.org/**")
        self.page.route("https://www.legacysurvey.org/**", lambda route: (calls.append(route.request.url), route.fulfill(status=404, body="no")))
        self._open_dossier(candidate["event_id"])
        self.page.wait_for_selector("[data-finder]", state="attached", timeout=30000)
        self.page.locator("[data-finder] summary").click()
        self.page.wait_for_function("() => document.querySelectorAll('[data-finder-field][data-finder-state]').length === 2", timeout=30000)
        expected = "ps1" if float(candidate["dec_deg"]) > -30 else "dss2"
        self.assertEqual(self.page.locator("[data-finder-field]").first.get_attribute("data-finder-source-id"), expected)
        self.assertTrue(any("layer=ls-dr9" in u for u in calls), "DR9 is tried after DR10")
        if expected == "ps1":
            self.assertTrue(any("fitscut.cgi" in u and "red=/x/y.fits" in u for u in calls), calls)
        else:
            self.assertTrue(any("hips2fits" in u for u in calls), calls)

    def test_approximate_localisation_region_is_drawn_only_for_multimessenger_records(self):
        """The region circle comes from the newest retained 90% sky area (or the coordinate
        uncertainty for messenger records) and is labelled approximate; optical records get none."""
        candidate = self._coordinate_dossier()
        self._stub_sky_network()
        self._open_dossier(candidate["event_id"])
        self.page.wait_for_selector("[data-sky-tools]", state="attached", timeout=30000)
        optical = self.page.evaluate("(id) => window.CTASSky.localisation(window.CTASWorkbench.candidateById(id))", candidate["event_id"])
        self.assertIsNone(optical, "an optical record without notices gets no region")
        synthetic = self.page.evaluate("""(id) => {
          const base = window.CTASWorkbench.candidateById(id);
          const gw = Object.assign({}, base, {primary_messenger: 'gravitational-wave', record_role: 'localization-region-alert',
            follow_up: {messenger_signals: [{sky_area_90_sq_deg: 314.159, observed_at: '2026-10-01T00:00:00Z', skymap_url: 'https://gracedb.ligo.org/x.fits'},
                                            {sky_area_90_sq_deg: 1000, observed_at: '2026-09-01T00:00:00Z', retracted: true}]}});
          const nu = Object.assign({}, base, {primary_messenger: 'neutrino', coordinate_error_arcsec: 3600, follow_up: {}});
          return [window.CTASSky.localisation(gw), window.CTASSky.localisation(nu)];
        }""", candidate["event_id"])
        self.assertAlmostEqual(synthetic[0]["radius_deg"], 10.0, places=3)
        self.assertTrue(synthetic[0]["approximate"])
        self.assertEqual(synthetic[0]["skymap_url"], "https://gracedb.ligo.org/x.fits")
        self.assertAlmostEqual(synthetic[1]["radius_deg"], 1.0, places=6)
        self.assertIn("coordinate uncertainty", synthetic[1]["basis"])

    def test_catalog_page_atlas_loads_only_when_opened_and_draws_the_plotted_candidates(self):
        calls = self._stub_sky_network()
        self.page.reload(wait_until="networkidle")
        self.page.wait_for_selector("[data-sky-atlas]", state="attached", timeout=30000)
        self.assertEqual([u for u in calls if "hips2fits" not in u], [], "the atlas requests nothing until opened")
        atlas = self.page.locator("[data-sky-atlas]")
        self.assertIn("Aladin Lite", atlas.locator("summary").inner_text())
        self.assertEqual(atlas.locator("[data-sky-layer]").count(), 4)
        self.assertIn("does not work in Safari", atlas.text_content())
        atlas.locator("summary").click()
        self.page.wait_for_function("() => window.__aladinCalls && window.__aladinCalls.some(c => c[0] === 'catalog')", timeout=30000)
        drawn = self.page.evaluate("() => window.__aladinCalls.filter(c => c[0] === 'catalog').pop()[2]")
        plotted = self.page.evaluate("() => window.CTASApp.getSkyCandidates().filter(c => c.ra_deg !== undefined && c.ra_deg !== null).length")
        self.assertEqual(drawn, plotted, "the atlas draws exactly the plotted set of the current window")
        # widening the window to the whole retained catalog redraws the markers
        self.page.evaluate("() => { document.querySelector('.ctas-window__more').open = true; document.querySelector('[data-sky-days=\"36500\"]').click(); }")
        self.page.wait_for_function("() => window.CTASApp.getSkyCandidates().length > 0 && window.__aladinCalls.filter(c => c[0] === 'catalog').pop()[2] === window.CTASApp.getSkyCandidates().filter(c => c.ra_deg !== undefined && c.ra_deg !== null).length", timeout=60000)
        drawn = self.page.evaluate("() => window.__aladinCalls.filter(c => c[0] === 'catalog').pop()[2]")
        self.assertGreater(drawn, 0)
        self.assertIn("plotted candidates with coordinates", self.page.locator("[data-sky-atlas-status]").inner_text())
        self.assertEqual(self.page.locator("[data-sky-atlas] [data-samp-events]").count(), 1)
        self.assertEqual(self._axe_violations(), [])

    def test_changelog_panel_loads_the_file_only_when_opened(self):
        self.page.wait_for_selector("[data-changelog-panel]", state="attached", timeout=30000)
        self.assertNotIn("changelog.json", self.requests)
        panel = self.page.locator("[data-changelog-panel]")
        self.assertIn("Plain-words changelog", panel.locator("summary").text_content())
        self.page.evaluate("() => { document.getElementById('catalog-changes').open = true; document.getElementById('ctas-reference').open = true; }")
        panel.locator("summary").click()
        self.page.wait_for_function("() => /claim|No release-to-release|Release checksum/.test(document.querySelector('[data-changelog-body]').textContent)", timeout=30000)
        self.assertIn("changelog.json", self.requests)
        body = panel.locator("[data-changelog-body]").inner_text()
        self.assertTrue("not a new discovery" in body or "No release-to-release changes" in body, body[:200])
        self.assertEqual(self._axe_violations(), [])


@unittest.skipUnless((DATA / "status.json").exists() and (ROOT / "ctas" / "status.html").exists(),
                     "no generated release in ctas/data; run the exporter first")
class StatusPageTests(unittest.TestCase):
    """ctas/status.html: the four denominators per source, the watchdog view, the changelog,
    the feeds and the machine descriptions, read from the release's own files."""

    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright is not installed: {exc}") from exc
        port = _free_port()
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), functools.partial(_QuietHandler, directory=str(ROOT)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.url = f"http://127.0.0.1:{port}/ctas/status.html"
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = isolate_browser(cls.playwright.chromium.launch(), cls.url)
        except Exception as exc:  # pragma: no cover
            cls.playwright.stop(); cls.server.shutdown()
            raise unittest.SkipTest(f"no Chromium available: {exc}") from exc

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.playwright.stop(); cls.server.shutdown(); cls.server.server_close()

    def setUp(self):
        self.page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        self.console, self.requests = [], []
        self.page.on("console", lambda m: self.console.append((m.type, m.text)))
        self.page.on("pageerror", lambda e: self.console.append(("pageerror", str(e))))
        self.page.on("response", lambda r: self.requests.append(r.url.split("/ctas/", 1)[-1].split("?")[0]) if "/ctas/" in r.url else None)
        self.page.goto(self.url, wait_until="networkidle", timeout=60000)
        self.page.wait_for_function("() => document.querySelectorAll('#ctas-status-sources table').length > 0", timeout=60000)

    def tearDown(self):
        self.page.close()

    def test_status_page_structure_and_no_errors(self):
        errors = [e for e in self.console if e[0] in {"error", "pageerror"} and not any(h in e[1] for h in THIRD_PARTY_HOSTS) and not is_third_party_load_failure(e[1])]
        self.assertEqual(errors, [])
        headings = self.page.evaluate("() => [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map(h => h.tagName)")
        self.assertEqual(headings.count("H1"), 1)
        previous, skips = 0, []
        for tag in headings:
            level = int(tag[1])
            if previous and level > previous + 1:
                skips.append((previous, tag))
            previous = level
        self.assertEqual(skips, [])
        self.assertNotIn("data/certification.json", self.requests, "the 700 KB report is linked, not fetched")
        for name in ("data/status.json", "data/source-accounting.json", "data/changelog.json", "data/feeds/index.json", "data/source-universe.json"):
            self.assertIn(name, self.requests)
        self.assertEqual([r for r in self.requests if r.startswith("data/feeds/") and r != "data/feeds/index.json"], [], "no feed file is fetched, only linked")
        for width, height in VIEWPORTS:
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self.page.wait_for_timeout(300)
                self.assertLessEqual(self.page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)

    def test_every_table_has_a_text_summary_and_the_denominators_reconcile(self):
        tables = self.page.locator("table")
        self.assertGreaterEqual(tables.count(), 6)
        for index in range(tables.count()):
            wrap = tables.nth(index).locator("xpath=..")
            summary_id = wrap.get_attribute("aria-labelledby")
            self.assertTrue(summary_id, f"table {index} has no summary")
            self.assertGreater(len(self.page.locator(f"#{summary_id}").text_content()), 40)
            self.assertTrue(tables.nth(index).locator("caption").count())
        status = json.loads((DATA / "status.json").read_text())
        accounting = json.loads((DATA / "source-accounting.json").read_text())
        self.assertEqual(accounting["totals"]["applicable_source_evaluations"], status["source_accounting"]["applicableSourceEvaluations"])
        source_rows = self.page.locator("#ctas-status-sources table").first.locator("tbody tr")
        self.assertEqual(source_rows.count(), accounting["source_count"])
        text = self.page.locator("#sources-summary").inner_text()
        self.assertIn(f"{status['source_accounting']['applicableSourceEvaluations']:,} applicable evaluations", text)
        self.assertIn("Declared", self.page.locator("#ctas-status-sources table").first.inner_text())
        freshness = self.page.locator("#ctas-status-freshness").inner_text()
        self.assertIn("valid until", freshness)
        self.assertIn("freshness-watchdog", freshness)
        self.assertIn("4 h", freshness)
        feeds = self.page.locator("#ctas-status-feeds").inner_text()
        self.assertIn("HEALPix order-2", feeds)
        self.assertGreaterEqual(self.page.locator("#ctas-status-feeds a[href$='.xml']").count(), 10)
        machine = self.page.locator("#ctas-status-machine")
        for href in ("openapi.yaml", "data/provenance.json", "data/links/manifest.json", "data/bulk-downloads.json", "schemas/status.schema.json"):
            self.assertEqual(machine.locator(f"a[href='{href}']").count(), 1, href)

    def test_status_page_passes_axe(self):
        axe = next((path for path in (ROOT / "node_modules" / "axe-core" / "axe.min.js", ROOT.parent / "node_modules" / "axe-core" / "axe.min.js") if path.exists()), None)
        if axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        self.page.add_script_tag(content=axe.read_text())
        result = self.page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
        self.assertEqual([f"{v['id']} ({len(v['nodes'])} nodes): {v['help']}" for v in result["violations"]], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

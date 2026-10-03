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
import json
import re
import socket
import sys
import threading
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "ctas" / "data"
SPECTRUM_FIXTURES = ROOT / "tests" / "fixtures" / "spectra"
TNS_TEXT_FILE = re.compile(r"^https://www\.wis-tns\.org/system/files/uploaded/[A-Za-z0-9_\-./%+]+$")
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

    def _plot_first_spectrum(self, page=None):
        page = page or self.page
        page.evaluate("""() => { const panel = document.querySelector('[data-dossier-view="spectra"]'); panel.open = true;
          panel.querySelectorAll('details.ctas-spectrum-record').forEach((d) => { d.open = true; }); }""")
        button = page.locator("[data-plot-spectrum]").first
        button.scroll_into_view_if_needed()
        button.click()

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
        self._plot_first_spectrum()
        self.page.wait_for_selector("[data-spectrum-svg] svg", timeout=30000)
        from urllib.parse import quote
        self.assertEqual(calls, ["https://relay.test/?url=" + quote(spectrum["public_download_url"], safe="")])
        status = self.page.locator("[data-spectrum-status]").first.inner_text()
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

    def test_spectrum_problems_are_explained_in_plain_words(self):
        candidate = self._spectrum_dossier()
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
                    self._plot_first_spectrum(page)
                    page.wait_for_function(
                        "() => document.querySelector('[data-spectrum-status].is-problem')", timeout=30000)
                    self.assertIn(expected, page.locator("[data-spectrum-status]").first.inner_text())
                    self.assertEqual(page.locator("[data-spectrum-svg] svg").count(), 0)
                finally:
                    page.close()

    def test_spectrum_without_a_relay_says_so(self):
        candidate = self._spectrum_dossier()
        self.page.route("**/live-config.json", lambda route: route.fulfill(
            status=200, content_type="application/json", body="{}"))
        self._open_dossier(candidate["event_id"])
        self._plot_first_spectrum()
        self.page.wait_for_function("() => document.querySelector('[data-spectrum-status].is-problem')", timeout=30000)
        self.assertIn("relay is not configured", self.page.locator("[data-spectrum-status]").first.inner_text())

    def test_plotted_spectrum_is_accessible_and_fits_a_phone(self):
        candidate = self._spectrum_dossier()
        fixture = (SPECTRUM_FIXTURES / "longslit-nm.dat").read_text()
        self._route_relay(lambda route: route.fulfill(
            status=200, content_type="text/plain", body=fixture, headers={"Access-Control-Allow-Origin": "*"}))
        for width, height in ((320, 568), (390, 844)):
            with self.subTest(viewport=f"{width}x{height}"):
                self.page.set_viewport_size({"width": width, "height": height})
                self._open_dossier(candidate["event_id"])
                self._plot_first_spectrum()
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


if __name__ == "__main__":
    unittest.main(verbosity=2)

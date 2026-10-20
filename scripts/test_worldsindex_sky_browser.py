#!/usr/bin/env python3
"""The WorldsIndex Sky tab, catalog atlas, report-a-problem link and status page, in a real browser.

Framework v2 Track 1, part D. Every external service is replaced by a local stand-in (no
network): the CDS Aladin Lite runtime by a stub that records what the page asked it to draw,
the finder-chart image services by tiny PNGs (the DR10 cutout answers 404 so the fallback order
is visible), and the SAMP Web Profile hub on localhost:21012 by a route that records the
XML-RPC calls. The test checks that the Sky tab exists only for objects with coordinates, that
the host marker and the catalog neighbours within 5' reach the atlas, that the finder chart
falls back in the declared order and offers a print view, that the SAMP buttons send
coord.pointAt.sky and table.load.votable with the object's rows, that the report link is a
prefilled data-problem issue naming the stable id and release checksum, that the status page
has one h1, an unbroken heading outline and a text summary before every table, that nothing
scrolls sideways at phone widths, and that axe-core finds no WCAG 2.1 AA violation (when
installed).
"""
from __future__ import annotations

import base64
import contextlib
import functools
import gzip
import http.server
import json
import re
import socket
import threading
import unittest
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent.parent
CATALOG = ROOT / "worldsindex" / "data" / "catalog-index.json.gz"
MANIFEST = ROOT / "worldsindex" / "data" / "manifest.json"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
ALADIN_STUB = """
window.__aladin = {layers: [], catalogs: [], created: []};
window.A = {
  init: Promise.resolve(),
  aladin(div, options) { window.__aladin.created.push(options); div.textContent = 'aladin stub'; return {
    setBaseImageLayer(layer) { window.__aladin.layers.push(typeof layer === 'string' ? layer : layer.id); },
    addCatalog(catalog) { window.__aladin.catalogs.push(catalog); }, removeLayer(catalog) { window.__aladin.catalogs = window.__aladin.catalogs.filter((c) => c !== catalog); }, addOverlay() {} }; },
  catalog(options) { return {name: options.name, color: options.color, sources: [], addSources(list) { this.sources.push(...list); }}; },
  marker(ra, dec, data) { return {ra, dec, data}; }, source(ra, dec, data) { return {ra, dec, data}; }, HiPS(id) { return {id}; },
};
"""
SAMP_REGISTER = ("<?xml version='1.0'?><methodResponse><params><param><value><struct>"
                 "<member><name>samp.private-key</name><value><string>key-1</string></value></member>"
                 "<member><name>samp.hub-id</name><value><string>hub</string></value></member></struct></value></param></params></methodResponse>")
SAMP_OK = "<?xml version='1.0'?><methodResponse><params><param><value><string></string></value></param></params></methodResponse>"


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


def _catalog():
    with gzip.open(CATALOG, "rt", encoding="utf-8") as stream:
        return json.load(stream)


@unittest.skipUnless(CATALOG.exists() and MANIFEST.exists(), "the published WorldsIndex catalog is not in this checkout")
class SkyBrowserTests(unittest.TestCase):
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
        catalog = _catalog()
        cls.projection = catalog["projectionId"]
        objects = catalog["objects"]
        cls.with_coords = next(o for o in objects if o["objectId"] == "object-trappist-1-b") if any(o["objectId"] == "object-trappist-1-b" for o in objects) else next(o for o in objects if o["raDeg"] is not None)
        cls.without_coords = next((o for o in objects if o["raDeg"] is None or o["decDeg"] is None), None)
        cls.manifest = json.loads(MANIFEST.read_text())
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

    def open(self, path, scheme="dark", width=1280, aladin_status=200):
        page = self.browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme)
        errors, self.image_urls, self.samp_calls = [], [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # First-party console errors count; a 404 for cloud-run.json does not, because the marker exists
        # only on releases a cloud run published (a local build has none) and the page says so.
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "127.0.0.1" in (m.location or {}).get("url", "")
                and not (m.location or {}).get("url", "").endswith("cloud-run.json") else None)
        page.route("https://aladin.cds.unistra.fr/AladinLite/api/v3/**", lambda route: route.fulfill(
            status=aladin_status, content_type="application/javascript", body=ALADIN_STUB if aladin_status == 200 else ""))

        def image(route):
            url = route.request.url
            self.image_urls.append(url)
            if "layer=ls-dr10" in url:
                route.fulfill(status=404, content_type="text/plain", body="no coverage")
            else:
                route.fulfill(status=200, content_type="image/png", body=PNG)

        page.route("https://www.legacysurvey.org/**", image)
        page.route("https://ps1images.stsci.edu/**", image)
        page.route("https://alasky.cds.unistra.fr/**", image)

        def samp(route):
            body = route.request.post_data or ""
            self.samp_calls.append(body)
            route.fulfill(status=200, content_type="text/xml", headers={"Access-Control-Allow-Origin": "*"},
                          body=SAMP_REGISTER if "samp.webhub.register" in body else SAMP_OK)

        page.route("http://localhost:21012/**", samp)
        page.goto(f"{self.base}{path}", wait_until="networkidle", timeout=90000)
        return page, errors

    def open_object(self, object_id, **kwargs):
        page, errors = self.open(f"/worldsindex/?object={object_id}&section=object", **kwargs)
        page.wait_for_function("!document.querySelector('#object-dossier').hidden", timeout=30000)
        return page, errors

    # ---- Sky tab --------------------------------------------------------------------------
    def test_sky_tab_only_for_objects_with_coordinates(self):
        page, errors = self.open_object(self.with_coords["objectId"])
        self.assertFalse(page.eval_on_selector("#object-tab-sky", "e => e.hidden"))
        page.click("#object-tab-sky")
        self.assertFalse(page.eval_on_selector("#tab-sky", "e => e.hidden"))
        text = page.text_content("#tab-sky")
        self.assertIn("Sky at the catalog position", text)
        self.assertIn("ICRS", text)
        self.assertEqual(page.locator("#tab-sky h2").count(), 1)
        self.assertIn("Aladin Lite is CDS software (LGPL-3.0-or-later)", text)
        self.assertEqual(errors, [])
        page.close()
        if self.without_coords is None:
            self.skipTest("every object in this release has coordinates")
        page, errors = self.open_object(self.without_coords["objectId"])
        self.assertTrue(page.eval_on_selector("#object-tab-sky", "e => e.hidden"), "no coordinates, no Sky tab")
        self.assertEqual(page.eval_on_selector("#tab-sky", "e => e.innerHTML"), "")
        self.assertFalse(page.eval_on_selector("#report-problem", "e => e.hidden"), "the report link does not need coordinates")
        self.assertEqual(errors, [])
        page.close()

    def test_atlas_draws_the_host_and_the_catalog_neighbours(self):
        page, errors = self.open_object(self.with_coords["objectId"])
        page.click("#object-tab-sky")
        self.assertEqual(page.evaluate("window.__aladin === undefined"), True, "nothing loads before the atlas is opened")
        page.click("#tab-sky [data-sky-atlas] summary")
        page.wait_for_function("window.__aladin && window.__aladin.catalogs.length >= 1", timeout=20000)
        created = page.evaluate("window.__aladin.created[0]")
        self.assertEqual(created["survey"], "CDS/P/DSS2/color")
        self.assertEqual(created["cooFrame"], "ICRSd")
        ra, dec = [float(v) for v in created["target"].split()]
        self.assertAlmostEqual(ra, self.with_coords["raDeg"], places=5)
        self.assertAlmostEqual(dec, self.with_coords["decDeg"], places=5)
        catalogs = page.evaluate("window.__aladin.catalogs.map((c) => ({name: c.name, n: c.sources.length, first: c.sources[0]}))")
        self.assertEqual(catalogs[0]["name"], "WorldsIndex catalog position")
        self.assertEqual(catalogs[0]["n"], 1)
        rows = page.locator("#tab-sky tbody tr").count()
        if rows:
            self.assertEqual(catalogs[1]["name"], "Catalog objects within 5′")
            self.assertEqual(catalogs[1]["n"], page.evaluate("document.querySelector('#tab-sky [data-sky-nearby-summary]').textContent.match(/^(\\d+) other/)[1] * 1"))
            self.assertIn("same host", page.text_content("#tab-sky tbody"))
        status = page.text_content("#tab-sky [data-sky-atlas-status]")
        self.assertIn("pinned 3.6.1", status)
        # Layer buttons: the four declared layers; one click sets the base layer.
        labels = page.eval_on_selector_all("#tab-sky [data-sky-layer]", "els => els.map(e => e.textContent)")
        self.assertEqual(labels[:4], ["DSS2 colour", "Legacy Surveys DR10", "Pan-STARRS DR1 colour", "2MASS colour"])
        page.click("#tab-sky [data-sky-layer='ls-dr10']")
        self.assertEqual(page.evaluate("window.__aladin.layers.at(-1)"), "CDS/P/DESI-Legacy-Surveys/DR10/color")
        self.assertEqual(page.get_attribute("#tab-sky [data-sky-layer='ls-dr10']", "aria-pressed"), "true")
        self.assertEqual(errors, [])
        page.close()

    def test_atlas_failure_is_plain_words(self):
        page, errors = self.open_object(self.with_coords["objectId"], aladin_status=404)
        page.click("#object-tab-sky")
        page.click("#tab-sky [data-sky-atlas] summary")
        page.wait_for_function("document.querySelector('#tab-sky [data-sky-atlas-status]').textContent.includes('could not be loaded')", timeout=20000)
        self.assertIn("finder chart and the nearby list remain usable", page.text_content("#tab-sky [data-sky-atlas-status]"))
        self.assertEqual(errors, [])
        page.close()

    def test_finder_chart_falls_back_in_order_and_prints(self):
        page, errors = self.open_object(self.with_coords["objectId"])
        page.click("#object-tab-sky")
        self.assertEqual(self.image_urls, [], "no image is fetched before the finder is opened")
        page.click("#tab-sky [data-finder] summary")
        page.wait_for_function("[...document.querySelectorAll('#tab-sky [data-finder-field]')].every((f) => f.dataset.finderState)", timeout=20000)
        states = page.eval_on_selector_all("#tab-sky [data-finder-field]", "els => els.map(e => [e.dataset.finderField, e.dataset.finderState, e.dataset.finderSourceId])")
        self.assertEqual(states, [["2", "loaded", "ls-dr9"], ["10", "loaded", "ls-dr9"]], "DR10 answered 404, DR9 answered")
        dr10 = [u for u in self.image_urls if "layer=ls-dr10" in u]
        self.assertEqual(len(dr10), 2)
        self.assertIn(f"ra={self.with_coords['raDeg']:.6f}", dr10[0])
        self.assertTrue(page.is_enabled("#tab-sky [data-finder-print]"))
        with page.expect_popup() as popup:
            page.click("#tab-sky [data-finder-print]")
        printed = popup.value
        printed.wait_for_load_state()
        self.assertIn(self.with_coords["name"], printed.title())
        self.assertIn("north up, east left", printed.content())
        self.assertIn(self.with_coords["objectId"], printed.content())
        printed.close()
        self.assertEqual(errors, [])
        page.close()

    def test_samp_sends_the_position_and_the_object_rows(self):
        page, errors = self.open_object(self.with_coords["objectId"])
        page.click("#object-tab-sky")
        self.assertIn("not work in Safari", page.text_content("#tab-sky"))
        page.click("#tab-sky [data-samp-point]")
        page.wait_for_function("document.querySelector('#tab-sky [data-samp-status]').textContent.startsWith('Pointed')", timeout=20000)
        self.assertIn("samp.webhub.register", self.samp_calls[0])
        self.assertIn("coord.pointAt.sky", self.samp_calls[1])
        self.assertIn(f"<string>{self.with_coords['raDeg']}</string>", self.samp_calls[1])
        page.click("#tab-sky [data-samp-table]")
        page.wait_for_function("document.querySelector('#tab-sky [data-samp-status]').textContent.startsWith('Sent')", timeout=20000)
        table_call = self.samp_calls[-1]
        self.assertIn("table.load.votable", table_call)
        data_url = re.search(r"<string>(data:application/x-votable\+xml;base64,[^<]+)</string>", table_call).group(1)
        votable = base64.b64decode(data_url.split(",", 1)[1]).decode()
        self.assertIn('<VOTABLE version="1.4"', votable)
        self.assertIn(f"<TD>{self.with_coords['objectId']}</TD>", votable)
        self.assertIn("release selection", votable)
        self.assertRegex(votable, r'nrows="[1-9]\d*"')
        self.assertEqual(errors, [])
        page.close()

    # ---- Report a problem -----------------------------------------------------------------
    def test_report_problem_link_is_a_prefilled_data_problem_issue(self):
        page, errors = self.open_object(self.with_coords["objectId"])
        href = page.get_attribute("#report-problem", "href")
        parsed = urlparse(href)
        self.assertEqual(parsed.netloc + parsed.path, "github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/issues/new")
        query = parse_qs(parsed.query)
        self.assertEqual(query["title"], [f"{self.with_coords['name']} ({self.with_coords['objectId']})"])
        self.assertEqual(query["labels"], ["data-problem"])
        body = query["body"][0]
        self.assertIn(f"Stable object id: {self.with_coords['objectId']}", body)
        self.assertIn(f"Atlas projection id: {self.projection}", body)
        self.assertIn(f"Release checksum (atlas SHA-256): {self.manifest['atlasSha256']}", body)
        self.assertIn("Panel: Overview", body)
        page.click("#object-tab-measurements")
        self.assertIn("Panel: Measurements", unquote(page.get_attribute("#report-problem", "href")))
        self.assertEqual(page.get_attribute("#report-problem", "target"), "_blank")
        self.assertEqual(errors, [])
        page.close()

    # ---- Catalog page -----------------------------------------------------------------------
    def test_catalog_page_atlas_and_footer_links(self):
        page, errors = self.open("/worldsindex/?section=sky")
        page.wait_for_function("document.querySelector('#atlas-loading').hidden", timeout=30000)
        self.assertEqual(page.locator("#catalog-sky [data-catalog-sky]").count(), 1)
        self.assertEqual(page.evaluate("window.__aladin === undefined"), True)
        page.click("#catalog-sky [data-catalog-sky] summary")
        page.wait_for_function("window.__aladin && window.__aladin.catalogs.length === 1", timeout=20000)
        drawn = page.evaluate("window.__aladin.catalogs[0].sources.length")
        self.assertGreater(drawn, 100)
        self.assertLessEqual(drawn, 5000)
        self.assertIn("filtered objects with coordinates drawn as markers", page.text_content("#catalog-sky [data-sky-atlas-status]"))
        narrow = page.evaluate("() => { const s = document.querySelector('#atlas-status'); const option = [...s.options].find((o) => ['REFUTED', 'CONTROVERSIAL', 'FALSE_POSITIVE'].includes(o.value)); s.value = option.value; s.dispatchEvent(new Event('change', {bubbles: true})); return option.value; }")
        self.assertTrue(narrow)
        page.wait_for_timeout(300)
        self.assertLess(page.evaluate("window.__aladin.catalogs[0].sources.length"), drawn, "the filter changed the marker set")
        self.assertEqual(page.evaluate("window.__aladin.catalogs.length"), 1, "the marker layer is replaced, not stacked")
        page.click("#catalog-sky [data-samp-objects]")
        page.wait_for_function("document.querySelector('#catalog-sky [data-samp-status]').textContent.startsWith('Sent')", timeout=20000)
        self.assertIn("data/exports/objects.vot.gz", self.samp_calls[-1])
        hrefs = page.eval_on_selector_all("footer .footer-data-links a", "els => els.map(e => e.getAttribute('href'))")
        for expected in ("status.html", "data/changelog.md", "data/feed.json", "data/feed.xml", "data/exports/index.json", "openapi.yaml"):
            self.assertIn(expected, hrefs)
        self.assertEqual(page.locator("h1:visible").count(), 1)
        self.assertEqual(errors, [])
        page.close()

    # ---- Status page ------------------------------------------------------------------------
    def test_status_page_structure_and_content(self):
        page, errors = self.open("/worldsindex/status.html")
        page.wait_for_function("document.querySelector('#status-loading').hidden", timeout=30000)
        self.assertEqual(page.locator("h1").count(), 1)
        levels = page.eval_on_selector_all("h1, h2, h3, h4", "els => els.map(e => Number(e.tagName[1]))")
        for previous, current in zip(levels, levels[1:]):
            self.assertLessEqual(current - previous, 1, f"heading outline jumps: {levels}")
        # Every table is preceded by a text summary.
        orphan = page.evaluate("[...document.querySelectorAll('.table-wrap')].filter((w) => !(w.previousElementSibling && w.previousElementSibling.matches('p.table-summary, h3, p'))).length")
        self.assertEqual(orphan, 0)
        self.assertGreaterEqual(page.locator("#sources-table tbody tr").count(), 40)
        text = page.text_content("main")
        self.assertIn("declared sources", text)
        self.assertIn(self.manifest["atlasSha256"], text)
        self.assertRegex(text, r"Monitor state \w+")
        for sample in ("Declared", "Applicable", "Executed", "Data-bearing", "Last attempt"):
            self.assertIn(sample, page.text_content("#sources-table thead"))
        self.assertIn("identity", text.lower())
        self.assertGreaterEqual(page.locator("#schema-links a").count(), 16)
        schema_hrefs = page.eval_on_selector_all("#schema-links a", "els => els.map(e => e.getAttribute('href'))")
        for href in schema_hrefs:
            self.assertTrue((ROOT / "worldsindex" / href).is_file(), href)
        listed = {Path(h).name for h in schema_hrefs}
        on_disk = {p.name for p in (ROOT / "worldsindex" / "schemas").glob("*.schema.json")}
        self.assertEqual(listed, on_disk, "every schema file is linked and every link has a file")
        self.assertIn("exports/index.json", text)
        self.assertIn("provenance.json", text)
        self.assertEqual(errors, [])
        for width in (320, 390, 768):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 800})
                page.wait_for_timeout(200)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
        page.close()

    def test_no_sideways_scroll_on_the_sky_tab(self):
        page, _ = self.open_object(self.with_coords["objectId"], width=390)
        page.click("#object-tab-sky")
        page.click("#tab-sky [data-finder] summary")
        page.wait_for_function("[...document.querySelectorAll('#tab-sky [data-finder-field]')].every((f) => f.dataset.finderState)", timeout=20000)
        for width in (320, 390, 768):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 800})
                page.wait_for_timeout(250)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
        page.close()

    def test_axe_reports_no_wcag_violation(self):
        axe = next((p for p in (ROOT / "node_modules" / "axe-core" / "axe.min.js", ROOT.parent / "node_modules" / "axe-core" / "axe.min.js",
                                ROOT.parent.parent / "node_modules" / "axe-core" / "axe.min.js", ROOT.parent.parent.parent / "node_modules" / "axe-core" / "axe.min.js") if p.exists()), None)
        if axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        run = "async (scope) => await axe.run(scope, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})"
        for scheme in ("dark", "light"):
            with self.subTest(scheme=scheme, page="object sky tab"):
                page, _ = self.open_object(self.with_coords["objectId"], scheme=scheme)
                page.click("#object-tab-sky")
                page.click("#tab-sky [data-finder] summary")
                page.wait_for_function("[...document.querySelectorAll('#tab-sky [data-finder-field]')].every((f) => f.dataset.finderState)", timeout=20000)
                page.add_script_tag(content=axe.read_text())
                result = page.evaluate(run, "#tab-sky")
                self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']} {[n['target'] for n in v['nodes']][:3]}" for v in result["violations"]], [])
                page.close()
            with self.subTest(scheme=scheme, page="status"):
                page, _ = self.open("/worldsindex/status.html", scheme=scheme)
                page.wait_for_function("document.querySelector('#status-loading').hidden", timeout=30000)
                page.add_script_tag(content=axe.read_text())
                result = page.evaluate(run, "main")
                self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']} {[n['target'] for n in v['nodes']][:3]}" for v in result["violations"]], [])
                page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

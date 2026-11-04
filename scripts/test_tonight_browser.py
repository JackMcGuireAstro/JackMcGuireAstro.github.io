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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_test_net import isolate_browser, is_third_party_load_failure  # noqa: E402  (third-party requests are blocked)

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
            cls.browser = isolate_browser(cls.playwright.chromium.launch(), cls.base)
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
        return [e for e in errors if not any(h in e for h in THIRD_PARTY_HOSTS) and not is_third_party_load_failure(e)]

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

    # ---- Track 4: kept sites, the cross-product list, the 30-night list and the run sheet
    def test_top_tonight_and_run_sheet_link(self):
        page, errors = self.open()
        page.select_option("#list", "both")
        self.assertTrue(page.is_visible("#sigmamax"))
        page.click("button[type=submit]")
        page.wait_for_function("document.querySelector('#results h3')?.textContent === 'Transients'", timeout=30000)
        self.assertIn("Transits", page.inner_text("#results"))
        link = page.locator("[data-runsheet-link]")
        if link.count():
            href = link.first.get_attribute("href")
            self.assertTrue(href.startswith("runsheet.html?site=wiro&date="), href)
        self.assertEqual(self.first_party(errors), [])
        page.close()

    def test_next_thirty_nights_list(self):
        page, errors = self.open()
        page.select_option("#list", "next30")
        page.click("button[type=submit]")
        # Wait for the 30-night list itself, not the paragraph of the list shown on load (a race under load).
        page.wait_for_function("(document.querySelector('#results p')?.textContent || '').includes('deepest first') || document.querySelector('#status').textContent.includes('not published')", timeout=30000)
        if (ROOT / "worldsindex" / "data" / "observing-windows.json").exists():
            self.assertGreater(page.locator("#results table").count(), 0)
            self.assertIn("deepest first", page.inner_text("#results p"))
        else:
            self.assertIn("not published yet", page.text_content("#status"))
        page.select_option("#site", "custom")
        page.fill("#lat", "-30.24"); page.fill("#lon", "-70.74"); page.fill("#tz", "America/Santiago")
        page.click("button[type=submit]")
        page.wait_for_timeout(500)
        if (ROOT / "worldsindex" / "data" / "observing-windows.json").exists():
            self.assertIn("covers", page.inner_text("#results"))
            self.assertEqual(self.first_party(errors), [])
        else:  # the release predates the list: its 404 is the expected answer
            self.assertEqual([e for e in self.first_party(errors) if "404" not in e], [])
        page.close()

    def test_a_kept_site_appears_in_the_list(self):
        page, _ = self.open()
        page.select_option("#site", "custom")
        page.fill("#lat", "41.3"); page.fill("#lon", "-105.6"); page.fill("#tz", "America/Denver"); page.fill("#elev", "2200")
        page.fill("#sitename", "Test Backyard")
        page.click("#savesite")
        page.wait_for_function("document.querySelector('#status').textContent.includes('kept in this browser only')", timeout=10000)
        self.assertEqual(page.input_value("#site"), "custom-test-backyard")
        stored = page.evaluate("JSON.parse(localStorage.getItem('jm.observatories.v1'))")
        self.assertEqual(stored, [{"id": "custom-test-backyard", "name": "Test Backyard", "lat": 41.3, "lon": -105.6, "elevation": 2200, "tz": "America/Denver", "custom": True}])
        page.close()

    def test_run_sheet(self):
        summary = json.loads((ROOT / "ctas" / "data" / "live-summary.json").read_text())
        at = {c: i for i, c in enumerate(summary["candidate_columns"])}
        ids = [r[at["event_id"]] for r in summary["candidate_rows"] if r[at["record_role"]] == "follow-up-target-candidate" and r[at["ra_deg"]] is not None][:2]
        for width, height in ((390, 844), (1280, 900)):
            page = self.browser.new_page(viewport={"width": width, "height": height}, timezone_id="America/Denver")
            errors = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{self.base}/tonight/runsheet.html?site=wiro&date=2026-10-03&events={','.join(ids)}", wait_until="networkidle", timeout=60000)
            page.wait_for_selector("#sheet article, #sheet p", timeout=30000)
            self.assertEqual(page.locator("#sheet article").count(), len(ids))
            first = page.locator("#sheet article").first
            self.assertIn("RA, Dec", first.text_content())
            self.assertGreater(first.locator(".chart svg").count(), 0)
            self.assertIn("WIRO", first.locator(".sheet-exp").inner_text())
            self.assertIn("Landolt", page.inner_text("#standards-title + p"))
            overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
            self.assertLessEqual(overflow, 1, f"{width}px")
            self.assertEqual(self.first_party(errors), [], f"{width}px")
            page.close()
        page = self.browser.new_page(viewport={"width": 1280, "height": 900}, timezone_id="America/Denver")
        page.goto(f"{self.base}/tonight/runsheet.html?site=wiro&date=2026-10-03", wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#sheet article, #sheet p", timeout=30000)
        self.assertIn("up longest tonight", page.inner_text("#sheet-head"))
        axe = next((p for p in (ROOT / "node_modules" / "axe-core" / "axe.min.js",
                                ROOT.parent / "node_modules" / "axe-core" / "axe.min.js") if p.exists()), None)
        if axe is not None:
            page.add_script_tag(content=axe.read_text())
            result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
            self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']}" for v in result["violations"]], [])
        page.close()

    # ---- the shared run list (tools/lib/run-cart.js): CTAS, WorldsIndex and Tonight feed the run sheet
    def _context(self, width=1280, height=900):
        ctx = self.browser.new_context(viewport={"width": width, "height": height}, timezone_id="America/Denver")
        errors = []
        ctx.on("page", lambda p: (p.on("console", lambda m: errors.append(m.text) if m.type == "error" else None),
                                  p.on("pageerror", lambda e: errors.append(str(e)))))
        return ctx, errors

    def _cart(self, page):
        return page.evaluate("JSON.parse(localStorage.getItem('jm.runlist.v1') || '[]')")

    def test_run_list_across_pages_and_the_run_sheet(self):
        summary = json.loads((ROOT / "ctas" / "data" / "live-summary.json").read_text())
        at = {c: i for i, c in enumerate(summary["candidate_columns"])}
        row = next(r for r in summary["candidate_rows"] if r[at["record_role"]] == "follow-up-target-candidate" and r[at["ra_deg"]] is not None)
        event_id, event_name = row[at["event_id"]], row[at["name"]]
        index = json.loads(gzip.decompress((ROOT / "worldsindex" / "data" / "catalog-index.json.gz").read_bytes()))
        planet = next(o for o in index["objects"] if o.get("name") == "HD 209458 b")
        ctx, errors = self._context()
        page = ctx.new_page()
        # 1. a CTAS dossier
        page.goto(f"{self.base}/ctas.html?event={event_id}", wait_until="networkidle", timeout=90000)
        button = page.locator("#dossier [data-runcart-toggle]").first
        button.wait_for(state="visible", timeout=60000)
        self.assertEqual(button.inner_text(), "Add to run list")
        button.click()
        page.wait_for_function("document.querySelector('#dossier [data-runcart-toggle]').getAttribute('aria-pressed') === 'true'", timeout=10000)
        self.assertEqual(page.locator("#dossier [data-runcart-toggle]").first.inner_text(), "✓ In run list")
        self.assertIn("Run list (1)", page.locator(".nav [data-runcart-badge]").inner_text())
        # 2. a WorldsIndex planet page
        page.goto(f"{self.base}/worldsindex/?object={planet['objectId']}&section=object", wait_until="networkidle", timeout=90000)
        page.wait_for_function("window.RunCart && !document.getElementById('add-to-runlist').hidden", timeout=60000)
        page.click("#add-to-runlist")
        page.wait_for_function("document.getElementById('add-to-runlist').getAttribute('aria-pressed') === 'true'", timeout=10000)
        self.assertIn("Run list (2)", page.locator(".site-journey [data-runcart-badge]").inner_text())
        # 3. a transit row of the Tonight planner (the fixture ephemerides)
        page.route("**/worldsindex/data/transit-ephemerides.json.gz", lambda route: route.fulfill(status=200, content_type="application/gzip", body=_transit_artifact()))
        page.goto(f"{self.base}/tonight/", wait_until="networkidle", timeout=60000)
        page.wait_for_selector("#results table, #results p", timeout=30000)
        self.show_transits(page)
        page.wait_for_function("window.RunCart && document.querySelector('#results [data-runcart-toggle]')", timeout=30000)
        row_button = page.locator("#results tbody tr [data-runcart-toggle]").first
        transit_name = row_button.get_attribute("data-name")
        row_button.click()
        page.wait_for_function("document.querySelector('#results tbody tr [data-runcart-toggle]').getAttribute('aria-pressed') === 'true'", timeout=10000)
        self.assertEqual(page.locator("#chart svg").count(), 0, "the row button does not open the row's chart")
        page.unroute("**/worldsindex/data/transit-ephemerides.json.gz")
        cart = self._cart(page)
        self.assertEqual([(x["kind"], x["name"]) for x in cart], [("ctas", event_name), ("planet", "HD 209458 b"), ("planet", transit_name)])
        self.assertEqual(cart[2]["transitWindow"]["site"], "custom")
        # 4. the run sheet prints the run list, in order
        page.goto(f"{self.base}/tonight/runsheet.html?site=wiro&date=2026-10-03", wait_until="networkidle", timeout=60000)
        page.wait_for_function("document.querySelectorAll('#sheet article').length === 3", timeout=60000)
        self.assertEqual(page.locator("#cart-body li").count(), 3)
        self.assertIn("from your run list", page.inner_text("#sheet-head"))
        kinds = page.eval_on_selector_all("#sheet article", "els => els.map(e => e.dataset.sheetKind)")
        self.assertEqual(kinds, ["ctas", "planet", "planet"])
        self.assertIn("HD 209458 b", page.locator("#sheet article").nth(1).inner_text())
        planet_text = page.locator("#sheet article").nth(1).inner_text()
        self.assertIn("host mag", planet_text.lower())
        self.assertIn("stands in for Cousins I", planet_text)
        # reorder with the keyboard, add a note, remove one
        up = page.locator("#cart-body li").nth(2).locator("[data-cart-move='-1']")
        up.focus(); page.keyboard.press("Enter")
        page.wait_for_function(f"document.querySelectorAll('#cart-body li')[1].textContent.includes({json.dumps(transit_name)})", timeout=10000)
        focused = page.evaluate("document.activeElement.closest('[data-cart-item]')?.textContent || ''")
        self.assertIn(transit_name, focused, "the keyboard stays on the item that moved")
        page.wait_for_function(f"document.querySelectorAll('#sheet article')[1]?.textContent.includes({json.dumps(transit_name)})", timeout=30000)
        note = page.locator("#cart-body li").first.locator("[data-cart-note]")
        note.fill("spectrum first"); note.press("Tab")
        page.wait_for_function("document.querySelector('#sheet .sheet-note')?.textContent.includes('spectrum first')", timeout=30000)
        page.locator("#cart-body li").nth(2).locator("[data-cart-remove]").click()
        page.wait_for_function("document.querySelectorAll('#sheet article').length === 2", timeout=30000)
        self.assertEqual([x["kind"] for x in self._cart(page)], ["ctas", "planet"])
        share = page.get_attribute("#cart-share", "href")
        self.assertIn("list=", share)
        # print layout: the controls disappear, the targets stay, one per page
        page.emulate_media(media="print")
        self.assertFalse(page.is_visible("#cart"))
        self.assertTrue(page.is_visible("#sheet article"))
        breaks = page.eval_on_selector_all("#sheet article", "els => els.map(e => getComputedStyle(e).breakBefore)")
        self.assertEqual(breaks[1:], ["page"] * (len(breaks) - 1))
        page.emulate_media(media="screen")
        # 5. the share link in a fresh browser: the sheet shows the link's list and can load it
        ctx2, errors2 = self._context(width=390, height=844)
        other = ctx2.new_page()
        other.goto(share, wait_until="networkidle", timeout=60000)
        other.wait_for_function("document.querySelectorAll('#sheet article').length === 2", timeout=60000)
        self.assertIn("from the link", other.inner_text("#sheet-head"))
        self.assertFalse(other.locator("#cart-banner").is_hidden())
        self.assertEqual(self._cart(other), [])
        other.click("[data-cart-load]")
        other.wait_for_function("JSON.parse(localStorage.getItem('jm.runlist.v1') || '[]').length === 2", timeout=10000)
        self.assertEqual([x["note"] if "note" in x else None for x in self._cart(other)], ["spectrum first", None])
        overflow = other.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
        self.assertLessEqual(overflow, 1, "390px")
        # another tab of the first browser follows a change made in this one
        sheet_tab = ctx.new_page()
        sheet_tab.goto(f"{self.base}/tonight/runsheet.html?site=wiro&date=2026-10-03", wait_until="networkidle", timeout=60000)
        sheet_tab.wait_for_function("document.querySelectorAll('#cart-body li').length === 2", timeout=30000)
        page.evaluate("RunCart.clear()")
        sheet_tab.wait_for_function("document.querySelectorAll('#cart-body li').length === 0 && document.querySelector('#cart-body').textContent.includes('empty')", timeout=10000)
        axe = next((p for p in (ROOT / "node_modules" / "axe-core" / "axe.min.js",
                                ROOT.parent / "node_modules" / "axe-core" / "axe.min.js") if p.exists()), None)
        if axe is not None:
            other.add_script_tag(content=axe.read_text())
            result = other.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
            self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']}" for v in result["violations"]], [])
        self.assertEqual(self.first_party(errors + errors2), [])
        ctx2.close(); ctx.close()

    def test_homepage_links_to_the_planner(self):
        page = self.browser.new_page()
        page.goto(f"{self.base}/index.html", wait_until="domcontentloaded")
        self.assertGreaterEqual(page.locator('a[href="tonight/"]').count(), 3)  # nav, tool card, footer
        page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

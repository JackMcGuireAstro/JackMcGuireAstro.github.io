#!/usr/bin/env python3
"""The WorldsIndex Compare tab and the status page's exceptions list, in a real browser.

Framework v2 Track 2a. Served from this checkout's built release (worldsindex/data) with no network.
On an object with a known conflict the test checks that nothing of the tab loads before it opens (no
compare.js, no conflict shard), that it then shows one row per source assertion with the selection row
labelled "selection, not a measurement", every row of a property in conflict (candidate solutions
included), filters that narrow the rows, a unit toggle that changes only the normalised column, an
"export exactly these rows" ECSV whose data rows equal the rows on screen, a time-evolution plot with
its table, one h1 and an unbroken heading outline, no sideways scroll at phone widths, no first-party
console error, and no axe-core WCAG 2.1 AA violation (when installed). A release without conflict sets
has no Compare tab. The status page loads the controversial / retracted / excluded list on request.
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
DATA = ROOT / "worldsindex" / "data"
MANIFEST = DATA / "manifest.json"
PREFERRED = ("object-hd-209458-b", "object-hd-189733-b", "object-wasp-12-b")


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


def _axe_path():
    for parent in (ROOT, ROOT.parent, ROOT.parent.parent, ROOT.parent.parent.parent):
        candidate = parent / "node_modules" / "axe-core" / "axe.min.js"
        if candidate.exists():
            return candidate
    return None


def _conflict_object():
    """An object whose release carries a statistically inconsistent property, preferring well-known planets."""
    index = json.loads(gzip.open(DATA / "conflict-index.json.gz").read())
    inconsistent = {entry[0] for entry in index["sets"] if entry[2] == "statistically-inconsistent"}
    for object_id in PREFERRED:
        if object_id in inconsistent:
            return object_id
    return sorted(inconsistent)[0]


@unittest.skipUnless(MANIFEST.exists() and json.loads(MANIFEST.read_text()).get("conflictSets"), "this checkout has no built release with conflict sets")
class CompareBrowserTests(unittest.TestCase):
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
        cls.object_id = _conflict_object()
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

    def open_object(self, scheme="dark", width=1280, strip_conflicts=False):
        page = self.browser.new_page(viewport={"width": width, "height": 900}, color_scheme=scheme, accept_downloads=True)
        errors, requests = [], []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" and "127.0.0.1" in (m.location or {}).get("url", "")
                and not (m.location or {}).get("url", "").endswith("cloud-run.json") and not is_third_party_load_failure(m.text) else None)
        page.on("request", lambda r: requests.append(r.url))
        page.route("https://aladin.cds.unistra.fr/**", lambda route: route.fulfill(status=404, body=""))
        if strip_conflicts:
            def manifest(route):
                body = json.loads(MANIFEST.read_text())
                body.pop("conflictSets", None)
                route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            page.route("**/worldsindex/data/manifest.json", manifest)
        page.goto(f"{self.base}/worldsindex/?object={self.object_id}&section=object", wait_until="networkidle", timeout=90000)
        page.wait_for_function("!document.querySelector('#object-dossier').hidden", timeout=30000)
        return page, errors, requests

    def open_compare(self, **kwargs):
        page, errors, requests = self.open_object(**kwargs)
        page.click("#object-tab-compare")
        page.wait_for_function("document.querySelector('#tab-compare').dataset.compareMounted", timeout=30000)
        return page, errors, requests

    def visible_rows(self, page) -> int:
        return page.locator("#tab-compare tbody tr[data-row]").count()

    def test_nothing_loads_before_the_tab_opens(self):
        page, errors, requests = self.open_object()
        self.assertFalse(page.eval_on_selector("#object-tab-compare", "e => e.hidden"))
        self.assertFalse(any("compare.js" in url or "/data/conflicts/" in url for url in requests), "compare.js or a conflict shard loaded before the tab opened")
        page.click("#object-tab-compare")
        page.wait_for_function("document.querySelector('#tab-compare').dataset.compareMounted", timeout=30000)
        shard_requests = [url for url in requests if "/data/conflicts/" in url]
        self.assertEqual(len(shard_requests), 1, "exactly one small shard is fetched")
        self.assertTrue(any("compare.js" in url for url in requests))
        self.assertEqual(errors, [])
        page.close()

    def test_rows_selection_and_conflict(self):
        page, errors, _ = self.open_compare()
        text = page.text_content("#tab-compare")
        self.assertEqual(page.locator("#tab-compare h2").count(), 1)
        self.assertIn("Selection, not a measurement", text)
        self.assertGreater(page.locator("#tab-compare tr.selection-row").count(), 0)
        conflict = page.locator("#tab-compare section[data-conflict='true']").first
        self.assertGreater(conflict.count(), 0, "the object has a property in conflict")
        # every row of a property in conflict is shown, candidate solutions included
        code = conflict.get_attribute("data-compare-property")
        heading = conflict.locator("h3").text_content()
        shown, total = [int(x) for x in heading.split("·")[1].split("rows")[0].split("of")]
        self.assertEqual(shown, total, heading)
        self.assertEqual(conflict.locator("tbody tr[data-row]").count(), total)
        self.assertIn("Conflict: every row is shown", conflict.text_content())
        self.assertEqual(int(page.eval_on_selector("#tab-compare", "e => e.dataset.compareRows")), self.visible_rows(page))
        self.assertIn("time system", text.lower())
        self.assertEqual(errors, [], code)
        page.close()

    def test_filters_and_unit_toggle(self):
        page, _, _ = self.open_compare()
        before = self.visible_rows(page)
        page.select_option("#tab-compare [data-filter='source']", "nasa-ps")
        after = self.visible_rows(page)
        self.assertLess(after, before)
        self.assertTrue(all(source == "nasa-ps" for source in page.eval_on_selector_all("#tab-compare tbody tr[data-row]", "rows => rows.map(r => r.dataset.source)")))
        page.select_option("#tab-compare [data-filter='source']", "all")
        self.assertEqual(self.visible_rows(page), before)
        radius = "#tab-compare section[data-compare-property='planet_radius'] tbody tr[data-row]"
        if page.locator(radius).count():
            original = page.locator(radius).first.locator("td").nth(1).text_content()
            normalized = page.locator(radius).first.locator("td[data-normalized]").text_content()
            page.check("#tab-compare input[data-unit='radius'][value='R_jup']")
            self.assertEqual(page.locator(radius).first.locator("td").nth(1).text_content(), original, "the original value never changes")
            self.assertNotEqual(page.locator(radius).first.locator("td[data-normalized]").text_content(), normalized)
            self.assertIn("R♃", page.locator(radius).first.locator("td[data-normalized]").text_content())
        page.close()

    def test_export_exactly_the_visible_rows(self):
        page, _, _ = self.open_compare()
        page.select_option("#tab-compare [data-filter='source']", "nasa-ps")
        rows = self.visible_rows(page)
        selections = page.locator("#tab-compare tr.selection-row").count()
        with page.expect_download() as info:
            page.click("#tab-compare [data-export='ecsv']")
        text = Path(info.value.path()).read_text(encoding="utf-8")
        lines = [line for line in text.splitlines() if line and not line.startswith("#")]
        header, data = lines[0].split(","), lines[1:]
        self.assertEqual(header[:4], ["object_id", "name", "host_name", "row_kind"])
        self.assertEqual(sum(1 for line in data if ",source assertion," in line), rows, "ECSV source rows == rows on screen")
        self.assertEqual(sum(1 for line in data if ",\"selection, not a measurement\"," in line or ",selection, not a measurement," in line), selections)
        with page.expect_download() as info:
            page.click("#tab-compare [data-export='manifest']")
        manifest = json.loads(Path(info.value.path()).read_text(encoding="utf-8"))
        self.assertEqual(manifest["sourceAssertionRows"], rows)
        self.assertEqual(manifest["filters"]["source"], "nasa-ps")
        with page.expect_download() as info:
            page.click("#tab-compare [data-export='votable']")
        self.assertIn(f'nrows="{rows + selections}"', Path(info.value.path()).read_text(encoding="utf-8"))
        page.close()

    def test_evolution_plot_and_outline(self):
        page, _, _ = self.open_compare()
        if page.locator("#tab-compare figure.evolution").count():
            figure = page.locator("#tab-compare figure.evolution").first
            self.assertEqual(figure.locator("svg[role='img']").count(), 1)
            self.assertIn("publications", figure.locator("svg").get_attribute("aria-label"))
            figure.locator("summary").click()
            self.assertGreater(figure.locator("tbody tr").count(), 1)
        self.assertEqual(page.locator("h1:visible").count(), 1)
        levels = page.eval_on_selector_all("h1:not([hidden]), #tab-compare h2, #tab-compare h3", "els => els.filter(e => e.offsetParent !== null).map(e => Number(e.tagName[1]))")
        for previous, current in zip(levels, levels[1:]):
            self.assertLessEqual(current - previous, 1, f"heading outline jumps: {levels}")
        page.close()

    def test_no_sideways_scroll(self):
        page, _, _ = self.open_compare(width=390)
        for width in (320, 390, 768):
            with self.subTest(width=width):
                page.set_viewport_size({"width": width, "height": 800})
                page.wait_for_timeout(250)
                self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
        page.close()

    def test_no_compare_tab_without_conflict_sets(self):
        page, errors, requests = self.open_object(strip_conflicts=True)
        self.assertTrue(page.eval_on_selector("#object-tab-compare", "e => e.hidden"), "no data block, no tab")
        self.assertFalse(any("/data/conflicts/" in url for url in requests))
        self.assertEqual(errors, [])
        page.close()

    def test_status_page_exceptions_list(self):
        page = self.browser.new_page(viewport={"width": 1280, "height": 900})
        requests = []
        page.on("request", lambda r: requests.append(r.url))
        page.goto(f"{self.base}/worldsindex/status.html", wait_until="networkidle", timeout=90000)
        page.wait_for_function("document.querySelector('#status-loading').hidden", timeout=30000)
        self.assertFalse(any("controversial-retracted-excluded" in url for url in requests), "the list loads only on request")
        self.assertIn("excluded: false positive", page.text_content("#exceptions-summary"))
        page.click("#exceptions-load")
        page.wait_for_selector("#exceptions-rows table", timeout=30000)
        self.assertGreater(page.locator("#exceptions-rows tbody tr").count(), 0)
        page.select_option("#exceptions-category", "retracted")
        self.assertIn("retracted", page.text_content("#exceptions-rows"))
        self.assertEqual(page.locator("h1").count(), 1)
        page.close()

    def test_axe_reports_no_wcag_violation(self):
        axe = _axe_path()
        if axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        run = "async (scope) => await axe.run(scope, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})"
        for scheme in ("dark", "light"):
            with self.subTest(scheme=scheme, page="compare tab"):
                page, _, _ = self.open_compare(scheme=scheme)
                figure = page.locator("#tab-compare figure.evolution summary").first
                if figure.count():
                    figure.click()
                page.add_script_tag(content=axe.read_text())
                result = page.evaluate(run, "#tab-compare")
                self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']} {[n['target'] for n in v['nodes']][:3]}" for v in result["violations"]], [])
                page.close()
            with self.subTest(scheme=scheme, page="status exceptions"):
                page = self.browser.new_page(viewport={"width": 1280, "height": 900}, color_scheme=scheme)
                page.goto(f"{self.base}/worldsindex/status.html", wait_until="networkidle", timeout=90000)
                page.wait_for_function("document.querySelector('#status-loading').hidden", timeout=30000)
                page.click("#exceptions-load")
                page.wait_for_selector("#exceptions-rows table", timeout=30000)
                page.add_script_tag(content=axe.read_text())
                result = page.evaluate(run, "main")
                self.assertEqual([f"{v['id']} ({len(v['nodes'])}): {v['help']} {[n['target'] for n in v['nodes']][:3]}" for v in result["violations"]], [])
                page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

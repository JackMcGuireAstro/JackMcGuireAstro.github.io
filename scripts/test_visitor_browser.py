#!/usr/bin/env python3
"""The visitor tools (Framework v2 Track 5) in a real browser, on the release in this tree:
Follow (watchlist, regions, saved and gallery filters, "new since you last looked", sharing,
export), SQL (DuckDB-WASM over the deploy's VOParquet tables), cone search (table and VOTable),
the notebook page, and the "Open in a notebook" links of CTAS and WorldsIndex. No sideways scroll
from 320 px; axe-core's WCAG 2 A/AA rules pass in light and dark mode; no first-party console error.

The deploy-time files are built into a temporary directory first (scripts/build_visitor_products.py)
and served at tools/data/. Every third-party request is blocked (browser_test_net). The SQL engine
is a local esbuild bundle of the same DuckDB-WASM release the page pins (installed beside the
checkout with axe-core: npm install --prefix .. @duckdb/duckdb-wasm@1.32.0 esbuild@0.25.10); when it
is absent the engine checks are skipped and say so.
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.parse import unquote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_test_net import isolate_browser, is_third_party_load_failure  # noqa: E402  (third-party requests are blocked)
import build_visitor_products as bvp  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
HAVE_DATA = (ROOT / "ctas/data/catalog-index.json").exists() and (ROOT / "worldsindex/data/manifest.json").exists()
DUCKDB_VERSION = "1.32.0"
MODULES = next((p for p in (ROOT / "node_modules", ROOT.parent / "node_modules") if (p / "@duckdb/duckdb-wasm/dist").exists()), None)


def _port() -> int:
    with contextlib.closing(socket.socket()) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _handler(products: Path, duckdb: Path | None):
    class Handler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            return

        def translate_path(self, path):
            clean = unquote(urlsplit(path).path)
            if clean.startswith("/tools/data/"):
                return str(products / clean.lstrip("/"))
            if duckdb is not None and clean.startswith("/__duckdb/"):
                return str(duckdb / clean[len("/__duckdb/"):])
            return super().translate_path(path)

        def end_headers(self):
            if self.path.endswith(".wasm"):
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map, ".wasm": "application/wasm", ".mjs": "text/javascript",
                          ".parquet": "application/vnd.apache.parquet"}

    return functools.partial(Handler, directory=str(ROOT))


@unittest.skipUnless(HAVE_DATA, "the releases are not overlaid (bash scripts/overlay_published_data.sh)")
class VisitorBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"playwright is not installed: {exc}") from exc
        cls.tmp = Path(tempfile.mkdtemp(prefix="visitor-browser-"))
        cls.manifest = bvp.build(ROOT, cls.tmp)
        cls.duckdb = None
        cls.duckdb_reason = "the local DuckDB-WASM copy is not installed beside the checkout"
        if MODULES is not None and (MODULES / ".bin/esbuild").exists():
            dist = cls.tmp / "duckdb"
            shutil.copytree(MODULES / "@duckdb/duckdb-wasm/dist", dist, ignore=shutil.ignore_patterns("*.map", "*node*", "*coi*", "types", "img"))
            done = subprocess.run([str(MODULES / ".bin/esbuild"), str(MODULES / "@duckdb/duckdb-wasm/dist/duckdb-browser.mjs"), "--bundle", "--format=esm",
                                   "--platform=browser", f"--outfile={dist / 'duckdb-bundle.mjs'}", "--log-level=error"], capture_output=True, text=True)
            version = json.loads((MODULES / "@duckdb/duckdb-wasm/package.json").read_text()).get("version")
            if done.returncode == 0 and version == DUCKDB_VERSION:
                cls.duckdb = dist
            else:
                cls.duckdb_reason = f"local DuckDB-WASM is {version} (the page pins {DUCKDB_VERSION}) or did not bundle: {done.stderr[-300:]}"
        port = _port()
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _handler(cls.tmp, cls.duckdb))
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{port}"
        cls.pw = sync_playwright().start()
        try:
            cls.browser = isolate_browser(cls.pw.chromium.launch(), cls.base)
        except Exception as exc:  # pragma: no cover
            cls.pw.stop(); cls.server.shutdown()
            raise unittest.SkipTest(f"no Chromium: {exc}") from exc
        cls.ctas = bvp.read_inputs(ROOT)["ctas"]["records"]
        cls.sn = next(r for r in cls.ctas if r.get("name") == "SN2023ixf") if any(r.get("name") == "SN2023ixf" for r in cls.ctas) \
            else next(r for r in cls.ctas if str(r.get("name", "")).startswith("SN") and isinstance(r.get("ra_deg"), float))

    @classmethod
    def tearDownClass(cls):
        cls.browser.close(); cls.pw.stop(); cls.server.shutdown(); cls.server.server_close()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def open(self, path, scheme="light", width=1280, context=None, wait=None, until="load"):
        page = (context or self.browser).new_page(viewport={"width": width, "height": 900}, color_scheme=scheme, accept_downloads=True) \
            if context is None else context.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" and not is_third_party_load_failure(m.text) else None)
        if self.duckdb is not None:
            page.add_init_script(f"window.DUCKDB_MODULE = '{self.base}/__duckdb/duckdb-bundle.mjs'; window.DUCKDB_BASE = '{self.base}/__duckdb/';")
        page.goto(self.base + path, wait_until=until, timeout=120000)
        if wait:
            page.wait_for_selector(wait, timeout=90000)
        return page

    # ------------------------------------------------------------------ Follow
    def test_follow_watchlist_regions_filters_and_new_since_last_look(self):
        context = self.browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
        page = self.open("/tools/follow/", context=context, wait="#gallery li")
        page.wait_for_function("document.querySelector('#release-line').textContent.includes('CTAS release')", timeout=60000)
        self.assertIn("WorldsIndex release", page.inner_text("#release-line"))
        # Watchlist: a CTAS name without its prefix, a WorldsIndex planet, and a position.
        page.fill("#watch-text", f"{self.sn['name'][2:]}\nHD 209458 b\n{self.sn['ra_deg']} {self.sn['dec_deg']} 3")
        page.click("#watch-save")
        page.wait_for_selector("#watch-results article:nth-of-type(3)", timeout=60000)
        articles = page.locator("#watch-results article")
        self.assertIn(self.sn["name"], articles.nth(0).inner_text())
        self.assertIn("HD 209458 b", articles.nth(1).inner_text())
        self.assertIn(self.sn["name"], articles.nth(2).inner_text())
        # A region as a circle around the same event.
        page.fill("#region-name", "Around the test event")
        page.fill("#region-text", f"{self.sn['ra_deg']:.6f} {self.sn['dec_deg']:.6f} 0.2")
        page.click("#region-add")
        page.wait_for_function("document.querySelector('#region-results').textContent.includes('square degrees')", timeout=60000)
        self.assertIn(self.sn["name"], page.inner_text("#region-results"))
        # A gallery filter: its count equals the deploy's feed count for this release.
        feed = next(f for f in self.manifest["feeds"] if f["id"] == "ctas-retracted")
        page.click("#gallery-ctas-retracted button")
        page.wait_for_function("document.querySelector('#filter-results').textContent.includes('Retracted events')", timeout=60000)
        self.assertIn(f"{feed['match_count']} record", page.inner_text("#filter-results").replace(",", ""))
        self.assertEqual(page.locator("#gallery-ctas-retracted a").count(), 2, "JSON and Atom feed links")
        # A filter built in the form.
        page.fill("#filter-name", "Bright Ia")
        page.select_option("#filter-clauses .cf", "classification")
        page.select_option("#filter-clauses .co", "contains")
        page.fill("#filter-clauses .cv", "SN Ia")
        page.click("#filter-save")
        page.wait_for_function("document.querySelectorAll('#filter-results article').length === 2", timeout=60000)
        stored = json.loads(page.evaluate("localStorage.getItem('jm.visitor.v1')"))
        self.assertEqual(len(stored["filters"]), 2)
        self.assertEqual(stored["regions"][0]["title"], "Around the test event")
        # "New since you last looked": forget one id of the retracted filter's last look, reload.
        key = next(k for k in stored["seen"] if k.startswith("filter:ctas:") and "retracted" in k)
        dropped = stored["seen"][key]["ids"].pop()
        page.evaluate("s => localStorage.setItem('jm.visitor.v1', s)", json.dumps(stored))
        page.reload(wait_until="load")
        page.wait_for_function("document.querySelectorAll('#filter-results article').length === 2", timeout=60000)
        first = page.locator("#filter-results article").nth(0)
        self.assertIn("1 new since you last looked", first.inner_text())
        self.assertEqual(first.locator(".badge-new").count(), 1)
        self.assertIn(dropped, first.locator("tr.is-new a").get_attribute("href"))
        page.reload(wait_until="load")
        page.wait_for_function("document.querySelectorAll('#filter-results article').length === 2", timeout=60000)
        self.assertIn("nothing new since you last looked", page.locator("#filter-results article").nth(0).inner_text())
        # Export.
        with page.expect_download() as download:
            page.click("#export-state")
        exported = json.loads(Path(download.value.path()).read_text())
        self.assertEqual(exported["schema"], "jm.visitor-state@1")
        self.assertEqual(len(exported["filters"]), 2)
        self.assertEqual(page.errors, [])
        context.close()

    def test_a_shared_filter_link_fills_the_builder(self):
        script = r"""
import { encodeFilterForUrl } from "./tools/lib/visitor-filters.js";
process.stdout.write(encodeFilterForUrl({schema: "jm.visitor-filter@1", title: "Shared one", product: "worldsindex", match: "all",
  clauses: [{field: "discoveryYear", op: "gte", value: 2024}, {field: "methodCode", op: "eq", value: "direct-imaging"}]}));
"""
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        token = subprocess.run([node, "--input-type=module", "-e", script], cwd=ROOT, capture_output=True, text=True, check=True).stdout
        page = self.open(f"/tools/follow/#filter={token}", wait=".clause-row:nth-of-type(2)")
        self.assertEqual(page.input_value("#filter-name"), "Shared one")
        self.assertEqual(page.input_value("#filter-product"), "worldsindex")
        self.assertEqual(page.locator(".clause-row .cf").nth(1).input_value(), "methodCode")
        self.assertIn("shared filter is loaded", page.inner_text("#filter-status"))
        self.assertEqual(page.errors, [])
        page.close()

    # ------------------------------------------------------------------ SQL
    def test_sql_page_lists_tables_and_runs_queries(self):
        page = self.open("/tools/sql/", wait="#tables h3")
        self.assertEqual(page.locator("#tables h3").count(), len(self.manifest["sql"]["tables"]))
        self.assertGreaterEqual(page.locator("#sql-examples button").count(), 6)
        if self.duckdb is None:
            self.skipTest(self.duckdb_reason)
        # The engine (the pinned DuckDB-WASM release, served locally here) starts in a worker and
        # answers SQL; CSV over HTTP needs no extension, so it checks the whole query path.
        sources = sum(1 for _ in (ROOT / "ctas/data/research/sources.csv").open()) - 1
        page.fill("#sql-text", f"SELECT count(*) AS n FROM read_csv('{self.base}/ctas/data/research/sources.csv')")
        page.click("#sql-run")
        page.wait_for_function("document.querySelector('#sql-status').textContent.startsWith('Done') || document.querySelector('#sql-status').textContent.includes('failed')", timeout=120000)
        self.assertTrue(page.inner_text("#sql-status").startswith("Done"), page.inner_text("#sql-status"))
        self.assertEqual(page.locator("#result tbody td").first.inner_text(), str(sources))
        with page.expect_download() as download:
            page.click("#sql-csv")
        self.assertEqual(Path(download.value.path()).read_text().splitlines(), ["n", str(sources)])
        page.fill("#sql-text", "SELECT no_such_column FROM range(3)")
        page.click("#sql-run")
        page.wait_for_function("document.querySelector('#sql-status').textContent.includes('failed')", timeout=60000)
        self.assertIn("no_such_column", page.inner_text("#sql-status"))
        self.assertEqual(page.errors, [])
        # The Parquet tables need DuckDB's Parquet reader, which DuckDB-WASM fetches from
        # extensions.duckdb.org: every third-party request is blocked in this suite, so the page must
        # say the tables could not be opened (and keep working). Every example query runs on the same
        # Parquet files with DuckDB in scripts/test_visitor_products.py.
        tables_line = page.inner_text("#tables-line")
        page.close()
        self.assertIn("could not be opened", tables_line)
        self.assertIn("extensions.duckdb.org", tables_line)

    # ------------------------------------------------------------------ cone search
    def test_cone_search_finds_the_event_and_writes_a_votable(self):
        page = self.open(f"/tools/cone/?ra={self.sn['ra_deg']}&dec={self.sn['dec_deg']}&r=0.05", wait="#cone-results tbody tr")
        self.assertEqual(page.locator("#cone-results tbody tr").first.locator("th").inner_text(), self.sn["name"])
        self.assertIn("within", page.inner_text("#cone-meta"))
        with page.expect_download() as download:
            page.click("#cone-votable")
        votable = Path(download.value.path()).read_text()
        self.assertIn('ucd="pos.eq.ra;meta.main"', votable)
        self.assertIn(self.sn["event_id"], votable)
        try:
            from astropy.io.votable import parse_single_table
            table = parse_single_table(download.value.path()).to_table()
            self.assertIn(self.sn["event_id"], [str(x) for x in table["id"]])
        except ImportError:  # pragma: no cover
            pass
        page.fill("#cone-radius", "0"); page.click("#cone-run")
        self.assertIn("radius", page.inner_text("#cone-status"))
        self.assertEqual(page.errors, [])
        page.close()

    # ------------------------------------------------------------------ notebook
    def test_notebook_page_and_links(self):
        page = self.open(f"/tools/notebook/?ctas={self.sn['event_id']}", wait="#nb-download:not([disabled])")
        self.assertIn(f'EVENT_ID = "{self.sn["event_id"]}"', page.inner_text("#nb-code"))
        self.assertTrue(page.get_attribute("#nb-repl", "href").startswith("https://jupyterlite.github.io/demo/repl/"))
        with page.expect_download() as download:
            page.click("#nb-download")
        notebook = json.loads(Path(download.value.path()).read_text())
        self.assertEqual((notebook["nbformat"], notebook["nbformat_minor"]), (4, 5))
        page.goto(self.base + "/tools/notebook/?worldsindex=object-hd-209458-b", wait_until="load")
        page.wait_for_selector("#nb-download:not([disabled])", timeout=60000)
        self.assertIn('OBJECT_ID = "object-hd-209458-b"', page.inner_text("#nb-code"))
        page.goto(self.base + "/tools/notebook/?ctas=not-an-event", wait_until="load")
        page.wait_for_function("document.querySelector('#nb-target').textContent.includes('No public record')", timeout=60000)
        self.assertEqual(page.errors, [])
        page.close()
        worlds = self.open("/worldsindex/?object=object-hd-209458-b&section=object", wait="#open-notebook:not([hidden])", until="networkidle")
        self.assertTrue(worlds.get_attribute("#open-notebook", "href").endswith("tools/notebook/?worldsindex=object-hd-209458-b"))
        worlds.close()
        ctas = self.open(f"/ctas.html?event={self.sn['event_id']}", until="networkidle")
        ctas.wait_for_selector("#candidate-workspace #dossier [data-open-notebook]", state="attached", timeout=90000)  # inside "More actions"
        self.assertTrue(ctas.get_attribute("[data-open-notebook]", "href").endswith(f"tools/notebook/?ctas={self.sn['event_id']}"))
        ctas.close()

    # ------------------------------------------------------------------ layout and accessibility
    def test_no_sideways_scroll(self):
        for path, wait in (("/tools/follow/", "#gallery li"), ("/tools/sql/", "#tables h3"), ("/tools/cone/", "#cone-form"), ("/tools/notebook/", "#nb-target")):
            page = self.open(path, wait=wait)
            for width in (320, 390, 768, 1280):
                with self.subTest(path=path, width=width):
                    page.set_viewport_size({"width": width, "height": 900}); page.wait_for_timeout(150)
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth"), 1)
            page.close()

    def test_axe(self):
        axe = next((p for p in (ROOT / "node_modules/axe-core/axe.min.js", ROOT.parent / "node_modules/axe-core/axe.min.js") if p.exists()), None)
        if axe is None:
            self.skipTest("axe-core is not installed")
        source = axe.read_text()
        for path, wait in (("/tools/follow/", "#gallery li"), ("/tools/sql/", "#tables h3"), (f"/tools/cone/?ra={self.sn['ra_deg']}&dec={self.sn['dec_deg']}&r=0.05", "#cone-results tbody tr"),
                           (f"/tools/notebook/?ctas={self.sn['event_id']}", "#nb-download:not([disabled])")):
            for scheme in ("light", "dark"):
                with self.subTest(path=path, scheme=scheme):
                    page = self.open(path, scheme=scheme, wait=wait)
                    page.add_script_tag(content=source)
                    result = page.evaluate("async () => (await axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})).violations.map(v => v.id + ': ' + v.nodes.map(n => n.target.join(' ')).slice(0, 3).join(' | '))")
                    self.assertEqual(result, [])
                    page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

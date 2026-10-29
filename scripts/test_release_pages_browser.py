#!/usr/bin/env python3
"""Release pages in a real browser (Track 7): loading budgets and the page-audit fixes.

Part 1, budgets.

The professional framework asks for Lighthouse-style budgets: Largest Contentful Paint at
most 2.5 s on a throttled mobile profile and at most 200 kB of JavaScript on first load.
This suite measures both with Playwright's Chromium, using the same throttling Lighthouse
uses for its mobile run (150 ms round trip, 1.6 Mbit/s down, 750 kbit/s up, CPU slowed 4x)
on a 412 x 823 px phone viewport, with every third-party request blocked
(browser_test_net.isolate_browser). JavaScript is counted as the gzip size of every
first-party script the page loads before its load event, because GitHub Pages serves
scripts gzip-compressed.

Two kinds of numbers are kept in benchmarks/performance/budgets.json:
  - the framework's targets (LCP 2,500 ms, JS 200 kB);
  - a per-page ratchet: a page already within a target must stay within it; a page that
    is over a target must not get worse than its recorded measurement plus a tolerance
    (5 % for bytes, which are deterministic; 35 % for LCP, which varies between machines).
So the check passes today, says honestly which pages miss the framework's targets, and
fails when a change makes any page heavier or slower. Set BUDGET_REPORT=<path> to write the
measurements as JSON (benchmarks/performance/latest.json is written that way).

Part 2, the page-audit items of 2026-10-12 (claude/agent-batch-2026-10-12.md):
  1. CTAS search answers under the search box (count, first matches, alias-loading state);
  2. the CTAS first screen says in plain words what CTAS is (the main actions stay in the
     sticky navigation; the calm-defaults design keeps the hero free of buttons);
  3. Downloads links point at <name>.json.gz when the deployment serves gzip (they were 404s);
  4. a deep link names the record in the tab title and, on a phone, lands on the dossier;
     WorldsIndex names the open planet in the tab title;
  5. the CTAS sections are in the same order in the document as on screen, so Tab follows
     what the reader sees.
"""
from __future__ import annotations

import contextlib
import functools
import gzip
import http.server
import json
import os
import socket
import sys
import threading
import unittest
from pathlib import Path
from urllib.parse import urlsplit, unquote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from browser_test_net import isolate_browser, is_third_party_load_failure  # noqa: E402,F401  (third-party requests are blocked)

ROOT = Path(__file__).resolve().parents[1]
BUDGETS = ROOT / "benchmarks" / "performance" / "budgets.json"
PAGES = {
    "home": "index.html",
    "ctas": "ctas.html",
    "worldsindex": "worldsindex/index.html",
    "tonight": "tonight/index.html",
    "tools": "tools/index.html",
}
THROTTLE = {"offline": False, "latency": 150, "downloadThroughput": 1_638_400 / 8, "uploadThroughput": 750_000 / 8}
CPU_SLOWDOWN = 4
VIEWPORT = {"width": 412, "height": 823}


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9, mtime=0))


class ReleasePagesTests(unittest.TestCase):
    measurements: dict = {}

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
        cls.budgets = json.loads(BUDGETS.read_text())

    @classmethod
    def tearDownClass(cls):
        report = os.environ.get("BUDGET_REPORT")
        if report and cls.measurements:
            Path(report).write_text(json.dumps({
                "schema": "page-budget-measurements@1",
                "profile": {"viewport": VIEWPORT, "network": THROTTLE, "cpu_slowdown": CPU_SLOWDOWN,
                            "third_party": "blocked", "js_bytes": "gzip -9 size of first-party scripts loaded before the load event"},
                "targets": cls.budgets["targets"],
                "pages": cls.measurements,
            }, indent=2, sort_keys=True) + "\n")
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    def measure(self, key: str) -> dict:
        context = self.browser.new_context(viewport=VIEWPORT, device_scale_factor=2, is_mobile=True, has_touch=True)
        page = context.new_page()
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", THROTTLE)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": CPU_SLOWDOWN})
        scripts: list[str] = []
        page.on("request", lambda request: scripts.append(request.url) if request.resource_type == "script" else None)
        page.add_init_script("""
          window.__lcp = 0;
          new PerformanceObserver((list) => {
            for (const entry of list.getEntries()) window.__lcp = Math.max(window.__lcp, entry.startTime);
          }).observe({type: 'largest-contentful-paint', buffered: true});
        """)
        page.goto(f"{self.base}/{PAGES[key]}", wait_until="load", timeout=180_000)
        page.wait_for_timeout(1500)  # LCP is final only after the page settles
        lcp = page.evaluate("window.__lcp")
        js_files, js_bytes = [], 0
        for url in dict.fromkeys(scripts):
            parts = urlsplit(url)
            if f"{parts.scheme}://{parts.netloc}" != self.base:
                continue
            path = ROOT / unquote(parts.path.lstrip("/"))
            if path.is_file():
                size = gzip_size(path)
                js_bytes += size
                js_files.append({"path": str(path.relative_to(ROOT)), "gzip_bytes": size})
        context.close()
        result = {"lcp_ms": round(lcp, 1), "js_gzip_bytes": js_bytes, "js_files": js_files,
                  "within_target": {"lcp": lcp <= self.budgets["targets"]["lcp_ms"],
                                    "js": js_bytes <= self.budgets["targets"]["js_gzip_bytes"]}}
        type(self).measurements[key] = result
        return result

    def check(self, key: str):
        result = self.measure(key)
        targets, ratchet = self.budgets["targets"], self.budgets["pages"][key]
        tolerance = self.budgets["tolerance"]
        for metric, target_key, tol_key in (("lcp_ms", "lcp_ms", "lcp"), ("js_gzip_bytes", "js_gzip_bytes", "bytes")):
            limit = max(targets[target_key], ratchet[metric] * (1 + tolerance[tol_key]))
            self.assertLessEqual(result[metric], limit,
                                 f"{key}: {metric} {result[metric]} exceeds its budget {limit:.0f} "
                                 f"(target {targets[target_key]}, recorded {ratchet[metric]})")

    def test_home(self):
        self.check("home")

    def test_ctas(self):
        self.check("ctas")

    def test_worldsindex(self):
        self.check("worldsindex")

    def test_tonight(self):
        self.check("tonight")

    def test_tools(self):
        self.check("tools")

    # ------------------------------------------------------------------ page audit
    def page(self, width=1280, height=900):
        context = self.browser.new_context(viewport={"width": width, "height": height})
        page = context.new_page()
        page.errors = []
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" and not is_third_party_load_failure(m.text) else None)
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        return context, page

    def ctas_leader(self):
        index = json.loads((ROOT / "ctas" / "data" / "catalog-index.json").read_text())
        columns = index["candidate_columns"]
        leaderboard = index.get("leaderboard") or {}
        rows = index["candidate_rows"]
        if leaderboard.get("event_ids"):
            first = leaderboard["event_ids"][0]
            rows = [r for r in rows if r[columns.index("event_id")] == first] or rows
        row = rows[0]
        return row[columns.index("event_id")], row[columns.index("name")]

    def test_audit_1_search_answers_under_the_search_box(self):
        context, page = self.page()
        page.goto(f"{self.base}/ctas.html", wait_until="networkidle", timeout=120_000)
        # A record the default filters show (the newest in the loaded summary), searched by name.
        name = page.evaluate("""() => { const rows = window.CTASCatalogModel.filteredCandidates(window.CTASApp.getCandidates(), {preset: 'all'}, Date.now());
          return (rows[0] || window.CTASApp.getCandidates()[0]).name; }""")
        page.fill("#ctas-q", name)
        answer = page.locator("#ctas-q-answer")
        answer.wait_for(state="visible", timeout=30_000)
        self.assertRegex(answer.inner_text(), r"\d[\d,]* match")
        self.assertGreaterEqual(answer.locator(f'button:has-text("{name}")').count(), 1)
        page.fill("#ctas-q", "")
        self.assertTrue(answer.is_hidden())
        self.assertEqual(page.errors, [])
        context.close()

    def test_audit_2_first_screen_is_plain(self):
        context, page = self.page(390, 844)
        page.goto(f"{self.base}/ctas.html", wait_until="domcontentloaded", timeout=120_000)
        statement = page.locator(".hero__statement").inner_text()
        for word in ("supernovae", "gamma-ray", "neutrino", "gravitational-wave", "follow"):
            self.assertIn(word, statement)
        # The calm-defaults design keeps the hero free of action buttons (test_ctas_static); the
        # sticky navigation carries Sky, Latest, Top 100 and the search box instead.
        nav = page.locator(".ctas-console-links a")
        self.assertGreaterEqual(nav.count(), 3)
        self.assertLess(page.locator("#ctas-q").bounding_box()["y"], 844 * 1.5)
        context.close()

    def test_audit_3_downloads_follow_the_delivery_encoding(self):
        for encoding, suffix in (("gzip", ".json.gz"), ("identity", ".json")):
            context, page = self.page()
            if encoding == "gzip":
                page.route("**/ctas/delivery.json", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(
                    {"schema": "ctas.delivery@1.0.0", "candidate_chunk_encoding": "gzip"})))
            page.goto(f"{self.base}/ctas.html#catalog-downloads", wait_until="networkidle", timeout=120_000)
            page.evaluate("document.getElementById('catalog-downloads').open = true")
            page.wait_for_selector("#catalog-downloads ol a[download]", timeout=60_000)
            hrefs = page.eval_on_selector_all("#catalog-downloads ol a[download]", "els => els.map(e => e.getAttribute('href'))")
            self.assertTrue(hrefs)
            self.assertTrue(all(href.endswith(suffix) for href in hrefs), (encoding, hrefs[:3]))
            if encoding == "gzip":
                self.assertIn("gzip-compressed", page.locator("#catalog-downloads").inner_text())
                self.assertTrue(all(not href.endswith(".json.gz.gz") for href in hrefs))
            else:
                self.assertEqual(page.request.get(f"{self.base}/{hrefs[0]}").status, 200)
            context.close()

    def test_audit_4_deep_links_name_and_reach_the_record(self):
        event_id, name = self.ctas_leader()
        context, page = self.page(390, 844)
        page.goto(f"{self.base}/ctas.html?event={event_id}#dossier", wait_until="networkidle", timeout=120_000)
        page.wait_for_function("document.title.indexOf('CTAS') > 0 && document.title.indexOf(' · CTAS') > 0", timeout=60_000)
        self.assertTrue(page.title().startswith(name), page.title())
        top = page.evaluate("document.getElementById('candidate-workspace').getBoundingClientRect().top")
        self.assertLess(abs(top), 844, f"dossier is {top:.0f} px from the top of the screen")
        context.close()
        index = json.loads(gzip.decompress((ROOT / "worldsindex" / "data" / "catalog-index.json.gz").read_bytes()))
        objects = index["objects"]
        target = next((o for o in objects if o["name"] == "HD 209458 b"), None) or next(o for o in objects if o.get("normalizedStatus") == "CONFIRMED")
        context, page = self.page()
        page.goto(f"{self.base}/worldsindex/?object={target['objectId']}&section=object", wait_until="networkidle", timeout=120_000)
        page.wait_for_function("document.title.indexOf(' · WorldsIndex') > 0", timeout=60_000)
        self.assertEqual(page.title(), f"{target['name']} · WorldsIndex")
        context.close()

    def test_audit_5_ctas_sections_are_in_reading_order(self):
        context, page = self.page()
        page.goto(f"{self.base}/ctas.html", wait_until="domcontentloaded", timeout=120_000)
        ids = ["celestial-sphere", "recent-stream", "ranked-candidates", "followup-examples", "ctas-learn", "ctas-reference", "methods-and-use"]
        # Document order of the sections themselves (compareDocumentPosition), not of every id on the page.
        dom = page.evaluate("""ids => { const els = ids.map(id => document.getElementById(id));
          return els.map(a => els.filter(b => b !== a && (b.compareDocumentPosition(a) & Node.DOCUMENT_POSITION_FOLLOWING)).length); }""", ids)
        visual = page.evaluate("ids => ids.map(id => document.getElementById(id).getBoundingClientRect().top + window.scrollY)", ids)
        self.assertEqual(dom, sorted(dom))
        self.assertEqual(visual, sorted(visual))
        context.close()

    def test_budget_file_names_every_page(self):
        self.assertEqual(set(self.budgets["pages"]), set(PAGES))
        self.assertEqual(self.budgets["targets"], {"lcp_ms": 2500, "js_gzip_bytes": 200_000})


if __name__ == "__main__":
    unittest.main(verbosity=2)

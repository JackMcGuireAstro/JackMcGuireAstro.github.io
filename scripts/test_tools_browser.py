#!/usr/bin/env python3
"""The observing tools (tools/) in a real browser.

Checks that the tools index, the coordinate converter, the exposure-time calculator and the
finder-chart page load without script errors and render their core calculation, that the
batch CSV downloads, that the finder's name lookup and print layout behave, that no page
scrolls sideways from 320 px wide up, and that axe-core's WCAG 2.1 AA rules pass (when
axe-core is installed) in light and dark mode.

No network is needed: the CDS hips2fits image request is answered with a local placeholder
PNG and the CDS Sesame lookup with a canned reply (or a refusal); every other off-site
request is blocked.
"""
from __future__ import annotations

import contextlib
import csv
import functools
import http.server
import io
import re
import socket
import struct
import threading
import unittest
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWPORTS = ((320, 568), (390, 844), (768, 1024), (1280, 720))
HIPS2FITS = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits"
SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/"
SESAME_REPLY = """# M 51\t#Q12345
#=Sc=Simbad (CDS, via client/server):    1     4ms
%@ 1522887
%I.0 M  51
%J 202.469575 +47.195258 = 13:29:52.69 +47:11:42.9
#====Done (2026-Oct-03,10:00:00z)====
"""


def _placeholder_png(size: int = 64) -> bytes:
    """A grey square with a few white dots, as a PNG, without any imaging library."""
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            row.append(255 if (x * 7 + y * 13) % 97 == 0 else 24)
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


PNG = _placeholder_png()


def _free_port() -> int:
    with contextlib.closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        return


class ToolsBrowserTests(unittest.TestCase):
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
        cls.axe = next((p for p in (ROOT / "node_modules" / "axe-core" / "axe.min.js",
                                    ROOT.parent / "node_modules" / "axe-core" / "axe.min.js") if p.exists()), None)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()

    # ------------------------------------------------------------------ helpers
    def open(self, path, scheme="light", width=1280, height=900, sesame="reply"):
        """Open a page with off-site requests stubbed. sesame: 'reply', 'notfound' or 'refuse'."""
        page = self.browser.new_page(viewport={"width": width, "height": height}, color_scheme=scheme,
                                     timezone_id="America/Denver", accept_downloads=True)
        page.errors, page.requests = [], []
        page.on("console", lambda m: page.errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: page.errors.append(str(e)))

        def route(r):
            url = r.request.url
            if url.startswith(self.base):
                return r.continue_()
            page.requests.append(url)
            if url.startswith(HIPS2FITS):
                return r.fulfill(status=200, content_type="image/png", body=PNG)
            if url.startswith(SESAME):
                if sesame == "refuse":
                    return r.abort()
                body = SESAME_REPLY if sesame == "reply" else "# xyzzy\n#! *** Nothing found ***\n#====Done====\n"
                return r.fulfill(status=200, content_type="text/plain", body=body,
                                 headers={"Access-Control-Allow-Origin": "*"})
            return r.abort()
        page.route("**/*", route)
        page.goto(f"{self.base}/{path}", wait_until="networkidle", timeout=60000)
        return page

    def no_sideways_scroll(self, page):
        for width, height in VIEWPORTS:
            with self.subTest(viewport=f"{width}x{height}"):
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(200)
                overflow = page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth")
                self.assertLessEqual(overflow, 1)

    def axe_violations(self, page):
        page.add_script_tag(content=self.axe.read_text())
        result = page.evaluate("async () => await axe.run(document, {runOnly: {type: 'tag',"
                               " values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}})")
        return [f"{v['id']} ({len(v['nodes'])}): {v['help']} {[n['target'] for n in v['nodes']][:3]}" for v in result["violations"]]

    # ------------------------------------------------------------------ index
    def test_index_lists_the_tools(self):
        page = self.open("tools/")
        hrefs = page.eval_on_selector_all(".tool-card", "els => els.map(e => e.getAttribute('href'))")
        self.assertEqual(hrefs, ["coordinates/", "exposure/", "finder/", "../tonight/"])
        self.assertEqual(page.title(), "Observing tools | Jack McGuire")
        self.assertEqual(page.errors, [])
        page.close()

    # ------------------------------------------------------------------ coordinates
    def open_coordinates(self, scheme="light", width=1280):
        page = self.open("tools/coordinates/?ra=00:42:44.3&dec=%2B41:16:09&name=M%2031", scheme, width)
        page.wait_for_selector("#position dl")
        page.fill("#jd", "2461317.625")  # 2026-10-04 03:00 UTC, evening in Wyoming
        page.dispatch_event("#jd", "change")
        return page

    def test_coordinates_single_target(self):
        page = self.open_coordinates()
        self.assertIn(page.input_value("#utc"), ("2026-10-04T03:00", "2026-10-04T03:00:00"))  # Chrome drops :00
        self.assertEqual(page.input_value("#mjd"), "61317.125000")
        position = page.inner_text("#position")
        for expected in ("M 31", "00:42:44.300", "+41:16:09.00", "121.17", "−21.57", "Andromeda"):
            self.assertIn(expected, position)
        sky = page.text_content("#sky")
        self.assertIn("Altitude", sky)
        self.assertIn("Airmass", sky)
        self.assertIn("Local apparent sidereal", page.text_content("#time"))
        shown_alt = float(page.text_content("#sky dd").split("°")[0].replace("\u2212", "-"))
        try:  # end to end against astropy, when it is installed (it is not needed in CI)
            import astropy.units as u
            from astropy.coordinates import AltAz, EarthLocation, SkyCoord
            from astropy.time import Time
            from astropy.utils import iers
            iers.conf.auto_download = False
            iers.conf.iers_degraded_accuracy = "ignore"
            t = Time("2026-10-04T03:00:00", scale="utc")
            t.delta_ut1_utc = 0
            ref = SkyCoord("00h42m44.3s +41d16m09s").transform_to(
                AltAz(obstime=t, location=EarthLocation.from_geodetic(-105.9767 * u.deg, 41.0979 * u.deg, 0 * u.m))).alt.deg
            self.assertAlmostEqual(shown_alt, ref, delta=0.05)
        except ImportError:
            self.assertTrue(20 < shown_alt < 90, shown_alt)
        self.assertEqual(page.get_attribute("#finder-link", "href"), "../finder/?ra=10.684583&dec=41.269167&name=M+31")
        page.fill("#ra", "25 00 00")
        page.click("#coord-form button[type=submit]")
        self.assertIn("below 24", page.text_content("#coord-status"))
        self.assertEqual(page.get_attribute("#ra", "aria-invalid"), "true")
        self.assertEqual(page.errors, [])
        self.assertEqual(page.requests, [])  # nothing leaves the page
        page.close()

    def test_coordinates_batch_and_csv(self):
        page = self.open_coordinates()
        page.fill("#batch", "M 31 00 42 44.3 +41 16 09\nVega,18:36:56.3,+38:47:01\nbad 99 00 00 +10 00 00\nSN 2026abc 187.25 -12.5")
        page.click("#batch-form button[type=submit]")
        page.wait_for_selector("#batch-results tbody tr")
        self.assertEqual(page.locator("#batch-results tbody tr").count(), 4)
        self.assertEqual(page.locator("#batch-results tbody tr.bad").count(), 1)
        self.assertIn("3 targets converted, 1 line", page.text_content("#batch-status"))
        with page.expect_download() as info:
            page.click("#csv")
        download = info.value
        self.assertEqual(download.suggested_filename, "coordinates.csv")
        rows = list(csv.DictReader(io.StringIO(Path(download.path()).read_text(encoding="utf-8"))))
        self.assertEqual([r["name"] for r in rows], ["M 31", "Vega", "bad", "SN 2026abc"])
        self.assertEqual(rows[1]["constellation"], "Lyr")
        self.assertEqual(rows[0]["utc"], "2026-10-04T03:00:00.000Z")
        self.assertGreater(float(rows[0]["alt_deg"]), 20)
        self.assertTrue(rows[2]["error"].startswith("line 3"))
        self.assertAlmostEqual(float(rows[3]["dec_deg"]), -12.5)
        self.assertEqual(page.errors, [])
        page.close()

    # ------------------------------------------------------------------ exposure
    def test_exposure_modes(self):
        page = self.open("tools/exposure/")
        page.wait_for_selector("#result .headline")
        self.assertIn("S/N", page.text_content("#result .big"))
        self.assertIn("WIRO", page.text_content("#result"))
        page.wait_for_selector("#curve svg path.curve")
        page.check("input[value=time]")
        page.wait_for_function("document.querySelector('#result .big').textContent.includes('to reach S/N')")
        self.assertTrue(page.is_hidden("#exptime"))
        page.check("input[value=maglim]")
        page.wait_for_function("document.querySelector('#result .big').textContent.startsWith('V =')")
        self.assertTrue(page.is_hidden("#mag"))
        page.select_option("#filter", "r")
        page.wait_for_function("document.querySelector('#result .big').textContent.startsWith('r =')")
        self.assertEqual(page.input_value("#sky"), "21.1")
        page.select_option("#telescope", "rbo")
        self.assertEqual(page.input_value("#diameter"), "0.6")
        page.fill("#readNoise", "12")
        self.assertEqual(page.input_value("#telescope"), "custom")
        page.click("#reset")
        self.assertEqual(page.input_value("#telescope"), "rbo")
        self.assertEqual(page.input_value("#readNoise"), "9")
        page.fill("#airmass", "0.5")
        page.wait_for_function("document.querySelector('#etc-status').textContent.includes('Airmass')")
        self.assertEqual(page.get_attribute("#airmass", "aria-invalid"), "true")
        self.assertIn("Placeholder values", page.text_content("#placeholder-note"))
        self.assertEqual(page.errors, [])
        self.assertEqual(page.requests, [])
        page.close()

    # ------------------------------------------------------------------ finder
    def open_finder(self, scheme="light", width=1280):
        page = self.open("tools/finder/?ra=202.469575&dec=47.195258&name=M%2051&fov=10", scheme, width)
        page.wait_for_function("document.querySelector('#finder-status').textContent === 'Chart ready.'", timeout=15000)
        return page

    def test_finder_chart_from_url_parameters(self):
        page = self.open_finder()
        src = page.get_attribute("#chart-img", "src")
        self.assertTrue(src.startswith(HIPS2FITS + "?hips=CDS/P/DSS2/red&width=1000&height=1000&fov=0.166667"), src)
        self.assertIn("ra=202.469575&dec=47.195258&format=jpg", src)
        self.assertGreaterEqual(page.locator("#overlay-slot svg.overlay line").count(), 9)  # reticle, compass, scale bar
        caption = page.inner_text("#caption")
        for expected in ("M 51", "13:29:52.70", "+47:11:42.9", "north up, east left", "scale bar 2′", "DSS2 red"):
            self.assertIn(expected, caption)
        self.assertFalse(page.is_disabled("#print"))
        page.fill("#offset", "13 29 50 +47 13 00")
        page.click("#offset-form button[type=submit]")
        self.assertIn("move the telescope", page.inner_text("#offset-result"))
        self.assertEqual(page.locator("#overlay-slot rect.offset").count(), 1)
        self.assertIn("From the offset star, move", page.inner_text("#caption"))
        page.select_option("#survey", "2mass-j")
        page.wait_for_function("document.querySelector('#chart-img').src.includes('CDS/P/2MASS/J')")
        self.assertTrue(all(u.startswith(HIPS2FITS) for u in page.requests), page.requests)
        self.assertEqual(page.errors, [])
        page.close()

    def test_finder_print_layout(self):
        page = self.open_finder()
        page.emulate_media(media="print")
        self.assertTrue(page.is_hidden("header.top"))
        self.assertTrue(page.is_hidden("#finder-form"))
        self.assertTrue(page.is_hidden(".notes"))
        self.assertTrue(page.is_visible("#chart-figure"))
        self.assertTrue(page.is_visible("#caption"))
        self.assertIn("invert(1)", page.eval_on_selector("#chart-img", "el => getComputedStyle(el).filter"))
        self.assertEqual(page.eval_on_selector("#overlay-slot svg line", "el => getComputedStyle(el).stroke"), "rgb(0, 0, 0)")
        for paper in ("A4", "Letter"):
            pdf = page.pdf(format=paper)
            self.assertTrue(pdf.startswith(b"%PDF"))
            self.assertEqual(len(re.findall(rb"/Type\s*/Page[^s]", pdf)), 1, f"the chart prints on one {paper} page")
        page.close()

    def test_finder_name_lookup(self):
        page = self.open("tools/finder/")
        page.fill("#target", "M 51")
        page.click("#finder-form button[type=submit]")
        page.wait_for_function("document.querySelector('#finder-status').textContent === 'Chart ready.'", timeout=15000)
        self.assertIn("ra=202.469575&dec=47.195258", page.get_attribute("#chart-img", "src"))
        self.assertIn("M 51", page.inner_text("#caption"))
        self.assertTrue(any(u.startswith(SESAME + "-oI/A?M%2051") for u in page.requests), page.requests)
        self.assertEqual(page.errors, [])
        page.close()
        page = self.open("tools/finder/", sesame="notfound")
        page.fill("#target", "xyzzy")
        page.click("#finder-form button[type=submit]")
        page.wait_for_function("document.querySelector('#finder-status').textContent.includes('did not find')")
        page.close()

    def test_finder_lookup_failure_says_type_coordinates(self):
        page = self.open("tools/finder/", sesame="refuse")
        page.fill("#target", "M 51")
        page.click("#finder-form button[type=submit]")
        page.wait_for_function("document.querySelector('#finder-status').textContent.includes('Type coordinates instead')")
        self.assertEqual(page.get_attribute("#target", "aria-invalid"), "true")
        self.assertTrue(page.is_hidden("#chart-img"))
        page.close()

    # ------------------------------------------------------------------ layout and accessibility
    def ready(self, name, scheme="light"):
        if name == "coordinates":
            page = self.open_coordinates(scheme)
            page.fill("#batch", "M 31 00 42 44.3 +41 16 09\nA very long target name that should wrap 12 30 00 -45 00 00\nbad 99 00 00 +10 00 00")
            page.click("#batch-form button[type=submit]")
            page.wait_for_selector("#batch-results tbody tr")
        elif name == "exposure":
            page = self.open("tools/exposure/", scheme)
            page.wait_for_selector("#curve svg")
        elif name == "finder":
            page = self.open_finder(scheme)
            page.fill("#offset", "13 29 50 +47 13 00")
            page.click("#offset-form button[type=submit]")
        else:
            page = self.open("tools/", scheme)
        return page

    def test_no_page_scrolls_sideways(self):
        for name in ("index", "coordinates", "exposure", "finder"):
            with self.subTest(page=name):
                page = self.ready(name)
                self.no_sideways_scroll(page)
                page.close()

    def test_axe_reports_no_wcag_violation(self):
        if self.axe is None:
            self.skipTest("axe-core is not installed; run: npm install axe-core")
        for name in ("index", "coordinates", "exposure", "finder"):
            for scheme in ("light", "dark"):
                with self.subTest(page=name, scheme=scheme):
                    page = self.ready(name, scheme)
                    self.assertEqual(self.axe_violations(page), [])
                    page.close()

    def test_pages_have_the_shared_head_and_nav(self):
        for path, canonical in (("tools/", "https://jackmcguireastro.github.io/tools/"),
                                ("tools/coordinates/", "https://jackmcguireastro.github.io/tools/coordinates/"),
                                ("tools/exposure/", "https://jackmcguireastro.github.io/tools/exposure/"),
                                ("tools/finder/", "https://jackmcguireastro.github.io/tools/finder/")):
            with self.subTest(path=path):
                page = self.open(path)
                self.assertEqual(page.get_attribute("link[rel=canonical]", "href"), canonical)
                self.assertTrue(page.get_attribute("meta[name=description]", "content"))
                self.assertEqual(page.eval_on_selector_all("nav[aria-label=Primary] a", "els => els.map(e => e.textContent)"),
                                 ["Home", "WorldsIndex", "CTAS", "Tonight", "Tools"])
                self.assertEqual(page.get_attribute(".skip-link", "href"), "#main")
                self.assertIn("How this is worked out", page.inner_text("main") if path != "tools/" else "How this is worked out")
                for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.href)"):
                    if href.startswith(self.base) and "#" not in href:
                        status = page.request.get(href).status
                        self.assertEqual(status, 200, href)
                page.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)

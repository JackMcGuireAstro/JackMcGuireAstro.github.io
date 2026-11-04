#!/usr/bin/env python3
"""Site-wide links and navigation (2026-11-04, the connected-site batch).

1. Every internal link on every page of the site (href and src, relative or root-relative to
   this site) resolves to a file in the tree, and every #fragment to an id or name on the
   target page. Data files that the deploy overlays from the data branches or builds itself
   (ctas/data, worldsindex/data, tools/data, release/manifest.json) are not in the tree and are
   only checked for their form; a page that links an absent one would be caught by the
   browser suites against real data.
2. Every real page carries the same primary navigation, in the same order, ending with the
   run-list badge, and loads the run-list scripts so the badge counts.
3. No page links QualQuest (removed 2026-11-04).
"""
from __future__ import annotations

import re
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit, unquote

ROOT = Path(__file__).resolve().parents[1]
SITE = "https://jackmcguireastro.github.io"
DEPLOY_BUILT = ("ctas/data/", "worldsindex/data/", "tools/data/", "release/manifest.json")
NAV = ["Home", "Research", "WorldsIndex", "CTAS", "Tonight", "Follow", "Tools", "Run list"]
REAL_PAGES = [
    "index.html", "research/barium-dwarfs/index.html", "tools/index.html", "tools/follow/index.html", "tools/sql/index.html",
    "tools/cone/index.html", "tools/notebook/index.html", "tools/finder/index.html", "tools/exposure/index.html",
    "tools/coordinates/index.html", "tonight/index.html", "tonight/runsheet.html", "ctas.html", "ctas/status.html",
    "worldsindex/index.html", "worldsindex/status.html",
]


class Page(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.ids: set[str] = set()
        self.navs: list[list[tuple[str, dict]]] = []   # primary navs: their links (text, attrs)
        self._nav_depth = 0
        self._in_a: dict | None = None
        self._text = ""
        self.scripts: list[str] = []
        self.inline_scripts: list[str] = []
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for key in ("id", "name"):
            if a.get(key):
                self.ids.add(a[key])
        if tag in ("a", "link", "area") and a.get("href"):
            self.links.append(a["href"])
        if tag in ("img", "script", "source", "iframe") and a.get("src"):
            self.links.append(a["src"])
            if tag == "script":
                self.scripts.append(a["src"])
        if tag == "script" and not a.get("src"):
            self._in_script = True
        if tag == "nav" and (a.get("aria-label") in ("Primary", "Astronomy website")):
            self.navs.append([])
            self._nav_depth = 1
        elif tag == "nav" and self._nav_depth:
            self._nav_depth += 1
        if tag == "a" and self._nav_depth:
            self._in_a, self._text = a, ""

    def handle_endtag(self, tag):
        if tag == "a" and self._in_a is not None:
            self.navs[-1].append((re.sub(r"\s+", " ", self._text).strip(), self._in_a))
            self._in_a = None
        if tag == "nav" and self._nav_depth:
            self._nav_depth -= 1
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_a is not None:
            self._text += data
        if self._in_script:
            self.inline_scripts.append(data)


def tracked_pages() -> list[str]:
    out = subprocess.run(["git", "ls-files", "*.html"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()
    return [p for p in out if not p.startswith("tests/")]


def parse(page: str) -> Page:
    parser = Page()
    parser.feed((ROOT / page).read_text(encoding="utf-8"))
    return parser


def target_of(page: str, href: str) -> tuple[Path | None, str]:
    """The file a link points at in this tree (None when it is external or not checkable) and its fragment."""
    if href.startswith(("mailto:", "tel:", "javascript:", "data:", "blob:")):
        return None, ""
    parts = urlsplit(href)
    if parts.scheme in ("http", "https"):
        if f"{parts.scheme}://{parts.netloc}" != SITE:
            return None, ""
        path = parts.path.lstrip("/") or "index.html"
        base = ROOT
    elif parts.scheme or parts.netloc:
        return None, ""
    else:
        path = unquote(parts.path)
        base = (ROOT / page).parent if not path.startswith("/") else ROOT
        path = path.lstrip("/") if path.startswith("/") else path
    target = (base / path).resolve() if path else (ROOT / page).resolve()
    if target.is_dir() or path.endswith("/"):
        target = target / "index.html"
    return target, parts.fragment


class SiteLinkTests(unittest.TestCase):
    def test_every_internal_link_resolves(self):
        problems = []
        cache: dict[Path, Page] = {}
        for page in tracked_pages():
            parsed = parse(page)
            for href in parsed.links:
                target, fragment = target_of(page, href)
                if target is None:
                    continue
                try:
                    rel = target.relative_to(ROOT).as_posix()
                except ValueError:
                    problems.append(f"{page}: {href} leaves the site tree")
                    continue
                if rel.startswith(DEPLOY_BUILT) or rel.startswith(".github/"):
                    continue
                if not target.exists():
                    problems.append(f"{page}: {href} → {rel} does not exist")
                    continue
                if fragment and target.suffix == ".html" and not fragment.startswith(("/", "!", ":~:")) and "=" not in fragment:
                    if target not in cache:
                        cache[target] = parse(rel)
                    if fragment not in cache[target].ids:
                        problems.append(f"{page}: {href} → no id or name “{fragment}” on {rel}")
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_real_page_has_the_same_primary_navigation(self):
        for page in REAL_PAGES:
            with self.subTest(page=page):
                navs = parse(page).navs
                self.assertTrue(navs, "no primary navigation")
                labels = [text for text, _ in navs[0]]
                self.assertEqual(labels[:len(NAV) - 1] + [labels[-1].split(" (")[0]], NAV, labels)
                badge = navs[0][-1][1]
                self.assertIn("data-runcart-badge", badge)
                self.assertTrue(badge["href"].endswith("tonight/runsheet.html"), badge["href"])
                for text, attrs in navs[0]:
                    target, _ = target_of(page, attrs["href"])
                    self.assertTrue(target is not None and target.exists(), f"{text}: {attrs['href']}")

    def test_every_real_page_loads_the_run_list(self):
        for page in REAL_PAGES:
            with self.subTest(page=page):
                parsed = parse(page)
                src = " ".join(parsed.scripts) + " " + " ".join(parsed.inline_scripts)
                if page == "ctas.html":   # the CTAS page loads them as deferred modules from ctas/app.js
                    src += (ROOT / "ctas/app.js").read_text()
                self.assertIn("tools/lib/run-cart.js", src)
                self.assertIn("tools/lib/run-cart-ui.js", src)

    def test_qualquest_is_gone(self):
        self.assertFalse((ROOT / "qualquest").exists())
        for page in tracked_pages():
            with self.subTest(page=page):
                self.assertNotIn("qualquest/", (ROOT / page).read_text(encoding="utf-8").lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)

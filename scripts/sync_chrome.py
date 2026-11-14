#!/usr/bin/env python3
"""One header and footer for every page (site review A2, 2026-11-14).

The site is hand-written HTML with no templating, so the shared chrome is pasted into each
page between markers:

    <!-- chrome:header --> ... <!-- /chrome:header -->
    <!-- chrome:footer --> ... <!-- /chrome:footer -->

The single source is assets/partials/header.html and footer.html (root-absolute links, so the
same bytes work at any depth).

    python3 scripts/sync_chrome.py           rewrite every marked block from the partials
    python3 scripts/sync_chrome.py --check   exit 1 if a page's block differs from its partial,
                                             a page in PAGES lacks a block, or a page in PAGES
                                             does not load chrome.css and chrome.js

The --check form is the advisory deploy check site.chrome (scripts/deploy_checks.sh, content).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARTIALS = {name: ROOT / "assets" / "partials" / f"{name}.html" for name in ("header", "footer")}
# Every page that carries the site chrome.
PAGES = [
    "index.html", "404.html", "research/barium-dwarfs/index.html",
    "tonight/index.html", "tonight/runsheet.html",
    "tools/index.html", "tools/follow/index.html", "tools/sql/index.html", "tools/cone/index.html",
    "tools/notebook/index.html", "tools/finder/index.html", "tools/exposure/index.html", "tools/coordinates/index.html",
    "worldsindex/index.html", "worldsindex/status.html",
    "ctas.html", "ctas/status.html",
]
ASSETS = ("/assets/css/chrome.css", "/assets/js/chrome.js")


def block_re(name: str) -> re.Pattern:
    return re.compile(rf"<!-- chrome:{name} -->\n(.*?)<!-- /chrome:{name} -->", re.S)


def partial(name: str) -> str:
    return PARTIALS[name].read_text(encoding="utf-8").rstrip("\n") + "\n"


def problems_in(page: str, text: str) -> list[str]:
    out = []
    for name in PARTIALS:
        blocks = block_re(name).findall(text)
        if len(blocks) != 1:
            out.append(f"{page}: expected one chrome:{name} block, found {len(blocks)}")
        elif blocks[0] != partial(name):
            out.append(f"{page}: the chrome:{name} block differs from assets/partials/{name}.html (run scripts/sync_chrome.py)")
    for asset in ASSETS:
        if asset not in text:
            out.append(f"{page}: does not load {asset}")
    return out


def main(argv: list[str]) -> int:
    check = "--check" in argv
    problems: list[str] = []
    changed = []
    for page in PAGES:
        path = ROOT / page
        if not path.exists():
            problems.append(f"{page}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        if check:
            problems += problems_in(page, text)
            continue
        new = text
        for name in PARTIALS:
            new = block_re(name).sub(lambda _m, n=name: f"<!-- chrome:{n} -->\n{partial(n)}<!-- /chrome:{n} -->", new)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed.append(page)
        problems += problems_in(page, new)
    if not check:
        print(f"rewrote {len(changed)} page(s): {', '.join(changed) or 'none'}")
    for line in problems:
        print(line)
    if check and not problems:
        print(f"site chrome: {len(PAGES)} pages match assets/partials/header.html and footer.html")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""
sync_ctas.py, mirror the CTAS project's real state onto this website.

The CTAS page (ctas.html) makes factual claims about the Cowboy Transient
Alert System: its version, how many test modules it has, which capabilities
are implemented versus planned. Those facts drift every time CTAS changes.
This script re-reads them straight from the CTAS source tree and:

  1. writes the extracted facts to assets/data/ctas-status.json;
  2. updates the small auto-maintained values inside ctas.html, the ones
     marked with data-ctas="..." attributes, in place;
  3. tells you, loudly, when the CTAS capability documentation has changed
     since the last sync, so the hand-written prose on the page can be
     reviewed by a human instead of being silently regenerated wrong.

Deliberately, it does NOT rewrite the descriptive prose. Auto-generated
capability text reads badly and, worse, can quietly overstate what the
software does. Facts are synced; wording stays a human decision.

USAGE
    python3 tools/sync_ctas.py /path/to/ctas/source
    python3 tools/sync_ctas.py /path/to/ctas/source --check   # report only

The CTAS source path is always supplied on the command line and is never
written into this repository, because the repository is published.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SITE = os.path.dirname(HERE)
STATUS_JSON = os.path.join(SITE, "assets", "data", "ctas-status.json")
CTAS_HTML = os.path.join(SITE, "ctas.html")

CAPABILITY_DOC = os.path.join("docs", "professional-capability-closure.md")


# --------------------------------------------------------------------------
# reading the CTAS source
# --------------------------------------------------------------------------
def _read(path: str) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def looks_like_ctas(root: str) -> bool:
    """Refuse to run against a directory that clearly is not CTAS."""
    pyproject = _read(os.path.join(root, "pyproject.toml")) or ""
    return "supernova" in pyproject.lower() and os.path.isdir(
        os.path.join(root, "src", "supernova_watch")
    )


def collect(root: str) -> dict:
    pyproject = _read(os.path.join(root, "pyproject.toml")) or ""

    def toml_value(key: str) -> str | None:
        m = re.search(rf'^{key}\s*=\s*"([^"]+)"', pyproject, re.M)
        return m.group(1) if m else None

    tests_dir = os.path.join(root, "tests")
    test_modules = sorted(
        f for f in os.listdir(tests_dir)
        if f.startswith("test_") and f.endswith(".py")
    ) if os.path.isdir(tests_dir) else []

    conn_dir = os.path.join(root, "src", "supernova_watch", "connectors")
    connectors = sorted(
        os.path.splitext(f)[0] for f in os.listdir(conn_dir)
        if f.endswith(".py") and not f.startswith("__")
        and os.path.splitext(f)[0] not in {"base", "manager", "utils"}
    ) if os.path.isdir(conn_dir) else []

    pkg_dir = os.path.join(root, "src", "supernova_watch")
    modules = sorted(
        os.path.splitext(f)[0] for f in os.listdir(pkg_dir)
        if f.endswith(".py") and not f.startswith("__")
    ) if os.path.isdir(pkg_dir) else []

    docs_dir = os.path.join(root, "docs")
    docs = sorted(f for f in os.listdir(docs_dir) if f.endswith(".md")) \
        if os.path.isdir(docs_dir) else []

    cap_text = _read(os.path.join(root, CAPABILITY_DOC)) or ""
    cap_hash = hashlib.sha256(cap_text.encode("utf-8")).hexdigest() if cap_text else None

    # The project's own one-line self-assessment, quoted rather than paraphrased.
    honest_grade = None
    m = re.search(r"##\s*Honest grade\s*\n+(.+?)(?:\n\n|\Z)", cap_text, re.S)
    if m:
        honest_grade = re.sub(r"\s+", " ", re.sub(r"[*`]", "", m.group(1))).strip()

    # Section headings inside the capability doc, so drift can be localised.
    cap_sections = re.findall(r"^###?\s+(.+)$", cap_text, re.M)

    return {
        "name": "Cowboy Transient Alert System",
        "acronym": "CTAS",
        "package_name": toml_value("name"),
        "version": toml_value("version"),
        "requires_python": toml_value("requires-python"),
        "description": toml_value("description"),
        "test_module_count": len(test_modules),
        "connector_count": len(connectors),
        "connectors": connectors,
        "module_count": len(modules),
        "doc_count": len(docs),
        "docs": docs,
        "capability_doc": CAPABILITY_DOC,
        "capability_doc_sha256": cap_hash,
        "capability_sections": cap_sections,
        "honest_grade": honest_grade,
        "public_deployment": False,
        "status_label": "Active development",
    }


# --------------------------------------------------------------------------
# writing back into the site
# --------------------------------------------------------------------------
def update_html(facts: dict, today: str) -> list[str]:
    """Replace the contents of <span data-ctas="KEY">…</span> markers."""
    html = _read(CTAS_HTML)
    if html is None:
        raise SystemExit(f"cannot read {CTAS_HTML}")

    replacements = {
        "version": facts.get("version") or "",
        "tests": str(facts.get("test_module_count") or ""),
        "synced": today,
    }

    changed: list[str] = []
    for key, value in replacements.items():
        if not value:
            continue
        pattern = re.compile(
            rf'(<span data-ctas="{re.escape(key)}">)(.*?)(</span>)', re.S
        )

        def _sub(m, _v=value, _k=key):
            if m.group(2) != _v:
                changed.append(f'{_k}: "{m.group(2)}" -> "{_v}"')
            return m.group(1) + _v + m.group(3)

        html, n = pattern.subn(_sub, html)
        if n == 0:
            print(f'  ! no data-ctas="{key}" marker found in ctas.html', file=sys.stderr)

    with open(CTAS_HTML, "w", encoding="utf-8") as fh:
        fh.write(html)
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Mirror the CTAS project's real state onto this website."
    )
    ap.add_argument("ctas_source", help="path to the CTAS source tree (contains pyproject.toml and src/)")
    ap.add_argument("--check", action="store_true",
                    help="report what would change; write nothing")
    ap.add_argument("--date", default=None,
                    help="override the sync date (default: today, e.g. '18 August 2026')")
    args = ap.parse_args()

    root = os.path.abspath(os.path.expanduser(args.ctas_source))
    if not os.path.isdir(root):
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2
    if not looks_like_ctas(root):
        print(f"error: {root} does not look like the CTAS source tree "
              f"(expected pyproject.toml naming supernova* and src/supernova_watch/)",
              file=sys.stderr)
        return 2

    facts = collect(root)
    today = args.date or _dt.date.today().strftime("%-d %B %Y")
    facts["synced"] = today

    previous = {}
    if os.path.exists(STATUS_JSON):
        try:
            with open(STATUS_JSON, encoding="utf-8") as fh:
                previous = json.load(fh)
        except (OSError, ValueError):
            previous = {}

    print(f"CTAS {facts['version']}, {facts['test_module_count']} test modules, "
          f"{facts['connector_count']} connectors, {facts['module_count']} modules")
    if facts["honest_grade"]:
        print(f"  project self-assessment: {facts['honest_grade'][:150]}…")

    # Flag capability-documentation drift for human review.
    old_hash = previous.get("capability_doc_sha256")
    new_hash = facts.get("capability_doc_sha256")
    if old_hash and new_hash and old_hash != new_hash:
        print("\n  *** The CTAS capability documentation has CHANGED since the last sync. ***")
        print("      The prose on ctas.html (workflow stages, capability columns, technical")
        print("      shape) is written by hand and is NOT regenerated automatically.")
        print(f"      Re-read {CAPABILITY_DOC} and update ctas.html where it no longer matches.")
        old_sections = set(previous.get("capability_sections") or [])
        new_sections = set(facts.get("capability_sections") or [])
        for s in sorted(new_sections - old_sections):
            print(f"        + new section: {s}")
        for s in sorted(old_sections - new_sections):
            print(f"        - removed section: {s}")
    elif not old_hash:
        print("\n  (first sync, recording a baseline of the capability documentation)")

    if args.check:
        print("\n--check: nothing written.")
        return 0

    os.makedirs(os.path.dirname(STATUS_JSON), exist_ok=True)
    with open(STATUS_JSON, "w", encoding="utf-8") as fh:
        json.dump(facts, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(f"\nwrote {os.path.relpath(STATUS_JSON, SITE)}")

    changed = update_html(facts, today)
    if changed:
        print("updated ctas.html:")
        for c in changed:
            print(f"    {c}")
    else:
        print("ctas.html already current")

    print("\nNothing was committed or pushed. Review with: git diff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

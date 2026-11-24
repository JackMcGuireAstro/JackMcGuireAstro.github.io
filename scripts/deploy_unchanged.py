#!/usr/bin/env python3
"""Which products the deploy need not re-check: their site code and data release are the
ones the live deployment already checked.

    deploy_unchanged.py --live release-manifest.json --site-commit SHA --ctas-commit SHA
                        --worldsindex-commit SHA [--enabled 1]

Reads the live site's release/manifest.json (scripts/build_release_manifest.py: site_commit,
ctas.data_commit, worldsindex.data_commit) and prints two lines for $GITHUB_ENV:

    CHECK_UNCHANGED=ctas,worldsindex      (empty when anything differs or the manifest is unreadable)
    CHECK_UNCHANGED_WHY=live deployment of main abcdef1 ...

A product counts as unchanged only when main's commit equals the live site's commit (a
fallback deployment serves another commit, so nothing is skipped after one) and the product's
data commit equals the live one. The deploy records such a product's checks as skipped
(scripts/release_checks.sh); its data and code are the bytes that passed when the live
deployment was verified. 2026-11-24: deploys took 20-51 minutes (median 26), most of it
re-checking the product that had not changed since the previous deploy.
"""
from __future__ import annotations

import argparse
import json
import sys


def unchanged(live: dict, site_commit: str, ctas_commit: str, worldsindex_commit: str, enabled: bool = True) -> tuple[list[str], str]:
    """The products whose inputs equal the live deployment's, and why. Pure function."""
    if not enabled:
        return [], "DEPLOY_SKIP_UNCHANGED=0: every check runs"
    if not isinstance(live, dict) or live.get("schema") != "jmastro.release-manifest@1":
        return [], "no readable release manifest on the live site: every check runs"
    live_site = live.get("site_commit") or ""
    if not site_commit or live_site != site_commit:
        return [], f"main {site_commit[:10]} differs from the live site's {live_site[:10] or 'unknown'}: every check runs"
    products = []
    for name, commit in (("ctas", ctas_commit), ("worldsindex", worldsindex_commit)):
        live_commit = ((live.get(name) or {}).get("data_commit")) or ""
        if commit and live_commit == commit:
            products.append(name)
    if not products:
        return [], f"both data releases are newer than the live deployment of main {site_commit[:10]}: every check runs"
    return products, (f"live deployment of main {site_commit[:10]} with the same "
                      + " and ".join(f"{name}-data {((live.get(name) or {}).get('data_commit') or '')[:10]}" for name in products))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--live", required=True, help="the live site's release/manifest.json (may be unreadable)")
    parser.add_argument("--site-commit", required=True)
    parser.add_argument("--ctas-commit", default="")
    parser.add_argument("--worldsindex-commit", default="")
    parser.add_argument("--enabled", default="1")
    args = parser.parse_args(argv)
    try:
        with open(args.live, encoding="utf-8") as handle:
            live = json.load(handle)
    except (OSError, ValueError):
        live = {}
    products, why = unchanged(live, args.site_commit, args.ctas_commit, args.worldsindex_commit, args.enabled != "0")
    print(f"CHECK_UNCHANGED={','.join(products)}")
    print(f"CHECK_UNCHANGED_WHY={why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

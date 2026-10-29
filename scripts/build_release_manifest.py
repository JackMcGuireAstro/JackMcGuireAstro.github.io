#!/usr/bin/env python3
"""Write release/manifest.json into the site tree about to be deployed (Track 7, gate 9).

One machine-readable record of what this deployment is: the site code commit, the CTAS and
WorldsIndex data commits and their release identifiers and checksums, the versioned source
universes, the known-limitations statement, the benchmark and reproducibility files (each
with its SHA-256), and the DOI state. The manifest is made at deploy time from the files
being deployed, never from memory, so it cannot go stale; it is not part of either
certified release, and a failure here never stops a deploy.

    python3 scripts/build_release_manifest.py --tree <site tree> [--ctas-commit SHA] [--worldsindex-commit SHA]
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import subprocess
from pathlib import Path

EVIDENCE = (
    "KNOWN_LIMITATIONS.md", "benchmarks/README.md", "benchmarks/identity/worldsindex-gold-set.json",
    "benchmarks/sources/ctas-sources.json", "benchmarks/sources/worldsindex-sources.json", "benchmarks/science/fixtures.json",
    "benchmarks/performance/budgets.json", "benchmarks/performance/latest.json", "repro/ctas/MANIFEST.json",
    "repro/worldsindex/MANIFEST.json", "review/README.md",
)


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def read(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def build(tree: Path, ctas_commit: str | None, wi_commit: str | None) -> dict:
    try:
        site = subprocess.run(["git", "-C", str(tree), "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        site = None
    status = read(tree / "ctas/data/status.json") or {}
    certificate = tree / "ctas/data/certification.json"
    universe = read(tree / "ctas/data/source-universe.json") or {}
    wi = read(tree / "worldsindex/data/manifest.json") or {}
    return {
        "schema": "jmastro.release-manifest@1",
        "built_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "site_commit": site,
        "ctas": {"data_commit": ctas_commit, "last_successful_update": status.get("last_successful_update"),
                 "candidate_count": status.get("candidate_count"), "catalog_content_checksum_sha256": status.get("catalog_content_checksum_sha256"),
                 "certificate_sha256": sha(certificate), "certificate_status": (read(certificate) or {}).get("status"),
                 "source_universe": {"file": "ctas/data/source-universe.json", "source_count": universe.get("source_count"),
                                     "artifact_checksum_sha256": universe.get("artifact_checksum_sha256"),
                                     "contract_set_checksum_sha256": universe.get("contract_set_checksum_sha256")}},
        "worldsindex": {"data_commit": wi_commit, "generatedAt": wi.get("generatedAt"), "objectCount": wi.get("objectCount"),
                        "detailRecordCount": wi.get("detailRecordCount"), "manifest_sha256": sha(tree / "worldsindex/data/manifest.json"),
                        "catalogIndexSha256": wi.get("catalogIndexSha256"),
                        "source_registry": {"file": "worldsindex/data/registry.json.gz", "sha256": sha(tree / "worldsindex/data/registry.json.gz")}},
        "evidence": {path: sha(tree / path) for path in EVIDENCE},
        "doi": None,
        "doi_state": "not minted: nothing is published or registered at this stage (owner's decision of 2026-10-19); see release/README.md",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", default=".")
    parser.add_argument("--ctas-commit")
    parser.add_argument("--worldsindex-commit")
    args = parser.parse_args()
    tree = Path(args.tree)
    manifest = build(tree, args.ctas_commit, args.worldsindex_commit)
    out = tree / "release" / "manifest.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    missing = [p for p, s in manifest["evidence"].items() if s is None]
    print(f"release manifest written: site {str(manifest['site_commit'])[:10]}, CTAS {manifest['ctas']['candidate_count']} events, "
          f"WorldsIndex {manifest['worldsindex']['objectCount']} objects" + (f"; missing evidence files: {missing}" if missing else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

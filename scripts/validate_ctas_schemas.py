#!/usr/bin/env python3
"""
validate_ctas_schemas.py, check every published CTAS JSON file against its JSON Schema.

The schemas under ctas/schemas/ are the response schemas that ctas/openapi.yaml
names for each static endpoint. This step runs check-jsonschema (pip install
check-jsonschema) over the generated release in the publisher's test phase and in the
"Validate and deploy site" workflow, so a release whose JSON drifts from its declared
shape is never committed or deployed.

    python3 scripts/validate_ctas_schemas.py [--site-root .] [--data-dir ctas/data]
                                             [--all-chunks] [--chunk-budget-mib 24]

Sampling rule for the 4,096 dossier chunk roots (stated here and in the output): the
roots whose bucket ends in the hex digit 0 (256 buckets, one in sixteen, spread over
the whole UUID space) in bucket order until their bytes reach the budget (24 MiB by
default; check-jsonschema validates a dossier chunk at about 0.7 s per MiB, so the
sample costs about 20 s), plus the largest root by bytes, plus every parts descriptor
and every part file (there are few, and they are the hardest case). --all-chunks
validates all 4,096 roots. Everything else (status, live summary, catalog index, every
catalog page, every manifest, the source universe, the release history, the link-health
report, the certificate, bulk-downloads, the DataLink manifest, the provenance
document, the cloud-run marker, observatories.json and ctas/delivery.json) is validated
completely. The whole step is budgeted to stay under about 60 s on the real release.

Exit status 0 when every file validates, 1 on a schema violation, 2 when a required
file or the validator is missing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "ctas/schemas"

# published path (relative to the data directory) -> schema name
FIXED_FILES = {
    "status.json": "status",
    "live-summary.json": "live-summary",
    "catalog-index.json": "catalog-index",
    "catalog-pages/manifest.json": "catalog-page-manifest",
    "candidate-chunks/manifest.json": "candidate-chunk-manifest",
    "alias-index.json": "alias-index",
    "source-universe.json": "source-universe",
    "source-matrix-patterns.json": "source-matrix-patterns",
    "research/manifest.json": "research-manifest",
    "release-history.json": "release-history",
    "certification.json": "certification",
}
# Track 1 products: required exactly when status.json declares their path (a release
# built before Track 1 declares none, and the deploy workflow validates whatever the
# data branch currently holds).
DECLARED_FILES = {
    "bulk-downloads.json": ("bulk_downloads_path", "bulk-downloads"),
    "links/manifest.json": ("datalink_manifest_path", "datalink-manifest"),
    "provenance.json": ("provenance_path", "provenance"),
}
# Present only after the link checker ran (the publisher) or in a deployed tree; the
# cloud-run marker only when the GitHub Actions publisher built the release.
OPTIONAL_FILES = {
    "link-health.json": "link-health",
    "cloud-run.json": "cloud-run",
}
# Fixed inputs under the data directory (certificate-bound, not written by the exporter).
FIXED_INPUT_FILES = {
    "observatories.json": "observatories",
}
SAMPLED_CHUNK_SUFFIX = "0"
DEFAULT_CHUNK_BUDGET_MIB = 24


def validator_command() -> list[str] | None:
    binary = shutil.which("check-jsonschema")
    if binary:
        return [binary]
    probe = subprocess.run([sys.executable, "-m", "check_jsonschema", "--version"], capture_output=True, text=True)
    if probe.returncode == 0:
        return [sys.executable, "-m", "check_jsonschema"]
    return None


def chunk_sample(
    data_dir: Path, all_chunks: bool, budget_mib: float = DEFAULT_CHUNK_BUDGET_MIB,
) -> tuple[list[Path], list[Path], list[Path], str]:
    manifest = json.loads((data_dir / "candidate-chunks/manifest.json").read_text())
    roots = manifest.get("chunks", [])
    parts = manifest.get("parts", [])
    if all_chunks:
        selected = list(roots)
        rule = f"all {len(roots)} chunk roots"
    else:
        budget = int(budget_mib * 1024 * 1024)
        eligible = sorted(
            (row for row in roots if Path(row["path"]).stem.endswith(SAMPLED_CHUNK_SUFFIX)),
            key=lambda row: row["path"],
        )
        selected, used = [], 0
        for row in eligible:
            size = int(row.get("bytes", 0))
            if selected and used + size > budget:
                break
            selected.append(row)
            used += size
        largest = max(roots, key=lambda row: row.get("bytes", 0), default=None)
        if largest and largest not in selected:
            selected.append(largest)
            used += int(largest.get("bytes", 0))
        rule = (
            f"{len(selected)} of {len(roots)} chunk roots ({used / (1024 * 1024):.1f} MiB): buckets ending in hex "
            f"{SAMPLED_CHUNK_SUFFIX} (one in sixteen, {len(eligible)} eligible) in bucket order up to {budget_mib:g} MiB, "
            f"plus the largest root ({Path(largest['path']).name if largest else 'none'})"
        )
    root_paths = [data_dir / Path(row["path"]).relative_to("ctas/data") for row in selected]
    descriptor_paths: list[Path] = []
    for row in roots:
        target = data_dir / Path(row["path"]).relative_to("ctas/data")
        if target.exists() and target.stat().st_size < 64 * 1024:
            try:
                if json.loads(target.read_text()).get("schema") == "ctas.candidate-chunk-parts@1.0.0":
                    descriptor_paths.append(target)
            except (OSError, ValueError):
                continue
    part_paths = [data_dir / Path(row["path"]).relative_to("ctas/data") for row in parts]
    return root_paths, descriptor_paths, part_paths, rule


def run(command: list[str], schema: Path, files: list[Path]) -> int:
    if not files:
        return 0
    result = subprocess.run(
        [*command, "--no-cache", "--schemafile", str(schema), *[str(path) for path in files]],
        capture_output=True, text=True,
    )
    label = f"{schema.name:36s} {len(files):5d} file(s)"
    if result.returncode == 0:
        print(f"ok    {label}")
        return 0
    print(f"FAIL  {label}")
    lines = (result.stdout + result.stderr).strip().splitlines()
    for line in lines[:12]:
        print("      " + line[:400])
    if len(lines) > 12:
        print(f"      ... {len(lines) - 12} more line(s)")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--site-root", default=str(ROOT))
    parser.add_argument("--data-dir", default=None, help="default: <site-root>/ctas/data")
    parser.add_argument("--all-chunks", action="store_true", help="validate every chunk root instead of the sample")
    parser.add_argument("--chunk-budget-mib", type=float, default=DEFAULT_CHUNK_BUDGET_MIB,
                        help=f"bytes of sampled chunk roots to validate (default {DEFAULT_CHUNK_BUDGET_MIB} MiB)")
    args = parser.parse_args()
    started = time.monotonic()
    site_root = Path(args.site_root).resolve()
    data_dir = Path(args.data_dir).resolve() if args.data_dir else site_root / "ctas/data"
    command = validator_command()
    if command is None:
        print("error: check-jsonschema is not installed (pip install check-jsonschema)", file=sys.stderr)
        return 2
    schemas = sorted(SCHEMA_DIR.glob("*.schema.json"))
    if not schemas:
        print(f"error: no schemas under {SCHEMA_DIR}", file=sys.stderr)
        return 2
    failures = 0
    # The schemas themselves must be valid JSON Schema 2020-12 documents.
    meta = subprocess.run([*command, "--no-cache", "--check-metaschema", *[str(path) for path in schemas]], capture_output=True, text=True)
    if meta.returncode != 0:
        print("FAIL  metaschema check of ctas/schemas")
        print("      " + (meta.stdout + meta.stderr).strip()[:1200])
        failures += 1
    else:
        print(f"ok    {'metaschema (JSON Schema 2020-12)':36s} {len(schemas):5d} schema(s)")
    missing = [name for name in FIXED_FILES if not (data_dir / name).exists()]
    if missing:
        print(f"error: published files missing under {data_dir}: {', '.join(missing)}", file=sys.stderr)
        return 2
    for name, schema in FIXED_FILES.items():
        failures += run(command, SCHEMA_DIR / f"{schema}.schema.json", [data_dir / name])
    try:
        status = json.loads((data_dir / "status.json").read_text())
    except (OSError, ValueError):
        status = {}
    for name, (status_key, schema) in DECLARED_FILES.items():
        declared = status.get(status_key) if isinstance(status, dict) else None
        if declared:
            if declared != f"ctas/data/{name}":
                print(f"FAIL  {schema + '.schema.json':36s} status.{status_key} is {declared!r}, not ctas/data/{name}")
                failures += 1
                continue
            if not (data_dir / name).exists():
                print(f"FAIL  {schema + '.schema.json':36s} status.json declares {name} but it is missing")
                failures += 1
                continue
            failures += run(command, SCHEMA_DIR / f"{schema}.schema.json", [data_dir / name])
        else:
            print(f"skip  {schema + '.schema.json':36s} {name} is not declared by this release (built before Track 1)")
    for name, schema in OPTIONAL_FILES.items():
        if (data_dir / name).exists():
            failures += run(command, SCHEMA_DIR / f"{schema}.schema.json", [data_dir / name])
        else:
            print(f"skip  {schema + '.schema.json':36s} {name} is not present yet")
    for name, schema in FIXED_INPUT_FILES.items():
        for candidate in (data_dir / name, site_root / "ctas/data" / name):
            if candidate.exists():
                failures += run(command, SCHEMA_DIR / f"{schema}.schema.json", [candidate])
                break
        else:
            print(f"skip  {schema + '.schema.json':36s} {name} is not present")
    pages = sorted((data_dir / "catalog-pages").glob("[0-9][0-9][0-9][0-9].json"))
    failures += run(command, SCHEMA_DIR / "catalog-page.schema.json", pages)
    roots, descriptors, parts, rule = chunk_sample(data_dir, args.all_chunks, args.chunk_budget_mib)
    print(f"chunk sampling rule: {rule}; {len(descriptors)} parts descriptor(s) and {len(parts)} part file(s) in full")
    failures += run(command, SCHEMA_DIR / "candidate-chunk.schema.json", roots)
    failures += run(command, SCHEMA_DIR / "candidate-chunk.schema.json", [path for path in descriptors if path not in roots])
    failures += run(command, SCHEMA_DIR / "candidate-json-part.schema.json", parts)
    delivery = site_root / "ctas/delivery.json"
    if delivery.exists():
        failures += run(command, SCHEMA_DIR / "delivery.schema.json", [delivery])
    elapsed = time.monotonic() - started
    if failures:
        print(f"{failures} schema check(s) failed after {elapsed:.1f} s", file=sys.stderr)
        return 1
    print(f"every published CTAS JSON file validates against ctas/schemas ({elapsed:.1f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

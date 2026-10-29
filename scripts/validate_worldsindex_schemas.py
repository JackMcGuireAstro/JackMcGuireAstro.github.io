#!/usr/bin/env python3
"""Validate every published WorldsIndex JSON file against worldsindex/schemas (Track 1).

The schemas (JSON Schema 2020-12, one per record type, named by worldsindex/openapi.yaml)
are the contract readers can rely on. This step runs check-jsonschema over the release:

* every top-level file fully: manifest, catalog index, sky atlas, registry, transit
  ephemerides, source coverage, source monitor, light-curve index and every light curve,
  bulk downloads, exports index, provenance, changelog, feed, and the cloud-run marker
  and the check history when present;
* the 256 detail shards and (Track 2a) the 256 conflict-set shards, each kind sampled within a time
  budget (default 30 s each, WORLDSINDEX_SCHEMA_BUDGET_SECONDS):
  shards are taken in a deterministic order that starts at an offset derived from the
  release's atlas digest, so successive releases sample different shards while one
  release always samples the same ones; the shards validated are printed.

Any file under worldsindex/data that no schema is mapped to fails the run, so a new
artifact cannot be published without a schema. Exit 1 on any mismatch; the publisher
(scripts/publish_worldsindex.sh) refuses the release then.

Usage: validate_worldsindex_schemas.py [--data worldsindex/data] [--schemas worldsindex/schemas] [--budget 30]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Published path (relative to worldsindex/data) -> schema file. Regular expressions, first match wins.
SCHEMA_FOR = [
    (r"^manifest\.json$", "manifest.schema.json"),
    (r"^catalog-index\.json\.gz$", "catalog-index.schema.json"),
    (r"^sky-detections\.json\.gz$", "sky-detections.schema.json"),
    (r"^registry\.json\.gz$", "registry.schema.json"),
    (r"^transit-ephemerides\.json\.gz$", "transit-ephemerides.schema.json"),
    (r"^source-coverage\.json$", "source-coverage.schema.json"),
    (r"^source-monitor\.json$", "source-monitor.schema.json"),
    (r"^lightcurves/index\.json$", "lightcurve-index.schema.json"),
    (r"^lightcurves/[A-Za-z0-9._-]+\.json\.gz$", "lightcurve.schema.json"),
    (r"^bulk-downloads\.json$", "bulk-downloads.schema.json"),
    (r"^exports/index\.json$", "exports-index.schema.json"),
    (r"^provenance\.json$", "provenance.schema.json"),
    (r"^changelog\.json$", "changelog.schema.json"),
    (r"^feed\.json$", "feed.schema.json"),
    (r"^cloud-run\.json$", "cloud-run.schema.json"),
    (r"^check-history\.json$", "check-history.schema.json"),
    (r"^details/[0-9a-f]{2}\.json\.gz$", "details-shard.schema.json"),
    # Framework v2 Track 2a: conflict sets (sharded like details/, sampled within the same budget),
    # their summary and index, and the controversial / retracted / excluded list.
    (r"^conflicts/[0-9a-f]{2}\.json\.gz$", "conflict-sets-shard.schema.json"),
    (r"^conflict-summary\.json$", "conflict-summary.schema.json"),
    (r"^conflict-index\.json\.gz$", "conflict-index.schema.json"),
    (r"^controversial-retracted-excluded\.json\.gz$", "status-exceptions.schema.json"),
    # Framework v2 Track 3b: calculated quantities (sharded like details/), their summary, the
    # population plot tables and the ephemeris staleness index (the plots themselves are SVG).
    (r"^derived/[0-9a-f]{2}\.json\.gz$", "derived-shard.schema.json"),
    (r"^derived-summary\.json$", "derived-summary.schema.json"),
    (r"^populations\.json$", "populations.schema.json"),
    (r"^staleness\.json$", "staleness.schema.json"),
    # Framework v2 Track 4: the precomputed 30-night transit list for WIRO and Red Buttes.
    (r"^observing-windows\.json$", "observing-windows.schema.json"),
]
# Sharded record types: validated by a deterministic sample within the time budget, not in full.
SHARDED = ("details-shard.schema.json", "conflict-sets-shard.schema.json", "derived-shard.schema.json")
NOT_JSON = re.compile(r"\.(ecsv\.gz|vot\.gz|md|xml|svg)$")


def schema_for(relative: str) -> str | None:
    for pattern, schema in SCHEMA_FOR:
        if re.fullmatch(pattern, relative):
            return schema
    return None


def materialize(data: Path, relative: str, scratch: Path) -> Path:
    """A plain .json copy of a published file (gunzipped when needed) for check-jsonschema."""

    source = data / relative
    target = scratch / relative.replace("/", "__")
    if target.suffix == ".gz":
        target = target.with_suffix("")
    if source.suffix == ".gz":
        with gzip.open(source, "rb") as stream, open(target, "wb") as out:
            shutil.copyfileobj(stream, out)
    else:
        shutil.copyfile(source, target)
    return target


def run_check(schema: Path, schemas: Path, files: list[Path]) -> tuple[bool, str]:
    command = [sys.executable, "-m", "check_jsonschema", "--base-uri", schemas.resolve().as_uri() + "/", "--schemafile", str(schema), *map(str, files)]
    completed = subprocess.run(command, capture_output=True, text=True)
    return completed.returncode == 0, (completed.stdout + completed.stderr).strip()


def openapi_consistency(openapi_path: Path, schemas: Path, mapped: set[str]) -> list[str]:
    """OpenAPI 3.1 document: its component schemas are the files under worldsindex/schemas,
    every mapped schema is one of them, and every response $ref resolves to a component."""

    problems: list[str] = []
    try:
        import yaml  # PyYAML, installed with check-jsonschema
    except ImportError:
        return ["PyYAML is not installed; cannot read openapi.yaml"]
    if not openapi_path.is_file():
        return [f"{openapi_path} is missing"]
    document = yaml.safe_load(openapi_path.read_text())
    if not str(document.get("openapi", "")).startswith("3.1."):
        problems.append(f"openapi version {document.get('openapi')!r} is not 3.1.x")
    components = (document.get("components") or {}).get("schemas") or {}
    referenced_files: set[str] = set()
    for name, body in components.items():
        ref = body.get("$ref") if isinstance(body, dict) else None
        if not ref or not ref.startswith("schemas/") or not (schemas / ref.split("/", 1)[1]).is_file():
            problems.append(f"component {name}: $ref {ref!r} is not an existing worldsindex/schemas file")
        else:
            referenced_files.add(ref.split("/", 1)[1])
    for schema in sorted(mapped - referenced_files):
        problems.append(f"schema {schema} validates published files but openapi.yaml does not name it")
    for path, item in (document.get("paths") or {}).items():
        for method, operation in item.items():
            for status, response in (operation.get("responses") or {}).items():
                for media, body in (response.get("content") or {}).items():
                    ref = (body.get("schema") or {}).get("$ref") if isinstance(body, dict) else None
                    if ref and (not ref.startswith("#/components/schemas/") or ref.rsplit("/", 1)[1] not in components):
                        problems.append(f"{method.upper()} {path} {status} {media}: unresolved {ref}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=str(ROOT / "worldsindex" / "data"))
    parser.add_argument("--schemas", default=str(ROOT / "worldsindex" / "schemas"))
    parser.add_argument("--budget", type=float, default=float(os.environ.get("WORLDSINDEX_SCHEMA_BUDGET_SECONDS", "30")), help="seconds for the detail-shard sample")
    args = parser.parse_args(argv)
    data, schemas = Path(args.data), Path(args.schemas)
    if not (data / "manifest.json").is_file():
        print(f"FAIL  no release at {data}")
        return 1
    manifest = json.loads((data / "manifest.json").read_text())
    published = sorted(p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file() and not p.name.startswith("."))
    declared = set(manifest.get("artifacts", {})) | {"manifest.json"}
    unmapped = [rel for rel in published if not NOT_JSON.search(rel) and schema_for(rel) is None]
    if unmapped:
        print("FAIL  published JSON files with no schema (add one to worldsindex/schemas and SCHEMA_FOR): " + ", ".join(unmapped))
        return 1
    # Schema files must themselves be valid 2020-12 documents with a $id under the published base.
    for schema_file in sorted(schemas.glob("*.schema.json")):
        document = json.loads(schema_file.read_text())
        if document.get("$schema") != "https://json-schema.org/draft/2020-12/schema" or not str(document.get("$id", "")).endswith(schema_file.name):
            print(f"FAIL  {schema_file.name} is not a draft 2020-12 schema with a matching $id")
            return 1
    # Every schema the mapping names must exist.
    for _, schema_name in SCHEMA_FOR:
        if not (schemas / schema_name).is_file():
            print(f"FAIL  schema {schema_name} is missing")
            return 1
    # The schemas are valid against the 2020-12 metaschema (bundled with check-jsonschema; no network).
    completed = subprocess.run([sys.executable, "-m", "check_jsonschema", "--check-metaschema", *map(str, sorted(schemas.glob("*.schema.json")))], capture_output=True, text=True)
    if completed.returncode != 0:
        print("FAIL  a schema file is not a valid JSON Schema 2020-12 document:\n" + (completed.stdout + completed.stderr).strip())
        return 1
    # worldsindex/openapi.yaml names every schema the mapping uses, and every schema it names exists.
    openapi_problems = openapi_consistency(schemas.parent / "openapi.yaml", schemas, {schema for _, schema in SCHEMA_FOR})
    if openapi_problems:
        print("FAIL  worldsindex/openapi.yaml disagrees with worldsindex/schemas:\n  " + "\n  ".join(openapi_problems))
        return 1

    failures: list[str] = []
    validated = 0
    with tempfile.TemporaryDirectory(prefix="worldsindex-schemas-") as scratch_name:
        scratch = Path(scratch_name)
        # Top-level files, grouped by schema so check-jsonschema starts once per schema.
        groups: dict[str, list[str]] = {}
        shards: list[str] = []
        for rel in published:
            if NOT_JSON.search(rel):
                continue
            schema = schema_for(rel)
            assert schema
            if schema in SHARDED:
                if rel not in declared:
                    failures.append(f"{rel}: published but not declared by the manifest")
                shards.append(rel)
                continue
            # The publisher's own files beside the release (cloud-run marker, check history)
            # are written after the build, so the manifest cannot declare them.
            if rel not in ("manifest.json", "cloud-run.json", "check-history.json") and rel not in declared:
                failures.append(f"{rel}: published but not declared by the manifest")
            groups.setdefault(schema, []).append(rel)
        started = time.monotonic()
        for schema, rels in sorted(groups.items()):
            files = [materialize(data, rel, scratch) for rel in rels]
            ok, output = run_check(schemas / schema, schemas, files)
            validated += len(files)
            if not ok:
                failures.append(f"{schema}: {output.splitlines()[-1] if output else 'validation failed'}")
                for line in output.splitlines():
                    if "::" in line:
                        failures.append("  " + line.strip())
            for file in files:
                file.unlink(missing_ok=True)
        top_seconds = time.monotonic() - started
        # Shards (detail, conflict-set): per kind, a deterministic sample within the budget, starting at
        # an offset derived from the atlas digest.
        shards.sort()
        offset_seed = int(hashlib.sha256(str(manifest.get("atlasSha256", "")).encode()).hexdigest()[:2], 16)
        reports: list[str] = []
        sampled: list[str] = []
        ordered: list[str] = []
        shard_seconds = 0.0
        for kind in SHARDED:
            kind_shards = [rel for rel in shards if schema_for(rel) == kind]
            if not kind_shards:
                continue
            offset = offset_seed % len(kind_shards)
            kind_ordered = kind_shards[offset:] + kind_shards[:offset]
            ordered.extend(kind_ordered)
            kind_sampled: list[str] = []
            shard_started = time.monotonic()
            for rel in kind_ordered:
                if time.monotonic() - shard_started > args.budget:
                    break
                file = materialize(data, rel, scratch)
                ok, output = run_check(schemas / kind, schemas, [file])
                file.unlink(missing_ok=True)
                kind_sampled.append(rel)
                if not ok:
                    failures.append(f"{rel}: {output.splitlines()[-1] if output else 'validation failed'}")
                    for line in output.splitlines():
                        if "::" in line:
                            failures.append("  " + line.strip())
            elapsed = time.monotonic() - shard_started
            shard_seconds += elapsed
            sampled.extend(kind_sampled)
            label = "detail" if kind == "details-shard.schema.json" else "conflict-set"
            reports.append(f"{len(kind_sampled)} of {len(kind_shards)} {label} shards sampled in {elapsed:.1f}s (budget {args.budget:g}s, starting at {kind_ordered[0]}): "
                           + ", ".join(Path(rel).stem.split('.')[0] for rel in kind_sampled))
    print(f"schemas: {validated} top-level JSON files validated fully in {top_seconds:.1f}s; " + ("; ".join(reports) if reports else "no shards published"))
    if failures:
        print("FAIL  published JSON does not match worldsindex/schemas:")
        for failure in failures:
            print("  " + failure)
        return 1
    print("ok -- every validated file matches its schema")
    return 0


if __name__ == "__main__":
    sys.exit(main())

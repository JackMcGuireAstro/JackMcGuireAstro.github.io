#!/usr/bin/env python3
"""Regenerate the release benchmarks from a site tree that holds both published releases
(Track 7; run `bash scripts/overlay_published_data.sh` first in a checkout of main).

Writes, under benchmarks/:
  sources/ctas-sources.json, sources/worldsindex-sources.json, sources/SUMMARY.md
      the four denominators (declared, applicable, executed, data-bearing) and the receipt
      outcome counts per source, copied from the releases' own accounting files, with
      receipt rates computed from them;
  science/fixtures.json
      the four scientific fixture dossiers named by the professional framework, each taken
      from a real published record, with the facts a reviewer should see and the checksum
      of the record as published;
  identity/ is written by the WorldsIndex builder (npm run identity:gold) and copied here by
  hand; performance/ by scripts/test_release_pages_browser.py; recovery/ by
  scripts/state_restore_drill.sh.

Every number is read from a release file named in the output; nothing is estimated.
    python3 scripts/build_benchmarks.py [--tree .] [--out benchmarks]
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
from pathlib import Path

CTAS_FIXTURES = {
    # (role, event name): chosen 2026-10-29 by scanning every published CTAS dossier for the
    # framework's v0.1 cases (claude/track7-batch-2026-10-29.md says how).
    "optical-transient-disagreeing-classifications-failed-provider": "SN2026aabx",
    "multimessenger-event-with-retraction": "GRB 260822A",
}
WI_FIXTURES = {
    "hot-jupiter-reference-system": "object-hd-209458-b",
    "disputed-planet-40-eridani": "object-oec-7db63939d648877516fc",
}


def canonical_sha(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ctas_sources(tree: Path) -> dict:
    path = tree / "ctas/data/source-accounting.json"
    doc = json.loads(path.read_text())
    rows = []
    for row in doc["sources"]:
        applicable = int(row.get("applicable_events") or 0)
        executed = int(row.get("events_with_executed_receipts") or 0)
        data = int(row.get("data_returned_events") or 0)
        rows.append({
            "source": row["source_key"], "name": row.get("name"),
            "declared": bool(row.get("declared")), "applicable_events": applicable,
            "events_with_executed_receipts": executed, "data_returned_events": data,
            "events_with_retained_records": int(row.get("events_with_retained_records") or 0),
            "executed_receipt_rate": round(executed / applicable, 4) if applicable else None,
            "data_bearing_rate": round(data / applicable, 4) if applicable else None,
            "implementation_state": row.get("implementation_state"), "representation_state": row.get("representation_state"),
            "outcome_counts": row.get("outcome_counts") or {},
        })
    return {"schema": "benchmark.sources@1", "product": "ctas", "input": {"file": "ctas/data/source-accounting.json", "sha256": file_sha(path),
            "generated_at": doc.get("generated_at"), "candidate_count": doc.get("candidate_count")},
            "definitions": doc.get("definitions"), "totals": doc.get("totals"), "sources": rows}


def wi_sources(tree: Path) -> dict:
    path = tree / "worldsindex/data/source-coverage.json"
    doc = json.loads(path.read_text())
    rows = []
    for key, row in sorted(doc["sources"].items()):
        den = row.get("denominators") or {}
        objects = den.get("objects") or {}
        rows.append({"source": key, "class": row.get("class"), "state": row.get("state"),
                     "declared": den.get("declared"), "applicable": den.get("applicable"), "executed": den.get("executed"),
                     "data_bearing": den.get("dataBearing"), "objects": objects,
                     "eligible": row.get("eligible"), "attached": row.get("attached"), "blocked": row.get("blocked"),
                     "blocked_reason": row.get("blockedReason"), "rows": row.get("rows")})
    return {"schema": "benchmark.sources@1", "product": "worldsindex", "input": {"file": "worldsindex/data/source-coverage.json",
            "sha256": file_sha(path), "generated_at": doc.get("generatedAt")}, "denominators": doc.get("denominators"), "sources": rows}


def summary_md(ctas: dict, wi: dict) -> str:
    lines = ["# Source benchmark (generated)", "",
             f"CTAS: {ctas['input']['file']} generated {ctas['input']['generated_at']}, {ctas['input']['candidate_count']} events; "
             f"WorldsIndex: {wi['input']['file']} generated {wi['input']['generated_at']}.", "",
             "## CTAS — events per source", "",
             "| Source | Applicable | Executed receipts | Data returned | Receipt rate | Representation |",
             "|---|---:|---:|---:|---:|---|"]
    for row in sorted(ctas["sources"], key=lambda r: -r["applicable_events"]):
        rate = "—" if row["executed_receipt_rate"] is None else f"{row['executed_receipt_rate']:.1%}"
        lines.append(f"| {row['source']} | {row['applicable_events']:,} | {row['events_with_executed_receipts']:,} | "
                     f"{row['data_returned_events']:,} | {rate} | {row['representation_state']} |")
    lines += ["", "## WorldsIndex — denominators per source", "", "| Source | Declared | Applicable | Executed | Data-bearing | Eligible | Attached |",
              "|---|---|---|---|---|---:|---:|"]
    for row in wi["sources"]:
        lines.append(f"| {row['source']} | {row['declared']} | {row['applicable']} | {row['executed']} | {row['data_bearing']} | "
                     f"{row['eligible'] if row['eligible'] is not None else '—'} | {row['attached'] if row['attached'] is not None else '—'} |")
    return "\n".join(lines) + "\n"


def find_ctas(tree: Path, name: str):
    for fn in sorted(glob.glob(str(tree / "ctas/data/candidate-chunks/*.json"))):
        if fn.endswith("manifest.json") or ".part-" in fn:
            continue
        for candidate in json.loads(Path(fn).read_text()).get("candidates", []):
            if candidate.get("name") == name:
                return Path(fn).name, candidate
    return None, None


def ctas_fixture(tree: Path, role: str, name: str) -> dict:
    chunk, record = find_ctas(tree, name)
    if record is None:
        return {"role": role, "name": name, "state": "not-in-this-release"}
    comparison = record.get("comparison") or {}
    classification = next((p for p in comparison.get("properties", []) if p.get("code") == "classification"), {"rows": []})
    labels = sorted({str(r.get("value")) for r in classification.get("rows", []) if r.get("value") is not None})
    verdicts = {}
    for row in classification.get("rows", []):
        verdict = (row.get("compat") or {}).get("verdict")
        verdicts[verdict] = verdicts.get(verdict, 0) + 1
    coverage = record.get("source_coverage") or []
    dispositions = {}
    for row in coverage:
        dispositions[row.get("disposition")] = dispositions.get(row.get("disposition"), 0) + 1
    timeline = json.dumps(record.get("evidence_timeline") or [])
    return {"role": role, "name": name, "event_id": record.get("event_id"), "event_type": record.get("event_type"),
            "chunk": chunk, "record_sha256": canonical_sha(record),
            "facts": {"classification_labels": labels, "classification_verdicts": verdicts,
                      "comparison_summary": comparison.get("summary"),
                      "source_dispositions": dispositions,
                      "provider_failures": sorted(r.get("source_id") for r in coverage if r.get("disposition") == "provider-failure"),
                      "timeline_mentions_retraction": "retract" in timeline.lower(),
                      "astro_evidence_conflict_sets": len((record.get("astro_evidence") or {}).get("conflictSets", []))},
            "a_reviewer_should_see": {
                "optical-transient-disagreeing-classifications-failed-provider": [
                    "both classifications side by side in Compare sources, marked inconsistent, neither averaged nor hidden",
                    "the headline classification labelled a selection with its rule",
                    "the failed provider listed with state provider-failure and its time, not as no-match"],
                "multimessenger-event-with-retraction": [
                    "the retraction kept in the evidence timeline with its time and source",
                    "the retracted notice not counted as current evidence"],
            }[role]}


def wi_fixture(tree: Path, role: str, object_id: str) -> dict:
    record = None
    for fn in sorted(glob.glob(str(tree / "worldsindex/data/details/*.json.gz"))):
        shard = json.loads(gzip.decompress(Path(fn).read_bytes()))
        if object_id in shard:
            record, shard_name = shard[object_id], Path(fn).name
            break
    if record is None:
        return {"role": role, "object_id": object_id, "state": "not-in-this-release"}
    atlas = record["atlasRecord"]
    conflicts = json.loads(gzip.decompress((tree / "worldsindex/data/conflict-index.json.gz").read_bytes()))
    columns = conflicts.get("columns") or []
    sets = [s for s in conflicts.get("sets", []) if isinstance(s, list) and object_id in s] if columns else []
    controversial = json.loads(gzip.decompress((tree / "worldsindex/data/controversial-retracted-excluded.json.gz").read_bytes()))
    listed = atlas["name"] in json.dumps(controversial)
    return {"role": role, "object_id": object_id, "name": atlas["name"], "shard": shard_name, "record_sha256": canonical_sha(record),
            "facts": {"normalized_status": atlas.get("normalizedStatus"), "identity_state": atlas.get("identityState"),
                      "source_ids": atlas.get("sourceIds"), "source_records": len(record.get("records", [])),
                      "conflict_sets_listed": len(sets), "in_controversial_retracted_excluded_view": listed},
            "a_reviewer_should_see": {
                "hot-jupiter-reference-system": [
                    "every published solution as its own row with its reference, none averaged",
                    "the selection rule named beside any selected value",
                    "transit windows padded by the ephemeris uncertainty"],
                "disputed-planet-40-eridani": [
                    "the planet listed in the controversial / retracted / excluded view with the reason and source",
                    "identity shown as unreviewed until a person reviews it",
                    "no source's status silently overriding another's"],
            }[role]}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tree", default=".")
    parser.add_argument("--out", default="benchmarks")
    args = parser.parse_args()
    tree, out = Path(args.tree), Path(args.out)
    (out / "sources").mkdir(parents=True, exist_ok=True)
    (out / "science").mkdir(parents=True, exist_ok=True)
    ctas, wi = ctas_sources(tree), wi_sources(tree)
    (out / "sources/ctas-sources.json").write_text(json.dumps(ctas, indent=1, sort_keys=True) + "\n")
    (out / "sources/worldsindex-sources.json").write_text(json.dumps(wi, indent=1, sort_keys=True) + "\n")
    (out / "sources/SUMMARY.md").write_text(summary_md(ctas, wi))
    fixtures = [ctas_fixture(tree, role, name) for role, name in CTAS_FIXTURES.items()]
    fixtures += [wi_fixture(tree, role, oid) for role, oid in WI_FIXTURES.items()]
    (out / "science/fixtures.json").write_text(json.dumps({"schema": "benchmark.science-fixtures@1", "fixtures": fixtures}, indent=1, sort_keys=True) + "\n")
    print(f"benchmarks written: {len(ctas['sources'])} CTAS sources, {len(wi['sources'])} WorldsIndex sources, {len(fixtures)} fixtures")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

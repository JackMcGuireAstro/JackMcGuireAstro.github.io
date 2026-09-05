#!/usr/bin/env python3
"""Check exported ingest-score provenance against the exact read-only export DB.

Run after export, before publication. This does not read credentials, migrate
the database, or recalculate the stored score. It verifies every released row.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path

from export_ctas_snapshot import PUBLIC_SCORE_FACTORS, SCORE_METHOD_VERSION, clean


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", required=True)
    parser.add_argument("--data-dir", default="ctas/data")
    args = parser.parse_args()
    database = Path(args.database).resolve()
    data = Path(args.data_dir).resolve()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as connection:
        stored = {row[0]: (row[1], row[2]) for row in connection.execute(
            "SELECT id, priority_score, priority_factors FROM events")}
    manifest = json.loads((data / "candidate-chunks/manifest.json").read_text())
    summary = json.loads((data / "live-summary.json").read_text())
    failures, seen, changed = [], set(), 0
    if summary["release"]["score_method_version"] != SCORE_METHOD_VERSION:
        failures.append("Summary score method does not match exporter")
    for chunk in manifest["chunks"]:
        relative = str(chunk["path"]).removeprefix("ctas/data/")
        target = (data / relative).resolve()
        if not target.is_relative_to(data):
            raise ValueError("Manifest path escapes data directory")
        raw = target.read_bytes()
        if len(raw) != chunk["bytes"] or hashlib.sha256(raw).hexdigest() != chunk["sha256"]:
            failures.append("Detail shard integrity failed: " + relative)
            continue
        for candidate in json.loads(raw)["candidates"]:
            event_id = candidate["event_id"]
            if event_id in seen or event_id not in stored:
                failures.append("Missing or repeated input event: " + event_id)
                continue
            seen.add(event_id)
            score, raw_factors = stored[event_id]
            expected_score = round(float(score or 0), 2)
            source_factors = json.loads(raw_factors) if isinstance(raw_factors, str) else (raw_factors or {})
            expected_factors = {key: value for key, value in source_factors.items()
                                if key in PUBLIC_SCORE_FACTORS and isinstance(value, (int, float, str, bool))
                                and clean(value) is not None}
            model = candidate["score_model"]
            if model["recorded_score_at_ingest"] != expected_score or model["recorded_factors_at_ingest"] != expected_factors:
                failures.append("Ingest score/factors changed: " + event_id)
            if model["method_version"] != SCORE_METHOD_VERSION:
                failures.append("Dossier score method differs: " + event_id)
            changed += model["final_score"] != expected_score
    if len(seen) != manifest["candidate_count"]:
        failures.append("Released candidate count differs from verified records")
    print(json.dumps({"status": "failed" if failures else "passed", "checked_candidates": len(seen),
                      "changed_release_scores": changed, "score_method": SCORE_METHOD_VERSION,
                      "catalog_checksum": manifest["catalog_content_checksum_sha256"], "failures": failures}, indent=2))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

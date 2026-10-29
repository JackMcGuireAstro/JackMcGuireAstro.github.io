#!/usr/bin/env python3
"""Backup-and-restore drill for the two saved state archives (Track 7, gate 9).

Both systems keep their working state as one GitHub Release asset that every cloud run
restores and saves: CTAS `soc-<time>.db.gz` (the collector's SQLite database) in
JackMcGuireAstro/ctas-state, WorldsIndex `wi-state-<time>.tar.gz` (the builder's snapshots,
outputs and database) in JackMcGuireAstro/worldsindex-state. The drill restores an archive
into a fresh, empty directory the way the cloud runner does and checks that what comes back
is usable, without touching anything else:

  CTAS        decompress (or, for an already decompressed copy, open it read-only with
              ?mode=ro&immutable=1, never writable), SQLite quick_check, every table the
              exporter reads present, the six append-only/immutable triggers present, row
              counts of the evidence tables, latest record times, and, when a published
              status.json is given, the database's public events against the release count.
  WorldsIndex extract with tar into the empty directory, refuse absolute or ".." members,
              every ACTIVE snapshot pointer resolving to a snapshot directory with its
              manifest, the size against the 1 GiB target and 2 GiB hard limit.

  python3 scripts/state_restore_drill.py --ctas soc-....db.gz [--ctas-status ctas/data/status.json] \\
      [--worldsindex wi-state-....tar.gz] --report benchmarks/recovery/drill-<date>.json

Exit status 0 when every check that ran passed, 1 otherwise. Nothing is uploaded or changed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

CTAS_REQUIRED_TABLES = (
    "events", "aliases", "alert_envelopes", "classification_assertions", "host_context_assertions", "observations",
    "spectra", "publications", "source_query_attempts", "source_query_response_evidence", "analysis_runs",
    "skymap_revisions", "skymap_target_ranks", "certification_runs", "publication_bundles", "security_audit_events",
)
CTAS_TRIGGERS = tuple(f"{table}_no_{op}" for table in ("certification_runs", "publication_bundles", "security_audit_events") for op in ("update", "delete"))
CTAS_COUNTED = ("events", "alert_envelopes", "classification_assertions", "observations", "spectra", "source_query_attempts", "analysis_runs")
WI_TARGET, WI_LIMIT = 1 << 30, 2 << 30


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def check(results: list, name: str, ok: bool, detail) -> None:
    results.append({"check": name, "ok": bool(ok), "detail": detail})


def drill_ctas(archive: Path, status_path: Path | None, work: Path) -> dict:
    results: list = []
    started = time.monotonic()
    if archive.suffix == ".gz":
        target = work / "soc.db"
        with gzip.open(archive, "rb") as source, target.open("wb") as sink:
            shutil.copyfileobj(source, sink, 1 << 20)
        uri, mode = f"file:{target}?mode=ro", "restored into an empty directory"
    else:
        target = archive
        uri, mode = f"file:{target}?mode=ro&immutable=1", "already decompressed copy, opened read-only and immutable"
    restore_seconds = round(time.monotonic() - started, 1)
    connection = sqlite3.connect(uri, uri=True)
    quick = connection.execute("PRAGMA quick_check").fetchone()[0]
    check(results, "sqlite quick_check", quick == "ok", quick)
    tables = {row[0] for row in connection.execute("select name from sqlite_master where type='table'")}
    missing = [t for t in CTAS_REQUIRED_TABLES if t not in tables]
    check(results, "tables the exporter reads", not missing, {"present": len(tables), "missing": missing})
    triggers = {row[0] for row in connection.execute("select name from sqlite_master where type='trigger'")}
    check(results, "append-only and immutable triggers", all(t in triggers for t in CTAS_TRIGGERS),
          {"expected": list(CTAS_TRIGGERS), "missing": [t for t in CTAS_TRIGGERS if t not in triggers]})
    counts = {t: connection.execute(f'select count(*) from "{t}"').fetchone()[0] for t in CTAS_COUNTED if t in tables}
    check(results, "evidence tables hold rows", all(counts.get(t, 0) > 0 for t in ("events", "alert_envelopes", "source_query_attempts")), counts)
    latest = {}
    for table, column in (("alert_envelopes", "received_at"), ("source_query_attempts", "checked_at"), ("certification_runs", "created_at")):
        columns = {row[1] for row in connection.execute(f'pragma table_info("{table}")')}
        latest[table] = connection.execute(f'select max("{column}") from "{table}"').fetchone()[0] if column in columns else None
    check(results, "latest record times readable", all(latest.values()), latest)
    if status_path and status_path.exists():
        # A published release counts the public events of the database it was exported from.
        # The archive saved with that release must hold at least as many; an older archive
        # (as in a drill on a copy fetched earlier) may hold fewer, and is reported as such.
        status = json.loads(status_path.read_text())
        published = int(status.get("candidate_count") or 0)
        events = counts.get("events", 0)
        released = str(status.get("last_successful_update") or "")
        archive_latest = str(latest.get("alert_envelopes") or "").replace(" ", "T")
        older = bool(archive_latest) and archive_latest[:19] < released[:19]
        check(results, "event count against the published release", events >= published or older,
              {"database_events": events, "published_candidate_count": published, "release": released,
               "archive_latest_alert": archive_latest, "archive_older_than_release": older})
    connection.close()
    return {"archive": archive.name, "bytes": archive.stat().st_size, "sha256": sha256(archive) if archive.suffix == ".gz" else None,
            "mode": mode, "restore_seconds": restore_seconds, "checks": results, "ok": all(r["ok"] for r in results)}


def drill_worldsindex(archive: Path, work: Path) -> dict:
    results: list = []
    started = time.monotonic()
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        unsafe = [m.name for m in members if m.name.startswith("/") or ".." in Path(m.name).parts]
        check(results, "no absolute or parent-directory members", not unsafe, unsafe[:10])
        if not unsafe:
            tar.extractall(work, filter="data")
    restore_seconds = round(time.monotonic() - started, 1)
    size = sum(f.stat().st_size for f in work.rglob("*") if f.is_file())
    check(results, "restored size within the 2 GiB hard limit", size < WI_LIMIT,
          {"bytes": size, "target_1_GiB_met": size < WI_TARGET})
    pointers = list(work.rglob("ACTIVE.json"))
    broken = []
    for pointer in pointers:
        try:
            active = json.loads(pointer.read_text())
        except ValueError:
            broken.append(str(pointer.relative_to(work)))
            continue
        snapshot = active.get("snapshotId") or active.get("active") or active.get("snapshot")
        if snapshot and not (pointer.parent / snapshot).exists():
            broken.append(f"{pointer.relative_to(work)} -> {snapshot}")
    check(results, "every ACTIVE snapshot pointer resolves", pointers and not broken, {"pointers": len(pointers), "broken": broken[:10]})
    manifests = list(work.rglob("manifest.json"))
    check(results, "snapshot manifests present", bool(manifests), {"manifests": len(manifests)})
    return {"archive": archive.name, "bytes": archive.stat().st_size, "sha256": sha256(archive), "restore_seconds": restore_seconds,
            "members": len(members), "checks": results, "ok": all(r["ok"] for r in results)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ctas", type=Path)
    parser.add_argument("--ctas-status", type=Path)
    parser.add_argument("--worldsindex", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = {"schema": "benchmark.restore-drill@1", "run_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "host": os.uname().sysname, "products": {}}
    for key, archive, runner in (("ctas", args.ctas, lambda a, w: drill_ctas(a, args.ctas_status, w)),
                                 ("worldsindex", args.worldsindex, drill_worldsindex)):
        if not archive:
            report["products"][key] = {"state": "not-run", "reason": "no archive given"}
            continue
        with tempfile.TemporaryDirectory(prefix=f"restore-{key}-") as work:
            report["products"][key] = runner(archive, Path(work))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    ran = [p for p in report["products"].values() if "ok" in p]
    print(json.dumps({k: v.get("ok", v.get("state")) for k, v in report["products"].items()}))
    return 0 if ran and all(p["ok"] for p in ran) else 1


if __name__ == "__main__":
    raise SystemExit(main())

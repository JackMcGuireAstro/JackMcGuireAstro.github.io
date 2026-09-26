#!/usr/bin/env python3
"""Keep the local CTAS database to what the catalog actually needs.

Measured 2026-09-25: soc.db was 7.0 GB and growing ~165 MB a day, but only about
0.5 GB of it was data received from sources (alert envelopes, observations). The
rest was derived bookkeeping kept forever:

* ``analysis_runs`` (3.9 GB, 626k rows): every re-computation is stored as a new
  immutable row. ``science-interest`` and ``population-anomaly`` are recomputed
  whenever the candidate population shifts (~485k rows between them); every other
  analysis type has about one run per candidate. Every reader in the backend and
  the site exporter uses the newest run per (event, analysis type).
* ``certification_runs`` (1.8 GB, 113 rows of ~16 MB evidence each), no longer
  written since 2026-08-23.

Policy (all thresholds are arguments):

* ``analysis_runs`` of the churning types: keep the newest run per (event, type),
  every run completed in the last ``--keep-days`` days, and every run whose id is
  cited by a file under the reference folders (benchmark packets, publication
  bundles, certification reports, reference sets). Prune the rest. Other analysis
  types are never touched.
* ``certification_runs``: keep the newest ``--cert-keep`` per scope and every run
  whose id is cited by a reference file.

A table the backend itself protects from deletion (a ``BEFORE DELETE`` trigger that
raises, as ``certification_runs``, ``publication_bundles`` and
``security_audit_events`` have had since the backend declared them immutable) is
never pruned: its rows are reported and left alone. Found on the live database on
2026-09-26, when the first prune stopped at the certification step.

Modes:

* ``preview``  read-only: print what would be pruned and roughly how much.
* ``prune``    archive the pruned rows to a gzip NDJSON file, verify the archive,
               delete, and with ``--vacuum`` compact the file and switch it to
               incremental auto-vacuum. Run with the backend stopped (see
               ``Shrink CTAS database.command``).
* ``daily``    delete in small batches while the backend runs (SQLite WAL makes
               that safe), then reclaim free pages with ``PRAGMA
               incremental_vacuum``. Installed as a daily LaunchAgent.

No row that any code path reads as "current" is ever removed, and no source data
table is touched.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import time

UUID = re.compile(rb"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DEFAULT_CHURN_TYPES = ("science-interest", "population-anomaly")
DEFAULT_REFERENCE_DIRS = ("benchmarks", "publication-bundles", "certification", "reference")
BATCH = 2000


def log(message: str) -> None:
    print(f"{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}  {message}", flush=True)


def referenced_ids(folders: list[Path]) -> set[str]:
    """Every UUID that appears anywhere in the reference files (streamed, bounded memory)."""
    found: set[str] = set()
    for folder in folders:
        if not folder.is_dir():
            continue
        for path in folder.rglob("*"):
            if not path.is_file():
                continue
            try:
                with open(path, "rb") as handle:
                    tail = b""
                    while True:
                        block = handle.read(1 << 20)
                        if not block:
                            break
                        chunk = tail + block
                        found.update(match.decode() for match in UUID.findall(chunk))
                        tail = chunk[-40:]
            except OSError:
                continue
    return found


def cutoff_text(keep_days: float, now: dt.datetime | None = None) -> str:
    now = now or dt.datetime.now(dt.timezone.utc)
    return (now - dt.timedelta(days=keep_days)).strftime("%Y-%m-%d %H:%M:%S")


def analysis_prune_ids(con: sqlite3.Connection, churn_types: list[str], cutoff: str, keep: set[str]) -> list[str]:
    marks = ",".join("?" * len(churn_types))
    rows = con.execute(
        f"""
        WITH ranked AS (
          SELECT id,
                 replace(substr(COALESCE(completed_at, created_at), 1, 19), 'T', ' ') AS at,
                 ROW_NUMBER() OVER (
                   PARTITION BY event_id, analysis_type
                   ORDER BY COALESCE(completed_at, created_at) DESC, id DESC
                 ) AS rank
          FROM analysis_runs
          WHERE analysis_type IN ({marks})
        )
        SELECT id FROM ranked WHERE rank > 1 AND at < ?
        """,
        (*churn_types, cutoff),
    ).fetchall()
    return [row[0] for row in rows if row[0] not in keep]


def certification_prune_ids(con: sqlite3.Connection, cert_keep: int, keep: set[str]) -> list[str]:
    rows = con.execute(
        """
        WITH ranked AS (
          SELECT id, ROW_NUMBER() OVER (PARTITION BY scope ORDER BY created_at DESC, id DESC) AS rank
          FROM certification_runs
        )
        SELECT id FROM ranked WHERE rank > ?
        """,
        (cert_keep,),
    ).fetchall()
    return [row[0] for row in rows if row[0] not in keep]


def protected_tables(con: sqlite3.Connection) -> set[str]:
    """Tables whose rows the database refuses to delete (a BEFORE DELETE trigger that raises)."""
    protected = set()
    for table, sql in con.execute("SELECT tbl_name, sql FROM sqlite_master WHERE type = 'trigger'"):
        text = " ".join((sql or "").upper().split())
        if " BEFORE DELETE ON " in text and "RAISE(" in text.replace("RAISE (", "RAISE("):
            protected.add(table)
    return protected


def estimated_bytes(con: sqlite3.Connection, table: str, ids: list[str], columns: str) -> int:
    total = 0
    for start in range(0, len(ids), BATCH):
        batch = ids[start:start + BATCH]
        marks = ",".join("?" * len(batch))
        total += con.execute(f"SELECT COALESCE(SUM({columns}), 0) FROM {table} WHERE id IN ({marks})", batch).fetchone()[0]
    return int(total)


ANALYSIS_SIZE = ("LENGTH(COALESCE(result,'')) + LENGTH(COALESCE(input_manifest,'')) + LENGTH(COALESCE(parameters,'')) + "
                 "LENGTH(COALESCE(quality,'')) + LENGTH(COALESCE(reproducibility,'')) + LENGTH(COALESCE(software_versions,'')) + 300")
CERT_SIZE = "LENGTH(COALESCE(evidence,'')) + LENGTH(COALESCE(gates,'')) + 300"


def archive_rows(con: sqlite3.Connection, table: str, ids: list[str], handle) -> int:
    written = 0
    con.row_factory = sqlite3.Row
    try:
        for start in range(0, len(ids), BATCH):
            batch = ids[start:start + BATCH]
            marks = ",".join("?" * len(batch))
            for row in con.execute(f"SELECT * FROM {table} WHERE id IN ({marks})", batch):
                handle.write((json.dumps({"table": table, "row": dict(row)}, default=str, separators=(",", ":")) + "\n").encode())
                written += 1
    finally:
        con.row_factory = None
    return written


def delete_rows(con: sqlite3.Connection, table: str, ids: list[str], pause: float = 0.0) -> int:
    deleted = 0
    for start in range(0, len(ids), BATCH):
        batch = ids[start:start + BATCH]
        marks = ",".join("?" * len(batch))
        with con:
            deleted += con.execute(f"DELETE FROM {table} WHERE id IN ({marks})", batch).rowcount
        if pause:
            time.sleep(pause)
    return deleted


def quick_check(con: sqlite3.Connection) -> str:
    return str(con.execute("PRAGMA quick_check").fetchone()[0])


def file_bytes(db: Path) -> int:
    return sum(os.path.getsize(str(db) + suffix) for suffix in ("", "-wal") if os.path.exists(str(db) + suffix))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("mode", choices=("preview", "prune", "daily"))
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--keep-days", type=float, default=3.0)
    parser.add_argument("--cert-keep", type=int, default=10)
    parser.add_argument("--churn-types", default=",".join(DEFAULT_CHURN_TYPES))
    parser.add_argument("--reference-dir", action="append", type=Path,
                        help="folder whose files may cite run ids (default: benchmarks, publication-bundles, "
                             "certification and reference beside the database)")
    parser.add_argument("--archive", type=Path, help="prune mode: gzip NDJSON archive of every pruned row")
    parser.add_argument("--vacuum", action="store_true", help="prune mode: VACUUM and switch to incremental auto-vacuum")
    parser.add_argument("--now", help="ISO timestamp override (tests)")
    args = parser.parse_args(argv)

    db = args.db.resolve()
    if not db.is_file():
        log(f"database not found: {db}")
        return 2
    churn = [name.strip() for name in args.churn_types.split(",") if name.strip()]
    folders = args.reference_dir or [db.parent / name for name in DEFAULT_REFERENCE_DIRS]
    now = dt.datetime.fromisoformat(args.now.replace("Z", "+00:00")) if args.now else None
    cutoff = cutoff_text(args.keep_days, now)

    started = time.monotonic()
    keep = referenced_ids(folders)
    log(f"{len(keep):,} run ids cited by reference files ({time.monotonic() - started:.0f}s)")

    if args.mode == "preview":
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=60)
    else:
        con = sqlite3.connect(str(db), timeout=60)
        con.execute("PRAGMA busy_timeout = 60000")
    before = file_bytes(db)

    protected = protected_tables(con)
    analysis_ids = analysis_prune_ids(con, churn, cutoff, keep)
    cert_ids = certification_prune_ids(con, args.cert_keep, keep)
    analysis_total = con.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0]
    cert_total = con.execute("SELECT COUNT(*) FROM certification_runs").fetchone()[0]

    def report(table: str, ids: list[str], total: int, size: str) -> list[str]:
        estimate = estimated_bytes(con, table, ids, size) / 1e9
        if table in protected:
            log(f"{table}: {len(ids):,} of {total:,} are past the retention policy (~{estimate:.2f} GB), "
                "but the database protects this table from deletion; left as they are")
            return []
        log(f"{table}: prune {len(ids):,} of {total:,} (~{estimate:.2f} GB of row data)")
        return ids

    analysis_ids = report("analysis_runs", analysis_ids, analysis_total, ANALYSIS_SIZE)
    cert_ids = report("certification_runs", cert_ids, cert_total, CERT_SIZE)

    if args.mode == "preview":
        log(f"preview only; database is {before / 1e9:.2f} GB and was not modified")
        return 0

    if args.mode == "prune":
        check = quick_check(con)
        if check != "ok":
            log(f"quick_check before pruning reported {check!r}; nothing was changed")
            return 3
        if analysis_ids or cert_ids:
            if not args.archive:
                log("prune mode needs --archive so every removed row stays recoverable")
                return 2
            args.archive.parent.mkdir(parents=True, exist_ok=True)
            partial = args.archive.with_name(args.archive.name + ".partial")
            with gzip.open(partial, "wb", compresslevel=6) as handle:
                written = archive_rows(con, "analysis_runs", analysis_ids, handle)
                written += archive_rows(con, "certification_runs", cert_ids, handle)
            with gzip.open(partial, "rb") as handle:
                verified = sum(1 for _ in handle)
            if written != len(analysis_ids) + len(cert_ids) or verified != written:
                log(f"archive verification failed ({written} written, {verified} read back); nothing was deleted")
                return 3
            partial.replace(args.archive)
            log(f"archived {written:,} rows to {args.archive} ({os.path.getsize(args.archive) / 1e6:.0f} MB)")

    deleted = delete_rows(con, "analysis_runs", analysis_ids, pause=0.05 if args.mode == "daily" else 0.0)
    deleted += delete_rows(con, "certification_runs", cert_ids, pause=0.05 if args.mode == "daily" else 0.0)
    log(f"deleted {deleted:,} rows")

    if args.mode == "prune" and args.vacuum:
        con.execute("PRAGMA auto_vacuum = INCREMENTAL")
        con.execute("VACUUM")
        check = quick_check(con)
        log(f"compacted; auto_vacuum={con.execute('PRAGMA auto_vacuum').fetchone()[0]}; quick_check={check}")
        if check != "ok":
            return 3
    elif args.mode == "daily":
        if con.execute("PRAGMA auto_vacuum").fetchone()[0] == 2:
            con.execute("PRAGMA incremental_vacuum")
    con.close()
    log(f"database {before / 1e9:.2f} GB -> {file_bytes(db) / 1e9:.2f} GB ({time.monotonic() - started:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

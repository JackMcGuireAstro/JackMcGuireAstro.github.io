#!/usr/bin/env python3
"""Move old raw payloads of high-volume photometry alerts out of CTAS's working database.

Why (2026-11-16): the collector database is kept between cloud runs as one compressed
release asset (scripts/ctas_cloud_state.py) and GitHub refuses assets of 2 GiB or more.
After the 2026-11-08 retention the largest table is ``alert_envelopes`` (2.2 GB of the
5.5 GB file), and almost all of it is the raw JSON of photometry alerts from five
high-volume sources (ztf-alerce, atlas, rubin-fink, rubin-lasair, ztf-irsa-release). Each
such row is about 3 KB of payload, so SQLite fits one row per 4 KB page; nothing in the
cloud cycle reads those payloads after the first day (the site exporter reads raw payloads
only of the TNS and GCN streams, which are never archived here).

What it does (``run``), per ISO week of ``received_at`` that ended at least
``--min-age-days`` (14) days ago, oldest first, at most ``--max-weeks`` weeks per run:

1. writes the week's not-yet-archived payloads of those five sources to
   ``envelopes-<ISO year>-W<week>.ndjson`` (one line per envelope:
   ``{"id", "sha256", "payload"}``; ``payload`` is the exact text the database holds, kept
   as a JSON string) and compresses it with zstd (``-10 --long=27``);
2. checks the compressed file locally (every line parses, every sha256 matches, every id
   is there exactly once);
3. uploads it to the private state repository's ``state`` release under a name that is
   not there yet (a second batch of the same week gets ``-p2``, ``-p3`` ...; an asset is
   never replaced or deleted by this script);
4. downloads it again, compares the file's sha256 with what was uploaded, decompresses it
   and verifies every payload's sha256;
5. in ONE transaction replaces each row's ``raw_payload`` with a pointer
   ``{"ctas_archived_payload": {"schema", "asset", "sha256", "bytes"}}``, each UPDATE
   conditioned on the row still holding exactly the payload text that came back from the
   download. If any row does not match, the transaction is rolled back and nothing in the
   database changes for that week (the uploaded asset stays unused).

Every other column of the row stays, so receipts, checksums, counts and the exporter's
joins are unchanged. The collector reads an archived payload through its own loader
(``supernova_watch/envelope_archive.py``): fetched on demand from the release, verified
against the pointer's sha256 and byte count, cached; an error if it cannot be fetched,
never silently empty. Restores never need the archives.

After a run that archived something the file is rebuilt (``VACUUM``; see ``compact``), so
the file and its compressed copy actually shrink.

    ctas_envelope_archive.py run --db <soc.db> --work <dir> [--report <file.json>]
        [--min-age-days 14] [--max-weeks 2] [--now <ISO time>] [--dry-run]
    ctas_envelope_archive.py status --db <soc.db>      counts archived / pending per week

GitHub calls go through ``gh`` with GH_TOKEN (scripts/ctas_cloud_state.py); for tests
CTAS_ENVELOPE_ARCHIVE_LOCAL_RELEASE=<dir> stands in for the release. Exit 0 when the run
finished (including "nothing to archive"), 1 when a week could not be archived (the
database is left as it was for that week; earlier weeks of the same run stay archived).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ctas_cloud_state as state  # noqa: E402

PROVIDERS = ("ztf-alerce", "atlas", "rubin-fink", "rubin-lasair", "ztf-irsa-release")
MARKER = "ctas_archived_payload"
POINTER_SCHEMA = "ctas.envelope-payload-archive-pointer@1"
TEXT_PREFIX = '{"' + MARKER + '": '
ASSET_PREFIX = "envelopes-"
ASSET_SUFFIX = ".ndjson.zst"
ZSTD_ARGS = ["-10", "--long=27"]
UPDATE_BATCH = 2000


def pointer_text(asset: str, payload: str) -> str:
    encoded = payload.encode("utf-8")
    pointer = {MARKER: {"schema": POINTER_SCHEMA, "asset": asset,
                        "sha256": hashlib.sha256(encoded).hexdigest(), "bytes": len(encoded)}}
    # json.dumps' default separators: the same text SQLAlchemy writes for a JSON column, so
    # the collector's prefix test (envelope_archive.TEXT_PREFIX) matches either way.
    return json.dumps(pointer)


def week_bounds(day: datetime) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day) - timedelta(days=day.weekday())
    return start, start + timedelta(days=7)


def week_label(start: datetime) -> str:
    year, week, _ = start.isocalendar()
    return f"{year}-W{week:02d}"


def db_time(value: datetime) -> str:
    """received_at as SQLAlchemy stores it on SQLite: 'YYYY-MM-DD HH:MM:SS.ffffff', UTC."""
    return value.strftime("%Y-%m-%d %H:%M:%S.%f")


def parse_now(text: str | None) -> datetime:
    if not text:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc).replace(tzinfo=None) if parsed.tzinfo else parsed


PENDING = (f"provider IN ({','.join('?' * len(PROVIDERS))}) AND typeof(raw_payload) = 'text' "
           f"AND substr(raw_payload, 1, {len(TEXT_PREFIX)}) != ?")


def pending_weeks(con: sqlite3.Connection, cutoff: datetime) -> list[datetime]:
    """Start (Monday 00:00) of every week holding archivable rows and ending by ``cutoff``."""
    weeks: list[datetime] = []
    row = con.execute(f"SELECT min(received_at) FROM alert_envelopes WHERE {PENDING} AND received_at < ?",
                      (*PROVIDERS, TEXT_PREFIX, db_time(cutoff))).fetchone()
    if not row or not row[0]:
        return weeks
    start, end = week_bounds(datetime.fromisoformat(str(row[0])[:19]))
    while end <= cutoff:
        found = con.execute(f"SELECT 1 FROM alert_envelopes WHERE {PENDING} AND received_at >= ? AND received_at < ? LIMIT 1",
                            (*PROVIDERS, TEXT_PREFIX, db_time(start), db_time(end))).fetchone()
        if found:
            weeks.append(start)
        start, end = end, end + timedelta(days=7)
    return weeks


# ---------------------------------------------------------------------- the release
class GhRelease:
    def names(self) -> set[str]:
        return {a["name"] for a in state.assets()}

    def upload(self, path: Path) -> None:
        state.gh("release", "upload", state.TAG, str(path), "--repo", state.STATE_REPO, capture=False)

    def download(self, name: str, directory: Path) -> Path:
        state.gh("release", "download", state.TAG, "--repo", state.STATE_REPO, "--pattern", name,
                 "--dir", str(directory), capture=False)
        return directory / name


class LocalRelease:
    """A directory standing in for the release (tests and rehearsals)."""

    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def names(self) -> set[str]:
        return {p.name for p in self.directory.iterdir() if p.is_file()}

    def upload(self, path: Path) -> None:
        target = self.directory / path.name
        if target.exists():
            raise RuntimeError(f"{path.name} already exists in the release")
        shutil.copyfile(path, target)

    def download(self, name: str, directory: Path) -> Path:
        shutil.copyfile(self.directory / name, directory / name)
        return directory / name


def release() -> GhRelease | LocalRelease:
    local = os.environ.get("CTAS_ENVELOPE_ARCHIVE_LOCAL_RELEASE")
    return LocalRelease(Path(local)) if local else GhRelease()


def free_name(label: str, existing: set[str]) -> str:
    base = f"{ASSET_PREFIX}{label}"
    if base + ASSET_SUFFIX not in existing:
        return base + ASSET_SUFFIX
    part = 2
    while f"{base}-p{part}{ASSET_SUFFIX}" in existing:
        part += 1
    return f"{base}-p{part}{ASSET_SUFFIX}"


# ---------------------------------------------------------------------- files
def compress(source: Path, target: Path) -> None:
    result = subprocess.run(["zstd", "-q", "-f", "-T0", *ZSTD_ARGS, str(source), "-o", str(target)],
                            check=False, text=True, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"zstd failed: {result.stderr.strip()[:300]}")


def read_lines(compressed: Path):
    """Yield (id, sha256, payload) from a compressed asset, streamed, checking each line."""
    process = subprocess.Popen(["zstd", "-q", "-d", "-c", "--long=27", str(compressed)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout is not None
    try:
        for raw in process.stdout:
            if not raw.strip():
                continue
            line = json.loads(raw)
            payload = line.get("payload")
            if not isinstance(payload, str) or not isinstance(line.get("id"), str):
                raise RuntimeError(f"{compressed.name}: a line has no id or payload text")
            if hashlib.sha256(payload.encode("utf-8")).hexdigest() != line.get("sha256"):
                raise RuntimeError(f"{compressed.name}: payload of {line['id']} does not match its sha256")
            yield line["id"], line["sha256"], payload
    except BaseException:
        process.stdout.close()
        process.kill()
        process.wait()
        if process.stderr:
            process.stderr.close()
        raise
    process.stdout.close()
    error = process.stderr.read() if process.stderr else b""
    if process.stderr:
        process.stderr.close()
    if process.wait() != 0:
        raise RuntimeError(f"zstd could not read {compressed.name}: {error.decode(errors='replace')[:300]}")


def check_asset(compressed: Path, expected: dict[str, str]) -> None:
    seen: set[str] = set()
    for envelope_id, sha, _ in read_lines(compressed):
        if envelope_id in seen or expected.get(envelope_id) != sha:
            raise RuntimeError(f"{compressed.name}: unexpected or repeated line for {envelope_id}")
        seen.add(envelope_id)
    if len(seen) != len(expected):
        raise RuntimeError(f"{compressed.name}: {len(seen)} lines, expected {len(expected)}")


# ---------------------------------------------------------------------- one week
def archive_week(con: sqlite3.Connection, start: datetime, work: Path, target, existing: set[str]) -> dict:
    began = time.monotonic()
    end = start + timedelta(days=7)
    label = week_label(start)
    name = free_name(label, existing)
    plain = work / name[: -len(".zst")]
    compressed = work / name
    expected: dict[str, str] = {}
    payload_bytes = 0
    with plain.open("w", encoding="utf-8") as out:
        for envelope_id, payload in con.execute(
                f"SELECT id, raw_payload FROM alert_envelopes WHERE {PENDING} AND received_at >= ? AND received_at < ? "
                "ORDER BY rowid", (*PROVIDERS, TEXT_PREFIX, db_time(start), db_time(end))):
            encoded = payload.encode("utf-8")
            sha = hashlib.sha256(encoded).hexdigest()
            expected[envelope_id] = sha
            payload_bytes += len(encoded)
            out.write(json.dumps({"id": envelope_id, "sha256": sha, "payload": payload}) + "\n")
    if not expected:
        plain.unlink(missing_ok=True)
        return {"week": label, "rows": 0}
    compress(plain, compressed)
    plain.unlink()
    check_asset(compressed, expected)
    asset_sha = state.sha256_file(compressed)
    asset_bytes = compressed.stat().st_size
    target.upload(compressed)
    existing.add(name)
    compressed.unlink()
    if name not in target.names():
        raise RuntimeError(f"{name} did not appear in the release after upload")
    check_dir = work / "roundtrip"
    shutil.rmtree(check_dir, ignore_errors=True)
    check_dir.mkdir()
    try:
        fetched = target.download(name, check_dir)
        if state.sha256_file(fetched) != asset_sha:
            raise RuntimeError(f"{name}: the downloaded copy differs from the uploaded one")
        updated = 0
        seen: set[str] = set()
        con.execute("BEGIN IMMEDIATE")
        try:
            batch: list[tuple[str, str, str]] = []
            for envelope_id, sha, payload in read_lines(fetched):
                if expected.get(envelope_id) != sha or envelope_id in seen:
                    raise RuntimeError(f"{name}: the downloaded copy has an unexpected line for {envelope_id}")
                seen.add(envelope_id)
                # Conditioned on the row holding exactly the text that came back from the release.
                batch.append((pointer_text(name, payload), envelope_id, payload))
                if len(batch) >= UPDATE_BATCH:
                    updated += con.executemany(
                        "UPDATE alert_envelopes SET raw_payload = ? WHERE id = ? AND raw_payload = ?", batch).rowcount
                    batch.clear()
            if batch:
                updated += con.executemany(
                    "UPDATE alert_envelopes SET raw_payload = ? WHERE id = ? AND raw_payload = ?", batch).rowcount
            if len(seen) != len(expected) or updated != len(expected):
                raise RuntimeError(f"{name}: {updated} of {len(expected)} rows matched the archived text")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        con.execute("COMMIT")
    finally:
        shutil.rmtree(check_dir, ignore_errors=True)
    return {"week": label, "asset": name, "rows": len(expected), "payload_bytes": payload_bytes,
            "asset_bytes": asset_bytes, "asset_sha256": asset_sha, "seconds": round(time.monotonic() - began, 1)}


def compact(con: sqlite3.Connection, work: Path) -> float:
    """Rebuild the file after a week was archived. The rewritten rows shrink in place, which
    SQLite does not give back on its own (each old row filled most of a 4 KB page, and the
    old payload bytes stay in the page until it is rewritten), so neither the file nor its
    compressed copy would get smaller. VACUUM keeps auto_vacuum=INCREMENTAL and writes its
    temporary copy under ``work`` (SQLITE_TMPDIR)."""
    began = time.monotonic()
    os.environ["SQLITE_TMPDIR"] = str(work)
    con.execute("VACUUM")
    return round(time.monotonic() - began, 1)


# ---------------------------------------------------------------------- commands
def run(args: argparse.Namespace) -> int:
    database = Path(args.db)
    work = Path(args.work)
    work.mkdir(parents=True, exist_ok=True)
    now = parse_now(args.now)
    cutoff = now - timedelta(days=args.min_age_days)
    report: dict = {"schema": "ctas.envelope-archive-run@1", "now": now.isoformat() + "Z",
                    "cutoff": cutoff.isoformat() + "Z", "providers": list(PROVIDERS),
                    "db_bytes_before": database.stat().st_size, "weeks": [], "status": "ok"}
    started = time.monotonic()
    con = sqlite3.connect(database, timeout=60, isolation_level=None)
    status = 0
    try:
        weeks = pending_weeks(con, cutoff)
        report["pending_weeks"] = [week_label(w) for w in weeks]
        if not weeks:
            report["status"] = "nothing-to-archive"
            print("nothing to archive: no complete week older than "
                  f"{args.min_age_days} days holds unarchived payloads")
        elif args.dry_run:
            report["status"] = "dry-run"
            print(f"dry run: would archive {min(len(weeks), args.max_weeks)} of {len(weeks)} pending week(s): "
                  + ", ".join(week_label(w) for w in weeks[: args.max_weeks]))
        else:
            target = release()
            existing = target.names()
            for start in weeks[: args.max_weeks]:
                try:
                    result = archive_week(con, start, work, target, existing)
                except Exception as error:  # one week failing leaves the database as it was for it
                    report["status"] = "failed"
                    report["error"] = f"{week_label(start)}: {str(error)[:300]}"
                    print(f"FAIL  {week_label(start)}: {str(error)[:300]}; the database is unchanged for that week")
                    status = 1
                    break
                report["weeks"].append(result)
                if result["rows"]:
                    print(f"archived {result['week']}: {result['rows']} payloads, {result['payload_bytes'] / 1e6:.0f} MB "
                          f"-> {result['asset']} ({result['asset_bytes'] / 1e6:.1f} MB) in {result['seconds']:.0f}s")
            if any(w.get("rows") for w in report["weeks"]):
                try:
                    report["vacuum_seconds"] = compact(con, work)
                except sqlite3.Error as error:
                    # The archived weeks are complete and consistent either way; the space
                    # comes back on the next run that compacts.
                    report["vacuum_error"] = str(error)[:200]
                    print(f"WARN  could not compact the database: {str(error)[:200]}")
    finally:
        con.close()
        report["db_bytes_after"] = database.stat().st_size
        report["rows_archived"] = sum(w.get("rows", 0) for w in report["weeks"])
        report["payload_bytes_moved"] = sum(w.get("payload_bytes", 0) for w in report["weeks"])
        report["seconds"] = round(time.monotonic() - started, 1)
        report["weeks_left"] = max(0, len(report.get("pending_weeks", [])) - len(report["weeks"]))
        if args.report:
            Path(args.report).write_text(json.dumps(report, indent=2) + "\n")
        if report["weeks"]:
            print(f"archive moved {report['payload_bytes_moved'] / 1e6:.0f} MB in {report['rows_archived']} payloads; "
                  f"database {report['db_bytes_before'] / 2**30:.2f} -> {report['db_bytes_after'] / 2**30:.2f} GiB; "
                  f"{report['weeks_left']} week(s) still pending")
    return status


def status_command(args: argparse.Namespace) -> int:
    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    rows = con.execute(
        f"SELECT strftime('%Y-%m-%d', received_at, 'weekday 0', '-6 days') AS monday, "
        f"SUM(substr(raw_payload, 1, {len(TEXT_PREFIX)}) = ?), SUM(substr(raw_payload, 1, {len(TEXT_PREFIX)}) != ?) "
        f"FROM alert_envelopes WHERE provider IN ({','.join('?' * len(PROVIDERS))}) GROUP BY 1 ORDER BY 1",
        (TEXT_PREFIX, TEXT_PREFIX, *PROVIDERS)).fetchall()
    con.close()
    for monday, archived, kept in rows:
        print(f"week of {monday}: {archived or 0} archived, {kept or 0} in the database")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run")
    r.add_argument("--db", required=True)
    r.add_argument("--work", required=True)
    r.add_argument("--report")
    r.add_argument("--min-age-days", type=int, default=int(os.environ.get("CTAS_ENVELOPE_ARCHIVE_MIN_AGE_DAYS", "14")))
    r.add_argument("--max-weeks", type=int, default=int(os.environ.get("CTAS_ENVELOPE_ARCHIVE_WEEKS", "2") or 2))
    r.add_argument("--now")
    r.add_argument("--dry-run", action="store_true")
    s = sub.add_parser("status")
    s.add_argument("--db", required=True)
    args = parser.parse_args(argv)
    if args.command == "status":
        return status_command(args)
    if args.min_age_days < 7:
        parser.error("--min-age-days must be at least 7")
    return run(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

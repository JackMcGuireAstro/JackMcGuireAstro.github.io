#!/usr/bin/env python3
"""Keep CTAS's working database in a private GitHub repository between cloud runs.

CTAS's collector and publisher run in GitHub Actions (.github/workflows/ctas-cloud.yml),
so nothing lives on Jack's Mac. Between runs the collector's SQLite database and its
fixed input files are kept as release assets of one release (tag ``state``) in the
private state repository (default JackMcGuireAstro/ctas-state):

    static-inputs.tar.gz      reference tables, benchmarks, templates (rarely change)
    soc-<UTC stamp>.db.gz     the database after a run; the newest is the current one

    ctas_cloud_state.py restore <data-dir>
        Download the static inputs and the newest database into <data-dir>
        (<data-dir>/soc.db). Fails when no database is found.
    ctas_cloud_state.py save <data-dir>
        Checkpoint and check the database (PRAGMA quick_check must be "ok"),
        compress it, upload it under a new name, then delete older copies
        except the newest KEEP_RECENT and the newest one of each of the
        previous KEEP_DAYS UTC days. A copy that fails the check is never uploaded,
        so the last good state is never replaced by a damaged one.

The ``gh`` command line tool does the GitHub calls; GH_TOKEN must allow reading
and writing the state repository's contents. Nothing here prints that token.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_REPO = os.environ.get("CTAS_STATE_REPO", "JackMcGuireAstro/ctas-state")
TAG = os.environ.get("CTAS_STATE_TAG", "state")
STATIC = "static-inputs.tar.gz"
DB_PREFIX, DB_SUFFIX = "soc-", ".db.gz"
KEEP_RECENT = int(os.environ.get("CTAS_STATE_KEEP_RECENT", "3"))
KEEP_DAYS = int(os.environ.get("CTAS_STATE_KEEP_DAYS", "2"))


def gh(*args: str, capture: bool = True) -> str:
    result = subprocess.run(["gh", *args], check=False, text=True,
                            stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"gh {args[0]} {args[1] if len(args) > 1 else ''} failed: {result.stderr.strip()[:300]}")
    return result.stdout if capture else ""


def assets() -> list[dict]:
    raw = gh("api", f"repos/{STATE_REPO}/releases/tags/{TAG}")
    release = json.loads(raw)
    return [{"id": a["id"], "name": a["name"], "size": a.get("size", 0), "created_at": a.get("created_at", "")}
            for a in release.get("assets", [])]


def database_assets(listing: list[dict]) -> list[dict]:
    found = [a for a in listing if a["name"].startswith(DB_PREFIX) and a["name"].endswith(DB_SUFFIX)]
    # The stamp in the name sorts in time order; created_at breaks ties.
    return sorted(found, key=lambda a: (a["name"], a["created_at"]), reverse=True)


def stamp_day(name: str) -> str:
    return name[len(DB_PREFIX):len(DB_PREFIX) + 8]


def to_delete(listing: list[dict]) -> list[dict]:
    """Database copies to remove: keep the newest KEEP_RECENT, plus the newest copy of
    each of the KEEP_DAYS most recent earlier UTC days; remove the rest."""
    newest = database_assets(listing)
    recent = newest[:KEEP_RECENT]
    keep = {a["id"] for a in recent}
    covered = {stamp_day(a["name"]) for a in recent}
    for asset in newest[KEEP_RECENT:]:
        day = stamp_day(asset["name"])
        if day in covered or len(covered - {stamp_day(a["name"]) for a in recent}) >= KEEP_DAYS:
            continue
        covered.add(day)
        keep.add(asset["id"])
    return [a for a in newest if a["id"] not in keep]


def restore(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    listing = assets()
    names = {a["name"] for a in listing}
    work = data_dir / ".state-download"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    if STATIC in names:
        gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", STATIC, "--dir", str(work), capture=False)
        with tarfile.open(work / STATIC) as archive:
            for member in archive.getmembers():
                target = (data_dir / member.name).resolve()
                if not str(target).startswith(str(data_dir.resolve()) + os.sep) and target != data_dir.resolve():
                    raise RuntimeError(f"refusing archive member outside the data folder: {member.name}")
            if hasattr(tarfile, "data_filter"):
                archive.extractall(data_dir, filter="data")
            else:
                archive.extractall(data_dir)
        print(f"restored {STATIC}")
    else:
        print(f"no {STATIC} in {STATE_REPO}; continuing without static inputs")
    databases = database_assets(listing)
    if not databases:
        raise RuntimeError(f"no database copy ({DB_PREFIX}*{DB_SUFFIX}) in {STATE_REPO} release {TAG}")
    chosen = databases[0]["name"]
    gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", chosen, "--dir", str(work), capture=False)
    target = data_dir / "soc.db"
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    with gzip.open(work / chosen, "rb") as source, open(target, "wb") as sink:
        shutil.copyfileobj(source, sink, 16 * 1024 * 1024)
    shutil.rmtree(work, ignore_errors=True)
    print(f"restored {chosen} ({target.stat().st_size / 1e9:.2f} GB)")


def checked_copy(database: Path, destination: Path) -> None:
    """Checkpoint the WAL, confirm the database is sound, and write a gzip copy."""
    con = sqlite3.connect(database, timeout=120)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        verdict = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    if verdict != "ok":
        raise RuntimeError(f"database check failed ({verdict}); the previous state is kept")
    with open(database, "rb") as source, gzip.open(destination, "wb", compresslevel=6) as sink:
        shutil.copyfileobj(source, sink, 16 * 1024 * 1024)


def save(data_dir: Path) -> None:
    database = data_dir / "soc.db"
    if not database.is_file():
        raise RuntimeError(f"{database} is missing")
    name = f"{DB_PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{DB_SUFFIX}"
    staged = data_dir.parent / name
    started = time.monotonic()
    checked_copy(database, staged)
    gh("release", "upload", TAG, str(staged), "--repo", STATE_REPO, capture=False)
    uploaded = staged.stat().st_size
    staged.unlink()
    listing = assets()
    if name not in {a["name"] for a in listing}:
        raise RuntimeError(f"{name} did not appear in the release after upload")
    for asset in to_delete(listing):
        gh("api", "-X", "DELETE", f"repos/{STATE_REPO}/releases/assets/{asset['id']}")
        print(f"removed older copy {asset['name']}")
    print(f"saved {name} ({uploaded / 1e6:.0f} MB compressed) in {time.monotonic() - started:.0f}s")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"restore", "save"}:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        (restore if argv[1] == "restore" else save)(Path(argv[2]))
    except (RuntimeError, OSError, sqlite3.Error, tarfile.TarError) as error:
        print(f"ctas_cloud_state {argv[1]}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

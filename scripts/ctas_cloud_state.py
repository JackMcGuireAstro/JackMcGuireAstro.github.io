#!/usr/bin/env python3
"""Keep CTAS's working database in a private GitHub repository between cloud runs.

CTAS's collector and publisher run in GitHub Actions (.github/workflows/ctas-cloud.yml),
so nothing lives on Jack's Mac. Between runs the collector's SQLite database and its
fixed input files are kept as release assets of one release (tag ``state``) in the
private state repository (default JackMcGuireAstro/ctas-state):

    static-inputs.tar.gz      reference tables, benchmarks, templates (rarely change)
    soc-<UTC stamp>.db.zst    the database after a run; the newest is the current one
                              (zstd -10 --long=27: about 40% smaller than the gzip -6
                              copies .db.gz written until 2026-11-08, which restore still
                              reads; gzip is used again when the zstd tool is missing)
    skymaps.tar.gz            the retained public sky maps of the last 30 days (Track 3d),
                              replaced on every save; absent until a map has been retained

    ctas_cloud_state.py restore <data-dir>
        Download the static inputs and the newest database into <data-dir>
        (<data-dir>/soc.db). Fails when no database is found.
    ctas_cloud_state.py save <data-dir>
        Checkpoint and check the database (PRAGMA quick_check must be "ok"),
        compress it (zstd, or gzip without the zstd tool; CTAS_STATE_COMPRESSOR forces one), upload it under a new name, then delete older copies
        except the newest KEEP_RECENT and the newest one of each of the
        previous KEEP_DAYS UTC days. A copy that fails the check is never uploaded,
        so the last good state is never replaced by a damaged one.

The ``gh`` command line tool does the GitHub calls; GH_TOKEN must allow reading
and writing the state repository's contents. Nothing here prints that token.

When STATE_RECEIPT names a file, ``restore`` writes a small JSON receipt there
(which copy was restored and when it was saved) for the run's public marker.

Size guard (2026-11-08). GitHub refuses release assets of 2 GiB or more, so ``save``
measures the compressed copy before uploading it:

* above CTAS_STATE_WARN_GIB (1.4 GiB) it uploads as usual and prints a WARN line;
* before uploading, the compressed copy must decompress to exactly the checked database;
  the first copy in a new format is also downloaded again and compared before any older
  copy is deleted (otherwise the new asset is removed and the older copies stay);
* above CTAS_STATE_REFUSE_GIB (1.9 GiB) it does NOT upload: the previous copy stays the
  current state (the next run restores it and CTAS keeps publishing from it), the line
  "STATE TOO LARGE" is printed and the exit status is 4.

When STATE_SIZE_REPORT names a file, ``save`` writes {raw_bytes, compressed_bytes, verdict,
warn_bytes, refuse_bytes} there; scripts/ctas_cloud_run.sh records it as the advisory
check ``ctas.state-size`` (a check-warning issue that closes itself once the size is back
under the warning line).
"""
from __future__ import annotations

import gzip
import hashlib
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
DB_PREFIX = "soc-"
ZSTD_SUFFIX, GZIP_SUFFIX = ".db.zst", ".db.gz"
DB_SUFFIXES = (ZSTD_SUFFIX, GZIP_SUFFIX)
ZSTD_ARGS = ["-10", "--long=27"]   # --long=27 must be given when decompressing too
KEEP_RECENT = int(os.environ.get("CTAS_STATE_KEEP_RECENT", "3"))
KEEP_DAYS = int(os.environ.get("CTAS_STATE_KEEP_DAYS", "2"))
SKYMAPS = "skymaps.tar.gz"
GIB = 1 << 30
WARN_BYTES = int(float(os.environ.get("CTAS_STATE_WARN_GIB", "1.4")) * GIB)
REFUSE_BYTES = int(float(os.environ.get("CTAS_STATE_REFUSE_GIB", "1.9")) * GIB)
TOO_LARGE_EXIT = 4


class StateTooLarge(RuntimeError):
    """The compressed copy is too close to GitHub's 2 GiB asset limit; it was not uploaded."""


def size_verdict(compressed: int, warn: int = WARN_BYTES, refuse: int = REFUSE_BYTES) -> str:
    if compressed > refuse:
        return "refuse"
    return "warn" if compressed > warn else "ok"


def write_size_report(raw: int, compressed: int, verdict: str) -> None:
    path = os.environ.get("STATE_SIZE_REPORT")
    if not path:
        return
    Path(path).write_text(json.dumps({"raw_bytes": raw, "compressed_bytes": compressed, "verdict": verdict,
                                      "warn_bytes": WARN_BYTES, "refuse_bytes": REFUSE_BYTES}) + "\n",
                          encoding="utf-8")
SKYMAP_KEEP_DAYS = int(os.environ.get("CTAS_SKYMAP_KEEP_DAYS", "30"))


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
    found = [a for a in listing if a["name"].startswith(DB_PREFIX) and a["name"].endswith(DB_SUFFIXES)]
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


def stamp_to_iso(name: str, prefix: str) -> str | None:
    """The UTC stamp in an asset name (``soc-20261016T061500Z.db.zst``) as ISO-8601."""
    stamp = name[len(prefix):len(prefix) + 16]
    try:
        return datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def write_receipt(asset: str, prefix: str) -> None:
    """Record which state copy was restored, when it was saved, and when (STATE_RECEIPT)."""
    path = os.environ.get("STATE_RECEIPT")
    if not path:
        return
    receipt = {"asset": asset, "saved_at": stamp_to_iso(asset, prefix),
               "restored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    Path(path).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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
    if SKYMAPS in names:
        gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", SKYMAPS, "--dir", str(work), capture=False)
        maps = data_dir / "skymaps"
        maps.mkdir(exist_ok=True)
        with tarfile.open(work / SKYMAPS) as archive:
            members = [m for m in archive.getmembers() if m.isfile() and "/" not in m.name and m.name.endswith(".fits")]
            for member in members:
                archive.extract(member, maps, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
        print(f"restored {SKYMAPS} ({len(members)} sky maps)")
    databases = database_assets(listing)
    if not databases:
        raise RuntimeError(f"no database copy ({DB_PREFIX}*{' or *'.join(DB_SUFFIXES)}) in {STATE_REPO} release {TAG}")
    chosen = databases[0]["name"]
    gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", chosen, "--dir", str(work), capture=False)
    target = data_dir / "soc.db"
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    decompress(work / chosen, target)
    shutil.rmtree(work, ignore_errors=True)
    write_receipt(chosen, DB_PREFIX)
    print(f"restored {chosen} ({target.stat().st_size / 1e9:.2f} GB)")


def compressor() -> str:
    """zstd when its command line tool is installed (GitHub's runners have it), else gzip."""
    forced = os.environ.get("CTAS_STATE_COMPRESSOR", "").strip().lower()
    if forced in {"zstd", "gzip"}:
        return forced
    return "zstd" if shutil.which("zstd") else "gzip"


def compress(source: Path, destination: Path) -> None:
    if destination.name.endswith(ZSTD_SUFFIX):
        result = subprocess.run(["zstd", "-q", "-f", "-T0", *ZSTD_ARGS, str(source), "-o", str(destination)],
                                check=False, text=True, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError(f"zstd failed: {result.stderr.strip()[:300]}")
        return
    with open(source, "rb") as handle, gzip.open(destination, "wb", compresslevel=6) as sink:
        shutil.copyfileobj(handle, sink, 16 * 1024 * 1024)


def decompress(archive: Path, target: Path) -> None:
    if archive.name.endswith(ZSTD_SUFFIX):
        result = subprocess.run(["zstd", "-q", "-d", "-f", "--long=27", str(archive), "-o", str(target)],
                                check=False, text=True, stderr=subprocess.PIPE)
        if result.returncode != 0:
            raise RuntimeError(f"zstd could not decompress {archive.name}: {result.stderr.strip()[:300]}")
        return
    with gzip.open(archive, "rb") as source, open(target, "wb") as sink:
        shutil.copyfileobj(source, sink, 16 * 1024 * 1024)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_decompressed(archive: Path) -> str:
    """sha256 of what the archive decompresses to, streamed (no second copy on disk)."""
    digest = hashlib.sha256()
    if archive.name.endswith(ZSTD_SUFFIX):
        process = subprocess.Popen(["zstd", "-q", "-d", "-c", "--long=27", str(archive)],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout is not None
        for block in iter(lambda: process.stdout.read(16 * 1024 * 1024), b""):
            digest.update(block)
        _, error = process.communicate()
        if process.returncode != 0:
            raise RuntimeError(f"zstd could not read {archive.name}: {error.decode(errors='replace').strip()[:300]}")
        return digest.hexdigest()
    with gzip.open(archive, "rb") as handle:
        for block in iter(lambda: handle.read(16 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_copy(database: Path, destination: Path) -> None:
    """Checkpoint the WAL, confirm the database is sound, and write a compressed copy
    (zstd for a .db.zst destination, gzip otherwise)."""
    con = sqlite3.connect(database, timeout=120)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        verdict = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    if verdict != "ok":
        raise RuntimeError(f"database check failed ({verdict}); the previous state is kept")
    compress(database, destination)


def save(data_dir: Path) -> None:
    database = data_dir / "soc.db"
    if not database.is_file():
        raise RuntimeError(f"{database} is missing")
    suffix = ZSTD_SUFFIX if compressor() == "zstd" else GZIP_SUFFIX
    name = f"{DB_PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{suffix}"
    staged = data_dir.parent / name
    started = time.monotonic()
    checked_copy(database, staged)
    raw, uploaded = database.stat().st_size, staged.stat().st_size
    verdict = size_verdict(uploaded)
    write_size_report(raw, uploaded, verdict)
    sizes = f"{uploaded / GIB:.2f} GiB compressed, {raw / GIB:.2f} GiB raw"
    if verdict == "refuse":
        staged.unlink()
        raise StateTooLarge(f"STATE TOO LARGE: the database is {sizes}, over the {REFUSE_BYTES / GIB:.1f} GiB "
                            "refusal line (GitHub's release-asset limit is 2 GiB); it was NOT uploaded and the "
                            "previous copy stays the current state. Trim it with scripts/ctas_db_retention.py.")
    if verdict == "warn":
        print(f"WARN  the database is {sizes}, over the {WARN_BYTES / GIB:.1f} GiB warning line; uploads are "
              f"refused above {REFUSE_BYTES / GIB:.1f} GiB (GitHub's limit is 2 GiB)")
    # Round trip before anything older is deleted: the staged copy must decompress to exactly
    # the checked database. The first copy in a format the release does not hold yet (the
    # first zstd save after 2026-11-08) is also downloaded again and compared byte for byte;
    # on any mismatch the new asset is removed and every older copy stays.
    source_sha = sha256_file(database)
    if sha256_decompressed(staged) != source_sha:
        staged.unlink()
        raise RuntimeError(f"{name} does not decompress to the database; nothing uploaded, the previous state is kept")
    first_in_format = not any(a["name"].endswith(suffix) for a in database_assets(assets()))
    staged_sha = sha256_file(staged) if first_in_format else ""
    gh("release", "upload", TAG, str(staged), "--repo", STATE_REPO, capture=False)
    staged.unlink()
    listing = assets()
    if name not in {a["name"] for a in listing}:
        raise RuntimeError(f"{name} did not appear in the release after upload")
    if first_in_format:
        check_dir = data_dir.parent / ".state-roundtrip"
        shutil.rmtree(check_dir, ignore_errors=True)
        check_dir.mkdir()
        try:
            gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", name, "--dir", str(check_dir), capture=False)
            fetched = check_dir / name
            same = sha256_file(fetched) == staged_sha and sha256_decompressed(fetched) == source_sha
        except (RuntimeError, OSError) as error:
            same, detail = False, str(error)
        else:
            detail = "the downloaded copy differs from the uploaded one"
        finally:
            shutil.rmtree(check_dir, ignore_errors=True)
        if not same:
            for asset in listing:
                if asset["name"] == name:
                    gh("api", "-X", "DELETE", f"repos/{STATE_REPO}/releases/assets/{asset['id']}")
            raise RuntimeError(f"round trip of {name} failed ({detail[:200]}); it was removed and the previous state is kept")
        print(f"round trip verified: {name} downloads and decompresses to the saved database")
    for asset in to_delete(listing):
        gh("api", "-X", "DELETE", f"repos/{STATE_REPO}/releases/assets/{asset['id']}")
        print(f"removed older copy {asset['name']}")
    save_skymaps(data_dir, database)
    print(f"saved {name} ({uploaded / 1e6:.0f} MB compressed) in {time.monotonic() - started:.0f}s")


def recent_skymap_files(data_dir: Path, database: Path) -> list[Path]:
    """Retained sky-map files whose revision is younger than SKYMAP_KEEP_DAYS."""

    maps = data_dir / "skymaps"
    if not maps.is_dir():
        return []
    con = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT artifact_path FROM skymap_revisions WHERE created_at >= datetime('now', ?)",
            (f"-{SKYMAP_KEEP_DAYS} days",),
        ).fetchall()
    except sqlite3.Error:
        rows = []
    finally:
        con.close()
    names = sorted({Path(str(row[0])).name for row in rows})
    return [maps / name for name in names if (maps / name).is_file()]


def save_skymaps(data_dir: Path, database: Path) -> None:
    files = recent_skymap_files(data_dir, database)
    if not files:
        return
    staged = data_dir.parent / SKYMAPS
    with tarfile.open(staged, "w:gz") as archive:
        for path in files:
            archive.add(path, arcname=path.name)
    gh("release", "upload", TAG, str(staged), "--repo", STATE_REPO, "--clobber", capture=False)
    print(f"saved {SKYMAPS} ({len(files)} sky maps, {staged.stat().st_size / 1e6:.1f} MB)")
    staged.unlink()


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"restore", "save"}:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        (restore if argv[1] == "restore" else save)(Path(argv[2]))
    except StateTooLarge as error:
        print(str(error), file=sys.stderr)
        return TOO_LARGE_EXIT
    except (RuntimeError, OSError, sqlite3.Error, tarfile.TarError) as error:
        print(f"ctas_cloud_state {argv[1]}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

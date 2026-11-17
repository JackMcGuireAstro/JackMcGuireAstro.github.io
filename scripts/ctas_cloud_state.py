#!/usr/bin/env python3
"""Keep CTAS's working database in a private GitHub repository between cloud runs.

CTAS's collector and publisher run in GitHub Actions (.github/workflows/ctas-cloud.yml),
so nothing lives on Jack's Mac. Between runs the collector's SQLite database and its
fixed input files are kept as release assets of one release (tag ``state``) in the
private state repository (default JackMcGuireAstro/ctas-state):

    static-inputs.tar.gz      reference tables, benchmarks, templates (rarely change)
    soc-<UTC stamp>.parts.json            one saved copy of the database (since 2026-11-16):
    soc-<UTC stamp>.db.zst.part-000 ...   the zstd stream (-10 --long=27) cut into parts of
                                          CTAS_STATE_PART_GIB (1.5 GiB) and a manifest
                                          naming every part with its size and sha256, the
                                          whole stream's sha256 and the database's sha256
    soc-<UTC stamp>.db.zst / .db.gz       single-file copies written until 2026-11-16 (zstd
                                          since 2026-11-08, gzip before); restore still reads them
    skymaps.tar.gz            the retained public sky maps of the last 30 days (Track 3d),
                              replaced on every save; absent until a map has been retained
    envelopes-<year>-W<week>.ndjson.zst   archived alert payloads (scripts/ctas_envelope_archive.py;
                                          never touched here)

Why parts: GitHub refuses a release asset of 2 GiB or more. Until 2026-11-16 the whole copy
was one asset and the save was refused at 1.9 GiB; cut into parts, the copy has no size limit
from GitHub, and what bounds it is the runner (disk and time), see the size guard below.

    ctas_cloud_state.py restore <data-dir>
        Download the static inputs and the newest complete database copy into <data-dir>
        (<data-dir>/soc.db). A set is complete when its manifest and every part it names
        are in the release with the named sizes; parts without a manifest (an interrupted
        upload) are never used. Every part's sha256, the joined stream's sha256 and the
        restored database's sha256 are checked against the manifest (streamed: no part is
        held in memory). Fails when no complete copy is found or a check fails.
    ctas_cloud_state.py save <data-dir>
        Checkpoint and check the database (PRAGMA quick_check must be "ok"), compress it
        into parts (zstd, or gzip without the zstd tool; CTAS_STATE_COMPRESSOR forces one),
        check that the joined parts decompress to exactly the database, upload the parts
        and then the manifest, and check that every one arrived with its size. The first
        set in a release is also downloaded again and checked part by part before anything
        older is deleted. Only then are older copies deleted: the newest KEEP_RECENT copies
        and the newest copy of each of the previous KEEP_DAYS UTC days stay (a set counts as
        one copy), and leftover parts of an earlier interrupted save are removed. A copy
        that fails a check is removed and never replaces the last good one.

The ``gh`` command line tool does the GitHub calls; GH_TOKEN must allow reading
and writing the state repository's contents. Nothing here prints that token.

When STATE_RECEIPT names a file, ``restore`` writes a small JSON receipt there
(which copy was restored and when it was saved) for the run's public marker.

Size guard (2026-11-16, replaces the 1.4/1.9 GiB lines of 2026-11-08):

* "state growing" (advisory): the compressed copy is over CTAS_STATE_WARN_GIB (1.4 GiB).
  It is uploaded as usual; the line is an early signal that growth needs a look.
* refusal (exit 4, "STATE TOO LARGE"): the raw database is over CTAS_STATE_REFUSE_RAW_GIB
  (20 GiB). A cycle needs about three times the raw size on the runner's disk (the restored
  database, plus a temporary copy and journal when ctas_envelope_archive.py compacts it),
  and the runner had about 84 GiB free at setup and 72 GiB during publish (telemetry,
  2026-11-15, with the 8 GiB swap file). Above the line the copy is not uploaded, the
  previous state stays current, and the database must be trimmed.

When STATE_SIZE_REPORT names a file, ``save`` writes {raw_bytes, compressed_bytes, parts,
verdict, warn_bytes, refuse_raw_bytes, free_disk_bytes} there; scripts/ctas_cloud_run.sh
records it as the advisory check ``ctas.state-size`` (a check-warning issue that closes
itself once the copy is back under the warning line).
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
import tempfile
import threading
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path

STATE_REPO = os.environ.get("CTAS_STATE_REPO", "JackMcGuireAstro/ctas-state")
TAG = os.environ.get("CTAS_STATE_TAG", "state")
STATIC = "static-inputs.tar.gz"
DB_PREFIX = "soc-"
ZSTD_SUFFIX, GZIP_SUFFIX = ".db.zst", ".db.gz"
DB_SUFFIXES = (ZSTD_SUFFIX, GZIP_SUFFIX)
PART_MARK = ".part-"
MANIFEST_SUFFIX = ".parts.json"
MANIFEST_SCHEMA = "ctas.state-parts@1"
ZSTD_ARGS = ["-10", "--long=27"]   # --long=27 must be given when decompressing too
KEEP_RECENT = int(os.environ.get("CTAS_STATE_KEEP_RECENT", "3"))
KEEP_DAYS = int(os.environ.get("CTAS_STATE_KEEP_DAYS", "2"))
SKYMAPS = "skymaps.tar.gz"
GIB = 1 << 30
BLOCK = 16 << 20
PART_BYTES = max(1, int(float(os.environ.get("CTAS_STATE_PART_GIB", "1.5")) * GIB))
WARN_BYTES = int(float(os.environ.get("CTAS_STATE_WARN_GIB", "1.4")) * GIB)
REFUSE_RAW_BYTES = int(float(os.environ.get("CTAS_STATE_REFUSE_RAW_GIB", "20")) * GIB)
TOO_LARGE_EXIT = 4


class StateTooLarge(RuntimeError):
    """The raw database is too large for the runner's disk; it was not uploaded."""


def size_verdict(compressed: int, raw: int = 0, warn: int = WARN_BYTES, refuse_raw: int = REFUSE_RAW_BYTES) -> str:
    if raw > refuse_raw:
        return "refuse"
    return "growing" if compressed > warn else "ok"


def write_size_report(raw: int, compressed: int, verdict: str, parts: int = 0, free_disk: int | None = None) -> None:
    path = os.environ.get("STATE_SIZE_REPORT")
    if not path:
        return
    Path(path).write_text(json.dumps({"raw_bytes": raw, "compressed_bytes": compressed, "parts": parts,
                                      "verdict": verdict, "warn_bytes": WARN_BYTES,
                                      "refuse_raw_bytes": REFUSE_RAW_BYTES, "free_disk_bytes": free_disk}) + "\n",
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
    """Single-file database copies (the format until 2026-11-16), newest first."""
    found = [a for a in listing if a["name"].startswith(DB_PREFIX) and a["name"].endswith(DB_SUFFIXES)]
    # The stamp in the name sorts in time order; created_at breaks ties.
    return sorted(found, key=lambda a: (a["name"], a["created_at"]), reverse=True)


def stamp_day(name: str) -> str:
    return name[len(DB_PREFIX):len(DB_PREFIX) + 8]


def stamp_of(name: str) -> str | None:
    """The UTC stamp of a database asset (single copy, part or manifest), else None."""
    if not name.startswith(DB_PREFIX):
        return None
    stamp = name[len(DB_PREFIX):len(DB_PREFIX) + 16]
    rest = name[len(DB_PREFIX) + 16:]
    if stamp_to_iso(name, DB_PREFIX) is None:
        return None
    if rest in DB_SUFFIXES or rest == MANIFEST_SUFFIX:
        return stamp
    for suffix in DB_SUFFIXES:
        if rest.startswith(suffix + PART_MARK) and rest[len(suffix + PART_MARK):].isdigit():
            return stamp
    return None


def manifest_name(stamp: str) -> str:
    return f"{DB_PREFIX}{stamp}{MANIFEST_SUFFIX}"


def part_name(base: str, index: int) -> str:
    return f"{base}{PART_MARK}{index:03d}"


def read_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("parts"), list) or not manifest["parts"]:
        raise RuntimeError(f"{path.name} is not a {MANIFEST_SCHEMA} manifest")
    stamp = stamp_of(path.name)
    for part in manifest["parts"]:
        if stamp_of(str(part.get("name"))) != stamp or "/" in str(part.get("name")) \
                or not isinstance(part.get("bytes"), int) or not isinstance(part.get("sha256"), str):
            raise RuntimeError(f"{path.name} names a part that does not belong to it: {part!r}"[:300])
    return manifest


def fetch_manifests(listing: list[dict], work: Path) -> tuple[dict[str, dict], set[str]]:
    """Every manifest in the release, read (each is a few hundred bytes).

    Returns (manifests, unreachable). A manifest that downloads but does not parse is left
    out of both (its set counts as incomplete); one that could not be downloaded is in
    ``unreachable``: its set is neither used nor deleted this time."""
    manifests: dict[str, dict] = {}
    unreachable: set[str] = set()
    work.mkdir(parents=True, exist_ok=True)
    for asset in listing:
        name = asset["name"]
        if not (name.endswith(MANIFEST_SUFFIX) and stamp_of(name)):
            continue
        try:
            gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", name, "--dir", str(work), capture=False)
        except RuntimeError as error:
            print(f"WARN  manifest {name} could not be downloaded ({str(error)[:200]})")
            unreachable.add(name)
            continue
        try:
            manifests[name] = read_manifest(work / name)
        except (RuntimeError, OSError, ValueError) as error:
            print(f"WARN  manifest {name} is damaged ({str(error)[:200]}); its set is not used")
        finally:
            (work / name).unlink(missing_ok=True)
    return manifests, unreachable


def database_copies(listing: list[dict], manifests: dict[str, dict], unreachable: set[str] | None = None) -> list[dict]:
    """Every saved database copy as one unit, newest first.

    A unit is a single-file copy, or a set (manifest plus parts). A set is complete when its
    manifest was read and every part it names is in the release with the named size. Parts
    with no manifest, or a manifest whose parts are not all there, form an incomplete unit:
    never restored, never counted as a copy, removed by the next save."""
    by_name = {a["name"]: a for a in listing}
    units: dict[str, dict] = {}
    for asset in listing:
        stamp = stamp_of(asset["name"])
        if stamp is None:
            continue
        if asset["name"].endswith(DB_SUFFIXES):
            units[asset["name"]] = {"key": asset["name"], "stamp": stamp, "kind": "single", "name": asset["name"],
                                    "assets": [asset], "complete": True, "created_at": asset.get("created_at", "")}
            continue
        unit = units.setdefault(manifest_name(stamp), {"key": manifest_name(stamp), "stamp": stamp, "kind": "set",
                                                       "name": manifest_name(stamp), "assets": [], "complete": False,
                                                       "created_at": ""})
        unit["assets"].append(asset)
        unit["created_at"] = max(unit["created_at"], asset.get("created_at", ""))
    for unit in units.values():
        if unit["kind"] != "set":
            continue
        manifest = manifests.get(unit["name"])
        unit["manifest"] = manifest
        unit["unreachable"] = unit["name"] in (unreachable or set())
        unit["complete"] = bool(manifest) and unit["name"] in by_name and all(
            part["name"] in by_name and by_name[part["name"]].get("size") == part["bytes"] for part in manifest["parts"])
    return sorted(units.values(), key=lambda u: (u["stamp"], u["created_at"], u["kind"] == "set"), reverse=True)


def to_delete(listing: list[dict], manifests: dict[str, dict] | None = None) -> list[dict]:
    """Assets of complete copies to remove: keep the newest KEEP_RECENT copies, plus the newest
    copy of each of the KEEP_DAYS most recent earlier UTC days; a set is kept or removed whole."""
    newest = [u for u in database_copies(listing, manifests or {}) if u["complete"]]
    recent = newest[:KEEP_RECENT]
    keep = {u["key"] for u in recent}
    covered = {u["stamp"][:8] for u in recent}
    for unit in newest[KEEP_RECENT:]:
        day = unit["stamp"][:8]
        if day in covered or len(covered - {u["stamp"][:8] for u in recent}) >= KEEP_DAYS:
            continue
        covered.add(day)
        keep.add(unit["key"])
    return [asset for unit in newest if unit["key"] not in keep for asset in unit["assets"]]


def incomplete_assets(listing: list[dict], manifests: dict[str, dict], current_stamp: str,
                      unreachable: set[str] | None = None) -> list[dict]:
    """Assets of incomplete sets other than the one being saved (an interrupted upload). A set
    whose manifest could not be downloaded this time is left alone."""
    return [asset for unit in database_copies(listing, manifests, unreachable)
            if not unit["complete"] and not unit.get("unreachable") and unit["stamp"] != current_stamp
            for asset in unit["assets"]]


def delete_assets(assets_to_go: list[dict], why: str) -> None:
    for asset in assets_to_go:
        gh("api", "-X", "DELETE", f"repos/{STATE_REPO}/releases/assets/{asset['id']}")
        print(f"removed {why} {asset['name']}")


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
        (work / STATIC).unlink(missing_ok=True)  # disk is tight on the runner: drop each archive once unpacked
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
        (work / SKYMAPS).unlink(missing_ok=True)
        print(f"restored {SKYMAPS} ({len(members)} sky maps)")
    manifests, unreachable = fetch_manifests(listing, work)
    copies = database_copies(listing, manifests, unreachable)
    newest = next((u for u in copies if u["complete"] or u.get("unreachable")), None)
    if newest is None:
        raise RuntimeError(f"no complete database copy ({DB_PREFIX}*{MANIFEST_SUFFIX} with its parts, or "
                           f"{DB_PREFIX}*{' or *'.join(DB_SUFFIXES)}) in {STATE_REPO} release {TAG}")
    if newest.get("unreachable"):
        # Never fall back past a newer copy that merely could not be read just now.
        raise RuntimeError(f"the manifest of the newest copy {newest['name']} could not be downloaded; try again")
    skipped = [u["name"] for u in copies if not u["complete"] and u["stamp"] > newest["stamp"]]
    if skipped:
        print(f"WARN  newer incomplete copies are not used (an interrupted save): {', '.join(skipped)}")
    target = data_dir / "soc.db"
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(target) + suffix).unlink(missing_ok=True)
    chosen = newest["name"]
    if newest["kind"] == "single":
        gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", chosen, "--dir", str(work), capture=False)
        decompress(work / chosen, target)
        (work / chosen).unlink(missing_ok=True)  # the compressed copy goes as soon as the database is out
    else:
        manifest = newest["manifest"]
        for part in manifest["parts"]:
            gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", part["name"], "--dir", str(work), capture=False)
        restore_set(work, manifest, target)
        print(f"checked {len(manifest['parts'])} part(s), the joined stream and the database against {chosen}")
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


class PartWriter:
    """A file-like sink that cuts what is written into parts of ``part_bytes`` in ``directory``,
    hashing each part and the whole stream as it goes (nothing is held in memory)."""

    def __init__(self, directory: Path, base: str, part_bytes: int = PART_BYTES):
        self.directory, self.base, self.part_bytes = directory, base, part_bytes
        self.parts: list[dict] = []
        self.whole = hashlib.sha256()
        self.total = 0
        self._handle = None
        self._digest = None
        self._size = 0

    def _open_next(self) -> None:
        self._close_current()
        name = part_name(self.base, len(self.parts))
        self._handle = open(self.directory / name, "wb")
        self._digest = hashlib.sha256()
        self._size = 0
        self.parts.append({"name": name})

    def _close_current(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self.parts[-1].update(bytes=self._size, sha256=self._digest.hexdigest())
            self._handle = None

    def write(self, data: bytes) -> int:
        view = memoryview(data)
        while view:
            if self._handle is None or self._size >= self.part_bytes:
                self._open_next()
            take = view[: self.part_bytes - self._size]
            self._handle.write(take)
            self._digest.update(take)
            self.whole.update(take)
            self._size += len(take)
            self.total += len(take)
            view = view[len(take):]
        return len(data)

    def flush(self) -> None:
        if self._handle is not None:
            self._handle.flush()

    def close(self) -> None:
        if not self.parts:
            self._open_next()   # an empty stream still has one (empty) part
        self._close_current()


class JoinedReader:
    """Reads the parts one after the other as one stream, hashing what it hands out."""

    def __init__(self, paths: list[Path]):
        self.paths = list(paths)
        self.digest = hashlib.sha256()
        self._handle = None

    def read(self, size: int = -1) -> bytes:
        size = BLOCK if size is None or size < 0 else size
        while True:
            if self._handle is None:
                if not self.paths:
                    return b""
                self._handle = open(self.paths.pop(0), "rb")
            data = self._handle.read(size)
            if data:
                self.digest.update(data)
                return data
            self._handle.close()
            self._handle = None

    def close(self) -> None:
        if self._handle is not None:
            self._handle.close()
            self._handle = None


def compress_into_parts(database: Path, directory: Path, base: str, part_bytes: int = PART_BYTES) -> PartWriter:
    """Compress the database straight into parts (zstd for a .db.zst base, gzip otherwise)."""
    writer = PartWriter(directory, base, part_bytes)
    if base.endswith(ZSTD_SUFFIX):
        with tempfile.TemporaryFile() as errors:
            process = subprocess.Popen(["zstd", "-q", "-c", "-T0", *ZSTD_ARGS, str(database)],
                                       stdout=subprocess.PIPE, stderr=errors)
            assert process.stdout is not None
            for block in iter(lambda: process.stdout.read(BLOCK), b""):
                writer.write(block)
            process.stdout.close()
            if process.wait() != 0:
                errors.seek(0)
                writer.close()
                raise RuntimeError(f"zstd failed: {errors.read().decode(errors='replace').strip()[:300]}")
    else:
        with open(database, "rb") as source, gzip.GzipFile(fileobj=writer, mode="wb", compresslevel=6) as sink:
            shutil.copyfileobj(source, sink, BLOCK)
    writer.close()
    return writer


def stream_parts(paths: list[Path], base: str, sink) -> str:
    """Decompress the joined parts, handing each decompressed block to ``sink``; returns the
    sha256 of the joined compressed stream. Streamed: parts are read block by block."""
    reader = JoinedReader(paths)
    try:
        if base.endswith(ZSTD_SUFFIX):
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(["zstd", "-q", "-d", "-c", "--long=27"],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors)
                assert process.stdin is not None and process.stdout is not None
                failure: list[BaseException] = []

                def feed() -> None:
                    try:
                        for block in iter(lambda: reader.read(BLOCK), b""):
                            process.stdin.write(block)
                    except BaseException as error:  # a broken pipe when zstd stops early
                        failure.append(error)
                    finally:
                        try:
                            process.stdin.close()
                        except OSError:
                            pass

                feeder = threading.Thread(target=feed, daemon=True)
                feeder.start()
                for block in iter(lambda: process.stdout.read(BLOCK), b""):
                    sink(block)
                process.stdout.close()
                feeder.join()
                if process.wait() != 0 or failure:
                    errors.seek(0)
                    raise RuntimeError(f"the parts do not decompress: {errors.read().decode(errors='replace').strip()[:300]}")
        else:
            with gzip.GzipFile(fileobj=reader, mode="rb") as source:
                for block in iter(lambda: source.read(BLOCK), b""):
                    sink(block)
    except (OSError, EOFError, zlib.error) as error:
        raise RuntimeError(f"the parts do not decompress: {error}") from None
    finally:
        reader.close()
    return reader.digest.hexdigest()


def check_parts(directory: Path, manifest: dict) -> list[Path]:
    """Every part beside the manifest has its size and sha256 (streamed, one part at a time)."""
    paths = []
    for part in manifest["parts"]:
        path = directory / part["name"]
        if not path.is_file():
            raise RuntimeError(f"part {part['name']} is missing")
        if path.stat().st_size != part["bytes"] or sha256_file(path) != part["sha256"]:
            raise RuntimeError(f"part {part['name']} does not match the manifest (size or sha256)")
        paths.append(path)
    return paths


def restore_set(directory: Path, manifest: dict, target: Path) -> None:
    """Join and decompress a checked set into ``target``, checking the joined stream's sha256 and
    the database's sha256 against the manifest. ``target`` is removed if any check fails."""
    paths = check_parts(directory, manifest)
    digest = hashlib.sha256()
    size = 0
    try:
        with open(target, "wb") as out:
            def sink(block: bytes) -> None:
                nonlocal size
                out.write(block)
                digest.update(block)
                size += len(block)
            stream_sha = stream_parts(paths, manifest["base"], sink)
        if stream_sha != manifest["sha256"]:
            raise RuntimeError("the joined parts do not match the manifest's sha256")
        if digest.hexdigest() != manifest["database_sha256"] or size != manifest["database_bytes"]:
            raise RuntimeError("the restored database does not match the manifest's database sha256")
    except BaseException:
        target.unlink(missing_ok=True)
        raise


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


def free_disk(path: Path) -> int:
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return -1


def save(data_dir: Path) -> None:
    database = data_dir / "soc.db"
    if not database.is_file():
        raise RuntimeError(f"{database} is missing")
    suffix = ZSTD_SUFFIX if compressor() == "zstd" else GZIP_SUFFIX
    started = time.monotonic()
    before = assets()
    taken = {stamp_of(a["name"]) for a in before}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    while stamp in taken:   # never reuse a stamp the release already holds (assets are never replaced)
        time.sleep(1)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{DB_PREFIX}{stamp}{suffix}"
    name = manifest_name(stamp)
    con = sqlite3.connect(database, timeout=120)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        verdict = con.execute("PRAGMA quick_check").fetchone()[0]
    finally:
        con.close()
    if verdict != "ok":
        raise RuntimeError(f"database check failed ({verdict}); the previous state is kept")
    raw = database.stat().st_size
    if size_verdict(0, raw) == "refuse":
        write_size_report(raw, 0, "refuse", 0, free_disk(data_dir))
        raise StateTooLarge(f"STATE TOO LARGE: the database is {raw / GIB:.2f} GiB raw, over the "
                            f"{REFUSE_RAW_BYTES / GIB:.0f} GiB line (a cycle needs about three times that on the "
                            "runner's disk); it was NOT uploaded and the previous copy stays the current state. "
                            "Trim it: scripts/ctas_db_retention.py, scripts/ctas_envelope_archive.py.")
    staging = data_dir.parent / f".state-parts-{stamp}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir()
    try:
        writer = compress_into_parts(database, staging, base)
        compressed = writer.total
        verdict = size_verdict(compressed, raw)
        write_size_report(raw, compressed, verdict, len(writer.parts), free_disk(data_dir))
        sizes = f"{compressed / GIB:.2f} GiB compressed in {len(writer.parts)} part(s), {raw / GIB:.2f} GiB raw"
        if verdict == "growing":
            print(f"WARN  state growing: the database is {sizes}, over the {WARN_BYTES / GIB:.1f} GiB early-signal "
                  f"line (it is saved in parts, so GitHub's 2 GiB file limit does not apply; uploads are refused "
                  f"above {REFUSE_RAW_BYTES / GIB:.0f} GiB raw)")
        # Before uploading: the joined parts must decompress to exactly the checked database.
        source_sha = sha256_file(database)
        paths = [staging / part["name"] for part in writer.parts]
        check = hashlib.sha256()
        joined_sha = stream_parts(paths, base, check.update)
        if joined_sha != writer.whole.hexdigest() or check.hexdigest() != source_sha:
            raise RuntimeError(f"{name}: the parts do not decompress to the database; nothing uploaded, the previous state is kept")
        manifest = {"schema": MANIFEST_SCHEMA, "base": base, "compression": "zstd" if suffix == ZSTD_SUFFIX else "gzip",
                    "part_bytes": PART_BYTES, "parts": writer.parts, "bytes": compressed, "sha256": joined_sha,
                    "database_bytes": raw, "database_sha256": source_sha,
                    "saved_at": stamp_to_iso(name, DB_PREFIX)}
        (staging / name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifests, unreachable = fetch_manifests(before, staging / ".manifests")
        first_set = not any(u["complete"] and u["kind"] == "set" for u in database_copies(before, manifests, unreachable))
        # Parts first, the manifest last: until the manifest is there the set is never current.
        try:
            for part in writer.parts:
                gh("release", "upload", TAG, str(staging / part["name"]), "--repo", STATE_REPO, capture=False)
                (staging / part["name"]).unlink()
            gh("release", "upload", TAG, str(staging / name), "--repo", STATE_REPO, capture=False)
        except RuntimeError:
            remove_set(stamp, "part of the failed upload")
            raise
        listing = assets()
        by_name = {a["name"]: a for a in listing}
        missing = [p["name"] for p in writer.parts if by_name.get(p["name"], {}).get("size") != p["bytes"]]
        if name not in by_name or missing:
            remove_set(stamp, "part of the incomplete upload")
            raise RuntimeError(f"{name}: {', '.join(missing) or 'the manifest'} did not arrive with the right size; "
                               "the set was removed and the previous state is kept")
        if first_set:
            # The first set in the release is downloaded again and checked part by part
            # before anything older is deleted.
            check_dir = data_dir.parent / ".state-roundtrip"
            shutil.rmtree(check_dir, ignore_errors=True)
            check_dir.mkdir()
            try:
                for part in writer.parts:
                    gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", part["name"], "--dir", str(check_dir), capture=False)
                fetched = check_dir / ".restored.db"
                restore_set(check_dir, manifest, fetched)
                fetched.unlink()
            except (RuntimeError, OSError) as error:
                remove_set(stamp, "part of the set that failed its round trip")
                raise RuntimeError(f"round trip of {name} failed ({str(error)[:200]}); it was removed and the previous state is kept")
            finally:
                shutil.rmtree(check_dir, ignore_errors=True)
            print(f"round trip verified: {name} downloads, joins and decompresses to the saved database")
        manifests[name] = manifest
        delete_assets(to_delete(listing, manifests), "older copy")
        delete_assets(incomplete_assets(listing, manifests, stamp, unreachable), "leftover of an interrupted save")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    save_skymaps(data_dir, database)
    print(f"saved {name} ({sizes}) in {time.monotonic() - started:.0f}s")


def remove_set(stamp: str, why: str) -> None:
    """Delete every asset of the set with this stamp (best effort; the next save also cleans up)."""
    try:
        listing = assets()
    except RuntimeError:
        return
    for asset in listing:
        if stamp_of(asset["name"]) == stamp and not asset["name"].endswith(DB_SUFFIXES):
            try:
                delete_assets([asset], why)
            except RuntimeError:
                pass


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

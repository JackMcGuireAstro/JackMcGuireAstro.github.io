#!/usr/bin/env python3
"""Keep WorldsIndex's working data in a private GitHub repository between cloud runs.

WorldsIndex's builder (the ExoNexus project) runs in GitHub Actions
(.github/workflows/worldsindex-cloud.yml), so nothing lives on Jack's Mac. Its code is
in the private repository (default JackMcGuireAstro/worldsindex-state); the data it
changes between runs (frozen provider snapshots, observations, public data products and
monitor receipts) is kept as release assets of the release tagged ``state``:

    wi-state-<UTC stamp>.tar.gz    the newest is the current one

    worldsindex_cloud_state.py restore <source-dir>
        Unpack the newest state archive into <source-dir>.
    worldsindex_cloud_state.py save <source-dir>
        Pack STATE_PATHS from <source-dir>, check the archive reads back and holds
        frozen snapshots, upload it under a new name, then delete older copies except
        the newest KEEP_RECENT and the newest of each of the previous KEEP_DAYS days.
        An archive that fails the check is never uploaded.

GH_TOKEN must allow reading and writing the state repository's contents. When
STATE_RECEIPT names a file, ``restore`` writes a small JSON receipt there (which
archive was restored and when it was saved) for the run's public marker.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

STATE_REPO = os.environ.get("WORLDSINDEX_STATE_REPO", "JackMcGuireAstro/worldsindex-state")
TAG = os.environ.get("WORLDSINDEX_STATE_TAG", "state")
PREFIX, SUFFIX = "wi-state-", ".tar.gz"
STATE_PATHS = ("data/snapshots", "data/observations", "data/atlas", "public/data", "outputs")
# GitHub refuses release assets of 2 GiB or more (HTTP 422 "size must be less than 2147483648").
ASSET_LIMIT_BYTES = int(os.environ.get("WORLDSINDEX_STATE_ASSET_LIMIT_BYTES", 2 * 1024 ** 3))
ASSET_WARN_BYTES = int(os.environ.get("WORLDSINDEX_STATE_ASSET_WARN_BYTES", int(1.5 * 1024 ** 3)))
SKIP_NAMES = {"scheduler.lock", ".DS_Store"}
KEEP_RECENT = int(os.environ.get("WORLDSINDEX_STATE_KEEP_RECENT", "3"))
KEEP_DAYS = int(os.environ.get("WORLDSINDEX_STATE_KEEP_DAYS", "2"))


def gh(*args: str, capture: bool = True) -> str:
    result = subprocess.run(["gh", *args], check=False, text=True,
                            stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result.stdout if capture else ""


def assets() -> list[dict]:
    release = json.loads(gh("api", f"repos/{STATE_REPO}/releases/tags/{TAG}"))
    return [{"id": a["id"], "name": a["name"], "created_at": a.get("created_at", "")} for a in release.get("assets", [])]


def state_assets(listing: list[dict]) -> list[dict]:
    found = [a for a in listing if a["name"].startswith(PREFIX) and a["name"].endswith(SUFFIX)]
    return sorted(found, key=lambda a: (a["name"], a["created_at"]), reverse=True)


def day(name: str) -> str:
    return name[len(PREFIX):len(PREFIX) + 8]


def to_delete(listing: list[dict]) -> list[dict]:
    newest = state_assets(listing)
    recent = newest[:KEEP_RECENT]
    keep = {a["id"] for a in recent}
    recent_days = {day(a["name"]) for a in recent}
    extra_days: set[str] = set()
    for asset in newest[KEEP_RECENT:]:
        d = day(asset["name"])
        if d in recent_days or d in extra_days or len(extra_days) >= KEEP_DAYS:
            continue
        extra_days.add(d)
        keep.add(asset["id"])
    return [a for a in newest if a["id"] not in keep]


def safe_extract(archive: tarfile.TarFile, target: Path) -> None:
    root = target.resolve()
    for member in archive.getmembers():
        dest = (target / member.name).resolve()
        if dest != root and not str(dest).startswith(str(root) + os.sep):
            raise RuntimeError(f"refusing archive member outside the source folder: {member.name}")
        if member.issym() or member.islnk():
            raise RuntimeError(f"refusing link in the state archive: {member.name}")
    if hasattr(tarfile, "data_filter"):
        archive.extractall(target, filter="data")
    else:
        archive.extractall(target)


def stamp_to_iso(name: str) -> str | None:
    """The UTC stamp in an archive name (``wi-state-20261016T061500Z.tar.gz``) as ISO-8601."""
    try:
        return datetime.strptime(name[len(PREFIX):len(PREFIX) + 16], "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def write_receipt(asset: str) -> None:
    """Record which archive was restored, when it was saved, and when (STATE_RECEIPT)."""
    path = os.environ.get("STATE_RECEIPT")
    if not path:
        return
    receipt = {"asset": asset, "saved_at": stamp_to_iso(asset),
               "restored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    Path(path).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def restore(source: Path) -> None:
    states = state_assets(assets())
    if not states:
        raise RuntimeError(f"no state archive ({PREFIX}*{SUFFIX}) in {STATE_REPO} release {TAG}")
    chosen = states[0]["name"]
    work = source / ".state-download"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", chosen, "--dir", str(work), capture=False)
    for path in STATE_PATHS:
        shutil.rmtree(source / path, ignore_errors=True)
    with tarfile.open(work / chosen) as archive:
        safe_extract(archive, source)
    shutil.rmtree(work, ignore_errors=True)
    write_receipt(chosen)
    print(f"restored {chosen}")


def pack(source: Path, destination: Path) -> int:
    count = 0
    with tarfile.open(destination, "w:gz", compresslevel=6) as archive:
        for path in STATE_PATHS:
            top = source / path
            if not top.exists():
                continue
            for item in sorted(top.rglob("*")):
                if any(part in SKIP_NAMES for part in item.relative_to(source).parts) or item.is_symlink():
                    continue
                if item.is_file():
                    archive.add(item, arcname=str(item.relative_to(source)), recursive=False)
                    count += 1
    return count


def check(archive_path: Path) -> None:
    with tarfile.open(archive_path) as archive:
        names = archive.getnames()
    if not any(n.startswith("data/snapshots/") for n in names):
        raise RuntimeError("the state archive holds no frozen snapshots; the previous state is kept")


def save(source: Path) -> None:
    name = f"{PREFIX}{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}{SUFFIX}"
    staged = source.parent / name
    started = time.monotonic()
    files = pack(source, staged)
    check(staged)
    size = staged.stat().st_size
    if size >= ASSET_LIMIT_BYTES:
        staged.unlink()
        raise RuntimeError(
            f"the state archive is {size / 1e6:.0f} MB, over GitHub's {ASSET_LIMIT_BYTES / 1e6:.0f} MB release-asset limit; "
            "nothing uploaded. The compaction summary line above names the largest areas; tighten retention there"
        )
    if size >= ASSET_WARN_BYTES:
        print(f"WARN  the state archive is {size / 1e6:.0f} MB; GitHub refuses assets from {ASSET_LIMIT_BYTES / 1e6:.0f} MB", file=sys.stderr)
    gh("release", "upload", TAG, str(staged), "--repo", STATE_REPO, capture=False)
    staged.unlink()
    listing = assets()
    if name not in {a["name"] for a in listing}:
        raise RuntimeError(f"{name} did not appear in the release after upload")
    for asset in to_delete(listing):
        gh("api", "-X", "DELETE", f"repos/{STATE_REPO}/releases/assets/{asset['id']}")
        print(f"removed older copy {asset['name']}")
    print(f"saved {name} ({files} files, {size / 1e6:.0f} MB) in {time.monotonic() - started:.0f}s")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"restore", "save"}:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        (restore if argv[1] == "restore" else save)(Path(argv[2]))
    except (RuntimeError, OSError, tarfile.TarError) as error:
        print(f"worldsindex_cloud_state {argv[1]}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

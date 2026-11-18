#!/usr/bin/env python3
"""Keep WorldsIndex's working data in a private GitHub repository between cloud runs.

WorldsIndex's builder (the ExoNexus project) runs in GitHub Actions
(.github/workflows/worldsindex-cloud.yml), so nothing lives on Jack's Mac. Its code is
in the private repository (default JackMcGuireAstro/worldsindex-state); the data it
changes between runs (frozen provider snapshots, observations, the atlas and its release
migrations, public data products and monitor receipts) is kept as release assets of the
release tagged ``state``.

Since 2026-11-18 a copy is a SET: the tar of STATE_PATHS, compressed with zstd (gzip when
zstd is missing) straight into parts under GitHub's 2 GiB per-asset limit, plus a manifest:

    wi-state-<UTC stamp>.parts.json             manifest (part names, sizes, sha256s, the
                                                joined stream's and the tar's sha256)
    wi-state-<UTC stamp>.tar.zst.part-000 ...   parts of PART_BYTES (default 1.5 GiB)

Until then a copy was one ``wi-state-<UTC stamp>.tar.gz``; GitHub refuses an asset of
2 GiB or more, and on 2026-11-18 the state outgrew that (every save failed). Old single
copies still restore.

    worldsindex_cloud_state.py restore <source-dir>
        Restore the newest complete copy into <source-dir>: a set only when its manifest and
        every part it names are in the release with the named sizes; each part, the joined
        stream and the tar are checked against the manifest before anything is unpacked.
        A newer incomplete set (an interrupted save) is reported and skipped; if the newest
        set's manifest cannot be downloaded, restore stops rather than fall back past it.
    worldsindex_cloud_state.py save <source-dir>
        Tar STATE_PATHS (directories included, so their times survive a restore), check the
        tar holds frozen snapshots, compress it into parts, check the joined parts decompress
        to exactly the tar, upload the parts and then the manifest, check every asset's size,
        and only then delete older copies (a set is kept or deleted whole: the newest
        KEEP_RECENT, plus the newest of each of the previous KEEP_DAYS days) and leftovers of
        interrupted saves. The first set in the release is downloaded again and checked part
        by part before anything older is deleted. Refused (exit 4, nothing uploaded) only when
        the tar exceeds REFUSE_RAW_GIB (default 20 GiB): a cycle needs several times that on
        the runner's disk. Above WARN_GIB compressed (default 1.5 GiB) it saves and warns.

GH_TOKEN must allow reading and writing the state repository's contents. When
STATE_RECEIPT names a file, ``restore`` writes a small JSON receipt there (which copy was
restored and when it was saved) for the run's public marker. Errors go to stderr with the
reason; the cloud runner copies them into runner.log.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import state_parts as parts  # noqa: E402

STATE_REPO = os.environ.get("WORLDSINDEX_STATE_REPO", "JackMcGuireAstro/worldsindex-state")
TAG = os.environ.get("WORLDSINDEX_STATE_TAG", "state")
PREFIX = "wi-state-"
LEGACY_SUFFIX = ".tar.gz"
PART_SUFFIXES = {"zstd": ".tar.zst", "gzip": ".tar.gz"}
PART_MARK = ".part-"
MANIFEST_SUFFIX = ".parts.json"
MANIFEST_SCHEMA = "worldsindex.state-parts@1"
STATE_PATHS = ("data/snapshots", "data/observations", "data/atlas", "public/data", "outputs")
SKIP_NAMES = {"scheduler.lock", ".DS_Store"}
GIB = parts.GIB
PART_BYTES = max(1, int(float(os.environ.get("WORLDSINDEX_STATE_PART_GIB", "1.5")) * GIB))
WARN_BYTES = int(float(os.environ.get("WORLDSINDEX_STATE_WARN_GIB", "1.5")) * GIB)
REFUSE_RAW_BYTES = int(float(os.environ.get("WORLDSINDEX_STATE_REFUSE_RAW_GIB", "20")) * GIB)
KEEP_RECENT = int(os.environ.get("WORLDSINDEX_STATE_KEEP_RECENT", "3"))
KEEP_DAYS = int(os.environ.get("WORLDSINDEX_STATE_KEEP_DAYS", "2"))
TOO_LARGE_EXIT = 4


class StateTooLarge(RuntimeError):
    pass


def gh(*args: str, capture: bool = True) -> str:
    result = subprocess.run(["gh", *args], check=False, text=True,
                            stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:2])} failed: {result.stderr.strip()[:300]}")
    return result.stdout if capture else ""


def assets() -> list[dict]:
    release = json.loads(gh("api", f"repos/{STATE_REPO}/releases/tags/{TAG}"))
    return [{"id": a["id"], "name": a["name"], "size": a.get("size", 0), "created_at": a.get("created_at", "")}
            for a in release.get("assets", [])]


def stamp_to_iso(name: str) -> str | None:
    """The UTC stamp in an asset name (``wi-state-20261016T061500Z.tar.gz``) as ISO-8601."""
    try:
        return datetime.strptime(name[len(PREFIX):len(PREFIX) + 16], "%Y%m%dT%H%M%SZ").strftime("%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def stamp_of(name: str) -> str | None:
    """The stamp of a state asset (legacy single copy, part or manifest), else None."""
    if not name.startswith(PREFIX) or stamp_to_iso(name) is None:
        return None
    stamp, rest = name[len(PREFIX):len(PREFIX) + 16], name[len(PREFIX) + 16:]
    if rest in (LEGACY_SUFFIX, MANIFEST_SUFFIX):
        return stamp
    for suffix in PART_SUFFIXES.values():
        if rest.startswith(suffix + PART_MARK) and rest[len(suffix + PART_MARK):].isdigit():
            return stamp
    return None


def manifest_name(stamp: str) -> str:
    return f"{PREFIX}{stamp}{MANIFEST_SUFFIX}"


def is_legacy(name: str) -> bool:
    return stamp_of(name) is not None and name.endswith(LEGACY_SUFFIX) and PART_MARK not in name


def read_manifest(path: Path) -> dict:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != MANIFEST_SCHEMA or not isinstance(manifest.get("parts"), list) or not manifest["parts"] \
            or manifest.get("compression") not in PART_SUFFIXES:
        raise RuntimeError(f"{path.name} is not a {MANIFEST_SCHEMA} manifest")
    stamp = stamp_of(path.name)
    for part in manifest["parts"]:
        if stamp_of(str(part.get("name"))) != stamp or "/" in str(part.get("name")) or is_legacy(str(part.get("name"))) \
                or not isinstance(part.get("bytes"), int) or not isinstance(part.get("sha256"), str):
            raise RuntimeError(f"{path.name} names a part that does not belong to it: {part!r}"[:300])
    return manifest


def fetch_manifests(listing: list[dict], work: Path) -> tuple[dict[str, dict], set[str]]:
    """Every manifest in the release, read. A manifest that does not parse counts as missing
    (its set is incomplete); one that could not be downloaded is ``unreachable``: its set is
    neither used nor deleted this time."""
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


def state_copies(listing: list[dict], manifests: dict[str, dict], unreachable: set[str] | None = None) -> list[dict]:
    """Every saved copy as one unit, newest first: a legacy single file, or a set (manifest plus
    parts), complete when its manifest was read and every part it names is in the release with
    the named size. Parts without a readable manifest form an incomplete unit."""
    by_name = {a["name"]: a for a in listing}
    units: dict[str, dict] = {}
    for asset in listing:
        stamp = stamp_of(asset["name"])
        if stamp is None:
            continue
        if is_legacy(asset["name"]):
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
    newest = [u for u in state_copies(listing, manifests or {}) if u["complete"]]
    recent = newest[:KEEP_RECENT]
    keep = {u["key"] for u in recent}
    recent_days = {u["stamp"][:8] for u in recent}
    extra_days: set[str] = set()
    for unit in newest[KEEP_RECENT:]:
        day = unit["stamp"][:8]
        if day in recent_days or day in extra_days or len(extra_days) >= KEEP_DAYS:
            continue
        extra_days.add(day)
        keep.add(unit["key"])
    return [asset for unit in newest if unit["key"] not in keep for asset in unit["assets"]]


def incomplete_assets(listing: list[dict], manifests: dict[str, dict], current_stamp: str,
                      unreachable: set[str] | None = None) -> list[dict]:
    """Assets of incomplete sets other than the one being saved (an interrupted upload)."""
    return [asset for unit in state_copies(listing, manifests, unreachable)
            if not unit["complete"] and not unit.get("unreachable") and unit["stamp"] != current_stamp
            for asset in unit["assets"]]


def delete_assets(assets_to_go: list[dict], why: str) -> None:
    for asset in assets_to_go:
        gh("api", "-X", "DELETE", f"repos/{STATE_REPO}/releases/assets/{asset['id']}")
        print(f"removed {why} {asset['name']}")


def remove_set(stamp: str, why: str) -> None:
    """Delete every asset of the set with this stamp (best effort; the next save also cleans up)."""
    try:
        listing = assets()
    except RuntimeError:
        return
    for asset in listing:
        if stamp_of(asset["name"]) == stamp and not is_legacy(asset["name"]):
            try:
                delete_assets([asset], why)
            except RuntimeError:
                pass


def safe_extract(archive: tarfile.TarFile, target: Path) -> None:
    root = target.resolve()
    for member in archive.getmembers():
        dest = (target / member.name).resolve()
        if dest != root and not str(dest).startswith(str(root) + os.sep):
            raise RuntimeError(f"refusing archive member outside the source folder: {member.name}")
        if member.issym():
            raise RuntimeError(f"refusing symbolic link in the state archive: {member.name}")
        if member.islnk():
            # The builder's compaction hard-links identical probe responses across runs, and
            # tar records every later copy as a hard link to the first. That is allowed only
            # when the link stays inside the archive's own tree; anything else is refused.
            link_target = (target / member.linkname).resolve()
            if os.path.isabs(member.linkname) or not str(link_target).startswith(str(root) + os.sep):
                raise RuntimeError(f"refusing hard link outside the state archive: {member.name} -> {member.linkname}")
    if hasattr(tarfile, "data_filter"):
        archive.extractall(target, filter="data")
    else:
        archive.extractall(target)


def write_receipt(asset: str) -> None:
    """Record which copy was restored, when it was saved, and when (STATE_RECEIPT)."""
    path = os.environ.get("STATE_RECEIPT")
    if not path:
        return
    receipt = {"asset": asset, "saved_at": stamp_to_iso(asset),
               "restored_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    Path(path).write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def unpack(tar_path: Path, source: Path) -> None:
    for path in STATE_PATHS:
        shutil.rmtree(source / path, ignore_errors=True)
    with tarfile.open(tar_path) as archive:
        safe_extract(archive, source)


def restore(source: Path) -> None:
    source.mkdir(parents=True, exist_ok=True)
    listing = assets()
    work = source / ".state-download"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    try:
        manifests, unreachable = fetch_manifests(listing, work / ".manifests")
        copies = state_copies(listing, manifests, unreachable)
        newest = next((u for u in copies if u["complete"] or u.get("unreachable")), None)
        if newest is None:
            raise RuntimeError(f"no complete state copy ({PREFIX}*{MANIFEST_SUFFIX} with its parts, or "
                               f"{PREFIX}*{LEGACY_SUFFIX}) in {STATE_REPO} release {TAG}")
        if newest.get("unreachable"):
            raise RuntimeError(f"the manifest of the newest copy {newest['name']} could not be downloaded; try again")
        skipped = [u["name"] for u in copies if not u["complete"] and u["stamp"] > newest["stamp"]]
        if skipped:
            print(f"WARN  newer incomplete copies are not used (an interrupted save): {', '.join(skipped)}")
        chosen = newest["name"]
        if newest["kind"] == "single":
            gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", chosen, "--dir", str(work), capture=False)
            unpack(work / chosen, source)
        else:
            manifest = newest["manifest"]
            for part in manifest["parts"]:
                gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", part["name"], "--dir", str(work), capture=False)
            tar_path = work / "state.tar"
            parts.join_into(work, manifest, tar_path)
            for part in manifest["parts"]:
                (work / part["name"]).unlink(missing_ok=True)
            print(f"checked {len(manifest['parts'])} part(s), the joined stream and the tar against {chosen}")
            unpack(tar_path, source)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    write_receipt(chosen)
    print(f"restored {chosen}")


def pack(source: Path, destination: Path) -> tuple[int, bool]:
    """Tar STATE_PATHS (uncompressed) into ``destination``; returns (files, holds snapshots).
    Directories are recorded too, so their modification times survive a restore."""
    count = 0
    snapshots = False
    with tarfile.open(destination, "w", format=tarfile.PAX_FORMAT) as archive:
        for path in STATE_PATHS:
            top = source / path
            if not top.exists():
                continue
            for item in [top, *sorted(top.rglob("*"))]:
                relative = item.relative_to(source)
                if any(part in SKIP_NAMES for part in relative.parts) or item.is_symlink():
                    continue
                if item.is_dir():
                    archive.add(item, arcname=str(relative), recursive=False)
                elif item.is_file():
                    archive.add(item, arcname=str(relative), recursive=False)
                    count += 1
                    snapshots = snapshots or str(relative).startswith("data/snapshots/")
    return count, snapshots


def compression() -> str:
    forced = os.environ.get("WORLDSINDEX_STATE_COMPRESSOR", "").strip().lower()
    if forced in PART_SUFFIXES:
        return forced
    return "zstd" if shutil.which("zstd") else "gzip"


def save(source: Path) -> None:
    started = time.monotonic()
    kind = compression()
    before = assets()
    taken = {stamp_of(a["name"]) for a in before}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    while stamp in taken:   # assets are never replaced: never reuse a stamp the release holds
        time.sleep(1)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = f"{PREFIX}{stamp}{PART_SUFFIXES[kind]}"
    name = manifest_name(stamp)
    staging = source.parent / f".state-parts-{stamp}"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        tar_path = staging / "state.tar"
        files, has_snapshots = pack(source, tar_path)
        if not has_snapshots:
            raise RuntimeError("the state holds no frozen snapshots (data/snapshots); nothing uploaded, the previous state is kept")
        raw = tar_path.stat().st_size
        if raw > REFUSE_RAW_BYTES:
            raise StateTooLarge(f"STATE TOO LARGE: the working data is {raw / GIB:.2f} GiB uncompressed, over the "
                                f"{REFUSE_RAW_BYTES / GIB:.0f} GiB line (a cycle needs several times that on the runner's disk); "
                                "nothing uploaded and the previous copy stays current. The compaction line in runner.log "
                                "names the largest areas (builder scripts/compact-state.ts).")
        tar_sha = parts.sha256_file(tar_path)
        writer = parts.compress_into_parts(tar_path, staging, base, kind, PART_BYTES)
        compressed = writer.total
        sizes = f"{compressed / GIB:.2f} GiB compressed in {len(writer.parts)} part(s), {raw / GIB:.2f} GiB uncompressed, {files} files"
        if compressed > WARN_BYTES:
            print(f"WARN  state growing: {sizes}, over the {WARN_BYTES / GIB:.1f} GiB early-signal line (saved in parts, "
                  f"so GitHub's 2 GiB file limit does not apply; refused above {REFUSE_RAW_BYTES / GIB:.0f} GiB uncompressed)")
        # Before uploading: the joined parts must decompress to exactly the tar.
        check = hashlib.sha256()
        paths = [staging / part["name"] for part in writer.parts]
        joined_sha = parts.stream_parts(paths, kind, check.update)
        if joined_sha != writer.whole.hexdigest() or check.hexdigest() != tar_sha:
            raise RuntimeError(f"{name}: the parts do not decompress to the packed state; nothing uploaded, the previous state is kept")
        tar_path.unlink()
        manifest = {"schema": MANIFEST_SCHEMA, "base": base, "compression": kind, "part_bytes": PART_BYTES,
                    "parts": writer.parts, "bytes": compressed, "sha256": joined_sha,
                    "payload": "tar", "payload_bytes": raw, "payload_sha256": tar_sha, "files": files,
                    "saved_at": stamp_to_iso(name)}
        (staging / name).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        manifests, unreachable = fetch_manifests(before, staging / ".manifests")
        first_set = not any(u["complete"] and u["kind"] == "set" for u in state_copies(before, manifests, unreachable))
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
            check_dir = source.parent / f".state-roundtrip-{stamp}"
            shutil.rmtree(check_dir, ignore_errors=True)
            check_dir.mkdir()
            try:
                for part in writer.parts:
                    gh("release", "download", TAG, "--repo", STATE_REPO, "--pattern", part["name"], "--dir", str(check_dir), capture=False)
                fetched = check_dir / ".restored.tar"
                parts.join_into(check_dir, manifest, fetched)
                with tarfile.open(fetched) as archive:
                    names = archive.getnames()
                if not any(n.startswith("data/snapshots/") for n in names):
                    raise RuntimeError("the downloaded set holds no frozen snapshots")
            except (RuntimeError, OSError, tarfile.TarError) as error:
                remove_set(stamp, "part of the set that failed its round trip")
                raise RuntimeError(f"round trip of {name} failed ({str(error)[:200]}); it was removed and the previous state is kept")
            finally:
                shutil.rmtree(check_dir, ignore_errors=True)
            print(f"round trip verified: {name} downloads, joins and decompresses to the saved state")
        manifests[name] = manifest
        delete_assets(to_delete(listing, manifests), "older copy")
        delete_assets(incomplete_assets(listing, manifests, stamp, unreachable), "leftover of an interrupted save")
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    print(f"saved {name} ({sizes}) in {time.monotonic() - started:.0f}s")


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[1] not in {"restore", "save"}:
        print(__doc__, file=sys.stderr)
        return 2
    try:
        (restore if argv[1] == "restore" else save)(Path(argv[2]))
    except StateTooLarge as error:
        print(str(error), file=sys.stderr)
        return TOO_LARGE_EXIT
    except (RuntimeError, OSError, tarfile.TarError, ValueError) as error:
        print(f"worldsindex_cloud_state {argv[1]}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

#!/usr/bin/env python3
"""Tests for scripts/worldsindex_cloud_state.py against a stand-in `gh` (no network)."""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "worldsindex_cloud_state.py"
sys.path.insert(0, str(ROOT / "scripts"))
import test_ctas_cloud_state as ctas_tests  # noqa: E402  (shares the stand-in gh)
import worldsindex_cloud_state as state  # noqa: E402


class WorldsIndexStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.release = self.tmp / "release"
        self.release.mkdir()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(ctas_tests.FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_RELEASE_DIR": str(self.release)}
        self.source = self.tmp / "work" / "source"
        self.source.mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, **env):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env={**self.env, **env}, text=True, capture_output=True)

    def save(self, **env):
        result = self.cli("save", str(self.source), **env)
        time.sleep(1.1)   # one copy per second: the stamp is the asset name
        return result

    def restore_into(self, name: str, **env):
        target = self.tmp / "work" / name
        target.mkdir(parents=True, exist_ok=True)
        return target, self.cli("restore", str(target), **env)

    def names(self):
        return sorted(p.name for p in self.release.iterdir())

    def write_state(self, root: Path, marker: str, noise: int = 0):
        (root / "data/snapshots/nasa").mkdir(parents=True, exist_ok=True)
        (root / "data/snapshots/nasa/manifest.json").write_text(marker)
        (root / "public/data").mkdir(parents=True, exist_ok=True)
        (root / "public/data/sky.json").write_text("{}")
        (root / "outputs/sync/scheduler.lock").mkdir(parents=True, exist_ok=True)
        (root / "outputs/sync/scheduler.lock/pid").write_text("1")
        (root / "outputs/sync/latest-attempt.json").write_text('{"state":"VALIDATED_UNCHANGED"}')
        if noise:
            (root / "data/atlas/migrations").mkdir(parents=True, exist_ok=True)
            (root / "data/atlas/migrations/0001_noise.sql").write_bytes(os.urandom(noise))

    def test_save_then_restore_round_trips_the_working_data_and_directory_times(self):
        self.write_state(self.source, "v1")
        (self.source / "package.json").write_text("{}")  # code is not part of the state
        failed = self.source / "outputs/promotion/exoplanet-eu/promote-20261112T210806-retrieval-failed"
        failed.mkdir(parents=True)
        (failed / "retrieval.json").write_text("{}")
        old = time.time() - 6 * 86400
        os.utime(failed / "retrieval.json", (old, old))
        os.utime(failed, (old, old))
        saved = self.save()
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertIn("round trip verified", saved.stdout, "the first set is downloaded again and checked")
        manifests = [n for n in self.names() if n.endswith(".parts.json")]
        self.assertEqual(len(manifests), 1)
        manifest = json.loads((self.release / manifests[0]).read_text())
        self.assertEqual(manifest["schema"], "worldsindex.state-parts@1")
        self.assertEqual([p["name"] for p in manifest["parts"]], [n for n in self.names() if ".part-" in n])
        fresh, restored = self.restore_into("fresh", STATE_RECEIPT=str(self.tmp / "receipt.json"))
        (fresh / "data/snapshots/stale").mkdir(parents=True, exist_ok=True)
        fresh, restored = self.restore_into("fresh", STATE_RECEIPT=str(self.tmp / "receipt.json"))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual((fresh / "data/snapshots/nasa/manifest.json").read_text(), "v1")
        self.assertFalse((fresh / "data/snapshots/stale").exists(), "restore replaces the state folders")
        self.assertFalse((fresh / "package.json").exists())
        self.assertFalse((fresh / "outputs/sync/scheduler.lock").exists(), "a lock is never saved")
        # 2026-11-18: directory times survive a restore (the builder's promotion guard read them).
        restored_dir = fresh / "outputs/promotion/exoplanet-eu/promote-20261112T210806-retrieval-failed"
        self.assertAlmostEqual(restored_dir.stat().st_mtime, old, delta=2)
        self.assertAlmostEqual((restored_dir / "retrieval.json").stat().st_mtime, old, delta=2)
        receipt = json.loads((self.tmp / "receipt.json").read_text())
        self.assertEqual(receipt["asset"], manifests[0])
        self.assertEqual(receipt["saved_at"], state.stamp_to_iso(manifests[0]))
        self.assertRegex(receipt["saved_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIsNone(state.stamp_to_iso("wi-state-latest.tar.gz"))

    def test_a_large_state_is_saved_in_three_parts_and_restored(self):
        self.write_state(self.source, "big", noise=300_000)
        saved = self.save(WORLDSINDEX_STATE_PART_GIB=str(120_000 / (1 << 30)))
        self.assertEqual(saved.returncode, 0, saved.stderr)
        parts_ = [n for n in self.names() if ".part-" in n]
        self.assertEqual(len(parts_), 3, parts_)
        self.assertTrue(all(n.endswith(tuple(f".part-{i:03d}" for i in range(3))) for n in parts_))
        fresh, restored = self.restore_into("fresh")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn("checked 3 part(s)", restored.stdout)
        self.assertEqual((fresh / "data/atlas/migrations/0001_noise.sql").read_bytes(),
                         (self.source / "data/atlas/migrations/0001_noise.sql").read_bytes())
        # gzip when zstd is missing: the same format, compression named in the manifest.
        self.write_state(self.source, "gz", noise=300_000)
        saved = self.save(WORLDSINDEX_STATE_COMPRESSOR="gzip", WORLDSINDEX_STATE_PART_GIB=str(120_000 / (1 << 30)))
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertTrue(any(".tar.gz.part-" in n for n in self.names()))
        fresh, restored = self.restore_into("fresh-gz")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual((fresh / "data/snapshots/nasa/manifest.json").read_text(), "gz")

    def test_state_without_snapshots_is_never_uploaded(self):
        (self.source / "public/data").mkdir(parents=True)
        (self.source / "public/data/x.json").write_text("{}")
        result = self.save()
        self.assertEqual(result.returncode, 1)
        self.assertIn("no frozen snapshots", result.stderr)
        self.assertEqual(self.names(), [])

    def test_the_disk_line_refuses_and_the_size_warning_only_warns(self):
        self.write_state(self.source, "big", noise=300_000)
        result = self.save(WORLDSINDEX_STATE_REFUSE_RAW_GIB=str(100_000 / (1 << 30)))
        self.assertEqual(result.returncode, state.TOO_LARGE_EXIT)
        self.assertIn("STATE TOO LARGE", result.stderr)
        self.assertEqual(self.names(), [])
        result = self.save(WORLDSINDEX_STATE_WARN_GIB=str(100_000 / (1 << 30)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARN  state growing", result.stdout)
        self.assertTrue(any(n.endswith(".parts.json") for n in self.names()))

    def test_a_missing_part_falls_back_to_the_older_copy_and_the_next_save_removes_the_broken_set(self):
        self.write_state(self.source, "v1")
        self.assertEqual(self.save().returncode, 0)
        self.write_state(self.source, "v2", noise=300_000)
        self.assertEqual(self.save(WORLDSINDEX_STATE_PART_GIB=str(120_000 / (1 << 30))).returncode, 0)
        newest = sorted(n for n in self.names() if n.endswith(".parts.json"))[-1]
        stamp = state.stamp_of(newest)
        broken = sorted(n for n in self.names() if ".part-" in n and state.stamp_of(n) == stamp)[1]
        (self.release / broken).unlink()
        fresh, restored = self.restore_into("fresh")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertIn("newer incomplete copies are not used", restored.stdout)
        self.assertEqual((fresh / "data/snapshots/nasa/manifest.json").read_text(), "v1")
        self.write_state(self.source, "v3")
        saved = self.save()
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertIn("leftover of an interrupted save", saved.stdout)
        self.assertFalse(any(state.stamp_of(n) == stamp for n in self.names()))

    def test_a_corrupt_part_of_unchanged_size_fails_the_restore_and_leaves_the_folders_alone(self):
        self.write_state(self.source, "v1")
        self.assertEqual(self.save().returncode, 0)
        part = next(n for n in self.names() if ".part-" in n)
        data = bytearray((self.release / part).read_bytes())
        data[len(data) // 2] ^= 0xFF
        (self.release / part).write_bytes(bytes(data))
        target = self.tmp / "work" / "kept"
        (target / "data/snapshots/nasa").mkdir(parents=True)
        (target / "data/snapshots/nasa/manifest.json").write_text("previous")
        _, restored = self.restore_into("kept")
        self.assertEqual(restored.returncode, 1)
        self.assertIn("does not match the manifest", restored.stderr)
        self.assertEqual((target / "data/snapshots/nasa/manifest.json").read_text(), "previous")

    def test_an_interrupted_upload_is_never_current_and_a_flipped_part_fails_the_first_round_trip(self):
        self.write_state(self.source, "v1")
        self.assertEqual(self.save().returncode, 0)
        # The manifest upload fails: the parts already uploaded are removed, v1 stays current.
        self.write_state(self.source, "v2")
        failed = self.save(FAKE_UPLOAD_FAILS_MATCH=".parts.json")
        self.assertEqual(failed.returncode, 1)
        self.assertEqual(len([n for n in self.names() if n.endswith(".parts.json")]), 1)
        fresh, restored = self.restore_into("fresh")
        self.assertEqual((fresh / "data/snapshots/nasa/manifest.json").read_text(), "v1")
        # Parts left without a manifest (the clean-up could not run) are never restored and are removed next time.
        orphan = self.release / "wi-state-29990101T000000Z.tar.zst.part-000"
        orphan.write_bytes(b"x" * 10)
        fresh, restored = self.restore_into("fresh2")
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual((fresh / "data/snapshots/nasa/manifest.json").read_text(), "v1")
        saved = self.save()
        self.assertEqual(saved.returncode, 0, saved.stderr)
        self.assertFalse(orphan.exists())
        # A brand-new release: the first set is downloaded again; a flipped byte removes it.
        for p in self.release.iterdir():
            p.unlink()
        legacy = self.tmp / "legacy.tar.gz"
        with tarfile.open(legacy, "w:gz") as handle:
            handle.add(self.source / "data", arcname="data")
        shutil.copy(legacy, self.release / "wi-state-20261101T000000Z.tar.gz")
        flipped = self.save(FAKE_UPLOAD_FLIPS="1")
        self.assertEqual(flipped.returncode, 1)
        self.assertIn("round trip", flipped.stderr)
        self.assertEqual(self.names(), ["wi-state-20261101T000000Z.tar.gz"], "nothing older is deleted")

    def test_legacy_single_copies_still_restore_and_a_newer_set_wins(self):
        self.write_state(self.source, "legacy")
        legacy = self.tmp / "legacy.tar.gz"
        with tarfile.open(legacy, "w:gz") as handle:
            for top in ("data", "public", "outputs"):
                handle.add(self.source / top, arcname=top)
        shutil.copy(legacy, self.release / "wi-state-20261101T000000Z.tar.gz")
        fresh, restored = self.restore_into("fresh", STATE_RECEIPT=str(self.tmp / "r.json"))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual((fresh / "data/snapshots/nasa/manifest.json").read_text(), "legacy")
        self.assertEqual(json.loads((self.tmp / "r.json").read_text())["asset"], "wi-state-20261101T000000Z.tar.gz")
        self.write_state(self.source, "set")
        self.assertEqual(self.save().returncode, 0)
        fresh, restored = self.restore_into("fresh2")
        self.assertEqual((fresh / "data/snapshots/nasa/manifest.json").read_text(), "set")
        self.assertIn("wi-state-20261101T000000Z.tar.gz", self.names(), "an older day's copy stays (retention)")

    def test_restore_refuses_paths_outside_the_source(self):
        bad = self.tmp / "evil.tar.gz"
        payload = self.tmp / "p.txt"
        payload.write_text("x")
        with tarfile.open(bad, "w:gz") as handle:
            handle.add(payload, arcname="../escape.txt")
        shutil.copy(bad, self.release / "wi-state-20261001T000000Z.tar.gz")
        result = self.cli("restore", str(self.source))
        self.assertEqual(result.returncode, 1)
        self.assertFalse((self.source.parent / "escape.txt").exists())

    def test_hard_linked_duplicates_round_trip_and_outside_links_are_refused(self):
        self.write_state(self.source, "linked")
        first = self.source / "outputs/sync/artifacts/sync-20261001000000-aaaaaaaa/exoplanet-eu-ab.response"
        second = self.source / "outputs/sync/artifacts/sync-20261002000000-bbbbbbbb/exoplanet-eu-ab.response"
        first.parent.mkdir(parents=True); second.parent.mkdir(parents=True)
        first.write_bytes(os.urandom(50_000))
        os.link(first, second)
        result = self.save()
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(next(p for p in self.release.iterdir() if p.name.endswith(".parts.json")).read_text())
        self.assertLess(manifest["bytes"], 60_000, "the set holds the payload once")
        restored, result = self.restore_into("restored")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((restored / second.relative_to(self.source)).read_bytes(), first.read_bytes())
        bad = self.tmp / "evil-link.tar.gz"
        with tarfile.open(bad, "w:gz") as handle:
            handle.add(first, arcname="data/snapshots/nasa/manifest.json")
            info = tarfile.TarInfo("outputs/sneaky.response"); info.type = tarfile.LNKTYPE; info.linkname = "../../etc/passwd"
            handle.addfile(info)
        for p in self.release.iterdir():
            p.unlink()
        shutil.copy(bad, self.release / "wi-state-20261003T000000Z.tar.gz")
        _, result = self.restore_into("restored")
        self.assertEqual(result.returncode, 1)
        self.assertIn("hard link outside", result.stderr)

    def test_the_restore_drill_reads_a_set_and_refuses_a_corrupt_one(self):
        self.write_state(self.source, "drill", noise=300_000)
        (self.source / "data/snapshots/nasa/ACTIVE.json").write_text('{"snapshotId": "manifest.json"}')
        self.assertEqual(self.save(WORLDSINDEX_STATE_PART_GIB=str(120_000 / (1 << 30))).returncode, 0)
        manifest = next(p for p in self.release.iterdir() if p.name.endswith(".parts.json"))
        report = self.tmp / "drill.json"
        drill = subprocess.run([sys.executable, str(ROOT / "scripts/state_restore_drill.py"), "--worldsindex", str(manifest),
                                "--report", str(report)], text=True, capture_output=True)
        self.assertEqual(drill.returncode, 0, drill.stdout + drill.stderr)
        checks = {c["check"]: c["ok"] for c in json.loads(report.read_text())["products"]["worldsindex"]["checks"]}
        self.assertTrue(checks["every part, the joined stream and the tar match the manifest"])
        self.assertTrue(checks["restored size within the 20 GiB disk line"])
        part = next(p for p in self.release.iterdir() if ".part-001" in p.name)
        data = bytearray(part.read_bytes()); data[10] ^= 0xFF; part.write_bytes(bytes(data))
        drill = subprocess.run([sys.executable, str(ROOT / "scripts/state_restore_drill.py"), "--worldsindex", str(manifest),
                                "--report", str(report)], text=True, capture_output=True)
        self.assertEqual(drill.returncode, 1)

    def test_a_manifest_naming_foreign_parts_is_refused(self):
        path = self.tmp / "wi-state-20261101T000000Z.parts.json"
        path.write_text(json.dumps({"schema": state.MANIFEST_SCHEMA, "compression": "zstd",
                                    "parts": [{"name": "wi-state-20261102T000000Z.tar.zst.part-000", "bytes": 1, "sha256": "x"}]}))
        with self.assertRaises(RuntimeError):
            state.read_manifest(path)

    def test_retention_keeps_sets_whole(self):
        legacy = ["wi-state-20261002T020000Z.tar.gz", "wi-state-20261002T010000Z.tar.gz", "wi-state-20261002T000000Z.tar.gz",
                  "wi-state-20261001T230000Z.tar.gz", "wi-state-20260930T120000Z.tar.gz", "wi-state-20260929T120000Z.tar.gz"]
        listing = [{"id": i, "name": n, "created_at": "", "size": 1} for i, n in enumerate(legacy)]
        self.assertEqual({a["name"] for a in state.to_delete(listing)}, {"wi-state-20260929T120000Z.tar.gz"})
        sets = {}
        rows = []
        for stamp in ("20261003T030000Z", "20261003T020000Z", "20261003T010000Z", "20261003T000000Z"):
            names = [f"wi-state-{stamp}.tar.zst.part-000", f"wi-state-{stamp}.tar.zst.part-001"]
            sets[f"wi-state-{stamp}.parts.json"] = {"parts": [{"name": n, "bytes": 1, "sha256": "x"} for n in names]}
            rows += names + [f"wi-state-{stamp}.parts.json"]
        listing = [{"id": 100 + i, "name": n, "created_at": "", "size": 1} for i, n in enumerate(rows + legacy)]
        gone = {a["name"] for a in state.to_delete(listing, sets)}
        self.assertEqual({n for n in gone if "20261003T000000Z" in n},
                         {"wi-state-20261003T000000Z.tar.zst.part-000", "wi-state-20261003T000000Z.tar.zst.part-001",
                          "wi-state-20261003T000000Z.parts.json"}, "a set goes whole")
        self.assertFalse(any("20261003T03" in n or "20261003T02" in n or "20261003T01" in n for n in gone))
        self.assertNotIn("wi-state-20261002T020000Z.tar.gz", gone, "the newest copy of an earlier day stays")


if __name__ == "__main__":
    unittest.main()

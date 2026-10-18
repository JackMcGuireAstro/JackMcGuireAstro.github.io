#!/usr/bin/env python3
"""Tests for scripts/worldsindex_cloud_state.py against a stand-in `gh` (no network)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
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

    def write_state(self, root: Path, marker: str):
        (root / "data/snapshots/nasa").mkdir(parents=True, exist_ok=True)
        (root / "data/snapshots/nasa/manifest.json").write_text(marker)
        (root / "public/data").mkdir(parents=True, exist_ok=True)
        (root / "public/data/sky.json").write_text("{}")
        (root / "outputs/sync/scheduler.lock").mkdir(parents=True, exist_ok=True)
        (root / "outputs/sync/scheduler.lock/pid").write_text("1")
        (root / "outputs/sync/latest-attempt.json").write_text('{"state":"VALIDATED_UNCHANGED"}')

    def test_save_then_restore_round_trips_the_working_data(self):
        self.write_state(self.source, "v1")
        (self.source / "package.json").write_text("{}")  # code is not part of the state
        saved = self.cli("save", str(self.source))
        self.assertEqual(saved.returncode, 0, saved.stderr)
        archive = next(self.release.iterdir())
        with tarfile.open(archive) as handle:
            names = set(handle.getnames())
        self.assertIn("data/snapshots/nasa/manifest.json", names)
        self.assertIn("outputs/sync/latest-attempt.json", names)
        self.assertNotIn("package.json", names)
        self.assertFalse(any("scheduler.lock" in n for n in names), "a lock is never saved")
        fresh = self.tmp / "fresh"
        fresh.mkdir()
        (fresh / "data/snapshots/stale").mkdir(parents=True)
        receipt = self.tmp / "state-restored.json"
        restored = self.cli("restore", str(fresh), STATE_RECEIPT=str(receipt))
        self.assertEqual(restored.returncode, 0, restored.stderr)
        self.assertEqual((fresh / "data/snapshots/nasa/manifest.json").read_text(), "v1")
        self.assertFalse((fresh / "data/snapshots/stale").exists(), "restore replaces the state folders")
        # The receipt names the restored archive and the time it was saved (for the public marker).
        import json
        document = json.loads(receipt.read_text())
        self.assertEqual(document["asset"], archive.name)
        self.assertEqual(document["saved_at"], state.stamp_to_iso(archive.name))
        self.assertRegex(document["saved_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIsNone(state.stamp_to_iso("wi-state-latest.tar.gz"))

    def test_state_without_snapshots_is_never_uploaded(self):
        (self.source / "public/data").mkdir(parents=True)
        (self.source / "public/data/x.json").write_text("{}")
        result = self.cli("save", str(self.source))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(list(self.release.iterdir()), [])

    def test_archive_over_the_asset_limit_is_refused_before_upload(self):
        self.write_state(self.source, "big")
        (self.source / "outputs/sync/blob.bin").write_bytes(os.urandom(300_000))
        result = self.cli("save", str(self.source), WORLDSINDEX_STATE_ASSET_LIMIT_BYTES="200000")
        self.assertEqual(result.returncode, 1)
        self.assertIn("release-asset limit", result.stderr)
        self.assertEqual(list(self.release.iterdir()), [])
        # Under the limit but over the warning line: saved, with a warning.
        result = self.cli("save", str(self.source), WORLDSINDEX_STATE_ASSET_LIMIT_BYTES="900000", WORLDSINDEX_STATE_ASSET_WARN_BYTES="200000")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARN", result.stderr)
        self.assertEqual(len([p for p in self.release.iterdir() if p.name.startswith("wi-state-")]), 1)

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
        # The builder's compaction hard-links identical probe responses across runs.
        self.write_state(self.source, "linked")
        first = self.source / "outputs/sync/artifacts/sync-20261001000000-aaaaaaaa/exoplanet-eu-ab.response"
        second = self.source / "outputs/sync/artifacts/sync-20261002000000-bbbbbbbb/exoplanet-eu-ab.response"
        first.parent.mkdir(parents=True); second.parent.mkdir(parents=True)
        first.write_bytes(b"x" * 50_000)
        os.link(first, second)
        result = self.cli("save", str(self.source))
        self.assertEqual(result.returncode, 0, result.stderr)
        archive = next(p for p in self.release.iterdir() if p.name.startswith("wi-state-"))
        with tarfile.open(archive) as handle:
            types = {m.name: m.type for m in handle.getmembers()}
        self.assertEqual(types[str(second.relative_to(self.source))], tarfile.LNKTYPE)
        self.assertLess(archive.stat().st_size, 60_000, "the archive holds the payload once")
        restored = self.tmp / "work" / "restored"
        restored.mkdir(parents=True)
        result = self.cli("restore", str(restored))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((restored / second.relative_to(self.source)).read_bytes(), b"x" * 50_000)
        # A hard link whose target lies outside the archive tree is still refused.
        bad = self.tmp / "evil-link.tar.gz"
        with tarfile.open(bad, "w:gz") as handle:
            handle.add(first, arcname="data/snapshots/nasa/manifest.json")
            info = tarfile.TarInfo("outputs/sneaky.response"); info.type = tarfile.LNKTYPE; info.linkname = "../../etc/passwd"
            handle.addfile(info)
        for p in self.release.iterdir(): p.unlink()
        shutil.copy(bad, self.release / "wi-state-20261003T000000Z.tar.gz")
        result = self.cli("restore", str(restored))
        self.assertEqual(result.returncode, 1)
        self.assertIn("hard link outside", result.stderr)

    def test_retention_rule(self):
        names = ["wi-state-20261002T020000Z.tar.gz", "wi-state-20261002T010000Z.tar.gz", "wi-state-20261002T000000Z.tar.gz",
                 "wi-state-20261001T230000Z.tar.gz", "wi-state-20260930T120000Z.tar.gz", "wi-state-20260929T120000Z.tar.gz"]
        listing = [{"id": i, "name": n, "created_at": ""} for i, n in enumerate(names)]
        self.assertEqual({a["name"] for a in state.to_delete(listing)}, {"wi-state-20260929T120000Z.tar.gz"})


if __name__ == "__main__":
    unittest.main()

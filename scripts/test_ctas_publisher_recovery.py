#!/usr/bin/env python3
"""Exercise publisher parts and safe recovery against isolated Git repositories."""
import contextlib
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-recovery-")
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "CTAS fixture")
        self.git("config", "user.email", "ctas@example.invalid")
        self.write("ctas/data/status.json", "base")
        self.commit("base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-qb", "remote-update")
        self.write("README.md", "remote code")
        self.commit("remote site update")
        self.remote = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.remote)
        self.git("checkout", "-q", "main")
        runner = (ROOT / "scripts/ctas_launchd_runner.sh").read_text()
        self.helper = runner[runner.index("recover_generated_commits() {"):
                             runner.index('git fetch --quiet origin')]

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.site, check=True,
                              text=True, capture_output=True).stdout.strip()

    def write(self, path, content):
        target = self.site / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def commit(self, subject="CTAS data: fixture"):
        self.git("add", "--all")
        self.git("commit", "-qm", subject)

    def recover(self, mode="recover"):
        return subprocess.run(["/bin/bash", "-c", "set -euo pipefail\nBRANCH=main\n" +
                               self.helper + "\nrecover_generated_commits " + mode],
                              cwd=self.site, text=True, capture_output=True)

    def assert_preserved(self, expected):
        self.assertEqual(self.git("rev-parse", "HEAD"), expected)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)", "refs/ctas-recovery/"), "")

    def test_generated_divergence_preserves_commit_and_recovers(self):
        self.write("ctas/data/status.json", "unpublished local snapshot")
        self.write("ctas/data/candidate-chunks/efe.part-000001.json", "old public part")
        self.commit()
        old = self.git("rev-parse", "HEAD")
        result = self.recover()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.remote)
        refs = self.git("for-each-ref", "--format=%(objectname)", "refs/ctas-recovery/")
        self.assertEqual(refs, old)
        self.assertEqual(self.git("show", old + ":ctas/data/status.json"), "unpublished local snapshot")
        self.assertEqual(self.git("status", "--porcelain"), "")

    def test_code_commit_is_never_reset_even_with_automatic_subject(self):
        self.write("ctas/app.js", "user code")
        self.commit()
        old = self.git("rev-parse", "HEAD")
        self.assertNotEqual(self.recover().returncode, 0)
        self.assert_preserved(old)

    def test_cancelled_code_edits_still_refuse_recovery(self):
        self.write("README.md", "temporary user code")
        self.commit()
        (self.site / "README.md").unlink()
        self.write("ctas/data/status.json", "new data")
        self.commit()
        old = self.git("rev-parse", "HEAD")
        self.assertNotEqual(self.recover().returncode, 0)
        self.assert_preserved(old)

    def test_unknown_data_filename_is_not_generated(self):
        self.write("ctas/data/private-notes.json", "keep me")
        self.commit()
        old = self.git("rev-parse", "HEAD")
        self.assertNotEqual(self.recover().returncode, 0)
        self.assert_preserved(old)

    def test_manual_data_commit_is_not_automatic(self):
        self.write("ctas/data/status.json", "manual changes")
        self.commit("manual edit")
        old = self.git("rev-parse", "HEAD")
        self.assertNotEqual(self.recover().returncode, 0)
        self.assert_preserved(old)

    def test_dirty_checkout_is_preserved(self):
        self.write("ctas/data/status.json", "generated")
        self.commit()
        self.write("ctas/data/status.json", "unfinished")
        old = self.git("rev-parse", "HEAD")
        self.assertNotEqual(self.recover().returncode, 0)
        self.assert_preserved(old)
        self.assertEqual((self.site / "ctas/data/status.json").read_text(), "unfinished")

    def test_verify_mode_never_moves_head(self):
        self.write("ctas/data/status.json", "generated")
        self.commit()
        old = self.git("rev-parse", "HEAD")
        result = self.recover("verify")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_preserved(old)


class PinnedDatabaseBackupTests(unittest.TestCase):
    """Run the actual publisher backup with concurrent WAL writes and failures."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-pinned-backup-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder / "Source with spaces %.db"
        self.destination = self.folder / "snapshot.db"
        self.connect = sqlite3.connect
        with self.connect(self.source) as database:
            database.execute("PRAGMA journal_mode=WAL")
            database.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, payload TEXT)")
            database.executemany("INSERT INTO events VALUES (?,?)", [
                (index, "original-" + str(index) + "x" * 4096) for index in range(320)
            ])
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        self.code = publisher.split("<<'PYCTASBACKUP'\n", 1)[1].split("\nPYCTASBACKUP", 1)[0]
        self.output = io.StringIO()

    def execute(self, source_factory, clock=None):
        def connect(database, *args, **kwargs):
            if kwargs.get("uri"):
                self.assertTrue(str(database).endswith("?mode=ro"))
                kwargs["factory"] = source_factory
            return self.connect(database, *args, **kwargs)
        with patch("sqlite3.connect", connect), \
                patch.object(sys, "argv", ["publisher-backup-test", str(self.source), str(self.destination)]), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output):
            with patch("time.monotonic", clock) if clock else contextlib.nullcontext():
                exec(compile(self.code, "publisher-backup", "exec"), {})

    def assert_cleaned(self):
        for suffix in ("", "-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.destination) + suffix).exists(), suffix)

    def test_concurrent_wal_writes_do_not_restart_or_change_frozen_snapshot(self):
        owner = self
        changed = []
        class ConcurrentSource(sqlite3.Connection):
            def backup(self, destination, *, pages, progress, sleep):
                owner.assertTrue(self.in_transaction)
                def during_copy(status, remaining, total):
                    if remaining > 0 and not changed:
                        with owner.connect(owner.source) as writer:
                            writer.execute("UPDATE events SET payload=? WHERE id=0", ("new live value",))
                            writer.execute("INSERT INTO events VALUES (?,?)", (999999, "new live event"))
                        changed.append(True)
                    progress(status, remaining, total)
                return super().backup(destination, pages=4, progress=during_copy, sleep=sleep)
        self.execute(ConcurrentSource)
        self.assertEqual(changed, [True])
        with self.connect(self.destination) as frozen:
            self.assertEqual(frozen.execute("SELECT COUNT(*) FROM events").fetchone()[0], 320)
            self.assertTrue(frozen.execute("SELECT payload FROM events WHERE id=0").fetchone()[0].startswith("original-0"))
        with self.connect(self.source) as live:
            self.assertEqual(live.execute("SELECT COUNT(*) FROM events").fetchone()[0], 321)
            self.assertEqual(live.execute("SELECT payload FROM events WHERE id=0").fetchone()[0], "new live value")

    def test_disk_full_stops_backup_and_removes_partial_copy_and_sidecars(self):
        owner = self
        class DiskFullSource(sqlite3.Connection):
            def backup(self, destination, *, pages, progress, sleep):
                for suffix in ("-wal", "-shm", "-journal"):
                    Path(str(owner.destination) + suffix).write_bytes(b"incomplete sidecar")
                raise sqlite3.OperationalError("database or disk is full")
        with self.assertRaises(SystemExit) as error:
            self.execute(DiskFullSource)
        self.assertEqual(error.exception.code, 1)
        self.assertIn("database or disk is full", self.output.getvalue())
        self.assert_cleaned()
        with self.connect(self.source) as live:
            self.assertEqual(live.execute("SELECT COUNT(*) FROM events").fetchone()[0], 320)

    def test_deadline_stops_backup_and_removes_partial_copy(self):
        ticks = iter([0.0, 601.0])
        with self.assertRaises(SystemExit) as error:
            self.execute(sqlite3.Connection, clock=lambda: next(ticks))
        self.assertEqual(error.exception.code, 1)
        self.assertIn("600-second deadline", self.output.getvalue())
        self.assert_cleaned()


class DetailAllowlistTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-detail-allowlist-")
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name).resolve()
        self.folder = self.site / "ctas/data/candidate-chunks"
        self.folder.mkdir(parents=True)
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        self.block = publisher[publisher.index("# ----------------------------------------------------------- collect detail files"):
                               publisher.index("# --------------------------------------------------------- collect catalog pages")]
        self.manifest = {"chunks": [], "parts": [], "chunk_count": 4096}
        for i in range(4096):
            bucket = f"{i:03x}"
            doc = {"schema": "ctas.public-candidate-chunk@1.0.0", "bucket": bucket,
                   "candidate_count": 0, "candidates": []}
            self.manifest["chunks"].append(self.write(bucket + ".json", doc, candidate_count=0))
        self.assembled = json.dumps({"schema": "ctas.public-candidate-chunk@1.0.0", "bucket": "000",
                                     "candidate_count": 1, "candidates": [{"event_id": "test"}]}).encode()
        pieces = [self.assembled[:80].decode(), self.assembled[80:].decode()]
        for i, fragment in enumerate(pieces, 1):
            self.manifest["parts"].append(self.write(f"000.part-{i:06d}.json", {
                "schema": "ctas.candidate-json-part@1.0.0", "bucket": "000", "part": i,
                "json_fragment": fragment}))
        self.descriptor = {"schema": "ctas.candidate-chunk-parts@1.0.0", "bucket": "000",
                           "candidate_count": 1, "assembled_bytes": len(self.assembled),
                           "assembled_sha256": hashlib.sha256(self.assembled).hexdigest(),
                           "parts": list(self.manifest["parts"])}
        self.rewrite_descriptor()

    def write(self, name, doc, **extra):
        raw = json.dumps(doc).encode()
        (self.folder / name).write_bytes(raw)
        return {"path": "ctas/data/candidate-chunks/" + name, "bytes": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(), **extra}

    def rewrite_descriptor(self):
        self.manifest["chunks"][0] = self.write("000.json", self.descriptor, candidate_count=1)

    def collect(self):
        (self.folder / "manifest.json").write_text(json.dumps(self.manifest))
        script = 'set -euo pipefail\nPUBLIC_FILES=()\ndie() { echo "$*" >&2; exit 1; }\n'
        script += self.block + '\nprintf "%s\\n" "${PUBLIC_FILES[@]}"\n'
        return subprocess.run(["/bin/bash", "-c", script], cwd=self.site, text=True, capture_output=True)

    def test_all_roots_and_new_parts_are_allowlisted(self):
        result = self.collect()
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = result.stdout.splitlines()
        self.assertEqual(len(actual), 4098)
        self.assertIn("ctas/data/candidate-chunks/000.part-000002.json", actual)

    def test_new_parts_and_retired_parts_reach_the_commit_tree(self):
        self.assertEqual(self.collect().returncode, 0)
        pages = self.site / "ctas/data/catalog-pages"
        pages.mkdir()
        (pages / "manifest.json").write_text(json.dumps({"pages": [], "page_count": 0, "candidate_count": 0}))
        obsolete = "ctas/data/candidate-chunks/000.part-999999.json"
        (self.site / obsolete).write_text("old generated part")
        def git(*args):
            return subprocess.run(["git", *args], cwd=self.site, check=True,
                                  text=True, capture_output=True).stdout.strip()
        git("init", "-q")
        git("config", "user.name", "CTAS fixture")
        git("config", "user.email", "ctas@example.invalid")
        git("add", "ctas")
        git("commit", "-qm", "old generated release")
        git("rm", "--cached", *[row["path"] for row in self.manifest["parts"]])
        git("commit", "-qm", "historical missing-parts fixture")
        (self.site / "private-notes.txt").write_text("leave untouched")
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        block = publisher[publisher.index("# ----------------------------------------------------------- collect detail files"):
                          publisher.index("# ------------------------------------------------------------------ tests")]
        script = ('set -euo pipefail\nPUBLIC_FILES=(ctas/data/candidate-chunks/manifest.json ctas/data/catalog-pages/manifest.json)\n'
                  'die() { echo "$*" >&2; exit 1; }\nsay() { :; }\n' + block +
                  '\ngit add -- "${PUBLIC_FILES[@]}"\ngit commit -qm "fixed release"\n')
        result = subprocess.run(["/bin/bash", "-c", script], cwd=self.site, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(git("ls-files", obsolete), "")
        for row in self.manifest["parts"]:
            self.assertEqual(git("ls-files", row["path"]), row["path"])
        self.assertEqual(git("ls-files", "private-notes.txt"), "")
        self.assertTrue((self.site / "private-notes.txt").exists())

    def test_tampered_part_is_refused(self):
        (self.folder / "000.part-000001.json").write_text("tampered")
        self.assertNotEqual(self.collect().returncode, 0)

    def test_unreachable_part_is_refused(self):
        self.manifest["parts"].append(self.write("001.part-000001.json", {}))
        self.assertNotEqual(self.collect().returncode, 0)

    def test_unlisted_descriptor_part_is_refused(self):
        self.manifest["parts"].pop()
        self.assertNotEqual(self.collect().returncode, 0)

    def test_reconstruction_checksum_mismatch_is_refused(self):
        self.descriptor["assembled_sha256"] = "0" * 64
        self.rewrite_descriptor()
        self.assertNotEqual(self.collect().returncode, 0)

    def test_redirected_part_is_refused(self):
        target = self.folder / "000.part-000001.json"
        copied = self.site / "redirected.json"
        copied.write_bytes(target.read_bytes())
        target.unlink()
        target.symlink_to(copied)
        self.assertNotEqual(self.collect().returncode, 0)

    def test_oversized_request_is_refused(self):
        row = self.manifest["parts"][0]
        raw = b" " * (4 * 1024 * 1024 + 1)
        (self.site / row["path"]).write_bytes(raw)
        row["bytes"] = len(raw)
        row["sha256"] = hashlib.sha256(raw).hexdigest()
        self.descriptor["parts"][0] = dict(row)
        self.rewrite_descriptor()
        self.assertNotEqual(self.collect().returncode, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

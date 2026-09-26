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
        # these tests cover the backup API copy; on APFS the default would clone instead
        with patch("sqlite3.connect", connect), patch.dict("os.environ", {"CTAS_SNAPSHOT_METHOD": "backup"}), \
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


class ClonedDatabaseSnapshotTests(unittest.TestCase):
    """Run the publisher's snapshot code with a stand-in for clonefile(2), so the
    copy-on-write path (APFS only) is exercised here: live writers and checkpoints
    between pinning, cloning the main file and cloning its WAL must never produce a
    snapshot that mixes two committed states."""

    ACCOUNTS = 64
    TOTAL = 64 * 1000

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-cloned-snapshot-")
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder / "live soc.db"
        self.destination = self.folder / "ctas-publish.snapshot"
        self.destination.write_bytes(b"")  # mktemp leaves an empty file behind
        self.live = sqlite3.connect(self.source, timeout=0.2)
        self.addCleanup(self.live.close)
        self.live.execute("PRAGMA journal_mode=WAL")
        self.live.execute("PRAGMA wal_autocheckpoint=0")
        self.live.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, balance INTEGER, payload TEXT)")
        self.live.executemany("INSERT INTO events VALUES (?,?,?)", [
            (index, 1000, "x" * 3000) for index in range(self.ACCOUNTS)])
        self.live.commit()
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        self.code = publisher.split("<<'PYCTASBACKUP'\n", 1)[1].split("\nPYCTASBACKUP", 1)[0]
        self.output = io.StringIO()
        self.clone_calls = []
        self.hooks = {}
        self.clone_errno = {}
        self.corrupt_main_clone = False

    # one live transaction: move value between accounts and append one event, so any
    # snapshot mixing two states breaks the balance total or the event/transfer pairing
    def transfer(self, step, checkpoint=None):
        with self.live:
            self.live.execute("UPDATE events SET balance = balance - 7 WHERE id = ?", (step % self.ACCOUNTS,))
            self.live.execute("UPDATE events SET balance = balance + 7 WHERE id = ?", ((step * 13 + 5) % self.ACCOUNTS,))
            self.live.execute("INSERT INTO events VALUES (?,?,?)", (100000 + step, 0, "y" * 5000))
        if checkpoint:
            self.live.execute(f"PRAGMA wal_checkpoint({checkpoint})").fetchall()

    def clone_stand_in(self, source, target, flags):
        source, target = source.decode(), target.decode()
        kind = "wal" if source.endswith("-wal") else "main"
        self.clone_calls.append(kind)
        self.hooks.get(kind, lambda: None)()
        if kind in self.clone_errno:
            self.errno_value = self.clone_errno[kind]
            return -1
        if Path(target).exists():
            self.errno_value = 17  # EEXIST, as clonefile(2) reports
            return -1
        Path(target).write_bytes(Path(source).read_bytes())
        if kind == "main" and self.corrupt_main_clone:
            # damage b-tree pages the WAL does not carry (a page the WAL holds would
            # legitimately be restored by recovery)
            with open(target, "r+b") as handle:
                handle.seek(4096)
                handle.write(bytes([0x55]) * 4096 * 3)
        return 0

    def fake_cdll(self):
        stand_in = self.clone_stand_in
        class Library:
            def __init__(self, name, use_errno=False):
                def clonefile(source, target, flags):
                    return stand_in(source, target, flags)
                self.clonefile = clonefile
        return Library

    def execute(self, method=None):
        environment = {"CTAS_SNAPSHOT_METHOD": method} if method else {}
        self.errno_value = 0
        with patch("ctypes.CDLL", self.fake_cdll()), patch("ctypes.get_errno", lambda: self.errno_value), \
                patch.dict("os.environ", environment), \
                patch.object(sys, "argv", ["publisher-clone-test", str(self.source), str(self.destination)]), \
                contextlib.redirect_stdout(self.output), contextlib.redirect_stderr(self.output):
            exec(compile(self.code, "publisher-clone", "exec"), {})

    def assert_consistent_snapshot(self):
        for suffix in ("-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.destination) + suffix).exists(), suffix)
        with contextlib.closing(sqlite3.connect(self.destination.as_uri() + "?mode=ro", uri=True)) as frozen:
            if "copy-on-write clone" in self.output.getvalue().splitlines()[-1]:
                # a settled clone stands alone: its WAL was recovered and retired
                self.assertEqual(frozen.execute("PRAGMA journal_mode").fetchone()[0], "delete")
            self.assertEqual(frozen.execute("PRAGMA quick_check").fetchone()[0], "ok")
            total = frozen.execute("SELECT SUM(balance) FROM events").fetchone()[0]
            transfers = frozen.execute("SELECT COUNT(*) FROM events WHERE id >= 100000").fetchone()[0]
            moved = frozen.execute("SELECT SUM(ABS(balance - 1000)) FROM events WHERE id < ?", (self.ACCOUNTS,)).fetchone()[0]
            self.assertEqual(total, self.TOTAL)
            return transfers, moved

    def test_writes_and_checkpoints_around_each_clone_give_one_committed_state(self):
        step = iter(range(10000))
        for round_ in range(12):
            with self.subTest(round=round_):
                for _ in range(round_ % 4):
                    self.transfer(next(step))
                checkpoint = ("PASSIVE", "FULL", None)[round_ % 3]
                self.hooks = {
                    "main": lambda: [self.transfer(next(step), checkpoint) for _ in range(1 + round_ % 3)],
                    "wal": lambda: [self.transfer(next(step), "PASSIVE") for _ in range(2)],
                }
                self.clone_calls = []
                self.destination.unlink(missing_ok=True)
                self.destination.write_bytes(b"")
                self.execute()
                self.assertEqual(self.clone_calls, ["main", "wal"])
                self.assertIn("copy-on-write clone", self.output.getvalue())
                transfers, _ = self.assert_consistent_snapshot()
                with contextlib.closing(sqlite3.connect(self.source)) as live:
                    live_transfers = live.execute("SELECT COUNT(*) FROM events WHERE id >= 100000").fetchone()[0]
                self.assertLessEqual(transfers, live_transfers)
            if round_ % 4 == 3:
                self.live.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()

    def test_snapshot_taken_from_a_fully_checkpointed_wal_survives_a_wal_reset(self):
        self.live.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchall()
        self.hooks = {"main": lambda: [self.transfer(step, "PASSIVE") for step in range(5)]}
        self.execute()
        transfers, _ = self.assert_consistent_snapshot()
        self.assertIn(transfers, (0, 5))

    def test_unsupported_volume_falls_back_to_a_full_copy_of_the_pinned_state(self):
        import errno as errors
        self.clone_errno = {"main": errors.ENOTSUP}
        self.hooks = {"main": lambda: self.transfer(1, "PASSIVE")}
        self.execute()
        self.assertIn("full copy", self.output.getvalue())
        transfers, _ = self.assert_consistent_snapshot()
        self.assertEqual(transfers, 0)  # the backup copies exactly the pinned state

    def test_wal_that_cannot_be_cloned_falls_back_to_a_full_copy(self):
        import errno as errors
        self.clone_errno = {"wal": errors.EXDEV}
        self.transfer(1)
        self.execute()
        self.assertEqual(self.clone_calls, ["main", "wal"])
        self.assertIn("full copy", self.output.getvalue())
        self.assertEqual(self.assert_consistent_snapshot()[0], 1)

    def test_unusable_clone_falls_back_to_a_full_copy(self):
        self.transfer(1, "TRUNCATE")
        self.corrupt_main_clone = True
        self.execute()
        self.assertIn("Cloned snapshot unusable", self.output.getvalue())
        self.assertIn("full copy", self.output.getvalue())
        self.assertEqual(self.assert_consistent_snapshot()[0], 1)

    def test_other_clone_errors_stop_the_run_and_remove_the_snapshot(self):
        self.clone_errno = {"main": 13}  # EACCES
        with self.assertRaises(SystemExit) as error:
            self.execute()
        self.assertEqual(error.exception.code, 1)
        self.assertIn("clonefile failed", self.output.getvalue())
        for suffix in ("", "-wal", "-shm", "-journal"):
            self.assertFalse(Path(str(self.destination) + suffix).exists(), suffix)

    def test_backup_method_never_clones(self):
        self.execute("backup")
        self.assertEqual(self.clone_calls, [])
        self.assertIn("full copy", self.output.getvalue())
        self.assert_consistent_snapshot()

    def test_live_database_is_only_read(self):
        before = self.source.stat().st_mtime_ns
        self.execute()
        self.assertEqual(self.source.stat().st_mtime_ns, before)
        self.assert_consistent_snapshot()


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


class UnfinishedGeneratedFilesTests(unittest.TestCase):
    """Run the runner's preserve-and-clear step against a checkout left dirty by an
    interrupted or refused export. Until 2026-09-24 this step was a pathspec
    `git stash`, whose internal `git apply` refuses patches of 1 GiB or more; a
    fully regenerated catalog exceeded that and the publisher failed every run."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-unfinished-")
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "CTAS fixture")
        self.git("config", "user.email", "ctas@example.invalid")
        self.write("ctas/app.js", "public code")
        self.write("ctas/data/status.json", "published status")
        self.write("ctas/data/candidate-chunks/000.json", "published root")
        self.write("ctas/data/candidate-chunks/001.json", "root to be retired")
        self.write("ctas/data/catalog-pages/0001.json", "published page")
        self.git("add", "--all")
        self.git("commit", "-qm", "CTAS data: fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.git("update-ref", "refs/remotes/origin/main", self.head)
        self.runner = (ROOT / "scripts/ctas_launchd_runner.sh").read_text()
        self.block = self.runner[self.runner.index("# Only generated public data may be dirty"):
                                 self.runner.index("# ------------------------------------------------- recover generated-only commits")]

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.site, check=True,
                              text=True, capture_output=True).stdout.strip()

    def write(self, path, content):
        target = self.site / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def dirty_export(self):
        self.write("ctas/data/status.json", "unfinished status")
        self.write("ctas/data/candidate-chunks/000.json", "unfinished root")
        self.write("ctas/data/candidate-chunks/000.part-000001.json", "new part")
        self.write("ctas/data/catalog-pages/0002.json", "new page")
        (self.site / "ctas/data/candidate-chunks/001.json").unlink()

    def run_block(self):
        script = ("set -uo pipefail\nBRANCH=main\n"
                  "say() { printf '%s\\n' \"$*\"; }\n"
                  "die() { printf 'FAIL  %s\\n' \"$*\" >&2; exit 1; }\n" + self.block)
        return subprocess.run(["/bin/bash", "-c", script], cwd=self.site, text=True, capture_output=True)

    def recovery_refs(self):
        refs = self.git("for-each-ref", "--format=%(refname) %(objectname)", "refs/ctas-recovery/")
        return [line.split() for line in refs.splitlines()]

    def test_unfinished_files_are_preserved_on_a_recovery_ref_and_cleared(self):
        self.dirty_export()
        result = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("preserved unfinished generated files at refs/ctas-recovery/", result.stdout)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(self.git("diff", "--cached", "--name-only"), "")
        refs = self.recovery_refs()
        self.assertEqual(len(refs), 1)
        ref, commit = refs[0]
        self.assertIn("-unfinished-", ref)
        self.assertEqual(self.git("show", "-s", "--format=%P", commit), self.head)
        self.assertEqual(self.git("show", commit + ":ctas/data/status.json"), "unfinished status")
        self.assertEqual(self.git("show", commit + ":ctas/data/candidate-chunks/000.json"), "unfinished root")
        self.assertEqual(self.git("show", commit + ":ctas/data/candidate-chunks/000.part-000001.json"), "new part")
        self.assertEqual(self.git("show", commit + ":ctas/data/catalog-pages/0002.json"), "new page")
        self.assertEqual(self.git("show", commit + ":ctas/app.js"), "public code")
        preserved_paths = self.git("ls-tree", "-r", "--name-only", commit).splitlines()
        self.assertNotIn("ctas/data/candidate-chunks/001.json", preserved_paths)
        self.assertEqual((self.site / "ctas/data/candidate-chunks/001.json").read_text(), "root to be retired")

    def test_preservation_failure_still_clears_the_checkout(self):
        self.dirty_export()
        # A plain file where the recovery namespace should live makes update-ref fail.
        (self.site / ".git/refs/ctas-recovery").write_text("blocked")
        result = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("could not preserve unfinished generated files; discarding them", result.stdout)
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), "")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual((self.site / "ctas/data/status.json").read_text(), "published status")

    def test_clean_checkout_is_left_alone(self):
        result = self.run_block()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")
        self.assertEqual(self.recovery_refs(), [])

    def test_non_generated_changes_still_stop_the_runner(self):
        for path in ("ctas/app.js", "ctas/data/private-notes.json"):
            with self.subTest(path=path):
                self.write(path, "not generated")
                self.write("ctas/data/status.json", "unfinished status")
                result = self.run_block()
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("unexpected non-generated changes", result.stderr)
                self.assertEqual((self.site / path).read_text(), "not generated")
                self.assertEqual((self.site / "ctas/data/status.json").read_text(), "unfinished status")
                self.assertEqual(self.recovery_refs(), [])
                self.git("checkout", "--", "ctas")
                (self.site / path).unlink(missing_ok=True)
                self.git("checkout", "--", ".")

    def test_generated_files_never_go_through_a_patch(self):
        code = "\n".join(line for line in self.runner.splitlines() if not line.lstrip().startswith("#"))
        self.assertNotIn("git stash", code)
        self.assertNotIn("git apply", code)


class PublisherDiscardTests(unittest.TestCase):
    """The publisher must leave no regenerated file behind on any non-committing
    exit. `git restore -- a b c` restores nothing when any listed path is absent
    from HEAD, so a run that produced a new page or part used to leave the whole
    regenerated catalog dirty after a refused release."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-discard-")
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name).resolve()
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "CTAS fixture")
        self.git("config", "user.email", "ctas@example.invalid")
        self.write("ctas/app.js", "public code")
        self.write("ctas/data/status.json", "published status")
        self.write("ctas/data/candidate-chunks/000.json", "published root")
        self.write("ctas/data/catalog-pages/0001.json", "published page")
        self.git("add", "--all")
        self.git("commit", "-qm", "CTAS data: fixture")
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        self.functions = publisher[publisher.index("discard_generated_files() {"):
                                   publisher.index("trap cleanup EXIT")]
        self.write("ctas/app.js", "edited code stays")
        self.write("ctas/data/status.json", "refused status")
        self.write("ctas/data/candidate-chunks/000.json", "refused root")
        self.write("ctas/data/candidate-chunks/000.part-000001.json", "new part")
        self.write("ctas/data/catalog-pages/0002.json", "new page")
        (self.site / "ctas/data/catalog-pages/0001.json").unlink()
        self.git("add", "--", "ctas/data/status.json")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.site, check=True,
                              text=True, capture_output=True).stdout.strip()

    def write(self, path, content):
        target = self.site / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)

    def run_script(self, body):
        script = ("set -uo pipefail\nSITE=" + repr(str(self.site)) + "\nSITE_READY=1\nPUBLISH_DB=''\n"
                  "LOCKDIR=" + repr(str(self.site / "lock.d")) + "\n" + self.functions + "\n" + body)
        return subprocess.run(["/bin/bash", "-c", script], cwd=self.site, text=True, capture_output=True)

    def assert_generated_files_match_head(self):
        self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all", "--", "ctas/data"), "")
        self.assertEqual((self.site / "ctas/data/status.json").read_text(), "published status")
        self.assertEqual((self.site / "ctas/data/catalog-pages/0001.json").read_text(), "published page")
        self.assertFalse((self.site / "ctas/data/candidate-chunks/000.part-000001.json").exists())
        self.assertFalse((self.site / "ctas/data/catalog-pages/0002.json").exists())
        self.assertEqual((self.site / "ctas/app.js").read_text(), "edited code stays")

    def test_discard_returns_all_generated_files_to_head_and_keeps_code_edits(self):
        result = self.run_script("discard_generated_files\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assert_generated_files_match_head()

    def test_failed_run_cleanup_discards_new_pages_and_parts(self):
        (self.site / "lock.d").mkdir()
        result = self.run_script("false\ncleanup\n")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assert_generated_files_match_head()
        self.assertFalse((self.site / "lock.d").exists())

    def test_successful_run_cleanup_keeps_the_working_tree(self):
        (self.site / "lock.d").mkdir()
        result = self.run_script("true\ncleanup\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.site / "ctas/data/status.json").read_text(), "refused status")
        self.assertTrue((self.site / "ctas/data/catalog-pages/0002.json").exists())

    def test_every_non_committing_exit_discards_generated_files(self):
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        for message in ("publication paused", "next freshness heartbeat in", "--dry-run:"):
            with self.subTest(message=message):
                index = publisher.index(message)
                following = publisher[index:publisher.index("exit 0", index)]
                self.assertIn("discard_generated_files", following)


if __name__ == "__main__":
    unittest.main(verbosity=2)

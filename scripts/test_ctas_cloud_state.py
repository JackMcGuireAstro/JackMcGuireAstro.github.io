#!/usr/bin/env python3
"""Tests for scripts/ctas_cloud_state.py against a stand-in `gh` (no network)."""
from __future__ import annotations

import gzip
import io
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ctas_cloud_state.py"
sys.path.insert(0, str(ROOT / "scripts"))
import ctas_cloud_state as state  # noqa: E402

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, shutil, sys, zlib
from pathlib import Path
store = Path(os.environ["FAKE_RELEASE_DIR"])
args = sys.argv[1:]
def asset(p):
    return {"id": zlib.crc32(p.name.encode()), "name": p.name, "size": p.stat().st_size,
            "created_at": "%020d" % p.stat().st_mtime_ns}
if args[:1] == ["api"] and "-X" not in args:
    print(json.dumps({"assets": [asset(p) for p in sorted(store.iterdir())]}))
elif args[:2] == ["api", "-X"]:
    wanted = int(args[3].rsplit("/", 1)[1])
    for p in store.iterdir():
        if zlib.crc32(p.name.encode()) == wanted:
            p.unlink()
elif args[:2] == ["release", "download"]:
    pattern = args[args.index("--pattern") + 1]; target = Path(args[args.index("--dir") + 1])
    shutil.copy(store / pattern, target / pattern)
elif args[:2] == ["release", "upload"]:
    if os.environ.get("FAKE_UPLOAD_FAILS"):
        print("upload refused", file=sys.stderr); sys.exit(1)
    shutil.copy(args[3], store / Path(args[3]).name)
    if os.environ.get("FAKE_UPLOAD_CORRUPTS"):
        with open(store / Path(args[3]).name, "ab") as handle:
            handle.write(b"x")
else:
    print("unexpected gh call: %r" % args, file=sys.stderr); sys.exit(2)
'''


def make_db(path: Path, rows: int) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT)")
    con.executemany("INSERT INTO events (name) VALUES (?)", [(f"SN{i}",) for i in range(rows)])
    con.commit()
    con.close()


def gz(path: Path, source: Path) -> None:
    with open(source, "rb") as src, gzip.open(path, "wb") as dst:
        dst.write(src.read())


class CloudStateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.release = self.tmp / "release"
        self.release.mkdir()
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        (bin_dir / "gh").write_text(FAKE_GH)
        (bin_dir / "gh").chmod(0o755)
        self.env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_RELEASE_DIR": str(self.release)}
        self.data = self.tmp / "work" / "data"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def cli(self, *args, **env):
        return subprocess.run([sys.executable, str(SCRIPT), *args], env={**self.env, **env},
                              text=True, capture_output=True)

    def put_static(self):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
            payload = b"label,value\n"
            info = tarfile.TarInfo("benchmarks/labels.csv")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))
        (self.release / "static-inputs.tar.gz").write_bytes(buffer.getvalue())

    def test_restore_takes_the_newest_database_and_the_static_inputs(self):
        self.put_static()
        for name, rows in (("soc-20260930T100000Z.db.gz", 1), ("soc-20261001T090000Z.db.gz", 5)):
            make_db(self.tmp / "x.db", rows)
            gz(self.release / name, self.tmp / "x.db")
            (self.tmp / "x.db").unlink()
        receipt = self.tmp / "state-restored.json"
        result = self.cli("restore", str(self.data), STATE_RECEIPT=str(receipt))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.data / "benchmarks/labels.csv").read_text(), "label,value\n")
        count = sqlite3.connect(self.data / "soc.db").execute("SELECT COUNT(*) FROM events").fetchone()[0]
        self.assertEqual(count, 5)
        self.assertIn("soc-20261001T090000Z.db.gz", result.stdout)
        # The receipt says which copy was restored and when it was saved (for the public marker).
        import json
        document = json.loads(receipt.read_text())
        self.assertEqual(document["asset"], "soc-20261001T090000Z.db.gz")
        self.assertEqual(document["saved_at"], "2026-10-01T09:00:00Z")
        self.assertRegex(document["restored_at"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
        self.assertIsNone(state.stamp_to_iso("soc-garbage.db.gz", "soc-"))

    def test_restore_without_a_database_fails(self):
        self.put_static()
        result = self.cli("restore", str(self.data))
        self.assertEqual(result.returncode, 1)
        self.assertIn("no database copy", result.stderr)

    def test_save_uploads_a_checked_copy_and_prunes_old_ones(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 3)
        for name in ("soc-20260927T100000Z.db.gz", "soc-20260928T100000Z.db.gz", "soc-20260929T100000Z.db.gz",
                     "soc-20260930T080000Z.db.gz", "soc-20260930T090000Z.db.gz", "soc-20260930T100000Z.db.gz"):
            (self.release / name).write_bytes(b"old")
        result = self.cli("save", str(self.data), CTAS_STATE_KEEP_RECENT="3", CTAS_STATE_KEEP_DAYS="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        names = sorted(p.name for p in self.release.iterdir())
        new = [n for n in names if n not in {"soc-20260927T100000Z.db.gz", "soc-20260928T100000Z.db.gz",
                                              "soc-20260929T100000Z.db.gz", "soc-20260930T080000Z.db.gz",
                                              "soc-20260930T090000Z.db.gz", "soc-20260930T100000Z.db.gz"}]
        self.assertEqual(len(new), 1)
        # newest 3 (the new copy and two from Sep 30) plus the newest of the 2 earlier days
        self.assertEqual(set(names), {new[0], "soc-20260930T100000Z.db.gz", "soc-20260930T090000Z.db.gz",
                                      "soc-20260929T100000Z.db.gz", "soc-20260928T100000Z.db.gz"})
        self.assertTrue(new[0].endswith(".db.zst" if state.compressor() == "zstd" else ".db.gz"), new[0])
        state.decompress(self.release / new[0], self.tmp / "check.db")
        self.assertEqual(sqlite3.connect(self.tmp / "check.db").execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
        self.assertFalse((self.data.parent / new[0]).exists(), "the staged copy is removed")

    def test_a_damaged_database_is_never_uploaded(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 50)
        sqlite3.connect(self.data / "soc.db").execute("PRAGMA wal_checkpoint(TRUNCATE)").close()
        raw = bytearray((self.data / "soc.db").read_bytes())
        raw[4096:8192] = b"\xff" * 4096
        (self.data / "soc.db").write_bytes(bytes(raw))
        (self.release / "soc-20260930T100000Z.db.gz").write_bytes(b"good")
        result = self.cli("save", str(self.data))
        self.assertEqual(result.returncode, 1)
        self.assertEqual([p.name for p in self.release.iterdir()], ["soc-20260930T100000Z.db.gz"])

    def test_a_failed_upload_keeps_the_previous_state(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 2)
        (self.release / "soc-20260930T100000Z.db.gz").write_bytes(b"good")
        result = self.cli("save", str(self.data), FAKE_UPLOAD_FAILS="1")
        self.assertEqual(result.returncode, 1)
        self.assertEqual([p.name for p in self.release.iterdir()], ["soc-20260930T100000Z.db.gz"])

    def test_a_copy_over_the_refusal_line_is_not_uploaded_and_the_previous_state_stays(self):
        # Thresholds in GiB, scaled down so a tiny test database crosses them.
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 20)
        (self.release / "soc-20260930T100000Z.db.gz").write_bytes(b"good")
        report = self.tmp / "state-size.json"
        result = self.cli("save", str(self.data), CTAS_STATE_WARN_GIB="0.0000001", CTAS_STATE_REFUSE_GIB="0.0000002",
                          STATE_SIZE_REPORT=str(report))
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("STATE TOO LARGE", result.stderr)
        self.assertEqual([p.name for p in self.release.iterdir()], ["soc-20260930T100000Z.db.gz"])
        self.assertEqual([p.name for p in self.data.parent.iterdir()], ["data"], "the staged copy is removed")
        import json
        document = json.loads(report.read_text())
        self.assertEqual(document["verdict"], "refuse")
        self.assertGreater(document["compressed_bytes"], document["refuse_bytes"])
        self.assertGreater(document["raw_bytes"], 0)

    def test_a_copy_over_the_warning_line_is_uploaded_with_a_warning(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 20)
        report = self.tmp / "state-size.json"
        result = self.cli("save", str(self.data), CTAS_STATE_WARN_GIB="0.0000001", STATE_SIZE_REPORT=str(report))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARN  the database is", result.stdout)
        self.assertEqual(len([p for p in self.release.iterdir() if p.name.startswith("soc-")]), 1)
        import json
        self.assertEqual(json.loads(report.read_text())["verdict"], "warn")

    def test_gzip_is_used_without_zstd_and_restore_reads_both_the_newest_first(self):
        import shutil
        if not shutil.which("zstd"):
            self.skipTest("the zstd tool is not installed here (GitHub's runners have it)")
        self.put_static()
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 4)
        result = self.cli("save", str(self.data), CTAS_STATE_COMPRESSOR="gzip")
        self.assertEqual(result.returncode, 0, result.stderr)
        gz_copy = [p.name for p in self.release.iterdir() if p.name.startswith("soc-")]
        self.assertEqual(len(gz_copy), 1)
        self.assertTrue(gz_copy[0].endswith(".db.gz"))
        # An older gzip copy and a newer zstd copy: restore takes the newer one.
        make_db(self.tmp / "y.db", 7)
        newer = "soc-29991231T000000Z.db.zst"
        state.compress(self.tmp / "y.db", self.release / newer)
        receipt = self.tmp / "receipt.json"
        restored = self.tmp / "restored"
        result = self.cli("restore", str(restored), STATE_RECEIPT=str(receipt))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sqlite3.connect(restored / "soc.db").execute("SELECT COUNT(*) FROM events").fetchone()[0], 7)
        import json
        self.assertEqual(json.loads(receipt.read_text())["asset"], newer)
        self.assertEqual(state.stamp_to_iso(newer, "soc-"), "2999-12-31T00:00:00Z")

    def test_the_first_copy_in_a_new_format_is_downloaded_and_checked_before_older_copies_go(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 6)
        other = ".db.gz" if state.compressor() == "zstd" else ".db.zst"   # a format this save does not write
        old = [f"soc-20260929T100000Z{other}", f"soc-20260930T090000Z{other}", f"soc-20260930T100000Z{other}",
               f"soc-20260930T110000Z{other}"]
        for name in old:
            (self.release / name).write_bytes(b"old")
        broken = self.cli("save", str(self.data), FAKE_UPLOAD_CORRUPTS="1", CTAS_STATE_KEEP_RECENT="1", CTAS_STATE_KEEP_DAYS="0")
        self.assertEqual(broken.returncode, 1, broken.stdout)
        self.assertIn("round trip", broken.stderr)
        self.assertEqual(sorted(p.name for p in self.release.iterdir()), old, "nothing removed, the bad copy deleted")
        good = self.cli("save", str(self.data), CTAS_STATE_KEEP_RECENT="1", CTAS_STATE_KEEP_DAYS="0")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("round trip verified", good.stdout)
        self.assertEqual(len(list(self.release.iterdir())), 1)
        # Once the release holds a copy in this format, the download check is not repeated.
        again = self.cli("save", str(self.data))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("round trip verified", again.stdout)

    def test_size_verdict_lines(self):
        gib = 1 << 30
        self.assertEqual(state.size_verdict(int(1.0 * gib)), "ok")
        self.assertEqual(state.size_verdict(int(1.5 * gib)), "warn")
        self.assertEqual(state.size_verdict(int(1.95 * gib)), "refuse")
        self.assertLess(state.REFUSE_BYTES, 2 * gib)

    def test_retention_rule(self):
        listing = [{"id": i, "name": n, "created_at": ""} for i, n in enumerate([
            "soc-20261001T020000Z.db.gz", "soc-20261001T010000Z.db.gz", "soc-20261001T000000Z.db.gz",
            "soc-20260930T230000Z.db.gz", "soc-20260929T120000Z.db.gz", "soc-20260928T120000Z.db.gz",
            "static-inputs.tar.gz"])]
        removed = {a["name"] for a in state.to_delete(listing)}
        self.assertEqual(removed, {"soc-20260928T120000Z.db.gz"})


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Tests for scripts/ctas_cloud_state.py against a stand-in `gh` (no network)."""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

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
    name = Path(args[3]).name
    if os.environ.get("FAKE_UPLOAD_FAILS") or (os.environ.get("FAKE_UPLOAD_FAILS_MATCH") and os.environ["FAKE_UPLOAD_FAILS_MATCH"] in name):
        print("upload refused", file=sys.stderr); sys.exit(1)
    if (store / name).exists() and "--clobber" not in args:
        print("asset exists", file=sys.stderr); sys.exit(1)
    shutil.copy(args[3], store / name)
    if os.environ.get("FAKE_UPLOAD_CORRUPTS"):
        with open(store / name, "ab") as handle:
            handle.write(b"x")
    if os.environ.get("FAKE_UPLOAD_FLIPS") and ".part-" in name:
        data = bytearray((store / name).read_bytes()); data[len(data) // 2] ^= 0xFF; (store / name).write_bytes(bytes(data))
else:
    print("unexpected gh call: %r" % args, file=sys.stderr); sys.exit(2)
'''


def make_db(path: Path, rows: int, noisy: bool = False) -> None:
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, name TEXT)")
    # noisy rows (sha256 hex) compress poorly, so a small part size gives several parts
    con.executemany("INSERT INTO events (name) VALUES (?)",
                    [(f"SN{i}" + (hashlib.sha256(str(i).encode()).hexdigest() if noisy else ""),) for i in range(rows)])
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
        self.assertIn("no complete database copy", result.stderr)

    def new_set(self, before: set[str]) -> tuple[str, list[str]]:
        added = sorted(p.name for p in self.release.iterdir() if p.name not in before)
        manifests = [n for n in added if n.endswith(".parts.json")]
        self.assertEqual(len(manifests), 1, added)
        return manifests[0], [n for n in added if ".part-" in n]

    def test_save_uploads_a_checked_set_and_prunes_old_copies(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 3)
        old = {"soc-20260927T100000Z.db.gz", "soc-20260928T100000Z.db.gz", "soc-20260929T100000Z.db.gz",
               "soc-20260930T080000Z.db.gz", "soc-20260930T090000Z.db.gz", "soc-20260930T100000Z.db.gz"}
        for name in old:
            (self.release / name).write_bytes(b"old")
        result = self.cli("save", str(self.data), CTAS_STATE_KEEP_RECENT="3", CTAS_STATE_KEEP_DAYS="2")
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest, parts = self.new_set(old)
        self.assertEqual(parts, [manifest.replace(".parts.json", ".db.zst.part-000" if state.compressor() == "zstd" else ".db.gz.part-000")])
        # newest 3 copies (the new set and two from Sep 30) plus the newest of the 2 earlier days
        self.assertEqual({p.name for p in self.release.iterdir()}, {manifest, *parts, "soc-20260930T100000Z.db.gz",
                         "soc-20260930T090000Z.db.gz", "soc-20260929T100000Z.db.gz", "soc-20260928T100000Z.db.gz"})
        document = json.loads((self.release / manifest).read_text())
        self.assertEqual(document["schema"], "ctas.state-parts@1")
        self.assertEqual(document["database_sha256"], state.sha256_file(self.data / "soc.db"))
        self.assertEqual([p["name"] for p in document["parts"]], parts)
        restored = self.tmp / "restored"
        result = self.cli("restore", str(restored))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sqlite3.connect(restored / "soc.db").execute("SELECT COUNT(*) FROM events").fetchone()[0], 3)
        self.assertEqual([p.name for p in self.data.parent.iterdir()], ["data"], "staged parts are removed")

    def test_a_three_part_set_round_trips_and_restore_checks_every_part(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 400, noisy=True)
        (self.release / "soc-20260930T100000Z.db.gz").write_bytes(b"old")
        if state.compressor() != "zstd":
            self.skipTest("the zstd tool is not installed here (GitHub's runners have it)")
        sqlite3.connect(self.data / "soc.db").execute("PRAGMA wal_checkpoint(TRUNCATE)").close()
        stream = subprocess.run(["zstd", "-q", "-c", "-T0", *state.ZSTD_ARGS, str(self.data / "soc.db")],
                                check=True, stdout=subprocess.PIPE).stdout
        part = len(stream) // 3 + 1          # exactly three parts, the last one shorter
        result = self.cli("save", str(self.data), CTAS_STATE_PART_GIB=str(part / (1 << 30)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("round trip verified", result.stdout)
        manifest, parts = self.new_set({"soc-20260930T100000Z.db.gz"})
        self.assertEqual(len(parts), 3, parts)
        document = json.loads((self.release / manifest).read_text())
        self.assertEqual(sum(p["bytes"] for p in document["parts"]), document["bytes"])
        self.assertTrue(all(p["bytes"] <= part for p in document["parts"]))
        self.assertEqual(document["bytes"], len(stream))
        receipt = self.tmp / "receipt.json"
        restored = self.tmp / "restored"
        result = self.cli("restore", str(restored), STATE_RECEIPT=str(receipt))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("checked 3 part(s)", result.stdout)
        self.assertEqual(state.sha256_file(restored / "soc.db"), document["database_sha256"])
        self.assertEqual(json.loads(receipt.read_text())["asset"], manifest)
        self.assertEqual(json.loads(receipt.read_text())["saved_at"], document["saved_at"])

    def test_a_set_with_a_missing_part_is_never_restored_and_the_next_save_removes_it(self):
        self.data.mkdir(parents=True)
        make_db(self.tmp / "older.db", 2)
        gz(self.release / "soc-20260930T100000Z.db.gz", self.tmp / "older.db")
        make_db(self.data / "soc.db", 400, noisy=True)
        part_gib = str(1800 / (1 << 30))
        self.assertEqual(self.cli("save", str(self.data), CTAS_STATE_PART_GIB=part_gib).returncode, 0)
        manifest, parts = self.new_set({"soc-20260930T100000Z.db.gz"})
        (self.release / parts[1]).unlink()
        restored = self.tmp / "restored"
        result = self.cli("restore", str(restored))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("newer incomplete copies are not used", result.stdout)
        self.assertIn("soc-20260930T100000Z.db.gz", result.stdout)
        self.assertEqual(sqlite3.connect(restored / "soc.db").execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        again = self.cli("save", str(self.data), CTAS_STATE_PART_GIB=part_gib)
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertIn("leftover of an interrupted save", again.stdout)
        names = {p.name for p in self.release.iterdir()}
        self.assertFalse({manifest, *parts} & names)

    def test_a_corrupt_part_stops_the_restore(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 400, noisy=True)
        part_gib = str(1800 / (1 << 30))
        self.assertEqual(self.cli("save", str(self.data), CTAS_STATE_PART_GIB=part_gib).returncode, 0)
        manifest, parts = self.new_set(set())
        data = bytearray((self.release / parts[2]).read_bytes())
        data[0] ^= 0xFF
        (self.release / parts[2]).write_bytes(bytes(data))   # same size: only the sha256 can tell
        restored = self.tmp / "restored"
        result = self.cli("restore", str(restored))
        self.assertEqual(result.returncode, 1)
        self.assertIn("does not match the manifest", result.stderr)
        self.assertFalse((restored / "soc.db").exists())

    def test_an_interrupted_upload_is_never_current_and_is_cleaned_up(self):
        self.data.mkdir(parents=True)
        make_db(self.tmp / "older.db", 2)
        gz(self.release / "soc-20260930T100000Z.db.gz", self.tmp / "older.db")
        make_db(self.data / "soc.db", 400, noisy=True)
        part_gib = str(1800 / (1 << 30))
        # The manifest upload fails after the parts went up: the set is removed again.
        failed = self.cli("save", str(self.data), CTAS_STATE_PART_GIB=part_gib, FAKE_UPLOAD_FAILS_MATCH=".parts.json")
        self.assertEqual(failed.returncode, 1)
        self.assertEqual({p.name for p in self.release.iterdir()}, {"soc-20260930T100000Z.db.gz"})
        # A run killed mid-upload leaves parts and no manifest: restore ignores them ...
        for index in range(2):
            (self.release / f"soc-20261001T000000Z.db.zst.part-{index:03d}").write_bytes(b"half")
        restored = self.tmp / "restored"
        result = self.cli("restore", str(restored))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sqlite3.connect(restored / "soc.db").execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)
        # ... and the next save removes them.
        result = self.cli("save", str(self.data), CTAS_STATE_PART_GIB=part_gib)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(p.name.startswith("soc-20261001T000000Z") for p in self.release.iterdir()))

    def test_a_damaged_manifest_makes_its_set_incomplete(self):
        self.data.mkdir(parents=True)
        make_db(self.tmp / "older.db", 2)
        gz(self.release / "soc-20260930T100000Z.db.gz", self.tmp / "older.db")
        make_db(self.data / "soc.db", 5)
        self.assertEqual(self.cli("save", str(self.data)).returncode, 0)
        manifest, _ = self.new_set({"soc-20260930T100000Z.db.gz"})
        (self.release / manifest).write_text("{not json")
        restored = self.tmp / "restored"
        result = self.cli("restore", str(restored))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("is damaged", result.stdout)
        self.assertEqual(sqlite3.connect(restored / "soc.db").execute("SELECT COUNT(*) FROM events").fetchone()[0], 2)

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

    def test_a_database_over_the_disk_line_is_not_uploaded_and_the_previous_state_stays(self):
        # The refusal is on the raw size (the runner's disk), scaled down so a tiny database crosses it.
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 20)
        (self.release / "soc-20260930T100000Z.db.gz").write_bytes(b"good")
        report = self.tmp / "state-size.json"
        result = self.cli("save", str(self.data), CTAS_STATE_REFUSE_RAW_GIB="0.0000001", STATE_SIZE_REPORT=str(report))
        self.assertEqual(result.returncode, 4, result.stderr)
        self.assertIn("STATE TOO LARGE", result.stderr)
        self.assertEqual([p.name for p in self.release.iterdir()], ["soc-20260930T100000Z.db.gz"])
        self.assertEqual([p.name for p in self.data.parent.iterdir()], ["data"], "nothing is staged")
        document = json.loads(report.read_text())
        self.assertEqual(document["verdict"], "refuse")
        self.assertGreater(document["raw_bytes"], document["refuse_raw_bytes"])

    def test_a_copy_over_the_early_signal_line_is_uploaded_as_state_growing(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 20)
        report = self.tmp / "state-size.json"
        result = self.cli("save", str(self.data), CTAS_STATE_WARN_GIB="0.0000001", STATE_SIZE_REPORT=str(report))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WARN  state growing", result.stdout)
        self.assertEqual(len([p for p in self.release.iterdir() if p.name.endswith(".parts.json")]), 1)
        document = json.loads(report.read_text())
        self.assertEqual((document["verdict"], document["parts"]), ("growing", 1))
        self.assertGreater(document["free_disk_bytes"], 0)

    def test_gzip_is_used_without_zstd_and_restore_reads_both_the_newest_first(self):
        import shutil
        if not shutil.which("zstd"):
            self.skipTest("the zstd tool is not installed here (GitHub's runners have it)")
        self.put_static()
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 4)
        result = self.cli("save", str(self.data), CTAS_STATE_COMPRESSOR="gzip")
        self.assertEqual(result.returncode, 0, result.stderr)
        gz_set = sorted(p.name for p in self.release.iterdir() if p.name.startswith("soc-"))
        self.assertEqual(len(gz_set), 2, gz_set)
        self.assertTrue(gz_set[0].endswith(".db.gz.part-000") and gz_set[1].endswith(".parts.json"), gz_set)
        restored_gz = self.tmp / "restored-gz"
        result = self.cli("restore", str(restored_gz))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sqlite3.connect(restored_gz / "soc.db").execute("SELECT COUNT(*) FROM events").fetchone()[0], 4)
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

    def test_the_first_set_is_downloaded_and_checked_before_older_copies_go(self):
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 6)
        old = ["soc-20260929T100000Z.db.zst", "soc-20260930T090000Z.db.zst", "soc-20260930T100000Z.db.zst",
               "soc-20260930T110000Z.db.zst"]
        for name in old:
            (self.release / name).write_bytes(b"old")
        # A part damaged in transit with its size unchanged: only the round trip can tell.
        broken = self.cli("save", str(self.data), FAKE_UPLOAD_FLIPS="1", CTAS_STATE_KEEP_RECENT="1", CTAS_STATE_KEEP_DAYS="0")
        self.assertEqual(broken.returncode, 1, broken.stdout)
        self.assertIn("round trip", broken.stderr)
        self.assertEqual(sorted(p.name for p in self.release.iterdir()), old, "nothing removed, the bad set deleted")
        # A part that arrives with the wrong size is caught before that.
        short = self.cli("save", str(self.data), FAKE_UPLOAD_CORRUPTS="1")
        self.assertEqual(short.returncode, 1)
        self.assertIn("did not arrive with the right size", short.stderr)
        self.assertEqual(sorted(p.name for p in self.release.iterdir()), old)
        good = self.cli("save", str(self.data), CTAS_STATE_KEEP_RECENT="1", CTAS_STATE_KEEP_DAYS="0")
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertIn("round trip verified", good.stdout)
        self.assertEqual(len([p for p in self.release.iterdir() if p.name.endswith(".parts.json")]), 1)
        self.assertFalse(any(p.name.endswith(".db.zst") for p in self.release.iterdir()))
        # Once the release holds a set, the download check is not repeated.
        time.sleep(1.1)   # a new stamp
        again = self.cli("save", str(self.data))
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertNotIn("round trip verified", again.stdout)

    def test_the_restore_drill_checks_a_set(self):
        import state_restore_drill as drill
        self.data.mkdir(parents=True)
        make_db(self.data / "soc.db", 400, noisy=True)
        self.assertEqual(self.cli("save", str(self.data), CTAS_STATE_PART_GIB=str(1800 / (1 << 30))).returncode, 0)
        manifest, parts = self.new_set(set())
        work = self.tmp / "drill"
        work.mkdir()
        report = drill.drill_ctas(self.release / manifest, None, work)
        first = report["checks"][0]
        self.assertEqual((first["check"], first["ok"]), ("every part, the joined stream and the database match the manifest", True))
        self.assertEqual(report["mode"], "set joined and restored into an empty directory")
        self.assertTrue(next(c for c in report["checks"] if c["check"] == "sqlite quick_check")["ok"])
        data = bytearray((self.release / parts[0]).read_bytes())
        data[-1] ^= 0xFF
        (self.release / parts[0]).write_bytes(bytes(data))
        (work / "soc.db").unlink(missing_ok=True)
        report = drill.drill_ctas(self.release / manifest, None, work)
        self.assertFalse(report["ok"])
        self.assertFalse(report["checks"][0]["ok"])

    def test_size_verdict_lines(self):
        gib = 1 << 30
        self.assertEqual(state.size_verdict(int(1.0 * gib), 5 * gib), "ok")
        self.assertEqual(state.size_verdict(int(1.5 * gib), 5 * gib), "growing")
        self.assertEqual(state.size_verdict(int(2.5 * gib), 12 * gib), "growing", "parts lift GitHub's per-file limit")
        self.assertEqual(state.size_verdict(int(1.0 * gib), 21 * gib), "refuse")
        self.assertEqual(state.REFUSE_RAW_BYTES, 20 * gib)
        self.assertEqual(state.PART_BYTES, int(1.5 * gib))
        self.assertLess(state.PART_BYTES, 2 * gib)

    def test_part_writer_and_joined_stream_round_trip(self):
        directory = self.tmp / "parts"
        directory.mkdir()
        writer = state.PartWriter(directory, "soc-20261116T000000Z.db.zst", part_bytes=10)
        for chunk in (b"abc", b"defghijklmnop", b"", b"qrstuvwxyz0123456789"):
            writer.write(chunk)
        writer.close()
        self.assertEqual([p["bytes"] for p in writer.parts], [10, 10, 10, 6])
        joined = b"".join((directory / p["name"]).read_bytes() for p in writer.parts)
        self.assertEqual(joined, b"abcdefghijklmnopqrstuvwxyz0123456789")
        import hashlib
        self.assertEqual(writer.whole.hexdigest(), hashlib.sha256(joined).hexdigest())
        reader = state.JoinedReader([directory / p["name"] for p in writer.parts])
        self.assertEqual(b"".join(iter(lambda: reader.read(7), b"")), joined)
        self.assertEqual(reader.digest.hexdigest(), hashlib.sha256(joined).hexdigest())

    def test_retention_treats_a_set_as_one_copy(self):
        def entry(i, name, size=1):
            return {"id": i, "name": name, "size": size, "created_at": ""}
        listing, manifests = [], {}
        for i, stamp in enumerate(("20261001T030000Z", "20261001T020000Z", "20261001T010000Z", "20260929T000000Z")):
            parts = [f"soc-{stamp}.db.zst.part-{k:03d}" for k in range(2)]
            listing += [entry(10 * i + k, n) for k, n in enumerate(parts)] + [entry(10 * i + 9, f"soc-{stamp}.parts.json")]
            manifests[f"soc-{stamp}.parts.json"] = {"parts": [{"name": n, "bytes": 1, "sha256": "x"} for n in parts]}
        listing.append(entry(99, "soc-20260928T000000Z.db.zst"))
        listing.append(entry(98, "envelopes-2026-W40.ndjson.zst"))
        with mock.patch.object(state, "KEEP_RECENT", 2), mock.patch.object(state, "KEEP_DAYS", 1):
            removed = sorted(a["name"] for a in state.to_delete(listing, manifests))
        # newest 2 sets plus the newest copy of one earlier day (09-29); the 3rd 10-01 set and 09-28 go
        self.assertEqual(removed, ["soc-20260928T000000Z.db.zst", "soc-20261001T010000Z.db.zst.part-000",
                                   "soc-20261001T010000Z.db.zst.part-001", "soc-20261001T010000Z.parts.json"])
        # a set whose part has the wrong size is incomplete: never a copy, removed as a leftover
        listing[0]["size"] = 7
        copies = state.database_copies(listing, manifests)
        self.assertFalse(next(u for u in copies if u["stamp"] == "20261001T030000Z")["complete"])
        leftovers = {a["name"] for a in state.incomplete_assets(listing, manifests, "20261116T000000Z")}
        self.assertEqual(leftovers, {"soc-20261001T030000Z.db.zst.part-000", "soc-20261001T030000Z.db.zst.part-001",
                                     "soc-20261001T030000Z.parts.json"})
        # ... unless its manifest merely could not be downloaded this time
        self.assertEqual(state.incomplete_assets(listing, {}, "20261116T000000Z", {"soc-20261001T030000Z.parts.json"})[:0], [])
        kept = {a["name"] for a in state.incomplete_assets(listing, {}, "x", {"soc-20261001T030000Z.parts.json"})}
        self.assertNotIn("soc-20261001T030000Z.parts.json", kept)

    def test_stamp_of_names(self):
        self.assertEqual(state.stamp_of("soc-20261116T000000Z.db.zst.part-012"), "20261116T000000Z")
        self.assertEqual(state.stamp_of("soc-20261116T000000Z.parts.json"), "20261116T000000Z")
        self.assertEqual(state.stamp_of("soc-20261116T000000Z.db.gz"), "20261116T000000Z")
        self.assertIsNone(state.stamp_of("soc-20261116T000000Z.db.zst.part-x"))
        self.assertIsNone(state.stamp_of("envelopes-2026-W40.ndjson.zst"))
        self.assertIsNone(state.stamp_of("skymaps.tar.gz"))

    def test_retention_rule(self):
        listing = [{"id": i, "name": n, "created_at": ""} for i, n in enumerate([
            "soc-20261001T020000Z.db.gz", "soc-20261001T010000Z.db.gz", "soc-20261001T000000Z.db.gz",
            "soc-20260930T230000Z.db.gz", "soc-20260929T120000Z.db.gz", "soc-20260928T120000Z.db.gz",
            "static-inputs.tar.gz"])]
        removed = {a["name"] for a in state.to_delete(listing)}
        self.assertEqual(removed, {"soc-20260928T120000Z.db.gz"})


if __name__ == "__main__":
    unittest.main()

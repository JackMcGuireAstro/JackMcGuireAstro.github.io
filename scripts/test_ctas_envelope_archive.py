#!/usr/bin/env python3
"""Tests for scripts/ctas_envelope_archive.py (the alert-envelope payload archive)."""
from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import ctas_envelope_archive as arc  # noqa: E402

# The collector's reader (supernova_watch/envelope_archive.py in the private state repo) pins
# the same text in tests/test_envelope_archive.py.
PINNED_POINTER = (
    '{"ctas_archived_payload": {"schema": "ctas.envelope-payload-archive-pointer@1", '
    '"asset": "envelopes-2026-W32.ndjson.zst", "sha256": '
    '"79525a9d6ab278b149055c20ee262b6b94ede930fc10bb1e7f528cccac1a7921", "bytes": 13}}'
)
NOW = "2026-11-08T21:10:00Z"


def make_db(path: Path) -> list[tuple]:
    con = sqlite3.connect(path)
    con.execute("PRAGMA auto_vacuum = INCREMENTAL")
    con.execute("CREATE TABLE alert_envelopes (id VARCHAR(36) PRIMARY KEY, provider VARCHAR(64), checksum VARCHAR(64), "
                "received_at DATETIME, raw_payload JSON, event_id VARCHAR(36))")
    con.execute("CREATE INDEX ix_received ON alert_envelopes (received_at)")
    rows = []
    days = ["2026-09-28 10:00:00.000001", "2026-09-30 23:59:59.999999", "2026-10-06 00:00:00.000000",
            "2026-10-11 12:00:00.500000", "2026-10-20 08:00:00.000000", "2026-11-07 08:00:00.000000"]
    n = 0
    for day in days:
        for provider in ("ztf-alerce", "atlas", "rubin-fink", "tns", "gcn"):
            for k in range(3):
                n += 1
                payload = json.dumps({"provider": provider, "n": n, "mag": 18.0 + k / 10, "note": "é ✓ ☃" * (k + 1),
                                      "blob": "x" * 2500})
                rows.append((f"env-{n:04d}", provider, hashlib.sha256(payload.encode()).hexdigest(), day, payload, "ev1"))
    con.executemany("INSERT INTO alert_envelopes VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    return rows


def payloads(path: Path) -> dict[str, str]:
    con = sqlite3.connect(path)
    try:
        return dict(con.execute("SELECT id, raw_payload FROM alert_envelopes"))
    finally:
        con.close()


def read_asset(path: Path) -> list[dict]:
    out = subprocess.run(["zstd", "-q", "-d", "-c", "--long=27", str(path)], check=True, stdout=subprocess.PIPE).stdout
    return [json.loads(line) for line in out.splitlines() if line.strip()]


@unittest.skipIf(shutil.which("zstd") is None, "the zstd tool is needed")
class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "soc.db"
        self.rows = make_db(self.db)
        self.release = self.tmp / "release"
        self.env = mock.patch.dict("os.environ", {"CTAS_ENVELOPE_ARCHIVE_LOCAL_RELEASE": str(self.release)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_archive(self, *extra: str) -> tuple[int, str, dict]:
        report = self.tmp / "report.json"
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = arc.main(["run", "--db", str(self.db), "--work", str(self.tmp / "work"), "--report", str(report),
                             "--now", NOW, *extra])
        return code, buffer.getvalue(), json.loads(report.read_text())

    def test_pointer_text_is_pinned(self):
        self.assertEqual(arc.pointer_text("envelopes-2026-W32.ndjson.zst", '{"ra": 150.0}'), PINNED_POINTER)
        self.assertTrue(PINNED_POINTER.startswith(arc.TEXT_PREFIX))

    def test_archives_old_photometry_weeks_only_and_round_trips_exactly(self):
        before = payloads(self.db)
        code, out, report = self.run_archive("--max-weeks", "10")
        self.assertEqual(code, 0, out)
        # weeks ending at least 14 days before NOW (2026-11-08 21:10): W40 (09-28..10-04) and W41;
        # W42 has no rows, W43 (10-19..10-25) ends after the cutoff
        self.assertEqual([w["week"] for w in report["weeks"]], ["2026-W40", "2026-W41"])
        self.assertEqual(sorted(p.name for p in self.release.iterdir()),
                         ["envelopes-2026-W40.ndjson.zst", "envelopes-2026-W41.ndjson.zst"])
        after = payloads(self.db)
        archived = {i for i, text in after.items() if text.startswith(arc.TEXT_PREFIX)}
        expected = {r[0] for r in self.rows if r[1] in arc.PROVIDERS and r[3] < "2026-10-19"}
        self.assertEqual(archived, expected)
        for envelope_id in set(after) - archived:
            self.assertEqual(after[envelope_id], before[envelope_id])  # tns, gcn and recent rows untouched
        lines = {}
        for asset in self.release.iterdir():
            for line in read_asset(asset):
                lines[line["id"]] = (asset.name, line)
        self.assertEqual(set(lines), expected)
        for envelope_id in expected:
            asset, line = lines[envelope_id]
            pointer = json.loads(after[envelope_id])["ctas_archived_payload"]
            self.assertEqual(line["payload"], before[envelope_id])  # byte for byte
            self.assertEqual(pointer, {"schema": arc.POINTER_SCHEMA, "asset": asset,
                                       "sha256": hashlib.sha256(before[envelope_id].encode()).hexdigest(),
                                       "bytes": len(before[envelope_id].encode())})
        self.assertEqual(report["rows_archived"], len(expected))
        self.assertIn("vacuum_seconds", report)
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
        self.assertEqual(con.execute("PRAGMA quick_check").fetchone()[0], "ok")
        con.close()
        # a second run finds nothing more to do
        code, out, report = self.run_archive("--max-weeks", "10")
        self.assertEqual((code, report["status"]), (0, "nothing-to-archive"))

    def test_bounded_per_run_and_resumes(self):
        code, _, report = self.run_archive("--max-weeks", "1")
        self.assertEqual((code, [w["week"] for w in report["weeks"]], report["weeks_left"]), (0, ["2026-W40"], 1))
        code, _, report = self.run_archive("--max-weeks", "1")
        self.assertEqual([w["week"] for w in report["weeks"]], ["2026-W41"])

    def test_dry_run_changes_nothing(self):
        before = payloads(self.db)
        code, out, report = self.run_archive("--dry-run")
        self.assertEqual((code, report["status"]), (0, "dry-run"))
        self.assertEqual(payloads(self.db), before)
        self.assertFalse(self.release.exists() and any(self.release.iterdir()))

    def test_damaged_download_leaves_the_week_in_the_database(self):
        before = payloads(self.db)
        original = arc.LocalRelease.download

        def damaged(release, name, directory):
            path = original(release, name, directory)
            data = path.read_bytes()
            path.write_bytes(data[:-40] + b"\0" * 40)
            return path

        with mock.patch.object(arc.LocalRelease, "download", damaged):
            code, out, report = self.run_archive()
        self.assertEqual((code, report["status"]), (1, "failed"))
        self.assertIn("2026-W40", report["error"])
        self.assertEqual(payloads(self.db), before)

    def test_changed_payload_in_release_is_refused(self):
        before = payloads(self.db)
        original = arc.LocalRelease.download

        def swapped(release, name, directory):
            path = original(release, name, directory)
            lines = read_asset(path)
            text = lines[0]["payload"].replace("18.0", "19.0")
            lines[0].update(payload=text, sha256=hashlib.sha256(text.encode()).hexdigest())
            plain = directory / "swap.ndjson"
            plain.write_text("".join(json.dumps(line) + "\n" for line in lines))
            subprocess.run(["zstd", "-q", "-f", "--long=27", str(plain), "-o", str(path)], check=True)
            return path

        with mock.patch.object(arc.LocalRelease, "download", swapped):
            code, out, report = self.run_archive()
        self.assertEqual(code, 1)
        self.assertEqual(payloads(self.db), before)

    def test_row_changed_after_reading_is_not_archived(self):
        before = payloads(self.db)
        original = arc.LocalRelease.upload

        def upload_then_change(release, path):
            original(release, path)
            con = sqlite3.connect(self.db)
            con.execute("UPDATE alert_envelopes SET raw_payload = '{\"changed\": true}' WHERE id = 'env-0001'")
            con.commit()
            con.close()

        with mock.patch.object(arc.LocalRelease, "upload", upload_then_change):
            code, out, report = self.run_archive("--max-weeks", "1")
        self.assertEqual(code, 1)
        self.assertIn("rows matched the archived text", report["error"])
        after = payloads(self.db)
        self.assertFalse(any(text.startswith(arc.TEXT_PREFIX) for text in after.values()))
        self.assertEqual({k: v for k, v in after.items() if k != "env-0001"},
                         {k: v for k, v in before.items() if k != "env-0001"})

    def test_existing_asset_is_never_replaced(self):
        self.release.mkdir(parents=True)
        (self.release / "envelopes-2026-W40.ndjson.zst").write_bytes(b"older")
        code, _, report = self.run_archive("--max-weeks", "1")
        self.assertEqual(code, 0)
        self.assertEqual(report["weeks"][0]["asset"], "envelopes-2026-W40-p2.ndjson.zst")
        self.assertEqual((self.release / "envelopes-2026-W40.ndjson.zst").read_bytes(), b"older")

    def test_minimum_age_is_enforced(self):
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), mock.patch("sys.stderr", io.StringIO()):
            arc.main(["run", "--db", str(self.db), "--work", str(self.tmp / "w"), "--min-age-days", "3"])

    def test_status_lists_weeks(self):
        self.run_archive("--max-weeks", "1")
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            arc.main(["status", "--db", str(self.db)])
        self.assertIn("week of 2026-09-28: 18 archived, 0 in the database", buffer.getvalue())


class WiringTests(unittest.TestCase):
    def test_cloud_cycle_runs_the_archive_before_retention_and_never_in_dry_runs(self):
        text = (ROOT / "scripts/ctas_cloud_run.sh").read_text()
        step = text.index('scripts/ctas_envelope_archive.py" run')
        self.assertLess(step, text.index('scripts/ctas_db_retention.py" daily'))
        self.assertGreater(step, text.index("set_phase retention"))
        guard = text.rindex("\nif ", 0, step)
        self.assertIn('"$DRY_RUN" != "1"', text[guard:step])
        self.assertIn('[ -f "$BACKEND/src/supernova_watch/envelope_archive.py" ]', text[guard:step])
        self.assertIn('--report "$WORK/envelope-archive.json"', text)
        self.assertIn("--id ctas.envelope-archive", text)
        self.assertIn('timeout --foreground "${CTAS_ENVELOPE_ARCHIVE_TIMEOUT:-1500}"', text)
        self.assertIn('CTAS_ENVELOPE_ARCHIVE_TOKEN="$STATE_TOKEN"', text)

    def test_state_copies_and_archives_do_not_collide(self):
        import ctas_cloud_state as state
        listing = [{"id": 1, "name": "envelopes-2026-W40.ndjson.zst", "size": 1, "created_at": "x"},
                   {"id": 2, "name": "soc-20261108T000000Z.db.zst", "size": 1, "created_at": "x"}]
        self.assertEqual([a["name"] for a in state.database_assets(listing)], ["soc-20261108T000000Z.db.zst"])
        self.assertEqual(state.to_delete(listing), [])
        self.assertIsNone(state.stamp_of("envelopes-2026-W40.ndjson.zst"))
        self.assertEqual(state.incomplete_assets(listing, {}, "20261116T000000Z"), [])


if __name__ == "__main__":
    unittest.main()

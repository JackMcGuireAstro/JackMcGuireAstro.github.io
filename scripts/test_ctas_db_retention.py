#!/usr/bin/env python3
"""Exercise ctas_db_retention.py against a synthetic database shaped like soc.db."""
import gzip
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import uuid

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ctas_db_retention.py"
NOW = "2026-09-26T03:00:00+00:00"

SCHEMA = """
CREATE TABLE events (id VARCHAR(36) PRIMARY KEY, name TEXT);
CREATE TABLE alert_envelopes (id VARCHAR(36) PRIMARY KEY, payload TEXT);
CREATE TABLE analysis_runs (
  id VARCHAR(36) PRIMARY KEY, event_id VARCHAR(36) REFERENCES events(id) ON DELETE CASCADE,
  analysis_type VARCHAR(64), method_name VARCHAR(120), method_version VARCHAR(48),
  analysis_key VARCHAR(64) UNIQUE, status VARCHAR(32), input_manifest JSON, input_checksum VARCHAR(64),
  parameters JSON, software_versions JSON, result JSON, result_checksum VARCHAR(64), quality JSON,
  warnings JSON, reproducibility JSON, data_rights VARCHAR(32), review_state VARCHAR(32),
  created_at DATETIME, completed_at DATETIME);
CREATE INDEX ix_analysis_runs_event_type_completed ON analysis_runs (event_id, analysis_type, completed_at);
CREATE TABLE certification_runs (
  id VARCHAR(36) PRIMARY KEY, scope VARCHAR(48), status VARCHAR(32), artifact_manifest_checksum VARCHAR(64),
  evidence_checksum VARCHAR(64), report_checksum VARCHAR(64) UNIQUE, gates JSON, evidence JSON,
  requested_by VARCHAR(120), created_at DATETIME);
"""


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-retention-")
        self.addCleanup(self.temporary.cleanup)
        self.data = Path(self.temporary.name)
        self.db = self.data / "soc.db"
        (self.data / "benchmarks").mkdir()
        con = sqlite3.connect(self.db)
        con.execute("PRAGMA journal_mode=WAL")
        con.executescript(SCHEMA)
        self.expected_kept, self.expected_pruned = set(), set()
        self.cited = None
        for e in range(20):
            event = f"event-{e:02d}"
            con.execute("INSERT INTO events VALUES (?, ?)", (event, event))
            con.execute("INSERT INTO alert_envelopes VALUES (?, ?)", (str(uuid.uuid4()), "source payload" * 50))
            for kind in ("science-interest", "population-anomaly"):
                days = [20, 15, 10, 6, 1]  # the 1-day-old run is both newest and recent
                for index, age in enumerate(days):
                    run = str(uuid.uuid4())
                    completed = f"2026-09-{26 - age:02d} 02:00:00.{index:06d}"
                    self.insert_run(con, run, event, kind, completed)
                    (self.expected_kept if age == 1 else self.expected_pruned).add(run)
                    if e == 3 and kind == "science-interest" and age == 15:
                        self.cited = run
            for kind, day in (("light-curve-inference", 6), ("light-curve-inference", 11), ("host-association", 2)):
                run = str(uuid.uuid4())
                self.insert_run(con, run, event, kind, f"2026-08-{day:02d} 01:00:00.000000")
                self.expected_kept.add(run)
        # a superseded churn run that is still young: kept by --keep-days
        young = str(uuid.uuid4())
        self.insert_run(con, young, "event-05", "population-anomaly", "2026-09-24 12:00:00.000000")
        self.expected_kept.add(young)
        self.expected_pruned.discard(self.cited)
        self.expected_kept.add(self.cited)
        (self.data / "benchmarks" / "packet.json").write_text(json.dumps({"prediction_run_id": self.cited}))
        self.cert_kept, self.cert_pruned = set(), set()
        for index in range(14):
            run = str(uuid.uuid4())
            con.execute("INSERT INTO certification_runs VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (run, "combined-production-publication", "passed", "a" * 64, "b" * 64, f"{index:064d}",
                         "[]", json.dumps({"blob": "x" * 20000}), "local-operator",
                         f"2026-08-{10 + index:02d} 00:00:00.000000"))
            (self.cert_kept if index >= 4 else self.cert_pruned).add(run)
        con.commit()
        con.close()
        self.young = young

    def insert_run(self, con, run, event, kind, completed):
        con.execute(
            "INSERT INTO analysis_runs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run, event, kind, "method", "1", uuid.uuid4().hex, "completed", "{}", "c" * 64, "{}", "{}",
             json.dumps({"score": 1, "pad": "y" * 3000}), "d" * 64, "{}", "[]", "{}", "public", "machine",
             completed, completed))

    def run_tool(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), *args, "--db", str(self.db), "--now", NOW],
                                text=True, capture_output=True)
        return result

    def ids(self, table):
        with sqlite3.connect(self.db) as con:
            return {row[0] for row in con.execute(f"SELECT id FROM {table}")}

    def test_preview_changes_nothing(self):
        before = self.ids("analysis_runs")
        result = self.run_tool("preview")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"analysis_runs: prune {len(self.expected_pruned):,} of", result.stdout)
        self.assertIn("certification_runs: prune 4 of 14", result.stdout)
        self.assertIn("1 run ids cited by reference files", result.stdout)
        self.assertEqual(self.ids("analysis_runs"), before)

    def test_prune_archives_deletes_and_compacts(self):
        size_before = sum(os.path.getsize(str(self.db) + s) for s in ("", "-wal") if os.path.exists(str(self.db) + s))
        archive = self.data / "_to_delete" / "pruned.ndjson.gz"
        result = self.run_tool("prune", "--archive", str(archive), "--vacuum")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        kept = self.ids("analysis_runs")
        self.assertEqual(kept, self.expected_kept)
        self.assertTrue(self.expected_pruned.isdisjoint(kept))
        self.assertIn(self.cited, kept)
        self.assertIn(self.young, kept)
        self.assertEqual(self.ids("certification_runs"), self.cert_kept)
        with gzip.open(archive, "rt") as handle:
            rows = [json.loads(line) for line in handle]
        self.assertEqual({row["row"]["id"] for row in rows if row["table"] == "analysis_runs"}, self.expected_pruned)
        self.assertEqual({row["row"]["id"] for row in rows if row["table"] == "certification_runs"}, self.cert_pruned)
        self.assertEqual(rows[0]["row"]["result"][:9], '{"score":')
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM alert_envelopes").fetchone()[0], 20)
            self.assertEqual(con.execute("SELECT COUNT(*) FROM events").fetchone()[0], 20)
        self.assertLess(os.path.getsize(self.db), size_before * 0.6)

    def test_prune_refuses_without_archive(self):
        result = self.run_tool("prune")
        self.assertEqual(result.returncode, 2)
        self.assertIn("needs --archive", result.stdout)
        self.assertEqual(len(self.ids("analysis_runs")), len(self.expected_kept) + len(self.expected_pruned))

    def test_daily_mode_prunes_in_place_and_is_idempotent(self):
        first = self.run_tool("daily")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(self.ids("analysis_runs"), self.expected_kept)
        second = self.run_tool("daily")
        self.assertIn("deleted 0 rows", second.stdout)

    def test_only_churning_types_are_touched(self):
        self.run_tool("daily", "--churn-types", "science-interest")
        remaining = self.ids("analysis_runs")
        with sqlite3.connect(self.db) as con:
            anomaly = {row[0] for row in con.execute(
                "SELECT id FROM analysis_runs WHERE analysis_type = 'population-anomaly'")}
        self.assertEqual(len(anomaly), 20 * 5 + 1)
        self.assertTrue(anomaly <= remaining)

    def protect_certifications(self):
        # the live backend's own rule (found on soc.db 2026-09-26)
        with sqlite3.connect(self.db) as con:
            con.executescript("""
            CREATE TRIGGER certification_runs_no_delete BEFORE DELETE ON certification_runs
            BEGIN SELECT RAISE(ABORT, 'certification runs are immutable'); END;
            CREATE TRIGGER certification_runs_no_update BEFORE UPDATE ON certification_runs
            BEGIN SELECT RAISE(ABORT, 'certification runs are immutable'); END;""")

    def test_tables_the_database_protects_are_reported_not_pruned(self):
        self.protect_certifications()
        preview = self.run_tool("preview")
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn("certification_runs: 4 of 14 are past the retention policy", preview.stdout)
        self.assertIn("protects this table from deletion; left as they are", preview.stdout)
        archive = self.data / "_to_delete" / "pruned.ndjson.gz"
        result = self.run_tool("prune", "--archive", str(archive), "--vacuum")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.ids("analysis_runs"), self.expected_kept)
        self.assertEqual(self.ids("certification_runs"), self.cert_kept | self.cert_pruned)
        with gzip.open(archive, "rt") as handle:
            tables = {json.loads(line)["table"] for line in handle}
        self.assertEqual(tables, {"analysis_runs"})
        self.assertIn("quick_check=ok", result.stdout)
        daily = self.run_tool("daily")
        self.assertEqual(daily.returncode, 0, daily.stdout + daily.stderr)
        self.assertIn("deleted 0 rows", daily.stdout)

    def test_prune_after_a_partial_earlier_prune_finishes_the_job(self):
        # 2026-09-26: analysis rows were deleted, then the certification step stopped
        # the run before compaction. A second run must archive only what remains and compact.
        self.protect_certifications()
        first = self.run_tool("daily")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        archive = self.data / "_to_delete" / "second.ndjson.gz"
        second = self.run_tool("prune", "--archive", str(archive), "--vacuum")
        self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertIn("analysis_runs: prune 0 of", second.stdout)
        self.assertIn("deleted 0 rows", second.stdout)
        self.assertIn("quick_check=ok", second.stdout)
        with sqlite3.connect(self.db) as con:
            self.assertEqual(con.execute("PRAGMA auto_vacuum").fetchone()[0], 2)
            self.assertEqual(con.execute("PRAGMA freelist_count").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

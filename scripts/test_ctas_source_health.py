#!/usr/bin/env python3
"""Checks for scripts/ctas_source_health.py on a small synthetic collector database."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ctas_source_health import health, markdown  # noqa: E402

NOW = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE source_query_attempts (source_id TEXT, terminal_state TEXT, error_code TEXT, checked_at TEXT)")
    con.execute("CREATE TABLE alert_envelopes (provider TEXT, received_at TEXT)")
    rows = (
        [("aavso-aid", "failed", "AAVSO_AID_AUTH_FAILED", "2026-10-03 10:00:00")] * 12
        + [("atlas", "data", None, "2026-10-03 11:00:00")] * 5
        + [("atlas", "failed", "ATLAS_QUERY_FAILED", "2026-10-03 11:00:00")] * 7
        + [("mast", "failed", "MAST_ARCHIVE_QUERY_FAILED", "2026-10-03 11:30:00")] * 10
        + [("ads", "no-match", None, "2026-10-03 09:00:00")] * 20
        + [("ads", "failed", "ADS_QUERY_FAILED", "2026-10-01 09:00:00")] * 50  # outside the window
    )
    con.executemany("INSERT INTO source_query_attempts VALUES (?, ?, ?, ?)", rows)
    con.executemany("INSERT INTO alert_envelopes VALUES (?, ?)", [("gcn", "2026-10-03 08:00:00")] * 3)
    con.commit()
    con.close()


class SourceHealthTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "soc.db"
        _db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def test_flags_credentials_total_and_majority_failures(self):
        report = health(str(self.db), now=NOW)
        problems = " ".join(report["problems"])
        self.assertIn("aavso-aid: 12 attempt(s) refused for credentials", problems)
        self.assertIn("mast: every attempt failed (10)", problems)
        self.assertIn("atlas: 7 of 12 attempts failed", problems)
        self.assertNotIn("ads", problems, "only the last day counts")
        self.assertEqual(report["intake"], {"gcn": 3})

    def test_markdown_has_one_row_per_source_and_no_details(self):
        text = markdown(health(str(self.db), now=NOW))
        self.assertIn("| atlas | 12 | 5 | 0 | 7 | 0 | ATLAS_QUERY_FAILED (7) |", text)
        self.assertIn("Alerts received: gcn 3", text)
        self.assertNotIn("http", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)

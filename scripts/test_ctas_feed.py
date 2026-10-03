#!/usr/bin/env python3
"""Checks for scripts/ctas_feed.py (Atom feed of strong new follow-up targets)."""
from __future__ import annotations

import json
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ctas_feed  # noqa: E402

ATOM = "{http://www.w3.org/2005/Atom}"
COLUMNS = ["event_id", "name", "record_role", "discovery_time", "ctas_score", "classification",
           "discovery_magnitude", "discovery_survey", "ra_deg", "dec_deg", "redshift"]


def summary(rows):
    return {"candidate_columns": COLUMNS, "candidate_rows": rows}


ROWS = [
    ["e1", "SN 2026abc", "follow-up-target-candidate", "2026-10-01T05:00:00Z", 71.2, "SN Ia", 18.24, "ZTF", 10.5, -20.25, 0.03],
    ["e2", "AT 2026old", "follow-up-target-candidate", "2026-08-01T05:00:00Z", 75.0, None, 19.0, "ATLAS", 11, 12, None],
    ["e3", "AT 2026low", "follow-up-target-candidate", "2026-09-30T05:00:00Z", 41.0, None, 19.5, "GOTO", 12, 13, None],
    ["e4", "GRB 261001A", "localization-region-alert", "2026-10-01T06:00:00Z", 90.0, None, None, "Swift", 13, 14, None],
    ["e/5", "AT 2026<x>&", "follow-up-target-candidate", "2026-09-29T05:00:00Z", 60.0, None, None, None, None, None, None],
]


class FeedTests(unittest.TestCase):
    def test_selects_recent_strong_follow_up_targets_newest_first(self):
        chosen = ctas_feed.select_targets(summary(ROWS))
        self.assertEqual([row["event_id"] for row in chosen], ["e1", "e/5"])

    def test_feed_is_valid_atom_with_escaped_titles_and_dossier_links(self):
        root = ET.fromstring(ctas_feed.render(summary(ROWS)))
        entries = root.findall(f"{ATOM}entry")
        self.assertEqual(len(entries), 2)
        self.assertEqual(root.find(f"{ATOM}updated").text, "2026-10-01T05:00:00Z")
        first = entries[0]
        self.assertEqual(first.find(f"{ATOM}title").text, "SN 2026abc — SN Ia, 18.2 mag, ZTF (score 71)")
        self.assertEqual(first.find(f"{ATOM}link").get("href"),
                         "https://jackmcguireastro.github.io/ctas.html?event=e1#dossier")
        self.assertIn("RA 10.50000°, Dec -20.25000°", first.find(f"{ATOM}summary").text)
        second = entries[1]
        self.assertIn("AT 2026<x>&", second.find(f"{ATOM}title").text)
        self.assertTrue(second.find(f"{ATOM}link").get("href").endswith("event=e%2F5#dossier"))

    def test_same_targets_give_the_same_bytes(self):
        self.assertEqual(ctas_feed.render(summary(ROWS)), ctas_feed.render(summary(list(ROWS))))

    def test_empty_release_still_gives_a_valid_feed(self):
        root = ET.fromstring(ctas_feed.render(summary([])))
        self.assertEqual(root.findall(f"{ATOM}entry"), [])

    def test_published_summary_if_present(self):
        path = Path(__file__).resolve().parent.parent / "ctas" / "data" / "live-summary.json"
        if not path.exists():
            self.skipTest("no published summary in this checkout")
        root = ET.fromstring(ctas_feed.render(json.loads(path.read_text())))
        self.assertGreater(len(root.findall(f"{ATOM}entry")), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

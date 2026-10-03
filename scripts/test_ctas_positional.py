#!/usr/bin/env python3
"""Advisory positional neighbours in the CTAS export (no merging, no identity change)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import export_ctas_snapshot as export  # noqa: E402

TARGET = "follow-up-target-candidate"


def rec(event_id, ra, dec, role=TARGET, **extra):
    return {"event_id": event_id, "name": event_id.upper(), "record_role": role, "ra_deg": ra, "dec_deg": dec, **extra}


class PositionalNeighbourTests(unittest.TestCase):
    def test_pairs_within_two_arcseconds_are_reported_both_ways(self):
        found = export.positional_neighbours([rec("a", 10.0, 20.0), rec("b", 10.0 + 1.5 / 3600 / 0.93969, 20.0), rec("c", 10.01, 20.0)])
        self.assertEqual([row["event_id"] for row in found["a"]], ["b"])
        self.assertEqual([row["event_id"] for row in found["b"]], ["a"])
        self.assertAlmostEqual(found["a"][0]["separation_arcsec"], 1.5, places=1)
        self.assertNotIn("c", found)

    def test_only_point_like_follow_up_targets_take_part(self):
        found = export.positional_neighbours([rec("a", 10.0, 20.0), rec("grb", 10.0, 20.0, role="localization-region-alert"),
                                              rec("nopos", None, None)])
        self.assertEqual(found, {})

    def test_right_ascension_wraps_and_poles_are_fast(self):
        found = export.positional_neighbours([rec("e", 359.99995, 0.0), rec("f", 0.0001, 0.0),
                                              rec("p1", 0.0, 89.99995), rec("p2", 180.0, 89.99996)])
        self.assertIn("f", [row["event_id"] for row in found["e"]])
        self.assertIn("p2", [row["event_id"] for row in found["p1"]])

    def test_at_most_five_nearest_are_kept(self):
        rows = [rec("x", 50.0, -10.0)] + [rec(f"n{i}", 50.0, -10.0 + i * 0.2 / 3600) for i in range(1, 9)]
        found = export.positional_neighbours(rows)
        self.assertEqual([row["event_id"] for row in found["x"]], ["n1", "n2", "n3", "n4", "n5"])


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
"""Checks for scripts/ctas_feed.py: the Atom feed of strong new follow-up targets, and the
per-category and per-sky-region Atom/JSON Feed set of the release (Track 1, part C)."""
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
        summary = json.loads(path.read_text())
        root = ET.fromstring(ctas_feed.render(summary))
        if not ctas_feed.select_targets(summary):
            # The 24-event fixture release has no target above the score floor within the
            # window; a valid empty feed is the right answer for it, not a failure.
            self.skipTest("the published summary has no strong new follow-up target")
        self.assertGreater(len(root.findall(f"{ATOM}entry")), 0)


INDEX_COLUMNS = ["event_id", "name", "event_type", "primary_messenger", "ranking_channel", "record_role", "status", "classification",
                 "ra_deg", "dec_deg", "ctas_score", "discovery_time", "discovery_survey", "discovery_magnitude",
                 "latest_classification_at", "latest_spectrum_at", "latest_messenger_at", "latest_retraction_at", "detail_chunk"]


def index(rows):
    return {"candidate_columns": INDEX_COLUMNS, "candidate_rows": rows, "catalog_as_of": "2026-10-01T00:00:00Z",
            "catalog_content_checksum_sha256": "c" * 64}


INDEX_ROWS = [
    ["11111111-1111-4111-8111-111111111111", "SN 2026abc", "optical-transient", "electromagnetic", "optical", "follow-up-target-candidate", "confirmed", "SN Ia",
     10.5, 41.2, 71.2, "2026-09-20T05:00:00Z", "ZTF", 18.2, "2026-09-25T00:00:00Z", None, None, None, "candidate-chunks/111.json"],
    ["22222222-2222-4222-8222-222222222222", "GRB 260930A", "gamma-ray-burst-candidate", "gamma-ray", "x-ray-gamma-ray", "localization-region-alert", "candidate", None,
     266.4, -29.0, 60.0, "2026-09-30T01:00:00Z", "Swift", None, None, None, "2026-09-30T01:05:00Z", None, "candidate-chunks/222.json"],
    ["33333333-3333-4333-8333-333333333333", "IceCube-261001A", "high-energy-neutrino-track", "neutrino", "neutrino-multimessenger", "localization-region-alert", "candidate", None,
     None, None, 55.0, "2026-10-01T00:00:00Z", "IceCube", None, None, None, None, None, "candidate-chunks/333.json"],
]


class RegionTests(unittest.TestCase):
    def test_galactic_latitude_closed_form(self):
        self.assertAlmostEqual(ctas_feed.galactic_latitude_deg(192.85948, 27.12825), 90.0, places=3)
        self.assertAlmostEqual(ctas_feed.galactic_latitude_deg(266.405, -28.936), 0.0, places=1)  # Sgr A*
        self.assertAlmostEqual(ctas_feed.galactic_latitude_deg(12.85948, -27.12825), -90.0, places=3)

    def test_regions_for_a_position(self):
        regions = ctas_feed.regions_for(266.405, -28.936)
        self.assertEqual(regions[1], "south")
        self.assertIn("galactic-plane", regions)
        self.assertRegex(regions[0], r"^healpix2-\d{3}$")
        self.assertEqual(ctas_feed.regions_for(10.5, 41.2)[1:], ["north"])
        self.assertEqual(ctas_feed.regions_for(None, None), [])
        self.assertEqual(ctas_feed.regions_for("x", 1), [])
        self.assertEqual(len(ctas_feed.region_names()), 195)

    def test_healpix_order_two_nested_matches_astropy(self):
        from astropy import units as u
        from astropy_healpix import lonlat_to_healpix
        for ra, dec in ((0, 0), (359.9, 89.9), (180, -45), (266.405, -28.936), (90, 30)):
            expected = int(lonlat_to_healpix(ra * u.deg, dec * u.deg, 4, order="nested"))
            self.assertEqual(ctas_feed.healpix2_nested(ra, dec), expected)
            self.assertTrue(0 <= expected < 192)


class CategoryAndRegionFeedTests(unittest.TestCase):
    def setUp(self):
        self.files, self.index = ctas_feed.build_feeds(index(INDEX_ROWS), generated_at="2026-10-01T00:30:00Z")

    def test_fixed_set_of_feeds_every_one_valid(self):
        self.assertEqual(self.index["feed_count"], 1 + len(ctas_feed.EVENT_TYPES) + len(ctas_feed.RANKING_CHANNELS) + 195)
        self.assertEqual(len(self.files), 2 * self.index["feed_count"])
        self.assertEqual(ctas_feed.feed_problems(self.files, self.index), [])
        by_name = {row["name"]: row for row in self.index["feeds"]}
        self.assertEqual(by_name["all-events"]["item_count"], 3)
        self.assertEqual(by_name["optical-transient"]["item_count"], 1)
        self.assertEqual(by_name["neutrino-multimessenger"]["item_count"], 1)
        self.assertEqual(by_name["galactic-plane"]["item_count"], 1, "the GRB near the galactic centre")
        self.assertEqual(by_name["north"]["item_count"], 1)
        self.assertEqual(by_name["south"]["item_count"], 1, "the record without coordinates is in no region feed")

    def test_json_feed_members_and_extension(self):
        document = json.loads(self.files["ctas/data/feeds/all-events.json"])
        self.assertEqual(document["version"], "https://jsonfeed.org/version/1.1")
        self.assertEqual(document["feed_url"], "https://jackmcguireastro.github.io/ctas/data/feeds/all-events.json")
        items = document["items"]
        self.assertEqual([item["title"].split(" — ")[0] for item in items], ["IceCube-261001A", "GRB 260930A", "SN 2026abc"], "newest public change first")
        first = items[-1]
        self.assertEqual(first["id"], "urn:ctas:event:11111111-1111-4111-8111-111111111111")
        self.assertEqual(first["date_published"], "2026-09-20T05:00:00Z")
        self.assertEqual(first["date_modified"], "2026-09-25T00:00:00Z", "the classification clock is the latest public change")
        self.assertIn("SN Ia", first["tags"])
        self.assertEqual(first["_ctas"]["regions"][1], "north")
        self.assertTrue(first["content_text"].endswith("inclusion is not confirmation."))
        self.assertTrue(all(key.startswith("_") or key in {"id", "url", "title", "content_text", "tags", "date_published", "date_modified"} for item in items for key in item))

    def test_atom_entries_carry_ids_updated_and_categories(self):
        root = ET.fromstring(self.files["ctas/data/feeds/channel/x-ray-gamma-ray.xml"])
        self.assertEqual(root.find(f"{ATOM}id").text, "https://jackmcguireastro.github.io/ctas/data/feeds/channel/x-ray-gamma-ray.xml")
        entries = root.findall(f"{ATOM}entry")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].find(f"{ATOM}id").text, "urn:ctas:event:22222222-2222-4222-8222-222222222222")
        self.assertEqual(entries[0].find(f"{ATOM}updated").text, "2026-09-30T01:05:00Z")
        self.assertIn("gamma-ray-burst-candidate", [c.get("term") for c in entries[0].findall(f"{ATOM}category")])
        empty = ET.fromstring(self.files["ctas/data/feeds/event-type/solar-flare.xml"])
        self.assertEqual(empty.findall(f"{ATOM}entry"), [])
        self.assertIsNotNone(empty.find(f"{ATOM}updated"))

    def test_feed_bytes_carry_no_release_clock(self):
        other, _ = ctas_feed.build_feeds(index(list(reversed(INDEX_ROWS))), generated_at="2026-12-24T23:59:59Z")
        self.assertEqual(self.files, other)
        self.assertEqual(self.index["generated_at"], "2026-10-01T00:30:00Z")

    def test_item_limit_keeps_the_newest(self):
        rows = [["%08d-0000-4000-8000-000000000000" % i, f"AT{i}", "optical-transient", "electromagnetic", "optical", "follow-up-target-candidate", "candidate", None,
                 1.0, 1.0, 40.0, f"2026-0{1 + i // 28 % 9}-{1 + i % 28:02d}T00:00:00Z", "ZTF", None, None, None, None, None, "candidate-chunks/000.json"] for i in range(130)]
        files, document = ctas_feed.build_feeds(index(rows))
        by_name = {row["name"]: row for row in document["feeds"]}
        self.assertEqual(by_name["all-events"]["member_count"], 130)
        self.assertEqual(by_name["all-events"]["item_count"], ctas_feed.FEED_ITEMS)
        items = json.loads(files["ctas/data/feeds/all-events.json"])["items"]
        self.assertEqual(items[0]["date_published"], max(row[11] for row in rows))

    def test_problems_are_named(self):
        broken = dict(self.files)
        broken["ctas/data/feeds/all-events.json"] = b"{}"
        self.assertTrue(any("index metadata" in problem for problem in ctas_feed.feed_problems(broken, self.index)))

    def test_event_types_and_channels_match_the_exporter(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("ctas_exporter", Path(__file__).resolve().parent / "export_ctas_snapshot.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        self.assertEqual(set(ctas_feed.EVENT_TYPES), set(exporter.RECORD_ROLE_BY_EVENT_TYPE))
        channels = {channel for _role, channel in exporter.RECORD_ROLE_BY_EVENT_TYPE.values()} | {exporter.RECORD_ROLE_FALLBACK[1]}
        self.assertEqual(set(ctas_feed.RANKING_CHANNELS), channels)

    def test_published_feeds_validate_if_present(self):
        root = Path(__file__).resolve().parent.parent
        index_path = root / "ctas/data/feeds/index.json"
        if not index_path.exists():
            self.skipTest("no published feeds in this checkout")
        document = json.loads(index_path.read_text())
        files = {}
        for row in document["feeds"]:
            for key in ("json_feed", "atom"):
                files[row[key]["path"]] = (root / row[key]["path"]).read_bytes()
        self.assertEqual(ctas_feed.feed_problems(files, document), [])
        self.assertEqual(document["feed_count"], 214)


if __name__ == "__main__":
    unittest.main(verbosity=2)

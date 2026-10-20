#!/usr/bin/env python3
"""Checks for scripts/ctas_changelog.py (the plain-words release changelog)."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ctas_changelog as C  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def candidate(event_id, name, **extra):
    row = {"event_id": event_id, "name": name, "event_type": "optical-transient", "primary_messenger": "electromagnetic",
           "discovery_survey": "ZTF", "classification": "Unclassified", "status": "candidate", "latest_retraction_at": None}
    row.update(extra)
    return row


def universe(rows):
    return {"sources": [{"source_key": key, "name": name, "operational_state": state, "public_record_counts": counts}
                        for key, name, state, counts in rows]}


PREVIOUS = [
    candidate("e1", "AT2026zxx"),
    candidate("e2", "AT2026abc"),
    candidate("e3", "AT2026gone"),
    candidate("e4", "SN2026old", classification="SN II", status="confirmed"),
    candidate("e5", "GRB 261001A", event_type="gamma-ray-burst-candidate"),
]
CURRENT = [
    candidate("e1", "SN2026zxx", classification="SN Ia", status="confirmed"),     # rename + new classification
    candidate("e2", "AT2026abc", status="retracted"),                              # retraction by status
    candidate("e4", "SN2026old", classification="SN IIb", status="confirmed"),    # revised classification
    candidate("e5", "GRB 261001A", event_type="gamma-ray-burst-candidate", latest_retraction_at="2026-10-02T00:00:00Z"),  # retraction notice
    candidate("e6", "AT2026new", discovery_survey="ATLAS"),                        # new
    candidate("e7", "AT2026new2"),                                                 # new
]
UNIVERSE_BEFORE = universe([("tns", "TNS", "active-returning-data", {"aliases": 5}), ("ztf", "ZTF", "rate-limited", {}), ("mast", "MAST", "scheduled", {})])
UNIVERSE_AFTER = universe([("tns", "TNS", "rate-limited", {"aliases": 6}), ("ztf", "ZTF", "active-returning-data", {"photometry": 12}), ("mast", "MAST", "not-searched", {}), ("new-src", "New source", "scheduled", {})])


def build(previous=PREVIOUS, current=CURRENT, before=UNIVERSE_BEFORE, after=UNIVERSE_AFTER, prev_acc=None, acc=None, published="2026-10-19T12:00:00Z"):
    return C.build_entry(previous_candidates=previous, current_candidates=current, previous_universe=before, current_universe=after,
                         previous_accounting={"dataBearingSourceEvaluations": 900, "executedQueryReceipts": 9000} if prev_acc is None else prev_acc,
                         current_accounting={"dataBearingSourceEvaluations": 1100, "executedQueryReceipts": 20500} if acc is None else acc,
                         published_at=published, catalog_content_checksum_sha256="a" * 64, previous_catalog_content_checksum_sha256="b" * 64,
                         base_commit="deadbeef")


class ChangelogEntryTests(unittest.TestCase):
    def test_changes_are_keyed_by_event_id_so_a_rename_is_a_rename(self):
        entry = build()
        self.assertEqual([row["event_id"] for row in entry["new_events"]], ["e6", "e7"])
        self.assertEqual(entry["removed_events"], [{"event_id": "e3", "name": "AT2026gone"}])
        self.assertEqual(entry["renames"], [{"event_id": "e1", "previous_name": "AT2026zxx", "name": "SN2026zxx"}])
        kinds = {row["event_id"]: row["kind"] for row in entry["classification_changes"]}
        self.assertEqual(kinds, {"e1": "new", "e4": "revised"})
        self.assertEqual({row["event_id"]: row["basis"] for row in entry["retractions"]}, {"e2": "status", "e5": "retraction-notice"})
        directions = {row["source_key"]: row["direction"] for row in entry["source_state_changes"]}
        self.assertEqual(directions, {"tns": "degraded", "ztf": "recovered", "mast": "changed"})
        kinds = [row["kind"] for row in entry["coverage_milestones"]]
        self.assertIn("dataBearingSourceEvaluations", kinds)
        self.assertIn("executedQueryReceipts", kinds)
        self.assertIn("first-retained-records", kinds)
        self.assertIn("source-contracts-declared", kinds)
        self.assertEqual(entry["new_event_count"], 2)
        self.assertEqual(entry["candidate_count"], 6)
        self.assertEqual(entry["previous_candidate_count"], 5)

    def test_plain_words_are_fixed_templates_over_the_counts(self):
        words = build()["summary"]
        self.assertIn("2 new public records (1 from ATLAS, 1 from ZTF): AT2026new, AT2026new2.", words)
        self.assertIn("1 record left the public catalog: AT2026gone.", words)
        self.assertIn("1 new classification: SN2026zxx is now reported as SN Ia.", words)
        self.assertIn("1 revised classification: SN2026old from SN II to SN IIb.", words)
        self.assertIn("2 retractions: AT2026abc, GRB 261001A.", words)
        self.assertIn("1 rename (same event id): AT2026zxx is now SN2026zxx.", words)
        self.assertIn("1 source went degraded: TNS (active-returning-data to rate-limited).", words)
        self.assertIn("1 source recovered: ZTF (rate-limited to active-returning-data).", words)
        self.assertTrue(any(w.startswith("CTAS passed 1,000 source evaluations that returned retained data") for w in words), words)
        self.assertTrue(any(w.startswith("CTAS passed 10,000 executed source-query receipts") for w in words), words)
        self.assertTrue(any(w.startswith("CTAS passed 20,000 executed source-query receipts") for w in words), words)
        self.assertIn("ZTF contributed its first retained public records (12).", words)
        self.assertIn("1 new source contract declared: New source.", words)

    def test_catalog_size_milestone_only_when_a_thousand_is_crossed(self):
        big_before = [candidate(f"p{i}", f"AT{i}") for i in range(998)]
        big_after = big_before + [candidate("q1", "ATq1"), candidate("q2", "ATq2"), candidate("q3", "ATq3")]
        entry = build(previous=big_before, current=big_after, before=UNIVERSE_BEFORE, after=UNIVERSE_BEFORE,
                      prev_acc={}, acc={})
        texts = [row["text"] for row in entry["coverage_milestones"]]
        self.assertEqual(texts, ["The public catalog passed 1,000 records (998 to 1,001)."])
        entry = build(previous=big_before, current=big_before[:-1] + [candidate("q1", "ATq1")], before=UNIVERSE_BEFORE, after=UNIVERSE_BEFORE, prev_acc={}, acc={})
        self.assertEqual(entry["coverage_milestones"], [])

    def test_deterministic_and_clock_free_apart_from_the_release_time(self):
        first, second = build(published="2026-10-19T12:00:00Z"), build(published="2026-10-19T12:30:00Z")
        self.assertEqual(first["entry_id"], second["entry_id"])
        self.assertEqual(json.dumps({k: v for k, v in first.items() if k != "published_at"}, sort_keys=True),
                         json.dumps({k: v for k, v in second.items() if k != "published_at"}, sort_keys=True))
        shuffled = build(previous=list(reversed(PREVIOUS)), current=list(reversed(CURRENT)))
        self.assertEqual(shuffled["entry_id"], first["entry_id"])

    def test_no_previous_release_or_no_change_gives_no_entry(self):
        self.assertIsNone(build(previous=None))
        self.assertIsNone(build(previous=PREVIOUS, current=copy.deepcopy(PREVIOUS), before=UNIVERSE_BEFORE, after=UNIVERSE_BEFORE,
                                prev_acc={"dataBearingSourceEvaluations": 1, "executedQueryReceipts": 1}, acc={"dataBearingSourceEvaluments": 1}))

    def test_lists_are_capped_but_counts_are_complete(self):
        many = [candidate(f"n{i}", f"AT2026n{i:04d}") for i in range(C.LIST_LIMIT + 25)]
        entry = build(previous=[], current=many, before=UNIVERSE_BEFORE, after=UNIVERSE_BEFORE, prev_acc={}, acc={})
        self.assertEqual(len(entry["new_events"]), C.LIST_LIMIT)
        self.assertEqual(entry["new_event_count"], C.LIST_LIMIT + 25)
        self.assertIn("and 220 more", entry["summary"][0])


class CarryForwardTests(unittest.TestCase):
    def test_entries_are_carried_forward_newest_first_without_repeats(self):
        entry = build()
        first = C.carry_forward(None, entry, generated_at="2026-10-19T12:00:00Z")
        self.assertEqual(first["schema"], C.CHANGELOG_SCHEMA)
        self.assertEqual(first["entry_count"], 1)
        again = C.carry_forward(first, build(published="2026-10-19T12:30:00Z"), generated_at="2026-10-19T12:30:00Z")
        self.assertEqual(again["entry_count"], 1, "the same comparison is not recorded twice")
        other = build(current=CURRENT + [candidate("e8", "AT2026more")], published="2026-10-19T13:00:00Z")
        grown = C.carry_forward(again, other, generated_at="2026-10-19T13:00:00Z")
        self.assertEqual([row["published_at"] for row in grown["entries"]], ["2026-10-19T13:00:00Z", "2026-10-19T12:00:00Z"])
        self.assertEqual(grown["generated_at"], "2026-10-19T13:00:00Z")
        self.assertEqual(C.changelog_problems(grown), [])

    def test_a_foreign_or_broken_previous_changelog_is_ignored(self):
        document = C.carry_forward({"schema": "something-else", "entries": [{"entry_id": "x"}]}, None, generated_at="2026-10-19T12:00:00Z")
        self.assertEqual(document["entries"], [])
        self.assertEqual(document["generated_at"], "2026-10-19T12:00:00Z")

    def test_at_most_keep_entries(self):
        document = None
        for index in range(C.KEEP_ENTRIES + 5):
            entry = build(current=CURRENT + [candidate(f"x{index}", f"AT2026x{index}")], published=f"2026-10-{(index % 28) + 1:02d}T00:00:00Z")
            document = C.carry_forward(document, entry, generated_at=entry["published_at"])
        self.assertEqual(document["entry_count"], C.KEEP_ENTRIES)

    def test_problems_name_tampering(self):
        document = C.carry_forward(None, build(), generated_at="2026-10-19T12:00:00Z")
        tampered = copy.deepcopy(document)
        tampered["entries"][0]["new_event_count"] = 99
        self.assertTrue(any("entry_id does not reproduce" in problem for problem in C.changelog_problems(tampered)))
        duplicated = copy.deepcopy(document)
        duplicated["entries"].append(copy.deepcopy(duplicated["entries"][0]))
        duplicated["entry_count"] = 2
        self.assertTrue(any("duplicate" in problem for problem in C.changelog_problems(duplicated)))


class PublishedChangelogTests(unittest.TestCase):
    def test_published_changelog_validates_if_present(self):
        path = ROOT / "ctas/data/changelog.json"
        if not path.exists():
            self.skipTest("no generated release in this checkout")
        document = json.loads(path.read_text())
        self.assertEqual(C.changelog_problems(document), [])
        self.assertEqual(document["schema"], C.CHANGELOG_SCHEMA)


if __name__ == "__main__":
    unittest.main(verbosity=2)

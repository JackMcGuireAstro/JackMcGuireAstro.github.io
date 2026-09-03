#!/usr/bin/env python3
"""Regression tests for CTAS cross-survey identity resolution.

Two separately designated TNS objects were being associated into a single CTAS
record by the coordinate-and-time matcher (all 14 known cases are closer than
3.0 arcsec and within 30 days, the configured association window).  The export
must never turn such a record into a confident object-specific link: it fails
closed, keeps both designations as provenance, and says the identity is
unresolved.

The fixture is real retained public data, reduced to the 14 unresolved records
plus a control sample, so these assertions exercise the production code path.
"""
from __future__ import annotations

import gzip
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_ctas_links  # noqa: E402
from export_ctas_snapshot import (  # noqa: E402
    conflicting_tns_object_ids,
    export,
    tns_object_id,
)

FIXTURE = ROOT / "tests" / "fixtures" / "ctas-identity.db.gz"

# The 14 records whose retained TNS designations disagree.  Each pair is two
# real TNS objects with distinct survey internal names, so neither designation
# may be published as *the* object for the record.
UNRESOLVED = {
    "AT2015cf/SN2015ca", "AT2019skc/SN2019bml", "AT2022abcj/AT2022zmd",
    "AT2019qgr/SN2019ovg", "AT2019wtq/SN2019wlx", "AT2022hkk/AT2022zwf",
    "AT2024dtc/SN2024drv", "AT2022aanc/AT2022abck", "AT2021gf/SN2021do",
    "AT2024aasg/AT2024xbi", "AT2026bzt/AT2026gtw", "AT2019uuu/SN2019ukq",
    "AT2019myq/SN2019lyb", "AT2019zri/SN2019zck",
}


class _Snapshot:
    candidates: list[dict] = []

    @classmethod
    def build(cls) -> list[dict]:
        if cls.candidates:
            return cls.candidates
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "fixture.db"
            with gzip.open(FIXTURE, "rb") as source, database.open("wb") as target:
                shutil.copyfileobj(source, target)
            cls.candidates, _counts = export(database, 0)
        return cls.candidates


class IdentityResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.candidates = _Snapshot.build()
        cls.unresolved = [
            candidate for candidate in cls.candidates
            if (candidate.get("identity_resolution") or {}).get("provider_designation_conflicts")
        ]

    def test_fixture_carries_every_known_unresolved_record(self):
        found = set()
        for candidate in self.unresolved:
            designations = sorted(
                str(row["designation"])
                for row in candidate.get("designations", [])
                if row.get("source_key") == "tns" and row.get("is_preferred")
            )
            found.add("/".join(designations))
        self.assertEqual(found, UNRESOLVED)

    def test_unresolved_records_publish_no_object_specific_tns_link(self):
        for candidate in self.unresolved:
            with self.subTest(candidate=candidate["name"]):
                tns_links = [
                    row for row in candidate.get("links", [])
                    if row.get("source_key") == "tns"
                ]
                self.assertEqual(tns_links, [], "a contested TNS object must not render a link")

    def test_unresolved_records_keep_both_designations_as_provenance(self):
        for candidate in self.unresolved:
            with self.subTest(candidate=candidate["name"]):
                preferred = [
                    row for row in candidate.get("designations", [])
                    if row.get("source_key") == "tns" and row.get("is_preferred")
                ]
                self.assertGreater(len(preferred), 1)
                self.assertEqual(
                    candidate["identity_resolution"]["state"], "CONFLICTING",
                    "an unresolved association must be stated, not hidden",
                )

    def test_source_coverage_never_points_at_another_object(self):
        """A coverage row carries no designation, so a mismatch reads as this record."""
        for candidate in self.candidates:
            own = tns_object_id(str(candidate.get("name") or ""))
            if not own:
                continue
            for row in candidate.get("source_coverage", []):
                url = row.get("object_specific_result_url") or ""
                if not url.startswith("https://www.wis-tns.org/object/"):
                    continue
                with self.subTest(candidate=candidate["name"], url=url):
                    self.assertEqual(url.rsplit("/", 1)[-1], own)

    def test_resolved_records_still_publish_their_own_tns_object(self):
        resolved = [
            candidate for candidate in self.candidates
            if candidate not in self.unresolved
            and any(row.get("source_key") == "tns" for row in candidate.get("links", []))
        ]
        self.assertGreater(len(resolved), 0, "the control sample must keep working links")
        for candidate in resolved:
            with self.subTest(candidate=candidate["name"]):
                for row in candidate["links"]:
                    if row.get("source_key") != "tns":
                        continue
                    self.assertEqual(
                        row["url"],
                        "https://www.wis-tns.org/object/"
                        + str(tns_object_id(str(row["designation"]))),
                    )

    def test_recursive_link_audit_reports_no_structural_problem(self):
        universe_path = ROOT / "ctas" / "data" / "source-universe.json"
        universe = json.loads(universe_path.read_text()) if universe_path.exists() else None
        _audit, tns_links, problems = check_ctas_links.audit_links(
            {"candidates": self.candidates}, universe
        )
        self.assertEqual(problems, [], f"structural link problems: {problems[:5]}")
        self.assertGreater(len(tns_links), 0)

    def test_helper_only_counts_preferred_designations(self):
        rows = [
            {"provider": "tns", "external_id": "AT2022hkk", "is_preferred": 1},
            {"provider": "tns", "external_id": "AT2022zwf", "is_preferred": 1},
            {"provider": "tns", "external_id": "SN2022hkk", "is_preferred": 0},
            {"provider": "gcn", "external_id": "GRB 260101A", "is_preferred": 1},
        ]
        self.assertEqual(conflicting_tns_object_ids(rows), ["2022hkk", "2022zwf"])
        self.assertEqual(
            conflicting_tns_object_ids([rows[0], rows[2], rows[3]]), ["2022hkk"]
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

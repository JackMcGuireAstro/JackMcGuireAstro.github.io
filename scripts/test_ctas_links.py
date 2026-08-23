#!/usr/bin/env python3
"""Focused tests for the recursive CTAS external-link audit."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ctas_links", ROOT / "scripts/check_ctas_links.py")
assert SPEC and SPEC.loader
LINKS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(LINKS)


class RecursiveAuditTests(unittest.TestCase):
    def snapshot(self):
        return {
            "candidates": [{
                "name": "AT2026abc",
                "links": [{
                    "source_key": "tns", "url": "https://www.wis-tns.org/object/2026abc",
                }],
                "follow_up": {
                    "observations": [{"source_url": "https://api.ztf.fink-portal.org/"}],
                    "messenger_signals": [{"skymap_url": "http://example.invalid/map.fits.gz"}],
                    "publications": [{"canonical_url": "https://example.invalid/circular"}],
                },
            }],
            "sources": [{"documentation_url": "https://doc.lsst.fink-broker.org/services/summary/"}],
        }

    def test_nested_urls_are_all_counted_and_suppressed_urls_do_not_fail(self):
        audit, tns, problems = LINKS.audit_links(self.snapshot(), {"sources": []})
        self.assertEqual(audit["url_occurrence_count"], 5)
        self.assertEqual(audit["renderability_counts"]["https-allowlisted-renderable"], 3)
        self.assertEqual(audit["renderability_counts"]["suppressed-insecure"], 1)
        self.assertEqual(audit["renderability_counts"]["suppressed-unallowlisted"], 1)
        self.assertFalse(problems)
        self.assertEqual({row["object_id"] for row in tns}, {"2026abc"})

    def test_roles_distinguish_object_record_artifact_query_docs_and_generic(self):
        cases = {
            "exact-object": {"field": "url", "url": "https://fink-portal.org/ZTF26abcdef"},
            "exact-record": {"field": "canonical_url", "url": "https://gcn.nasa.gov/circulars/45431"},
            "artifact": {"field": "skymap_url", "url": "https://example.invalid/map.fits.gz"},
            "query": {"field": "source_url", "url": "https://irsa.ipac.caltech.edu/cgi-bin/Gator/nph-query?objstr=x"},
            "documentation": {"field": "documentation_url", "url": "https://doc.lsst.fink-broker.org/services/summary/"},
            "generic-reference": {"field": "source_url", "url": "https://gcn.nasa.gov/notices"},
        }
        for expected, occurrence in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(LINKS.link_role(occurrence), expected)

    def test_api_root_named_query_evidence_is_still_generic(self):
        occurrence = {
            "field": "query_evidence_url",
            "url": "https://api.ztf.fink-portal.org/",
        }
        self.assertEqual(LINKS.link_role(occurrence), "generic-reference")

    def test_fink_human_object_pages_are_allowlisted(self):
        state, reason = LINKS.renderability("https://fink-portal.org/ZTF26abcdef")
        self.assertEqual(state, "https-allowlisted-renderable")
        self.assertIsNone(reason)

    def test_tns_object_identity_is_strict(self):
        snapshot = self.snapshot()
        snapshot["candidates"][0]["links"][0]["url"] = "https://www.wis-tns.org/object/2026xyz"
        _, _, problems = LINKS.audit_links(snapshot, None)
        self.assertTrue(any("does not match" in problem for problem in problems))

    def test_tns_link_matches_declared_designation(self):
        snapshot = self.snapshot()
        snapshot["candidates"][0]["name"] = "ZTF26abcdef"
        snapshot["candidates"][0]["links"][0]["designation"] = "AT2026xyz"
        _, _, problems = LINKS.audit_links(snapshot, None)
        self.assertTrue(any("declared designation" in problem for problem in problems))

    def test_tns_query_fragment_is_not_canonical(self):
        occurrence = {
            "url": "https://www.wis-tns.org/object/2026abc?download=1",
            "candidate_name": "AT2026abc", "source_key": "tns", "field": "url",
        }
        canonical, problem = LINKS.validate_tns_object(occurrence)
        self.assertIsNone(canonical)
        self.assertEqual(problem, "malformed TNS object link")


if __name__ == "__main__":
    unittest.main(verbosity=2)

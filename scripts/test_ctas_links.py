#!/usr/bin/env python3
"""Focused tests for the recursive CTAS external-link audit."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import tempfile
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

    def test_lasair_human_object_pages_are_allowlisted_and_object_specific(self):
        url = "https://lasair-ztf.lsst.ac.uk/object/ZTF26abcdef"
        state, reason = LINKS.renderability(url)
        self.assertEqual(state, "https-allowlisted-renderable")
        self.assertIsNone(reason)
        self.assertEqual(LINKS.link_role({"field": "source_url", "url": url}), "exact-object")

    def test_lasair_non_object_paths_are_not_promoted_to_object_pages(self):
        url = "https://lasair-ztf.lsst.ac.uk/object/not-a-ztf-id"
        self.assertEqual(LINKS.link_role({"field": "source_url", "url": url}), "generic-reference")

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

    def test_related_tns_object_is_bound_to_its_own_external_id(self):
        snapshot = self.snapshot()
        snapshot["candidates"][0]["follow_up"]["publication_revisions"] = [{
            "related_objects": [{
                "external_id": "2026xyz",
                "source_url": "https://www.wis-tns.org/object/2026xyz",
            }],
        }]
        _, tns, problems = LINKS.audit_links(snapshot, None)
        self.assertFalse(problems)
        self.assertIn("2026xyz", {row["object_id"] for row in tns})

        snapshot["candidates"][0]["follow_up"]["publication_revisions"][0][
            "related_objects"
        ][0]["source_url"] = "https://www.wis-tns.org/object/2026wrong"
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

    def test_noncanonical_tns_object_url_is_suppressed_not_rendered(self):
        malformed = "https://www.wis-tns.org/object/At%202026abc"
        state, reason = LINKS.renderability(malformed)
        self.assertEqual(state, "suppressed-malformed")
        self.assertIn("canonical public object path", reason)

        snapshot = self.snapshot()
        snapshot["candidates"][0]["follow_up"]["publication_revisions"] = [{
            "related_objects": [{
                "external_id": "2026abc",
                "source_url": malformed,
            }],
        }]
        audit, _, problems = LINKS.audit_links(snapshot, None)
        self.assertFalse(problems)
        self.assertEqual(audit["renderability_counts"]["suppressed-malformed"], 1)

    def test_partitioned_catalog_is_checksum_verified_and_reordered_by_index(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data = root / "ctas/data"
            chunks = data / "candidate-chunks"
            chunks.mkdir(parents=True)
            first = {"event_id": "first", "name": "AT2026first", "links": []}
            second = {"event_id": "second", "name": "AT2026second", "links": []}
            documents = {
                "ctas/data/candidate-chunks/00.json": {
                    "schema": "ctas.public-candidate-chunk@1.0.0", "bucket": "00",
                    "candidate_count": 1, "candidates": [first],
                },
                "ctas/data/candidate-chunks/01.json": {
                    "schema": "ctas.public-candidate-chunk@1.0.0", "bucket": "01",
                    "candidate_count": 1, "candidates": [second],
                },
            }
            chunk_rows = []
            for relative, document in documents.items():
                raw = (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode()
                (root / relative).write_bytes(raw)
                chunk_rows.append({
                    "path": relative, "candidate_count": 1, "bytes": len(raw),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                })
            index = {"candidate_count": 2, "candidates": [
                {"event_id": "second"}, {"event_id": "first"},
            ]}
            index_raw = (json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n").encode()
            index_path = data / "catalog-index.json"
            index_path.write_bytes(index_raw)
            ordered = [second, first]
            canonical = (json.dumps(ordered, sort_keys=True, separators=(",", ":")) + "\n").encode()
            manifest = {
                "schema": "ctas.public-complete-catalog-manifest@1.0.0",
                "candidate_count": 2, "chunk_count": 2,
                "catalog_index": {
                    "path": "ctas/data/catalog-index.json", "bytes": len(index_raw),
                    "sha256": hashlib.sha256(index_raw).hexdigest(),
                },
                "assembled_candidates_checksum_sha256": hashlib.sha256(canonical).hexdigest(),
                "chunks": chunk_rows,
            }
            manifest_path = chunks / "manifest.json"
            manifest_path.write_text(json.dumps(manifest))
            reconstructed = LINKS.load_partitioned_catalog(index_path, manifest_path)
            self.assertEqual(reconstructed["candidates"], ordered)

            (root / chunk_rows[0]["path"]).write_bytes(b"tampered\n")
            with self.assertRaisesRegex(ValueError, "byte length mismatch"):
                LINKS.load_partitioned_catalog(index_path, manifest_path)


if __name__ == "__main__":
    unittest.main(verbosity=2)

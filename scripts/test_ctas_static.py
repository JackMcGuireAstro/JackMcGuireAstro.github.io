#!/usr/bin/env python3
"""Focused tests for the public CTAS static-catalog contract and artifacts."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ctas_exporter", ROOT / "scripts/export_ctas_snapshot.py")
assert SPEC and SPEC.loader
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def event(**updates):
    candidate = {
        "name": "AT2026abc",
        "ra_deg": 10.0,
        "dec_deg": -20.0,
        "discovery_time": "2026-08-22T00:00:00Z",
        "discovery_survey": "ZTF",
        "discovery_magnitude": 17.2,
        "classification": "Unclassified",
        "ctas_score": 61.0,
        "event_type": "optical-transient",
        "primary_messenger": "electromagnetic",
        "updated_at": "2026-08-23T00:00:00Z",
        "follow_up_counts": {
            "classifications": 0, "classification_history": 0, "observations": 0,
            "spectra": 0, "messenger_signals": 0, "publications": 0,
            "host_context": 0, "catalog_counterparts": 0, "archive_products": 0,
        },
        "follow_up_total": 0,
    }
    candidate.update(updates)
    return candidate


class CompletenessTests(unittest.TestCase):
    def test_event_only_is_defined_by_zero_retained_follow_up(self):
        result = EXPORTER.completeness_for(event())
        self.assertEqual(result["label"], "Event record only")

    def test_complete_optical_candidate_is_rich(self):
        candidate = event(
            classification="SN Ia", host_name="NGC 1",
            links=[{"source_key": "tns", "url": "https://www.wis-tns.org/object/2026abc"}],
            source_coverage=[{"source_id": "tns"}],
            follow_up_counts={
                "classifications": 1, "classification_history": 0, "observations": 2,
                "spectra": 1, "messenger_signals": 0, "publications": 1,
                "host_context": 1, "catalog_counterparts": 0, "archive_products": 0,
            },
            follow_up_total=6,
        )
        self.assertEqual(EXPORTER.completeness_for(candidate)["label"], "Rich public record")

    def test_no_coordinates_is_honest(self):
        candidate = event(ra_deg=None, dec_deg=None)
        component = {row["id"]: row for row in EXPORTER.completeness_for(candidate)["components"]}
        self.assertEqual(component["coordinates"]["state"], "missing")

    def test_non_em_notice_does_not_require_optical_spectrum(self):
        candidate = event(event_type="neutrino-alert", primary_messenger="neutrino", discovery_magnitude=None)
        component = {row["id"]: row for row in EXPORTER.completeness_for(candidate)["components"]}
        self.assertEqual(component["spectrum"]["state"], "not-applicable")
        self.assertEqual(component["follow-up-photometry"]["state"], "not-applicable")

    def test_spectrum_marks_component_present(self):
        candidate = event(
            follow_up_counts={**event()["follow_up_counts"], "spectra": 1}, follow_up_total=1,
        )
        component = {row["id"]: row for row in EXPORTER.completeness_for(candidate)["components"]}
        self.assertEqual(component["spectrum"]["state"], "present")

    def test_score_and_completeness_are_independent(self):
        low, high = event(ctas_score=1), event(ctas_score=99)
        self.assertEqual(EXPORTER.completeness_for(low), EXPORTER.completeness_for(high))

    def test_catalog_context_is_not_promoted_to_host_context(self):
        candidate = event(
            follow_up_counts={**event()["follow_up_counts"], "catalog_counterparts": 1},
            follow_up_total=1,
        )
        components = {row["id"]: row for row in EXPORTER.completeness_for(candidate)["components"]}
        self.assertEqual(components["catalog-context"]["state"], "present")
        self.assertEqual(components["host-context"]["state"], "missing")

    def test_nested_evidence_link_counts_as_source_link(self):
        candidate = event(
            follow_up={"observations": [{"source_url": "https://fink-portal.org/ZTF26abc"}]},
            follow_up_counts={**event()["follow_up_counts"], "observations": 1},
            follow_up_total=1,
        )
        components = {row["id"]: row for row in EXPORTER.completeness_for(candidate)["components"]}
        self.assertEqual(components["source-links"]["state"], "present")


class DispositionAndLinkTests(unittest.TestCase):
    def test_source_dispositions_remain_distinct(self):
        self.assertEqual(EXPORTER.public_attempt_disposition("no-match"), "searched-no-match")
        self.assertEqual(EXPORTER.public_attempt_disposition("unavailable"), "temporarily-unavailable")
        self.assertEqual(EXPORTER.public_attempt_disposition("blocked-rights"), "rights-blocked")
        self.assertEqual(EXPORTER.public_attempt_disposition("ambiguous"), "ambiguous-identity")
        self.assertEqual(EXPORTER.public_attempt_disposition("indeterminate"), "incomplete-result")
        self.assertEqual(EXPORTER.public_attempt_disposition("unavailable", "TNS_IDENTITY_UNAVAILABLE"), "not-searched")

    def test_valid_and_invalid_tns_designations(self):
        self.assertEqual(EXPORTER.tns_object_id("SN2026abc"), "2026abc")
        self.assertEqual(EXPORTER.tns_object_id("AT2026abc"), "2026abc")
        self.assertIsNone(EXPORTER.tns_object_id("IceCube-2026"))
        self.assertIsNone(EXPORTER.tns_object_id("2026abc/other"))

    def test_recursive_secret_and_path_rejection(self):
        self.assertTrue(EXPORTER.recursive_safety_problems({"nested": {"api_key": "x"}}))
        self.assertTrue(EXPORTER.recursive_safety_problems({"nested": "/Users/private/file"}))
        self.assertFalse(EXPORTER.recursive_safety_problems({"components": [{"id": "spectrum"}]}))

    def test_score_explanation_describes_coverage_as_a_reduction(self):
        explanation = EXPORTER.score_explanation_for(event(score_factors={"coverage_reduction": 7.5}))
        self.assertIn("reduces the score by 7.5 points", explanation)
        self.assertNotIn("coverage adds", explanation)

    def test_runtime_errors_are_sanitized_for_public_status(self):
        detail = EXPORTER.public_runtime_detail("ConnectError: CERTIFICATE_VERIFY_FAILED; retry in 19.4s")
        self.assertEqual(
            detail,
            "Provider TLS certificate validation failed; CTAS retained the last rights-cleared data and scheduled a retry.",
        )

    def test_reported_label_kind_separates_operational_alerts(self):
        self.assertEqual(
            EXPORTER.reported_label_kind(event(classification="high-importance")),
            "operational alert label",
        )
        self.assertEqual(
            EXPORTER.reported_label_kind(event(classification="SN Ia")),
            "astronomical classification",
        )


class TimelineTests(unittest.TestCase):
    def test_three_clocks_remain_separate_and_ordered(self):
        candidate = event(
            follow_up={
                "observations": [{
                    "provider": "ztf", "observed_at": "2026-08-20T00:00:00Z",
                    "source_published_at": "2026-08-20T00:05:00Z",
                    "ctas_received_at": "2026-08-20T00:06:00Z", "summary": "detection",
                }],
                "classifications": [{
                    "provider": "tns", "classification": "SN Ia",
                    "asserted_at": "2026-08-21T00:00:00Z", "ctas_received_at": "2026-08-21T00:01:00Z",
                }],
            },
        )
        timeline = EXPORTER.timeline_for(candidate)
        observation = next(row for row in timeline if row["evidence_type"] == "observation")
        self.assertEqual(observation["scientific_time"], "2026-08-20T00:00:00Z")
        self.assertEqual(observation["provider_publication_time"], "2026-08-20T00:05:00Z")
        self.assertEqual(observation["ctas_receipt_time"], "2026-08-20T00:06:00Z")
        clocks = [row.get("scientific_time") or row.get("provider_publication_time") or row.get("ctas_receipt_time") for row in timeline]
        self.assertEqual(clocks, sorted(clocks, reverse=True))

    def test_conflict_and_retraction_are_retained(self):
        candidate = event(follow_up={
            "classifications": [
                {"provider": "a", "classification": "SN Ia", "asserted_at": "2026-08-20T00:00:00Z"},
                {"provider": "b", "classification": "SN II", "asserted_at": "2026-08-20T00:00:00Z"},
            ],
            "classification_history": [
                {"provider": "a", "classification": "SN?", "asserted_at": "2026-08-19T00:00:00Z", "retracted": 1},
            ],
        })
        kinds = [row["evidence_type"] for row in EXPORTER.timeline_for(candidate)]
        self.assertEqual(kinds.count("classification"), 2)
        self.assertIn("classification retraction", kinds)


class CertificateAndArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = ROOT / "ctas/data"
        cls.snapshot = json.loads((cls.data_dir / "candidates.json").read_text())
        cls.index = json.loads((cls.data_dir / "catalog-index.json").read_text())
        cls.manifest = json.loads((cls.data_dir / "candidate-chunks/manifest.json").read_text())
        cls.universe = json.loads((cls.data_dir / "source-universe.json").read_text())
        cls.certificate = json.loads((cls.data_dir / "certification.json").read_text())

    def test_certificate_pass_and_failure_logic(self):
        self.assertEqual(EXPORTER.certificate_status([{"passed": True}]), "certified-static-catalog")
        self.assertEqual(EXPORTER.certificate_status([{"passed": True}, {"passed": False}]), "not-certified")
        self.assertEqual(EXPORTER.certificate_status([]), "not-certified")

    def test_certificate_checksum_and_status_are_self_consistent(self):
        report = deepcopy(self.certificate)
        checksum = report.pop("report_checksum_sha256")
        canonical = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.assertEqual(checksum, hashlib.sha256(canonical).hexdigest())
        self.assertEqual(
            self.certificate["status"],
            EXPORTER.certificate_status(self.certificate["gates"]),
        )

    def test_follow_up_counts_reproduce_arrays(self):
        for candidate in self.snapshot["candidates"]:
            for key, count in candidate["follow_up_counts"].items():
                self.assertEqual(count, len(candidate.get("follow_up", {}).get(key, [])))
            self.assertEqual(candidate["follow_up_total"], sum(candidate["follow_up_counts"].values()))

    def test_source_universe_schema_and_vocabulary(self):
        self.assertEqual(self.universe["schema"], EXPORTER.SOURCE_UNIVERSE_SCHEMA)
        self.assertEqual(self.universe["source_count"], len(self.universe["sources"]))
        keys = [row["source_key"] for row in self.universe["sources"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue({"rubin-lsst", "pan-starrs", "goto", "master", "blackgem", "wfst", "yse", "chime", "maxi", "einstein-probe", "ads"} <= set(keys))
        self.assertTrue(all(row["operational_state"] in EXPORTER.SOURCE_STATE_VOCABULARY for row in self.universe["sources"]))

    def test_event_only_coverage_is_exact(self):
        event_only = [candidate for candidate in self.snapshot["candidates"] if candidate["record_completeness"]["label"] == "Event record only"]
        zero_follow_up = [candidate for candidate in self.snapshot["candidates"] if candidate["follow_up_total"] == 0]
        self.assertEqual(len(event_only), len(zero_follow_up))

    def test_public_allowlist_and_publisher_contract(self):
        self.assertFalse(EXPORTER.validate(self.snapshot))
        publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        self.assertNotIn("git add -- ctas/data\n", publisher)
        self.assertIn(".backup '$PUBLISH_DB'", publisher)
        self.assertEqual(publisher.count('--database "$PUBLISH_DB"'), 2)
        for name in (
            "candidates.json", "catalog-index.json", "candidate-chunks/manifest.json",
            "status.json", "source-universe.json", "release-history.json",
            "link-health.json", "certification.json",
        ):
            self.assertIn(name, publisher)
        self.assertIn('for bucket_index in {0..31}', publisher)
        self.assertIn('PUBLIC_FILES+=("ctas/data/candidate-chunks/$bucket.json")', publisher)
        self.assertIn('HEARTBEAT_INTERVAL="${CTAS_HEARTBEAT_INTERVAL:-900}"', publisher)
        self.assertIn('[ "$HEARTBEAT_INTERVAL" -ge 120 ]', publisher)
        self.assertIn('[ "$HEARTBEAT_INTERVAL" -le 900 ]', publisher)
        self.assertIn("publication_state_checksum_sha256", publisher)
        self.assertIn("CURRENT_CODE_BINDING", publisher)
        self.assertIn("HEAD_CODE_BINDING", publisher)
        self.assertIn("CODE_BINDING_CHANGED", publisher)
        self.assertIn('if [ "$FORCE" -eq 0 ] && [ "$HEARTBEAT_AGE"', publisher)
        self.assertNotIn("git diff --quiet HEAD -- ctas/data/candidates.json", publisher)

    def test_compact_index_and_all_detail_shards_are_checksum_bound(self):
        self.assertEqual(self.index["candidate_count"], self.snapshot["candidate_count"])
        self.assertEqual(self.manifest["candidate_count"], self.snapshot["candidate_count"])
        self.assertEqual(self.manifest["chunk_count"], EXPORTER.CANDIDATE_BUCKET_COUNT)
        names = set()
        for row in self.manifest["chunks"]:
            path = ROOT / row["path"]
            raw = path.read_bytes()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"])
            document = json.loads(raw)
            self.assertEqual(document["candidate_count"], row["candidate_count"])
            for candidate in document["candidates"]:
                self.assertEqual(document["bucket"], EXPORTER.candidate_bucket(candidate["name"]))
                names.add(candidate["name"])
        self.assertEqual(names, {row["name"] for row in self.snapshot["candidates"]})
        self.assertEqual(names, {row["name"] for row in self.index["candidates"]})

    def test_published_magnitudes_and_names_are_safe_for_scientific_views(self):
        candidates = self.snapshot["candidates"]
        self.assertTrue(all("%" not in row["name"] for row in candidates))
        self.assertTrue(all(
            row.get("discovery_magnitude") is None or -30 <= float(row["discovery_magnitude"]) <= 40
            for row in candidates
        ))
        flagged = [row for row in candidates if row.get("data_quality_flags")]
        self.assertEqual(len(flagged), self.snapshot["statistics"]["magnitude_values_excluded"])
        self.assertTrue(all("reported_discovery_magnitude" in row for row in flagged))


if __name__ == "__main__":
    unittest.main(verbosity=2)

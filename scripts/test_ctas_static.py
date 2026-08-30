#!/usr/bin/env python3
"""Focused tests for the public CTAS static-catalog contract and artifacts."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import unittest
import uuid
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("ctas_exporter", ROOT / "scripts/export_ctas_snapshot.py")
assert SPEC and SPEC.loader
EXPORTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EXPORTER)


def event(**updates):
    candidate = {
        "event_id": "123e4567-e89b-42d3-a456-426614174000",
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


class ScoreModelTests(unittest.TestCase):
    def test_terminal_status_override_is_applied_last_and_repairs_legacy_score(self):
        for terminal_status in ("retracted", "bogus"):
            with self.subTest(status=terminal_status):
                candidate = event(
                    status=terminal_status,
                    ctas_score=88.0,
                    score_factors={
                        "recency_points": 12.0,
                        "classification_gap_points": 8.0,
                        "coverage_reduction": 3.0,
                        "multimessenger_points": 5.0,
                    },
                )
                model = EXPORTER.score_model_for(candidate)
                self.assertEqual(model["status_override"], terminal_status)
                self.assertGreater(model["final_preclip"], 0.0)
                self.assertEqual(model["final_score"], 0.0)
                self.assertEqual(model["recorded_score_before_publication_correction"], 88.0)
                self.assertTrue(model["publication_correction_applied"])
                self.assertTrue(model["reconciled"])
                self.assertEqual(candidate["ctas_score"], 0.0)
                self.assertEqual(candidate["score_factors"]["status"], terminal_status)

    def test_nonterminal_score_mismatch_is_not_silently_rewritten(self):
        candidate = event(status="candidate", ctas_score=88.0, score_factors={})
        model = EXPORTER.score_model_for(candidate)
        self.assertIsNone(model["status_override"])
        self.assertFalse(model["publication_correction_applied"])
        self.assertFalse(model["reconciled"])
        self.assertEqual(candidate["ctas_score"], 88.0)

    def test_already_zero_terminal_score_does_not_claim_a_publication_repair(self):
        candidate = event(status="retracted", ctas_score=0.0, score_factors={"recency_points": 12.0})
        model = EXPORTER.score_model_for(candidate)
        self.assertEqual(model["status_override"], "retracted")
        self.assertEqual(model["final_score"], 0.0)
        self.assertFalse(model["publication_correction_applied"])
        self.assertTrue(model["reconciled"])

    def test_one_cent_persisted_factor_rounding_is_explicit_not_hidden(self):
        candidate = event(status="candidate", ctas_score=35.01, score_factors={})
        model = EXPORTER.score_model_for(candidate)
        self.assertEqual(model["persisted_factor_rounding_residual"], 0.01)
        self.assertEqual(model["final_score"], 35.01)
        self.assertTrue(model["reconciled"])
        self.assertIn("rounded to hundredths", model["factor_precision_note"])


class ScienceBriefTests(unittest.TestCase):
    def test_science_brief_is_deterministic_and_evidence_bounded(self):
        candidate = event(
            classification="SN Ia",
            follow_up_total=1,
            follow_up={
                "classifications": [{
                    "provider": "tns",
                    "classification": "SN Ia",
                    "assertion_id": "classification:1",
                    "asserted_at": "2026-08-22T02:00:00Z",
                    "source_published_at": "2026-08-22T02:05:00Z",
                    "ctas_received_at": "2026-08-22T02:06:00Z",
                }],
            },
            identity_resolution={"state": "AMBIGUOUS"},
            astro_evidence={
                "conflictSets": [{
                    "conflictSetId": "conflict:classification:1",
                    "propertyCode": "classification",
                }],
            },
            record_completeness={
                "components": [
                    {"id": "coordinates", "label": "Coordinates", "state": "present"},
                    {"id": "spectrum", "label": "Spectrum", "state": "missing"},
                    {"id": "host-context", "label": "Host context", "state": "not-assessed"},
                ],
            },
        )
        first = EXPORTER.science_brief_for(deepcopy(candidate))
        second = EXPORTER.science_brief_for(deepcopy(candidate))
        self.assertEqual(first, second)
        self.assertEqual(first["schema"], "ctas.candidate-science-brief@1.0.0")
        self.assertEqual(
            {row["component_id"] for row in first["missing_information"]},
            {"spectrum", "host-context"},
        )
        self.assertTrue(any(
            row["conflict_set_ids"] == ["conflict:classification:1"]
            for row in first["uncertain_or_conflicting"]
        ))
        self.assertTrue(any(
            row["label"] == "Cross-source identity" and row["state"] == "ambiguous"
            for row in first["uncertain_or_conflicting"]
        ))
        self.assertNotIn(
            "Reported classification",
            {row["label"] for row in first["confidently_known"]},
            "a conflicted classification must not be promoted to confidently known",
        )
        self.assertEqual(
            first["most_recent_change"]["public_available_at"],
            "2026-08-22T02:05:00Z",
        )
        self.assertIn("does not confirm", first["claim_boundary"])

    def test_most_recent_change_uses_public_availability_not_scientific_epoch(self):
        candidate = event(
            evidence_timeline=[
                {
                    "entry_id": "scientifically-later-publicly-older",
                    "assertion_kind": "provider assertion",
                    "evidence_type": "observation",
                    "title": "Older public arrival",
                    "provider": "provider-a",
                    "scientific_time": "2026-08-29T00:00:00Z",
                    "provider_publication_time": "2026-08-21T00:00:00Z",
                    "public_available_at": "2026-08-21T00:00:00Z",
                },
                {
                    "entry_id": "publicly-latest",
                    "assertion_kind": "provider assertion",
                    "evidence_type": "classification",
                    "title": "Latest public arrival",
                    "provider": "provider-b",
                    "scientific_time": "2026-08-20T00:00:00Z",
                    "ctas_receipt_time": "2026-08-22T00:00:00Z",
                    "public_available_at": "2026-08-22T00:00:00Z",
                },
            ],
            record_completeness={"components": []},
        )
        brief = EXPORTER.science_brief_for(candidate)
        self.assertEqual(brief["most_recent_change"]["entry_id"], "publicly-latest")


class ArtifactBuilderTests(unittest.TestCase):
    def test_alias_index_preserves_an_explicit_same_string_collision(self):
        first = event(designations=[{
            "source_key": "provider-a", "designation": "AT2026collision", "ambiguous": True,
        }])
        second = event(
            event_id="223e4567-e89b-42d3-a456-426614174001",
            name="AT2026other",
            designations=[{
                "source_key": "provider-b", "designation": "AT2026collision", "ambiguous": True,
            }],
        )
        document, raw = EXPORTER.alias_index_artifact([first, second], "a" * 64)
        self.assertEqual(document["alias_count"], 2)
        self.assertEqual({row[0] for row in document["rows"]}, {first["event_id"], second["event_id"]})
        self.assertTrue(all(row[2] == "AT2026collision" and row[3] is True for row in document["rows"]))
        self.assertEqual(
            raw,
            (json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )


class TimelineTests(unittest.TestCase):
    def test_three_clocks_remain_separate_and_ordered(self):
        candidate = event(
            follow_up={
                "observations": [
                    {
                        "provider": "ztf", "observed_at": "2026-08-20T00:00:00Z",
                        "source_published_at": "2026-08-20T00:05:00Z",
                        "ctas_received_at": "2026-08-20T00:06:00Z", "summary": "detection",
                    },
                    {
                        "provider": "archive", "observed_at": "2026-08-19T00:00:00Z",
                        "summary": "scientific clock only",
                    },
                ],
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
        self.assertEqual(observation["public_available_at"], "2026-08-20T00:05:00Z")
        self.assertEqual(observation["availability_basis"], "provider publication time")
        classification = next(row for row in timeline if row["evidence_type"] == "classification")
        self.assertEqual(classification["public_available_at"], "2026-08-21T00:01:00Z")
        self.assertEqual(classification["availability_basis"], "CTAS receipt time")
        scientific_only = next(row for row in timeline if row.get("summary") == "scientific clock only")
        self.assertIsNone(scientific_only["public_available_at"])
        self.assertEqual(
            scientific_only["availability_basis"],
            "no defensible public-availability clock retained",
        )
        clocks = [row.get("scientific_time") or row.get("provider_publication_time") or row.get("ctas_receipt_time") for row in timeline]
        self.assertEqual(clocks, sorted(clocks, reverse=True))
        self.assertTrue(all(
            row.get("public_available_at") in {
                row.get("provider_publication_time"), row.get("ctas_receipt_time"), None,
            }
            for row in timeline
        ))

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
        cls.bootstrap_raw = (cls.data_dir / "catalog-bootstrap.json").read_bytes()
        cls.index_raw = (cls.data_dir / "catalog-index.json").read_bytes()
        cls.index = json.loads(cls.bootstrap_raw)
        cls.index_candidates = EXPORTER.inflate_catalog_candidates(cls.index)
        cls.manifest = json.loads((cls.data_dir / "candidate-chunks/manifest.json").read_text())
        if cls.manifest.get("schema") != EXPORTER.CANDIDATE_MANIFEST_SCHEMA:
            raise AssertionError("complete-catalog artifacts do not use the current manifest schema")
        complete_by_id = {}
        for metadata in cls.manifest["chunks"]:
            raw = (ROOT / metadata["path"]).read_bytes()
            if len(raw) != metadata["bytes"] or hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
                raise AssertionError(f"invalid candidate chunk: {metadata['path']}")
            for candidate in json.loads(raw)["candidates"]:
                if candidate["event_id"] in complete_by_id:
                    raise AssertionError(f"duplicate candidate UUID: {candidate['event_id']}")
                complete_by_id[candidate["event_id"]] = candidate
        ordered = [complete_by_id[row["event_id"]] for row in cls.index_candidates]
        cls.snapshot = {**cls.index, "candidates": ordered}
        cls.universe = json.loads((cls.data_dir / "source-universe.json").read_text())
        cls.alias_index = json.loads((cls.data_dir / "alias-index.json").read_text())
        cls.research_manifest = json.loads((cls.data_dir / "research/manifest.json").read_text())
        cls.status = json.loads((cls.data_dir / "status.json").read_text())
        cls.certificate = json.loads((cls.data_dir / "certification.json").read_text())

    def test_certificate_pass_and_failure_logic(self):
        self.assertEqual(EXPORTER.certificate_status([{"passed": True}]), "verified-static-snapshot")
        self.assertEqual(EXPORTER.certificate_status([{"passed": True}, {"passed": False}]), "verification-failed")
        self.assertEqual(
            EXPORTER.certificate_status([{"id": "deployed-code-binding", "passed": False}]),
            "publication-binding-pending",
        )
        self.assertEqual(
            EXPORTER.certificate_status([
                {"id": "deployed-code-binding", "passed": False},
                {"id": "local-origin-code-alignment", "passed": False},
            ]),
            "publication-binding-pending",
        )
        self.assertEqual(
            EXPORTER.certificate_status([
                {"id": "deployed-code-binding", "passed": False},
                {"id": "catalog-population", "passed": False},
            ]),
            "verification-failed",
        )
        self.assertEqual(EXPORTER.certificate_status([]), "verification-failed")

    def test_semantic_catalog_checksum_ignores_source_poll_timestamps(self):
        first = event(source_coverage=[{
            "source_id": "tns", "disposition": "searched-no-match",
            "checked_at": "2026-08-23T18:00:00Z",
            "next_eligible_at": "2026-08-23T21:00:00Z",
        }])
        second = deepcopy(first)
        second["source_coverage"][0]["checked_at"] = "2026-08-23T21:00:00Z"
        second["source_coverage"][0]["next_eligible_at"] = "2026-08-24T00:00:00Z"
        self.assertEqual(
            EXPORTER.catalog_semantic_checksum([first]),
            EXPORTER.catalog_semantic_checksum([second]),
        )

    def test_semantic_catalog_checksum_ignores_projection_clock_and_ticking_age(self):
        first = event(
            astro_evidence={"generatedAt": "2026-08-23T18:00:00Z", "measurementCount": 2},
            source_matrix=[{
                "sourceContractId": "tns",
                "retainedEvidenceLatestAt": "2026-08-23T17:00:00Z",
                "retainedEvidenceAgeSeconds": 3600,
            }],
        )
        second = deepcopy(first)
        second["astro_evidence"]["generatedAt"] = "2026-08-23T18:02:00Z"
        second["source_matrix"][0]["retainedEvidenceAgeSeconds"] = 3720
        self.assertEqual(
            EXPORTER.catalog_semantic_checksum([first]),
            EXPORTER.catalog_semantic_checksum([second]),
        )

    def test_semantic_catalog_checksum_detects_evidence_changes(self):
        first = event(source_coverage=[{
            "source_id": "tns", "disposition": "searched-no-match",
            "checked_at": "2026-08-23T18:00:00Z",
        }])
        second = deepcopy(first)
        second["source_coverage"][0]["disposition"] = "searched-with-data"
        second["source_coverage"][0]["retained_record_count"] = 1
        self.assertNotEqual(
            EXPORTER.catalog_semantic_checksum([first]),
            EXPORTER.catalog_semantic_checksum([second]),
        )

    def test_source_universe_version_ignores_export_clock_but_detects_contract_changes(self):
        first = {
            "schema": "ctas.public-source-universe@1.0.0",
            "generated_at": "2026-08-23T18:00:00Z",
            "sources": [{"source_key": "tns", "contract_version": "1.0.0"}],
        }
        second = deepcopy(first)
        second["generated_at"] = "2026-08-23T18:02:00Z"
        self.assertEqual(
            EXPORTER.source_universe_contract_checksum(first),
            EXPORTER.source_universe_contract_checksum(second),
        )
        second["sources"][0]["contract_version"] = "1.0.1"
        self.assertNotEqual(
            EXPORTER.source_universe_contract_checksum(first),
            EXPORTER.source_universe_contract_checksum(second),
        )

    def test_certificate_checksum_and_status_are_self_consistent(self):
        report = deepcopy(self.certificate)
        checksum = report.pop("report_checksum_sha256")
        canonical = (json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.assertEqual(checksum, hashlib.sha256(canonical).hexdigest())
        self.assertEqual(
            self.certificate["status"],
            EXPORTER.certificate_status(self.certificate["gates"]),
        )

    def test_columnar_bootstrap_is_small_lossless_and_compatibly_mirrored(self):
        self.assertEqual(self.index["schema"], EXPORTER.CATALOG_INDEX_SCHEMA)
        self.assertNotIn("candidates", self.index)
        self.assertEqual(self.bootstrap_raw, self.index_raw)
        self.assertLessEqual(len(self.bootstrap_raw), EXPORTER.CATALOG_BOOTSTRAP_MAX_BYTES)
        self.assertEqual(self.index["candidate_columns"], list(EXPORTER.CATALOG_CANDIDATE_COLUMNS))
        self.assertEqual(self.index["candidate_count"], len(self.index["candidate_rows"]))
        self.assertEqual(self.index["candidate_count"], len(self.index_candidates))
        self.assertEqual(len(self.index["candidate_columns"]), len(set(self.index["candidate_columns"])))
        self.assertTrue(all(
            len(row) == len(self.index["candidate_columns"])
            for row in self.index["candidate_rows"]
        ))
        event_ids = [row["event_id"] for row in self.index_candidates]
        self.assertEqual(len(event_ids), len(set(event_ids)))
        self.assertTrue(all(
            row["detail_chunk"] == f"candidate-chunks/{EXPORTER.candidate_bucket(row['event_id'])}.json"
            for row in self.index_candidates
        ))
        self.assertTrue(all(
            sum(row["follow_up_counts"].values()) == row["follow_up_total"]
            for row in self.index_candidates
        ))

    def test_schema_projector_and_contract_tests_are_checksum_bound(self):
        for relative in (
            "ctas/schema/astro-evidence-core-0.1.0.schema.json",
            "ctas/astro-evidence.js",
            "ctas/catalog-model.js",
            "ctas/observability.js",
            "ctas/workbench.js",
            "ctas/data/observatories.json",
            "ctas/research/README.md",
            "ctas/research/ctas-quickstart.ipynb",
            "scripts/ctas_astro_evidence.py",
            "scripts/export_ctas_snapshot.py",
            "scripts/check_ctas_links.py",
            "scripts/rebuild_ctas_release_history.py",
            "scripts/test_ctas_catalog_model.js",
            "scripts/test_ctas_links.py",
            "scripts/test_ctas_astro_evidence.py",
        ):
            self.assertIn(relative, self.certificate["files"])
            self.assertEqual(
                self.certificate["files"][relative]["sha256"],
                hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(),
            )

    def test_uuid_routes_and_alias_routes_remain_distinct(self):
        app = (ROOT / "ctas/app.js").read_text()
        self.assertIn('url.searchParams.set("event", summary.event_id)', app)
        self.assertIn('url.searchParams.get("alias")', app)
        self.assertIn('url.searchParams.get("source")', app)
        self.assertIn('row.source_key).toLowerCase() === source.toLowerCase()', app)
        self.assertNotIn('url.searchParams.set("candidate", summary.name)', app)

    def test_history_navigation_clears_a_dossier_when_the_route_loses_its_event(self):
        app = (ROOT / "ctas/app.js").read_text()
        self.assertIn("function clearCandidateForHistoryNavigation()", app)
        self.assertIn("var route = routeMatches();", app)
        self.assertIn("if (!route.kind)", app)
        self.assertIn("clearCandidateForHistoryNavigation();", app)
        self.assertIn('window.addEventListener("hashchange", reconcileHistoryRoute)', app)
        self.assertIn('window.addEventListener("popstate", reconcileHistoryRoute)', app)

    def test_replay_route_never_selects_future_evidence(self):
        workbench = (ROOT / "ctas/workbench.js").read_text()
        self.assertIn("selected = -1;", workbench)
        self.assertIn("Date.parse(row.public_available_at) <= requestedTime", workbench)
        self.assertIn('type="range" min="-1"', workbench)
        self.assertIn("selected = Math.max(-1", workbench)
        self.assertIn("No dated evidence had reached CTAS by this cutoff.", workbench)
        self.assertNotIn("Date.parse(row.public_available_at) >= requestedTime", workbench)

    def test_public_copy_calls_it_snapshot_verification_not_certification(self):
        html = (ROOT / "ctas.html").read_text()
        app = (ROOT / "ctas/app.js").read_text()
        self.assertIn("Automated snapshot checks", html)
        self.assertIn("not scientific certification or human peer review", html)
        self.assertIn('statusCell("Snapshot integrity"', app)
        self.assertIn('passedCheckCount + " of " + checkCount + " checks passed"', app)
        self.assertIn("Only local commit and origin publication bindings are pending", app)
        self.assertIn('"failed_gate_ids"', (ROOT / "scripts/export_ctas_snapshot.py").read_text())
        self.assertIn('localPreview ? "Local preview"', app)
        self.assertIn('stale ? "Publisher paused"', app)
        self.assertIn('window.location.protocol === "file:"', app)
        self.assertIn("Open the current public CTAS catalog", app)
        self.assertNotIn("Certified Static Catalog", html + app)
        self.assertNotIn("Static release assurance", html + app)

    def test_public_workspace_defaults_are_calm_without_removing_features(self):
        html = (ROOT / "ctas.html").read_text()
        app = (ROOT / "ctas/app.js").read_text()
        workbench = (ROOT / "ctas/workbench.js").read_text()

        self.assertNotIn('class="ctas-contents"', html)
        self.assertNotIn('class="ctas-utility"', html)
        self.assertEqual(html.count('class="ctas-navigation__menu"'), 2)
        self.assertIn('id="ctas-reference"', html)
        self.assertIn('class="ctas-filter-drawer"', html)
        self.assertNotIn('id="celestial-sphere" open', html)

        for element_id in (
            "about-ctas", "catalog-overview", "active-sources", "recent-stream",
            "celestial-sphere", "ranked-candidates", "ctas-research-tools",
            "methods-and-use", "ctas-q", "ctas-class", "ctas-messenger",
            "ctas-statusfilter", "ctas-survey", "ctas-cone-ra",
            "ctas-cone-dec", "ctas-cone-radius",
        ):
            self.assertIn(f'id="{element_id}"', html)

        self.assertIn('stream.hidden = mode !== "explore"', workbench)
        self.assertIn('sky.hidden = mode !== "explore"', workbench)
        self.assertIn('ranked.hidden = mode === "learn"', workbench)
        self.assertIn('research.hidden = mode !== "research"', workbench)
        self.assertIn('research.open = false', workbench)
        self.assertIn('MODE_COPY[requested] ? requested : "explore"', workbench)
        self.assertNotIn('loadLocal(MODE_KEY, "explore")', workbench)
        self.assertIn("Top 100 CTAS-ranked candidates", html)
        self.assertIn("Reported in the last 24 hours", html)
        self.assertIn("Browse the complete retained catalog", app)
        self.assertIn("var PAGE = 100;", app)
        self.assertIn("discovery >= cutoff", app)
        self.assertIn("maintained contracts—not every astronomical source", app)

        self.assertIn('data-dossier-view="brief"', app)
        dossier = app[app.index("function renderDetails"):app.index("function statusCell")]
        self.assertIn("skyContextPanel(candidate)", dossier)
        self.assertLess(dossier.index("skyContextPanel(candidate)"), dossier.index("renderIdentity(candidate)"))
        self.assertIn("hips-image-services/hips2fits", workbench)
        self.assertIn("CDS/P/DSS2/color", workbench)
        self.assertIn("not</em> automatically the transient, its host, or a confirmed counterpart", workbench)
        self.assertIn("CTAS reported position", workbench)
        self.assertIn('data-dossier-view="identity"><summary>', app)
        self.assertNotIn('data-dossier-view="identity" open', app)
        self.assertNotIn('view || "identity"', app)
        self.assertNotIn('(index === 0 ? " open" : "")', app)

    def test_complete_catalog_download_ui_uses_parts_not_a_giant_blob(self):
        html = (ROOT / "ctas.html").read_text()
        app = (ROOT / "ctas/app.js").read_text()
        self.assertIn('id="catalog-downloads"', html)
        self.assertIn("Download complete-catalog manifest", html)
        self.assertIn("function renderCatalogDownloads()", app)
        self.assertIn('getJSON("candidate-chunks/manifest.json" + suffix)', app)
        self.assertIn("safeCatalogDownloadPath(row.path)", app)
        self.assertIn('download>Download part', app)
        self.assertNotIn("ctas/data/candidates.json", html + app)

    def test_follow_up_counts_reproduce_arrays(self):
        for candidate in self.snapshot["candidates"]:
            for key, count in candidate["follow_up_counts"].items():
                self.assertEqual(count, len(candidate.get("follow_up", {}).get(key, [])))
            self.assertEqual(candidate["follow_up_total"], sum(candidate["follow_up_counts"].values()))

    def test_every_published_score_reconciles_and_terminal_records_are_zero(self):
        for candidate in self.snapshot["candidates"]:
            model = candidate["score_model"]
            with self.subTest(event_id=candidate["event_id"]):
                self.assertEqual(model["schema"], "ctas.follow-up-score@1.0.0")
                self.assertTrue(model["reconciled"])
                self.assertLessEqual(
                    abs(float(candidate["ctas_score"]) - float(model["final_score"])),
                    float(model["tolerance"]) + 1e-9,
                )
                if str(candidate.get("status") or "").lower() in {"retracted", "bogus"}:
                    self.assertEqual(candidate["ctas_score"], 0.0)
                    self.assertEqual(model["status_override"], str(candidate["status"]).lower())

    def test_every_science_brief_matches_completeness_and_is_claim_bounded(self):
        for candidate in self.snapshot["candidates"]:
            brief = candidate["science_brief"]
            with self.subTest(event_id=candidate["event_id"]):
                self.assertEqual(brief["schema"], "ctas.candidate-science-brief@1.0.0")
                self.assertEqual(brief, EXPORTER.science_brief_for(candidate))
                self.assertEqual(
                    {row["component_id"] for row in brief["missing_information"]},
                    {
                        row["id"] for row in candidate["record_completeness"]["components"]
                        if row["state"] in {"missing", "not-assessed"}
                    },
                )
                self.assertTrue(brief["what_happened"]["basis_record_ids"])
                self.assertIn("does not confirm", brief["claim_boundary"])

    def test_replay_timeline_never_uses_observation_time_as_public_availability(self):
        for candidate in self.snapshot["candidates"]:
            timeline = candidate["evidence_timeline"]
            with self.subTest(event_id=candidate["event_id"]):
                self.assertEqual(
                    len({row["entry_id"] for row in timeline}),
                    len(timeline),
                )
                for row in timeline:
                    self.assertIn(
                        row.get("public_available_at"),
                        {row.get("provider_publication_time"), row.get("ctas_receipt_time"), None},
                    )
                    if not row.get("provider_publication_time") and not row.get("ctas_receipt_time"):
                        self.assertIsNone(row.get("public_available_at"))

    def test_source_universe_schema_and_vocabulary(self):
        self.assertEqual(self.universe["schema"], EXPORTER.SOURCE_UNIVERSE_SCHEMA)
        self.assertEqual(self.universe["source_count"], len(self.universe["sources"]))
        keys = [row["source_key"] for row in self.universe["sources"]]
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue({"rubin-lsst", "pan-starrs", "goto", "master", "blackgem", "wfst", "yse", "chime", "maxi", "einstein-probe", "ads"} <= set(keys))
        self.assertTrue(all(row["operational_state"] in EXPORTER.SOURCE_STATE_VOCABULARY for row in self.universe["sources"]))

    def test_alias_index_is_exact_checksum_bound_and_ambiguity_preserving(self):
        self.assertEqual(self.alias_index["schema"], EXPORTER.ALIAS_INDEX_SCHEMA)
        self.assertEqual(
            self.alias_index["catalog_content_checksum_sha256"],
            self.snapshot["catalog_content_checksum_sha256"],
        )
        self.assertEqual(self.alias_index["candidate_count"], self.snapshot["candidate_count"])
        self.assertEqual(self.alias_index["columns"], ["event_id", "source_key", "designation", "ambiguous"])
        self.assertEqual(self.alias_index["alias_count"], len(self.alias_index["rows"]))
        alias_raw = (self.data_dir / "alias-index.json").read_bytes()
        self.assertEqual(
            self.status["artifacts"]["alias_index"],
            {
                "path": "ctas/data/alias-index.json",
                "sha256": hashlib.sha256(alias_raw).hexdigest(),
            },
        )
        expected = sorted([
            [
                candidate["event_id"], str(alias.get("source_key") or ""),
                str(alias.get("designation") or ""), bool(alias.get("ambiguous")),
            ]
            for candidate in self.snapshot["candidates"]
            for alias in candidate.get("designations", [])
            if alias.get("designation")
        ], key=lambda row: (row[2].casefold(), row[1].casefold(), row[0]))
        self.assertEqual(self.alias_index["rows"], expected)
        event_ids = {candidate["event_id"] for candidate in self.snapshot["candidates"]}
        self.assertTrue(all(
            len(row) == 4 and row[0] in event_ids and isinstance(row[3], bool)
            for row in self.alias_index["rows"]
        ))
        aliases_to_events = {}
        for row in self.alias_index["rows"]:
            aliases_to_events.setdefault(row[2].casefold(), []).append(row)
        for rows in aliases_to_events.values():
            if len({row[0] for row in rows}) > 1:
                self.assertTrue(
                    all(row[3] is True for row in rows),
                    "an unscoped alias bound to multiple UUIDs must remain explicitly ambiguous",
                )

    def test_research_artifacts_are_parseable_complete_and_checksum_bound(self):
        self.assertEqual(self.research_manifest["schema"], EXPORTER.RESEARCH_TABLE_MANIFEST_SCHEMA)
        self.assertEqual(
            self.research_manifest["catalog_content_checksum_sha256"],
            self.snapshot["catalog_content_checksum_sha256"],
        )
        tables = {row["path"]: row for row in self.research_manifest["tables"]}
        required = {
            "ctas/data/research/events.csv",
            "ctas/data/research/aliases.csv",
            "ctas/data/research/sources.csv",
            "ctas/data/research/events.vot",
            "ctas/data/research/tom-targets.csv",
        }
        self.assertEqual(set(tables), required)
        manifest_raw = (self.data_dir / "research/manifest.json").read_bytes()
        self.assertEqual(
            self.status["artifacts"]["research_tables"],
            {
                "path": "ctas/data/research/manifest.json",
                "sha256": hashlib.sha256(manifest_raw).hexdigest(),
            },
        )
        for relative, metadata in tables.items():
            raw = (ROOT / relative).read_bytes()
            with self.subTest(path=relative):
                self.assertEqual(len(raw), metadata["bytes"])
                self.assertEqual(hashlib.sha256(raw).hexdigest(), metadata["sha256"])
                self.assertGreaterEqual(metadata["row_count"], 0)
                self.assertIn(relative, self.certificate["files"])

        def csv_rows(relative):
            with (ROOT / relative).open(newline="", encoding="utf-8") as handle:
                rows = list(csv.reader(handle))
            self.assertTrue(rows)
            return rows[0], rows[1:]

        event_header, event_rows = csv_rows("ctas/data/research/events.csv")
        self.assertEqual(event_header, list(EXPORTER.CATALOG_CANDIDATE_COLUMNS))
        self.assertEqual(len(event_rows), self.snapshot["candidate_count"])
        self.assertEqual(len({row[0] for row in event_rows}), len(event_rows))
        alias_header, alias_rows = csv_rows("ctas/data/research/aliases.csv")
        self.assertEqual(alias_header, self.alias_index["columns"])
        self.assertEqual(len(alias_rows), self.alias_index["alias_count"])
        source_header, source_rows = csv_rows("ctas/data/research/sources.csv")
        self.assertEqual(source_header[0:2], ["source_key", "name"])
        self.assertEqual(len(source_rows), self.universe["source_count"])

        votable_root = ET.parse(ROOT / "ctas/data/research/events.vot").getroot()
        self.assertTrue(votable_root.tag.endswith("VOTABLE"))
        self.assertEqual(
            len(votable_root.findall(".//{*}TR")),
            self.snapshot["candidate_count"],
        )
        fields = [row.get("name") for row in votable_root.findall(".//{*}FIELD")]
        self.assertTrue({"event_id", "ra_deg", "dec_deg", "ctas_score"} <= set(fields))
        checksum_params = [
            row.get("value") for row in votable_root.findall(".//{*}PARAM")
            if row.get("name") == "catalog_content_checksum_sha256"
        ]
        self.assertEqual(checksum_params, [self.snapshot["catalog_content_checksum_sha256"]])

        tom_header, tom_rows = csv_rows("ctas/data/research/tom-targets.csv")
        self.assertTrue({"name", "type", "ra", "dec", "epoch", "ctas_event_id"} <= set(tom_header))
        self.assertEqual(len(tom_rows), tables["ctas/data/research/tom-targets.csv"]["row_count"])
        id_column = tom_header.index("ctas_event_id")
        type_column = tom_header.index("type")
        self.assertTrue(all(row[type_column] == "SIDEREAL" and row[id_column] in {
            candidate["event_id"] for candidate in self.snapshot["candidates"]
        } for row in tom_rows))

        notebook_path = ROOT / "ctas/research/ctas-quickstart.ipynb"
        notebook = json.loads(notebook_path.read_text())
        notebook_text = "\n".join(
            "".join(cell.get("source", [])) for cell in notebook.get("cells", [])
        )
        self.assertEqual(notebook["nbformat"], 4)
        self.assertIn("catalog_content_checksum_sha256", notebook_text)
        self.assertIn("ctas/data/research/events.csv", notebook_text)
        self.assertIn("not a probability", notebook_text)
        html = (ROOT / "ctas.html").read_text()
        for relative in ("ctas/data/research/events.vot", "ctas/data/research/tom-targets.csv"):
            self.assertIn(f'href="{relative}"', html)
        self.assertIn('href="ctas/research/ctas-quickstart.ipynb"', html)

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
            "catalog-bootstrap.json", "catalog-index.json", "alias-index.json",
            "research/manifest.json", "research/events.csv", "research/aliases.csv",
            "research/sources.csv", "research/events.vot", "research/tom-targets.csv",
            "candidate-chunks/manifest.json",
            "status.json", "source-universe.json", "release-history.json",
            "link-health.json", "certification.json",
        ):
            self.assertIn(name, publisher)
        self.assertNotIn("candidates.json", publisher)
        self.assertIn("--catalog-index ctas/data/catalog-index.json", publisher)
        self.assertIn("--candidate-manifest ctas/data/candidate-chunks/manifest.json", publisher)
        self.assertIn('for bucket_index in {0..255}', publisher)
        self.assertIn('PUBLIC_FILES+=("ctas/data/candidate-chunks/$bucket.json")', publisher)
        self.assertIn('HEARTBEAT_INTERVAL="${CTAS_HEARTBEAT_INTERVAL:-900}"', publisher)
        self.assertEqual(publisher.count('--release-base-ref origin/main'), 2)
        self.assertIn('restore --source=HEAD --staged --worktree', publisher)
        self.assertIn('restore --source=HEAD --worktree', publisher)
        self.assertIn('[ "$HEARTBEAT_INTERVAL" -ge 120 ]', publisher)
        self.assertIn('[ "$HEARTBEAT_INTERVAL" -le 900 ]', publisher)
        self.assertIn("publication_state_checksum_sha256", publisher)
        self.assertIn("CURRENT_CODE_BINDING", publisher)
        self.assertIn("HEAD_CODE_BINDING", publisher)
        self.assertIn("CODE_BINDING_CHANGED", publisher)
        self.assertIn("export PYTHONDONTWRITEBYTECODE=1", publisher)
        self.assertIn('git fetch --quiet origin "$BRANCH"', publisher)
        self.assertIn('git rebase "origin/$BRANCH"', publisher)
        self.assertIn("remote update preserved checksum-bound CTAS code", publisher)
        self.assertNotIn("git push --force", publisher)
        self.assertIn("deployed-code-binding,local-origin-code-alignment", publisher)
        self.assertIn("local checksum-bound code successor is not published; publication paused", publisher)
        mirror = (ROOT / "scripts/mirror_loop.sh").read_text()
        self.assertLess(
            mirror.index('*"publication paused"*'),
            mirror.index("*published*"),
        )
        self.assertIn('if [ "$FORCE" -eq 0 ] && [ "$HEARTBEAT_AGE"', publisher)
        self.assertNotIn("ctas/data/candidates.json", publisher)
        runner = (ROOT / "scripts/ctas_launchd_runner.sh").read_text()
        agent = (ROOT / "scripts/io.github.jackmcguireastro.ctas-mirror.plist").read_text()
        self.assertIn('git fetch --quiet origin "$BRANCH"', runner)
        self.assertIn('git merge --quiet --ff-only "origin/$BRANCH"', runner)
        self.assertIn("export PYTHONDONTWRITEBYTECODE=1", runner)
        self.assertIn("trap 'rmdir", runner)
        self.assertNotIn("exec env CTAS_SITE", runner)
        self.assertIn("<key>StartInterval</key>", agent)
        self.assertIn("<integer>120</integer>", agent)
        self.assertNotIn("<key>WatchPaths</key>", agent)
        self.assertIn("Library/Application Support/CTASPublisher/site", agent)
        installer = (ROOT / "scripts/install_ctas_mirror.sh").read_text()
        self.assertLess(
            installer.index('launchctl enable "$DOMAIN/$LABEL"'),
            installer.index('launchctl bootstrap "$DOMAIN" "$DEST"'),
        )
        self.assertIn('grep -q \'state = running\'', installer)
        ignore = (ROOT / ".gitignore").read_text()
        self.assertIn("__pycache__/", ignore)
        self.assertIn("*.py[cod]", ignore)

    def test_compact_index_and_all_detail_shards_are_checksum_bound(self):
        self.assertEqual(self.index["candidate_count"], self.snapshot["candidate_count"])
        self.assertEqual(self.manifest["candidate_count"], self.snapshot["candidate_count"])
        self.assertEqual(self.manifest["chunk_count"], EXPORTER.CANDIDATE_BUCKET_COUNT)
        self.assertEqual(len(self.manifest["chunks"]), EXPORTER.CANDIDATE_BUCKET_COUNT)
        names = set()
        event_ids = set()
        complete_by_id = {}
        self.assertEqual(self.manifest["catalog_index"]["path"], "ctas/data/catalog-index.json")
        index_raw = self.index_raw
        self.assertEqual(len(index_raw), self.manifest["catalog_index"]["bytes"])
        self.assertEqual(hashlib.sha256(index_raw).hexdigest(), self.manifest["catalog_index"]["sha256"])
        self.assertEqual(
            [row["path"] for row in self.manifest["chunks"]],
            [f"ctas/data/candidate-chunks/{index:02x}.json" for index in range(EXPORTER.CANDIDATE_BUCKET_COUNT)],
        )
        for row in self.manifest["chunks"]:
            path = ROOT / row["path"]
            raw = path.read_bytes()
            self.assertEqual(len(raw), row["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"])
            document = json.loads(raw)
            self.assertEqual(document["schema"], EXPORTER.CANDIDATE_CHUNK_SCHEMA)
            self.assertEqual(document["candidate_count"], row["candidate_count"])
            self.assertEqual(document["bucket"], Path(row["path"]).stem)
            for candidate in document["candidates"]:
                parsed = uuid.UUID(candidate["event_id"])
                self.assertEqual(str(parsed), candidate["event_id"])
                self.assertEqual(document["bucket"], EXPORTER.candidate_bucket(candidate["event_id"]))
                names.add(candidate["name"])
                self.assertNotIn(candidate["event_id"], event_ids)
                event_ids.add(candidate["event_id"])
                complete_by_id[candidate["event_id"]] = candidate
        self.assertEqual(names, {row["name"] for row in self.snapshot["candidates"]})
        self.assertEqual(names, {row["name"] for row in self.index_candidates})
        self.assertEqual(event_ids, {row["event_id"] for row in self.snapshot["candidates"]})
        self.assertEqual(event_ids, {row["event_id"] for row in self.index_candidates})
        ordered = [complete_by_id[row["event_id"]] for row in self.index_candidates]
        self.assertEqual(ordered, self.snapshot["candidates"])
        self.assertEqual(
            hashlib.sha256(EXPORTER.canonical_candidate_list_bytes(ordered)).hexdigest(),
            self.manifest["assembled_candidates_checksum_sha256"],
        )

    def test_every_allowlisted_public_artifact_is_below_github_object_limit(self):
        self.assertFalse(
            (ROOT / "ctas/data/candidates.json").exists(),
            "the retired oversized monolith must not remain publicly reachable",
        )
        explicit = [
            "ctas/data/catalog-bootstrap.json", "ctas/data/catalog-index.json",
            "ctas/data/alias-index.json", "ctas/data/research/manifest.json",
            "ctas/data/candidate-chunks/manifest.json",
            "ctas/data/status.json", "ctas/data/source-universe.json",
            "ctas/data/release-history.json", "ctas/data/link-health.json",
            "ctas/data/certification.json",
        ] + [row["path"] for row in self.research_manifest["tables"]] + [
            f"ctas/data/candidate-chunks/{index:02x}.json"
            for index in range(EXPORTER.CANDIDATE_BUCKET_COUNT)
        ]
        for relative in explicit:
            self.assertLess((ROOT / relative).stat().st_size, EXPORTER.GITHUB_MAX_BLOB_BYTES)

    def test_git_catalog_loader_crosses_legacy_to_partitioned_releases(self):
        candidates = [event(), event(
            event_id="223e4567-e89b-42d3-a456-426614174001", name="AT2026xyz",
        )]
        legacy = {"generated_at": "2026-08-29T10:00:00Z", "candidates": candidates}
        blobs = {("legacy", "ctas/data/candidates.json"): json.dumps(legacy).encode()}
        ordered = [candidates[1], candidates[0]]
        index = {
            "schema": EXPORTER.CATALOG_INDEX_SCHEMA,
            "generated_at": "2026-08-29T11:00:00Z",
            "candidate_count": 2,
            "candidate_columns": list(EXPORTER.CATALOG_CANDIDATE_COLUMNS),
            "candidate_rows": [EXPORTER.compact_candidate_row(row) for row in ordered],
        }
        index_raw = (json.dumps(index, sort_keys=True, separators=(",", ":")) + "\n").encode()
        bucket_rows, chunks = EXPORTER.candidate_chunk_artifacts(candidates)
        manifest, _ = EXPORTER.complete_catalog_manifest_artifact(
            candidates,
            EXPORTER.inflate_catalog_candidates(index),
            index_raw,
            bucket_rows,
            chunks,
            "a" * 64,
        )
        blobs[("sharded", "ctas/data/catalog-index.json")] = index_raw
        blobs[("sharded", "ctas/data/candidate-chunks/manifest.json")] = json.dumps(manifest).encode()
        blobs.update({("sharded", path): raw for path, raw in chunks.items()})
        with patch.object(EXPORTER, "git_blob", side_effect=lambda _repo, ref, path: blobs.get((ref, path))):
            self.assertEqual(EXPORTER.git_catalog_document(ROOT, "legacy"), legacy)
            self.assertEqual(EXPORTER.git_catalog_document(ROOT, "sharded")["candidates"], ordered)

    def test_source_accounting_reconciles_from_every_event(self):
        aggregate = {
            "applicableSourceEvaluations": 0,
            "executedQueryReceipts": 0,
            "dataBearingSourceEvaluations": 0,
            "outcomeCounts": {},
        }
        for candidate in self.snapshot["candidates"]:
            accounting = candidate["source_accounting"]
            matrix = candidate["source_matrix"]
            receipt_extensions = candidate["compatibility_provenance"]["receiptProvenance"]
            self.assertEqual(accounting["declaredSources"], self.universe["source_count"])
            self.assertEqual(accounting["applicableSources"], len(matrix))
            self.assertEqual(
                accounting["executedQueryReceipts"],
                sum(row["executionState"] == "EXECUTED" for row in receipt_extensions),
            )
            self.assertEqual(
                accounting["dataBearingSources"],
                sum(int(row["retainedRecordCount"]) > 0 for row in matrix),
            )
            reproduced_outcomes = {}
            for row in matrix:
                outcome = row["currentQueryOutcome"]
                reproduced_outcomes[outcome] = reproduced_outcomes.get(outcome, 0) + 1
            self.assertEqual(accounting["outcomeCounts"], dict(sorted(reproduced_outcomes.items())))
            aggregate["applicableSourceEvaluations"] += accounting["applicableSources"]
            aggregate["executedQueryReceipts"] += accounting["executedQueryReceipts"]
            aggregate["dataBearingSourceEvaluations"] += accounting["dataBearingSources"]
            for outcome, count in accounting["outcomeCounts"].items():
                aggregate["outcomeCounts"][outcome] = aggregate["outcomeCounts"].get(outcome, 0) + count
        aggregate["outcomeCounts"] = dict(sorted(aggregate["outcomeCounts"].items()))
        self.assertEqual(self.snapshot["source_accounting"], {
            "declaredSources": self.universe["source_count"],
            **aggregate,
        })

    def test_real_rights_cleared_regression_records_remain_inspectable(self):
        by_name = {row["name"]: row for row in self.snapshot["candidates"]}
        multi = by_name["AT2026zxr"]
        self.assertEqual(multi["event_id"], "3f55528c-f552-4513-933b-9c982b8b675f")
        providers = {row["provider"] for row in multi["follow_up"]["observations"]}
        self.assertTrue({"atlas", "rubin-fink", "rubin-lasair"} <= providers)

        stale = by_name["SN2026wpd"]
        self.assertEqual(stale["event_id"], "1a6053f4-5660-4228-ad6a-c0a2f9c553f5")
        self.assertTrue(any(
            row["retainedEvidenceState"] == "STALE_LAST_GOOD_RETAINED"
            for row in stale["source_matrix"]
        ))

        no_match = by_name["AT2026zxx"]
        self.assertEqual(no_match["event_id"], "2ce4c4eb-dd3a-485e-9f34-e46ba0553883")
        self.assertTrue(any(
            row["currentQueryOutcome"] == "SEARCHED_NO_MATCH"
            for row in no_match["source_matrix"]
        ))

        revision = by_name["NuEm-220601A-118386"]
        self.assertEqual(revision["event_id"], "c72e4f9e-ad8d-4573-bb15-917a911b5a6b")
        signals = revision["follow_up"]["messenger_signals"]
        self.assertGreaterEqual(len(signals), 2)
        self.assertTrue(any(row.get("retracted") for row in signals))
        self.assertTrue(any(row.get("supersedes_provider_signal_id") for row in signals))

    def test_release_history_is_unique_and_git_grounded(self):
        history = json.loads((self.data_dir / "release-history.json").read_text())
        checksums = [row["catalog_content_checksum_sha256"] for row in history["entries"]]
        self.assertEqual(len(checksums), len(set(checksums)))
        self.assertTrue(all(
            row.get("history_basis") in {
                "git-verified-public-candidate-count-transition",
                "semantic-diff-from-public-git-base",
            }
            for row in history["entries"]
        ))

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

#!/usr/bin/env python3
"""Focused tests for the public CTAS static-catalog contract and artifacts."""

from __future__ import annotations

import csv
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
import uuid
import xml.etree.ElementTree as ET
from copy import deepcopy
from datetime import datetime, timedelta, timezone
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

    def test_evidence_provider_labels_fold_into_registry_sources(self):
        # The IRSA ZTF adapter labels its data-release photometry "ztf-irsa-release"; the
        # registry source that retrieved it is "ztf-irsa". Every alias must name a source the
        # exporter already knows, or the closure gates would refuse the release.
        self.assertEqual(EXPORTER.provider_source_key("ztf-irsa-release"), "ztf-irsa")
        self.assertEqual(EXPORTER.provider_source_key(" ZTF-IRSA-Release "), "ztf-irsa")
        self.assertEqual(EXPORTER.provider_source_key("rubin-fink"), "rubin-fink")
        self.assertEqual(EXPORTER.provider_source_key(None), "")
        for alias, source_key in EXPORTER.PROVIDER_SOURCE_ALIASES.items():
            self.assertNotEqual(alias, source_key)
            self.assertIn(source_key, EXPORTER.SOURCE_FAMILY_MAP)

    def test_collector_gcn_mission_sources_resolve_in_the_source_universe(self):
        # The collector registers CHIME/FRB, DSA-110, MAXI/GSC, BOOM and SVOM as sources
        # with these exact ids (supernova_watch/sources.py, 2026-10-19). Each must already
        # be a key the exporter publishes, with a reviewed family and a GCN
        # represented-through path, so the closure and disposition gates cannot meet an
        # unknown label when a database carries those registry rows.
        contract_keys = {row[0] for row in EXPORTER.ADDITIONAL_SOURCE_CONTRACTS}
        for source_key in ("chime", "dsa110-gcn", "maxi", "boom-gcn", "svom-gcn"):
            with self.subTest(source_key=source_key):
                self.assertEqual(
                    EXPORTER.source_family(source_key), "multimessenger-and-high-energy"
                )
                self.assertEqual(EXPORTER.provider_source_key(source_key), source_key)
                self.assertTrue(
                    {"gcn", "gcn-high-energy"} & set(EXPORTER.REPRESENTED_THROUGH[source_key]),
                    source_key,
                )
                self.assertTrue(
                    source_key in contract_keys or source_key == "svom-gcn",
                    f"{source_key} has neither a placeholder contract nor a registry precedent",
                )
        # Mission records themselves are labelled "gcn", the registry source every GCN
        # connector shares; discovery-survey labels fold into the same keys.
        self.assertEqual(EXPORTER.provider_source_key("gcn"), "gcn")
        for survey, source_key in (("Chime", "chime"), ("CHIME", "chime"), ("DSA-110", "dsa110-gcn"),
                                   ("MAXI", "maxi"), ("BOOM", "boom-gcn")):
            self.assertEqual(EXPORTER.SURVEY_SOURCE_ALIASES[survey], source_key)

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
        candidate = event(follow_up_counts={"observations": 5, "spectra": 0, "messenger_signals": 0})
        candidate["record_role"], candidate["ranking_channel"] = EXPORTER.record_role_for(candidate)
        candidate["score_model"] = EXPORTER.score_model_for(
            candidate, datetime(2026, 9, 3, tzinfo=timezone.utc)
        )
        explanation = EXPORTER.score_explanation_for(candidate)
        self.assertIn("existing observation coverage reduces the score by 7.5", explanation)
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


class LocalStoreExceptionTests(unittest.TestCase):
    """A damaged page is not the same as "no evidence"."""

    def setUp(self):
        EXPORTER.LOCAL_STORE_READ_FAILURES.clear()

    tearDown = setUp

    class _Cursor:
        """A cursor whose multi-id read fails, exactly as a damaged page does."""

        def __init__(self, unreadable):
            self.unreadable = set(unreadable)

        def execute(self, statement, parameters):
            if len(parameters) > 1:
                raise __import__("sqlite3").DatabaseError("database disk image is malformed")
            event_id = parameters[0]
            if event_id in self.unreadable:
                raise __import__("sqlite3").DatabaseError("database disk image is malformed")
            return self

        def fetchall(self):
            return []

    def test_an_unreadable_range_is_narrowed_to_its_records_and_declared(self):
        cursor = self._Cursor({"bad-1", "bad-2"})
        rows = EXPORTER.rows_by_event(
            cursor, ["ok-1", "bad-1", "ok-2", "bad-2"], "SELECT x FROM t WHERE event_id IN ({ids})"
        )
        self.assertEqual(rows, {})
        self.assertEqual(len(EXPORTER.LOCAL_STORE_READ_FAILURES), 1)
        failure = EXPORTER.LOCAL_STORE_READ_FAILURES[0]
        self.assertEqual(sorted(failure["unreadable_event_ids"]), ["bad-1", "bad-2"])
        self.assertEqual(failure["unreadable_event_count"], 2)
        self.assertIn("malformed", failure["error"])

    def test_a_readable_store_declares_no_exception(self):
        cursor = self._Cursor(set())
        EXPORTER.rows_by_event(cursor, ["ok-1"], "SELECT x FROM t WHERE event_id IN ({ids})")
        self.assertEqual(EXPORTER.LOCAL_STORE_READ_FAILURES, [])


class ScoreModelTests(unittest.TestCase):
    """The score is recomputed for each release, and only where it means something."""

    RELEASE = datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc)

    def scored(self, **updates):
        candidate = event(**updates)
        role, channel = EXPORTER.record_role_for(candidate)
        candidate["record_role"] = role
        candidate["ranking_channel"] = channel
        candidate["default_leaderboard_eligible"] = role in EXPORTER.TARGET_ROLES
        candidate["score_model"] = EXPORTER.score_model_for(candidate, self.RELEASE)
        candidate["ctas_score"] = candidate["score_model"]["final_score"]
        return candidate

    def term(self, model, code):
        return next(row for row in model["terms"] if row["code"] == code)

    def test_recency_decays_with_wall_clock_alone(self):
        """No candidate mutation, a later release, less recency."""
        candidate = event(discovery_time="2026-09-02T12:00:00Z")
        candidate["record_role"], candidate["ranking_channel"] = EXPORTER.record_role_for(candidate)
        early = EXPORTER.score_model_for(candidate, datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc))
        late = EXPORTER.score_model_for(candidate, datetime(2026, 9, 3, 18, 0, tzinfo=timezone.utc))
        self.assertGreater(
            self.term(early, "recency_points")["points"],
            self.term(late, "recency_points")["points"],
        )
        self.assertEqual(early["score_clock"], EXPORTER.SCORE_CLOCK_REFERENCE)
        self.assertEqual(late["score_clock"], EXPORTER.SCORE_CLOCK_REFERENCE)
        self.assertEqual(self.term(late, "recency_points")["reference_time"], "2026-09-02T12:00:00Z")

    def test_a_record_whose_score_did_not_change_keeps_identical_bytes(self):
        """The release clock lives once per release, so unchanged records do not churn.

        Every release used to rewrite all 4096 detail roots because each record
        restated the clock and ticking ages ("473.07 h before this release").
        """
        candidate = event(
            discovery_time="2026-08-01T06:00:00Z", discovery_magnitude=19.5,
            follow_up={"observations": [{"observed_at": "2026-08-10T00:00:00Z", "detection": True}]},
            follow_up_counts={"observations": 1, "spectra": 0, "messenger_signals": 0},
        )
        candidate["record_role"], candidate["ranking_channel"] = EXPORTER.record_role_for(candidate)
        clocks = [datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc),
                  datetime(2026, 9, 3, 0, 30, tzinfo=timezone.utc),
                  datetime(2026, 9, 9, 17, 5, tzinfo=timezone.utc)]
        models = [EXPORTER.score_model_for(candidate, clock) for clock in clocks]
        encoded = {json.dumps(model, sort_keys=True) for model in models}
        self.assertEqual(len(encoded), 1, "an unchanged record must serialize identically")
        model = models[0]
        self.assertTrue(model["inside_follow_up_window"])
        self.assertEqual(model["newest_evidence_at"], "2026-08-10T00:00:00Z")
        self.assertIn("No retained observation since 2026-08-10.", model["why_now"])
        self.assertNotIn("before this release", json.dumps(model))
        for retired in ("score_as_of", "valid_until", "evidence_age_days"):
            self.assertNotIn(retired, model)
        # The release clocks themselves still differ, once, in the release manifest.
        self.assertNotEqual(EXPORTER.score_clock_for(clocks[0]), EXPORTER.score_clock_for(clocks[1]))

    def test_an_archival_record_is_stable_and_states_the_date_it_left_the_window(self):
        candidate = event(discovery_time="2021-03-01T00:00:00Z")
        candidate["record_role"], candidate["ranking_channel"] = EXPORTER.record_role_for(candidate)
        first = EXPORTER.score_model_for(candidate, datetime(2026, 9, 3, tzinfo=timezone.utc))
        later = EXPORTER.score_model_for(candidate, datetime(2026, 10, 3, tzinfo=timezone.utc))
        self.assertEqual(first, later)
        self.assertFalse(first["inside_follow_up_window"])
        self.assertIn("Archival: newest retained evidence dates from 2021-03-01.", first["why_now"])
        reasons = {row["reason"] for row in first["not_applicable"]}
        self.assertTrue(any("dates from 2021-03-01" in reason for reason in reasons))

    def test_a_young_record_still_rescores_at_every_release(self):
        candidate = event(discovery_time="2026-09-02T23:00:00Z")
        candidate["record_role"], candidate["ranking_channel"] = EXPORTER.record_role_for(candidate)
        first = EXPORTER.score_model_for(candidate, datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc))
        later = EXPORTER.score_model_for(candidate, datetime(2026, 9, 3, 0, 30, tzinfo=timezone.utc))
        self.assertGreater(first["final_score"], later["final_score"])

    def test_an_august_discovery_cannot_keep_its_august_recency_in_september(self):
        """The audited failure: AT2026wtb held +19.65 recency a month later."""
        candidate = self.scored(name="AT2026wtb", discovery_time="2026-08-02T00:00:00Z",
                                score_factors={"recency_points": 19.65})
        self.assertEqual(self.term(candidate["score_model"], "recency_points")["points"], 0.0)
        self.assertEqual(
            candidate["score_model"]["recorded_factors_at_ingest"]["recency_points"], 19.65,
            "the frozen factor stays visible as provenance, but never scores",
        )

    def test_missing_spectrum_is_not_applicable_off_the_optical_channel(self):
        for event_type in ("fast-radio-burst", "high-energy-neutrino-track",
                           "high-energy-trigger", "terrestrial-gamma-ray-flash",
                           "solar-flare", "x-ray-transient", "gamma-ray-transient"):
            with self.subTest(event_type=event_type):
                candidate = self.scored(event_type=event_type)
                term = self.term(candidate["score_model"], "spectroscopy_gap_points")
                self.assertFalse(term["applicable"])
                self.assertEqual(term["points"], 0.0)
                self.assertIn("spectroscopy_gap_points",
                              {row["code"] for row in candidate["score_model"]["not_applicable"]})

    def test_missing_spectrum_still_applies_to_an_optical_target(self):
        candidate = self.scored(event_type="optical-transient")
        term = self.term(candidate["score_model"], "spectroscopy_gap_points")
        self.assertTrue(term["applicable"])
        self.assertEqual(term["points"], 7.0)

    def test_single_channel_records_receive_no_messenger_diversity_bonus(self):
        for name, event_type, messenger in (
            ("CALET-260101", "high-energy-trigger", "gamma-ray"),
            ("Chime-1175472802", "fast-radio-burst", "radio"),
            ("EP260830a", "x-ray-transient", "x-ray"),
        ):
            with self.subTest(name=name):
                candidate = self.scored(
                    name=name, event_type=event_type, primary_messenger=messenger,
                    follow_up={"messenger_signals": [{"messenger": messenger}]},
                    follow_up_counts={"messenger_signals": 1, "observations": 0, "spectra": 0},
                    score_factors={"multimessenger_points": 4.0},
                )
                self.assertEqual(candidate["score_model"]["multimessenger_bonus"], 0.0)
                self.assertIn("at least two", candidate["score_model"]["multimessenger_basis"])

    def test_two_independently_retained_channels_do_earn_the_bonus(self):
        candidate = self.scored(
            follow_up={"messenger_signals": [{"messenger": "neutrino", "detection": True}], "observations": [
                {"observed_at": "2026-09-02T00:00:00Z", "detection": True}]},
            follow_up_counts={"messenger_signals": 1, "observations": 1, "spectra": 0},
        )
        self.assertEqual(candidate["score_model"]["multimessenger_bonus"], 8.0)

    def test_a_zero_probability_alternative_is_not_a_classification_conflict(self):
        candidate = self.scored(follow_up={"classifications": [
            {"classification": "TGF", "probability": 1.0},
            {"classification": "n/a", "probability": 0.0},
        ]})
        self.assertEqual(self.term(candidate["score_model"], "classification_conflict_points")["points"], 0.0)

    def test_wavelengths_are_not_distinct_physical_messengers(self):
        for messenger in ("x-ray", "radio", "gamma-ray"):
            candidate = self.scored(follow_up={
                "messenger_signals": [{"messenger": messenger, "detection": True}],
                "observations": [{"detection": True}],
            })
            self.assertEqual(EXPORTER._retained_messenger_channels(candidate), ["electromagnetic"])
            self.assertEqual(candidate["score_model"]["multimessenger_bonus"], 0)

    def test_unconfirmed_or_inactive_signals_do_not_earn_a_bonus(self):
        for update in ({"detection": None}, {"detection": False}, {"retracted": True},
                       {"superseded": True}, {"role": "upper-limit"}, {"messenger": "multimessenger"}):
            signal = {"messenger": "neutrino", "detection": True, **update}
            candidate = self.scored(follow_up={"messenger_signals": [signal], "observations": [{"detection": True}]})
            self.assertEqual(candidate["score_model"]["multimessenger_bonus"], 0)
        candidate = self.scored(follow_up={"messenger_signals": [{"messenger": "neutrino", "detection": True}],
                                         "observations": [{"detection": False}]})
        self.assertEqual(candidate["score_model"]["multimessenger_bonus"], 0)

    def test_compact_score_metadata_matches_the_dossier(self):
        candidate = self.scored(ctas_score=12.34)
        clock = EXPORTER.score_clock_for(self.RELEASE)
        row = dict(zip(EXPORTER.CATALOG_CANDIDATE_COLUMNS, EXPORTER.compact_candidate_row(candidate, clock)))
        model = candidate["score_model"]
        self.assertEqual(model["recorded_score_at_ingest"], 12.34)
        self.assertEqual(model["score_clock"], EXPORTER.SCORE_CLOCK_REFERENCE)
        self.assertEqual(row["score_as_of"], clock["score_as_of"])
        self.assertEqual(row["score_valid_until"], clock["valid_until"])
        self.assertEqual(row["score_method_version"], model["method_version"])
        self.assertEqual(row["score_applicable_terms"], model["applicable_terms"])
        code = (ROOT / "scripts/export_ctas_snapshot.py").read_text()
        self.assertEqual(code.count('candidate["score_model"] = score_model_for(candidate, generated_dt)'), 1)
        self.assertIn('"score_method_version": SCORE_METHOD_VERSION', code)

    def test_notice_revision_pipeline_removes_retracted_messenger_from_score(self):
        rows = EXPORTER.derive_messenger_revisions([
            {"provider": "gcn", "provider_signal_id": "event:r0:initial", "messenger": "neutrino", "role": "trigger", "detection": True},
            {"provider": "gcn", "provider_signal_id": "event:r1:retraction", "messenger": "neutrino", "role": "retraction", "detection": False},
        ])
        self.assertTrue(rows[0]["superseded"])
        self.assertTrue(rows[1]["retracted"])
        candidate = self.scored(follow_up={"messenger_signals": rows, "observations": [{"detection": True}]})
        self.assertEqual(candidate["score_model"]["detected_physical_messengers"], ["electromagnetic"])
        self.assertEqual(candidate["score_model"]["multimessenger_bonus"], 0)

    def test_a_subtype_refinement_is_not_a_classification_conflict(self):
        candidate = self.scored(follow_up={"classifications": [
            {"classification": "SN Ia", "probability": 0.7},
            {"classification": "SN Ia-91T", "probability": 0.3},
        ]})
        self.assertEqual(self.term(candidate["score_model"], "classification_conflict_points")["points"], 0.0)

    def test_two_incompatible_active_assertions_are_a_conflict(self):
        candidate = self.scored(follow_up={"classifications": [
            {"classification": "SN Ia", "probability": 0.6},
            {"classification": "SN II", "probability": 0.4},
        ]})
        self.assertEqual(self.term(candidate["score_model"], "classification_conflict_points")["points"], 8.0)

    def test_a_retracted_assertion_cannot_create_a_conflict(self):
        candidate = self.scored(follow_up={"classifications": [
            {"classification": "SN Ia", "probability": 0.6},
            {"classification": "SN II", "probability": 0.4, "retracted": 1},
        ]})
        self.assertEqual(self.term(candidate["score_model"], "classification_conflict_points")["points"], 0.0)

    def test_terminal_records_are_zero_and_never_leaderboard_targets(self):
        for terminal_status in ("retracted", "bogus"):
            with self.subTest(status=terminal_status):
                candidate = self.scored(status=terminal_status, discovery_time="2026-09-02T23:00:00Z")
                self.assertEqual(candidate["ctas_score"], 0.0)
                self.assertEqual(candidate["score_model"]["status_override"], terminal_status)
                self.assertEqual(candidate["record_role"], "retracted-event")
                self.assertFalse(candidate["default_leaderboard_eligible"])

    def test_known_terrestrial_and_solar_records_stay_out_of_the_leaderboard(self):
        for event_type in ("terrestrial-gamma-ray-flash", "solar-flare"):
            with self.subTest(event_type=event_type):
                candidate = self.scored(event_type=event_type)
                self.assertFalse(candidate["default_leaderboard_eligible"])
                self.assertEqual(candidate["ranking_channel"], "contextual-non-target")

    def test_every_score_reproduces_exactly_from_its_applicable_terms(self):
        candidate = self.scored(discovery_time="2026-09-02T21:00:00Z", discovery_magnitude=16.4,
                                follow_up_counts={"observations": 3, "spectra": 0, "messenger_signals": 0})
        model = candidate["score_model"]
        applied = [row for row in model["terms"] if row["applicable"]]
        core = model["baseline"] + sum(row["points"] for row in applied)
        expected = round(max(0.0, min(100.0, max(0.0, min(100.0, core)) + model["multimessenger_bonus"])), 2)
        self.assertEqual(model["final_score"], expected)
        self.assertEqual(candidate["ctas_score"], expected)
        self.assertIn("= " + f"{expected:g}", model["arithmetic"])

    def test_the_score_is_published_with_the_clock_it_was_computed_for(self):
        model = self.scored()["score_model"]
        clock = EXPORTER.score_clock_for(self.RELEASE)
        self.assertEqual(model["score_clock"], EXPORTER.SCORE_CLOCK_REFERENCE)
        self.assertEqual(clock["schema"], EXPORTER.SCORE_CLOCK_SCHEMA)
        self.assertEqual(clock["score_as_of"], "2026-09-03T00:00:00Z")
        self.assertEqual(clock["valid_until"], "2026-09-03T00:30:00Z")
        self.assertEqual(clock["method_version"], EXPORTER.SCORE_METHOD_VERSION)
        self.assertEqual(model["method_version"], EXPORTER.SCORE_METHOD_VERSION)
        self.assertIn("not a probability", model["claim_boundary"])


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


class SuggestedFollowUpTests(unittest.TestCase):
    """The clock-free follow-up draft: objectives only for eligible targets."""

    def target(self, **updates):
        return event(**{"record_role": "follow-up-target-candidate", "ranking_channel": "optical", "status": "candidate", **updates})

    def test_unclassified_optical_target_gets_imaging_then_spectroscopy_nightly(self):
        block = EXPORTER.suggested_follow_up_for(self.target())
        self.assertEqual(block["schema"], "ctas-suggested-follow-up.v1")
        self.assertEqual([row["kind"] for row in block["objectives"]], ["imaging", "spectroscopy"])
        self.assertEqual([row["priority"] for row in block["objectives"]], [1, 2])
        self.assertEqual(block["objectives"][0]["closes_gap"], "follow-up-photometry")
        self.assertEqual(block["objectives"][1]["closes_gap"], "spectrum")
        self.assertEqual(block["objectives"][1]["suggested_evidence_type"], "rights-cleared spectrum or spectrum metadata")
        self.assertEqual(block["cadence"]["code"], "nightly")
        self.assertIn("No public classification is retained.", block["basis"])
        self.assertIn("No public spectrum is retained.", block["basis"])
        self.assertIn("Discovered 2026-08-22.", block["basis"])
        self.assertTrue(block["eligibility"]["is_target"])
        self.assertEqual(block["eligibility"]["newest_evidence_at"], "2026-08-22T00:00:00Z")
        self.assertEqual(block["latest_magnitude"], {
            "value": 17.2, "band": None, "system": None, "observed_at": "2026-08-22T00:00:00Z",
            "provider": "ZTF", "basis": "discovery-magnitude",
        })
        self.assertEqual(block["claim_boundary"], EXPORTER.SUGGESTED_FOLLOW_UP_CLAIM)
        self.assertIn("not an exposure-time calculation or observing request", block["claim_boundary"])

    def test_the_draft_is_deterministic_and_clock_free(self):
        candidate = self.target(discovery_time="2019-01-01T00:00:00Z")
        first = EXPORTER.suggested_follow_up_for(deepcopy(candidate))
        second = EXPORTER.suggested_follow_up_for(deepcopy(candidate))
        self.assertEqual(first, second)
        serialized = json.dumps(first)
        # Ages, "recent" and today's date would rewrite every dossier each release.
        self.assertNotIn(" ago", serialized)
        self.assertNotIn("recent", serialized.lower())
        self.assertNotIn(datetime.now(timezone.utc).strftime("%Y-%m-%d"), serialized)
        self.assertEqual(first["basis"][0], "Discovered 2019-01-01.")

    def test_latest_magnitude_prefers_the_newest_retained_detection(self):
        candidate = self.target(
            follow_up_counts={**event()["follow_up_counts"], "observations": 3},
            follow_up={"observations": [
                {"observed_at": "2026-08-23T00:00:00Z", "detection": 1, "magnitude": 17.9, "band": "g", "magnitude_system": "AB", "provider": "a"},
                {"observed_at": "2026-08-24T00:00:00Z", "detection": 1, "magnitude": 18.123456, "band": "r", "magnitude_system": "AB", "provider": "b"},
                {"observed_at": "2026-08-25T00:00:00Z", "detection": 0, "limiting_magnitude": 20.5, "band": "r", "provider": "c"},
                {"observed_at": "2026-08-26T00:00:00Z", "detection": 1, "magnitude": 99.0, "band": "r", "provider": "d"},
                {"observed_at": "2026-08-27T00:00:00Z", "detection": 1, "magnitude": 18.5, "band": "r", "provider": "e", "superseded": 1},
            ]},
        )
        block = EXPORTER.suggested_follow_up_for(candidate)
        self.assertEqual(block["latest_magnitude"], {
            "value": 18.123, "band": "r", "system": "AB", "observed_at": "2026-08-24T00:00:00Z",
            "provider": "b", "basis": "latest-retained-detection",
        })
        self.assertIsNone(block["objectives"][0]["closes_gap"], "photometry exists, so imaging closes no gap")
        self.assertIn("No retained observation since 2026-08-26.", block["basis"], "the superseded row does not count")

    def test_classified_target_with_a_spectrum_is_monitored_weekly(self):
        block = EXPORTER.suggested_follow_up_for(self.target(
            classification="SN Ia", follow_up_counts={**event()["follow_up_counts"], "spectra": 1},
        ))
        self.assertEqual([row["kind"] for row in block["objectives"]], ["imaging", "monitoring"])
        self.assertEqual(block["cadence"]["code"], "weekly")
        self.assertNotIn("No public spectrum is retained.", block["basis"])

    def test_disagreeing_classifications_ask_for_a_spectrum_even_when_one_exists(self):
        block = EXPORTER.suggested_follow_up_for(self.target(
            classification="SN Ia", follow_up_counts={**event()["follow_up_counts"], "spectra": 1},
            follow_up={"classifications": [
                {"classification": "SN Ia", "probability": 0.6}, {"classification": "SN II", "probability": 0.4},
            ]},
        ))
        self.assertIn("Active classification assertions disagree.", block["basis"])
        spectroscopy = block["objectives"][1]
        self.assertEqual(spectroscopy["kind"], "spectroscopy")
        self.assertEqual(spectroscopy["closes_gap"], "classification")
        self.assertIn("disagreeing", spectroscopy["text"])
        self.assertEqual(block["cadence"]["code"], "nightly")

    def test_multimessenger_alert_gets_a_rapid_counterpart_search(self):
        block = EXPORTER.suggested_follow_up_for(event(
            record_role="localization-region-alert", ranking_channel="neutrino-multimessenger",
            primary_messenger="neutrino", discovery_magnitude=None, classification=None,
        ))
        self.assertEqual([row["kind"] for row in block["objectives"]], ["counterpart"])
        self.assertEqual(block["cadence"]["code"], "rapid")
        self.assertIn("Multimessenger counterpart pending: no optical detection is retained.", block["basis"])
        self.assertFalse(block["eligibility"]["magnitude_present"])
        self.assertIsNone(block["latest_magnitude"]["value"])
        self.assertIn("No magnitude is retained; exposure estimates are not possible.", block["eligibility"]["reasons"])

    def test_retracted_terrestrial_and_coordinate_free_records_get_no_objectives(self):
        for candidate, reason in (
            (self.target(status="retracted", record_role="retracted-event"), "Reported retracted; held out of follow-up."),
            (self.target(status="bogus"), "Reported bogus; held out of follow-up."),
            (event(record_role="known-terrestrial-event", ranking_channel="contextual-non-target"), "Record role known terrestrial event: not a follow-up target."),
            (event(record_role="unvalidated-detector-trigger", ranking_channel="radio"), "Record role unvalidated detector trigger: not a follow-up target."),
            (self.target(ra_deg=None, dec_deg=None), "No valid coordinates are retained."),
        ):
            with self.subTest(reason=reason):
                block = EXPORTER.suggested_follow_up_for(candidate)
                self.assertEqual(block["objectives"], [])
                self.assertEqual(block["cadence"]["code"], "none")
                self.assertFalse(block["eligibility"]["is_target"])
                self.assertIn(reason, block["basis"])

    def test_evidence_types_come_from_the_science_brief_vocabulary(self):
        candidate = self.target(record_completeness=EXPORTER.completeness_for(self.target()))
        brief_vocabulary = {row["suggested_evidence_type"] for row in EXPORTER.science_brief_for(candidate)["missing_information"]}
        block = EXPORTER.suggested_follow_up_for(candidate)
        self.assertEqual({row["kind"] for row in block["objectives"]}, {"imaging", "spectroscopy"})
        self.assertTrue({row["suggested_evidence_type"] for row in block["objectives"]} <= brief_vocabulary, brief_vocabulary)
        self.assertTrue(set(EXPORTER.SUGGESTED_EVIDENCE_TYPES.values()) >= brief_vocabulary - {"source-attributed public evidence"})

    def test_browser_fallback_derivation_agrees_with_the_exporter(self):
        """ctas/followup.js re-derives the block for releases that predate it."""
        cases = [
            self.target(),
            self.target(classification="SN Ia", follow_up_counts={**event()["follow_up_counts"], "spectra": 1}),
            self.target(classification="SN Ia", follow_up_counts={**event()["follow_up_counts"], "spectra": 1},
                        follow_up={"classifications": [{"classification": "SN Ia", "probability": 0.6}, {"classification": "SN II", "probability": 0.4}]}),
            event(record_role="localization-region-alert", ranking_channel="neutrino-multimessenger", primary_messenger="neutrino", discovery_magnitude=None),
            self.target(status="retracted", record_role="retracted-event"),
            event(record_role="known-terrestrial-event", ranking_channel="contextual-non-target"),
            self.target(ra_deg=None, dec_deg=None),
            self.target(follow_up_counts={**event()["follow_up_counts"], "observations": 2}, follow_up={"observations": [
                {"observed_at": "2026-08-23T00:00:00Z", "detection": 1, "magnitude": 17.9, "band": "g", "magnitude_system": "AB", "provider": "a"},
                {"observed_at": "2026-08-24T00:00:00Z", "detection": 1, "magnitude": 18.123456, "band": "r", "magnitude_system": "AB", "provider": "b"},
            ]}),
        ]
        script = (
            "const f=require(process.argv[1]);let input='';process.stdin.on('data',d=>input+=d);"
            "process.stdin.on('end',()=>{process.stdout.write(JSON.stringify(JSON.parse(input).map(c=>f.derive(c))));});"
        )
        result = subprocess.run(
            ["node", "-e", script, str(ROOT / "ctas/followup.js")], input=json.dumps(cases),
            capture_output=True, text=True, check=True,
        )
        for candidate, browser in zip(cases, json.loads(result.stdout)):
            with self.subTest(role=candidate.get("record_role"), status=candidate.get("status")):
                self.assertEqual(browser, EXPORTER.suggested_follow_up_for(candidate))


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


class LinkHealthRebindTests(unittest.TestCase):
    """The publisher binds link-health.json into the report without a second export.

    A synthetic export (a report, its status and the data files it binds) stands in
    for the real one: the rebind must prove the bound bytes are unchanged, bind the
    link-health artifact, flip only the gates whose inputs changed, and leave the
    report self-consistent. Any altered byte must refuse the rebind.
    """

    CATALOG = "c" * 64

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-rebind-")
        self.addCleanup(self.temporary.cleanup)
        self.out = Path(self.temporary.name).resolve() / "data"
        (self.out / "candidate-chunks").mkdir(parents=True)
        self.site = Path(self.temporary.name).resolve() / "site"
        self.site.mkdir()
        self.data = {
            "ctas/data/catalog-index.json": b'{"candidate_rows":[]}\n',
            "ctas/data/candidate-chunks/000.json": b'{"bucket":"000"}\n',
        }
        for name, raw in self.data.items():
            EXPORTER.write_output_artifact(self.out, name, raw)
        self.write_report()

    def write_report(self, gates=None):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        stamp = lambda value: value.isoformat().replace("+00:00", "Z")
        files = {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
                 for name, raw in sorted(self.data.items())}
        gates = gates or [
            {"id": EXPORTER.REQUIRED_ARTIFACTS_GATE_ID, "passed": False, "evidence": "required"},
            {"id": "catalog-population", "passed": True, "evidence": "2 records"},
            {"id": EXPORTER.FRESHNESS_GATE_ID, "passed": True, "evidence": "fresh"},
            {"id": EXPORTER.LINK_HEALTH_GATE_ID, "passed": False, "evidence": "links"},
        ]
        certificate = {
            "schema": EXPORTER.CERTIFICATE_SCHEMA,
            "generated_at": stamp(now),
            "valid_until": stamp(now + timedelta(minutes=EXPORTER.SNAPSHOT_VALIDITY_MINUTES)),
            "status": EXPORTER.certificate_status(gates),
            "catalog_content_checksum_sha256": self.CATALOG,
            "content_release_id": hashlib.sha256(b"".join(
                name.encode() + b"\0" + raw for name, raw in sorted(self.data.items())
            )).hexdigest(),
            "gates": gates,
            "files": files,
        }
        certificate["report_checksum_sha256"] = hashlib.sha256(
            EXPORTER.canonical_report_bytes(certificate)).hexdigest()
        status = {
            "catalog_content_checksum_sha256": self.CATALOG,
            "static_snapshot_verification": EXPORTER.verification_summary(certificate),
            "artifacts": {
                "link_health": {"path": EXPORTER.LINK_HEALTH_PATH, "sha256": None},
                "certification": {"path": "ctas/data/certification.json",
                                  "report_checksum_sha256": certificate["report_checksum_sha256"]},
            },
        }
        (self.out / "certification.json").write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n")
        (self.out / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
        return certificate

    def write_link_health(self, **overrides):
        report = {
            "schema": EXPORTER.LINK_HEALTH_SCHEMA,
            "catalog_content_checksum_sha256": self.CATALOG,
            "structural_status": "passed",
            "live_status": "degraded-provider-unavailable",
            **overrides,
        }
        (self.out / "link-health.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report

    def rebind(self):
        return EXPORTER.rebind_link_health(self.out, self.site)

    def read(self, name):
        return json.loads((self.out / name).read_text())

    def test_binds_the_artifact_and_flips_only_the_link_gates(self):
        self.write_link_health()
        self.assertEqual(self.rebind(), 0)
        certificate, status = self.read("certification.json"), self.read("status.json")
        gates = {gate["id"]: gate for gate in certificate["gates"]}
        self.assertTrue(gates[EXPORTER.LINK_HEALTH_GATE_ID]["passed"])
        self.assertTrue(gates[EXPORTER.FRESHNESS_GATE_ID]["passed"])
        self.assertTrue(gates["catalog-population"]["passed"])
        self.assertEqual(gates[EXPORTER.LINK_HEALTH_GATE_ID]["evidence"], "links")
        # the synthetic export binds no code, so the required-artifact set stays short
        self.assertFalse(gates[EXPORTER.REQUIRED_ARTIFACTS_GATE_ID]["passed"])
        link_raw = (self.out / "link-health.json").read_bytes()
        self.assertEqual(certificate["files"][EXPORTER.LINK_HEALTH_PATH],
                         {"sha256": hashlib.sha256(link_raw).hexdigest(), "bytes": len(link_raw)})
        for name, raw in self.data.items():
            self.assertEqual(certificate["files"][name]["sha256"], hashlib.sha256(raw).hexdigest())
        bound = {**self.data, EXPORTER.LINK_HEALTH_PATH: link_raw}
        self.assertEqual(certificate["content_release_id"], hashlib.sha256(b"".join(
            name.encode() + b"\0" + raw for name, raw in sorted(bound.items()))).hexdigest())
        self.assertEqual(certificate["report_checksum_sha256"], hashlib.sha256(
            EXPORTER.canonical_report_bytes(certificate)).hexdigest())
        self.assertEqual(status["static_snapshot_verification"], EXPORTER.verification_summary(certificate))
        self.assertEqual(status["artifacts"]["link_health"]["sha256"], hashlib.sha256(link_raw).hexdigest())
        self.assertEqual(status["artifacts"]["certification"]["report_checksum_sha256"],
                         certificate["report_checksum_sha256"])
        self.assertEqual(status["catalog_content_checksum_sha256"], self.CATALOG)

    def test_rebind_is_idempotent(self):
        self.write_link_health()
        self.assertEqual(self.rebind(), 0)
        first = (self.out / "certification.json").read_bytes(), (self.out / "status.json").read_bytes()
        self.assertEqual(self.rebind(), 0)
        self.assertEqual(first, ((self.out / "certification.json").read_bytes(), (self.out / "status.json").read_bytes()))

    def test_stale_or_failed_link_health_keeps_the_gate_failed(self):
        for overrides in ({"catalog_content_checksum_sha256": "d" * 64}, {"structural_status": "failed"},
                          {"live_status": "failed-wrong-object"}, {"schema": "ctas.link-health@0.9.0"}):
            with self.subTest(overrides=overrides):
                self.write_report()
                self.write_link_health(**overrides)
                self.assertEqual(self.rebind(), 0)
                certificate = self.read("certification.json")
                gates = {gate["id"]: gate for gate in certificate["gates"]}
                self.assertFalse(gates[EXPORTER.LINK_HEALTH_GATE_ID]["passed"])
                self.assertEqual(certificate["status"], "verification-failed")

    def test_any_altered_bound_byte_refuses_the_rebind(self):
        self.write_link_health()
        chunk = self.out / "candidate-chunks" / "000.json"
        original = chunk.read_bytes()
        chunk.write_bytes(original + b" ")
        self.assertEqual(self.rebind(), 1)
        chunk.write_bytes(original)
        self.assertEqual(self.rebind(), 0)

    def test_a_missing_bound_file_refuses_the_rebind(self):
        self.write_link_health()
        (self.out / "catalog-index.json").unlink()
        self.assertEqual(self.rebind(), 1)

    def test_an_altered_or_foreign_report_refuses_the_rebind(self):
        self.write_link_health()
        certificate = self.read("certification.json")
        certificate["gates"][1]["passed"] = False  # edited after the export: checksum no longer reproduces
        (self.out / "certification.json").write_text(json.dumps(certificate, indent=2, sort_keys=True) + "\n")
        self.assertEqual(self.rebind(), 1)
        self.write_report()
        status = self.read("status.json")
        status["catalog_content_checksum_sha256"] = "e" * 64
        (self.out / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
        self.assertEqual(self.rebind(), 1)
        self.write_report(gates=[{"id": "catalog-population", "passed": True, "evidence": "2 records"}])
        self.assertEqual(self.rebind(), 1, "a report without the re-evaluated gates is refused")

    def test_missing_or_invalid_link_health_refuses_the_rebind(self):
        self.assertEqual(self.rebind(), 1)
        (self.out / "link-health.json").write_text("[]\n")
        self.assertEqual(self.rebind(), 1)

    def test_bound_link_health_bytes_are_the_published_bytes(self):
        report = self.write_link_health()
        (self.out / "link-health.json").write_text(json.dumps(report))  # compact, unlike the checker
        self.assertEqual(self.rebind(), 0)
        raw = (self.out / "link-health.json").read_bytes()
        self.assertEqual(raw, (json.dumps(report, indent=2, sort_keys=True) + "\n").encode())
        self.assertEqual(self.read("certification.json")["files"][EXPORTER.LINK_HEALTH_PATH]["sha256"],
                         hashlib.sha256(raw).hexdigest())

    def test_required_artifact_gate_recognises_the_bound_link_health(self):
        """With every other required artifact bound, link-health completes the set."""
        self.data = {path: b"x\n" for path in EXPORTER.REQUIRED_PUBLIC_ARTIFACTS
                     if path != EXPORTER.LINK_HEALTH_PATH}
        for name, raw in self.data.items():
            target = self.site / name if name in EXPORTER.BOUND_CODE_PATHS else EXPORTER.output_path_for_public_artifact(self.out, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        self.write_report()
        self.write_link_health()
        self.assertEqual(self.rebind(), 0)
        gates = {gate["id"]: gate for gate in self.read("certification.json")["gates"]}
        self.assertTrue(gates[EXPORTER.REQUIRED_ARTIFACTS_GATE_ID]["passed"])
        self.assertEqual(self.read("certification.json")["status"], "verified-static-snapshot")


class PublisherPageAllowlistTests(unittest.TestCase):
    """Exercise the real shell allowlist with temporary Git commit trees."""

    def setUp(self):
        self.publisher = (ROOT / "scripts/publish_ctas.sh").read_text()
        start = self.publisher.index("# --------------------------------------------------------- collect catalog pages")
        end = self.publisher.index("# ------------------------------------------------------------------ tests", start)
        self.block = self.publisher[start:end]
        self.assertGreater(start, self.publisher.rindex("export_ctas_snapshot.py --database"))
        self.assertGreater(start, self.publisher.index('cd "$SITE"'))
        self.temporary = tempfile.TemporaryDirectory(prefix="ctas-publisher-pages-")
        self.addCleanup(self.temporary.cleanup)
        self.site = Path(self.temporary.name).resolve()
        self.pages_dir = self.site / "ctas/data/catalog-pages"
        self.pages_dir.mkdir(parents=True)
        chunks = self.site / "ctas/data/candidate-chunks"
        chunks.mkdir()
        (chunks / "manifest.json").write_text('{"chunks": []}')
        # The DataLink block that follows the page block collects from its own manifest
        # (Track 1); an empty release declares no documents.
        links = self.site / "ctas/data/links"
        links.mkdir()
        (links / "manifest.json").write_text('{"documents": [], "document_count": 0, "event_count": 0}')
        # The feed block (Track 1, part C) collects from the feeds index the same way.
        feeds = self.site / "ctas/data/feeds"
        feeds.mkdir()
        (feeds / "index.json").write_text('{"feeds": [], "feed_count": 0}')
        self.git("init", "-q")
        self.git("config", "user.name", "CTAS fixture")
        self.git("config", "user.email", "ctas-fixture@example.invalid")
        self.write_pages(0)
        self.git("add", "ctas")
        self.git("commit", "-qm", "empty catalog fixture")

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.site, text=True,
                              capture_output=True, check=True).stdout

    def write_pages(self, count):
        pages = []
        for index in range(1, count + 1):
            relative = f"ctas/data/catalog-pages/{index:04d}.json"
            raw = json.dumps({"candidate_rows": [[f"fixture-{index}"]]}).encode()
            (self.site / relative).write_bytes(raw)
            pages.append({"page": index, "path": relative, "bytes": len(raw),
                          "sha256": hashlib.sha256(raw).hexdigest(), "candidate_count": 1})
        self.manifest = {"pages": pages, "page_count": count, "candidate_count": count}
        self.write_manifest()

    def write_manifest(self):
        (self.pages_dir / "manifest.json").write_text(json.dumps(self.manifest))

    def collect(self, commit=True):
        script = ('set -euo pipefail\ncd "$1"\n'
                  'PUBLIC_FILES=(ctas/data/catalog-pages/manifest.json)\n'
                  'die() { echo "$*" >&2; exit 1; }\nsay() { :; }\n' + self.block)
        if commit:
            script += '\ngit add -- "${PUBLIC_FILES[@]}"\ngit commit -qm "page fixture release"\n'
        return subprocess.run(["/bin/bash", "-c", script, "publisher-page-test", str(self.site)],
                              cwd=self.site.parent, text=True, capture_output=True)

    def test_new_growth_and_retired_pages_reach_the_commit_tree(self):
        (self.site / "unrelated-private.txt").write_text("must remain untracked")
        for count in (2, 3, 1):
            with self.subTest(count=count):
                self.write_pages(count)
                result = self.collect()
                self.assertEqual(result.returncode, 0, result.stderr)
                actual = set(self.git("ls-tree", "-r", "--name-only", "HEAD",
                                      "ctas/data/catalog-pages").splitlines())
                expected = {"ctas/data/catalog-pages/manifest.json"} | {
                    f"ctas/data/catalog-pages/{i:04d}.json" for i in range(1, count + 1)}
                self.assertEqual(actual, expected)
                self.assertEqual(self.git("ls-files", "unrelated-private.txt"), "")
                self.assertTrue((self.site / "unrelated-private.txt").exists())

    def test_invalid_pages_fail_before_retirement_or_commit(self):
        self.write_pages(3)
        self.assertEqual(self.collect().returncode, 0)
        head = self.git("rev-parse", "HEAD")
        for defect in ("missing", "bytes", "hash", "duplicate", "gap", "traversal",
                       "symlink", "row-count", "page-count", "total-count"):
            with self.subTest(defect=defect):
                first = self.pages_dir / "0001.json"
                if first.is_symlink():
                    first.unlink()
                self.write_pages(1)
                row = self.manifest["pages"][0]
                if defect == "missing": first.unlink()
                elif defect == "bytes": row["bytes"] += 1
                elif defect == "hash": row["sha256"] = "0" * 64
                elif defect == "duplicate":
                    self.manifest["pages"].append(dict(row)); self.manifest["page_count"] = 2
                elif defect == "gap": row["page"] = 2
                elif defect == "traversal": row["path"] = "ctas/data/catalog-pages/../../private.txt"
                elif defect == "symlink":
                    copied = self.site / "redirected.json"
                    copied.write_bytes(first.read_bytes()); first.unlink(); first.symlink_to(copied)
                elif defect == "row-count": row["candidate_count"] = 2
                elif defect == "page-count": self.manifest["page_count"] = 2
                elif defect == "total-count": self.manifest["candidate_count"] = 2
                self.write_manifest()
                self.assertNotEqual(self.collect().returncode, 0)
                self.assertEqual(self.git("rev-parse", "HEAD"), head)
                self.assertTrue((self.pages_dir / "0003.json").is_file())

    def test_repair_only_untracked_pages_are_not_mistaken_for_no_change(self):
        """A release whose only change is a page the previous release lacked must publish.

        The unchanged decision is data_branch.sh comparing the complete new release tree
        with the published one, so a file that was never in the previous release always
        makes the trees differ (see test_data_branch.py for the end-to-end check)."""
        self.assertIn('10)\n    say "public artifacts already match the published $DATA_BRANCH release', self.publisher)
        self.assertNotIn("git diff --quiet HEAD", self.publisher)
        helper = (ROOT / "scripts/data_branch.sh").read_text()
        self.assertIn('[ "$(store rev-parse "$CURRENT^{tree}")" = "$TREE" ]', helper)
        self.write_pages(1)
        self.assertEqual(self.collect(commit=False).returncode, 0)

class CertificateAndArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data_dir = ROOT / "ctas/data"
        cls.summary_raw = (cls.data_dir / "live-summary.json").read_bytes()
        cls.summary = json.loads(cls.summary_raw)
        cls.page_manifest = json.loads((cls.data_dir / "catalog-pages/manifest.json").read_text())
        cls.source_matrix_patterns = json.loads(
            (cls.data_dir / "source-matrix-patterns.json").read_text()
        )["patterns"]
        cls.index_raw = (cls.data_dir / "catalog-index.json").read_bytes()
        cls.index = json.loads(cls.index_raw)
        cls.index_candidates = EXPORTER.inflate_catalog_candidates(cls.index)
        cls.manifest = json.loads((cls.data_dir / "candidate-chunks/manifest.json").read_text())
        if cls.manifest.get("schema") != EXPORTER.CANDIDATE_MANIFEST_SCHEMA:
            raise AssertionError("complete-catalog artifacts do not use the current manifest schema")
        complete_by_id = {}
        for metadata in cls.manifest["chunks"]:
            raw = (ROOT / metadata["path"]).read_bytes()
            if len(raw) != metadata["bytes"] or hashlib.sha256(raw).hexdigest() != metadata["sha256"]:
                raise AssertionError(f"invalid candidate chunk: {metadata['path']}")
            for candidate in EXPORTER.decode_chunk(raw, lambda path: (ROOT / path).read_bytes())["candidates"]:
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

    def test_source_universe_definition_ignores_operational_state(self):
        """Each dossier names this version; polling state must not rewrite every dossier."""
        first = {
            "schema": EXPORTER.SOURCE_UNIVERSE_SCHEMA,
            "generated_at": "2026-08-23T18:00:00Z",
            "sources": [{"source_key": "tns", "contract_version": "1.0.0",
                         "last_attempt_at": "2026-08-23T17:58:00Z", "operational_state": "searched-no-match",
                         "public_disposition_counts": {"searched-no-match": 10},
                         "public_record_counts": {}, "contract_checksum_sha256": "a" * 64}],
        }
        second = deepcopy(first)
        second["generated_at"] = "2026-08-23T18:30:00Z"
        second["sources"][0].update({
            "last_attempt_at": "2026-08-23T18:27:00Z", "operational_state": "searched-with-data",
            "public_disposition_counts": {"searched-no-match": 11},
            "public_record_counts": {"aliases": 1}, "contract_checksum_sha256": "b" * 64,
        })
        self.assertEqual(
            EXPORTER.source_universe_definition_checksum(first),
            EXPORTER.source_universe_definition_checksum(second),
        )
        self.assertNotEqual(
            EXPORTER.source_universe_contract_checksum(first),
            EXPORTER.source_universe_contract_checksum(second),
            "the operational state stays checksum-bound in source-universe.json",
        )
        second["sources"][0]["contract_version"] = "1.0.1"
        self.assertNotEqual(
            EXPORTER.source_universe_definition_checksum(first),
            EXPORTER.source_universe_definition_checksum(second),
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

    def test_first_screen_loads_a_summary_not_the_complete_catalog(self):
        """The page must be usable without downloading every retained record."""
        self.assertEqual(self.summary["schema"], EXPORTER.LIVE_SUMMARY_SCHEMA)
        self.assertLessEqual(len(self.summary_raw), EXPORTER.LIVE_SUMMARY_MAX_BYTES)
        self.assertFalse((self.data_dir / "catalog-bootstrap.json").exists(),
                         "the duplicated bootstrap bytes must not be republished")
        self.assertEqual(
            self.summary["catalog_content_checksum_sha256"],
            self.index["catalog_content_checksum_sha256"],
        )
        # The summary reports the complete catalog size but carries only the
        # first-screen records.
        self.assertEqual(self.summary["candidate_count"], self.index["candidate_count"])
        self.assertLess(len(self.summary["candidate_rows"]), self.summary["candidate_count"])
        self.assertEqual(self.summary["candidate_columns"], list(EXPORTER.CATALOG_CANDIDATE_COLUMNS))
        summary_ids = {
            row[EXPORTER.CATALOG_CANDIDATE_COLUMNS.index("event_id")]
            for row in self.summary["candidate_rows"]
        }
        for group in (
            self.summary["leaderboard"]["event_ids"],
            self.summary["recent_reports"]["event_ids"],
            *self.summary["channel_leaderboards"].values(),
        ):
            self.assertTrue(set(group) <= summary_ids)
        self.assertLessEqual(len(self.summary["leaderboard"]["event_ids"]), EXPORTER.TOP_RANK_LIMIT)
        self.assertEqual(
            len(self.summary["recent_reports"]["newest_event_ids"]),
            min(EXPORTER.NEWEST_REPORT_COUNT, self.summary["recent_reports"]["count"]),
        )

    def test_default_leaderboard_excludes_non_target_records(self):
        """A terrestrial flash or a retraction is searchable, never a target."""
        by_id = {row["event_id"]: row for row in self.index_candidates}
        for event_id in self.summary["leaderboard"]["event_ids"]:
            role = by_id[event_id]["record_role"]
            self.assertEqual(role, "follow-up-target-candidate", f"{event_id} is {role}")

    def test_complete_catalog_is_paginated_within_budget(self):
        pages = self.page_manifest["pages"]
        self.assertEqual(
            sum(row["candidate_count"] for row in pages), self.index["candidate_count"]
        )
        seen = []
        for row in pages:
            raw = (ROOT / row["path"]).read_bytes()
            self.assertEqual(len(raw), row["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"])
            self.assertLessEqual(len(raw), EXPORTER.CATALOG_PAGE_MAX_BYTES)
            page = json.loads(raw)
            self.assertEqual(page["candidate_columns"], self.index["candidate_columns"])
            seen.extend(page["candidate_rows"])
        self.assertEqual(seen, self.index["candidate_rows"])

    def test_columnar_index_is_lossless(self):
        self.assertEqual(self.index["schema"], EXPORTER.CATALOG_INDEX_SCHEMA)
        self.assertNotIn("candidates", self.index)
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
            "ctas.html",
            "ctas/schema/astro-evidence-core-0.1.0.schema.json",
            "ctas/astro-evidence.js",
            "ctas/catalog-model.js",
            "ctas/chunk-loader.js",
            "scripts/ctas_chunks.py",
            "scripts/test_ctas_chunks.py",
            "scripts/test_ctas_chunk_loader.js",
            "scripts/test_ctas_publisher_recovery.py",
            "ctas/presentation.js",
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
            "scripts/test_ctas_identity.py",
            "scripts/test_ctas_browser.py",
            "scripts/ctas_interop.py",
            "scripts/test_ctas_interop.py",
            "scripts/validate_ctas_schemas.py",
            "ctas/openapi.yaml",
            "ctas/schemas/common.schema.json",
            "ctas/schemas/candidate-chunk.schema.json",
            "ctas/schemas/status.schema.json",
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
        self.assertIn("At compilation, the final local commit and origin publication bindings had not yet been recorded", app)
        self.assertIn('"failed_gate_ids"', (ROOT / "scripts/export_ctas_snapshot.py").read_text())
        self.assertIn('localPreview ? "Local preview"', app)
        self.assertIn('stale ? "Snapshot out of date"', app)
        self.assertIn('statusCell("Browser check"', app)
        self.assertIn("not a two-minute publication guarantee", app)
        self.assertIn('window.location.protocol === "file:"', app)
        self.assertIn("Open the current public CTAS catalog", app)
        self.assertNotIn("Certified Static Catalog", html + app)
        self.assertNotIn("Static release assurance", html + app)

    def test_public_workspace_defaults_are_calm_without_removing_features(self):
        html = (ROOT / "ctas.html").read_text()
        app = (ROOT / "ctas/app.js").read_text()
        workbench = (ROOT / "ctas/workbench.js").read_text()
        styles = (ROOT / "ctas/ctas.css").read_text()

        self.assertNotIn('class="ctas-contents"', html)
        self.assertNotIn('class="ctas-utility"', html)
        self.assertEqual(html.count('class="ctas-navigation__menu"'), 1)
        self.assertIn('id="ctas-reference"', html)
        self.assertIn('class="ctas-filter-drawer"', html)
        self.assertIn('<section class="ctas-panel ctas-sky ctas-live-stage" id="celestial-sphere"', html)
        self.assertNotIn('<details class="ctas-panel ctas-sky"', html)
        self.assertIn('aria-labelledby="ctas-sky-title"', html)
        self.assertIn('id="ctas-sky-canvas" tabindex="0"', html)
        self.assertIn('id="ctas-sky-tooltip" role="tooltip"', html)

        for element_id in (
            "about-ctas", "catalog-overview", "active-sources", "recent-stream",
            "celestial-sphere", "ranked-candidates", "ctas-research-tools",
            "methods-and-use", "ctas-q", "ctas-class", "ctas-messenger",
            "ctas-statusfilter", "ctas-survey", "ctas-cone-ra",
            "ctas-cone-dec", "ctas-cone-radius",
        ):
            self.assertIn(f'id="{element_id}"', html)

        self.assertNotIn("Choose your depth", html)
        self.assertNotIn("data-ctas-mode", html + workbench)
        self.assertNotIn("data-switch-mode", html + workbench)
        self.assertNotIn("MODE_COPY", workbench)
        self.assertNotIn("MODE_KEY", workbench)
        self.assertIn('url.searchParams.delete("mode")', workbench)
        self.assertIn('brief.open = true', workbench)
        self.assertNotIn('href="index.html#transients"', html)
        self.assertNotIn('href="index.html#science"', html)
        self.assertIn('statusCell("Latest record", esc(relative(status.latest_record_update)', app)
        self.assertIn('"Snapshot built"', app)
        self.assertIn("Scientist workbench", html)
        self.assertIn("Top 100 CTAS-ranked candidates", html)
        self.assertIn("Latest arrivals", html)
        self.assertIn("View every report from the last 24 hours", html)
        self.assertIn('data-preset="today"', html)
        self.assertIn("Browse complete catalog", app)
        self.assertIn("var PAGE = 100;", app)
        self.assertIn("discovery >= cutoff", app)
        self.assertIn("maintained contracts—not every astronomical source", app)

        self.assertIn("ctas-console-head", html)
        self.assertNotIn('class="ctas-hero__actions"', html)
        self.assertEqual(html.count('id="ctas-q"'), 1)
        self.assertIn('["celestial-sphere", "recent-stream", "ranked-candidates", "ctas-learn", "about-ctas", "ctas-reference", "methods-and-use"]', app)
        self.assertIn('data-candidate-id=', app)
        self.assertIn("function emphasizedEventId()", app)
        self.assertIn("function repaintCandidateLinks(redrawSky)", app)
        self.assertIn("is-candidate-emphasized", app)
        self.assertIn("state.hoveredEventId", app)
        self.assertIn("state.focusedEventId", app)
        self.assertIn('window.matchMedia("(min-width: 961px)")', app)
        self.assertIn("#celestial-sphere { order: 10; }", styles)
        self.assertIn(".ctas-stream__list li:hover .ctas-card-actions", styles)
        self.assertNotIn(".ctas-stream__list article:hover", styles)
        self.assertIn(".ctas-workspace:not([hidden])", styles)
        self.assertIn("position: fixed", styles)
        self.assertIn("@media (forced-colors: active)", styles)

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
        # The 4096-shard manifest is fetched on demand, never to draw a first
        # screen: it is needed only to verify a shard or list the downloads.
        self.assertIn("function ensureCatalogManifest()", app)
        self.assertIn('getJSON("candidate-chunks/manifest.json")', app)
        self.assertNotIn('getJSON("candidate-chunks/manifest.json" + suffix)', app)
        self.assertIn("safeCatalogDownloadPath(row.path)", app)
        self.assertIn('download>Download part', app)
        self.assertNotIn("ctas/data/candidates.json", html + app)

    def test_follow_up_counts_reproduce_arrays(self):
        for candidate in self.snapshot["candidates"]:
            for key, count in candidate["follow_up_counts"].items():
                self.assertEqual(count, len(candidate.get("follow_up", {}).get(key, [])))
            self.assertEqual(candidate["follow_up_total"], sum(candidate["follow_up_counts"].values()))

    def test_every_published_score_reconciles_and_terminal_records_are_zero(self):
        release_clock = self.index["catalog_as_of"]
        del release_clock
        problems = []
        for candidate in self.snapshot["candidates"]:
            model = candidate["score_model"]
            applied = [row for row in model["terms"] if row["applicable"]]
            core = float(model["baseline"]) + sum(float(row["points"]) for row in applied)
            expected = round(
                max(0.0, min(100.0, max(0.0, min(100.0, core)) + float(model["multimessenger_bonus"]))), 2
            )
            if model["status_override"]:
                expected = 0.0
            if model["schema"] != EXPORTER.SCORE_METHOD_VERSION:
                problems.append((candidate["event_id"], "schema", model["schema"]))
            elif abs(float(candidate["ctas_score"]) - expected) > float(model["tolerance"]):
                problems.append((candidate["event_id"], "arithmetic", candidate["ctas_score"], expected))
            elif str(candidate.get("status") or "").lower() in {"retracted", "bogus"} \
                    and candidate["ctas_score"] != 0.0:
                problems.append((candidate["event_id"], "terminal", candidate["ctas_score"]))
        self.assertEqual(problems[:5], [], f"{len(problems)} records did not reproduce their score")

    def test_the_release_declares_its_local_store_exceptions(self):
        exceptions = self.status["local_store_exceptions"]
        self.assertEqual(exceptions["failure_count"], len(exceptions["failures"]))
        self.assertEqual(
            exceptions["unreadable_event_count"],
            sum(row["unreadable_event_count"] for row in exceptions["failures"]),
        )
        for row in exceptions["failures"]:
            self.assertEqual(len(row["unreadable_event_ids"]), row["unreadable_event_count"])

    def test_every_published_score_carries_one_release_clock(self):
        references = {candidate["score_model"]["score_clock"] for candidate in self.snapshot["candidates"]}
        self.assertEqual(references, {EXPORTER.SCORE_CLOCK_REFERENCE}, "records refer to the release clock")
        clock = self.manifest["score_clock"]
        self.assertEqual(clock["schema"], EXPORTER.SCORE_CLOCK_SCHEMA)
        self.assertEqual(clock["method_version"], EXPORTER.SCORE_METHOD_VERSION)
        self.assertEqual(clock["score_as_of"], self.certificate["generated_at"])
        self.assertEqual(
            clock, EXPORTER.score_clock_for(EXPORTER.parse_utc(self.certificate["generated_at"])),
        )
        stamps = {(row["score_as_of"], row["score_valid_until"]) for row in self.index_candidates}
        self.assertEqual(stamps, {(clock["score_as_of"], clock["valid_until"])},
                         "one release computes one score clock")
        for candidate in self.snapshot["candidates"][:200]:
            for retired in ("score_as_of", "valid_until", "evidence_age_days"):
                self.assertNotIn(retired, candidate["score_model"])

    def test_every_dossier_names_the_source_universe_definition(self):
        expected = (
            f"{EXPORTER.SOURCE_UNIVERSE_SCHEMA}+sha256:"
            f"{self.universe['definition_checksum_sha256'][:16]}"
        )
        self.assertEqual(
            self.universe["definition_checksum_sha256"],
            EXPORTER.source_universe_definition_checksum(self.universe),
        )
        self.assertEqual(
            self.universe["contract_set_checksum_sha256"],
            EXPORTER.source_universe_contract_checksum(self.universe),
            "both published checksums reproduce from the published file",
        )
        self.assertEqual(
            {candidate["astro_evidence"]["sourceUniverseVersion"] for candidate in self.snapshot["candidates"]},
            {expected},
        )

    def test_no_non_target_record_reaches_the_published_leaderboard(self):
        by_id = {row["event_id"]: row for row in self.index_candidates}
        for event_id in self.summary["leaderboard"]["event_ids"]:
            self.assertTrue(by_id[event_id]["default_leaderboard_eligible"])

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

    def test_every_suggested_follow_up_reproduces_and_is_claim_bounded(self):
        if not all("suggested_follow_up" in candidate for candidate in self.snapshot["candidates"]):
            self.skipTest("this release predates ctas-suggested-follow-up.v1; the browser derives the draft")
        for candidate in self.snapshot["candidates"]:
            block = candidate["suggested_follow_up"]
            with self.subTest(event_id=candidate["event_id"]):
                self.assertEqual(block["schema"], EXPORTER.SUGGESTED_FOLLOW_UP_SCHEMA)
                self.assertEqual(block, EXPORTER.suggested_follow_up_for(candidate))
                self.assertEqual(bool(block["objectives"]), block["eligibility"]["is_target"])
                self.assertEqual(block["cadence"]["code"] == "none", not block["objectives"])
                self.assertEqual(block["claim_boundary"], EXPORTER.SUGGESTED_FOLLOW_UP_CLAIM)
        self.assertIn("suggested-follow-up-integrity", {gate["id"] for gate in self.certificate["gates"]})

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
        self.assertTrue({"rubin-lsst", "rubin-rsp-alerts", "pan-starrs", "goto", "master", "blackgem", "wfst", "yse", "chime", "maxi", "einstein-probe", "ads",
                         "boom-gcn", "dsa110-gcn", "integral-gcn", "km3net-gcn", "moa-gcn"} <= set(keys))
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
        } | {
            # Track 1 (2026-10-19): an ECSV 1.0 and VOTable 1.5 pair for every public
            # research table, including the three that existed only as JSON before.
            f"ctas/data/research/{table}.{suffix}"
            for table in EXPORTER.RESEARCH_INTEROP_TABLES for suffix in ("ecsv", "vot")
        }
        self.assertEqual(set(tables), required)
        # Five tables before Track 1; seventeen with the six ECSV/VOTable pairs.
        self.assertEqual(len(tables), 17)
        self.assertEqual(len(EXPORTER.RESEARCH_INTEROP_TABLES), 6)
        self.assertEqual(self.research_manifest["interoperability"]["round_trip_problem_count"], 0)
        for table in EXPORTER.RESEARCH_INTEROP_TABLES:
            for suffix in ("ecsv", "vot"):
                columns = tables[f"ctas/data/research/{table}.{suffix}"]["columns"]
                self.assertTrue(columns)
                self.assertTrue(all(column["ucd"] and column["description"] for column in columns))
        votable_15 = ET.parse(ROOT / "ctas/data/research/candidates.vot").getroot()
        self.assertEqual(votable_15.get("version"), "1.5")
        self.assertEqual(
            [field.get("name") for field in votable_15.findall(".//{*}FIELD")],
            list(EXPORTER.CATALOG_CANDIDATE_COLUMNS),
        )
        self.assertTrue(all(field.get("ucd") for field in votable_15.findall(".//{*}FIELD")))
        self.assertEqual(len(votable_15.findall(".//{*}TR")), self.snapshot["candidate_count"])
        ecsv_header = (ROOT / "ctas/data/research/candidates.ecsv").read_text(encoding="utf-8").splitlines()[0]
        self.assertEqual(ecsv_header, "# %ECSV 1.0")
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
        # Build the immutable lookup once, not once per exported target row.
        candidate_ids = {candidate["event_id"] for candidate in self.snapshot["candidates"]}
        self.assertTrue(all(
            row[type_column] == "SIDEREAL" and row[id_column] in candidate_ids
            for row in tom_rows
        ))

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
        self.assertIn('source.execute("BEGIN")', publisher)
        self.assertIn('source.backup(destination, pages=4096', publisher)
        # the frozen view is a copy-on-write clone where possible, pinned by the same
        # read transaction and structurally checked, never a copy per refresh
        self.assertIn('cloned = clone(source_path, destination_path)', publisher)
        self.assertIn('frozen.execute("PRAGMA quick_check")', publisher)
        self.assertIn('SNAPSHOT_DIR="${CTAS_SNAPSHOT_DIR:-$SITE/.git}"', publisher)
        self.assertIn('source_path.as_uri() + "?mode=ro"', publisher)
        # One export per release; the link-health artifact is bound afterwards by the
        # rebind step instead of a complete second export.
        self.assertEqual(publisher.count('export_ctas_snapshot.py --database "$PUBLISH_DB"'), 1)
        self.assertIn('export_ctas_snapshot.py --rebind-link-health --output-dir ctas/data', publisher)
        self.assertLess(publisher.index('check_ctas_links.py'), publisher.index('--rebind-link-health'))
        self.assertLess(publisher.index('--rebind-link-health'), publisher.index('ctas_feed.py'))
        self.assertIn('test_ctas_ingest_provenance.py --database "$PUBLISH_DB"', publisher)
        for name in (
            "live-summary.json", "catalog-index.json", "alias-index.json",
            "catalog-pages/manifest.json", "source-matrix-patterns.json",
            "research/manifest.json", "research/events.csv", "research/aliases.csv",
            "research/sources.csv", "research/events.vot", "research/tom-targets.csv",
            "research/candidates.ecsv", "research/candidates.vot", "research/aliases.ecsv",
            "research/sources.vot", "research/tom-targets.ecsv", "research/source-accounting.vot",
            "research/release-history.ecsv",
            "candidate-chunks/manifest.json", "bulk-downloads.json", "links/manifest.json", "provenance.json",
            "changelog.json", "source-accounting.json", "feeds/index.json",
            "status.json", "source-universe.json", "release-history.json",
            "link-health.json", "certification.json",
        ):
            self.assertIn(name, publisher)
        # Track 1, part C: feed files are collected from their index and retired like
        # stale DataLink documents.
        self.assertIn('FEED_FILES=$(python3', publisher)
        self.assertIn('PUBLIC_FILES+=("$feed_file")', publisher)
        self.assertIn('feeds/(?:all-events|event-type/[a-z0-9-]+|channel/[a-z0-9-]+|region/[a-z0-9-]+)', publisher)
        self.assertIn('"ctas/data/feeds/index.json",', publisher)
        # Track 1: DataLink documents are collected from their manifest like pages and
        # chunks, the VOParquet assets go to the rolling GitHub Release after the tests
        # and before the data-branch push, and every JSON file is schema-checked.
        self.assertIn('DATALINK_FILES=$(python3', publisher)
        self.assertIn('PUBLIC_FILES+=("$link_document")', publisher)
        self.assertIn('--bulk-dir "$BULK_DIR"', publisher)
        self.assertIn('scripts/validate_ctas_schemas.py', publisher)
        self.assertIn('scripts/test_ctas_interop.py', publisher)
        self.assertIn('gh release upload "$tag" "$asset" --repo "$repo" --clobber', publisher)
        self.assertLess(publisher.index('scripts/validate_ctas_schemas.py'), publisher.index('upload_bulk_assets\n'))
        self.assertLess(publisher.index('if [ "$DRY" -eq 1 ]; then'), publisher.index('upload_bulk_assets\n'))
        self.assertLess(publisher.index('upload_bulk_assets\n'), publisher.index('scripts/data_branch.sh publish "$DATA_STORE"'))
        self.assertIn('links/[0-9a-f]{3}\\.xml', publisher)
        self.assertNotIn("candidates.json", publisher)
        self.assertIn("--catalog-index ctas/data/catalog-index.json", publisher)
        self.assertIn("--candidate-manifest ctas/data/candidate-chunks/manifest.json", publisher)
        self.assertIn('DETAIL_FILES=$(python3', publisher)
        self.assertIn('PUBLIC_FILES+=("$detail")', publisher)
        self.assertIn('for index in range(4096)', publisher)
        self.assertIn('used_parts != set(part_metadata)', publisher)
        self.assertIn('HEARTBEAT_INTERVAL="${CTAS_HEARTBEAT_INTERVAL:-900}"', publisher)
        self.assertEqual(publisher.count('--release-base-ref "$DATA_TIP"'), 1)
        self.assertEqual(publisher.count('with_release python3 scripts/export_ctas_snapshot.py'), 1)
        self.assertIn('restore --source=HEAD --staged --worktree', publisher)
        self.assertIn('discard_generated_files() {', publisher)
        self.assertIn('clean -fdq -- ctas/data', publisher)
        self.assertIn('[ "$HEARTBEAT_INTERVAL" -ge 120 ]', publisher)
        self.assertIn('[ "$HEARTBEAT_INTERVAL" -le 900 ]', publisher)
        self.assertIn("publication_state_checksum_sha256", publisher)
        self.assertIn("CURRENT_CODE_BINDING", publisher)
        self.assertIn("HEAD_CODE_BINDING", publisher)
        self.assertIn("CODE_BINDING_CHANGED", publisher)
        self.assertIn("export PYTHONDONTWRITEBYTECODE=1", publisher)
        self.assertIn('scripts/data_branch.sh sync "$DATA_STORE" "$DATA_BRANCH"', publisher)
        self.assertIn('scripts/data_branch.sh retry "$DATA_STORE" "$DATA_BRANCH"', publisher)
        self.assertIn('scripts/data_branch.sh publish "$DATA_STORE" "$DATA_BRANCH"', publisher)
        self.assertIn('DATA_BRANCH="${CTAS_DATA_BRANCH:-ctas-data}"', publisher)
        self.assertNotIn("git push", publisher)
        self.assertNotIn("git commit", publisher)
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
        self.assertIn("<key>SuccessfulExit</key>", agent)
        self.assertIn("<key>ThrottleInterval</key>", agent)
        self.assertIn("<integer>60</integer>", agent)
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
            [f"ctas/data/candidate-chunks/{index:03x}.json" for index in range(EXPORTER.CANDIDATE_BUCKET_COUNT)],
        )
        for row in self.manifest["chunks"]:
            path = ROOT / row["path"]
            raw = path.read_bytes()
            self.assertEqual(len(raw), row["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"])
            self.assertLessEqual(len(raw), EXPORTER.CANDIDATE_SHARD_TARGET_MAX_BYTES)
            document = EXPORTER.decode_chunk(raw, lambda part: (ROOT / part).read_bytes())
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
        datalink_manifest = json.loads((self.data_dir / "links/manifest.json").read_text())
        explicit = [
            "ctas/data/live-summary.json", "ctas/data/catalog-index.json",
            "ctas/data/catalog-pages/manifest.json", "ctas/data/source-matrix-patterns.json",
            "ctas/data/alias-index.json", "ctas/data/research/manifest.json",
            "ctas/data/candidate-chunks/manifest.json",
            "ctas/data/status.json", "ctas/data/source-universe.json",
            "ctas/data/release-history.json", "ctas/data/link-health.json",
            "ctas/data/certification.json", EXPORTER.BULK_DOWNLOADS_PATH,
            EXPORTER.DATALINK_MANIFEST_PATH, EXPORTER.PROVENANCE_PATH,
        ] + [row["path"] for row in self.research_manifest["tables"]] + [
            row["path"] for row in datalink_manifest["documents"]
        ] + [
            f"ctas/data/candidate-chunks/{index:03x}.json"
            for index in range(EXPORTER.CANDIDATE_BUCKET_COUNT)
        ]
        explicit += [row["path"] for row in self.manifest.get("parts", [])]
        for row in self.manifest.get("parts", []):
            raw = (ROOT / row["path"]).read_bytes()
            self.assertLessEqual(len(raw), EXPORTER.CANDIDATE_SHARD_TARGET_MAX_BYTES)
            self.assertEqual(len(raw), row["bytes"])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), row["sha256"])
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
        bucket_rows, chunks, parts = EXPORTER.candidate_chunk_artifacts(candidates)
        manifest, _ = EXPORTER.complete_catalog_manifest_artifact(
            candidates,
            EXPORTER.inflate_catalog_candidates(index),
            index_raw,
            bucket_rows,
            chunks,
            "a" * 64,
            parts,
        )
        blobs[("sharded", "ctas/data/catalog-index.json")] = index_raw
        blobs[("sharded", "ctas/data/candidate-chunks/manifest.json")] = json.dumps(manifest).encode()
        blobs.update({("sharded", path): raw for path, raw in {**chunks, **parts}.items()})
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
            matrix = EXPORTER.expand_source_matrix(
                candidate["source_matrix"], self.source_matrix_patterns
            )
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
        # These are real public events pinned by their stable event id. Their names change
        # legitimately: AT2026zxx became SN2026zxx when TNS received its classification on
        # 2026-10-19 17:04 UTC, and a lookup by name refused every release for seven hours.
        # The name is reported, never required.
        by_event = {row["event_id"]: row for row in self.snapshot["candidates"]}

        def pinned(event_id, former_names):
            self.assertIn(event_id, by_event, f"pinned regression event {event_id} ({'/'.join(former_names)}) is missing from the release")
            row = by_event[event_id]
            self.assertTrue(row["name"], f"pinned regression event {event_id} has no name")
            return row

        multi = pinned("3f55528c-f552-4513-933b-9c982b8b675f", ["AT2026zxr"])
        providers = {row["provider"] for row in multi["follow_up"]["observations"]}
        self.assertTrue({"atlas", "rubin-fink", "rubin-lasair"} <= providers)

        stale = pinned("1a6053f4-5660-4228-ad6a-c0a2f9c553f5", ["SN2026wpd"])
        self.assertTrue(any(
            row["retainedEvidenceState"] == "STALE_LAST_GOOD_RETAINED"
            for row in EXPORTER.expand_source_matrix(stale["source_matrix"], self.source_matrix_patterns)
        ))

        no_match = pinned("2ce4c4eb-dd3a-485e-9f34-e46ba0553883", ["AT2026zxx", "SN2026zxx"])
        self.assertTrue(any(
            row["currentQueryOutcome"] == "SEARCHED_NO_MATCH"
            for row in EXPORTER.expand_source_matrix(no_match["source_matrix"], self.source_matrix_patterns)
        ))

        revision = pinned("c72e4f9e-ad8d-4573-bb15-917a911b5a6b", ["NuEm-220601A-118386"])
        signals = revision["follow_up"]["messenger_signals"]
        self.assertGreaterEqual(len(signals), 2)
        self.assertTrue(any(row.get("retracted") for row in signals))
        self.assertTrue(any(row.get("supersedes_provider_signal_id") for row in signals))

    def test_interoperability_products_are_certified(self):
        gates = {gate["id"]: gate for gate in self.certificate["gates"]}
        self.assertGreaterEqual(len(gates), 43)
        self.assertEqual(self.status["static_snapshot_verification"]["check_count"], len(gates))
        self.assertIn(EXPORTER.INTEROP_GATE_ID, gates)
        self.assertTrue(gates[EXPORTER.INTEROP_GATE_ID]["passed"], gates[EXPORTER.INTEROP_GATE_ID]["evidence"])
        self.assertEqual(self.status["provenance_path"], EXPORTER.PROVENANCE_PATH)
        self.assertEqual(self.status["bulk_downloads_path"], EXPORTER.BULK_DOWNLOADS_PATH)
        self.assertEqual(self.status["datalink_manifest_path"], EXPORTER.DATALINK_MANIFEST_PATH)
        for key, path in (
            ("provenance", EXPORTER.PROVENANCE_PATH), ("bulk_downloads", EXPORTER.BULK_DOWNLOADS_PATH),
            ("datalink_manifest", EXPORTER.DATALINK_MANIFEST_PATH),
        ):
            raw = (ROOT / path).read_bytes()
            self.assertEqual(self.status["artifacts"][key], {"path": path, "sha256": hashlib.sha256(raw).hexdigest()})
            self.assertEqual(self.certificate["files"][path]["sha256"], hashlib.sha256(raw).hexdigest())
        datalink_manifest = json.loads((self.data_dir / "links/manifest.json").read_text())
        self.assertEqual(datalink_manifest["event_count"], self.snapshot["candidate_count"])
        self.assertEqual(datalink_manifest["catalog_content_checksum_sha256"], self.snapshot["catalog_content_checksum_sha256"])
        for row in datalink_manifest["documents"]:
            raw = (ROOT / row["path"]).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (row["bytes"], row["sha256"]), row["path"])
            self.assertIn(row["path"], self.certificate["files"])
            self.assertNotIn(self.snapshot["catalog_content_checksum_sha256"].encode(), raw, "per-bucket DataLink documents carry no release clock")
        buckets = {EXPORTER.candidate_bucket(candidate["event_id"]) for candidate in self.snapshot["candidates"]}
        self.assertEqual({row["bucket"] for row in datalink_manifest["documents"]}, buckets)
        bulk = json.loads((self.data_dir / "bulk-downloads.json").read_text())
        self.assertEqual(bulk["catalog_content_checksum_sha256"], self.snapshot["catalog_content_checksum_sha256"])
        self.assertEqual({asset["name"] for asset in bulk["assets"]}, {EXPORTER.BULK_CANDIDATES_ASSET, EXPORTER.BULK_PHOTOMETRY_ASSET})
        candidates_asset = next(asset for asset in bulk["assets"] if asset["name"] == EXPORTER.BULK_CANDIDATES_ASSET)
        self.assertEqual(candidates_asset["row_count"], self.snapshot["candidate_count"])
        self.assertFalse(list((ROOT / "ctas").rglob("*.parquet")), "VOParquet assets never enter the Pages tree")
        provenance = json.loads((self.data_dir / "provenance.json").read_text())
        self.assertIn("ctas:release/" + self.snapshot["catalog_content_checksum_sha256"], provenance["entity"])
        self.assertEqual(
            {key[len("ctas:source/"):] for key in provenance["entity"] if key.startswith("ctas:source/")},
            {row["source_key"] for row in self.universe["sources"]},
        )
        for path in EXPORTER.API_DESCRIPTION_PATHS:
            self.assertIn(path, self.certificate["files"], path)

    def test_changelog_source_accounting_and_feeds_are_certified_and_reconcile(self):
        """Track 1, part C: the plain-words changelog, the per-source denominators and the
        feed set are declared by status.json, bound by the certificate, pass their own
        structural checks, and the per-source counts reproduce from the published dossiers."""
        sys.path.insert(0, str(ROOT / "scripts"))
        import ctas_changelog
        import ctas_feed

        for key, path in (("changelog_path", "ctas/data/changelog.json"), ("source_accounting_path", "ctas/data/source-accounting.json"),
                          ("feeds_index_path", "ctas/data/feeds/index.json"), ("status_page_path", "ctas/status.html")):
            self.assertEqual(self.status.get(key), path)
        files = self.certificate["files"]
        gates = {gate["id"]: gate for gate in self.certificate["gates"]}
        self.assertTrue(gates[EXPORTER.RELEASE_PRODUCTS_GATE_ID]["passed"], gates[EXPORTER.RELEASE_PRODUCTS_GATE_ID]["evidence"])
        for path in ("ctas/data/changelog.json", "ctas/data/source-accounting.json", "ctas/data/feeds/index.json",
                     "ctas/sky.js", "ctas/changelog.js", "ctas/status.html", "ctas/status.js", "scripts/ctas_changelog.py", "scripts/ctas_feed.py"):
            self.assertTrue(path in files, path)
            raw = (ROOT / path).read_bytes()
            self.assertEqual(files[path], {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}, path)
            self.assertTrue(path in EXPORTER.REQUIRED_PUBLIC_ARTIFACTS or path in EXPORTER.BOUND_CODE_PATHS, path)
        # changelog
        changelog = json.loads((ROOT / "ctas/data/changelog.json").read_text())
        self.assertEqual(ctas_changelog.changelog_problems(changelog), [])
        self.assertEqual(changelog["schema"], ctas_changelog.CHANGELOG_SCHEMA)
        for entry in changelog["entries"]:
            self.assertEqual(entry["summary"], ctas_changelog.plain_words(entry))
        # per-source accounting reproduces from the dossiers' expanded source matrices
        accounting = json.loads((ROOT / "ctas/data/source-accounting.json").read_text())
        recomputed = {}
        for candidate in self.snapshot["candidates"]:
            for row in EXPORTER.expand_source_matrix(candidate["source_matrix"], self.source_matrix_patterns):
                counts = recomputed.setdefault(row["sourceContractId"], {"applicable_events": 0, "executed_query_receipts": 0, "data_returned_events": 0, "events_with_retained_records": 0})
                counts["applicable_events"] += 1
                counts["executed_query_receipts"] += int(row.get("executedReceiptCount") or 0)
                counts["data_returned_events"] += 1 if row.get("currentQueryOutcome") == "DATA_RETURNED" else 0
                counts["events_with_retained_records"] += 1 if int(row.get("retainedRecordCount") or 0) > 0 else 0
        by_key = {row["source_key"]: row for row in accounting["sources"]}
        self.assertEqual(set(recomputed) <= set(by_key), True)
        for key, counts in recomputed.items():
            for field, value in counts.items():
                self.assertEqual(by_key[key][field], value, f"{key}.{field}")
        declared = {row["source_key"] for row in self.universe["sources"]}
        self.assertEqual({row["source_key"] for row in accounting["sources"] if row["declared"]}, declared)
        for row in accounting["sources"]:
            if row["source_key"] not in recomputed:
                self.assertEqual((row["applicable_events"], row["executed_query_receipts"], row["data_returned_events"]), (0, 0, 0))
        totals = accounting["totals"]
        aggregate = self.status["source_accounting"]
        self.assertEqual(totals["declared_sources"], aggregate["declaredSources"])
        self.assertEqual(totals["applicable_source_evaluations"], aggregate["applicableSourceEvaluations"])
        self.assertEqual(totals["executed_query_receipts"], aggregate["executedQueryReceipts"])
        self.assertEqual(totals["data_bearing_source_evaluations"], aggregate["dataBearingSourceEvaluations"])
        self.assertEqual(totals["outcome_counts"], aggregate["outcomeCounts"])
        self.assertEqual(accounting["source_count"], len(accounting["sources"]))
        # feeds: every file the index names is bound and validates
        index = json.loads((ROOT / "ctas/data/feeds/index.json").read_text())
        self.assertEqual(index["feed_count"], 1 + len(ctas_feed.EVENT_TYPES) + len(ctas_feed.RANKING_CHANNELS) + 195)
        feed_files = {}
        for row in index["feeds"]:
            for key in ("json_feed", "atom"):
                path = row[key]["path"]
                raw = (ROOT / path).read_bytes()
                feed_files[path] = raw
                self.assertEqual(files[path], {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}, path)
        self.assertEqual(ctas_feed.feed_problems(feed_files, index), [])
        self.assertEqual(set(str(p.relative_to(ROOT)) for p in (ROOT / "ctas/data/feeds").rglob("*") if p.is_file()),
                         set(feed_files) | {"ctas/data/feeds/index.json"}, "nothing but the declared feeds under feeds/")
        all_items = json.loads(feed_files["ctas/data/feeds/all-events.json"])["items"]
        self.assertEqual(len(all_items), min(ctas_feed.FEED_ITEMS, len(self.snapshot["candidates"])))
        event_ids = {candidate["event_id"] for candidate in self.snapshot["candidates"]}
        self.assertTrue(all(item["_ctas"]["event_id"] in event_ids for item in all_items))
        # the pages and the workflow that go with them
        page = (ROOT / "ctas.html").read_text()
        for token in ('ctas/sky.js?v=', 'ctas/changelog.js?v=', 'href="ctas/status.html"', 'href="ctas/data/feeds/index.json"',
                      'type="application/feed+json"', 'ctas/data/feeds/all-events.xml'):
            self.assertIn(token, page)
        status_page = (ROOT / "ctas/status.html").read_text()
        self.assertEqual(status_page.count("<h1"), 1)
        self.assertIn('src="status.js', status_page)
        self.assertIn('src="changelog.js', status_page)
        app = (ROOT / "ctas/app.js").read_text()
        self.assertIn('data-report-problem', app)
        self.assertIn('encodeURIComponent("data-problem")', app)
        workflow = (ROOT / ".github/workflows/worldsindex-release.yml").read_text()
        self.assertIn("gh label create data-problem", workflow)
        self.assertIn("--force", workflow)
        self.assertIn("issues: write", workflow)
        workbench = (ROOT / "ctas/workbench.js").read_text()
        self.assertNotIn("api/v3/latest/aladin.js", workbench, "the Aladin runtime is pinned")
        sky = (ROOT / "ctas/sky.js").read_text()
        self.assertIn('ALADIN_VERSION = "3.6.1"', sky)
        self.assertIn('"https://aladin.cds.unistra.fr/AladinLite/api/v3/"', sky)
        self.assertIn("api/v3/3.6.1/aladin.js", workbench)
        validator = (ROOT / "scripts/validate_ctas_schemas.py").read_text()
        for name in ("changelog.json", "source-accounting.json", "feeds/index.json"):
            self.assertIn(f'"{name}"', validator)

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

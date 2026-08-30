#!/usr/bin/env python3
"""Contract tests for the public CTAS AstroEvidence compatibility export."""

from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "ctas/schema/astro-evidence-core-0.1.0.schema.json"
PROJECTOR_PATH = ROOT / "ctas/astro-evidence.js"

SPEC = importlib.util.spec_from_file_location("ctas_astro_evidence", ROOT / "scripts/ctas_astro_evidence.py")
assert SPEC and SPEC.loader
EVIDENCE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(EVIDENCE)

EXPORTER_SPEC = importlib.util.spec_from_file_location(
    "ctas_snapshot_exporter", ROOT / "scripts/export_ctas_snapshot.py",
)
assert EXPORTER_SPEC and EXPORTER_SPEC.loader
EXPORTER = importlib.util.module_from_spec(EXPORTER_SPEC)
EXPORTER_SPEC.loader.exec_module(EXPORTER)

GENERATED_AT = "2026-08-29T12:00:00Z"
TARGET_ID = "123e4567-e89b-42d3-a456-426614174000"


def source(source_key: str, **updates):
    row = {
        "source_key": source_key,
        "name": source_key.upper(),
        "organization_or_facility": source_key.upper(),
        "source_family": "photometric-follow-up",
        "data_types": ["source-native photometry"],
        "access_mode": "bounded event API",
        "implementation_state": "implemented",
        "rights_or_public_access_basis": "Public metadata with provider attribution.",
        "authentication_requirement": "Anonymous public access",
        "query_scope": "One stable event identity",
        "rate_or_cadence_limit": "Provider documented limit",
        "known_limitations": "Fixture contract; no claim beyond retained rows.",
        "last_verified": "2026-08-29",
        "contract_version": "1.0.0",
    }
    row.update(updates)
    return row


def fixture():
    candidate = {
        "event_id": TARGET_ID,
        "name": "AT2026fixture",
        "event_type": "optical-transient",
        "primary_messenger": "electromagnetic",
        "ra_deg": 12.5,
        "dec_deg": -31.25,
        "coordinate_error_arcsec": 0.4,
        "discovery_time": "2026-08-27T00:00:00Z",
        "updated_at": "2026-08-29T10:00:00Z",
        "classification": "Unclassified",
        "designations": [
            {"alias_id": "alias-tns", "source_key": "tns", "designation": "AT2026fixture", "is_preferred": True,
             "asserted_at": "2026-08-27T00:00:00Z"},
            {"alias_id": "alias-ztf", "source_key": "ztf", "designation": "ZTF26fixture", "is_preferred": False,
             "asserted_at": "2026-08-27T00:01:00Z"},
        ],
        "identity_resolution": {"state": "RESOLVED"},
        "follow_up": {
            "classifications": [
                {"assertion_id": "class-a", "source_record_id": "tns-class-a", "provider": "tns",
                 "classification": "SN Ia", "probability": 0.91, "method": "spectrum", "model_name": "human",
                 "model_version": "1", "asserted_at": "2026-08-28T00:00:00Z",
                 "citation_url": "https://www.wis-tns.org/object/2026fixture"},
                {"assertion_id": "class-b", "source_record_id": "ztf-class-b", "provider": "ztf",
                 "classification": "SN II", "probability": 0.70, "method": "early-light-curve", "model_name": "fixture",
                 "model_version": "2", "asserted_at": "2026-08-28T00:10:00Z",
                 "citation_url": "https://example.org/ztf-class-b"},
            ],
            "classification_history": [
                {"assertion_id": "class-old", "source_record_id": "tns-class-old", "provider": "tns",
                 "classification": "Unknown", "asserted_at": "2026-08-27T12:00:00Z", "superseded": True},
            ],
            "observations": [
                {"assertion_id": "obs-ztf", "provider_observation_id": "ZTF-1", "provider": "ztf",
                 "observed_at": "2026-08-28T01:00:00Z", "original_time": "2461280.5416667",
                 "mjd": 61280.0416667, "jd": 2461280.5416667, "detection": True,
                 "magnitude": 18.0, "magnitude_error": 0.1, "magnitude_system": "AB", "band": "r",
                 "photometry_method": "PSF", "calibration": "fixture-cal-v1", "difference_photometry": True,
                 "source_url": "https://example.org/ztf-observation"},
                {"assertion_id": "obs-atlas", "provider_observation_id": "ATLAS-1", "provider": "atlas",
                 "observed_at": "2026-08-28T01:00:00Z", "mjd": 61280.0416667, "detection": True,
                 "magnitude": 19.0, "magnitude_error": 0.1, "magnitude_system": "AB", "band": "r",
                 "photometry_method": "PSF", "calibration": "fixture-cal-v1", "difference_photometry": True,
                 "source_url": "https://example.org/atlas-observation"},
                {"assertion_id": "obs-limit", "provider_observation_id": "ATLAS-2", "provider": "atlas",
                 "observed_at": "2026-08-28T01:00:00Z", "mjd": 61280.0416667, "detection": False,
                 "limiting_magnitude": 20.0, "magnitude_system": "AB", "band": "r",
                 "photometry_method": "PSF", "calibration": "fixture-cal-v1", "difference_photometry": True,
                 "source_url": "https://example.org/atlas-limit"},
                {"assertion_id": "obs-flux-limit", "provider_observation_id": "ATLAS-3", "provider": "atlas",
                 "observed_at": "2026-08-28T02:00:00Z", "jd": 2461280.5833333,
                 "detection": False, "limiting_flux": 3.0,
                 "flux_unit": "uJy", "band": "o", "photometry_method": "aperture",
                 "calibration": "fixture-cal-v1", "difference_photometry": False},
                {"assertion_id": "obs-no-value", "provider_observation_id": "ZTF-2", "provider": "ztf",
                 "observed_at": "2026-08-28T03:00:00Z", "detection": False, "band": "g",
                 "photometry_method": "PSF", "calibration": "fixture-cal-v1"},
            ],
            "host_context": [
                {"assertion_id": "host-a", "source_record_id": "ned-a", "provider": "ned", "canonical_name": "NGC 1",
                 "redshift": 0.010, "redshift_error": 0.001, "redshift_reference": "heliocentric",
                 "queried_at": "2026-08-28T04:00:00Z", "source_url": "https://example.org/ned-a"},
                {"assertion_id": "host-b", "source_record_id": "simbad-b", "provider": "simbad", "canonical_name": "NGC 1",
                 "redshift": 0.020, "redshift_error": 0.001, "redshift_reference": "heliocentric",
                 "queried_at": "2026-08-28T04:01:00Z", "source_url": "https://example.org/simbad-b"},
            ],
            "spectra": [],
            "messenger_signals": [
                {"assertion_id": "signal-r0", "provider": "gcn", "provider_signal_id": "Fixture-1:r0",
                 "observed_at": "2026-08-28T06:00:00Z", "messenger": "neutrino", "role": "coincidence",
                 "alert_type": "fixture coincidence", "detection": True, "properties": {"revision": 0},
                 "source_url": "https://example.org/gcn-r0"},
                {"assertion_id": "signal-r1", "provider": "gcn", "provider_signal_id": "Fixture-1:r1",
                 "observed_at": "2026-08-28T06:00:00Z", "messenger": "neutrino", "role": "retraction",
                 "alert_type": "fixture coincidence", "detection": False,
                 "properties": {"revision": 1, "comments": ["This is a retraction."]},
                 "source_url": "https://example.org/gcn-r1"},
            ],
            "publications": [], "catalog_counterparts": [],
            "archive_products": [
                {"assertion_id": "archive-1", "provider": "mast", "provider_product_id": "mast-product-1",
                 "data_product_type": "image", "product_filename": "fixture.fits",
                 "observed_start_mjd": 61279.0, "observed_end_mjd": 61279.25,
                 "public_download_url": "https://example.org/fixture.fits", "attribution": "MAST",
                 "calibration_level": 2, "response_checksum": "c" * 64},
            ],
        },
    }
    universe = [
        source("tns", source_family="discovery-and-alert-brokers", data_types=["identity", "classification"]),
        source("ztf"), source("atlas"),
        source("ned", source_family="host-counterpart-and-catalog-context", data_types=["host redshift"]),
        source("simbad", source_family="host-counterpart-and-catalog-context", data_types=["host redshift"]),
        source("gcn", source_family="multimessenger-and-high-energy", data_types=["messenger notices"]),
        source("mast", source_family="archives", data_types=["archive products"]),
        source("link-only", source_family="reports-and-literature", access_mode="link-only", implementation_state="link-only"),
        source("private-tom-skyportal", source_family="other-declared-sources", access_mode="authorized user source",
               implementation_state="implemented-user-authorization-required",
               rights_or_public_access_basis="Only user-owned or explicitly authorized private records.",
               authentication_requirement="User token required"),
    ]
    attempts = [
        {"id": "receipt-data", "event_id": TARGET_ID, "source_id": "ztf", "query_kind": "target-photometry",
         "terminal_state": "data", "checked_at": "2026-08-28T05:00:00Z", "response_checksum": "a" * 64,
         "records_seen": 2, "records_retained": 2, "records_rejected": 0, "normalized_request": {"target": TARGET_ID},
         "provider_release_version": "fixture-r1", "parser_version": "fixture-parser@1", "response_schema_version": "fixture@1"},
        {"id": "receipt-failed", "event_id": TARGET_ID, "source_id": "ztf", "query_kind": "target-photometry",
         "terminal_state": "failed", "checked_at": "2026-08-29T05:00:00Z", "error_code": "ZTF_QUERY_FAILED"},
        {"id": "receipt-zero", "event_id": TARGET_ID, "source_id": "tns", "query_kind": "target-identity",
         "terminal_state": "no-match", "checked_at": "2026-08-29T05:01:00Z", "response_checksum": "b" * 64,
         "records_seen": 0, "records_retained": 0, "records_rejected": 0,
         "normalized_request": {"name": "AT2026fixture", "Authorization": "must-not-export"}},
    ]
    return candidate, universe, attempts


def decorate(candidate, document, accounting, matrix, metadata):
    public = deepcopy(candidate)
    public["astro_evidence"] = {
        "projectionSchema": "ctas.astro-evidence-compatibility@1.0.0",
        "coreSchemaName": document["schemaName"], "coreSchemaVersion": document["schemaVersion"],
        "generatedAt": document["generatedAt"], "sourceUniverseVersion": document["sourceUniverseVersion"],
        "target": document["target"], "persistedQueryReceipts": document["queryReceipts"],
        "conflictSets": document["conflictSets"], "selections": document["selections"],
        "dataProducts": document["dataProducts"], "analysisRuns": document["analysisRuns"],
        "measurementCount": len(document["measurements"]),
        "projectionMethod": "Fixture projection",
    }
    public["source_accounting"] = accounting
    public["source_matrix"] = matrix
    public["compatibility_provenance"] = {
        "receiptProvenance": metadata["receiptProvenance"],
        "selectionProvenance": metadata["selectionProvenance"],
    }
    return public


def node_call(operation: str, candidate, universe):
    script = r"""
const fs = require('fs');
const api = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
(async () => {
  const result = process.argv[2] === 'bundle'
    ? await api.buildExportBundle(input.candidate, {sources: input.universe})
    : await api.project(input.candidate, {sources: input.universe});
  process.stdout.write(JSON.stringify(result));
})().catch(error => { console.error(error.stack || error); process.exit(1); });
"""
    result = subprocess.run(
        ["node", "-e", script, str(PROJECTOR_PATH), operation],
        input=json.dumps({"candidate": candidate, "universe": universe}),
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


class AstroEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidate, cls.universe, cls.attempts = fixture()
        cls.document, cls.accounting, cls.matrix, cls.metadata = EVIDENCE.build_projection(
            cls.candidate, cls.universe, cls.attempts, [], GENERATED_AT, "fixture-universe@1",
        )
        cls.public_candidate = decorate(cls.candidate, cls.document, cls.accounting, cls.matrix, cls.metadata)

    def test_frozen_schema_checksum_and_validation(self):
        self.assertEqual(hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest(),
                         "e25daa3b6eaee9196d1b87b211197d5eacd3594e81e716176f10ff267525a8cb")
        try:
            import jsonschema
        except ImportError as exc:  # pragma: no cover - scientific test environment owns this dependency
            self.skipTest(str(exc))
        schema = json.loads(SCHEMA_PATH.read_text())
        validator = jsonschema.Draft202012Validator(schema, format_checker=jsonschema.FormatChecker())
        self.assertEqual(list(validator.iter_errors(self.document)), [])

    def test_reference_closure_and_honest_absent_analysis(self):
        contracts = {row["sourceContractId"] for row in self.document["sourceContracts"]}
        measurements = {row["measurementId"] for row in self.document["measurements"]}
        for collection in ("queryReceipts", "measurements", "dataProducts"):
            self.assertTrue({row["sourceContractId"] for row in self.document[collection]} <= contracts)
        for conflict in self.document["conflictSets"]:
            self.assertTrue(set(conflict["measurementIds"]) <= measurements)
        for selection in self.document["selections"]:
            self.assertTrue(set(selection["measurementIds"]) <= measurements)
        self.assertEqual(self.document["analysisRuns"], [], "a missing persisted run must not become a fabricated run")

    def test_source_accounting_failure_isolation_and_zero_counts(self):
        self.assertEqual(self.accounting["declaredSources"], 9)
        self.assertEqual(self.accounting["applicableSources"], len(self.matrix))
        self.assertEqual(self.accounting["executedQueryReceipts"], 3)
        self.assertEqual(len(self.document["queryReceipts"]), 3, "no compatibility receipts may be invented")
        zero = next(row for row in self.document["queryReceipts"] if row["receiptId"] == "receipt-zero")
        self.assertEqual((zero["recordsSeen"], zero["recordsRetained"], zero["recordsRejected"]), (0, 0, 0))
        ztf = next(row for row in self.matrix if row["sourceContractId"] == "ztf")
        self.assertEqual(ztf["currentQueryOutcome"], "QUERY_FAILED")
        self.assertGreater(ztf["retainedRecordCount"], 0)
        self.assertEqual(ztf["retainedEvidenceState"], "STALE_LAST_GOOD_RETAINED")
        link = next(row for row in self.matrix if row["sourceContractId"] == "link-only")
        self.assertEqual(link["currentQueryOutcome"], "LINK_ONLY_NOT_QUERIED")
        private = EVIDENCE.source_contract(
            next(row for row in self.universe if row["source_key"] == "private-tom-skyportal"),
            GENERATED_AT, "Explicitly authorized fixture binding.",
        )
        self.assertEqual(private["rightsState"], "AUTHORIZED_PRIVATE")
        serialized = json.dumps(self.metadata)
        self.assertNotIn("must-not-export", serialized)
        self.assertIn("redactedFields", serialized)

    def test_explicit_physical_taxonomy_recognizes_type_ii_and_excludes_scores(self):
        expected = {
            "Type II": "transient.classification",
            "SN Ia-91T-like": "transient.classification",
            "SLSN-I": "transient.classification",
            "Kilonova candidate": "transient.classification",
            "SN-vs-other score": "alert.event_label",
            "early SN Ia score": "alert.event_label",
            "SN Ia probability": "alert.event_label",
            "supernova classifier": "alert.event_label",
        }
        for label, property_code in expected.items():
            with self.subTest(label=label):
                self.assertEqual(EVIDENCE.classification_property(label), property_code)

        candidate = deepcopy(self.candidate)
        candidate["follow_up"]["classifications"] = [
            {"assertion_id": "class-type-ii", "provider": "tns", "classification": "Type II",
             "probability": 0.8, "asserted_at": "2026-08-29T01:00:00Z"},
            {"assertion_id": "class-sn-score", "provider": "ztf", "classification": "SN-vs-other score",
             "probability": 0.9, "asserted_at": "2026-08-29T01:01:00Z"},
            {"assertion_id": "class-early-score", "provider": "ztf", "classification": "early SN Ia score",
             "probability": 0.7, "asserted_at": "2026-08-29T01:02:00Z"},
        ]
        candidate["follow_up"]["classification_history"] = []
        document, accounting, matrix, metadata = EVIDENCE.build_projection(
            candidate, self.universe, self.attempts, [], GENERATED_AT, "fixture-universe@1",
        )
        public = decorate(candidate, document, accounting, matrix, metadata)
        measurements = {
            row["sourceRecordId"]: row for row in document["measurements"]
            if row["label"] == "Source-reported classification or alert label"
        }
        self.assertEqual(measurements["class-type-ii"]["propertyCode"], "transient.classification")
        self.assertEqual(measurements["class-sn-score"]["propertyCode"], "alert.event_label")
        self.assertEqual(measurements["class-early-score"]["propertyCode"], "alert.event_label")
        self.assertFalse(any(
            row["propertyCode"] == "transient.classification" for row in document["conflictSets"]
        ))
        self.assertEqual(node_call("project", public, self.universe), document)

    def test_receipt_projection_redacts_sensitive_fields_and_signed_urls(self):
        sensitive = "synthetic-sensitive-value"
        attempt = deepcopy(self.attempts[0])
        attempt.update({
            "target_identity": {"providerAlias": "AT2026fixture", "apiKey": sensitive},
            "normalized_request": {
                "target_id": TARGET_ID,
                "headers": {"Authorization": f"Bearer {sensitive}"},
                "url": f"https://example.org/query?ra=12.5&access_token={sensitive}",
            },
            "provider_release_version": f"authorization={sensitive}",
            "response_status": f"HTTP 200 token={sensitive}",
            "pagination": {"page": 1, "cursorToken": sensitive},
            "caps": {"recordCap": 100, "signedUrl": f"https://example.org/task?token={sensitive}"},
            "immutable_artifact_reference": (
                f"https://fixture-user:{sensitive}@example.org/artifact"
                f"?version=1&X-Amz-Signature={sensitive}"
            ),
            "evidence_url": f"https://example.org/receipt?receipt=1&token={sensitive}",
            "error_category": f"authorization={sensitive}",
            "request_fingerprint_sha256": "d" * 64,
            "started_at": "2026-08-28T04:59:59Z",
            "completed_at": "2026-08-28T05:00:00Z",
            "record_cap": 100,
            "latency_ms": 1000,
            "retry_count": 0,
            "pagination_complete": True,
            "request_executed": True,
        })
        document, accounting, matrix, metadata = EVIDENCE.build_projection(
            self.candidate, self.universe, [attempt], [], GENERATED_AT, "fixture-universe@1",
        )
        serialized = json.dumps({"document": document, "metadata": metadata}, sort_keys=True)
        self.assertNotIn(sensitive, serialized)
        detail = metadata["receiptProvenance"][0]
        self.assertEqual(detail["targetIdentity"]["apiKey"], EVIDENCE.REDACTED)
        self.assertEqual(detail["normalizedRequest"]["headers"]["Authorization"], EVIDENCE.REDACTED)
        self.assertIn("ra=12.5", detail["normalizedRequest"]["url"])
        self.assertIn("REDACTED", detail["normalizedRequest"]["url"])
        self.assertNotIn("fixture-user", detail["immutableArtifactReference"])
        self.assertIn("REDACTED", detail["immutableArtifactReference"])
        self.assertIn("REDACTED", document["queryReceipts"][0]["evidenceUrl"])

        unsafe = decorate(self.candidate, document, accounting, matrix, metadata)
        unsafe_detail = unsafe["compatibility_provenance"]["receiptProvenance"][0]
        unsafe_detail["normalizedRequest"] = attempt["normalized_request"]
        unsafe_detail["immutableArtifactReference"] = attempt["immutable_artifact_reference"]
        unsafe["astro_evidence"]["persistedQueryReceipts"][0]["evidenceUrl"] = attempt["evidence_url"]
        browser = node_call("bundle", unsafe, self.universe)
        self.assertNotIn(sensitive, json.dumps(browser, sort_keys=True))

    def test_receipt_completeness_and_join_validation_fail_closed(self):
        candidate = deepcopy(self.public_candidate)
        receipt = next(
            row for row in candidate["astro_evidence"]["persistedQueryReceipts"]
            if row["receiptId"] == "receipt-data"
        )
        detail = next(
            row for row in candidate["compatibility_provenance"]["receiptProvenance"]
            if row["receiptId"] == "receipt-data"
        )
        receipt.update({
            "startedAt": "2026-08-29T05:00:01Z", "completedAt": "2026-08-29T05:00:00Z",
            "requestFingerprintSha256": "not-a-sha256", "recordsSeen": 1,
            "recordsRetained": 3, "recordsRejected": -1, "paginationComplete": False,
        })
        detail.update({"latencyMs": -1, "retryCount": -1, "caps": {"recordCap": 0}})
        safe_detail = EVIDENCE.sanitized_receipt_detail(detail)
        expected = EVIDENCE.receipt_completeness(receipt, safe_detail)
        self.assertFalse(expected["complete"])
        self.assertTrue({
            "latencyFiniteNonNegative", "paginationOutcomeConsistency", "recordCapConsistency",
            "recordCapPositive", "recordCountClosure", "recordCountsNonNegative",
            "requestFingerprintSha256", "retryCountNonNegative", "timeOrdering",
        } <= set(expected["missingFields"]))

        detail["completeness"] = {"complete": True, "missingFields": []}
        problems = EXPORTER.receipt_provenance_problems(candidate, 0)
        self.assertTrue(any("inaccurate completeness" in problem for problem in problems))
        browser = node_call("bundle", candidate, self.universe)
        browser_detail = next(
            row for row in browser["manifest"]["receiptExtensions"]
            if row["receiptId"] == "receipt-data"
        )
        self.assertEqual(browser_detail["completeness"], expected)

        broken = deepcopy(self.public_candidate)
        broken["compatibility_provenance"]["receiptProvenance"].pop()
        self.assertTrue(any(
            "exactly one provenance extension" in problem
            for problem in EXPORTER.receipt_provenance_problems(broken, 0)
        ))
        with self.assertRaises(subprocess.CalledProcessError):
            node_call("bundle", broken, self.universe)

    def test_source_native_time_and_jd_are_preserved_without_invented_context(self):
        by_id = {row["measurementId"]: row for row in self.document["measurements"]}
        native = by_id[EVIDENCE.stable_id("measurement", "magnitude", "obs-ztf")]["time"]
        self.assertEqual(native["originalValue"], "2461280.5416667")
        self.assertEqual(native["format"], "SOURCE_NATIVE")
        self.assertAlmostEqual(native["normalizedMjd"], 61280.0416667)
        self.assertIsNone(native["scale"])
        self.assertIsNone(native["referencePosition"])

        jd_only = by_id[EVIDENCE.stable_id("measurement", "limiting-flux", "obs-flux-limit")]["time"]
        self.assertEqual(jd_only["originalValue"], 2461280.5833333)
        self.assertEqual(jd_only["format"], "JD")
        self.assertAlmostEqual(jd_only["normalizedMjd"], 61280.0833333)
        self.assertIsNone(jd_only["scale"])
        self.assertIsNone(jd_only["referencePosition"])

        asserted = by_id[EVIDENCE.stable_id("measurement", "classification", "class-a")]["time"]
        self.assertEqual(asserted["format"], "ISO 8601")
        self.assertIsNone(asserted["scale"])
        self.assertIsNone(asserted["referencePosition"])

    def test_messenger_revision_chain_and_retraction_are_inspectable(self):
        notices = [
            row for row in self.document["measurements"]
            if row["propertyCode"] == "messenger.notice.role"
        ]
        self.assertEqual(len(notices), 2)
        by_record = {row["sourceRecordId"]: row for row in notices}
        self.assertEqual(by_record["Fixture-1:r0"]["activeState"], "SUPERSEDED")
        self.assertEqual(by_record["Fixture-1:r1"]["activeState"], "RETRACTED")
        self.assertIn("messenger-revision=1", by_record["Fixture-1:r1"]["qualityFlags"])
        self.assertIn(
            "supersedes-provider-signal-id=Fixture-1:r0",
            by_record["Fixture-1:r1"]["qualityFlags"],
        )
        revision_conflicts = [
            row for row in self.document["conflictSets"]
            if "SOURCE_REVISION" in row["relations"]
        ]
        self.assertEqual(len(revision_conflicts), 1)
        self.assertEqual(set(revision_conflicts[0]["measurementIds"]), {row["measurementId"] for row in notices})

    def test_archive_product_uses_observation_window_not_response_checksum(self):
        product = next(
            row for row in self.document["dataProducts"]
            if row["sourceContractId"] == "mast"
        )
        self.assertEqual(product["productType"], "IMAGE")
        self.assertEqual(product["observedStart"]["originalValue"], 61279.0)
        self.assertEqual(product["observedStart"]["format"], "MJD")
        self.assertEqual(product["observedStart"]["normalizedMjd"], 61279.0)
        self.assertEqual(product["observedEnd"]["normalizedMjd"], 61279.25)
        self.assertIsNone(product["observedStart"]["scale"])
        self.assertIsNone(product["observedStart"]["referencePosition"])
        self.assertIsNone(product["checksumSha256"], "a query-response checksum is not a product checksum")

    def test_conflicts_are_compatible_and_limit_direction_is_correct(self):
        by_property = {row["propertyCode"]: row for row in self.document["conflictSets"]}
        self.assertIn("transient.classification", by_property)
        self.assertIn("transient.classification.probability", by_property)
        self.assertIn("phot.mag", by_property)
        self.assertIn("src.redshift;meta.id.assoc", by_property)
        labels = [row for row in self.document["measurements"] if row["propertyCode"] == "transient.classification.status"]
        self.assertEqual(len(labels), 1)
        magnitude_conflict = by_property["phot.mag"]
        lower_limit_id = EVIDENCE.stable_id("measurement", "limiting-magnitude", "obs-limit")
        bright_detection_id = EVIDENCE.stable_id("measurement", "magnitude", "obs-ztf")
        self.assertTrue({lower_limit_id, bright_detection_id} <= set(magnitude_conflict["measurementIds"]))
        detection_states = [row for row in self.document["measurements"] if row["propertyCode"] == "photometry.detection_state"]
        self.assertEqual(len(detection_states), 5)

    def test_python_and_browser_projection_are_semantically_equal(self):
        browser = node_call("project", self.public_candidate, self.universe)
        self.assertEqual(browser, self.document)

    def test_projection_is_order_deterministic(self):
        candidate = deepcopy(self.candidate)
        for key, rows in candidate["follow_up"].items():
            if isinstance(rows, list):
                rows.reverse()
        document, accounting, matrix, metadata = EVIDENCE.build_projection(
            candidate, list(reversed(self.universe)), list(reversed(self.attempts)), [],
            GENERATED_AT, "fixture-universe@1",
        )
        self.assertEqual(EVIDENCE.canonical_json_bytes(document), EVIDENCE.canonical_json_bytes(self.document))
        self.assertEqual(accounting, self.accounting)
        self.assertEqual(matrix, self.matrix)
        self.assertEqual(metadata, self.metadata)

    def test_every_persisted_analysis_run_is_retained_in_stable_order(self):
        runs = [
            {"id": "analysis-new", "analysis_type": "light-curve-inference", "analysis_key": "new",
             "method_name": "fixture", "method_version": "2", "status": "complete",
             "input_manifest": {"records": [{"observation_id": "obs-ztf"}]},
             "parameters": {}, "software_versions": {"fixture": "2"}, "result": {"ok": True},
             "warnings": [], "review_state": "machine", "completed_at": "2026-08-29T09:00:00Z"},
            {"id": "analysis-old", "analysis_type": "light-curve-inference", "analysis_key": "old",
             "method_name": "fixture", "method_version": "1", "status": "insufficient-data",
             "input_manifest": {"records": [{"observation_id": "obs-ztf"}]},
             "parameters": {}, "software_versions": {"fixture": "1"},
             "result": {"inference_available": False}, "warnings": [], "review_state": "machine",
             "completed_at": "2026-08-28T09:00:00Z"},
        ]
        document, _, _, _ = EVIDENCE.build_projection(
            self.candidate, self.universe, self.attempts, runs,
            GENERATED_AT, "fixture-universe@1",
        )
        self.assertEqual([row["analysisRunId"] for row in document["analysisRuns"]], ["analysis-old", "analysis-new"])
        self.assertTrue(all(row["inputRecordIds"] for row in document["analysisRuns"]))

    def test_deterministic_bundle_manifest_and_astropy_round_trip(self):
        first = node_call("bundle", self.public_candidate, self.universe)
        second = node_call("bundle", self.public_candidate, self.universe)
        self.assertEqual(first, second)
        for name, metadata in first["manifest"]["files"].items():
            raw = first["files"][name]["content"].encode()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), metadata["sha256"])
            self.assertEqual(len(raw), metadata["bytes"])
        self.assertEqual(first["manifest"]["receiptExtensions"], self.metadata["receiptProvenance"])
        try:
            from astropy.io.votable import parse_single_table
            from astropy.table import Table
        except ImportError as exc:  # pragma: no cover
            self.skipTest(str(exc))
        ecsv_name = next(name for name in first["files"] if name.endswith(".ecsv"))
        vot_name = next(name for name in first["files"] if name.endswith(".vot"))
        ecsv = Table.read(io.BytesIO(first["files"][ecsv_name]["content"].encode()), format="ascii.ecsv")
        votable = parse_single_table(io.BytesIO(first["files"][vot_name]["content"].encode())).to_table()
        ecsv_keys = {(str(row["record_type"]), str(row["record_id"])) for row in ecsv}
        votable_keys = {(str(row["record_type"]), str(row["record_id"])) for row in votable}
        self.assertEqual(ecsv_keys, votable_keys)
        self.assertIn(("target", TARGET_ID), ecsv_keys)
        self.assertTrue(any(str(row["property_code"]) == "phot.mag" and str(row["ucd"]) == "phot.mag" for row in ecsv))
        self.assertTrue(any(str(row["value_kind"]) == "LOWER_LIMIT" for row in ecsv))
        self.assertTrue(any(str(row["value_kind"]) == "UPPER_LIMIT" for row in ecsv))
        for table in (ecsv, votable):
            for column in (
                "normalized_numeric", "normalized_mjd", "ra_deg", "dec_deg",
                "uncertainty_positive", "uncertainty_negative",
            ):
                self.assertIn(column, table.colnames)
                self.assertEqual(table[column].dtype.kind, "f")
        magnitude_rows = [
            row for row in ecsv
            if str(row["record_id"]) == EVIDENCE.stable_id("measurement", "magnitude", "obs-ztf")
        ]
        self.assertEqual(len(magnitude_rows), 1)
        self.assertEqual(float(magnitude_rows[0]["normalized_numeric"]), 18.0)
        self.assertAlmostEqual(float(magnitude_rows[0]["normalized_mjd"]), 61280.0416667)
        self.assertEqual(ecsv.meta.get("schema_name"), "astro-evidence-core")
        self.assertNotIn("<TIMESYS", first["files"][vot_name]["content"])

    def test_manifest_access_dates_and_non_data_labels_follow_receipt_execution(self):
        candidate = deepcopy(self.public_candidate)
        candidate["astro_evidence"]["persistedQueryReceipts"].extend([
            {
                "receiptId": "receipt-not-run", "targetId": TARGET_ID, "sourceContractId": "ztf",
                "queryKind": "PER_TARGET_QUERY", "applicabilityState": "UNRESOLVED", "outcome": "NOT_QUERIED",
                "scope": "fixture preflight", "startedAt": "2026-08-29T11:00:00Z",
                "completedAt": "2026-08-29T11:00:00Z", "requestFingerprintSha256": None,
                "responseChecksumSha256": None, "recordsSeen": None, "recordsRetained": None,
                "recordsRejected": None, "paginationComplete": None, "parserVersion": "fixture",
                "schemaVersion": "fixture", "isCurrent": False, "evidenceUrl": None,
                "errorCode": None, "errorDetail": None, "nextEligibleAt": None, "staleReceiptId": None,
            },
            {
                "receiptId": "receipt-partial", "targetId": TARGET_ID, "sourceContractId": "ztf",
                "queryKind": "PER_TARGET_QUERY", "applicabilityState": "APPLICABLE", "outcome": "PARTIAL_RESULT",
                "scope": "fixture partial", "startedAt": "2026-08-29T10:00:00Z",
                "completedAt": "2026-08-29T10:00:00Z", "requestFingerprintSha256": None,
                "responseChecksumSha256": None, "recordsSeen": 3, "recordsRetained": 2,
                "recordsRejected": 1, "paginationComplete": False, "parserVersion": "fixture",
                "schemaVersion": "fixture", "isCurrent": False, "evidenceUrl": None,
                "errorCode": "ROW_CAP", "errorDetail": None, "nextEligibleAt": None, "staleReceiptId": None,
            },
            {
                "receiptId": "receipt-stale", "targetId": TARGET_ID, "sourceContractId": "ztf",
                "queryKind": "PER_TARGET_QUERY", "applicabilityState": "APPLICABLE",
                "outcome": "STALE_LAST_GOOD_RETAINED", "scope": "fixture retained evidence",
                "startedAt": "2026-08-29T10:30:00Z", "completedAt": "2026-08-29T10:30:00Z",
                "requestFingerprintSha256": None, "responseChecksumSha256": None,
                "recordsSeen": None, "recordsRetained": 2, "recordsRejected": None,
                "paginationComplete": None, "parserVersion": "fixture", "schemaVersion": "fixture",
                "isCurrent": False, "evidenceUrl": None, "errorCode": None, "errorDetail": None,
                "nextEligibleAt": None, "staleReceiptId": "receipt-data",
            },
        ])
        candidate["compatibility_provenance"]["receiptProvenance"].extend([
            {"receiptId": "receipt-not-run", "sourceContractId": "ztf", "executionState": "NOT_EXECUTED"},
            {"receiptId": "receipt-partial", "sourceContractId": "ztf", "executionState": "EXECUTED"},
            {"receiptId": "receipt-stale", "sourceContractId": "ztf", "executionState": "NOT_EXECUTED"},
        ])
        bundle = node_call("bundle", candidate, self.universe)
        self.assertEqual(bundle["manifest"]["sourceAccessDates"]["ztf"], "2026-08-29T10:00:00Z")
        excluded_ids = {row["receiptId"] for row in bundle["manifest"]["excludedOrNonDataSources"]}
        self.assertIn("receipt-not-run", excluded_ids)
        self.assertNotIn("receipt-partial", excluded_ids)
        self.assertNotIn("receipt-stale", excluded_ids)

    def test_chunk_writes_cannot_escape_selected_output_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / "chosen-output"
            target = EXPORTER.write_output_artifact(
                output, "ctas/data/candidate-chunks/00.json", b"fixture\n",
            )
            self.assertEqual(target, output.resolve() / "candidate-chunks/00.json")
            self.assertEqual(target.read_bytes(), b"fixture\n")
            self.assertTrue(output.resolve() in target.parents)
            with self.assertRaises(ValueError):
                EXPORTER.write_output_artifact(
                    output, "ctas/data/../outside.json", b"must-not-write",
                )
            self.assertFalse((root / "outside.json").exists())

    def test_unchanged_candidate_inputs_produce_byte_identical_chunks(self):
        candidate = deepcopy(self.candidate)
        attempts = deepcopy(self.attempts)
        stable_time = EXPORTER.stable_candidate_projection_time(candidate, attempts, [])
        self.assertEqual(stable_time, "2026-08-29T10:00:00Z")
        first_document, accounting, matrix, metadata = EVIDENCE.build_projection(
            candidate, self.universe, attempts, [], stable_time, "fixture-universe@1",
        )
        first = decorate(candidate, first_document, accounting, matrix, metadata)
        first["source_matrix"] = [
            {key: value for key, value in row.items() if key != "retainedEvidenceAgeSeconds"}
            for row in first["source_matrix"]
        ]

        second_candidate = deepcopy(self.candidate)
        second_attempts = deepcopy(self.attempts)
        second_time = EXPORTER.stable_candidate_projection_time(second_candidate, second_attempts, [])
        second_document, second_accounting, second_matrix, second_metadata = EVIDENCE.build_projection(
            second_candidate, self.universe, second_attempts, [], second_time, "fixture-universe@1",
        )
        second = decorate(
            second_candidate, second_document, second_accounting, second_matrix, second_metadata,
        )
        second["source_matrix"] = [
            {key: value for key, value in row.items() if key != "retainedEvidenceAgeSeconds"}
            for row in second["source_matrix"]
        ]

        first_buckets, first_chunks = EXPORTER.candidate_chunk_artifacts([first])
        second_buckets, second_chunks = EXPORTER.candidate_chunk_artifacts([second])
        self.assertEqual(first_chunks, second_chunks)
        serialized = b"".join(first_chunks.values())
        self.assertNotIn(b"retainedEvidenceAgeSeconds", serialized)
        self.assertIn(b'"generatedAt":"2026-08-29T10:00:00Z"', serialized)

        index_rows = [{"event_id": TARGET_ID, "detail_chunk": "candidate-chunks/00.json"}]
        index_document = {
            "schema": EXPORTER.CATALOG_INDEX_SCHEMA, "catalog_as_of": stable_time,
            "candidate_count": 1, "candidates": index_rows,
        }
        index_raw = (
            json.dumps(index_document, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
        checksum = EXPORTER.catalog_semantic_checksum([first])
        _, first_manifest = EXPORTER.complete_catalog_manifest_artifact(
            [first], index_rows, index_raw, first_buckets, first_chunks, checksum,
        )
        _, second_manifest = EXPORTER.complete_catalog_manifest_artifact(
            [second], index_rows, index_raw, second_buckets, second_chunks, checksum,
        )
        self.assertEqual(first_manifest, second_manifest)

    def test_unknown_outcomes_fail_closed_and_offsets_normalize(self):
        with self.assertRaises(ValueError):
            EVIDENCE.canonical_outcome({"terminal_state": "provider-added-a-new-state"})
        self.assertEqual(EVIDENCE.iso_datetime("2026-08-29T12:34:56.123-02:00"), "2026-08-29T14:34:56.123000Z")


if __name__ == "__main__":
    unittest.main(verbosity=2)

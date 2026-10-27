#!/usr/bin/env python3
"""Track 2b: the source comparison published under ``comparison``.

Unit checks of scripts/ctas_comparison.py (the stated pair rules, revisions, the headline
selections, receipt tracing and the certificate gate), the wiring into the exporter, and, when
a generated release is present under ctas/data, every published block of it (schema and gate).
The inputs are shaped exactly like the exporter's queries (ENVELOPE_SQL, CLASSIFICATION_SQL,
HOST_SQL) return them from the collector database.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ctas_comparison as cmp  # noqa: E402

UUID = "15155e9e-052b-4294-abc4-b48c645ad118"
SHA = "c" * 64


def env(provider: str, when: str, **fields) -> dict:
    row = {"event_id": UUID, "envelope_id": f"env-{provider}-{when}", "provider": provider, "topic": f"topic-{provider}",
           "checksum": SHA, "received_at": when, "source_publication_time": None, "event_time": None, "name": "2026zfw",
           "coordinate_frame": "ICRS", "coordinate_epoch": "J2000", "source_url": "https://www.wis-tns.org/object/2026zfw"}
    row.update(fields)
    return row


def tns(when: str, ra="11.0606556", dec="-25.1085945", z=None, modified=None) -> dict:
    return env("tns", when, ra=ra, dec=dec, transient_redshift=z, tns_lastmodified=modified or when,
               discovery_bibcode="2026TNSTR3377....1T", class_bibcodes="2026TNSCR3448....1S")


def classification(provider: str, label: str, when: str, *, superseded=0, retracted=0, probability=None) -> dict:
    return {"event_id": UUID, "assertion_id": f"ca-{provider}-{label}-{when}", "provider": provider, "classification": label,
            "subtype": None, "probability": probability, "method": "unknown", "model_name": None, "model_version": None,
            "asserted_at": when, "citation_url": "https://www.wis-tns.org/object/2026zfw", "superseded": superseded,
            "retracted": retracted, "envelope_id": f"env-cls-{label}-{when}", "envelope_checksum": SHA,
            "envelope_topic": "tns_public_objects_hourly", "envelope_received_at": when,
            "class_bibcodes": "2026TNSCR3448....1S" if provider == "tns" else None}


def ned(name: str, z: float, err: float | None, cross=()) -> dict:
    return {"event_id": UUID, "assertion_id": f"ned-{name}", "provider": "ned", "queried_name": name, "canonical_name": name,
            "cross_identifications": json.dumps(list(cross)), "transient_offset_arcsec": 4.2, "redshift": z, "redshift_error": err,
            "redshift_reference": "2019ApJS..245...25W", "response_checksum": "d" * 64, "source_url": "https://ned.ipac.caltech.edu/",
            "queried_at": "2026-10-20 10:00:00", "envelope_id": None, "envelope_checksum": None}


def candidate(**fields) -> dict:
    row = {"event_id": UUID, "name": "SN2026zfw", "ra_deg": 11.0606556, "dec_deg": -25.1085945, "classification": "SN Ia",
           "redshift": 0.054131, "astro_evidence": {"conflictSets": [], "persistedQueryReceipts": []}, "follow_up": {}}
    row.update(fields)
    return row


def build(envelopes=(), classifications=(), hosts=(), **extra):
    return cmp.build_comparison(candidate(**extra), envelopes=list(envelopes), classifications=list(classifications), hosts=list(hosts))


def prop(block: dict, code: str) -> dict:
    return next(item for item in block["properties"] if item["code"] == code)


def ztf_observation(provider: str, assertion: str, *, magnitude=None, error=None, limit=None, detection=1) -> dict:
    """A ZTF alert observation shaped exactly as the exporter publishes it in follow_up."""
    pipeline = {"ztf-alerce": "ALeRCE public ZTF broker API", "rubin-fink": "Fink public broker archive",
                "rubin-lasair": "Lasair public broker", "ztf-irsa": "IRSA ZTF forced photometry"}[provider]
    row = {"assertion_id": assertion, "band": "r", "calibration": "ZTF alert-packet calibration", "detection": detection,
           "difference_photometry": 1, "instrument": "ZTF alert photometry", "jd": 2461216.9737269,
           "magnitude_system": "AB", "mjd": 61216.47372689983, "observed_at": "2026-06-25T11:22:10.004145Z",
           "original_band": "ztf-r", "original_time": "2026-06-25 11:22:10.004145+00:00", "photometry_method": "difference-psf",
           "pipeline": pipeline, "provider": provider, "provider_observation_id": f"{provider}:ZTF26abdrmsn:1",
           "source_record_id": f"env-{assertion}", "ctas_received_at": "2026-10-15T19:04:41Z"}
    if magnitude is not None:
        row.update(magnitude=magnitude, magnitude_error=error)
    if limit is not None:
        row["limiting_magnitude"] = limit
    return row


def photometry_candidate(observations: list[dict]) -> tuple[dict, list[dict], dict]:
    """A candidate run through the real AstroEvidence measurement and conflict screen."""
    from ctas_astro_evidence import build_conflicts, build_measurements
    row = candidate(follow_up={"observations": observations})
    measurements, _ids, compatibility = build_measurements(row)
    row["astro_evidence"] = {"conflictSets": build_conflicts(UUID, measurements, compatibility), "persistedQueryReceipts": []}
    receipts = {obs["source_record_id"]: {"kind": "alert-envelope", "envelopeId": obs["source_record_id"], "checksumSha256": SHA,
                                          "topic": "alert", "receivedAt": "2026-10-15T19:04:41Z"} for obs in observations}
    return row, measurements, receipts


class PhotometryTests(unittest.TestCase):
    def build(self, observations):
        row, measurements, receipts = photometry_candidate(observations)
        screened = [s for s in row["astro_evidence"]["conflictSets"] if s["propertyCode"].startswith("phot.")]
        block = cmp.build_comparison(row, envelopes=[], classifications=[], hosts=[], measurements=measurements,
                                     envelope_receipts=receipts)
        return screened, [p for p in (block or {}).get("properties", []) if p["code"] == "photometry"]

    def test_the_limit_reported_with_a_detection_is_not_a_non_detection(self):
        # Seen on 518 real events: three brokers relay one ZTF detection; ALeRCE also reports the
        # epoch's limiting magnitude, which the AstroEvidence screen compares as a non-detection.
        screened, listed = self.build([
            ztf_observation("ztf-alerce", "a1", magnitude=19.560162, error=0.12774864, limit=20.0827),
            ztf_observation("rubin-fink", "a2", magnitude=19.560162, error=0.12774864),
            ztf_observation("rubin-lasair", "a3", magnitude=19.5602, error=0.127749),
        ])
        self.assertTrue(screened, "the AstroEvidence screen flags the group")
        self.assertEqual(listed, [], "the comparison does not list a conflict that is a detection threshold")

    def test_a_real_non_detection_fainter_than_a_detection_is_listed(self):
        screened, listed = self.build([
            ztf_observation("rubin-fink", "b1", magnitude=19.56, error=0.12),
            ztf_observation("rubin-lasair", "b2", limit=20.4, detection=0),
        ])
        self.assertTrue(screened)
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0]["set"]["conflict"])
        self.assertEqual({row["limitType"] for row in listed[0]["rows"]}, {"NONE", "LOWER_LIMIT"})
        self.assertTrue(all(row["receipt"]["kind"] == "alert-envelope" for row in listed[0]["rows"]))
        self.assertIn("detection threshold", listed[0]["rule"])

    def test_detections_beyond_three_sigma_are_listed(self):
        screened, listed = self.build([
            ztf_observation("rubin-fink", "c1", magnitude=19.10, error=0.05),
            ztf_observation("ztf-alerce", "c2", magnitude=19.80, error=0.05, limit=20.6),
        ])
        self.assertTrue(screened)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["set"]["relationLabel"], "statistically-inconsistent")
        self.assertEqual(len(listed[0]["rows"]), 2, "the threshold row is left out")


class RuleTests(unittest.TestCase):
    def test_positions_without_uncertainty_use_the_stated_tolerance(self):
        agree = build([tns("2026-08-19 08:09:17"), env("tns-public-reports", "2026-08-20 00:00:00", detail_ra=11.06066, detail_dec=-25.10862)])
        self.assertFalse(prop(agree, "position")["set"]["conflict"])
        far = build([tns("2026-08-19 08:09:17"), env("tns-public-reports", "2026-08-20 00:00:00", detail_ra=11.0616556, detail_dec=-25.1085945)])
        position = prop(far, "position")
        self.assertTrue(position["set"]["conflict"])
        self.assertEqual(position["set"]["relationLabel"], "statistically-inconsistent")
        self.assertIn("tolerance 2 arcsec", position["rows"][1]["compat"]["reason"])

    def test_a_localization_is_compared_with_its_error_radius_and_two_without_radii_are_listed(self):
        inside = build([tns("2026-08-19 08:09:17"), env("gcn", "2026-08-19 00:00:00", ra=11.07, dec=-25.11, coordinate_error_arcsec=120.0)])
        self.assertFalse(prop(inside, "position")["set"]["conflict"])
        outside = build([tns("2026-08-19 08:09:17"), env("gcn", "2026-08-19 00:00:00", ra=12.5, dec=-25.11, coordinate_error_arcsec=60.0)])
        self.assertTrue(prop(outside, "position")["set"]["conflict"])
        two = build([env("gcn", "2026-08-19 00:00:00", ra=11.0, dec=-25.0), env("gcn", "2026-08-19 01:00:00", topic="other", ra=11.5, dec=-25.2)])
        position = prop(two, "position")
        self.assertFalse(position["set"]["conflict"])
        self.assertEqual(position["set"]["relationLabel"], "not-yet-assessed")

    def test_a_revision_within_the_rule_is_consistent_and_beyond_it_is_labelled(self):
        small = build([tns("2026-08-02 14:06:28", ra="247.381542", dec="19.620217"), tns("2026-08-02 23:43:39", ra="247.381534644", dec="19.6201926947")])
        position = prop(small, "position")
        self.assertEqual([row["state"] for row in position["rows"]], ["active", "superseded"])
        self.assertFalse(position["set"]["conflict"])
        big = build([tns("2026-08-31 16:31:42", z="0.014"), tns("2026-10-12 08:25:50", z="0.007088")])
        redshift = prop(big, "redshift")
        self.assertTrue(redshift["set"]["conflict"])
        self.assertEqual(redshift["set"]["relationLabel"], "revision-or-retraction")
        self.assertEqual(redshift["rows"][1]["value"], "0.014")

    def test_classes_refine_or_conflict_and_alert_labels_are_not_compared(self):
        refine = build(classifications=[classification("tns", "SN II", "2026-09-20 21:03:40"), classification("tns-public-reports", "SN IIP", "2026-09-22 14:05:42")])
        self.assertFalse(prop(refine, "classification")["set"]["conflict"])
        clash = build(classifications=[classification("tns", "SN Ia", "2026-10-15 22:03:28"), classification("tns-public-reports", "SN II", "2026-10-16 00:00:00")])
        self.assertEqual(prop(clash, "classification")["set"]["relationLabel"], "status-conflict")
        labels = build(classifications=[classification("gcn", "Gamma-ray burst candidate", "2026-08-04 05:48:03", probability=0.8),
                                        classification("gcn", "Solar Flare", "2026-08-04 05:48:03", probability=0.1)])
        self.assertFalse(prop(labels, "classification")["set"]["conflict"])
        self.assertEqual(prop(labels, "classification")["set"]["relationLabel"], "method-incompatible")
        species = build(classifications=[classification("tns", "SN Ia", "2026-10-15 22:03:28"), classification("gcn", "GRB", "2026-10-15 00:00:00")])
        self.assertEqual(prop(species, "classification")["set"]["relationLabel"], "definition-incompatible")

    def test_a_retracted_classification_is_a_retraction(self):
        block = build(classifications=[classification("tns", "SN Ia", "2026-10-15 22:03:28"),
                                       classification("tns", "SN II", "2026-10-10 00:00:00", retracted=1)])
        classes = prop(block, "classification")
        self.assertTrue(classes["set"]["conflict"])
        self.assertEqual(classes["set"]["relationLabel"], "revision-or-retraction")
        self.assertEqual(classes["rows"][-1]["state"], "retracted")

    def test_redshift_definitions_hosts_and_uncertainties(self):
        block = build([tns("2026-08-19 08:09:17", z="0.054131")], hosts=[ned("NGC 1", 0.0539, 0.0001)])
        redshift = prop(block, "redshift")
        self.assertEqual(redshift["set"]["relationLabel"], "definition-incompatible")
        self.assertFalse(redshift["set"]["conflict"])
        hosts = build([env("tns-public-reports", "2026-08-20 00:00:00", detail_host_name="IC 4682", detail_host_redshift=0.011908)],
                      hosts=[ned("NGC 7", 0.02, 0.0001)])
        self.assertEqual(prop(hosts, "redshift")["set"]["relationLabel"], "identity-ambiguity")
        sigma = build(hosts=[ned("NGC 1", 0.0539, 0.0001), {**ned("NGC 1", 0.0560, 0.0001), "assertion_id": "ned-2", "provider": "ned-2"}])
        self.assertEqual(prop(sigma, "redshift")["set"]["relationLabel"], "statistically-inconsistent")
        self.assertGreater(prop(sigma, "redshift")["set"]["maxPairwiseSigma"], 3)

    def test_hosts_agree_by_name_or_cross_identification(self):
        alias = build([env("tns-public-reports", "2026-08-20 00:00:00", detail_host_name="NGC3310")],
                      hosts=[ned("WISEA J1038", 0.0033, None, cross=["NGC 3310", "UGC 5786"])])
        self.assertIsNone(prop(alias, "host")["set"]["relationLabel"])
        other = build([env("tns-public-reports", "2026-08-20 00:00:00", detail_host_name="IC 4682")], hosts=[ned("NGC 7", 0.02, None)])
        self.assertEqual(prop(other, "host")["set"]["relationLabel"], "identity-ambiguity")
        self.assertIsNone(prop(other, "host")["rows"][0]["associationProbability"])

    def test_astronote_sexagesimal_positions_and_bibcodes(self):
        note = env("tns-astronotes", "2026-09-01 00:00:00", ra="00:44:14.557", dec="-25:06:30.94", astronote_id="2026-239",
                   source_publication_time="2026-09-01T00:00:00Z", reported_assertions=json.dumps({"reported_object_type": "SN Ia"}))
        block = build([tns("2026-08-19 08:09:17"), note])
        position = prop(block, "position")
        note_row = next(row for row in position["rows"] if row["sourceId"] == "tns-astronotes")
        self.assertAlmostEqual(note_row["normalized"][0], 11.0606542, places=5)
        tns_row = next(row for row in position["rows"] if row["sourceId"] == "tns")
        self.assertEqual(tns_row["reference"]["bibcode"], "2026TNSTR3377....1T")
        self.assertIn("ui.adsabs.harvard.edu", tns_row["reference"]["url"])

    def test_nothing_is_averaged(self):
        block = build([tns("2026-08-19 08:09:17", z="0.054131"), env("tns-public-reports", "2026-08-20 00:00:00", detail_transient_redshift=0.0545)])
        text = json.dumps(block)
        for word in ('"mean"', '"average"', '"weightedMean"'):
            self.assertNotIn(word, text)
        values = sorted(row["normalized"] for row in prop(block, "redshift")["rows"])
        self.assertEqual(values, [0.054131, 0.0545])


class SelectionTests(unittest.TestCase):
    def test_headline_selections_follow_the_written_rules_and_never_replace_rows(self):
        block = build([tns("2026-08-19 08:09:17", z="0.054131"), env("tns-public-reports", "2026-08-20 00:00:00", detail_ra=11.06066, detail_dec=-25.10862,
                                                                       detail_transient_redshift=0.0545)],
                      classifications=[classification("tns", "SN Ia", "2026-08-20 00:00:00"), classification("tns-public-reports", "SN Ia-91T", "2026-08-21 00:00:00")])
        for code, expected in (("position", [11.0606556, -25.1085945]), ("classification", "sn ia"), ("redshift", 0.054131)):
            selection = prop(block, code)["selections"][0]
            self.assertEqual(selection["label"], cmp.SELECTION_LABEL)
            self.assertEqual(selection["value"], expected)
            self.assertTrue(selection["matchesDisplayedValue"])
            self.assertEqual(selection["basis"], "naming-authority")
        self.assertEqual(len(prop(block, "redshift")["rows"]), 2)

    def test_a_selection_that_differs_from_the_display_is_reported_not_applied(self):
        block = build([tns("2026-08-19 08:09:17"), env("tns-public-reports", "2026-08-20 00:00:00", detail_ra=11.06066, detail_dec=-25.10862)],
                      ra_deg=12.0, dec_deg=-25.0)
        self.assertFalse(prop(block, "position")["selections"][0]["matchesDisplayedValue"])
        self.assertEqual(block["summary"]["selectionsDifferingFromDisplay"], 1)

    def test_no_selection_without_an_eligible_row(self):
        block = build(classifications=[classification("gcn", "GRB", "2026-08-04 05:48:03"), classification("gcn", "Solar Flare", "2026-08-04 05:48:03")])
        self.assertEqual(prop(block, "classification")["selections"], [])


class ReceiptAndGateTests(unittest.TestCase):
    def test_an_event_with_fewer_than_two_rows_per_property_has_no_block(self):
        self.assertIsNone(build([tns("2026-08-19 08:09:17")]))

    def test_every_row_names_a_receipt_and_the_gate_passes(self):
        block = build([tns("2026-08-19 08:09:17"), env("tns-public-reports", "2026-08-20 00:00:00", detail_ra=11.0616556, detail_dec=-25.1085945)],
                      hosts=[ned("NGC 1", 0.05, None), ned("NGC 2", 0.05, None)])
        for item in block["properties"]:
            for row in item["rows"]:
                self.assertIn(row["receipt"]["kind"], cmp.RECEIPT_KINDS)
        self.assertEqual(prop(block, "host")["rows"][0]["receipt"]["kind"], "provider-response")
        self.assertEqual(cmp.comparison_problems([{"name": "SN2026zfw", "comparison": block}]), [])

    def test_a_query_receipt_traces_rows_without_an_envelope(self):
        receipts = {"persistedQueryReceipts": [{"receiptId": "r1", "sourceContractId": "ned", "outcome": "DATA_RETURNED",
                                                "responseChecksumSha256": "e" * 64, "completedAt": "2026-10-20T10:00:00Z"}], "conflictSets": []}
        hosts = [{**ned("NGC 1", 0.05, None), "response_checksum": None}, {**ned("NGC 2", 0.05, None), "response_checksum": None}]
        block = build(hosts=hosts, astro_evidence=receipts)
        self.assertEqual(prop(block, "host")["rows"][0]["receipt"]["kind"], "query-receipt")

    def test_the_gate_names_untraced_rows_and_broken_structure(self):
        block = build([tns("2026-08-19 08:09:17"), env("tns-public-reports", "2026-08-20 00:00:00", detail_ra=11.0616556, detail_dec=-25.1085945)])
        broken = json.loads(json.dumps(block))
        prop(broken, "position")["rows"][1]["receipt"] = None
        prop(broken, "position")["set"]["relationLabel"] = "averaged"
        prop(broken, "position")["selections"][0]["chosenRow"] = 9
        problems = cmp.comparison_problems([{"name": "X", "comparison": broken}])
        self.assertTrue(any("does not trace to a receipt" in line for line in problems))
        self.assertTrue(any("outside the vocabulary" in line for line in problems))
        self.assertTrue(any("selection points outside" in line for line in problems))
        self.assertEqual(cmp.comparison_problems([{"name": "Y"}]), [])

    def test_blocks_are_deterministic(self):
        rows = [tns("2026-08-19 08:09:17"), env("tns-public-reports", "2026-08-20 00:00:00", detail_ra=11.06066, detail_dec=-25.10862)]
        self.assertEqual(json.dumps(build(rows), sort_keys=True), json.dumps(build(list(reversed(rows))), sort_keys=True))


class WiringTests(unittest.TestCase):
    def test_exporter_binds_the_files_and_declares_the_gate(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("exporter_for_comparison", ROOT / "scripts/export_ctas_snapshot.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        for path in ("scripts/ctas_comparison.py", "scripts/test_ctas_comparison.py", "ctas/comparison.js", "scripts/test_ctas_comparison.js"):
            self.assertIn(path, exporter.BOUND_CODE_PATHS)
        source = (ROOT / "scripts/export_ctas_snapshot.py").read_text()
        self.assertIn('gate("comparison-receipt-trace"', source)
        self.assertIn("comparison.ENVELOPE_SQL", source)
        classes = json.loads((ROOT / "scripts/check-classes.json").read_text())["checks"]
        self.assertEqual(classes["ctas.gate.comparison-receipt-trace"]["class"], "integrity")
        self.assertEqual(classes["ctas.test_ctas_comparison"]["class"], "integrity")
        page = (ROOT / "ctas.html").read_text()
        self.assertLess(page.index("ctas/comparison.js"), page.index("ctas/app.js"))
        schema = json.loads((ROOT / "ctas/schemas/candidate-chunk.schema.json").read_text())
        self.assertIn("comparison", schema["$defs"]["candidate"]["properties"])
        self.assertIn("scripts/test_ctas_comparison.py", (ROOT / "scripts/publish_ctas.sh").read_text())

    def test_the_queries_run_on_the_collector_schema(self):
        import sqlite3
        con = sqlite3.connect(":memory:")
        con.executescript("""
            CREATE TABLE alert_envelopes (id TEXT, provider TEXT, topic TEXT, checksum TEXT, received_at TEXT,
              source_publication_time TEXT, event_time TEXT, raw_payload TEXT, event_id TEXT, rights_decision TEXT);
            CREATE TABLE classification_assertions (id TEXT, event_id TEXT, provider TEXT, classification TEXT, subtype TEXT,
              probability REAL, method TEXT, model_name TEXT, model_version TEXT, asserted_at TEXT, citation_url TEXT,
              superseded INTEGER, retracted INTEGER, envelope_id TEXT, data_rights TEXT);
            CREATE TABLE host_context_assertions (id TEXT, event_id TEXT, provider TEXT, queried_name TEXT, canonical_name TEXT,
              cross_identifications TEXT, transient_offset_arcsec REAL, redshift REAL, redshift_error REAL, redshift_reference TEXT,
              response_checksum TEXT, source_url TEXT, queried_at TEXT, envelope_id TEXT, data_rights TEXT);
        """)
        con.execute("INSERT INTO alert_envelopes VALUES ('e1','tns','tns_public_objects_hourly',?, '2026-08-19', NULL, NULL, ?, ?, 'public')",
                    (SHA, json.dumps({"ra": "11.06", "dec": "-25.1", "name": "2026zfw", "source_payload": {"tns_public_objects_hourly_row": {"Discovery_ADS_bibcode": "2026TNSTR3377....1T"}}}), UUID))
        row = con.execute(cmp.ENVELOPE_SQL.format(ids="?"), [UUID]).fetchone()
        self.assertIsNotNone(row)
        con.execute(cmp.CLASSIFICATION_SQL.format(ids="?"), [UUID]).fetchall()
        con.execute(cmp.HOST_SQL.format(ids="?"), [UUID]).fetchall()


class ReleaseTests(unittest.TestCase):
    """Every comparison block of the generated release under ctas/data, if present."""

    @classmethod
    def setUpClass(cls):
        manifest_path = ROOT / "ctas/data/candidate-chunks/manifest.json"
        if not manifest_path.exists():
            raise unittest.SkipTest("no generated CTAS release under ctas/data")
        from ctas_chunks import decode_chunk
        manifest = json.loads(manifest_path.read_text())
        cls.blocks = []
        for metadata in manifest["chunks"]:
            raw = (ROOT / metadata["path"]).read_bytes()
            for row in decode_chunk(raw, lambda path: (ROOT / path).read_bytes())["candidates"]:
                if cmp.CANDIDATE_KEY in row:
                    cls.blocks.append({"name": row.get("name"), "event_id": row.get("event_id"), cmp.CANDIDATE_KEY: row[cmp.CANDIDATE_KEY]})
        cls.certificate = json.loads((ROOT / "ctas/data/certification.json").read_text())

    def test_every_published_block_passes_the_gate_and_the_schema(self):
        self.assertEqual(cmp.comparison_problems(self.blocks), [])
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((ROOT / "ctas/schemas/candidate-chunk.schema.json").read_text())
        block_schema = dict(schema["$defs"]["candidate"]["properties"]["comparison"])
        block_schema["$defs"] = schema["$defs"]
        validator = Draft202012Validator(block_schema)
        for row in self.blocks:
            errors = [error.message for error in validator.iter_errors(row[cmp.CANDIDATE_KEY])]
            self.assertEqual(errors, [], row.get("name"))

    def test_the_gate_ran_and_the_counts_are_reported(self):
        gates = {gate["id"]: gate for gate in self.certificate.get("gates", [])}
        if "comparison-receipt-trace" in gates:
            self.assertTrue(gates["comparison-receipt-trace"]["passed"], gates["comparison-receipt-trace"]["evidence"])
        counts: dict[str, int] = {}
        conflicts: dict[str, int] = {}
        for row in self.blocks:
            for item in row[cmp.CANDIDATE_KEY]["properties"]:
                counts[item["code"]] = counts.get(item["code"], 0) + 1
                if item["set"]["conflict"]:
                    conflicts[item["code"]] = conflicts.get(item["code"], 0) + 1
        print(f"release: {len(self.blocks)} dossiers compare sources {dict(sorted(counts.items()))}; in conflict {dict(sorted(conflicts.items()))}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

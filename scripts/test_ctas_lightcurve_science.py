#!/usr/bin/env python3
"""Track 3c part 1: light-curve science and broker classes.

Unit checks of scripts/ctas_lightcurve_science.py (input selection and de-duplication, the
multiband fits, the features, absolute magnitude, jump detector, periodogram, SALT3
selection, screens, the certificate gate, determinism) and scripts/ctas_broker_classes.py
(parsing recorded real ALeRCE, Fink and ANTARES responses for ZTF26abljbah, the stated
reading rules, the consensus panel, the certificate gate), the wiring into the exporter, and,
when a generated release is present under ctas/data, every published block of it.

The broker fixtures under tests/fixtures/ctas-brokers are the complete public responses
recorded on 2026-10-27 (byte lengths 1,674 / 834 / 6,070; the not-found answers 249 / 2 /
272).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ctas_broker_classes as brokers  # noqa: E402
import ctas_lightcurve_science as lc  # noqa: E402

FIXTURES = ROOT / "tests/fixtures/ctas-brokers"
FIXTURE_SHA256 = {
    "alerce-ZTF26abljbah.json": "38da7a922858bc4bb54619c2d48b6d31fb38f673b58833c7bc07382b53d9655e",
    "fink-ZTF26abljbah.json": "516f7d64b247ef049936d46fb7f11259713d347e6b00a740ef9202c6140e4aeb",
    "antares-ZTF26abljbah.json": "fabd676a6c16e4a0e6dacddcdbf74c82e96230ccd817bfcfdc82f1f40e857867",
    "alerce-404-ZTF99zzzzzzz.json": "e197222b83c236e1b5429a95501ce4413949e1bbd8b9fe537afb29af60f2ed41",
    "fink-empty-ZTF99zzzzzzz.json": "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945",
    "antares-empty-ZTF99zzzzzzz.json": "c108fcaf36edf33016b8edbfaa81a86de3c862587cace114c67aea2532b1fcad",
}


def bazin_mag(t, t0, tau_rise, tau_fall, peak_flux):
    shape = math.exp(-(t - t0) / tau_fall) / (1 + math.exp(-(t - t0) / tau_rise))
    return lc.ZP_UJY - 2.5 * math.log10(peak_flux * shape)


def synthetic_event(*, bands=("ztf-g", "ztf-r"), n=14, start=60000.0, step=2.5, noise=0.02, duplicate=False,
                    t0=60012.0, tau_rise=3.0, tau_fall=25.0):
    rows = []
    amplitudes = {"ztf-g": 400.0, "ztf-r": 600.0, "ztf-i": 500.0}
    for i in range(n):
        for k, band in enumerate(bands):
            t = start + i * step + 0.1 * k
            jitter = noise * math.sin(7.3 * i + 2.1 * k)  # deterministic "noise"
            mag = bazin_mag(t, t0, tau_rise, tau_fall, amplitudes[band]) + jitter
            row = {"assertion_id": f"{band}-{i}", "provider": "ztf-alerce", "original_band": band, "band": band[-1],
                   "magnitude_system": "AB", "detection": 1, "mjd": t, "magnitude": mag, "magnitude_error": 0.05,
                   "quality_flags": ["alerce-alert-detection"], "superseded": 0}
            rows.append(row)
            if duplicate:
                rows.append({**row, "assertion_id": f"fink-{band}-{i}", "provider": "rubin-fink", "magnitude_error": 0.06})
    return {"event_id": "e1", "name": "SN2026test", "classification": "Unclassified", "ra_deg": 150.0, "dec_deg": 2.0,
            "follow_up": {"observations": rows}}


class InputTests(unittest.TestCase):
    def test_same_exposure_from_two_brokers_is_kept_once(self):
        rows, excluded = lc.usable_detections(synthetic_event(duplicate=True)["follow_up"]["observations"])
        self.assertEqual(len(rows), 28)
        self.assertEqual(excluded.get("same-exposure-duplicate"), 28)
        self.assertTrue(all(row["provider"] == "ztf-alerce" for row in rows))  # smaller uncertainty wins

    def test_unfitted_bands_and_flags_are_counted_not_mixed(self):
        event = synthetic_event()
        extra = [
            {"provider": "tns-astronotes", "original_band": "r", "detection": 1, "mjd": 60001, "magnitude": 18, "magnitude_error": 0.1, "magnitude_system": "AB"},
            {"provider": "ztf-alerce", "original_band": "ztf-r", "detection": 1, "mjd": 60002, "magnitude": 18, "magnitude_error": 0.1,
             "magnitude_system": "AB", "quality_flags": json.dumps(["negative-difference-flux", "scientific-fit-excluded"])},
            {"provider": "atlas", "original_band": "o", "detection": 1, "mjd": 60003, "magnitude": 18, "magnitude_error": 0.9, "magnitude_system": "AB"},
            {"provider": "ztf-alerce", "original_band": "ztf-g", "detection": 0, "mjd": 60004, "limiting_magnitude": 20.5},
        ]
        rows, excluded = lc.usable_detections(event["follow_up"]["observations"] + extra)
        self.assertEqual(len(rows), 28)
        self.assertEqual(excluded, {"band-or-pipeline-not-fitted": 1, "flagged-not-for-fits": 1, "uncertainty-missing-or-above-0.5-mag": 1})

    def test_atlas_bands_are_fitted_bands(self):
        self.assertEqual(lc.band_key({"provider": "atlas", "original_band": "o", "magnitude_system": "AB"}), "atlaso")
        self.assertEqual(lc.band_key({"provider": "rubin-lasair", "original_band": "ztf-i"}), "ztfi")
        self.assertIsNone(lc.band_key({"provider": "ztf-irsa-release", "original_band": "ztf-r"}))
        self.assertIsNone(lc.band_key({"provider": "atlas", "original_band": "o", "magnitude_system": "Vega"}))


class FitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.event = synthetic_event()
        cls.block = lc.build_lightcurve_science(cls.event)
        cls.runs = {run["analysisType"]: run for run in cls.block["runs"]}

    def test_both_fits_complete_and_the_bazin_fit_recovers_the_shape(self):
        bazin = self.runs["multiband-bazin"]
        self.assertEqual(bazin["status"], "complete", bazin.get("notComputedReason"))
        self.assertEqual(self.runs["multiband-villar"]["status"], "complete")
        parameters = bazin["result"]["parameters"]
        t0 = bazin["result"]["referenceMjd"] + parameters["t0"]["value"]
        self.assertAlmostEqual(t0, 60012.0, delta=1.0)
        self.assertAlmostEqual(math.exp(parameters["log_tau_rise"]["value"]), 3.0, delta=1.0)
        self.assertAlmostEqual(math.exp(parameters["log_tau_fall"]["value"]), 25.0, delta=4.0)
        self.assertEqual(bazin["result"]["bands"], ["ztfg", "ztfr"])

    def test_features_are_shared_shape_with_per_band_peaks(self):
        features = self.runs["lightcurve-features"]
        self.assertEqual(features["status"], "complete")
        result = features["result"]
        self.assertTrue(result["sharedShape"])
        self.assertIn(result["model"], {"multiband-bazin", "multiband-villar"})
        self.assertGreater(result["halfMaxRiseDays"], 0)
        self.assertGreater(result["halfMaxDurationDays"], result["halfMaxRiseDays"])
        self.assertLess(result["bands"]["ztfr"]["peakMagnitude"], result["bands"]["ztfg"]["peakMagnitude"])  # r is brighter
        self.assertIsNotNone(result["bands"]["ztfr"]["peakMagnitudeSigma"])

    def test_the_block_is_deterministic(self):
        again = lc.build_lightcurve_science(copy.deepcopy(self.event))
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.block, sort_keys=True))
        parallel = lc.build_all([copy.deepcopy(self.event)] * 60, workers=2)
        self.assertEqual(json.dumps(parallel["e1"], sort_keys=True), json.dumps(self.block, sort_keys=True))

    def test_every_run_names_method_inputs_and_claim_and_the_gate_passes(self):
        self.assertEqual(lc.lightcurve_problems([{"name": "x", lc.CANDIDATE_KEY: self.block}]), [])
        tampered = copy.deepcopy(self.block)
        tampered["runs"][0]["result"]["chi2"] = 0.0
        self.assertTrue(any("result checksum" in p for p in lc.lightcurve_problems([{"name": "x", lc.CANDIDATE_KEY: tampered}])))
        tampered = copy.deepcopy(self.block)
        tampered["runs"][1]["status"] = "not-computed"
        self.assertTrue(lc.lightcurve_problems([{"name": "x", lc.CANDIDATE_KEY: tampered}]))

    def test_absolute_magnitude_needs_a_redshift_and_says_so(self):
        run = self.runs["absolute-magnitude"]
        self.assertEqual(run["status"], "not-computed")
        self.assertIn("redshift", run["notComputedReason"])

    def test_one_band_gives_reasons_not_defaults(self):
        block = lc.build_lightcurve_science(synthetic_event(bands=("ztf-r",)))
        runs = {run["analysisType"]: run for run in block["runs"]}
        self.assertEqual(runs["multiband-bazin"]["status"], "not-computed")
        self.assertIn("two bands", runs["multiband-bazin"]["notComputedReason"])
        self.assertNotIn("result", runs["lightcurve-features"])
        self.assertIsNone(runs["screens"]["result"]["fastTransient"]["flag"])

    def test_an_event_without_fitted_detections_gets_no_block(self):
        self.assertIsNone(lc.build_lightcurve_science({"follow_up": {"observations": []}}))


class ExtinctionTests(unittest.TestCase):
    def test_fitzpatrick_extinction_is_positive_and_bluer_is_larger(self):
        try:
            import extinction  # noqa: F401
        except ImportError:
            self.skipTest("extinction (an sncosmo dependency) is not installed")
        g, r = lc.band_extinction(0.1, lc.BANDS["ztfg"][1]), lc.band_extinction(0.1, lc.BANDS["ztfr"][1])
        self.assertGreater(g, r)
        self.assertAlmostEqual(r, 0.1 * 0.86 * 2.27, delta=0.06)

    def test_sfd_lookup_when_the_map_is_available(self):
        directory = lc.sfd_directory()
        if directory is None:
            self.skipTest("CTAS_SFD_DIR is not set")
        # The Galactic poles are nearly dust-free; the anticentre plane is not.
        self.assertLess(lc.sfd_ebv(192.85948, 27.12825, directory), 0.05)
        self.assertGreater(lc.sfd_ebv(83.0, 25.0, directory), 0.3)

    def test_absolute_magnitude_with_a_redshift(self):
        if lc.sfd_directory() is None:
            self.skipTest("CTAS_SFD_DIR is not set")
        event = synthetic_event()
        event["redshift"] = 0.05
        run = {r["analysisType"]: r for r in lc.build_lightcurve_science(event)["runs"]}["absolute-magnitude"]
        self.assertEqual(run["status"], "complete")
        self.assertAlmostEqual(run["result"]["distanceModulus"], 36.7, delta=0.2)
        self.assertAlmostEqual(run["result"]["kCorrectionTerm"], 2.5 * math.log10(1.05), places=4)


class JumpAndPeriodTests(unittest.TestCase):
    def test_a_brightening_above_the_baseline_is_a_jump(self):
        rows = [{"band": "ztfr", "mjd": 60000.0 + d, "mag": 19.0, "err": 0.05} for d in range(0, 50, 5)]
        rows.append({"band": "ztfr", "mjd": 60061.0, "mag": 17.5, "err": 0.05})
        run = lc.jump_run(sorted(rows, key=lambda r: r["mjd"]), {"inputChecksumSha256": "0" * 64})
        self.assertEqual(run["status"], "complete")
        self.assertTrue(run["result"]["jump"])
        steady = rows[:-1] + [{"band": "ztfr", "mjd": 60061.0, "mag": 19.01, "err": 0.05}]
        self.assertFalse(lc.jump_run(steady, {"inputChecksumSha256": "0" * 64})["result"]["jump"])

    def test_a_sinusoid_period_is_recovered(self):
        # Irregular (deterministic) sampling, as for a survey; a regular grid would alias.
        times = [60000.0 + 1.37 * i + 0.45 * math.sin(i * i * 0.731) for i in range(60)]
        rows = [{"band": "ztfg", "mjd": t, "mag": 18.0 + 0.3 * math.sin(2 * math.pi * (t - 60000.0) / 2.5), "err": 0.02}
                for t in times]
        run = lc.periodogram_run(rows, {"inputChecksumSha256": "0" * 64})
        self.assertEqual(run["status"], "complete")
        band = run["result"]["bands"]["ztfg"]
        self.assertAlmostEqual(band["bestPeriodDays"], 2.5, delta=0.05)
        self.assertTrue(band["periodicCandidate"])


class SelectionAndScreenTests(unittest.TestCase):
    def test_salt3_selection_rule(self):
        self.assertEqual(lc.ia_rule({"classification": "SN Ia"}, None)[0], True)
        self.assertEqual(lc.ia_rule({"classification": "SN II"}, None)[0], False)
        broker = {"brokers": [{"broker": "Fink", "reading": {"coarseClass": "SN Ia", "rule": "r"}}]}
        self.assertEqual(lc.ia_rule({"classification": "Unclassified"}, broker)[0], True)
        self.assertIsNone(lc.salt3_run({"classification": "SN II"}, [], {"inputChecksumSha256": "0" * 64}, None))

    def test_fast_and_nuclear_screens_state_their_rules(self):
        features = {"halfMaxRiseDays": 1.5, "declineMagPerDay15": 0.05, "bands": {}}
        candidate = {"follow_up": {"host_context": [{"transient_offset_arcsec": 0.3}]}}
        run = lc.screens_run(candidate, features, {"inputChecksumSha256": "0" * 64})
        self.assertTrue(run["result"]["fastTransient"]["flag"])
        self.assertTrue(run["result"]["nuclearTransient"]["flag"])
        self.assertIn("not classifications", run["claimBoundary"])


# ============================================================================ brokers
def fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def receipt(source: str, body: bytes | None, *, state: str = "data", attempt: str = "a1", when: str = "2026-10-27 12:00:00") -> dict:
    return {"event_id": "e1", "attempt_id": attempt, "source_id": source, "terminal_state": state, "detail": f"{source} {state}",
            "checked_at": when, "response_body": body,
            "evidence_checksum": hashlib.sha256(body).hexdigest() if body is not None else None,
            "parser_contract": brokers.PARSER_CONTRACTS[source] if body is not None else None,
            "request_url": "https://example.invalid/"}


class BrokerTests(unittest.TestCase):
    def test_fixtures_are_the_recorded_responses(self):
        for name, digest in FIXTURE_SHA256.items():
            self.assertEqual(hashlib.sha256(fixture(name)).hexdigest(), digest, name)

    def test_three_brokers_side_by_side_from_real_responses(self):
        rows = [receipt("alerce-classifiers", fixture("alerce-ZTF26abljbah.json")),
                receipt("fink-science-scores", fixture("fink-ZTF26abljbah.json"), attempt="a2"),
                receipt("antares-ztf-loci", fixture("antares-ZTF26abljbah.json"), attempt="a3")]
        block = brokers.build_broker_classes({"name": "SN2026x", "classification": "SN Ia"}, rows)
        by_broker = {entry["broker"]: entry for entry in block["brokers"]}
        alerce = by_broker["ALeRCE"]
        self.assertEqual([c["classifier"] for c in alerce["output"]["classifiers"]], ["stamp_classifier", "stamp_classifier_2025_beta"])
        self.assertEqual(alerce["reading"]["brokerClass"], "bogus")
        self.assertEqual(alerce["reading"]["coarseClass"], "bogus or artefact")
        fink = by_broker["Fink"]
        self.assertEqual(fink["output"]["alerts"], 3)
        self.assertEqual(fink["reading"]["coarseClass"], "SN (type unknown)")  # SN-vs-all 0.706, Ia-vs-non-Ia 0.032
        antares = by_broker["ANTARES"]
        self.assertEqual(antares["output"]["locusId"], "ANT20260s3q9ygj2hn9")
        self.assertIn("extragalactic", antares["output"]["tags"])
        self.assertIsNone(antares["reading"]["coarseClass"])  # tags alone give no reading
        self.assertEqual(block["consensus"]["state"], "disagree")
        self.assertEqual(block["consensus"]["tnsCoarseClass"], "SN Ia")
        self.assertEqual(brokers.broker_problems([{"name": "x", brokers.CANDIDATE_KEY: block}]), [])

    def test_not_found_answers_are_receipts_without_outputs(self):
        rows = [receipt("alerce-classifiers", fixture("alerce-404-ZTF99zzzzzzz.json"), state="no-match"),
                receipt("fink-science-scores", fixture("fink-empty-ZTF99zzzzzzz.json"), state="no-match", attempt="a2"),
                receipt("antares-ztf-loci", fixture("antares-empty-ZTF99zzzzzzz.json"), state="no-match", attempt="a3")]
        block = brokers.build_broker_classes({"name": "x", "classification": None}, rows)
        self.assertTrue(all(entry["currentOutcome"] == "searched-no-match" and "output" not in entry for entry in block["brokers"]))
        self.assertEqual(block["consensus"]["state"], "no-broker-reading")

    def test_the_latest_receipt_is_the_outcome_and_the_latest_data_the_output(self):
        rows = [receipt("fink-science-scores", fixture("fink-ZTF26abljbah.json"), attempt="old", when="2026-10-20 00:00:00"),
                receipt("fink-science-scores", None, state="failed", attempt="new", when="2026-10-27 00:00:00")]
        entry = brokers.build_broker_classes({"name": "x"}, rows)["brokers"][0]
        self.assertEqual(entry["currentOutcome"], "provider-failure")
        self.assertEqual(entry["currentReceipt"]["attemptId"], "new")
        self.assertEqual(entry["dataReceipt"]["attemptId"], "old")

    def test_a_response_that_does_not_match_its_checksum_is_not_published(self):
        row = receipt("alerce-classifiers", fixture("alerce-ZTF26abljbah.json"))
        row["evidence_checksum"] = "0" * 64
        entry = brokers.build_broker_classes({"name": "x"}, [row])["brokers"][0]
        self.assertNotIn("output", entry)
        self.assertIn("checksum", entry["dataReceipt"]["unreadable"])

    def test_agreement_and_the_gate(self):
        lc_rows = json.dumps([{"classifier_name": "lc_classifier", "classifier_version": "hierarchical_rf_1.1.0",
                               "class_name": "SNIa", "probability": 0.6, "ranking": 1}]).encode()
        block = brokers.build_broker_classes({"name": "x", "classification": "SN Ia"}, [receipt("alerce-classifiers", lc_rows)])
        self.assertEqual(block["consensus"]["state"], "agree")
        tampered = copy.deepcopy(block)
        tampered["brokers"][0]["reading"]["coarseClass"] = "SN Ia (averaged)"
        self.assertTrue(brokers.broker_problems([{"name": "x", brokers.CANDIDATE_KEY: tampered}]))

    def test_coarse_vocabulary(self):
        self.assertEqual(brokers.coarse_class("SNIbc"), "SN core-collapse")
        self.assertEqual(brokers.coarse_class("SN IIn"), "SN core-collapse")
        self.assertEqual(brokers.coarse_class("SLSN-I"), "SLSN")
        self.assertEqual(brokers.coarse_class("QSO"), "AGN / QSO / blazar")
        self.assertIsNone(brokers.coarse_class("Unclassified"))


class WiringTests(unittest.TestCase):
    def test_exporter_binds_publishes_and_gates_both_blocks(self):
        source = (ROOT / "scripts/export_ctas_snapshot.py").read_text()
        for path in ("scripts/ctas_lightcurve_science.py", "scripts/ctas_broker_classes.py", "ctas/lightcurve-science.js",
                     "scripts/test_ctas_lightcurve_science.py", "scripts/test_ctas_lightcurve_science.js"):
            self.assertIn(f'"{path}"', source)
        for gate in ("lightcurve-science-provenance", "broker-classes-receipt-trace"):
            self.assertIn(f'gate("{gate}"', source)
        classes = json.loads((ROOT / "scripts/check-classes.json").read_text())["checks"]
        for check in ("ctas.gate.lightcurve-science-provenance", "ctas.gate.broker-classes-receipt-trace",
                      "ctas.test_ctas_lightcurve_science", "ctas.front.lightcurve-science"):
            self.assertIn(check, classes)
        self.assertIn("scripts/test_ctas_lightcurve_science.py", (ROOT / "scripts/publish_ctas.sh").read_text())

    def test_broker_receipts_are_read_while_the_database_is_open(self):
        # A query after the exporter closes its connection marks every event unreadable
        # (found in the 2026-10-27 rehearsal); the receipts must be read with the others.
        source = (ROOT / "scripts/export_ctas_snapshot.py").read_text()
        read = source.index("broker_rows = rows_by_event(cur, ids, broker_classes.broker_rows_sql())")
        close = source.index("    con.close()\n", source.index("def export("))
        self.assertLess(read, close)


class ReleaseTests(unittest.TestCase):
    """Every lightcurve_science and broker_classes block of the generated release, if present."""

    @classmethod
    def setUpClass(cls):
        manifest_path = ROOT / "ctas/data/candidate-chunks/manifest.json"
        if not manifest_path.exists():
            raise unittest.SkipTest("no generated CTAS release under ctas/data")
        from ctas_chunks import decode_chunk
        manifest = json.loads(manifest_path.read_text())
        cls.candidates = []
        for metadata in manifest["chunks"]:
            raw = (ROOT / metadata["path"]).read_bytes()
            cls.candidates.extend(decode_chunk(raw, lambda path: (ROOT / path).read_bytes())["candidates"])
        cls.certificate = json.loads((ROOT / "ctas/data/certification.json").read_text())

    def test_every_published_block_passes_the_gates_and_the_schema(self):
        self.assertEqual(lc.lightcurve_problems(self.candidates), [])
        self.assertEqual(brokers.broker_problems(self.candidates), [])
        gates = {gate["id"]: gate for gate in self.certificate.get("gates", [])}
        for gate in ("lightcurve-science-provenance", "broker-classes-receipt-trace"):
            if gate in gates:
                self.assertTrue(gates[gate]["passed"], gates[gate]["evidence"])
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((ROOT / "ctas/schemas/candidate-chunk.schema.json").read_text())
        for key in (lc.CANDIDATE_KEY, brokers.CANDIDATE_KEY):
            block_schema = dict(schema["$defs"]["candidate"]["properties"][key])
            block_schema["$defs"] = schema["$defs"]
            validator = Draft202012Validator(block_schema)
            for row in self.candidates:
                if key in row:
                    errors = [error.message for error in validator.iter_errors(row[key])]
                    self.assertEqual(errors, [], f"{row.get('name')} {key}")

    def test_release_counts(self):
        counts: dict[str, int] = {}
        consensus: dict[str, int] = {}
        for row in self.candidates:
            for run in (row.get(lc.CANDIDATE_KEY) or {}).get("runs", []):
                if run["status"] == "complete":
                    counts[run["analysisType"]] = counts.get(run["analysisType"], 0) + 1
            state = ((row.get(brokers.CANDIDATE_KEY) or {}).get("consensus") or {}).get("state")
            if state:
                consensus[state] = consensus.get(state, 0) + 1
        with_lc = sum(1 for row in self.candidates if lc.CANDIDATE_KEY in row)
        print(f"release: {with_lc} dossiers with light-curve science, complete runs {dict(sorted(counts.items()))}; "
              f"broker consensus {dict(sorted(consensus.items()))}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

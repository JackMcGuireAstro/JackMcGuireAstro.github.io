#!/usr/bin/env python3
"""Track 3d: multimessenger event pages.

Unit checks of scripts/ctas_multimessenger.py against the collector's real output for the
public S190814bv LALInference sky map (tests/fixtures/ctas/multimessenger-S190814bv.json, made
by running the collector end to end on that map; see its note): the LVK alert fields, the
credible-region MOCs and their revisions, the galaxy ranking, the counterpart registry with
public state and implied absolute magnitude; a neutrino circle in the exact shape of a real
published IceCube notice and the public events inside it; the certificate gate (including
tampered blocks); the wiring into the exporter; and, when a generated release is present under
ctas/data, every published multimessenger block of it.
"""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ctas_multimessenger as mm  # noqa: E402

FIXTURE = json.loads((ROOT / "tests/fixtures/ctas/multimessenger-S190814bv.json").read_text())


def _signal_row(row: dict) -> dict:
    """The collector's messenger_signals row in the shape the exporter publishes."""

    return {**row, "properties": json.loads(row["properties"]), "classification": json.loads(row["classification"]),
            "detector_network": json.loads(row["detector_network"]), "ctas_received_at": row["created_at"],
            "source_published_at": "2019-08-15T21:10:00Z",  # the alert's time_created
            "assertion_id": row["id"]}


def gw_candidate():
    return {"event_id": "gw-1", "name": "S190814bv", "status": "candidate", "ra_deg": None, "dec_deg": None,
            "follow_up": {"messenger_signals": [_signal_row(row) for row in FIXTURE["messenger_signals"]]}}


def gw_index():
    optical = next(row for row in FIXTURE["events"] if row["preferred_name"] == "AT2019illus")
    return {optical["id"]: {"name": "AT2019illus", "status": "candidate", "ra": 12.84, "dec": -24.97,
                            "discovery_time": "2019-08-15T22:00:00Z", "peak_magnitude": 21.0, "prior_activity": False},
            "gw-1": {"name": "S190814bv", "status": "candidate", "ra": None, "dec": None, "discovery_time": None,
                     "peak_magnitude": None, "prior_activity": None}}


def icecube_candidate():
    # the exact field names of the published IceCube-211216B notice (Bronze track, 2026-10 release)
    signal = {"alert_type": "initial", "assertion_id": "5dcc6666-1b3d-4f70-8b77-dcf3a4ac7be9", "messenger": "neutrino",
              "instrument": "IC86 / Bronze Track Alert", "observed_at": "2021-12-16T23:41:13.930000Z",
              "ctas_received_at": "2026-08-04T14:25:36.942916Z", "false_alarm_rate_hz": 7.582325652140846e-08,
              "properties": {"source_ra_deg": 64.95, "source_dec_deg": 15.44, "position_error_90_deg": 0.513333,
                             "containment_probability": 0.9, "canonical_coordinates_withheld": True}}
    return {"event_id": "nu-1", "name": "IceCube-211216B", "status": "candidate", "ra_deg": None, "dec_deg": None,
            "follow_up": {"messenger_signals": [signal]}}


class GravitationalWaveTests(unittest.TestCase):
    def setUp(self):
        self.block = mm.build_multimessenger(gw_candidate(), FIXTURE["analysis_runs"], gw_index())

    def test_lvk_fields_are_first_class(self):
        lvk = self.block["lvk"]
        self.assertIs(lvk["significant"], True)
        self.assertEqual(lvk["significantBasis"], "LVK alert flag")
        self.assertAlmostEqual(lvk["falseAlarmRateHz"], 2.033e-33)
        self.assertEqual(lvk["classification"]["NSBH"], 0.99)
        self.assertEqual(set(lvk["properties"]), set(mm.LVK_PROPERTY_KEYS))
        self.assertIsNone(lvk["properties"]["HasSSM"])  # not in the alert, not defaulted
        self.assertEqual(lvk["instruments"], ["H1", "L1", "V1"])
        self.assertEqual((lvk["group"], lvk["pipeline"]), ("CBC", "LALInference"))
        self.assertIsNone(lvk["chirpMassBins"])
        self.assertFalse(lvk["retracted"])
        self.assertTrue(lvk["gracedbUrl"].endswith("/S190814bv/view/"))

    def test_localization_mocs_from_the_real_map(self):
        loc = self.block["localization"]
        self.assertEqual(loc["status"], "complete")
        areas = loc["result"]["credible_area_sq_deg"]
        self.assertTrue(20 < areas["90"] < 26)  # published 23 deg^2
        regions = loc["result"]["regions"]
        self.assertEqual([r["credible_level"] for r in regions], [0.1, 0.5, 0.9])
        covered = [r["covering_area_sq_deg"] for r in regions]
        self.assertEqual(covered, sorted(covered))
        self.assertTrue(all(r["moc_ascii"] and r["moc_json"] for r in regions))
        self.assertTrue(loc["result"]["space_time_moc"]["stmoc_ascii"].startswith("t"))
        self.assertEqual(len(loc["revisions"]), 1)
        self.assertEqual(len(loc["inputs"]["skymap_checksum_sha256"]), 64)
        self.assertGreater(loc["result"]["map_distance_mean_mpc"], 200)

    def test_galaxy_list_names_its_incomplete_catalogue(self):
        gal = self.block["galaxies"]
        self.assertEqual(gal["status"], "complete")
        self.assertEqual(gal["result"]["catalogue"]["name"], "CTAS retained NED galaxies")
        self.assertFalse(gal["result"]["catalogue"]["complete_subset"])
        self.assertTrue(any("far from a complete catalogue" in w for w in gal["warnings"]))

    def test_registry_joins_public_state_and_implied_magnitude(self):
        reg = self.block["counterparts"]
        self.assertEqual(reg["status"], "complete")
        row = reg["result"]["rows"][0]
        self.assertEqual(row["name"], "AT2019illus")
        self.assertEqual(row["state"], "public")
        self.assertEqual(row["rank2d"], 1)
        # m - 5 log10(d / 10 pc) at the map's mean distance (~267 Mpc): 21 - 37.1
        self.assertAlmostEqual(row["impliedAbsoluteMagnitude"], 21.0 - 5 * __import__("math").log10(
            self.block["localization"]["result"]["map_distance_mean_mpc"] * 1e5), places=2)
        self.assertIn("token", reg["result"]["treasureMap"])

    def test_gate_passes_and_catches_tampering(self):
        ids = list(gw_index())
        candidates = [dict(gw_candidate(), multimessenger=self.block)] + [{"event_id": i, "name": i} for i in ids if i != "gw-1"]
        self.assertEqual(mm.multimessenger_problems(candidates), [])
        bad = copy.deepcopy(self.block)
        bad["localization"]["result"]["regions"][0]["covering_area_sq_deg"] = 1e6
        self.assertTrue(any("do not nest" in p for p in mm.multimessenger_problems([dict(gw_candidate(), multimessenger=bad)])))
        bad = copy.deepcopy(self.block)
        bad["counterparts"]["inputChecksumSha256"] = "nope"
        self.assertTrue(any("checksum" in p for p in mm.multimessenger_problems([dict(gw_candidate(), multimessenger=bad)])))
        bad = copy.deepcopy(self.block)
        problems = mm.multimessenger_problems([dict(gw_candidate(), multimessenger=bad)])  # registry event missing
        self.assertTrue(any("not an event of this release" in p for p in problems))

    def test_a_retracted_alert_says_so(self):
        candidate = gw_candidate()
        candidate["status"] = "retracted"
        block = mm.build_multimessenger(candidate, FIXTURE["analysis_runs"], gw_index())
        self.assertTrue(block["lvk"]["retracted"])

    def test_no_runs_still_publishes_the_alert_fields(self):
        block = mm.build_multimessenger(gw_candidate(), [], gw_index())
        self.assertIsNone(block["localization"])
        self.assertIsNone(block["galaxies"])
        self.assertEqual(block["counterparts"]["status"], "not-computed")


class CircleTests(unittest.TestCase):
    def index(self):
        return {
            "inside": {"name": "AT2021inside", "status": "candidate", "ra": 65.0, "dec": 15.3,
                       "discovery_time": "2021-12-18T00:00:00Z", "peak_magnitude": 19.5, "prior_activity": None},
            "outside": {"name": "AT2021outside", "status": "candidate", "ra": 70.0, "dec": 15.3,
                        "discovery_time": "2021-12-18T00:00:00Z", "peak_magnitude": 19.5, "prior_activity": None},
            "late": {"name": "AT2022late", "status": "candidate", "ra": 65.0, "dec": 15.4,
                     "discovery_time": "2022-03-01T00:00:00Z", "peak_magnitude": 19.5, "prior_activity": None},
            "nu-1": {"name": "IceCube-211216B", "status": "candidate", "ra": None, "dec": None,
                     "discovery_time": None, "peak_magnitude": None, "prior_activity": None},
            "frb": {"name": "Chime-1177736985", "status": "candidate", "ra": 65.0, "dec": 15.4, "optical": False,
                    "discovery_time": "2021-12-18T00:00:00Z", "peak_magnitude": None, "prior_activity": None},
        }

    def test_neutrino_region_and_its_candidates(self):
        block = mm.build_multimessenger(icecube_candidate(), [], self.index())
        self.assertEqual(block["kind"], "neutrino")
        region = block["region"]
        self.assertEqual((region["centerRaDeg"], region["centerDecDeg"], region["radiusField"]), (64.95, 15.44, "position_error_90_deg"))
        self.assertAlmostEqual(region["areaSqDeg"], 0.8278, places=3)
        self.assertEqual(region["containment"], 0.9)
        rows = block["candidates"]["result"]["rows"]
        self.assertEqual([row["name"] for row in rows], ["AT2021inside"])
        self.assertLess(rows[0]["separationDeg"], region["radiusDeg"])
        self.assertEqual(mm.multimessenger_problems([dict(icecube_candidate(), multimessenger=block),
                                                     {"event_id": "inside", "name": "AT2021inside"}]), [])
        bad = copy.deepcopy(block)
        bad["candidates"]["result"]["rows"][0]["separationDeg"] = 5.0
        self.assertTrue(any("outside the published circle" in p for p in mm.multimessenger_problems(
            [dict(icecube_candidate(), multimessenger=bad), {"event_id": "inside"}])))

    def test_a_chime_ellipse_is_drawn_as_its_circumscribing_circle(self):
        # the exact properties of the published CHIME/FRB notice Chime-1177736985
        signal = {"messenger": "radio", "instrument": "Chime", "assertion_id": "c1", "observed_at": "2021-12-17T00:00:00Z",
                  "properties": {"dm": 63.082386, "ra_dec_error": [0.671257, 0.671257, 0], "snr": 12.2613983}}
        candidate = {"event_id": "frb-1", "name": "Chime-1177736985", "ra_deg": 194.8571803, "dec_deg": 4.2000939,
                     "follow_up": {"messenger_signals": [signal]}}
        block = mm.build_multimessenger(candidate, [], {})
        self.assertEqual(block["kind"], "radio")
        self.assertEqual(block["region"]["radiusDeg"], 0.671257)
        self.assertEqual(block["region"]["ellipse"]["semiMajorDeg"], 0.671257)
        self.assertIn("circumscribing", block["region"]["radiusField"])

    def test_events_without_messenger_signals_have_no_block(self):
        self.assertIsNone(mm.build_multimessenger({"event_id": "x", "follow_up": {}}, [], {}))
        gamma = {"event_id": "g", "follow_up": {"messenger_signals": [{"messenger": "gamma-ray", "properties": {}}]}}
        self.assertIsNone(mm.build_multimessenger(gamma, [], {}))

    def test_the_block_is_deterministic(self):
        first = mm.build_multimessenger(icecube_candidate(), [], self.index())
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(mm.build_multimessenger(icecube_candidate(), [], self.index()), sort_keys=True))


class FrontFixtureTests(unittest.TestCase):
    def test_the_front_end_fixture_is_current(self):
        stored = json.loads((ROOT / "tests/fixtures/ctas/multimessenger-blocks.json").read_text())
        self.assertEqual(stored["gravitationalWave"]["multimessenger"],
                         json.loads(json.dumps(mm.build_multimessenger(gw_candidate(), FIXTURE["analysis_runs"], gw_index()))))
        self.assertEqual(stored["neutrino"]["multimessenger"],
                         json.loads(json.dumps(mm.build_multimessenger(icecube_candidate(), [], CircleTests().index()))))


class WiringTests(unittest.TestCase):
    def test_exporter_binds_publishes_and_gates(self):
        source = (ROOT / "scripts/export_ctas_snapshot.py").read_text()
        for path in ("scripts/ctas_multimessenger.py", "scripts/test_ctas_multimessenger.py", "ctas/multimessenger.js",
                     "scripts/test_ctas_multimessenger.js"):
            self.assertIn(f'"{path}"', source)
        self.assertIn('gate("multimessenger-provenance"', source)
        classes = json.loads((ROOT / "scripts/check-classes.json").read_text())["checks"]
        for check in ("ctas.gate.multimessenger-provenance", "ctas.test_ctas_multimessenger", "ctas.front.multimessenger"):
            self.assertIn(check, classes)
        self.assertIn("scripts/test_ctas_multimessenger.py", (ROOT / "scripts/publish_ctas.sh").read_text())
        run = (ROOT / "scripts/ctas_cloud_run.sh").read_text()
        self.assertIn('SKYMAP_ARTIFACT_DIR="$DATA/skymaps"', run)
        self.assertIn("skymaps", (ROOT / "scripts/ctas_cloud_state.py").read_text())


class ReleaseTests(unittest.TestCase):
    """Every multimessenger block of the generated release, if present."""

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

    def test_every_published_block_passes_the_gate_and_the_schema(self):
        self.assertEqual(mm.multimessenger_problems(self.candidates), [])
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((ROOT / "ctas/schemas/candidate-chunk.schema.json").read_text())
        block_schema = dict(schema["$defs"]["candidate"]["properties"][mm.CANDIDATE_KEY])
        block_schema["$defs"] = schema["$defs"]
        validator = Draft202012Validator(block_schema)
        for row in self.candidates:
            if mm.CANDIDATE_KEY in row:
                errors = [error.message for error in validator.iter_errors(row[mm.CANDIDATE_KEY])]
                self.assertEqual(errors, [], row.get("name"))

    def test_release_counts(self):
        kinds: dict[str, int] = {}
        inside = 0
        for row in self.candidates:
            block = row.get(mm.CANDIDATE_KEY)
            if block:
                kinds[block["kind"]] = kinds.get(block["kind"], 0) + 1
                inside += 1 if ((block.get("candidates") or {}).get("result") or {}).get("rowCount") else 0
        print(f"release: multimessenger blocks {dict(sorted(kinds.items()))}; regions with public optical events inside {inside}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

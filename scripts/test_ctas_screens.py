#!/usr/bin/env python3
"""Track 3a: the collector screens published under ``analysis_screens``.

Unit checks of scripts/ctas_analysis_screens.py (which run is published, what is trimmed,
the badge rule, the GW ranking states, the certificate gate), the wiring into the exporter,
and, when a generated release is present under ctas/data, every published block of it.
No gravitational-wave sky map has been retained by CTAS since the analysis existed (the LVK
observing run ended in November 2025), so the GW cases use a fixture shaped exactly like the
collector's runs (supernova_watch/gw_skymap.py rank_revision and plan_tiles).
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ctas_analysis_screens as screens  # noqa: E402

UUID = "15155e9e-052b-4294-abc4-b48c645ad118"
GW_EVENT = "6f2a9d4c-1b3e-4c5d-8e9f-0a1b2c3d4e5f"
OPTICAL_PUBLIC = "0c346ad0-1111-4222-8333-444455556666"
OPTICAL_RETRACTED = "1a6053f4-1111-4222-8333-444455556666"
OPTICAL_HIDDEN = "2625f7aa-1111-4222-8333-444455556666"


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def run(kind: str, *, when: str, status: str = "complete", result: dict | None = None, parameters: dict | None = None,
        manifest: dict | None = None, rights: str = "public", run_id: str | None = None) -> dict:
    return {
        "id": run_id or f"run-{kind}-{when}", "event_id": UUID, "analysis_type": kind,
        "method_name": {"population-anomaly": "robust-population-feature-deviation",
                        "multiband-light-curve": "shared-shape-multiband-bazin-plus-ab-sed-screen",
                        "gw-counterpart-ranking": "healpix-credible-level-distance-ranking",
                        "gw-tiling-plan": "greedy-healpix-probability-coverage"}.get(kind, "bazin-single-band"),
        "method_version": "1.0.0", "status": status,
        "input_manifest": json.dumps(manifest or {}), "input_checksum": sha(f"in-{kind}-{when}"),
        "parameters": json.dumps(parameters or {}), "software_versions": json.dumps({"numpy": "2.1"}),
        "result": json.dumps(result or {}), "result_checksum": sha(f"out-{kind}-{when}"),
        "quality": json.dumps({"minimum_data_gate_passed": True}), "warnings": json.dumps(["a stated limitation"]),
        "data_rights": rights, "review_state": "machine", "created_at": when, "completed_at": when,
    }


ANOMALY = {"anomaly_available": True, "robust_deviation_score": 2.4, "feature_deviations": [
    {"feature": "discovery_magnitude", "robust_z": 1.1}, {"feature": "observation_count", "robust_z": -3.4},
    {"feature": "log_coordinate_error_arcsec", "robust_z": 0.2}, {"feature": "messenger_count", "robust_z": 2.0}]}
MULTIBAND = {"inference_available": True, "tau_rise_days": 1.46, "tau_fall_days": 15.0, "model_peak_mjd": 61279.94,
             "peak_ab_magnitudes": {"g": 19.3, "r": 19.38}, "model_peak_colors_mag": {"g-r": -0.08},
             "residuals": [{"mjd": 1, "band": "g"}] * 50, "parameter_covariance": {"matrix": [[1]]},
             "sed_screen": {"status": "complete", "temperature_k": 6362.9, "bands": ["g", "r"], "epoch_mjd": 61283.3}}
RANKING = {"ranking_available": True, "candidate_count": 3, "ranking_truncated": False, "rankings": [
    {"event_id": OPTICAL_PUBLIC, "preferred_name": "AT2027aaa", "rank_2d": 1, "rank_3d": 1, "credible_level": 0.31,
     "probability_density_per_sr": 12.5, "cell_probability_mass": 0.02, "distance_likelihood": 0.004, "joint_score": 0.05},
    {"event_id": OPTICAL_RETRACTED, "preferred_name": "AT2027bbb", "rank_2d": 2, "rank_3d": None, "credible_level": 0.62,
     "probability_density_per_sr": 4.1, "cell_probability_mass": 0.01, "distance_likelihood": None, "joint_score": None},
    {"event_id": OPTICAL_HIDDEN, "preferred_name": "internal-name", "rank_2d": 3, "rank_3d": 2, "credible_level": 0.88,
     "probability_density_per_sr": 0.5, "cell_probability_mass": 0.001, "distance_likelihood": 0.001, "joint_score": 0.0005}]}
GW_MANIFEST = {"signal_id": "sig-1", "skymap_revision_id": "rev-1", "skymap_checksum_sha256": sha("map"),
               "candidate_set_checksum_sha256": sha("cands"), "candidates": [{"event_id": OPTICAL_PUBLIC}] * 3}
PUBLIC = {OPTICAL_PUBLIC: {"name": "AT2027aaa", "status": "candidate"},
          OPTICAL_RETRACTED: {"name": "AT2027bbb", "status": "retracted"}}


def tiling(field_id: str, fov: float, when: str) -> dict:
    return run("gw-tiling-plan", when=when, manifest={"signal_id": "sig-1", "skymap_revision_id": "rev-1", "skymap_checksum_sha256": sha("map")},
               parameters={"field_of_view_sq_deg": fov, "max_tiles": 100, "target_probability": 0.9,
                           "field": {"id": field_id, "name": field_id.upper(), "placeholder": True, "basis": "placeholder"}},
               result={"tiling_available": True, "tile_count": 1, "cumulative_probability": 0.03, "target_probability_reached": False,
                       "tiles": [{"rank": 1, "ra_deg": 40.0, "dec_deg": 10.0, "incremental_probability": 0.03, "cumulative_probability": 0.03}]})


class SelectionTests(unittest.TestCase):
    def test_only_the_latest_complete_public_run_of_each_kind_is_published(self):
        rows = [
            run("population-anomaly", when="2026-10-24 00:00:00", run_id="old"),
            run("population-anomaly", when="2026-10-25 00:00:00", run_id="new", result=ANOMALY),
            run("population-anomaly", when="2026-10-26 00:00:00", status="insufficient-data", run_id="later-but-insufficient"),
            run("multiband-light-curve", when="2026-10-26 00:00:00", rights="restricted", run_id="not-public"),
            run("light-curve-inference", when="2026-10-26 00:00:00", run_id="already-in-astro-evidence"),
        ]
        chosen = screens.latest_complete_runs(rows)
        self.assertEqual([row["id"] for row in chosen], ["new"])

    def test_tiling_is_published_once_per_field(self):
        rows = [tiling("wiro", 0.081, "2026-10-25 01:00:00"), tiling("rbo", 0.117, "2026-10-25 01:00:00"),
                tiling("wiro", 0.081, "2026-10-25 02:00:00")]
        chosen = screens.latest_complete_runs(rows)
        self.assertEqual([(row["parameters"], row["completed_at"]) for row in chosen][0][1], "2026-10-25 01:00:00")
        self.assertEqual(sorted(json.loads(row["parameters"])["field"]["id"] for row in chosen), ["rbo", "wiro"])
        self.assertEqual(next(row for row in chosen if '"wiro"' in row["parameters"])["completed_at"], "2026-10-25 02:00:00")

    def test_an_event_without_a_complete_screen_gets_no_key(self):
        self.assertIsNone(screens.build_analysis_screens({"event_id": UUID}, [], PUBLIC))
        self.assertIsNone(screens.build_analysis_screens(
            {"event_id": UUID}, [run("population-anomaly", when="2026-10-25", status="insufficient-data")], PUBLIC))


class ScreenTests(unittest.TestCase):
    def test_every_screen_names_method_version_inputs_checksums_and_boundary(self):
        block = screens.build_analysis_screens({"event_id": UUID, "status": "candidate"}, [
            run("population-anomaly", when="2026-10-25", result=ANOMALY,
                manifest={"event_id": UUID, "population_checksum_sha256": sha("pop"), "population_size": 18020,
                          "target_features": {"event_id": UUID, "discovery_magnitude": 20.83}}),
            run("multiband-light-curve", when="2026-10-25", result=MULTIBAND,
                manifest={"records": [{"provider": "ztf-alerce", "band": "g", "observed_at": "2026-07-24T09:40:55Z"},
                                      {"provider": "rubin-fink", "band": "r", "observed_at": "2026-08-01T00:00:00Z"}]}),
        ], PUBLIC)
        self.assertEqual(block["schema"], "ctas.analysis-screens@1")
        self.assertEqual(screens.screen_problems([{"name": "x", "analysis_screens": block}]), [])
        anomaly, multiband = block["runs"]
        self.assertEqual(anomaly["inputs"], {"populationChecksumSha256": sha("pop"), "populationSize": 18020,
                                             "targetFeatures": {"discovery_magnitude": 20.83}})
        self.assertEqual(multiband["inputs"], {"recordCount": 2, "providers": ["rubin-fink", "ztf-alerce"], "bands": ["g", "r"],
                                               "observedFrom": "2026-07-24T09:40:55Z", "observedTo": "2026-08-01T00:00:00Z"})
        self.assertEqual(multiband["resultChecksumSha256"], sha("out-multiband-light-curve-2026-10-25"))
        self.assertEqual(multiband["omittedResultFields"], ["residuals", "parameter_covariance"])
        self.assertNotIn("residuals", multiband["result"])
        self.assertEqual(multiband["result"]["sed_screen"]["temperature_k"], 6362.9)
        for row in block["runs"]:
            self.assertTrue(row["claimBoundary"])
            self.assertEqual(row["status"], "COMPLETE")
        text = json.dumps(block)
        for banned in ('"created_at"', '"simulation"', '"token"', "/Users/"):
            self.assertNotIn(banned, text)

    def test_the_badge_rule_and_driving_features(self):
        row = screens.build_screen(run("population-anomaly", when="2026-10-25", result=ANOMALY), PUBLIC)
        self.assertTrue(row["badge"]["unusual"])
        self.assertIn("2.0", row["badge"]["rule"])
        self.assertEqual([item["feature"] for item in row["badge"]["drivingFeatures"]],
                         ["observation_count", "messenger_count", "discovery_magnitude"])
        calm = screens.build_screen(run("population-anomaly", when="2026-10-25", result={**ANOMALY, "robust_deviation_score": 0.79}), PUBLIC)
        self.assertFalse(calm["badge"]["unusual"])

    def test_gw_ranking_marks_retracted_and_unpublished_candidates_and_a_retracted_event(self):
        block = screens.build_analysis_screens(
            {"event_id": GW_EVENT, "status": "retracted"},
            [run("gw-counterpart-ranking", when="2027-01-20", result=RANKING, manifest=GW_MANIFEST),
             tiling("wiro", 0.081, "2027-01-20"), tiling("rbo", 0.117, "2027-01-20")], PUBLIC)
        ranking = block["runs"][0]
        self.assertEqual([row["candidateState"] for row in ranking["result"]["rankings"]],
                         ["public", "retracted", "not-in-public-catalog"])
        self.assertEqual(ranking["result"]["rankings"][0]["name"], "AT2027aaa")
        self.assertEqual(ranking["inputs"]["candidateCount"], 3)
        self.assertTrue(all(row["eventRetracted"] for row in block["runs"]))
        self.assertEqual([row["analysisType"] for row in block["runs"]], ["gw-counterpart-ranking", "gw-tiling-plan", "gw-tiling-plan"])
        self.assertEqual(screens.screen_problems([{"name": "S270120", "analysis_screens": block}]), [])

    def test_the_gate_names_what_is_missing(self):
        broken = {"schema": screens.SCHEMA, "runs": [{"analysisType": "population-anomaly", "methodName": "m", "methodVersion": "",
                                                      "inputs": {}, "inputChecksumSha256": "x", "resultChecksumSha256": sha("r"),
                                                      "status": "COMPLETE", "claimBoundary": "b"}]}
        problems = screens.screen_problems([{"name": "AT1", "analysis_screens": broken}])
        self.assertTrue(any("method name or version" in line for line in problems))
        self.assertTrue(any("inputs or input checksum" in line for line in problems))
        self.assertEqual(screens.screen_problems([{"name": "AT2"}]), [])


class WiringTests(unittest.TestCase):
    def test_exporter_binds_the_files_and_declares_the_gate(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("exporter_for_screens", ROOT / "scripts/export_ctas_snapshot.py")
        exporter = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(exporter)
        for path in ("scripts/ctas_analysis_screens.py", "scripts/test_ctas_screens.py", "ctas/screens.js", "scripts/test_ctas_screens.js"):
            self.assertIn(path, exporter.BOUND_CODE_PATHS)
        source = (ROOT / "scripts/export_ctas_snapshot.py").read_text()
        self.assertIn('gate("analysis-screen-provenance"', source)
        self.assertIn("'population-anomaly', 'multiband-light-curve'", source)
        classes = json.loads((ROOT / "scripts/check-classes.json").read_text())["checks"]
        self.assertEqual(classes["ctas.gate.analysis-screen-provenance"]["class"], "integrity")
        self.assertEqual(classes["ctas.test_ctas_screens"]["class"], "integrity")
        page = (ROOT / "ctas.html").read_text()
        self.assertLess(page.index("ctas/screens.js"), page.index("ctas/app.js"))
        schema = json.loads((ROOT / "ctas/schemas/candidate-chunk.schema.json").read_text())
        self.assertIn("analysis_screens", schema["$defs"]["candidate"]["properties"])


class ReleaseTests(unittest.TestCase):
    """Every analysis_screens block of the generated release under ctas/data, if present."""

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
        cls.with_screens = [row for row in cls.candidates if screens.CANDIDATE_KEY in row]
        cls.certificate = json.loads((ROOT / "ctas/data/certification.json").read_text())

    def test_every_published_block_passes_the_gate_and_the_schema(self):
        self.assertEqual(screens.screen_problems(self.candidates), [])
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((ROOT / "ctas/schemas/candidate-chunk.schema.json").read_text())
        # The analysis_screens subschema with its local definitions (the rest of the candidate
        # schema is validated by scripts/validate_ctas_schemas.py).
        block_schema = dict(schema["$defs"]["candidate"]["properties"]["analysis_screens"])
        block_schema["$defs"] = schema["$defs"]
        validator = Draft202012Validator(block_schema)
        for row in self.with_screens:
            errors = [error.message for error in validator.iter_errors(row[screens.CANDIDATE_KEY])]
            self.assertEqual(errors, [], row.get("name"))

    def test_blocks_hold_only_complete_screens_and_the_gate_ran(self):
        for row in self.with_screens:
            kinds = [screen["analysisType"] for screen in row["analysis_screens"]["runs"]]
            self.assertEqual(len(kinds), len(set(kinds)) if "gw-tiling-plan" not in kinds else len(kinds))
            for screen in row["analysis_screens"]["runs"]:
                self.assertEqual(screen["status"], "COMPLETE")
                self.assertNotIn("residuals", screen["result"])
        gates = {gate["id"]: gate for gate in self.certificate.get("gates", [])}
        if "analysis-screen-provenance" in gates:
            self.assertTrue(gates["analysis-screen-provenance"]["passed"], gates["analysis-screen-provenance"]["evidence"])
        counts: dict[str, int] = {}
        for row in self.with_screens:
            for screen in row["analysis_screens"]["runs"]:
                counts[screen["analysisType"]] = counts.get(screen["analysisType"], 0) + 1
        print(f"release: {len(self.with_screens)} dossiers with collector screens {dict(sorted(counts.items()))}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

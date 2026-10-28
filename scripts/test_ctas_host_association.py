#!/usr/bin/env python3
"""Track 3c part 2: host association, population anomaly and alert stamps.

Unit checks of scripts/ctas_host_association.py (candidate extraction and merging across
NED, SIMBAD and Gaia rows in the shape the exporter publishes, the directional light radius,
the posterior with the host-missed hypothesis and the redshift term, prior activity, the
extinction and Gaia point-source flags, the colour-magnitude point, the certificate gate,
determinism) and scripts/ctas_population_anomaly.py (feature rows, the seeded isolation
forest, neighbours, the gate), the wiring into the exporter, the nuclear screen of part 1
reading the probable host, and, when a generated release is present under ctas/data, every
published block and the population file of it.
"""
from __future__ import annotations

import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ctas_host_association as host  # noqa: E402
import ctas_lightcurve_science as lc  # noqa: E402
import ctas_population_anomaly as population  # noqa: E402


def counterpart(catalog, ra, dec, *, record="1", ctype="", flags=None, photometry=None, motion=None, assertion=None):
    return {"assertion_id": assertion or f"{catalog}-{record}", "catalog": catalog, "catalog_record_id": record,
            "ra_deg": ra, "dec_deg": dec, "counterpart_type": ctype, "quality_flags": json.dumps(flags or {}),
            "photometry": json.dumps(photometry or {}), "motion": json.dumps(motion or {}),
            "response_checksum": "0" * 64, "source_url": f"https://example.invalid/{record}"}


def event(rows, *, ra=150.0, dec=2.0, redshift=None, observations=None, discovery="2026-10-01T00:00:00Z", name="SN2026abc"):
    candidate = {"event_id": "e1", "name": name, "ra_deg": ra, "dec_deg": dec, "discovery_time": discovery,
                 "follow_up": {"catalog_counterparts": rows, "observations": observations or []}}
    if redshift is not None:
        candidate["redshift"] = redshift
    return candidate


ARCSEC = 1 / 3600.0


class GeometryTests(unittest.TestCase):
    def test_directional_light_radius_along_and_across_the_major_axis(self):
        # 20" x 10" ellipse at PA 0: along the major axis (PA 0) the radius is 10", across it 5".
        self.assertAlmostEqual(host.directional_light_radius(20, 10, 0, 0), 10.0, places=6)
        self.assertAlmostEqual(host.directional_light_radius(20, 10, 0, 90), 5.0, places=6)

    def test_separation_and_position_angle(self):
        self.assertAlmostEqual(host.separation_arcsec(150, 2, 150, 2 + 10 * ARCSEC), 10.0, places=4)
        self.assertAlmostEqual(host.position_angle_deg(150, 2, 150, 2.001), 0.0, places=3)
        self.assertAlmostEqual(host.position_angle_deg(150, 2, 150.001, 2), 90.0, places=1)


class PosteriorTests(unittest.TestCase):
    def test_candidates_merge_across_catalogues_and_keep_every_row(self):
        rows = [
            counterpart("NEDTAP.objdir", 150.0, 2.0 + 3 * ARCSEC, record="ned1", flags={"preferred_name": "NGC 1", "object_type_code": "G", "redshift": 0.02, "redshift_bibcode": "2000X"}),
            counterpart("simbad.basic", 150.0, 2.0 + 3.5 * ARCSEC, record="NGC 1", ctype="G", flags={"major_axis_arcmin": 0.5, "minor_axis_arcmin": 0.25, "position_angle_deg": 0}),
            counterpart("simbad.basic", 150.0, 2.0, record="star", ctype="*"),
        ]
        galaxies, considered = host.galaxy_candidates(event(rows))
        self.assertEqual(len(galaxies), 1)
        self.assertEqual(galaxies[0]["catalogs"], ["NED", "SIMBAD"])
        self.assertEqual(len(galaxies[0]["assertionIds"]), 2)
        self.assertIsNotNone(galaxies[0]["shape"])
        self.assertEqual(considered, {"NED": 1, "SIMBAD": 1})

    def test_the_close_galaxy_is_probable_and_posteriors_sum_to_one(self):
        rows = [
            counterpart("NEDTAP.objdir", 150.0, 2.0 + 2 * ARCSEC, record="near", flags={"preferred_name": "Near", "object_type_code": "G"}),
            counterpart("NEDTAP.objdir", 150.0, 2.0 + 40 * ARCSEC, record="far", flags={"preferred_name": "Far", "object_type_code": "G"}),
        ]
        run = host.host_posterior_run(event(rows))
        result = run["result"]
        self.assertEqual(result["verdict"], "probable")
        self.assertEqual(result["probableHost"], "Near")
        total = sum(c["posterior"] for c in result["candidates"]) + result["hostMissedPosterior"]
        self.assertAlmostEqual(total, 1.0, places=2)
        self.assertEqual(result["candidates"][0]["offsetBasis"], "angular-scale")
        self.assertIn("angular scale", " ".join(run["warnings"]))

    def test_a_far_lonely_galaxy_leaves_the_host_missed(self):
        rows = [counterpart("NEDTAP.objdir", 150.0, 2.0 + 45 * ARCSEC, record="far", flags={"preferred_name": "Far", "object_type_code": "G"})]
        result = host.host_posterior_run(event(rows))["result"]
        self.assertEqual(result["verdict"], "host-missed-most-probable")
        self.assertIsNone(result["probableHost"])

    def test_the_redshift_term_prefers_the_matching_galaxy(self):
        rows = [
            counterpart("NEDTAP.objdir", 150.0, 2.0 + 3 * ARCSEC, record="a", flags={"preferred_name": "Wrong z", "object_type_code": "G", "redshift": 0.20}),
            counterpart("NEDTAP.objdir", 150.0, 2.0 - 4 * ARCSEC, record="b", flags={"preferred_name": "Right z", "object_type_code": "G", "redshift": 0.051}),
        ]
        result = host.host_posterior_run(event(rows, redshift=0.05))["result"]
        self.assertEqual(result["candidates"][0]["name"], "Right z")
        self.assertEqual(result["candidates"][0]["offsetBasis"], "physical-scale")
        self.assertIsNotNone(result["candidates"][0]["offsetKpc"])

    def test_no_galaxy_is_a_stated_reason(self):
        run = host.host_posterior_run(event([]))
        self.assertEqual(run["status"], "not-computed")
        self.assertIn("no catalogued galaxy", run["notComputedReason"])


class ContextTests(unittest.TestCase):
    def test_prior_activity_counts_only_detections_well_before_discovery(self):
        obs = [{"assertion_id": f"a{i}", "provider": "atlas", "mjd": 61200.0 + i, "detection": 1, "magnitude": 19.0, "magnitude_error": 0.1, "original_band": "o"}
               for i in range(3)]
        obs.append({"assertion_id": "late", "provider": "atlas", "mjd": 61310.0, "detection": 1, "magnitude": 18.0, "magnitude_error": 0.05})
        obs.append({"assertion_id": "noisy", "provider": "atlas", "mjd": 61205.5, "detection": 1, "magnitude": 20.0, "magnitude_error": 0.5})
        run = host.prior_activity_run(event([], observations=obs, discovery="2026-10-01T00:00:00Z"))  # MJD 61314
        self.assertTrue(run["result"]["flag"])
        self.assertEqual(run["result"]["detectionCount"], 3)
        self.assertEqual(run["result"]["preDiscoveryEpochs"], 4)

    def test_prior_activity_without_history_says_why(self):
        obs = [{"assertion_id": "late", "provider": "atlas", "mjd": 61310.0, "detection": 1, "magnitude": 18.0, "magnitude_error": 0.05}]
        run = host.prior_activity_run(event([], observations=obs))
        self.assertEqual(run["status"], "not-computed")
        self.assertIn("30 days before discovery", run["notComputedReason"])

    def test_extinction_and_gaia_point_source(self):
        star = counterpart("I/355/gaiadr3", 150.0, 2.0 + 0.5 * ARCSEC, record="42", flags={"class_probability_star": 0.99, "bp_minus_rp_mag": 1.0, "ruwe": 1.0},
                           photometry={"G": {"magnitude": 15.0}}, motion={"parallax_mas": 2.0, "parallax_error_mas": 0.1})
        run = host.extinction_point_source_run(event([star]), "/maps", lambda ra, dec, d: 0.5)
        self.assertAlmostEqual(run["result"]["ebvRecalibrated"], 0.43, places=5)
        self.assertTrue(run["result"]["highExtinction"])
        self.assertTrue(run["result"]["pointSourceFlag"])
        self.assertIn("Pan-STARRS", run["warnings"][0])
        cmd = host.stellar_counterpart_run(event([star]))
        self.assertAlmostEqual(cmd["result"]["absoluteG"], 15.0 + 5 * math.log10(0.02), places=4)
        self.assertEqual(cmd["result"]["distancePc"], 500.0)

    def test_without_the_map_extinction_is_not_computed(self):
        run = host.extinction_point_source_run(event([]), None, lambda *a: 0.0)
        self.assertEqual(run["status"], "not-computed")

    def test_the_block_needs_something_to_say_and_names_blast(self):
        self.assertIsNone(host.build_host_association(event([]), "/maps", lambda *a: 0.01))
        rows = [counterpart("NEDTAP.objdir", 150.0, 2.0 + 2 * ARCSEC, record="near", flags={"preferred_name": "Near", "object_type_code": "G"})]
        block = host.build_host_association(event(rows, name="SN2026abc"), "/maps", lambda *a: 0.01)
        self.assertEqual(block["externalLinks"][0]["url"], "https://blast.scimma.org/transients/2026abc")
        self.assertEqual(host.host_problems([{**event(rows), "host_association": block}]), [])
        self.assertEqual(block, host.build_host_association(event(rows, name="SN2026abc"), "/maps", lambda *a: 0.01))

    def test_the_gate_catches_a_posterior_that_does_not_trace(self):
        rows = [counterpart("NEDTAP.objdir", 150.0, 2.0 + 2 * ARCSEC, record="near", flags={"preferred_name": "Near", "object_type_code": "G"})]
        candidate = event(rows)
        candidate["host_association"] = host.build_host_association(candidate, None, None)
        broken = copy.deepcopy(candidate)
        broken["follow_up"]["catalog_counterparts"] = []
        self.assertTrue(any("does not trace" in p for p in host.host_problems([broken])))
        tampered = copy.deepcopy(candidate)
        tampered["host_association"]["runs"][0]["result"]["candidates"][0]["posterior"] = 0.1
        self.assertTrue(any("checksum" in p or "sum to" in p for p in host.host_problems([tampered])))


class NuclearScreenTests(unittest.TestCase):
    def test_the_probable_host_offset_drives_the_nuclear_screen(self):
        features = {"halfMaxRiseDays": 10.0, "declineMagPerDay15": 0.02, "bands": {}}
        run = lc.screens_run({"probable_host_offset_arcsec": 0.3}, features, {"inputChecksumSha256": "0" * 64})
        self.assertTrue(run["result"]["nuclearTransient"]["flag"])
        self.assertEqual(run["methodVersion"], lc.SCREENS_VERSION)
        self.assertIn("probable host", run["parameters"]["rules"]["nuclear-transient"])


def feature_candidate(i, rise, duration, decline, g, r):
    features = {"halfMaxRiseDays": rise, "halfMaxDurationDays": duration, "declineMagPerDay15": decline,
                "bands": {"ztfg": {"peakMagnitude": g}, "ztfr": {"peakMagnitude": r}}, "model": "multiband-bazin"}
    run = {"analysisType": "lightcurve-features", "status": "complete", "result": features}
    return {"event_id": f"e{i:03d}", "name": f"AT2026{i:03d}", "classification": "SN Ia",
            "lightcurve_science": {"schema": lc.SCHEMA, "inputs": {"inputChecksumSha256": "0" * 64}, "runs": [run]}}


class PopulationTests(unittest.TestCase):
    def setUp(self):
        try:
            import coniferest  # noqa: F401
        except ImportError:
            self.skipTest("coniferest is not installed")
        self.candidates = []
        for i in range(120):
            x = (i % 12) / 12.0
            self.candidates.append(feature_candidate(i, 8 + 4 * x, 30 + 10 * x, 0.02 + 0.01 * x, 18.5 + 0.1 * x, 18.4))
        self.candidates.append(feature_candidate(999, 0.8, 4.0, 0.4, 18.0, 19.2))  # fast, red
        self.candidates.append({"event_id": "nofeat", "name": "AT2026none"})

    def test_the_odd_one_out_ranks_first_with_drivers_and_neighbours(self):
        doc = population.build(self.candidates, "test")
        self.assertEqual(doc["status"], "complete")
        self.assertEqual(doc["population"], 121)
        self.assertEqual(doc["events"][0]["name"], "AT2026999")
        self.assertEqual(doc["events"][0]["percentile"], 100.0)
        self.assertTrue(doc["events"][0]["drivers"])
        self.assertEqual(len(doc["events"][0]["neighbours"]), population.NEIGHBOURS)
        self.assertEqual(population.problems(doc, self.candidates), [])

    def test_the_document_is_deterministic_and_the_gate_checks_the_table(self):
        doc = population.build(self.candidates, "test")
        self.assertEqual(doc, population.build(self.candidates, "test"))
        changed = copy.deepcopy(self.candidates)
        changed[0]["lightcurve_science"]["runs"][0]["result"]["halfMaxRiseDays"] = 99.0
        self.assertTrue(any("do not match" in p for p in population.problems(doc, changed)))

    def test_a_small_population_is_not_computed(self):
        doc = population.build(self.candidates[:10], "test")
        self.assertEqual(doc["status"], "not-computed")
        self.assertEqual(population.problems(doc, self.candidates[:10]), [])


class WiringTests(unittest.TestCase):
    def test_exporter_binds_publishes_and_gates(self):
        source = (ROOT / "scripts/export_ctas_snapshot.py").read_text()
        for path in ("scripts/ctas_host_association.py", "scripts/ctas_population_anomaly.py", "ctas/host-association.js",
                     "scripts/test_ctas_host_association.py", "scripts/test_ctas_host_association.js"):
            self.assertIn(f'"{path}"', source)
        for gate in ("host-association-provenance", "population-anomaly-provenance"):
            self.assertIn(f'gate("{gate}"', source)
        self.assertIn("POPULATION_ANOMALY_PATH", source)
        classes = json.loads((ROOT / "scripts/check-classes.json").read_text())["checks"]
        for check in ("ctas.gate.host-association-provenance", "ctas.gate.population-anomaly-provenance",
                      "ctas.test_ctas_host_association", "ctas.front.host-association"):
            self.assertIn(check, classes)
        self.assertIn("scripts/test_ctas_host_association.py", (ROOT / "scripts/publish_ctas.sh").read_text())
        self.assertIn("population-anomaly.json", (ROOT / "scripts/publish_ctas.sh").read_text())

    def test_the_relay_serves_stamps_as_capped_images_only(self):
        worker = (ROOT / "relay/worker.js").read_text()
        self.assertIn('host: "avro.alerce.online"', worker)
        self.assertIn('host: "api.ztf.fink-portal.org"', worker)
        self.assertIn("STAMP_MAX_BYTES = 1024 * 1024", worker)


class ReleaseTests(unittest.TestCase):
    """Every host_association block and the population file of the generated release, if present."""

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
        path = ROOT / population.PATH
        cls.population = json.loads(path.read_text()) if path.exists() else None

    def test_every_published_block_passes_the_gate_and_the_schema(self):
        self.assertEqual(host.host_problems(self.candidates), [])
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((ROOT / "ctas/schemas/candidate-chunk.schema.json").read_text())
        block_schema = dict(schema["$defs"]["candidate"]["properties"][host.CANDIDATE_KEY])
        block_schema["$defs"] = schema["$defs"]
        validator = Draft202012Validator(block_schema)
        for row in self.candidates:
            if host.CANDIDATE_KEY in row:
                errors = [error.message for error in validator.iter_errors(row[host.CANDIDATE_KEY])]
                self.assertEqual(errors, [], row.get("name"))

    def test_the_population_file_matches_the_dossiers(self):
        if self.population is None:
            self.skipTest("this release has no population-anomaly.json (built before Track 3c part 2)")
        self.assertEqual(population.problems(self.population, self.candidates), [])
        try:
            from jsonschema import Draft202012Validator
        except ImportError:
            self.skipTest("jsonschema is not installed")
        schema = json.loads((ROOT / "ctas/schemas/population-anomaly.schema.json").read_text())
        self.assertEqual([e.message for e in Draft202012Validator(schema).iter_errors(self.population)][:5], [])

    def test_release_counts(self):
        verdicts: dict[str, int] = {}
        runs: dict[str, int] = {}
        for row in self.candidates:
            for run in (row.get(host.CANDIDATE_KEY) or {}).get("runs", []):
                if run["status"] == "complete":
                    runs[run["analysisType"]] = runs.get(run["analysisType"], 0) + 1
                    if run["analysisType"] == "host-posterior":
                        verdict = run["result"]["verdict"]
                        verdicts[verdict] = verdicts.get(verdict, 0) + 1
        print(f"release: {sum(1 for r in self.candidates if host.CANDIDATE_KEY in r)} dossiers with host association; "
              f"complete runs {dict(sorted(runs.items()))}; verdicts {dict(sorted(verdicts.items()))}; "
              f"population {None if self.population is None else self.population.get('population')}")


if __name__ == "__main__":
    unittest.main(verbosity=2)

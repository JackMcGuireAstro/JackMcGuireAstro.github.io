"""Tests of jmastro (the CTAS/WorldsIndex client) and tom_ctas (the TOM Toolkit broker).

They read the site tree this repository is in, with the data branches overlaid
(bash scripts/overlay_published_data.sh), as the deploy's checks do; without the data they skip.

    python3 -m unittest discover -s tools/python/tests -v
"""
from __future__ import annotations

import gzip
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools" / "python"))

from jmastro import CTAS, WorldsIndex, separation_deg  # noqa: E402
from jmastro import _filters  # noqa: E402
import tom_ctas  # noqa: E402

HAVE_CTAS = (ROOT / "ctas/data/catalog-index.json").exists()
HAVE_WORLDS = (ROOT / "worldsindex/data/catalog-index.json.gz").exists()
try:
    import astropy  # noqa: F401
    HAVE_ASTROPY = True
except ImportError:  # pragma: no cover
    HAVE_ASTROPY = False


class VendoredEngineTests(unittest.TestCase):
    def test_the_package_filter_engine_is_the_scripts_copy(self):
        # tools/python/jmastro/_filters.py is a byte-for-byte copy of scripts/visitor_filters.py,
        # so the package works on its own and cannot drift from the deploy's feeds.
        self.assertEqual((ROOT / "tools/python/jmastro/_filters.py").read_bytes(), (ROOT / "scripts/visitor_filters.py").read_bytes())

    def test_separation(self):
        self.assertAlmostEqual(separation_deg(10, 0, 11, 0), 1.0, places=9)
        self.assertAlmostEqual(separation_deg(0, 89.9, 180, 89.9), 0.2, places=9)


@unittest.skipUnless(HAVE_CTAS, "the CTAS release is not overlaid (bash scripts/overlay_published_data.sh)")
class CTASClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctas = CTAS(ROOT)
        cls.rows = cls.ctas.catalog_rows()
        cls.sample = next(r for r in cls.rows if r.get("name", "").startswith("SN") and isinstance(r.get("ra_deg"), float) and r.get("n_observations", 0) > 5)

    def test_query_object_finds_a_record_by_name_with_and_without_the_prefix(self):
        name = self.sample["name"]
        for query in (name, name.replace("SN", "SN "), name[2:], name.lower()):
            found = self.ctas.query_object(query, as_table=False)
            self.assertIn(self.sample["event_id"], [r["event_id"] for r in found], query)

    def test_query_region_is_nearest_first_and_inside_the_radius(self):
        found = self.ctas.query_region(self.sample["ra_deg"], self.sample["dec_deg"], radius=1.0, as_table=False)
        self.assertEqual(found[0]["event_id"], self.sample["event_id"])
        self.assertTrue(all(r["distance_deg"] <= 1.0 for r in found))
        self.assertEqual([r["distance_deg"] for r in found], sorted(r["distance_deg"] for r in found))

    def test_query_filter_matches_the_engine(self):
        flt = {"schema": _filters.FILTER_SCHEMA, "product": "ctas", "match": "all",
               "clauses": [{"field": "classification", "op": "contains", "value": "SN Ia"}]}
        found = self.ctas.query_filter(flt, as_table=False)
        self.assertTrue(found)
        self.assertTrue(all("sn ia" in r["classification"].lower() for r in found))
        with self.assertRaises(ValueError):
            self.ctas.query_filter({**flt, "product": "worldsindex"})

    def test_get_dossier_returns_the_complete_record_and_photometry(self):
        dossier = self.ctas.get_dossier(self.sample["event_id"])
        self.assertEqual(dossier["event_id"], self.sample["event_id"])
        self.assertIn("follow_up", dossier)
        photometry = self.ctas.get_photometry(self.sample["event_id"], as_table=False)
        self.assertEqual(len(photometry), len(dossier["follow_up"]["observations"]))

    def test_a_multipart_dossier_is_reassembled(self):
        manifest = json.loads((ROOT / "ctas/data/candidate-chunks/manifest.json").read_text())
        roots = [c["path"] for c in manifest["chunks"]
                 if json.loads((ROOT / c["path"]).read_text()).get("schema") == "ctas.candidate-chunk-parts@1.0.0"][:1]
        if not roots:
            self.skipTest("this release has no multipart chunk")
        bucket = roots[0].removeprefix("ctas/data/")
        event = next(r for r in self.rows if r["detail_chunk"] == bucket)
        self.assertEqual(self.ctas.get_dossier(event["event_id"])["event_id"], event["event_id"])

    @unittest.skipUnless(HAVE_ASTROPY, "astropy is not installed")
    def test_datalink_rows_belong_to_the_event(self):
        links = self.ctas.get_datalink(self.sample["event_id"])
        self.assertGreater(len(links), 0)
        self.assertTrue(all(str(x) == self.sample["event_id"] for x in links["ID"]))

    @unittest.skipUnless(HAVE_ASTROPY, "astropy is not installed")
    def test_tables_are_astropy_tables(self):
        from astropy.table import Table
        table = self.ctas.query_region(self.sample["ra_deg"], self.sample["dec_deg"], radius=0.2)
        self.assertIsInstance(table, Table)
        self.assertIn("distance_deg", table.colnames)


@unittest.skipUnless(HAVE_CTAS, "the CTAS release is not overlaid")
class TomBrokerTests(unittest.TestCase):
    def test_query_parameters_become_a_visitor_filter(self):
        flt = tom_ctas.query_parameters_to_filter({"classification": "SN Ia", "min_score": "40", "discovered_within_days": "30"})
        self.assertEqual(_filters.validate_filter(flt), [])
        self.assertEqual([c["field"] for c in flt["clauses"]], ["classification", "ctas_score", "discovery_time"])
        self.assertIsNone(tom_ctas.query_parameters_to_filter({}))

    def test_fetch_alerts_and_generic_alerts(self):
        broker = tom_ctas.CTASBroker(base_url=str(ROOT))
        alerts = list(broker.fetch_alerts({"classification": "SN Ia", "max_results": 25}))
        self.assertTrue(0 < len(alerts) <= 25)
        generic = broker.to_generic_alert(alerts[0])
        self.assertEqual(generic.id, alerts[0]["event_id"])
        self.assertTrue(generic.url.endswith(alerts[0]["event_id"]))
        self.assertIsInstance(generic.ra, float)
        times = [g.timestamp for g in map(broker.to_generic_alert, alerts) if g.timestamp]
        self.assertEqual(times, sorted(times, reverse=True))
        target, extras, aliases = broker.to_target(alerts[0])
        self.assertEqual(target["name"], alerts[0]["name"])
        self.assertEqual(broker.fetch_alert(alerts[0]["event_id"])["event_id"], alerts[0]["event_id"])


@unittest.skipUnless(HAVE_WORLDS, "the WorldsIndex release is not overlaid")
class WorldsIndexClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.worlds = WorldsIndex(ROOT)

    def test_query_object_by_planet_host_and_alias(self):
        found = self.worlds.query_object("HD 209458 b", as_table=False)
        self.assertIn("object-hd-209458-b", [o["objectId"] for o in found])
        self.assertTrue(self.worlds.query_object("hd209458", as_table=False))

    def test_query_region_and_detail(self):
        target = next(o for o in self.worlds.objects() if o["objectId"] == "object-hd-209458-b")
        near = self.worlds.query_region(target["raDeg"], target["decDeg"], radius=0.01, as_table=False)
        self.assertEqual(near[0]["objectId"], "object-hd-209458-b")
        detail = self.worlds.get_detail("object-hd-209458-b")
        self.assertTrue(detail["records"])

    @unittest.skipUnless(HAVE_ASTROPY, "astropy is not installed")
    def test_measurements_belong_to_the_object(self):
        rows = self.worlds.get_measurements("object-hd-209458-b")
        self.assertGreater(len(rows), 10)
        self.assertEqual(set(rows["object_id"]), {"object-hd-209458-b"})


if __name__ == "__main__":
    unittest.main(verbosity=2)

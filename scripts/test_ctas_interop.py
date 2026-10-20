#!/usr/bin/env python3
"""Tests for the Track 1 interoperability exports (scripts/ctas_interop.py).

Needs astropy, astropy_healpix, pyarrow and pandas (what the cloud publisher installs).
The end-to-end class runs the real exporter on tests/fixtures/ctas-identity.db.gz twice
and reads every new file back: ECSV and VOTable cell for cell against the JSON source,
the VOParquet assets with pyarrow and Astropy, a fixture event's DataLink document, the
PROV-JSON provenance, the OpenAPI description and the schema validator.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import warnings
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import ctas_interop as I  # noqa: E402
import export_ctas_snapshot as X  # noqa: E402

from astropy import units as u  # noqa: E402
from astropy.io import votable as astropy_votable  # noqa: E402
from astropy.table import Table  # noqa: E402

FIXTURE = ROOT / "tests/fixtures/ctas-identity.db.gz"
FIXTURE_EVENT = "b0e51ba0-5f53-409a-8b83-a21006c57554"  # AT2026aaft: observations, counterparts, a TNS link


def event(**updates):
    candidate = {
        "event_id": "123e4567-e89b-42d3-a456-426614174000",
        "name": "AT2026abc",
        "ra_deg": 10.0,
        "dec_deg": -20.0,
        "links": [{"source_key": "tns", "label": "TNS", "designation": "AT2026abc", "url": "https://www.wis-tns.org/object/2026abc"}],
        "follow_up": {
            "observations": [
                {"provider": "rubin-fink", "pipeline": "Fink", "band": "g", "mjd": 61285.4, "jd": 2461285.9, "detection": 1,
                 "magnitude": 19.35, "magnitude_error": 0.18, "source_url": "https://api.ztf.fink-portal.org/",
                 "quality_flags": ["fink-tag:valid"], "superseded": 0, "assertion_id": "obs-1", "observed_at": "2026-09-02T09:36:02Z"},
                {"provider": "rubin-fink", "pipeline": "Fink", "band": "i", "mjd": 61285.3, "jd": 2461285.8, "detection": 0,
                 "limiting_magnitude": 19.12, "source_url": "https://api.ztf.fink-portal.org/",
                 "quality_flags": ["fink-tag:upperlim"], "superseded": 0, "assertion_id": "obs-2", "observed_at": "2026-09-02T07:50:39Z"},
            ],
            "messenger_signals": [
                {"provider": "lvk-public-alerts", "alert_type": "INITIAL", "messenger": "gravitational-wave",
                 "skymap_url": "https://gracedb.ligo.org/api/superevents/S260901a/files/bayestar.multiorder.fits",
                 "source_url": "https://gracedb.ligo.org/superevents/S260901a/view/"},
            ],
            "publications": [
                {"provider": "gcn-circulars", "publication_type": "GCN Circular", "title": "S260901a: counterpart search",
                 "canonical_url": "https://gcn.nasa.gov/circulars/41234"},
            ],
            "spectra": [
                {"provider": "wiserep", "file_name": "AT2026abc_spec.fits", "telescope": "NOT", "instrument": "ALFOSC",
                 "observed_at": "2026-09-03T00:00:00Z", "public_download_url": "https://www.wiserep.org/system/files/AT2026abc_spec.fits",
                 "source_url": "https://www.wiserep.org/object/12345"},
            ],
            "archive_products": [
                {"provider": "mast", "mission": "HST", "product_filename": "ibxl01010_drz.fits", "size_bytes": 1234567,
                 "public_download_url": "https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:HST/product/ibxl01010_drz.fits"},
            ],
            "catalog_counterparts": [
                {"provider": "simbad", "catalog": "simbad.basic", "catalog_record_id": "LEDA 941152", "source_url": "https://simbad.cds.unistra.fr/simbad/sim-id?Ident=LEDA+941152"},
            ],
        },
        "source_coverage": [{"source_id": "rubin-lasair", "object_specific_result_url": "https://lasair-ztf.lsst.ac.uk/objects/ZTF26abc/"}],
    }
    candidate.update(updates)
    return candidate


class ColumnSpecTests(unittest.TestCase):
    def test_every_catalog_column_has_a_ucd_and_description(self):
        self.assertEqual([spec.name for spec in I.EVENT_COLUMN_SPECS], list(X.CATALOG_CANDIDATE_COLUMNS))
        for specs in (I.EVENT_COLUMN_SPECS, I.ALIAS_COLUMN_SPECS, I.SOURCE_COLUMN_SPECS, I.SOURCE_ACCOUNTING_COLUMN_SPECS,
                      I.RELEASE_HISTORY_COLUMN_SPECS, I.PHOTOMETRY_COLUMN_SPECS, I.CANDIDATE_BULK_COLUMN_SPECS):
            names = [spec.name for spec in specs]
            self.assertEqual(len(names), len(set(names)))
            for spec in specs:
                with self.subTest(column=spec.name):
                    self.assertTrue(spec.ucd and spec.ucd.split(";")[0].strip())
                    self.assertTrue(spec.description)
                    self.assertIn(spec.dtype, {"str", "float", "int", "bool", "json"})
                    if spec.unit is not None:
                        # The unit string is VOUnits and round-trips through astropy.
                        self.assertEqual(u.Unit(spec.vounit, format="vounit"), spec.unit)

    def test_physical_columns_carry_vounits(self):
        by_name = {spec.name: spec for spec in I.EVENT_COLUMN_SPECS}
        self.assertEqual(by_name["ra_deg"].vounit, "deg")
        self.assertEqual(by_name["coordinate_error_arcsec"].vounit, "arcsec")
        self.assertEqual(by_name["discovery_magnitude"].vounit, "mag")
        photometry = {spec.name: spec for spec in I.PHOTOMETRY_COLUMN_SPECS}
        self.assertEqual(photometry["mjd"].vounit, "d")
        self.assertEqual(photometry["exposure_seconds"].vounit, "s")
        self.assertEqual(photometry["limiting_magnitude"].vounit, "mag")
        self.assertIsNone(photometry["flux"].unit, "flux units vary by provider and live in flux_unit")
        self.assertEqual(photometry["is_upper_limit"].dtype, "bool")
        self.assertEqual(photometry["detection"].dtype, "bool")
        self.assertEqual(photometry[I.HEALPIX_COLUMN].ucd, "pos.healpix")

    def test_tom_specs_follow_the_dynamic_alias_columns(self):
        specs = I.tom_target_column_specs(["name", "type", "ra", "dec", "epoch", "ctas_event_id", "ctas_score", "classification", "primary_messenger", "ctas_url", "source_url", "name2", "name3"])
        self.assertEqual(specs[-1].name, "name3")
        with self.assertRaises(ValueError):
            I.tom_target_column_specs(["name", "surprise"])


class RoundTripTests(unittest.TestCase):
    def rows(self):
        return X.photometry_rows([event()])

    def test_ecsv_and_votable_keep_values_units_ucds_and_limit_flags(self):
        rows = self.rows()
        specs = I.PHOTOMETRY_COLUMN_SPECS
        table = I.build_table(specs, rows, name="ctas_photometry", description="fixture")
        ecsv = I.ecsv_bytes(table)
        votable = I.votable_bytes(table, params={"catalog_content_checksum_sha256": "a" * 64})
        self.assertEqual(I.round_trip_problems("photometry", specs, rows, ecsv=ecsv, votable=votable), [])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            back_ecsv = Table.read(ecsv.decode(), format="ascii.ecsv")
            back_vot = Table.read(io.BytesIO(votable), format="votable")
        names = [spec.name for spec in specs]
        limit_row = next(index for index, row in enumerate(rows) if row[names.index("assertion_id")] == "obs-2")
        detection_row = 1 - limit_row
        for back in (back_ecsv, back_vot):
            self.assertEqual((bool(back["is_upper_limit"][limit_row]), bool(back["is_upper_limit"][detection_row])), (True, False))
            self.assertEqual((bool(back["detection"][limit_row]), bool(back["detection"][detection_row])), (False, True))
            self.assertEqual(str(back["limiting_magnitude"].unit), "mag")
            self.assertTrue(back["magnitude"].mask[limit_row], "a limit has no magnitude; it is masked, not NaN")
            self.assertAlmostEqual(float(back["limiting_magnitude"][limit_row]), 19.12)
            self.assertAlmostEqual(float(back["magnitude"][detection_row]), 19.35)
            self.assertEqual(back["mjd"].meta["ucd"], "time.epoch;obs")
            self.assertEqual(back["mjd"].unit.to_string("vounit"), "d")
            self.assertEqual(json.loads(back["quality_flags"][limit_row]), ["fink-tag:upperlim"])
            self.assertEqual(back["provider"][0], "rubin-fink")
        root = ET.fromstring(votable)
        self.assertEqual(root.get("version"), "1.5")
        fields = root.findall(".//{*}FIELD")
        self.assertEqual([field.get("name") for field in fields], [spec.name for spec in specs])
        self.assertTrue(all(field.get("ucd") for field in fields))
        self.assertEqual({field.get("unit") for field in fields if field.get("name") in {"ra_deg", "dec_deg"}}, {"deg"})
        self.assertEqual([param.get("value") for param in root.findall(".//{*}PARAM")], ["a" * 64])

    def test_round_trip_detects_a_changed_cell(self):
        rows = self.rows()
        specs = I.PHOTOMETRY_COLUMN_SPECS
        table = I.build_table(specs, rows, name="ctas_photometry", description="fixture")
        ecsv = I.ecsv_bytes(table)
        altered = deepcopy(rows)
        altered[0][[spec.name for spec in specs].index("magnitude")] = 18.0
        self.assertTrue(I.round_trip_problems("photometry", specs, altered, ecsv=ecsv))
        names = [spec.name for spec in specs]
        dropped_limit = deepcopy(rows)
        limit_row = next(index for index, row in enumerate(rows) if row[names.index("assertion_id")] == "obs-2")
        dropped_limit[limit_row][names.index("is_upper_limit")] = False
        self.assertTrue(I.round_trip_problems("photometry", specs, dropped_limit, ecsv=ecsv))

    def test_voparquet_metadata_sort_and_row_groups(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        candidates = [
            event(event_id="123e4567-e89b-42d3-a456-426614174000", ra_deg=10.0, dec_deg=-20.0),
            event(event_id="223e4567-e89b-42d3-a456-426614174001", ra_deg=200.0, dec_deg=45.0),
            event(event_id="323e4567-e89b-42d3-a456-426614174002", ra_deg=None, dec_deg=None),
        ]
        rows = X.photometry_rows(candidates)
        specs = I.PHOTOMETRY_COLUMN_SPECS
        pixel_column = [spec.name for spec in specs].index(I.HEALPIX_COLUMN)
        pixels = [row[pixel_column] for row in rows]
        self.assertEqual(pixels[-2:], [None, None], "rows without coordinates sort last")
        self.assertEqual(pixels[:4], sorted(pixels[:4]))
        self.assertEqual(set(pixels[:4]), {I.healpix_index(10.0, -20.0), I.healpix_index(200.0, 45.0)})
        with patch.object(I, "PARQUET_ROW_GROUP_ROWS", 4):
            table = I.build_table(specs, rows, name="ctas_photometry", description="fixture")
            raw = I.voparquet_bytes(table, params={"catalog_content_checksum_sha256": "b" * 64})
            self.assertEqual(I.parquet_problems("photometry", specs, rows, raw), [])
            parquet_file = pq.ParquetFile(pa.BufferReader(raw))
            self.assertEqual(parquet_file.metadata.num_row_groups, 2)
        metadata = parquet_file.schema_arrow.metadata
        self.assertEqual(metadata[b"IVOA.VOTable-Parquet.version"], b"1.0")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            header = astropy_votable.parse(io.BytesIO(metadata[b"IVOA.VOTable-Parquet.content"])).get_first_table()
        self.assertEqual([field.name for field in header.fields], [spec.name for spec in specs])
        self.assertEqual(header.fields[0].ucd, "pos.healpix")
        self.assertEqual(len(header.array), 0)
        arrow = parquet_file.read()
        flags = arrow.column("is_upper_limit").to_pylist()
        magnitudes = arrow.column("magnitude").to_pylist()
        self.assertEqual(flags.count(True), 3)
        self.assertTrue(all(magnitude is None for magnitude, flag in zip(magnitudes, flags) if flag))
        self.assertTrue(all(magnitude is not None for magnitude, flag in zip(magnitudes, flags) if not flag))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "t.parquet"
            path.write_bytes(raw)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                astropy_plain = Table.read(str(path), format="parquet")
                astropy_vo = Table.read(str(path), format="parquet.votable")
        self.assertEqual(len(astropy_plain), len(rows))
        self.assertEqual(list(astropy_vo["mjd"].meta.get("ucd") and [astropy_vo["mjd"].meta["ucd"]] or []), ["time.epoch;obs"])
        self.assertEqual(astropy_vo["mjd"].unit.to_string("vounit"), "d")
        self.assertEqual(len(astropy_vo), len(rows))


class DataLinkTests(unittest.TestCase):
    def test_rows_cover_every_retained_link_with_core_semantics(self):
        rows = I.datalink_rows_for(event(), "0ab")
        by_semantics = {}
        for row in rows:
            by_semantics.setdefault(row["semantics"], []).append(row)
        self.assertEqual(set(by_semantics), {"#this", "#documentation", "#progenitor", "#auxiliary", "#derivation"})
        self.assertEqual(by_semantics["#this"][0]["access_url"], f"{I.SITE_BASE_URL}/ctas/data/candidate-chunks/0ab.json.gz")
        self.assertEqual(
            {row["access_url"] for row in by_semantics["#derivation"]},
            {f"{I.SITE_BASE_URL}/{path}" for path, _media, _label in I.DATALINK_DERIVATION_TABLES},
        )
        self.assertIn(f"{I.SITE_BASE_URL}/ctas/data/research/candidates.vot", {row["access_url"] for row in by_semantics["#derivation"]})
        urls = {row["access_url"] for row in rows}
        self.assertIn("https://www.wis-tns.org/object/2026abc", urls)
        self.assertIn("https://gracedb.ligo.org/api/superevents/S260901a/files/bayestar.multiorder.fits", urls)
        self.assertIn("https://gcn.nasa.gov/circulars/41234", urls)
        self.assertIn("https://www.wiserep.org/system/files/AT2026abc_spec.fits", urls)
        self.assertIn("https://lasair-ztf.lsst.ac.uk/objects/ZTF26abc/", urls)
        skymap = next(row for row in rows if row["access_url"].endswith(".fits") and "gracedb" in row["access_url"])
        self.assertEqual((skymap["semantics"], skymap["content_type"]), ("#progenitor", "application/fits"))
        archive = next(row for row in rows if "mast.stsci.edu" in row["access_url"])
        self.assertEqual((archive["semantics"], archive["content_length"]), ("#auxiliary", 1234567))
        self.assertEqual(len({(row["access_url"], row["semantics"]) for row in rows}), len(rows), "no duplicate links")
        self.assertTrue(all(row["ID"] == event()["event_id"] for row in rows))
        self.assertTrue(all(row["access_url"].startswith("https://") for row in rows))

    def test_document_parses_as_a_datalink_votable_and_is_byte_identical_for_unchanged_input(self):
        rows = I.datalink_rows_for(event(), "0ab")
        raw = I.datalink_document(rows, "0ab")
        self.assertEqual(I.datalink_problems(raw), [])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            table = astropy_votable.parse(io.BytesIO(raw)).get_first_table()
        self.assertEqual([field.name for field in table.fields], [column[0] for column in I.DATALINK_COLUMNS])
        self.assertEqual(len(table.array), len(rows))
        self.assertEqual(ET.fromstring(raw).get("version"), "1.5")
        again = I.datalink_document(I.datalink_rows_for(deepcopy(event()), "0ab"), "0ab")
        self.assertEqual(raw, again)
        documents, manifest, manifest_raw = I.datalink_artifacts({"0ab": [event()], "0ac": []}, "c" * 64)
        self.assertEqual(set(documents), {"ctas/data/links/0ab.xml"})
        self.assertEqual(manifest["event_count"], 1)
        self.assertEqual(manifest["documents"][0]["sha256"], hashlib.sha256(documents["ctas/data/links/0ab.xml"]).hexdigest())
        self.assertNotIn(b"c" * 64, documents["ctas/data/links/0ab.xml"], "no release checksum inside a per-bucket document")

    def test_problems_name_a_foreign_semantics_term(self):
        rows = I.datalink_rows_for(event(), "0ab")
        rows[0]["semantics"] = "#made-up"
        self.assertTrue(I.datalink_problems(I.datalink_document(rows, "0ab")))


class ProvenanceTests(unittest.TestCase):
    def build(self, generated_at="2026-10-19T12:00:00Z"):
        universe = {
            "definition_checksum_sha256": "d" * 64, "contract_set_checksum_sha256": "e" * 64,
            "sources": [{"source_key": "tns", "name": "TNS", "operational_state": "active-returning-data", "last_attempt_at": "2026-10-19T11:00:00Z"}],
        }
        candidate = event(astro_evidence={"persistedQueryReceipts": [
            {"sourceContractId": "tns", "parserVersion": "tns-csv@2", "schemaVersion": "1", "startedAt": "2026-10-19T10:00:00Z", "completedAt": "2026-10-19T10:00:05Z"},
        ]})
        return I.provenance_document(
            generated_at=generated_at, catalog_content_checksum_sha256="f" * 64, publication_state_checksum_sha256="0" * 64,
            candidate_count=1, source_universe=universe, candidates=[candidate],
            database={"sha256": "1" * 64, "bytes": 10, "event_count": 1, "latest_record_update": "2026-10-19T09:00:00Z"},
            code_hashes={"scripts/export_ctas_snapshot.py": "2" * 64, "scripts/ctas_interop.py": "3" * 64},
            collector={"commit": "abc123", "run_id": None, "run_url": None, "workflow": None},
            artifacts={"ctas/data/status.json": {"sha256": "4" * 64, "bytes": 5}},
        )

    def test_document_is_valid_prov_json_and_names_sources_times_versions_and_code(self):
        document = self.build()
        self.assertEqual(I.provenance_problems(document, catalog_content_checksum_sha256="f" * 64, source_keys={"tns"}), [])
        source = document["entity"]["ctas:source/tns"]
        self.assertEqual(source["ctas:parser_versions"], ["parserVersion=tns-csv@2", "schemaVersion=1"])
        self.assertEqual(source["ctas:last_attempt_at"], "2026-10-19T11:00:00Z")
        self.assertEqual(source["ctas:latest_receipt_in_release"], "2026-10-19T10:00:05Z")
        self.assertEqual(document["entity"]["ctas:code/scripts/export_ctas_snapshot.py"]["ctas:sha256"], "2" * 64)
        self.assertEqual(document["agent"]["ctas:agent/collector"]["ctas:commit"], "abc123")
        self.assertEqual(document["prefix"]["prov"], "http://www.w3.org/ns/prov#")
        self.assertTrue(I.provenance_problems(document, catalog_content_checksum_sha256="f" * 64, source_keys={"tns", "gcn"}))

    def test_state_asset_site_commit_and_versions_are_named_when_given(self):
        universe = {"definition_checksum_sha256": "d" * 64, "contract_set_checksum_sha256": "e" * 64, "sources": []}
        document = I.provenance_document(
            generated_at="2026-10-19T12:00:00Z", catalog_content_checksum_sha256="f" * 64, publication_state_checksum_sha256="0" * 64,
            candidate_count=0, source_universe=universe, candidates=[],
            database={"sha256": "1" * 64, "bytes": 10}, code_hashes={"scripts/export_ctas_snapshot.py": "2" * 64},
            collector={"commit": "abc123"}, artifacts={},
            site={"commit": "5" * 40, "origin_main_commit": "5" * 40, "release_base_commit": "6" * 40, "repository": "owner/repo"},
            state_asset={"asset": "ctas-state-20261019T1930Z.tar.zst", "saved_at": "2026-10-19T19:30:00Z", "restored_at": "2026-10-19T19:35:00Z"},
            versions={"exporter_schema_version": 2, "certificate_schema": "ctas.static-snapshot-verification@1.1.0"},
        )
        self.assertEqual(I.provenance_problems(document, catalog_content_checksum_sha256="f" * 64, source_keys=set()), [])
        state = document["entity"]["ctas:state-asset/ctas-state-20261019T1930Z.tar.zst"]
        self.assertEqual((state["prov:type"], state["ctas:saved_at"]), ("ctas:StateAsset", "2026-10-19T19:30:00Z"))
        export = document["activity"]["ctas:activity/export"]
        self.assertEqual(export["ctas:site_commit"], "5" * 40)
        self.assertEqual(export["ctas:exporter_schema_version"], 2)
        self.assertEqual(document["agent"]["ctas:agent/exporter"]["ctas:site_commit"], "5" * 40)
        self.assertTrue(any(
            row.get("prov:generatedEntity", "").startswith("ctas:database-snapshot/") and row.get("prov:usedEntity", "").startswith("ctas:state-asset/")
            for row in document["wasDerivedFrom"].values()
        ))
        # The exporter reads the state asset from the cloud-run marker; without one there is none.
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "cloud-run.json"
            marker.write_text(json.dumps({"schema": "cloud-run-marker@1", "workflow": "ctas-cloud.yml", "state_asset": "ctas-state-x.tar.zst",
                                          "state_saved_at": "2026-10-19T19:30:00Z", "state_restored_at": None, "run_id": "7", "run_url": None}))
            self.assertEqual(X.restored_state_asset(str(marker))["asset"], "ctas-state-x.tar.zst")
            marker.write_text(json.dumps({"schema": "cloud-run-marker@1", "workflow": "ctas-cloud.yml", "state_asset": None}))
            self.assertIsNone(X.restored_state_asset(str(marker)))
        self.assertIsNone(X.restored_state_asset(None))
        self.assertIsNone(X.restored_state_asset("/nonexistent/marker.json"))

    def test_deterministic_apart_from_times(self):
        first = json.dumps(self.build("2026-10-19T12:00:00Z"), sort_keys=True)
        second = json.dumps(self.build("2026-10-19T12:30:00Z"), sort_keys=True)
        self.assertEqual(first.replace("2026-10-19T12:00:00Z", "T"), second.replace("2026-10-19T12:30:00Z", "T"))
        self.assertEqual(json.dumps(self.build(), sort_keys=True), json.dumps(self.build(), sort_keys=True))


class ApiDescriptionTests(unittest.TestCase):
    def test_openapi_names_every_json_endpoint_and_every_schema_exists(self):
        import yaml

        document = yaml.safe_load((ROOT / "ctas/openapi.yaml").read_text())
        self.assertTrue(str(document["openapi"]).startswith("3.1."))
        paths = document["paths"]
        for required in (
            "/ctas/data/status.json", "/ctas/data/live-summary.json", "/ctas/data/catalog-index.json",
            "/ctas/data/catalog-pages/{page}.json", "/ctas/data/candidate-chunks/{bucket}.json", "/ctas/data/alias-index.json",
            "/ctas/data/source-universe.json", "/ctas/data/research/manifest.json", "/ctas/data/release-history.json",
            "/ctas/data/link-health.json", "/ctas/data/certification.json", "/ctas/data/feed.xml",
            "/ctas/data/bulk-downloads.json", "/ctas/data/links/{bucket}.xml", "/ctas/data/provenance.json", "/ctas/delivery.json",
        ):
            self.assertIn(required, paths)
        referenced = set()
        for path, item in paths.items():
            for method, operation in item.items():
                if method not in {"get"}:
                    continue
                for response in operation["responses"].values():
                    for media in (response.get("content") or {}).values():
                        schema = media.get("schema") or {}
                        if "$ref" in schema:
                            target = (ROOT / "ctas" / schema["$ref"]).resolve()
                            self.assertTrue(target.exists(), f"{path}: {schema['$ref']} is missing")
                            referenced.add(target.name)
        import validate_ctas_schemas as validator

        expected = (
            {f"{name}.schema.json" for name in validator.FIXED_FILES.values()}
            | {f"{name}.schema.json" for name in validator.OPTIONAL_FILES.values()}
            | {f"{schema}.schema.json" for _key, schema in validator.DECLARED_FILES.values()}
            | {f"{name}.schema.json" for name in validator.FIXED_INPUT_FILES.values()}
        )
        self.assertTrue(expected <= referenced, expected - referenced)
        self.assertIn("candidate-json-part.schema.json", referenced)
        for path in X.API_DESCRIPTION_PATHS:
            self.assertTrue((ROOT / path).exists(), path)
            self.assertIn(path, X.BOUND_CODE_PATHS)
        schema_files = {path.name for path in (ROOT / "ctas/schemas").glob("*.schema.json")}
        self.assertEqual(schema_files, {Path(path).name for path in X.API_DESCRIPTION_PATHS if path.endswith(".schema.json")})

    def test_common_schema_pins_the_catalog_columns_and_banned_keys(self):
        common = json.loads((ROOT / "ctas/schemas/common.schema.json").read_text())
        self.assertEqual(common["$defs"]["candidateColumns"]["const"], list(X.CATALOG_CANDIDATE_COLUMNS))
        banned = {entry["required"][0] for entry in common["$defs"]["noBannedKeys"]["not"]["anyOf"]}
        self.assertEqual(banned, set(X.RECURSIVE_BANNED_KEYS))
        chunk = json.loads((ROOT / "ctas/schemas/candidate-chunk.schema.json").read_text())
        evidence = chunk["$defs"]["candidate"]["properties"]["astro_evidence"]
        self.assertFalse(evidence["additionalProperties"])
        # The dossier schema reuses the frozen, certificate-bound AstroEvidence schema by a
        # relative reference (ctas/schemas/ -> ctas/schema/), so check-jsonschema needs no network.
        self.assertTrue(evidence["properties"]["persistedQueryReceipts"]["items"]["$ref"].startswith("../schema/astro-evidence-core-0.1.0.schema.json#"))
        self.assertTrue((ROOT / "ctas/schema/astro-evidence-core-0.1.0.schema.json").exists())


class ColumnRegistryTests(unittest.TestCase):
    def test_export_refuses_a_table_whose_column_lacks_metadata(self):
        table_set = {"candidates": {"specs": I.EVENT_COLUMN_SPECS, "rows": [[None] * len(I.EVENT_COLUMN_SPECS)]}}
        self.assertEqual(X.interop_column_registry_problems(table_set), [])
        short = {"candidates": {"specs": I.EVENT_COLUMN_SPECS[:-1], "rows": [[None] * (len(I.EVENT_COLUMN_SPECS) - 1)]}}
        problems = X.interop_column_registry_problems(short)
        self.assertTrue(problems and "CATALOG_CANDIDATE_COLUMNS" in problems[0], problems)
        blank = I.ColumnSpec("x", "str", "", None, "")
        with_blank = {"candidates": {"specs": I.EVENT_COLUMN_SPECS, "rows": []}, "extra": {"specs": (blank,), "rows": [["a", "b"]]}}
        problems = X.interop_column_registry_problems(with_blank)
        self.assertTrue(any("lacks a UCD" in problem for problem in problems), problems)
        self.assertTrue(any("registry and rows disagree" in problem for problem in problems), problems)


class RepositoryMetadataTests(unittest.TestCase):
    def test_citation_and_codemeta_name_the_author_without_doi_or_orcid(self):
        import yaml

        citation = yaml.safe_load((ROOT / "CITATION.cff").read_text(encoding="utf-8"))
        self.assertEqual(citation["cff-version"], "1.2.0")
        self.assertEqual(citation["authors"][0]["family-names"], "McGuire")
        self.assertEqual(citation["authors"][0]["given-names"], "Jack")
        self.assertNotIn("orcid", citation["authors"][0])
        self.assertNotIn("doi", citation)
        self.assertNotIn("identifiers", citation)
        self.assertEqual(citation["repository-code"], "https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io")
        codemeta = json.loads((ROOT / "codemeta.json").read_text(encoding="utf-8"))
        self.assertEqual(codemeta["@type"], "SoftwareSourceCode")
        self.assertEqual(codemeta["author"][0]["familyName"], "McGuire")
        self.assertNotIn("identifier", codemeta)
        self.assertEqual(codemeta["codeRepository"], citation["repository-code"])
        # Repository metadata, not release data: neither file is certificate-bound.
        self.assertNotIn("CITATION.cff", X.BOUND_CODE_PATHS)
        self.assertNotIn("codemeta.json", X.BOUND_CODE_PATHS)


class FixtureExportTests(unittest.TestCase):
    """The real exporter on the fixture database, twice, so unchanged products can be compared."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        base = Path(cls.temporary.name)
        cls.database = base / "fixture.db"
        with gzip.open(FIXTURE, "rb") as source, cls.database.open("wb") as target:
            shutil.copyfileobj(source, target)
        cls.runs = []
        for index in (1, 2):
            out = base / f"out{index}"
            bulk = base / f"bulk{index}"
            result = subprocess.run(
                [sys.executable, str(ROOT / "scripts/export_ctas_snapshot.py"), "--database", str(cls.database),
                 "--output-dir", str(out), "--bulk-dir", str(bulk)],
                cwd=ROOT, capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise AssertionError(f"exporter failed on the fixture:\n{result.stdout[-2000:]}\n{result.stderr[-4000:]}")
            cls.runs.append((out, bulk, result.stdout))
        cls.out, cls.bulk, cls.stdout = cls.runs[0]
        cls.status = json.loads((cls.out / "status.json").read_text())
        cls.certificate = json.loads((cls.out / "certification.json").read_text())
        cls.index = json.loads((cls.out / "catalog-index.json").read_text())
        cls.research = json.loads((cls.out / "research/manifest.json").read_text())

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def read_candidates(self, out):
        manifest = json.loads((out / "candidate-chunks/manifest.json").read_text())
        candidates = {}
        for row in manifest["chunks"]:
            raw = (out / Path(row["path"]).relative_to("ctas/data")).read_bytes()
            for candidate in X.decode_chunk(raw, lambda path: (out / Path(path).relative_to("ctas/data")).read_bytes())["candidates"]:
                candidates[candidate["event_id"]] = candidate
        return candidates

    def test_gate_count_grew_and_the_interoperability_gate_passed(self):
        gates = {gate["id"]: gate for gate in self.certificate["gates"]}
        self.assertGreaterEqual(len(gates), 43)
        self.assertIn(X.INTEROP_GATE_ID, gates)
        self.assertTrue(gates[X.INTEROP_GATE_ID]["passed"], gates[X.INTEROP_GATE_ID]["evidence"])
        failed = {gate_id for gate_id, gate in gates.items() if not gate["passed"]}
        # Only the gates that depend on the link checker and a committed working tree may fail here.
        self.assertTrue(failed <= {X.REQUIRED_ARTIFACTS_GATE_ID, X.LINK_HEALTH_GATE_ID, "deployed-code-binding", "local-origin-code-alignment"}, failed)
        self.assertEqual(self.status["static_snapshot_verification"]["check_count"], len(gates))
        self.assertEqual(self.status["provenance_path"], X.PROVENANCE_PATH)
        self.assertEqual(self.status["artifacts"]["provenance"]["sha256"], hashlib.sha256((self.out / "provenance.json").read_bytes()).hexdigest())
        for path in X.REQUIRED_PUBLIC_ARTIFACTS:
            if path.startswith("ctas/data/") and path != X.LINK_HEALTH_PATH and path not in X.BOUND_CODE_PATHS:
                self.assertTrue((self.out / Path(path).relative_to("ctas/data")).exists(), path)

    def test_every_research_table_has_an_ecsv_and_votable_that_round_trip_from_the_json_source(self):
        tables = {Path(row["path"]).name: row for row in self.research["tables"]}
        for table in X.RESEARCH_INTEROP_TABLES:
            self.assertIn(f"{table}.ecsv", tables)
            self.assertIn(f"{table}.vot", tables)
            self.assertTrue(all(column["ucd"] and column["description"] for column in tables[f"{table}.vot"]["columns"]))
        self.assertEqual(self.research["interoperability"]["round_trip_problem_count"], 0)
        for name, row in tables.items():
            raw = (self.out / "research" / name).read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (row["bytes"], row["sha256"]), name)
            self.assertIn(row["path"], self.certificate["files"])
        universe = json.loads((self.out / "source-universe.json").read_text())
        aliases = json.loads((self.out / "alias-index.json").read_text())
        history = json.loads((self.out / "release-history.json").read_text())
        candidates = self.read_candidates(self.out)
        ordered = [candidates[row[0]] for row in self.index["candidate_rows"]]
        expected_rows = {
            "candidates": (I.EVENT_COLUMN_SPECS, self.index["candidate_rows"]),
            "aliases": (I.ALIAS_COLUMN_SPECS, [[row[0], row[1], row[2], row[3]] for row in sorted(
                ([candidate["event_id"], alias.get("source_key"), alias.get("designation"), bool(alias.get("ambiguous"))]
                 for candidate in ordered for alias in candidate.get("designations", []) if alias.get("designation")),
                key=lambda row: 0)]),
            "sources": (I.SOURCE_COLUMN_SPECS, [[source.get(spec.name) for spec in I.SOURCE_COLUMN_SPECS] for source in universe["sources"]]),
            "source-accounting": (I.SOURCE_ACCOUNTING_COLUMN_SPECS, X.source_accounting_rows(ordered)),
            "release-history": (I.RELEASE_HISTORY_COLUMN_SPECS, X.release_history_rows(history)),
        }
        for table, (specs, rows) in expected_rows.items():
            with self.subTest(table=table):
                problems = I.round_trip_problems(
                    table, specs, rows,
                    ecsv=(self.out / f"research/{table}.ecsv").read_bytes(),
                    votable=(self.out / f"research/{table}.vot").read_bytes(),
                )
                self.assertEqual(problems, [])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            back = Table.read(io.BytesIO((self.out / "research/candidates.vot").read_bytes()), format="votable")
        self.assertEqual(len(back), self.index["candidate_count"])
        self.assertEqual(back["ra_deg"].unit.to_string("vounit"), "deg")
        self.assertEqual(back["ra_deg"].meta["ucd"], "pos.eq.ra;meta.main")
        self.assertEqual(set(back["event_id"]), set(candidates))
        self.assertEqual(len(aliases["rows"]), len(expected_rows["aliases"][1]))
        tom = (self.out / "research/tom-targets.ecsv").read_bytes()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tom_table = Table.read(tom.decode(), format="ascii.ecsv")
        self.assertEqual(len(tom_table), tables["tom-targets.csv"]["row_count"])

    def test_voparquet_assets_match_bulk_downloads_and_read_back(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        bulk = json.loads((self.out / "bulk-downloads.json").read_text())
        self.assertEqual(bulk["release_tag"], X.BULK_RELEASE_TAG)
        self.assertEqual({asset["name"] for asset in bulk["assets"]}, {X.BULK_CANDIDATES_ASSET, X.BULK_PHOTOMETRY_ASSET})
        candidates = self.read_candidates(self.out)
        for asset in bulk["assets"]:
            path = self.bulk / asset["name"]
            raw = path.read_bytes()
            self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (asset["bytes"], asset["sha256"]))
            self.assertEqual(asset["download_url"], f"https://github.com/{X.BULK_RELEASE_REPOSITORY}/releases/download/{X.BULK_RELEASE_TAG}/{asset['name']}")
            parquet_file = pq.ParquetFile(pa.BufferReader(raw))
            metadata = parquet_file.schema_arrow.metadata
            self.assertEqual(metadata[b"IVOA.VOTable-Parquet.version"], b"1.0")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                header = astropy_votable.parse(io.BytesIO(metadata[b"IVOA.VOTable-Parquet.content"])).get_first_table()
            self.assertEqual([field.name for field in header.fields], asset["columns"])
            self.assertEqual(parquet_file.metadata.num_rows, asset["row_count"])
            pixels = [value for value in parquet_file.read().column(I.HEALPIX_COLUMN).to_pylist() if value is not None]
            self.assertEqual(pixels, sorted(pixels))
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                astropy_table = Table.read(str(path), format="parquet")
                astropy_vo = Table.read(str(path), format="parquet.votable")
            self.assertEqual(len(astropy_table), asset["row_count"])
            self.assertEqual(astropy_vo["ra_deg"].unit.to_string("vounit"), "deg")
            self.assertEqual(astropy_vo["ra_deg"].meta["ucd"], "pos.eq.ra;meta.main")
        photometry = pq.read_table(str(self.bulk / X.BULK_PHOTOMETRY_ASSET))
        total = sum(len((candidate.get("follow_up") or {}).get("observations", [])) for candidate in candidates.values())
        self.assertEqual(photometry.num_rows, total)
        self.assertGreater(total, 0)
        limits = photometry.filter(pa.compute.equal(photometry.column("is_upper_limit"), True))
        self.assertGreater(limits.num_rows, 0)
        self.assertTrue(all(value is None for value in limits.column("magnitude").to_pylist()))
        self.assertTrue(all(value is not None for value in limits.column("limiting_magnitude").to_pylist()))
        self.assertEqual(I.parquet_problems("candidates", I.CANDIDATE_BULK_COLUMN_SPECS,
                                            I.sort_rows_by_healpix([[I.healpix_index(row[5], row[6]), *row] for row in self.index["candidate_rows"]], I.CANDIDATE_BULK_COLUMN_SPECS, ("event_id",)),
                                            (self.bulk / X.BULK_CANDIDATES_ASSET).read_bytes()), [])
        self.assertFalse(list(self.out.glob("**/*.parquet")), "Parquet never enters the Pages tree")

    def test_fixture_event_has_a_datalink_document(self):
        manifest = json.loads((self.out / "links/manifest.json").read_text())
        self.assertEqual(manifest["event_count"], self.index["candidate_count"])
        bucket = X.candidate_bucket(FIXTURE_EVENT)
        entry = next(row for row in manifest["documents"] if row["bucket"] == bucket)
        raw = (self.out / f"links/{bucket}.xml").read_bytes()
        self.assertEqual((len(raw), hashlib.sha256(raw).hexdigest()), (entry["bytes"], entry["sha256"]))
        self.assertEqual(I.datalink_problems(raw), [])
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            table = astropy_votable.parse(io.BytesIO(raw)).get_first_table()
        rows = [dict(zip(table.array.dtype.names, (str(value) if value is not None else None for value in record))) for record in table.array]
        mine = [row for row in rows if row["ID"] == FIXTURE_EVENT]
        self.assertTrue(mine)
        semantics = {row["semantics"] for row in mine}
        self.assertIn("#this", semantics)
        self.assertIn("#documentation", semantics)
        self.assertIn("#progenitor", semantics)
        self.assertIn(f"{I.SITE_BASE_URL}/ctas/data/candidate-chunks/{bucket}.json.gz", {row["access_url"] for row in mine})
        self.assertIn("https://www.wis-tns.org/object/2026aaft", {row["access_url"] for row in mine})
        self.assertEqual(sum(row["bucket"] == bucket for row in manifest["documents"]), 1)
        self.assertEqual(self.certificate["files"][f"ctas/data/links/{bucket}.xml"]["sha256"], entry["sha256"])

    def test_unchanged_dossiers_and_datalink_documents_are_byte_identical_between_runs(self):
        first, second = self.runs[0][0], self.runs[1][0]
        for relative in sorted(path.relative_to(first) for path in first.glob("links/*.xml")):
            self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes(), str(relative))
        for relative in sorted(path.relative_to(first) for path in first.glob("candidate-chunks/*.json")):
            if relative.name != "manifest.json":
                self.assertEqual((first / relative).read_bytes(), (second / relative).read_bytes(), str(relative))
        first_prov = json.loads((first / "provenance.json").read_text())
        second_prov = json.loads((second / "provenance.json").read_text())
        for document in (first_prov, second_prov):
            for key in ("prov:endTime",):
                document["activity"]["ctas:activity/export"].pop(key, None)
            document["wasGeneratedBy"]["_:g1"].pop("prov:time", None)
            for name, entity in document["entity"].items():
                if name.startswith("ctas:release/") or name.startswith("ctas:artifact/"):
                    entity.pop("prov:generatedAtTime", None)
                    entity.pop("ctas:sha256", None)
                    entity.pop("ctas:bytes", None)
                    entity.pop("ctas:publication_state_checksum_sha256", None)
        self.assertEqual(first_prov, second_prov, "provenance differs in more than times and release-clock artifacts")

    def test_provenance_validates_and_names_the_database_sources_and_code(self):
        provenance = json.loads((self.out / "provenance.json").read_text())
        universe = json.loads((self.out / "source-universe.json").read_text())
        problems = I.provenance_problems(
            provenance, catalog_content_checksum_sha256=self.index["catalog_content_checksum_sha256"],
            source_keys={row["source_key"] for row in universe["sources"]},
        )
        self.assertEqual(problems, [])
        snapshot = next(value for key, value in provenance["entity"].items() if key.startswith("ctas:database-snapshot/"))
        self.assertEqual(snapshot["ctas:sha256"], I.sha256_file(self.database))
        self.assertEqual(snapshot["ctas:bytes"], self.database.stat().st_size)
        self.assertEqual(provenance["entity"]["ctas:code/scripts/export_ctas_snapshot.py"]["ctas:sha256"],
                         hashlib.sha256((ROOT / "scripts/export_ctas_snapshot.py").read_bytes()).hexdigest())
        tns = provenance["entity"]["ctas:source/tns"]
        self.assertIn("ctas:last_attempt_at", tns)
        self.assertIsInstance(tns["ctas:parser_versions"], list)

    def test_pages_tree_bytes_added_are_reported_and_bounded(self):
        line = next(line for line in self.stdout.splitlines() if line.startswith("interoperability:"))
        added = int(line.split()[1])
        self.assertLess(added, 2 * 1024 * 1024, line)

    def test_schema_validator_accepts_the_release(self):
        import validate_ctas_schemas as validator

        if validator.validator_command() is None:
            self.skipTest("check-jsonschema is not installed")
        shutil.copy(ROOT / "ctas/delivery.json", Path(self.temporary.name) / "delivery.json")
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/validate_ctas_schemas.py"), "--data-dir", str(self.out)],
            cwd=ROOT, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("chunk sampling rule", result.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)

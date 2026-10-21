#!/usr/bin/env python3
"""Round-trip checks of the WorldsIndex interoperability exports against a built release.

Reads worldsindex/data (a release the static builder wrote) with Astropy and pyarrow and
proves, cell for cell, that the ECSV, VOTable and VOParquet files say what the JSON
release says:

* objects: every row against the catalog index and the atlas populations (values, limit
  flags, source record ids), HEALPix indices against astropy_healpix, rows sorted by them;
* measurements: every row joins back to its native detail record by source_record_id and
  source_field with the same value, its limit flag follows the source's own flag, its
  reference is the record's (or the composite value's) reference; a complete count check
  against the field map declared in exports/index.json column descriptions is not
  possible from Python, so the join is checked for every row of a deterministic sample
  of objects plus every row of HD 209458 b and 51 Eri b;
* transit ephemerides, source coverage (with the four denominators) and identity states:
  complete equality with their JSON artifacts;
* VOTable against ECSV: cell for cell, UCD and unit per FIELD against exports/index.json,
  VOTable version 1.5 and an ICRS COOSYS where coordinates exist;
* VOParquet (when the assets are on disk: WORLDSINDEX_RELEASE_ASSETS_DIR, or the
  directory named on the command line): the two VOParquet metadata keys, the VOTable
  header's FIELDs, zstd row groups, cells against the ECSV, sizes and SHA-256 against
  bulk-downloads.json;
* column-level units parse as VOUnits (astropy format="vounit") and every UCD is valid
  against the controlled vocabulary; feed.xml is well-formed Atom with as many entries as
  feed.json has items; provenance.json's artifact digests equal the manifest's.

Usage: test_worldsindex_interop.py [--data worldsindex/data] [--assets DIR]
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import random
import sys
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from astropy import units as u
from astropy.io.votable import parse as parse_votable
from astropy.io.votable.ucd import check_ucd
from astropy.table import Table

ROOT = Path(__file__).resolve().parents[1]
ROW_LEVEL_NON_VOUNITS = {"percent", "ppm", ""}


def read_gzip_json(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def read_ecsv(path: Path) -> Table:
    text = gzip.decompress(path.read_bytes()).decode("utf-8")
    assert text.startswith("# %ECSV 1.0\n# ---\n"), f"{path.name}: not ECSV 1.0"
    return Table.read(text.splitlines(), format="ascii.ecsv")


def read_votable(path: Path):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        document = parse_votable(gzip.open(path), verify="ignore")
    return document


def cells(column) -> list:
    """Python values of a column; None where masked or empty string (strings) / NaN."""
    data = np.asarray(np.ma.getdata(column)).tolist()
    mask = np.ma.getmaskarray(column).tolist()
    out = []
    for value, masked in zip(data, mask):
        if masked:
            out.append(None)
        elif isinstance(value, bytes):
            out.append(value.decode("utf-8") or None)
        elif isinstance(value, str):
            out.append(value or None)
        elif isinstance(value, float) and math.isnan(value):
            out.append(None)
        elif isinstance(value, (np.bool_, bool)):
            out.append(bool(value))
        else:
            out.append(value)
    return out


def units_equal(a, b) -> bool:
    """Same physical unit and scale (within float rounding of the printed scale factor)."""
    a, b = u.Unit(a), u.Unit(b)
    return a.is_equivalent(b) and math.isclose((a / b).decompose().scale, 1.0, rel_tol=1e-6)


def same(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, float) or isinstance(b, float):
        return float(a) == float(b)
    return a == b


def check_metadata(table_name: str, columns: list[dict], ecsv: Table, votable_table) -> None:
    assert ecsv.colnames == [c["name"] for c in columns], f"{table_name}: ECSV columns differ from exports/index.json"
    fields = {field.name: field for field in votable_table.fields}
    assert list(fields) == ecsv.colnames, f"{table_name}: VOTable FIELDs differ from the ECSV columns"
    for column in columns:
        name = column["name"]
        assert ecsv[name].meta.get("ucd") == column["ucd"], f"{table_name}.{name}: ECSV UCD"
        assert fields[name].ucd == column["ucd"], f"{table_name}.{name}: VOTable UCD"
        assert check_ucd(column["ucd"], check_controlled_vocabulary=True), f"{table_name}.{name}: UCD {column['ucd']} is not in the controlled vocabulary"
        unit = column["unit"]
        ecsv_unit = ecsv[name].unit
        if unit:
            parsed = u.Unit(unit, format="vounit")  # raises when the string is not VOUnits
            assert ecsv_unit is not None and units_equal(ecsv_unit, parsed), f"{table_name}.{name}: ECSV unit {ecsv_unit} != {unit}"
            assert fields[name].unit is not None and units_equal(fields[name].unit, parsed), f"{table_name}.{name}: VOTable unit {fields[name].unit} != {unit}"
        else:
            assert ecsv_unit is None or str(ecsv_unit) == "", f"{table_name}.{name}: ECSV unit should be absent"
            assert fields[name].unit is None, f"{table_name}.{name}: VOTable unit should be absent"
        assert (fields[name].description or "") == column["description"], f"{table_name}.{name}: VOTable description"
        assert (ecsv[name].description or "") == column["description"], f"{table_name}.{name}: ECSV description"
        expected_datatype = {"string": {"char", "unicodeChar"}, "float64": {"double"}, "int64": {"long"}, "bool": {"boolean"}}[column["datatype"]]
        assert fields[name].datatype in expected_datatype, f"{table_name}.{name}: VOTable datatype {fields[name].datatype}"


def column_pair_equal(label: str, left, right) -> None:
    """Two (masked) columns hold the same cells: same masks, same values (strings: "" is absent)."""
    kind = np.asarray(np.ma.getdata(left)).dtype.kind
    lm, rm = np.ma.getmaskarray(left), np.ma.getmaskarray(right)
    ld, rd = np.asarray(np.ma.getdata(left)), np.asarray(np.ma.getdata(right))
    if kind in "fiub":
        if kind == "f":
            lm = lm | ~np.isfinite(ld.astype(float))
            rm = rm | ~np.isfinite(rd.astype(float))
        assert np.array_equal(lm, rm), f"{label}: masks differ at rows {np.flatnonzero(lm != rm)[:5].tolist()}"
        assert np.array_equal(ld[~lm], rd[~rm]), f"{label}: values differ"
    else:
        ls = np.where(lm, "", ld.astype(str))
        rs = np.where(rm, "", np.char.decode(rd, "utf-8") if rd.dtype.kind == "S" else rd.astype(str))
        assert np.array_equal(ls, rs), f"{label}: text differs at rows {np.flatnonzero(ls != rs)[:5].tolist()}"


def check_votable_against_ecsv(table_name: str, ecsv: Table, votable_table) -> None:
    array = votable_table.array
    assert len(array) == len(ecsv), f"{table_name}: VOTable has {len(array)} rows, ECSV {len(ecsv)}"
    for name in ecsv.colnames:
        column_pair_equal(f"{table_name}.{name} (VOTable vs ECSV)", ecsv[name], array[name])


def check_sorted_by_healpix(table_name: str, table: Table, tie: str) -> None:
    from astropy_healpix import lonlat_to_healpix
    hp_mask = np.ma.getmaskarray(table["healpix_8_nested"])
    hp = np.asarray(np.ma.getdata(table["healpix_8_nested"]))
    ra_mask = np.ma.getmaskarray(table["ra_deg"]) | np.ma.getmaskarray(table["dec_deg"])
    ra = np.asarray(np.ma.getdata(table["ra_deg"]), dtype=float)
    dec = np.asarray(np.ma.getdata(table["dec_deg"]), dtype=float)
    assert np.array_equal(hp_mask, ra_mask), f"{table_name}: HEALPix present exactly where coordinates are"
    expected = lonlat_to_healpix(ra[~ra_mask] * u.deg, dec[~ra_mask] * u.deg, 256, order="nested")
    assert np.array_equal(hp[~hp_mask], expected), f"{table_name}: HEALPix differs from astropy_healpix at rows {np.flatnonzero(hp[~hp_mask] != expected)[:5].tolist()}"
    keys = [(pixel is None, -1 if pixel is None else pixel, t) for pixel, t in zip(cells(table["healpix_8_nested"]), cells(table[tie]))]
    assert keys == sorted(keys), f"{table_name}: rows are not sorted by HEALPix then {tie}"


def provenance_entities(data: Path) -> dict:
    return json.loads((data / "provenance.json").read_text())["entity"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", default=str(ROOT / "worldsindex" / "data"))
    parser.add_argument("--assets", default=os.environ.get("WORLDSINDEX_RELEASE_ASSETS_DIR"))
    args = parser.parse_args(argv)
    data = Path(args.data)
    manifest = json.loads((data / "manifest.json").read_text())
    if "exports" not in manifest:
        print("note: this release predates the interoperability exports (built by an older builder); nothing to round-trip")
        return 0
    index = json.loads((data / "exports" / "index.json").read_text())
    assert index["schemaVersion"] == "worldsindex-exports.v1"
    assert manifest["exports"]["path"] == "exports/index.json"
    for table_name, entry in index["tables"].items():
        for kind in ("ecsv", "votable"):
            file = entry["files"][kind]
            raw = (data / file["path"]).read_bytes()
            assert len(raw) == file["bytes"] and hashlib.sha256(raw).hexdigest() == file["sha256"], f"{table_name} {kind}: digest"
            assert manifest["artifacts"][file["path"]]["sha256"] == file["sha256"], f"{table_name} {kind}: manifest digest"
        assert manifest["exports"]["tables"][table_name]["rows"] == entry["rows"]

    catalog = read_gzip_json(data / "catalog-index.json.gz")
    atlas = read_gzip_json(data / "sky-detections.json.gz")
    detections = {d["objectId"]: d for d in atlas["detections"]}
    objects_by_id = {o["objectId"]: o for o in catalog["objects"]}
    tables = {}
    for table_name, entry in index["tables"].items():
        ecsv = read_ecsv(data / entry["files"]["ecsv"]["path"])
        document = read_votable(data / entry["files"]["votable"]["path"])
        assert document.version == "1.5", f"{table_name}: VOTable version {document.version}"
        votable_table = document.resources[0].tables[0]
        assert votable_table.name == table_name
        assert len(ecsv) == entry["rows"], f"{table_name}: {len(ecsv)} ECSV rows, index says {entry['rows']}"
        check_metadata(table_name, entry["columns"], ecsv, votable_table)
        if "ra_deg" in ecsv.colnames:
            assert any(c.system == "ICRS" for c in document.resources[0].coordinate_systems), f"{table_name}: no ICRS COOSYS"
        check_votable_against_ecsv(table_name, ecsv, votable_table)
        tables[table_name] = ecsv

    # ---- objects against the catalog index and the atlas populations
    objects = tables["objects"]
    assert len(objects) == len(catalog["objects"]) == manifest["objectCount"]
    check_sorted_by_healpix("objects", objects, "object_id")
    rows = {row["object_id"]: row for row in (dict(zip(objects.colnames, values)) for values in zip(*[cells(objects[c]) for c in objects.colnames]))}
    axes = {"orbital_period_days": "orbitalPeriodDays", "radius_earth": "radiusEarth", "mass_earth": "massEarth", "minimum_mass_earth": "minimumMassEarth", "estimated_mass_earth": "estimatedMassEarth", "eccentricity": "eccentricity", "equilibrium_temperature_k": "equilibriumTemperatureK", "insolation_earth": "insolationEarth"}
    for object_id, catalog_row in objects_by_id.items():
        row = rows[object_id]
        assert row["name"] == catalog_row["name"] and row["status"] == catalog_row["normalizedStatus"] and row["identity_state"] == catalog_row["identityState"], object_id
        assert same(row["ra_deg"], catalog_row["raDeg"]) and same(row["dec_deg"], catalog_row["decDeg"]), object_id
        assert (row["source_ids"] or "").split(" ") == catalog_row["sourceIds"], object_id
        assert same(row["discovery_year"], catalog_row["discoveryYear"]) and row["source_record_count"] == catalog_row["sourceRecordCount"], object_id
        population = detections[object_id].get("population", {})
        for column, key in axes.items():
            value = population.get(key)
            if value is None:
                assert row[column] is None and row[f"{column}_limit"] is None, f"{object_id}.{column}: absent axis"
            else:
                assert same(row[column], value["value"]) and row[f"{column}_limit"] == value["limitType"] and row[f"{column}_source_record_id"] == value["sourceRecordId"] and row[f"{column}_source_id"] == value["sourceId"], f"{object_id}.{column}"
    assert rows["object-51-eri-b"]["mass_earth_limit"] == "LOWER_LIMIT", "a lower limit travels as the source carries it"
    assert rows["object-hd-209458-b"]["detail_record_count"] >= 20

    # ---- identity states and transit ephemerides: complete equality
    identity = tables["identity_states"]
    assert len(identity) == len(catalog["objects"])
    for object_id, state in zip(cells(identity["object_id"]), cells(identity["identity_state"])):
        assert objects_by_id[object_id]["identityState"] == state, object_id
    ephemerides = read_gzip_json(data / "transit-ephemerides.json.gz")
    transit = tables["transit_ephemerides"]
    assert len(transit) == ephemerides["count"]
    check_sorted_by_healpix("transit_ephemerides", transit, "object_id")
    snake = ["object_id", "name", "host_name", "ra_deg", "dec_deg", "status", "period_days", "period_err_days", "t0_bjd", "t0_err_days", "duration_hours", "depth_ppt", "host_mag", "host_mag_band", "time_system", "source_id", "source_table", "source_record_id", "reference", "reference_url", "matched_via"]
    transit_rows = {row["object_id"]: row for row in (dict(zip(transit.colnames, values)) for values in zip(*[cells(transit[c]) for c in transit.colnames]))}
    for raw in ephemerides["rows"]:
        expected = dict(zip(snake, raw))
        row = transit_rows[expected["object_id"]]
        for key, value in expected.items():
            assert same(row[key], value if value != "" else None), f"transit {expected['object_id']}.{key}: {row[key]!r} != {value!r}"

    # ---- source coverage with the four denominators
    coverage = json.loads((data / "source-coverage.json").read_text())
    cov = tables["source_coverage"]
    assert len(cov) == len(coverage["sources"])
    cov_rows = {row["source_id"]: row for row in (dict(zip(cov.colnames, values)) for values in zip(*[cells(cov[c]) for c in cov.colnames]))}
    for source_id, source in coverage["sources"].items():
        row = cov_rows[source_id]
        assert row["coverage_class"] == source["class"] and row["eligible"] == source["eligible"] and row["attached"] == source["attached"] and row["blocked"] == source["blocked"], source_id
        d = source.get("denominators")
        if d:
            assert row["declared"] is True and row["applicable"] == d["applicable"] and row["executed"] == d["executed"] and row["data_bearing"] == d["dataBearing"], source_id
            assert row["objects_executed"] == d["objects"]["executed"] and row["objects_applicable"] == d["objects"]["applicable"], source_id
            assert (row["not_computable"] or None) == d["notComputable"], source_id
        attempt = source.get("lastAttempt")
        assert (row["last_attempt_outcome"] or None) == (attempt["outcome"] if attempt else None), source_id
    if "denominators" in coverage:
        s = coverage["denominators"]["sources"]
        assert s["declared"] == len(coverage["sources"]) and s["executed"] + s["executedUnknown"] + s["notExecuted"] == s["declared"]

    # ---- measurements: join back to the native rows
    measurements = tables["measurements"]
    check_sorted_by_healpix("measurements", measurements, "object_id")
    m_cols = measurements.colnames
    m_cells = {c: cells(measurements[c]) for c in m_cols}
    by_object: dict[str, list[int]] = {}
    for i, object_id in enumerate(m_cells["object_id"]):
        by_object.setdefault(object_id, []).append(i)
    sample = {"object-hd-209458-b", "object-51-eri-b"}
    rng = random.Random(20261019)
    sample.update(rng.sample(sorted(by_object), 300))
    checked = 0
    for object_id in sorted(sample):
        bucket = hashlib.sha256(object_id.encode()).hexdigest()[:2]
        shard = read_gzip_json(data / "details" / f"{bucket}.json.gz")
        records = {record["sourceRecordId"]: record for record in shard[object_id]["records"]}
        for i in by_object.get(object_id, []):
            record = records[m_cells["source_record_id"][i]]
            field = m_cells["source_field"][i]
            native = record["values"][field]
            assert float(native) == m_cells["value"][i], f"{object_id} {field}: {native} != {m_cells['value'][i]}"
            assert record["sourceId"] == m_cells["source_id"][i] and record["recordType"] == m_cells["record_type"][i]
            lim = record["values"].get(f"{field}lim")
            if m_cells["limit_type"][i] is not None and field.startswith("pl_") and f"{field}lim" in record["values"]:
                flag = None if lim in (None, "") else float(lim)
                expected = "NONE" if flag in (None, 0.0) else "LOWER_LIMIT" if flag == 1.0 else "UPPER_LIMIT" if flag == -1.0 else "UNKNOWN_LIMIT"
                assert m_cells["limit_type"][i] == expected, f"{object_id} {field}: limit {lim} -> {m_cells['limit_type'][i]}"
            if record["sourceId"] == "nasa-pscomppars":
                reference = (record.get("references") or {}).get(field)
                assert (m_cells["reference"][i] or None) == ((reference or {}).get("label") or None), f"{object_id} {field}: composite reference"
            elif record.get("reference"):
                assert m_cells["reference"][i] == (record["reference"].get("label") or None) and (m_cells["reference_url"][i] or None) == (record["reference"].get("href") or None), f"{object_id} {field}: reference"
            if record["sourceId"] == "exoplanet-eu" and field in {"mass", "mass_sini"}:
                assert m_cells["unit"][i] == "1.8981246e+27kg" and m_cells["mass_definition"][i] == ("MINIMUM_MASS_M_SIN_I" if field == "mass_sini" else "MASS")
            checked += 1
    assert checked > 1000, f"only {checked} measurement rows were checked"
    units_seen = set(m_cells["unit"]) - {None}
    for unit in units_seen:
        if unit not in ROW_LEVEL_NON_VOUNITS:
            u.Unit(unit, format="vounit")
    assert "LOWER_LIMIT" in set(m_cells["limit_type"]) and "UPPER_LIMIT" in set(m_cells["limit_type"]), "limit flags are present in the measurements"
    assert all(m is None or m in {"MASS", "MINIMUM_MASS_M_SIN_I", "MASS_RADIUS_RELATION_ESTIMATE"} for m in set(m_cells["mass_definition"]))

    # ---- references
    references = tables["references"]
    assert len(references) == index["tables"]["references"]["rows"] > 1000
    assert len(set(cells(references["reference_key"]))) == len(references)

    # ---- VOParquet assets
    bulk = json.loads((data / "bulk-downloads.json").read_text())
    assert bulk["schemaVersion"] == "worldsindex-bulk-downloads.v1" and bulk["releaseTag"] == manifest["bulkDownloads"]["releaseTag"]
    for asset in bulk["assets"]:
        assert asset["downloadUrl"] == f"https://github.com/{bulk['repository']}/releases/download/{bulk['releaseTag']}/{asset['name']}"
        assert asset["sourceEcsvSha256"] == index["tables"][asset["table"]]["files"]["ecsv"]["sha256"]
    parquet_checked = 0
    if args.assets and bulk["assets"]:
        import pyarrow.parquet as pq
        assets_dir = Path(args.assets)
        for asset in bulk["assets"]:
            path = assets_dir / asset["name"]
            if not path.is_file():
                print(f"note: {path} is not on disk; skipping its round trip")
                continue
            raw = path.read_bytes()
            assert len(raw) == asset["bytes"] and hashlib.sha256(raw).hexdigest() == asset["sha256"], f"{asset['name']}: digest differs from bulk-downloads.json"
            parquet_file = pq.ParquetFile(path)
            metadata = parquet_file.schema_arrow.metadata
            assert metadata[b"IVOA.VOTable-Parquet.version"] == b"1.0", asset["name"]
            header = ET.fromstring(metadata[b"IVOA.VOTable-Parquet.content"])
            ns = "{http://www.ivoa.net/xml/VOTable/v1.3}"
            fields = header.findall(f".//{ns}FIELD")
            assert header.get("version") == "1.5" and not header.findall(f".//{ns}DATA"), asset["name"]
            columns = index["tables"][asset["table"]]["columns"]
            assert [f.get("name") for f in fields] == [c["name"] for c in columns], f"{asset['name']}: header FIELDs"
            for f, c in zip(fields, columns):
                assert f.get("ucd") == c["ucd"], f"{asset['name']}.{c['name']}: UCD in the VOParquet header"
                if c["unit"]:
                    assert units_equal(f.get("unit"), u.Unit(c["unit"], format="vounit")), f"{asset['name']}.{c['name']}: unit in the VOParquet header"
            assert parquet_file.metadata.row_group(0).column(0).compression == "ZSTD", asset["name"]
            arrow = parquet_file.read()
            ecsv = tables[asset["table"]]
            assert arrow.num_rows == len(ecsv) == asset["rows"] and arrow.column_names == ecsv.colnames
            for name in ecsv.colnames:
                column = arrow.column(name).combine_chunks()
                nulls = np.asarray(column.is_null().to_numpy(zero_copy_only=False))
                if ecsv[name].dtype.kind in "fiub":
                    fill = {"f": 0.0, "b": False}.get(ecsv[name].dtype.kind, 0)
                    values = np.asarray(column.fill_null(fill).to_numpy(zero_copy_only=False))
                    right = np.ma.MaskedArray(values, mask=nulls)
                else:
                    right = np.ma.MaskedArray(np.asarray(column.fill_null("").to_pylist(), dtype=str), mask=nulls)
                column_pair_equal(f"{asset['name']}.{name} (Parquet vs ECSV)", ecsv[name], right)
            hp = arrow.column("healpix_8_nested").to_pylist()
            present = [p for p in hp if p is not None]
            assert present == sorted(present) and all(p is not None for p in hp[: len(present)]), f"{asset['name']}: not sorted by HEALPix"
            parquet_checked += 1
    elif bulk["assets"]:
        print("note: no --assets directory; the VOParquet round trip was not run")

    # ---- Track 2a: conflicts and selections against the conflict-set shards they were flattened from
    conflict_checked = 0
    if manifest.get("conflictSets"):
        assert "conflicts" in tables and "selections" in tables, "a release with conflict sets exports both tables"
        conflicts, selections = tables["conflicts"], tables["selections"]
        c_cells = {name: cells(conflicts[name]) for name in conflicts.colnames}
        s_cells = {name: cells(selections[name]) for name in selections.colnames}
        vocabulary = {"statistically-inconsistent", "method-incompatible", "definition-incompatible", "epoch-dependent", "identity-ambiguity", "status-conflict", "revision-or-retraction", "not-yet-assessed"}
        assert set(c_cells["relation"]) <= vocabulary | {None}, set(c_cells["relation"]) - vocabulary
        assert set(s_cells["row_kind"]) == {"selection, not a measurement"}
        assert set(s_cells["basis"]) <= {"default-solution", "filled-from-publication", "calculated"}
        assert set(s_cells["rule_id"]) == {"nasa-pscomppars-like"} and set(s_cells["review_state"]) == {"unreviewed"} and set(s_cells["selector"]) == {"builder"}
        for unit in set(c_cells["canonical_unit"]) | set(s_cells["unit"]):
            if unit not in (None, "", "percent"):
                u.Unit(unit, format="vounit")
        assert all(calc == (formula is not None) for calc, formula in zip(s_cells["calculated"], s_cells["formula_id"])), "calculated values name their formula"
        assert sum(1 for flag in c_cells["in_conflict"] if flag) == manifest["conflictSets"]["setsInConflict"] + sum(1 for code, flag in zip(c_cells["property_code"], c_cells["in_conflict"]) if code == "object_status" and flag)
        check_sorted_by_healpix("conflicts", conflicts, "object_id")
        check_sorted_by_healpix("selections", selections, "object_id")
        # Every selection row of a sample of shards is the shard's selection, value for value.
        by_selection = {sid: (value, record_ids) for sid, value, record_ids in zip(s_cells["selection_id"], s_cells["value"], s_cells["chosen_source_record_ids"])}
        for shard_name in ("00", "7f", "ff"):
            shard = read_gzip_json(data / "conflicts" / f"{shard_name}.json.gz")
            for product in shard.values():
                for prop in product["properties"]:
                    for selection in prop["selections"]:
                        if selection["basis"] == "not-available":
                            assert selection["id"] not in by_selection
                            continue
                        value, record_ids = by_selection[selection["id"]]
                        assert same(value, selection["value"]) and record_ids == " ".join(selection["chosenSourceRecordIds"]), selection["id"]
                        conflict_checked += 1
        assert any(key.startswith("wi:conflict-sets/") for key in provenance_entities(data)), "provenance names the conflict sets"

    # ---- feeds, changelog and provenance coherence
    feed = json.loads((data / "feed.json").read_text())
    atom = ET.fromstring((data / "feed.xml").read_bytes())
    assert feed["version"] == "https://jsonfeed.org/version/1.1"
    assert len(atom.findall("{http://www.w3.org/2005/Atom}entry")) == len(feed["items"]) == manifest["feeds"]["items"]
    changelog = json.loads((data / "changelog.json").read_text())
    assert changelog["generatedAt"] == manifest["generatedAt"] and changelog["schemaVersion"] == "worldsindex-changelog.v1"
    assert manifest["generatedAt"] in (data / "changelog.md").read_text()
    provenance = json.loads((data / "provenance.json").read_text())
    assert manifest["provenancePath"] == "provenance.json" and provenance["schemaVersion"] == "worldsindex-provenance.v1"
    for path, digest in manifest["artifacts"].items():
        if path == "provenance.json":
            continue
        entity = provenance["entity"][f"wi:artifact/{path}"]
        assert entity["wi:sha256"] == digest["sha256"] and entity["wi:bytes"] == digest["bytes"], path
    registry = read_gzip_json(data / "registry.json.gz")
    for source in registry["sources"]["entries"]:
        entity = provenance["entity"][f"wi:source/{source['id']}"]
        assert entity["wi:contractVersion"] == source["adapterVersion"] and entity["wi:parserVersion"] == source["parserVersion"], source["id"]
    assert any(key.startswith("wi:identity-rules/") for key in provenance["entity"])

    print(f"WorldsIndex interop round trip passed: {len(index['tables'])} tables as ECSV 1.0 and VOTable 1.5 ({sum(e['rows'] for e in index['tables'].values()):,} rows), "
          f"{checked:,} measurement rows joined to their native records, {parquet_checked} VOParquet asset(s) checked, {len(feed['items'])} feed items, {conflict_checked:,} selections matched to their conflict shards.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

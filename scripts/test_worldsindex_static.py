#!/usr/bin/env python3
"""Fail-closed checks for the GitHub-native WorldsIndex release."""

from __future__ import annotations

import gzip
import hashlib
import json
import math
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRANSIT_COLUMNS = [
    "objectId", "name", "host", "raDeg", "decDeg", "status",
    "periodDays", "periodErrDays", "t0Bjd", "t0ErrDays", "durationHours", "depthPpt",
    "hostMag", "hostMagBand", "timeSystem", "sourceId", "sourceTable", "sourceRecordId", "reference", "referenceUrl", "matchedVia",
]
TRANSIT_SOURCES = {"nasa-pscomppars": "pscomppars", "nasa-ps": "ps", "nasa-toi": "toi", "nasa-koi": "cumulative", "nasa-k2": "k2pandc"}
BKJD_OFFSET = 2454833.0
APP = ROOT / "worldsindex"
DATA = APP / "data"


CONTRIBUTIONS_SCHEMA_VERSIONS = {"worldsindex-source-contributions.v1", "worldsindex-source-contributions.v2"}


def check_contribution_rows(contributions: dict, packaged_records: dict) -> None:
    """The rows a consumed contributions file declares must be in the shards: every frozen source the
    builder attached rows for has packaged rows of that source (spectra included; packaged rows are
    distinct records, attachedRows counts one per object a row reached), and v2 rows attached through
    a stated cross-identifier carry `via` with the identifier and the source, table and column that
    stated it."""
    by_source: dict[str, int] = {}
    via_rows = 0
    for record in packaged_records.values():
        by_source[record["sourceId"]] = by_source.get(record["sourceId"], 0) + 1
        if record.get("via"):
            via = record["via"]
            assert via.get("value") and via.get("sourceId") and via.get("table") and via.get("column"), record["sourceRecordId"]
            assert record.get("link"), f"{record['sourceRecordId']} carries via without the attaching link"
            via_rows += 1
    for source_id, summary in contributions["sources"].items():
        if source_id == "nasa-name-tables":
            continue  # its rows are aliases on the catalog index (checked above), not packaged records
        if summary["state"] == "FROZEN_SNAPSHOT" and summary["attachedRows"] > 0:
            assert by_source.get(source_id, 0) > 0, f"{source_id}: {summary['attachedRows']} attached rows declared, none packaged"
    spectra = contributions["sources"].get("nasa-atmospheric-spectroscopy")
    if spectra and spectra["state"] == "FROZEN_SNAPSHOT" and spectra["attachedRows"] > 0:
        assert by_source.get("nasa-atmospheric-spectroscopy", 0) > 0, "spectrum rows were consumed but none is packaged"
    if contributions["schemaVersion"] == "worldsindex-source-contributions.v2":
        declared_via = sum(summary.get("viaCrossIdentifier", 0) for summary in contributions["sources"].values())
        assert (declared_via == 0) == (via_rows == 0), f"v2 declares {declared_via} cross-identifier rows, shards carry {via_rows}"


def read_gzip_json(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def main() -> None:
    manifest = json.loads((DATA / "manifest.json").read_text())
    atlas_path = DATA / "sky-detections.json.gz"
    atlas = read_gzip_json(atlas_path)
    catalog_index_path = DATA / "catalog-index.json.gz"
    catalog_index = read_gzip_json(catalog_index_path)
    registry = read_gzip_json(DATA / "registry.json.gz")
    by_id = {item["objectId"]: item for item in atlas["detections"]}

    assert manifest["schemaVersion"] == "worldsindex-static-release.v1"
    assert atlas["coverage"]["objects"] == len(atlas["detections"]) == manifest["objectCount"]
    assert atlas["coverage"]["renderableObjects"] == manifest["renderableObjectCount"]
    assert atlas["coverage"]["sourceRecordOccurrences"] == manifest["sourceRecordOccurrences"]
    assert hashlib.sha256(atlas_path.read_bytes()).hexdigest() == manifest["atlasSha256"]
    assert catalog_index["schemaVersion"] == "worldsindex-catalog-index.v1"
    assert len(catalog_index["objects"]) == manifest["catalogIndexObjectCount"] == manifest["objectCount"]
    assert hashlib.sha256(catalog_index_path.read_bytes()).hexdigest() == manifest["catalogIndexSha256"]
    assert catalog_index_path.stat().st_size == manifest["catalogIndexBytes"]
    assert catalog_index["coverage"] == manifest["coverage"]
    assert sum(catalog_index["coverage"]["primaryMethodCounts"].values()) == manifest["objectCount"]
    assert all(
        set(item["methodCodes"]) == {claim["methodCode"] for claim in by_id[item["objectId"]]["methodClaims"]}
        for item in catalog_index["objects"]
    )
    # Every byte the release ships must be declared by the manifest, and every declaration
    # must match the bytes on disk. This is the test-side half of the publisher's artifact
    # guard: a manifest cannot reference an artifact that is missing, stale, or unlisted.
    artifacts = manifest["artifacts"]
    assert isinstance(artifacts, dict) and artifacts, "manifest must declare its artifacts"
    # macOS/iCloud sync conflict copies ("catalog-index 2.json.gz") are not build outputs and are
    # never staged by the publisher or checked out by CI; report them but do not fail on them.
    sync_duplicate = re.compile(r" \d+(\.[^.]+)+$")
    sync_duplicates = sorted(
        path.relative_to(DATA).as_posix()
        for path in DATA.rglob("*")
        if path.is_file() and sync_duplicate.search(path.name)
    )
    if sync_duplicates:
        print(f"warning: {len(sync_duplicates)} sync duplicate(s) under worldsindex/data are ignored: {sync_duplicates[:4]}")
    # cloud-run.json is the publisher's run marker (scripts/cloud_run_marker.py), published
    # beside the release for the freshness watchdog; the builder does not produce it.
    on_disk = sorted(
        path.relative_to(DATA).as_posix()
        for path in DATA.rglob("*")
        if path.is_file() and path.name != "manifest.json" and path.relative_to(DATA).as_posix() != "cloud-run.json"
        and not path.name.startswith(".") and not sync_duplicate.search(path.name)
    )
    assert sorted(artifacts) == on_disk, (
        f"manifest.artifacts and worldsindex/data disagree: "
        f"undeclared={sorted(set(on_disk) - set(artifacts))} missing={sorted(set(artifacts) - set(on_disk))}"
    )
    for relative_path, declared in artifacts.items():
        artifact_bytes = (DATA / relative_path).read_bytes()
        assert len(artifact_bytes) == declared["bytes"], f"{relative_path}: byte count drifted from manifest"
        assert hashlib.sha256(artifact_bytes).hexdigest() == declared["sha256"], f"{relative_path}: sha256 drifted from manifest"
    assert artifacts["catalog-index.json.gz"]["sha256"] == manifest["catalogIndexSha256"]
    assert artifacts["sky-detections.json.gz"]["sha256"] == manifest["atlasSha256"]
    assert {f"details/{shard}.json.gz" for shard in manifest["detailShards"]} <= set(artifacts)
    assert len(registry["sources"]["entries"]) == 42
    assert len(registry["methods"]["entries"]) == 13
    detail_paths = sorted(path for path in (DATA / "details").glob("*.json.gz") if not sync_duplicate.search(path.name))
    assert len(detail_paths) == len(manifest["detailShards"]) == 256
    detail_object_count = 0
    packaged_record_ids: dict[str, set[str]] = {}
    packaged_records: dict[str, dict] = {}
    for path in detail_paths:
        shard = read_gzip_json(path)
        detail_object_count += len(shard)
        assert all(item["atlasRecord"]["objectId"] == object_id for object_id, item in shard.items())
        for object_id, item in shard.items():
            ids = {record.get("sourceRecordId") for record in item["records"]}
            assert None not in ids, f"{object_id} has a native row without a stable source-record id"
            packaged_record_ids[object_id] = ids
            for record in item["records"]:
                packaged_records.setdefault(record["sourceRecordId"], record)
    assert detail_object_count == manifest["objectCount"]

    # NASA context attached by exact NASA keys (optional; present when the builder produced it).
    contributions = manifest.get("sourceContributions")
    if contributions:
        # v1 (2026-10-05) and v2 (builder c54691a, 2026-10-16; additive link/via per row) are both
        # consumed; the static builder on main accepted only v1 until 2026-10-20 and silently dropped
        # every v2 file (scripts/worldsindex_contributions.mjs).
        assert contributions["schemaVersion"] in CONTRIBUTIONS_SCHEMA_VERSIONS, contributions["schemaVersion"]
        indexed = {item["objectId"]: item for item in catalog_index["objects"]}
        assert "HIP 108859" in indexed["object-hd-209458-b"].get("aliases", []), "NASA aliases must be searchable"
        for entry in registry["sources"]["entries"]:
            summary = contributions["sources"].get(entry["id"])
            if summary and summary["state"] == "FROZEN_SNAPSHOT" and summary["attachedRows"] > 0:
                assert entry["state"] == "INGESTED" and entry["release"] == summary["snapshotId"], entry["id"]
        check_contribution_rows(contributions, packaged_records)

    packaged_sources = {"nasa-pscomppars", "nasa-toi", "nasa-koi", "nasa-k2", "exoplanet-eu"}
    for detection in atlas["detections"]:
        for value in detection.get("population", {}).values():
            if value.get("sourceId") in packaged_sources:
                assert value.get("sourceRecordId") in packaged_record_ids[detection["objectId"]], (
                    f"{detection['objectId']} display value cannot be joined to its exact native source record"
                )

    transit_count = check_transit_ephemerides(manifest, by_id, packaged_records)
    interop_note = check_interop_products(manifest)
    check_front_end(atlas)

    assert "object-hd-209458-b" in by_id
    assert by_id["object-hd-209458-b"]["sourceRecordCount"] >= 20
    assert {"nasa-ps", "nasa-pscomppars", "exoplanet-eu", "open-exoplanet-catalogue"}.issubset(
        by_id["object-hd-209458-b"]["sourceIds"]
    )
    assert by_id["object-51-eri-b"]["population"]["massEarth"]["limitType"] == "LOWER_LIMIT"

    public_text = "\n".join(
        path.read_text(errors="ignore")
        for path in ROOT.rglob("*")
        if path.is_file()
        and ".git" not in path.parts
        and "ctas" not in path.parts
        and path.suffix.lower() in {".html", ".js", ".css", ".md", ".txt"}
    ).lower()
    assert "worldsindex.therealjackmcg.chatgpt.site" not in public_text
    assert "chatgpt-hosted sites service" not in public_text
    html = (APP / "index.html").read_text()
    javascript = (APP / "assets" / "app.js").read_text()
    assert "data/catalog-index.json.gz" in javascript
    assert "data/sky-detections.json.gz" in html
    assert "assets/app.js" in html

    html_ids = re.findall(r'\bid="([^"]+)"', html)
    assert len(html_ids) == len(set(html_ids)), "WorldsIndex HTML ids must be unique"
    required_ids = {
        "object-search",
        "sky",
        "object-dossier",
        "tab-system",
        "comparison-table",
        "plot",
        "method-detail",
        "source-list",
        "provider-health",
        "release-timeline",
        "download-all-csv",
    }
    assert required_ids.issubset(html_ids)
    assert {"Discover", "Sky", "Compare", "Methods", "Data"}.issubset(set(re.findall(r'data-app-section="[^"]+"[^>]*>([^<]+)', html)))
    assert "Traceability score" not in html + javascript
    assert "83/100" not in html + javascript
    assert "else await selectObject" not in javascript, "A clean visit must not preselect a featured object"
    assert "query.get('compare')||''" in javascript, "Comparison must not start with arbitrary default objects"
    assert "promotion gate decides separately" in javascript
    assert "withheld for review; previous release retained" in javascript, "the UI must be able to say a change was withheld"
    assert "rehearsal, not an official retrieval" in javascript, "a rehearsal outcome must never read as a real promotion"
    assert "executePrimarySearch" in javascript
    assert "exactSearchResult" in javascript
    assert "selectObject(first.dataset.object" not in javascript, "Ambiguous searches must not silently open the first result"
    assert "convertUncertaintyPair" in javascript, "Comparison uncertainties must use unit-safe conversion"
    assert "public_release_generated_at" in javascript
    assert "atlas_generated_at" in javascript
    assert "source_record_id" in javascript and "mass_definition" in javascript and "limit_type" in javascript
    assert "scope:'complete_atlas',filters:{status:'all',method:'all',methodBasis:'claims',identity:'all',source:'all'" in javascript

    print(
        "WorldsIndex static release passed: "
        f"{manifest['objectCount']:,} objects; "
        f"{manifest['detailRecordCount']:,} native rows; "
        + (f"{transit_count:,} transit ephemerides; " if transit_count is not None else "")
        + f"{len(registry['sources']['entries'])} source contracts; "
        f"{len(registry['methods']['entries'])} methods"
        + (f"; {interop_note}." if interop_note else ".")
    )


def check_interop_products(manifest: dict) -> str | None:
    """Track 1 products: exports, bulk downloads, provenance, changelog and feeds.

    Optional like the other later artifacts (a release built by an older builder declares none
    of them); once declared, every file is present, declared as an artifact, and coherent with
    the manifest. The Astropy/pyarrow round trip is scripts/test_worldsindex_interop.py.
    """
    artifacts = manifest["artifacts"]
    if "provenancePath" not in manifest and "exports" not in manifest:
        assert not any(path.startswith("exports/") for path in artifacts), "exports shipped without a manifest declaration"
        print("note: this release predates the interoperability exports (built by an older builder)")
        return None
    assert manifest["provenancePath"] == "provenance.json" and "provenance.json" in artifacts
    provenance = json.loads((DATA / "provenance.json").read_text())
    assert provenance["schemaVersion"] == manifest["provenanceSchemaVersion"] == "worldsindex-provenance.v1"
    assert provenance["generatedAt"] == manifest["generatedAt"]
    for path, declared in artifacts.items():
        if path != "provenance.json":
            assert provenance["entity"][f"wi:artifact/{path}"]["wi:sha256"] == declared["sha256"], f"provenance digest for {path}"
    exports = manifest["exports"]
    assert exports["path"] == "exports/index.json" and exports["path"] in artifacts
    index = json.loads((DATA / exports["path"]).read_text())
    assert index["schemaVersion"] == exports["schemaVersion"] == "worldsindex-exports.v1"
    assert set(index["tables"]) == set(exports["tables"]) >= {"objects", "measurements", "transit_ephemerides", "identity_states", "references"}
    for table, entry in index["tables"].items():
        assert entry["rows"] == exports["tables"][table]["rows"]
        for kind, suffix in (("ecsv", ".ecsv.gz"), ("votable", ".vot.gz")):
            file = entry["files"][kind]
            assert file["path"] == f"exports/{table}{suffix}" == exports["tables"][table][kind] and file["path"] in artifacts
            assert artifacts[file["path"]]["sha256"] == file["sha256"] and artifacts[file["path"]]["bytes"] == file["bytes"]
        for column in entry["columns"]:
            assert column["ucd"] and column["description"] and column["datatype"] in {"string", "float64", "int64", "bool"}, f"{table}.{column['name']}"
        with gzip.open(DATA / entry["files"]["ecsv"]["path"], "rt", encoding="utf-8") as stream:
            assert stream.readline() == "# %ECSV 1.0\n", f"{table}: not ECSV 1.0"
        with gzip.open(DATA / entry["files"]["votable"]["path"], "rt", encoding="utf-8") as stream:
            head = stream.read(400)
        assert 'VOTABLE version="1.5"' in head and 'xmlns="http://www.ivoa.net/xml/VOTable/v1.3"' in head, f"{table}: not VOTable 1.5"
    assert index["tables"]["objects"]["rows"] == manifest["objectCount"] == index["tables"]["identity_states"]["rows"]
    assert index["tables"]["transit_ephemerides"]["rows"] == manifest["transitEphemerides"]["count"]
    bulk_declared = manifest["bulkDownloads"]
    assert bulk_declared["path"] == "bulk-downloads.json" in artifacts
    bulk = json.loads((DATA / "bulk-downloads.json").read_text())
    assert bulk["schemaVersion"] == "worldsindex-bulk-downloads.v1" and bulk["releaseTag"] == bulk_declared["releaseTag"] == "worldsindex-data-latest"
    assert [asset["name"] for asset in bulk["assets"]] == bulk_declared["assets"]
    assert (bulk["unavailableReason"] is None) == bool(bulk["assets"]), "either assets or the reason they are missing"
    for asset in bulk["assets"]:
        assert asset["downloadUrl"].startswith(f"https://github.com/{bulk['repository']}/releases/download/{bulk['releaseTag']}/") and re.fullmatch(r"[0-9a-f]{64}", asset["sha256"]) and asset["bytes"] > 0
        assert asset["rows"] == index["tables"][asset["table"]]["rows"]
    changelog_declared = manifest["changelog"]
    assert changelog_declared["path"] == "changelog.json" in artifacts and changelog_declared["markdownPath"] == "changelog.md" in artifacts
    changelog = json.loads((DATA / "changelog.json").read_text())
    assert changelog["schemaVersion"] == "worldsindex-changelog.v1" and changelog["generatedAt"] == manifest["generatedAt"] and changelog["comparable"] == changelog_declared["comparable"]
    assert changelog["summary"] == changelog_declared["summary"]
    assert (DATA / "changelog.md").read_text().startswith(f"# WorldsIndex release {manifest['generatedAt']}")
    feeds = manifest["feeds"]
    assert feeds["json"] == "feed.json" in artifacts and feeds["atom"] == "feed.xml" in artifacts
    feed = json.loads((DATA / "feed.json").read_text())
    assert feed["version"] == "https://jsonfeed.org/version/1.1" and len(feed["items"]) == feeds["items"]
    assert (DATA / "feed.xml").read_text().count("<entry>") == feeds["items"]
    assert manifest["apiDescriptionUrl"].endswith("/worldsindex/openapi.yaml") and (ROOT / "worldsindex" / "openapi.yaml").is_file()
    assert (ROOT / "worldsindex" / "schemas" / "manifest.schema.json").is_file()
    return f"{len(index['tables'])} export tables, {len(bulk['assets'])} VOParquet asset(s), {feeds['items']} feed items"


def _number(value):
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def check_transit_ephemerides(manifest: dict, by_id: dict, packaged_records: dict) -> int | None:
    """transit-ephemerides.json.gz: one ephemeris per planet, each traceable to one packaged row.

    Optional like the observation products: a release built before the artifact existed does
    not declare it, and is reported rather than failed. Once declared, it is checked fully.
    """
    declared = manifest.get("transitEphemerides")
    if "transit-ephemerides.json.gz" not in manifest["artifacts"]:
        assert declared is None, "manifest describes transit ephemerides but does not declare the artifact"
        print("note: this release predates transit-ephemerides.json.gz (built by an older builder)")
        return None
    assert declared and declared["path"] == "transit-ephemerides.json.gz"
    payload = read_gzip_json(DATA / "transit-ephemerides.json.gz")
    assert payload["schemaVersion"] == declared["schemaVersion"] == "worldsindex-transit-ephemerides.v1"
    assert payload["timeStandard"] == "BJD_TDB"
    assert payload["columns"] == TRANSIT_COLUMNS, "transit ephemeris columns changed without a schema version change"
    assert payload["count"] == len(payload["rows"]) == declared["count"] > 0
    assert sum(declared["bySource"].values()) == declared["count"]
    rows = [dict(zip(payload["columns"], row)) for row in payload["rows"]]
    assert all(len(row) == len(TRANSIT_COLUMNS) for row in payload["rows"])
    assert len({row["objectId"] for row in rows}) == len(rows), "one ephemeris per planet"
    assert len({row["sourceRecordId"] for row in rows}) == len(rows), "a catalog row is used for one planet only"
    period_keys = {"nasa-koi": "koi_period"}
    for row in rows:
        object_id = row["objectId"]
        atlas = by_id.get(object_id)
        assert atlas is not None, f"{object_id}: transit ephemeris for an object outside the atlas"
        assert row["status"] == atlas["normalizedStatus"] in {"CONFIRMED", "CANDIDATE", "CONTROVERSIAL"}, object_id
        assert abs(row["raDeg"] - atlas["raDeg"]) < 1e-5 and abs(row["decDeg"] - atlas["decDeg"]) < 1e-5, object_id
        assert row["sourceTable"] == TRANSIT_SOURCES.get(row["sourceId"]), f"{object_id}: unknown ephemeris source {row['sourceId']}"
        assert row["periodDays"] > 0 and 2400000 < row["t0Bjd"] < 2500000, object_id
        for key in ("periodErrDays", "t0ErrDays", "durationHours", "depthPpt"):
            assert row[key] is None or row[key] > 0, f"{object_id}: {key} must be positive or null"
        assert row["hostMagBand"] in (None, "V", "TESS") and (row["hostMag"] is None) == (row["hostMagBand"] is None), object_id
        assert row["matchedVia"] in (None, "tic+period") and (row["matchedVia"] is None or row["sourceId"] == "nasa-toi"), object_id
        # Never averaged: the period and epoch are exactly the cited row's values.
        record = packaged_records.get(row["sourceRecordId"])
        assert record is not None and record["sourceId"] == row["sourceId"], f"{object_id}: ephemeris row {row['sourceRecordId']} is not packaged"
        values = record["values"]
        assert _number(values.get(period_keys.get(row["sourceId"], "pl_orbper"))) == row["periodDays"], f"{object_id}: period differs from its source row"
        epoch = _number(values.get("koi_time0bk")) + BKJD_OFFSET if row["sourceId"] == "nasa-koi" else _number(values.get("pl_tranmid"))
        assert abs(epoch - row["t0Bjd"]) < 1e-7, f"{object_id}: epoch differs from its source row"
    return len(rows)


def check_front_end(atlas: dict) -> None:
    """Framework v2 Track 1 part D, the parts a static read can verify: the Sky tab, the report link
    and the footer data links are in the page; sky.js pins the Aladin Lite build and the four
    layers and keeps the LGPL note; status.html has one h1, an unbroken heading outline and a link
    to every schema file (and no link without a file); every object without a coordinate is one
    the panel will not render for; the automation notes document the panel and the licence."""
    index = (APP / "index.html").read_text(encoding="utf-8")
    assert 'id="object-tab-sky"' in index and 'id="tab-sky"' in index, "the Sky tab is missing"
    assert 'id="report-problem"' in index, "the report-a-problem link is missing"
    assert 'id="catalog-sky"' in index, "the catalog page has no atlas container"
    for href in ("status.html", "data/changelog.md", "data/feed.json", "data/feed.xml", "data/exports/index.json", "openapi.yaml"):
        assert f'href="{href}"' in index, f"footer link to {href} is missing"
    assert "sky.js?v=" in (APP / "assets" / "app.js").read_text(encoding="utf-8")
    sky = (APP / "assets" / "sky.js").read_text(encoding="utf-8")
    assert "https://aladin.cds.unistra.fr/AladinLite/api/v3/" in sky and "ALADIN_VERSION = '3.6.1'" in sky, "Aladin Lite must stay pinned"
    for hips in ("CDS/P/DSS2/color", "CDS/P/DESI-Legacy-Surveys/DR10/color", "CDS/P/PanSTARRS/DR1/color-z-zg-g", "CDS/P/2MASS/color"):
        assert hips in sky, hips
    assert "LGPL-3.0-or-later" in sky and "not work in Safari" in sky and "data-problem" in sky
    for host in ("legacysurvey.org/viewer/jpeg-cutout", "ps1images.stsci.edu/cgi-bin/fitscut.cgi", "alasky.cds.unistra.fr/hips-image-services/hips2fits"):
        assert host in sky, host
    status_html = (APP / "status.html").read_text(encoding="utf-8")
    assert status_html.count("<h1") == 1, "status page must have exactly one h1"
    levels = [int(level) for level in re.findall(r"<h([1-6])[\s>]", status_html)]
    assert all(after - before <= 1 for before, after in zip(levels, levels[1:])), f"status page heading outline jumps: {levels}"
    schema_links = set(re.findall(r'href="schemas/([^"]+)"', status_html))
    schema_files = {path.name for path in (APP / "schemas").glob("*.schema.json")}
    assert schema_links == schema_files, f"status page schema links {schema_links ^ schema_files} differ from the files"
    assert "status.js?v=" in status_html
    without_coordinates = [d["name"] for d in atlas["detections"] if d.get("raDeg") is None or d.get("decDeg") is None]
    assert len(without_coordinates) <= 20, f"unexpectedly many objects without coordinates: {len(without_coordinates)}"
    notes = (ROOT / "WORLDSINDEX-AUTOMATION.md").read_text(encoding="utf-8")
    assert "Aladin Lite" in notes and "LGPL" in notes and "status.html" in notes, "WORLDSINDEX-AUTOMATION.md must document the sky panel, its licence and the status page"


if __name__ == "__main__":
    main()

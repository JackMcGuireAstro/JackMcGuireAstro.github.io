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
    on_disk = sorted(
        path.relative_to(DATA).as_posix()
        for path in DATA.rglob("*")
        if path.is_file() and path.name != "manifest.json" and not path.name.startswith(".") and not sync_duplicate.search(path.name)
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
        assert contributions["schemaVersion"] == "worldsindex-source-contributions.v1"
        indexed = {item["objectId"]: item for item in catalog_index["objects"]}
        assert "HIP 108859" in indexed["object-hd-209458-b"].get("aliases", []), "NASA aliases must be searchable"
        for entry in registry["sources"]["entries"]:
            summary = contributions["sources"].get(entry["id"])
            if summary and summary["state"] == "FROZEN_SNAPSHOT" and summary["attachedRows"] > 0:
                assert entry["state"] == "INGESTED" and entry["release"] == summary["snapshotId"], entry["id"]

    packaged_sources = {"nasa-pscomppars", "nasa-toi", "nasa-koi", "nasa-k2", "exoplanet-eu"}
    for detection in atlas["detections"]:
        for value in detection.get("population", {}).values():
            if value.get("sourceId") in packaged_sources:
                assert value.get("sourceRecordId") in packaged_record_ids[detection["objectId"]], (
                    f"{detection['objectId']} display value cannot be joined to its exact native source record"
                )

    transit_count = check_transit_ephemerides(manifest, by_id, packaged_records)

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
        f"{len(registry['methods']['entries'])} methods."
    )


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


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Fail-closed checks for the GitHub-native WorldsIndex release."""

from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "worldsindex"
DATA = APP / "data"


def read_gzip_json(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def main() -> None:
    manifest = json.loads((DATA / "manifest.json").read_text())
    atlas_path = DATA / "sky-detections.json.gz"
    atlas = read_gzip_json(atlas_path)
    registry = read_gzip_json(DATA / "registry.json.gz")

    assert manifest["schemaVersion"] == "worldsindex-static-release.v1"
    assert atlas["coverage"]["objects"] == len(atlas["detections"]) == manifest["objectCount"]
    assert atlas["coverage"]["renderableObjects"] == manifest["renderableObjectCount"]
    assert atlas["coverage"]["sourceRecordOccurrences"] == manifest["sourceRecordOccurrences"]
    assert hashlib.sha256(atlas_path.read_bytes()).hexdigest() == manifest["atlasSha256"]
    assert len(registry["sources"]["entries"]) == 42
    assert len(registry["methods"]["entries"]) == 13
    assert len(list((DATA / "details").glob("*.json.gz"))) == len(manifest["detailShards"]) == 256

    by_id = {item["objectId"]: item for item in atlas["detections"]}
    assert "object-hd-209458-b" in by_id
    assert by_id["object-hd-209458-b"]["sourceRecordCount"] >= 20
    assert {"nasa-ps", "nasa-pscomppars", "exoplanet-eu", "open-exoplanet-catalogue"}.issubset(
        by_id["object-hd-209458-b"]["sourceIds"]
    )

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
    assert "data/sky-detections.json.gz" in (APP / "index.html").read_text()
    assert "assets/app.js" in (APP / "index.html").read_text()

    print(
        "WorldsIndex static release passed: "
        f"{manifest['objectCount']:,} objects; "
        f"{manifest['detailRecordCount']:,} native rows; "
        f"{len(registry['sources']['entries'])} source contracts; "
        f"{len(registry['methods']['entries'])} methods."
    )


if __name__ == "__main__":
    main()

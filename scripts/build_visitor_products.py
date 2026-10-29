#!/usr/bin/env python3
"""Deploy-time files for the visitor tools (Framework v2 Track 5), made from a site tree's own
published releases. Nothing about visitors is read or written: every input is public release
data, every output is a static file the browser fetches.

    build_visitor_products.py --tree <site tree> [--out <dir, default the tree>]

writes under <out>/tools/data/:

  manifest.json                 what was built, from which release, with sizes and SHA-256
  sql/<table>.parquet           VOParquet copies of public ECSV exports for DuckDB-WASM on the
                                SQL page (GitHub Pages serves byte ranges with CORS; GitHub
                                Release assets send no CORS header, so the release-asset
                                VOParquet files cannot be used from a browser)
  cone/<product>/<nnn>.json     a HEALPix order-2 NESTED (nside 4, 192 cells) index of every
                                public position, one file per cell, for the cone search page
  feeds/<id>.json, <id>.xml     JSON Feed 1.1 and Atom 1.0 per gallery filter
                                (tools/follow/gallery.json), evaluated with scripts/visitor_filters.py

The deploy runs it on the tree it is about to publish, after the checks, so the files always
describe the data that is deployed; they are not part of either certified release (the CTAS
certificate and the WorldsIndex manifest are unchanged) and a failure here never stops a
deploy: the pages say "not available in this deployment" instead.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import visitor_filters as vf  # noqa: E402

SITE = "https://jackmcguireastro.github.io"
SCHEMA = "jm.visitor-products@1"
CONE_ORDER = 2
CONE_SCHEMA = "jm.cone-index-cell@1"
FEED_ITEMS = 100
CLAIM = ("Derived at deploy time from the published CTAS and WorldsIndex releases named in `inputs`; "
         "a copy and an index of public data, not a new measurement. Nothing about visitors is collected.")
SQL_TABLES = (
    # (table name, product, ECSV path in the tree)
    ("ctas_candidates", "ctas", "ctas/data/research/candidates.ecsv"),
    ("worldsindex_objects", "worldsindex", "worldsindex/data/exports/objects.ecsv.gz"),
    ("worldsindex_measurements", "worldsindex", "worldsindex/data/exports/measurements.ecsv.gz"),
    ("worldsindex_transit_ephemerides", "worldsindex", "worldsindex/data/exports/transit_ephemerides.ecsv.gz"),
)
CONE_COLUMNS = {
    "ctas": ["id", "name", "ra", "dec", "event_type", "classification", "discovery_time"],
    "worldsindex": ["id", "name", "ra", "dec", "status", "method", "discovery_year"],
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def write(path: Path, raw: bytes) -> dict[str, Any]:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {"bytes": len(raw), "sha256": sha256(raw)}


def load_parquet_module():
    spec = importlib.util.spec_from_file_location("worldsindex_parquet_for_visitor", HERE / "worldsindex_parquet.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ------------------------------------------------------------------ inputs
def read_inputs(tree: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"ctas": None, "worldsindex": None, "problems": []}
    try:
        index_raw = (tree / "ctas/data/catalog-index.json").read_bytes()
        index = json.loads(index_raw)
        status = json.loads((tree / "ctas/data/status.json").read_text())
        now = status.get("last_successful_update") or index.get("latest_record_update")
        out["ctas"] = {"records": vf.inflate_ctas_rows(index), "now": now,
                       "release": {"release_time": now, "catalog_content_checksum_sha256": index.get("catalog_content_checksum_sha256"),
                                   "catalog_index_sha256": sha256(index_raw), "candidate_count": index.get("candidate_count")}}
    except (OSError, ValueError) as error:
        out["problems"].append(f"CTAS release not readable: {type(error).__name__}: {error}")
    try:
        manifest = json.loads((tree / "worldsindex/data/manifest.json").read_text())
        catalog_raw = (tree / "worldsindex/data/catalog-index.json.gz").read_bytes()
        catalog = json.loads(gzip.decompress(catalog_raw))
        now = manifest.get("generatedAt")
        out["worldsindex"] = {"records": catalog.get("objects") or [], "now": now,
                              "release": {"release_time": now, "catalog_index_sha256": manifest.get("catalogIndexSha256"),
                                          "object_count": manifest.get("objectCount")}}
    except (OSError, ValueError) as error:
        out["problems"].append(f"WorldsIndex release not readable: {type(error).__name__}: {error}")
    return out


# ------------------------------------------------------------------ SQL (VOParquet on Pages)
def read_ecsv(parquet, path: Path):
    """worldsindex_parquet.read_ecsv for comma-delimited ECSV (WorldsIndex); Astropy's own reader
    for the space-delimited ECSV 1.0 default that the CTAS research tables use."""
    raw = path.read_bytes()
    text_head = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)[:200_000]
    if b"# delimiter: ','" in text_head:
        return parquet.read_ecsv(path)
    import io

    import numpy as np
    import pyarrow as pa
    from astropy.table import Table

    table = Table.read(io.BytesIO(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw), format="ascii.ecsv")
    arrays = {}
    for name in table.colnames:
        col = table[name]
        kind = col.dtype.kind
        mask = np.asarray(getattr(col, "mask", np.zeros(len(col), bool)), dtype=bool) if hasattr(col, "mask") else None
        values = np.asarray(col)
        if kind in "USO":
            arrays[name] = pa.array(["" if (mask is not None and mask[i]) else str(v) for i, v in enumerate(values)], pa.string())
        elif kind == "b":
            arrays[name] = pa.array(values.astype(bool), pa.bool_(), mask=mask)
        elif kind in "iu":
            arrays[name] = pa.array(values.astype("int64"), pa.int64(), mask=mask)
        else:
            arrays[name] = pa.array(values.astype("float64"), pa.float64(), mask=mask)
    return table[:0], pa.table(arrays)


def build_sql(tree: Path, data_dir: Path, available: set[str]) -> tuple[list[dict], list[str]]:
    tables, problems = [], []
    try:
        parquet = load_parquet_module()
    except Exception as error:  # pragma: no cover - environment dependent
        return [], [f"VOParquet writer unavailable: {type(error).__name__}: {error}"]
    for name, product, relative in SQL_TABLES:
        source = tree / relative
        if product not in available or not source.exists():
            problems.append(f"{name}: {relative} is not in this tree")
            continue
        try:
            header, arrow = read_ecsv(parquet, source)
            target = data_dir / "sql" / f"{name}.parquet"
            result = parquet.write_parquet(header, arrow, name, target)
            tables.append({"name": name, "product": product, "path": f"tools/data/sql/{name}.parquet",
                           "bytes": result["bytes"], "sha256": result["sha256"], "rows": result["rows"],
                           "columns": [{"name": c, "type": str(arrow.schema.field(c).type),
                                        "unit": str(header[c].unit) if header[c].unit is not None else None,
                                        "ucd": (header[c].meta or {}).get("ucd"), "description": header[c].description or None}
                                       for c in arrow.column_names],
                           "source": relative, "source_sha256": sha256(source.read_bytes())})
        except Exception as error:
            problems.append(f"{name}: {type(error).__name__}: {error}")
    return tables, problems


# ------------------------------------------------------------------ cone index
def healpix_cells(ra: list[float], dec: list[float]) -> list[int]:
    import astropy.units as u
    from astropy.coordinates import Latitude, Longitude
    from astropy_healpix import HEALPix

    hp = HEALPix(nside=2 ** CONE_ORDER, order="nested")
    return [int(p) for p in hp.lonlat_to_healpix(Longitude(ra * u.deg), Latitude(dec * u.deg))]


def cone_row(product: str, record: dict) -> list[Any] | None:
    if product == "ctas":
        ra, dec = record.get("ra_deg"), record.get("dec_deg")
        row = [record.get("event_id"), record.get("name"), ra, dec, record.get("event_type"), record.get("classification"), record.get("discovery_time")]
    else:
        ra, dec = record.get("raDeg"), record.get("decDeg")
        row = [record.get("objectId"), record.get("name"), ra, dec, record.get("normalizedStatus"), record.get("methodCode"), record.get("discoveryYear")]
    if not isinstance(ra, (int, float)) or not isinstance(dec, (int, float)) or not (-90 <= dec <= 90):
        return None
    return row


def build_cone(data_dir: Path, inputs: dict) -> tuple[dict, list[str]]:
    summary: dict[str, Any] = {"order": CONE_ORDER, "scheme": "HEALPix NESTED, ICRS", "cells": 12 * 4 ** CONE_ORDER, "products": {}}
    problems = []
    for product in ("ctas", "worldsindex"):
        if not inputs.get(product):
            continue
        rows = [r for r in (cone_row(product, rec) for rec in inputs[product]["records"]) if r is not None]
        skipped = len(inputs[product]["records"]) - len(rows)
        try:
            cells = healpix_cells([r[2] for r in rows], [r[3] for r in rows]) if rows else []
        except Exception as error:  # pragma: no cover - environment dependent
            problems.append(f"cone {product}: HEALPix unavailable: {type(error).__name__}: {error}")
            continue
        by_cell: dict[int, list] = {p: [] for p in range(12 * 4 ** CONE_ORDER)}
        for row, cell in zip(rows, cells):
            by_cell[cell].append(row)
        total_bytes, digest = 0, hashlib.sha256()
        for cell, members in by_cell.items():
            members.sort(key=lambda r: str(r[0]))
            raw = (json.dumps({"schema": CONE_SCHEMA, "product": product, "order": CONE_ORDER, "cell": cell,
                               "columns": CONE_COLUMNS[product], "rows": members},
                              ensure_ascii=False, separators=(",", ":")) + "\n").encode()
            write(data_dir / "cone" / product / f"{cell:03d}.json", raw)
            total_bytes += len(raw)
            digest.update(raw)
        summary["products"][product] = {"rows": len(rows), "without_position": skipped, "bytes": total_bytes,
                                        "path_pattern": f"tools/data/cone/{product}/<nnn>.json", "cells_sha256": digest.hexdigest(),
                                        "columns": CONE_COLUMNS[product]}
    return summary, problems


# ------------------------------------------------------------------ gallery feeds
def worldsindex_item(record: dict, now: str | None) -> dict:
    object_id = str(record.get("objectId"))
    name = str(record.get("name") or object_id)
    status = record.get("normalizedStatus") or "status not recorded"
    method = record.get("methodCode") or "method not recorded"
    year = record.get("discoveryYear")
    text = (f"{name} (host {record.get('hostName') or 'not recorded'}): {status}, {method}"
            + (f", discovered {year}" if year else "") + ". WorldsIndex lists what public catalogs report; "
            "membership in a catalog is not independent confirmation.")
    item = {"id": f"urn:worldsindex:object:{object_id}",
            "url": f"{SITE}/worldsindex/?object={quote(object_id, safe='')}&section=object",
            "title": f"{name} — {status.lower().replace('_', ' ')}", "content_text": text,
            "tags": [t for t in (status, method) if t],
            "_worldsindex": {"object_id": object_id, "name": name, "status": record.get("normalizedStatus"),
                             "method": record.get("methodCode"), "discovery_year": year,
                             "ra_deg": record.get("raDeg"), "dec_deg": record.get("decDeg")}}
    if now:
        item["date_modified"] = now
    return item


def render_feeds(flt: dict, items: list[dict], count: int, release: dict) -> tuple[bytes, bytes]:
    base = f"{SITE}/tools/data/feeds/{flt['id']}"
    home = f"{SITE}/tools/follow/#gallery={quote(flt['id'])}"
    description = (f"Records of the public {'CTAS' if flt['product'] == 'ctas' else 'WorldsIndex'} release that match the gallery filter "
                   f"\"{flt['title']}\" ({count} now; the newest {FEED_ITEMS} are listed). Rebuilt with every deployment.")
    document = {"version": "https://jsonfeed.org/version/1.1", "title": f"Follow — {flt['title']}", "home_page_url": home,
                "feed_url": base + ".json", "description": description, "authors": [{"name": "Jack McGuire", "url": f"{SITE}/"}],
                "language": "en", "items": items,
                "_visitor": {"filter": {k: flt[k] for k in ("id", "title", "product", "match", "clauses") if k in flt},
                             "match_count": count, "item_limit": FEED_ITEMS, "release": release, "atom_url": base + ".xml"}}
    json_raw = (json.dumps(document, indent=1, ensure_ascii=False) + "\n").encode()
    updated = max((i.get("date_modified") or i.get("date_published") or "" for i in items), default="") or release.get("release_time") or "2026-01-01T00:00:00Z"
    out = ['<?xml version="1.0" encoding="utf-8"?>', '<feed xmlns="http://www.w3.org/2005/Atom">',
           f"  <title>{escape('Follow — ' + flt['title'])}</title>", f"  <subtitle>{escape(description)}</subtitle>",
           f'  <link rel="self" type="application/atom+xml" href="{escape(base)}.xml"/>',
           f'  <link rel="alternate" type="text/html" href="{escape(home)}"/>',
           f"  <id>{escape(base)}.xml</id>", f"  <updated>{escape(updated)}</updated>", "  <author><name>Jack McGuire</name></author>"]
    for item in items:
        out += ["  <entry>", f"    <title>{escape(item['title'])}</title>",
                f'    <link rel="alternate" type="text/html" href="{escape(item["url"])}"/>',
                f"    <id>{escape(item['id'])}</id>",
                f"    <updated>{escape(item.get('date_modified') or item.get('date_published') or updated)}</updated>"]
        if item.get("date_published"):
            out.append(f"    <published>{escape(item['date_published'])}</published>")
        for tag in item.get("tags") or []:
            out.append(f'    <category term="{escape(str(tag), {chr(34): "&quot;"})}"/>')
        out += [f"    <summary>{escape(item['content_text'])}</summary>", "  </entry>"]
    out.append("</feed>")
    return json_raw, ("\n".join(out) + "\n").encode()


def build_feeds(gallery: dict, data_dir: Path, inputs: dict) -> tuple[list[dict], list[str]]:
    import ctas_feed

    feeds, problems = [], []
    for flt in gallery.get("filters") or []:
        product = flt.get("product")
        if not inputs.get(product):
            problems.append(f"feed {flt.get('id')}: {product} release not available")
            continue
        try:
            matched = vf.apply_filter(flt, inputs[product]["records"], inputs[product]["now"])
        except ValueError as error:
            problems.append(f"feed {flt.get('id')}: {error}")
            continue
        if product == "ctas":
            items = sorted((ctas_feed.feed_item(r) for r in matched), key=ctas_feed._item_sort_key, reverse=True)[:FEED_ITEMS]
        else:
            ordered = sorted(matched, key=lambda r: (-(r.get("discoveryYear") or 0), str(r.get("name") or ""), str(r.get("objectId"))))
            items = [worldsindex_item(r, inputs[product]["now"]) for r in ordered[:FEED_ITEMS]]
        json_raw, atom_raw = render_feeds(flt, items, len(matched), inputs[product]["release"])
        j = write(data_dir / "feeds" / f"{flt['id']}.json", json_raw)
        a = write(data_dir / "feeds" / f"{flt['id']}.xml", atom_raw)
        feeds.append({"id": flt["id"], "title": flt["title"], "product": product, "match_count": len(matched),
                      "match_ids_sha256": sha256("\n".join(sorted(str(r.get(vf.ID_FIELD[product])) for r in matched)).encode()),
                      "json": {"path": f"tools/data/feeds/{flt['id']}.json", **j}, "atom": {"path": f"tools/data/feeds/{flt['id']}.xml", **a}})
    return feeds, problems


# ------------------------------------------------------------------ main
def build(tree: Path, out: Path) -> dict:
    data_dir = out / "tools" / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    inputs = read_inputs(tree)
    available = {p for p in ("ctas", "worldsindex") if inputs.get(p)}
    problems = list(inputs["problems"])
    sql, p = build_sql(tree, data_dir, available)
    problems += p
    cone, p = build_cone(data_dir, inputs)
    problems += p
    gallery_path = tree / "tools/follow/gallery.json"
    if gallery_path.exists():
        feeds, p = build_feeds(json.loads(gallery_path.read_text()), data_dir, inputs)
        problems += p
    else:
        feeds = []
        problems.append("tools/follow/gallery.json is not in this tree")
    manifest = {
        "schema": SCHEMA,
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "claim": CLAIM,
        "inputs": {p: inputs[p]["release"] for p in available},
        "sql": {"engine_note": "Query with DuckDB-WASM on tools/sql/; GitHub Release assets cannot be read from a browser (no CORS).",
                "tables": sql},
        "cone": cone,
        "feeds": feeds,
        "problems": problems,
    }
    (data_dir / "manifest.json").write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n")
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tree", default=".")
    parser.add_argument("--out")
    args = parser.parse_args(argv)
    tree = Path(args.tree).resolve()
    out = Path(args.out).resolve() if args.out else tree
    try:
        manifest = build(tree, out)
    except Exception:
        traceback.print_exc()
        print("visitor products: not built (see the error above); the pages say so", file=sys.stderr)
        return 1
    sizes = sum(t["bytes"] for t in manifest["sql"]["tables"]) + sum(v["bytes"] for v in manifest["cone"]["products"].values()) \
        + sum(f["json"]["bytes"] + f["atom"]["bytes"] for f in manifest["feeds"])
    cone = ", ".join(f"{k} {v['rows']}" for k, v in manifest["cone"]["products"].items())
    print(f"visitor products: {len(manifest['sql']['tables'])} SQL tables, cone index {cone}, "
          f"{len(manifest['feeds'])} gallery feeds, {sizes / 1e6:.1f} MB"
          + (f"; {len(manifest['problems'])} problem(s): " + "; ".join(manifest["problems"]) if manifest["problems"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

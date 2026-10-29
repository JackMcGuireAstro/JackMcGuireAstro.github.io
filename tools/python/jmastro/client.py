"""An astroquery-style client for the CTAS and WorldsIndex public releases (Framework v2 Track 5).

Both products are static files on GitHub Pages, so the client is a reader of those files: no
account, no key, no server-side query. It is kept in the website repository (tools/python) and
is not published to PyPI.

    from jmastro import CTAS, WorldsIndex
    ctas = CTAS()
    ctas.query_object("SN 2024ggi")            # astropy Table (list of dicts without astropy)
    ctas.query_region(210.91, 54.31, radius=0.5)  # degrees
    ctas.query_filter({"schema": "jm.visitor-filter@1", "product": "ctas", "match": "all",
                       "clauses": [{"field": "classification", "op": "contains", "value": "SN Ia"}]})
    ctas.get_dossier(event_id)                  # the complete public record, checksum-verified
    worlds = WorldsIndex()
    worlds.query_object("TRAPPIST-1 e"); worlds.get_detail(object_id); worlds.get_measurements(object_id)

`base_url` may be a URL or a local directory holding a site tree (a checkout with the data
branches overlaid by scripts/overlay_published_data.sh), which the tests use.
"""
from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import os
import re
import urllib.request
from pathlib import Path
from typing import Any, Iterable

SITE = "https://jackmcguireastro.github.io"
USER_AGENT = "jmastro-client/1.0 (+https://jackmcguireastro.github.io/tools/python/)"
FILTER_SCHEMA = "jm.visitor-filter@1"


def _fold(name: Any) -> str:
    return re.sub(r"[\s_-]+", "", str(name or "")).lower()


def _keys(name: Any) -> list[str]:
    key = _fold(name)
    bare = re.sub(r"^(sn|at)(?=\d{4})", "", key)
    return [key] if bare == key else [key, bare]


def separation_deg(ra1: float, dec1: float, ra2: float, dec2: float) -> float:
    r = math.pi / 180
    a, b, dr = dec1 * r, dec2 * r, (ra2 - ra1) * r
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(dr / 2) ** 2
    return 2 * math.asin(min(1.0, math.sqrt(h))) / r


def _table(rows: list[dict], columns: Iterable[str] | None = None, as_table: bool = True):
    """An astropy Table of the rows (lists and dicts become JSON text), or the rows themselves."""
    if not as_table:
        return rows
    try:
        from astropy.table import Table
    except ImportError:  # pragma: no cover - astropy is optional
        return rows
    names = list(columns) if columns is not None else (list(rows[0].keys()) if rows else [])
    cols = {}
    for name in names:
        values = [r.get(name) for r in rows]
        if any(isinstance(v, (list, dict)) for v in values):
            values = [json.dumps(v) if v is not None else "" for v in values]
        elif all(v is None or isinstance(v, (int, float)) and not isinstance(v, bool) for v in values) and any(v is not None for v in values):
            values = [float("nan") if v is None else v for v in values]
        elif any(isinstance(v, str) for v in values):
            values = ["" if v is None else str(v) for v in values]
        cols[name] = values
    return Table(cols, names=names) if names else Table()


class _Reader:
    def __init__(self, base_url: str | os.PathLike | None = None, timeout: float = 60.0):
        base = base_url if base_url is not None else os.environ.get("JMASTRO_BASE_URL", SITE)
        self.local = None if str(base).startswith(("http://", "https://")) else Path(base)
        self.base = str(base).rstrip("/")
        self.timeout = timeout
        self._cache: dict[str, Any] = {}

    def raw(self, path: str, optional: bool = False) -> bytes | None:
        if self.local is not None:
            file = self.local / path
            if not file.exists():
                if optional:
                    return None
                raise FileNotFoundError(path)
            return file.read_bytes()
        request = urllib.request.Request(f"{self.base}/{path}", headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if optional and error.code == 404:
                return None
            raise

    def json(self, path: str, optional: bool = False):
        raw = self.raw(path, optional=optional)
        if raw is None:
            return None
        if raw[:2] == b"\x1f\x8b":
            raw = gzip.decompress(raw)
        return json.loads(raw)

    def cached(self, key: str, load):
        if key not in self._cache:
            self._cache[key] = load()
        return self._cache[key]


class CTAS(_Reader):
    """The Cowboy Transient Alert System public release."""

    page = f"{SITE}/ctas.html"

    def catalog_rows(self) -> list[dict]:
        def load():
            index = self.json("ctas/data/catalog-index.json")
            cols = index["candidate_columns"]
            return [dict(zip(cols, row)) for row in index["candidate_rows"]]
        return self.cached("catalog", load)

    def release_time(self) -> str | None:
        status = self.cached("status", lambda: self.json("ctas/data/status.json", optional=True) or {})
        return status.get("last_successful_update")

    def _aliases(self) -> dict[str, set[str]]:
        def load():
            index = self.json("ctas/data/alias-index.json")
            cols = index["columns"]
            at_id, at_name = cols.index("event_id"), cols.index("designation")
            out: dict[str, set[str]] = {}
            for row in index["rows"]:
                for key in _keys(row[at_name]):
                    out.setdefault(key, set()).add(row[at_id])
            return out
        return self.cached("aliases", load)

    def query_object(self, name: str, as_table: bool = True):
        """Records whose name or any public alias equals `name` (spaces, case, SN/AT prefix ignored)."""
        keys = _keys(name)
        ids = set().union(*(self._aliases().get(k, set()) for k in keys)) if keys else set()
        rows = [r for r in self.catalog_rows() if r["event_id"] in ids or any(k in keys for k in _keys(r["name"]))]
        return _table(rows, as_table=as_table)

    def query_region(self, ra: float, dec: float, radius: float = 0.1, as_table: bool = True):
        """Records within `radius` degrees of (ra, dec), nearest first, with a `distance_deg` column."""
        rows = []
        for r in self.catalog_rows():
            if isinstance(r.get("ra_deg"), (int, float)) and isinstance(r.get("dec_deg"), (int, float)):
                d = separation_deg(ra, dec, r["ra_deg"], r["dec_deg"])
                if d <= radius:
                    rows.append({**r, "distance_deg": d})
        rows.sort(key=lambda r: (r["distance_deg"], r["event_id"]))
        return _table(rows, as_table=as_table)

    def query_filter(self, flt: dict, as_table: bool = True):
        """Records selected by a visitor filter (the Follow page's format, jm.visitor-filter@1)."""
        return _table(_apply_filter(flt, "ctas", self.catalog_rows(), self.release_time()), as_table=as_table)

    def _row(self, event_id: str) -> dict:
        for r in self.catalog_rows():
            if r["event_id"] == event_id:
                return r
        raise KeyError(f"no public CTAS record {event_id}")

    def _chunk_bytes(self, path: str) -> bytes:
        raw = self.raw(path + ".gz", optional=True)  # GitHub Pages serves the gzip form
        if raw is None:
            raw = self.raw(path)
        return gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw

    def get_dossier(self, event_id: str) -> dict:
        """The complete public record of one event, with the chunk checked against the manifest."""
        row = self._row(event_id)
        path = f"ctas/data/{row['detail_chunk']}"
        manifest = self.cached("chunk-manifest", lambda: self.json("ctas/data/candidate-chunks/manifest.json"))
        declared = {c["path"]: c for c in manifest.get("chunks", [])}
        raw = self._chunk_bytes(path)
        entry = declared.get(path)
        if entry is not None and hashlib.sha256(raw).hexdigest() != entry["sha256"]:
            raise ValueError(f"{path}: SHA-256 differs from the release manifest")
        chunk = json.loads(raw)
        if chunk.get("schema") == "ctas.candidate-chunk-parts@1.0.0":
            text = "".join(json.loads(self._chunk_bytes(part["path"]))["json_fragment"] for part in chunk["parts"])
            if hashlib.sha256(text.encode()).hexdigest() != chunk["assembled_sha256"]:
                raise ValueError(f"{path}: reassembled parts differ from the declared SHA-256")
            chunk = json.loads(text)
        for candidate in chunk["candidates"]:
            if candidate["event_id"] == event_id:
                return candidate
        raise KeyError(f"{event_id} is not in {path}")

    def get_photometry(self, event_id: str, as_table: bool = True):
        keep = ["mjd", "band", "magnitude", "magnitude_error", "limiting_magnitude", "detection", "difference_photometry",
                "magnitude_system", "instrument", "provider"]
        obs = self.get_dossier(event_id)["follow_up"].get("observations") or []
        return _table([{k: o.get(k) for k in keep} for o in obs], columns=keep, as_table=as_table)

    def get_datalink(self, event_id: str):
        """The DataLink 1.1 rows of one event (astropy Table; needs astropy)."""
        from astropy.io.votable import parse_single_table

        row = self._row(event_id)
        bucket = row["detail_chunk"].split("/")[-1].removesuffix(".json")
        table = parse_single_table(io.BytesIO(self.raw(f"ctas/data/links/{bucket}.xml"))).to_table()
        return table[table["ID"] == event_id]

    def page_url(self, event_id: str) -> str:
        return f"{SITE}/ctas.html?event={event_id}"


class WorldsIndex(_Reader):
    """The WorldsIndex exoplanet atlas public release."""

    def objects(self) -> list[dict]:
        return self.cached("objects", lambda: self.json("worldsindex/data/catalog-index.json.gz")["objects"])

    def release_time(self) -> str | None:
        manifest = self.cached("manifest", lambda: self.json("worldsindex/data/manifest.json"))
        return manifest.get("generatedAt")

    def query_object(self, name: str, as_table: bool = True):
        """Objects whose name, host name or alias equals `name` (spaces and case ignored)."""
        keys = _keys(name)
        rows = [o for o in self.objects()
                if any(k in keys for k in _keys(o.get("name"))) or any(k in keys for k in _keys(o.get("hostName")))
                or any(any(k in keys for k in _keys(a)) for a in o.get("aliases") or [])]
        return _table(rows, as_table=as_table)

    def query_region(self, ra: float, dec: float, radius: float = 0.1, as_table: bool = True):
        rows = []
        for o in self.objects():
            if isinstance(o.get("raDeg"), (int, float)) and isinstance(o.get("decDeg"), (int, float)):
                d = separation_deg(ra, dec, o["raDeg"], o["decDeg"])
                if d <= radius:
                    rows.append({**o, "distance_deg": d})
        rows.sort(key=lambda r: (r["distance_deg"], r["objectId"]))
        return _table(rows, as_table=as_table)

    def query_filter(self, flt: dict, as_table: bool = True):
        return _table(_apply_filter(flt, "worldsindex", self.objects(), self.release_time()), as_table=as_table)

    def get_detail(self, object_id: str) -> dict | None:
        """Every retained catalog row of one object (the release's detail shard)."""
        bucket = hashlib.sha256(object_id.encode()).hexdigest()[:2]
        shard = self.json(f"worldsindex/data/details/{bucket}.json.gz")
        return shard.get(object_id)

    def get_measurements(self, object_id: str):
        """The object's rows of the measurements export (ECSV; astropy Table, needs astropy)."""
        from astropy.table import Table

        def load():
            raw = self.raw("worldsindex/data/exports/measurements.ecsv.gz")
            return Table.read(gzip.decompress(raw).decode(), format="ascii.ecsv")
        table = self.cached("measurements", load)
        return table[table["object_id"] == object_id]

    def page_url(self, object_id: str) -> str:
        return f"{SITE}/worldsindex/?object={object_id}&section=object"


def _apply_filter(flt: dict, product: str, records: list[dict], now: str | None) -> list[dict]:
    """The Follow page's filter semantics (tools/lib/visitor-filters.js and scripts/visitor_filters.py)."""
    from . import _filters

    if flt.get("product") != product:
        raise ValueError(f'this filter is for "{flt.get("product")}", not "{product}"')
    return _filters.apply_filter(flt, records, now)

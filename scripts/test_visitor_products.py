#!/usr/bin/env python3
"""The deploy-time visitor files (scripts/build_visitor_products.py) on a real release.

Builds every file from this tree's published data (overlay the data branches first) into a
temporary directory and checks:
  - the VOParquet tables read back with pyarrow with the source ECSV's rows, columns and the
    IVOA VOParquet metadata, and every example query of the SQL page runs on them (DuckDB,
    when its Python package is installed);
  - the cone index holds every positioned record exactly once, each in its astropy-healpix cell,
    and the largest cell radius the browser assumes (tools/lib/healpix.js) really bounds the cells;
  - the gallery feeds are valid JSON Feed 1.1 and Atom 1.0, and the Python engine selects exactly
    the records the browser's JavaScript engine selects (node runs tools/lib/visitor-filters.js);
  - every filter field exists in the published catalogs;
  - the notebooks the browser makes run in CPython against this tree (served on localhost).

    python3 scripts/test_visitor_products.py
    python3 scripts/test_visitor_products.py --write-fixture   # regenerate test_visitor_tools_fixture.json
"""
from __future__ import annotations

import contextlib
import functools
import io
import gzip
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import xml.dom.minidom
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_visitor_products as bvp  # noqa: E402
import visitor_filters as vf  # noqa: E402

HAVE_DATA = (ROOT / "ctas/data/catalog-index.json").exists() and (ROOT / "worldsindex/data/manifest.json").exists()
NODE = shutil.which("node")


def write_fixture() -> None:
    import astropy.units as u
    import numpy as np
    from astropy.coordinates import Latitude, Longitude
    from astropy_healpix import HEALPix, __version__ as hp_version

    rng = np.random.default_rng(20261029)
    ra = np.concatenate([rng.uniform(0, 360, 400), rng.uniform(0, 360, 50), rng.uniform(0, 360, 50)])
    dec = np.concatenate([np.degrees(np.arcsin(rng.uniform(-1, 1, 400))), rng.uniform(80, 89.999, 50), rng.uniform(-89.999, -80, 50)])
    ra, dec = np.round(ra, 6), np.round(dec, 6)
    pix = {}
    for order in (0, 2, 5, 8, 12, 16, 20):
        hp = HEALPix(nside=2 ** order, order="nested")
        pix[str(order)] = [int(p) for p in hp.lonlat_to_healpix(Longitude(ra * u.deg), Latitude(dec * u.deg))]
    centres = {}
    for order in (3, 10):
        hp = HEALPix(nside=2 ** order, order="nested")
        sel = rng.integers(0, 12 * 4 ** order, 60)
        lon, lat = hp.healpix_to_lonlat(sel)
        centres[str(order)] = {"pix": [int(x) for x in sel], "ra": np.round(lon.deg, 9).tolist(), "dec": np.round(lat.deg, 9).tolist()}
    document = {"source": f"astropy-healpix {hp_version}, NESTED; regenerate with scripts/test_visitor_products.py --write-fixture",
                "ra": ra.tolist(), "dec": dec.tolist(), "pix": pix, "centres": centres}
    (ROOT / "scripts/test_visitor_tools_fixture.json").write_text(json.dumps(document, separators=(",", ":")))


@unittest.skipUnless(HAVE_DATA, "the releases are not overlaid (bash scripts/overlay_published_data.sh)")
class VisitorProductsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="visitor-products-"))
        cls.manifest = bvp.build(ROOT, cls.tmp)
        cls.data = cls.tmp / "tools" / "data"
        cls.inputs = bvp.read_inputs(ROOT)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_manifest_lists_both_releases_and_no_problems(self):
        self.assertEqual(self.manifest["schema"], bvp.SCHEMA)
        self.assertEqual(set(self.manifest["inputs"]), {"ctas", "worldsindex"})
        self.assertEqual(self.manifest["problems"], [])
        self.assertEqual(json.loads((self.data / "manifest.json").read_text())["schema"], bvp.SCHEMA)

    def test_parquet_tables_round_trip(self):
        import pyarrow.parquet as pq

        tables = {t["name"]: t for t in self.manifest["sql"]["tables"]}
        self.assertEqual(set(tables), {name for name, _, _ in bvp.SQL_TABLES})
        for name, table in tables.items():
            path = self.tmp / table["path"]
            self.assertEqual(path.stat().st_size, table["bytes"])
            self.assertLess(table["bytes"], 50_000_000, f"{name}: keep each SQL file well under 100 MB")
            parquet = pq.read_table(path)
            self.assertEqual(parquet.num_rows, table["rows"])
            meta = parquet.schema.metadata
            self.assertEqual(meta[b"IVOA.VOTable-Parquet.version"], b"1.0")
            self.assertIn(b"<FIELD", meta[b"IVOA.VOTable-Parquet.content"])
            source = ROOT / table["source"]
            raw = source.read_bytes()
            text = (gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw).decode()
            from astropy.table import Table
            ecsv = Table.read(text, format="ascii.ecsv")
            self.assertEqual(len(ecsv), parquet.num_rows, name)
            self.assertEqual(ecsv.colnames, parquet.column_names, name)

    def test_every_sql_example_runs(self):
        try:
            import duckdb
        except ImportError:
            self.skipTest("the duckdb Python package is not installed (the browser test runs the examples in DuckDB-WASM)")
        con = duckdb.connect()
        for table in self.manifest["sql"]["tables"]:
            con.execute(f"CREATE VIEW {table['name']} AS SELECT * FROM read_parquet('{(self.tmp / table['path']).as_posix()}')")
        examples = json.loads((ROOT / "tools/sql/examples.json").read_text())["examples"]
        for example in examples:
            rows = con.execute(example["sql"]).fetchall()
            self.assertGreater(len(rows), 0, example["title"])

    def test_cone_index_holds_every_positioned_record_once_in_its_cell(self):
        import astropy.units as u
        from astropy.coordinates import Latitude, Longitude
        from astropy_healpix import HEALPix

        hp = HEALPix(nside=2 ** bvp.CONE_ORDER, order="nested")
        for product, id_key, ra_key, dec_key in (("ctas", "event_id", "ra_deg", "dec_deg"), ("worldsindex", "objectId", "raDeg", "decDeg")):
            seen = []
            for cell in range(12 * 4 ** bvp.CONE_ORDER):
                doc = json.loads((self.data / "cone" / product / f"{cell:03d}.json").read_text())
                self.assertEqual((doc["schema"], doc["cell"], doc["columns"]), (bvp.CONE_SCHEMA, cell, bvp.CONE_COLUMNS[product]))
                if doc["rows"]:
                    cells = hp.lonlat_to_healpix(Longitude([r[2] for r in doc["rows"]] * u.deg), Latitude([r[3] for r in doc["rows"]] * u.deg))
                    self.assertTrue(all(int(c) == cell for c in cells), f"{product} cell {cell}")
                seen += [r[0] for r in doc["rows"]]
            records = self.inputs[product]["records"]
            positioned = [str(r[id_key]) for r in records if isinstance(r.get(ra_key), (int, float)) and isinstance(r.get(dec_key), (int, float)) and -90 <= r[dec_key] <= 90]
            self.assertEqual(len(seen), len(set(seen)), f"{product}: a record is in two cells")
            self.assertEqual(sorted(map(str, seen)), sorted(positioned))
            self.assertEqual(self.manifest["cone"]["products"][product]["rows"], len(positioned))

    def test_the_browser_cell_radius_bounds_every_cell(self):
        import astropy.units as u
        import numpy as np
        from astropy.coordinates import Latitude, Longitude, SkyCoord
        from astropy_healpix import HEALPix

        source = (ROOT / "tools/lib/healpix.js").read_text()
        bounds = {int(k): float(v) for k, v in re.findall(r"(\d+): ([\d.]+)", source[source.index("MAX_CELL_RADIUS_DEG = {"):source.index("};", source.index("MAX_CELL_RADIUS_DEG = {"))])}
        rng = np.random.default_rng(1)
        ra = rng.uniform(0, 360, 400_000)
        dec = np.degrees(np.arcsin(rng.uniform(-1, 1, 400_000)))
        for order in (0, 1, 2, 3, 4, 5):
            hp = HEALPix(nside=2 ** order, order="nested")
            cells = hp.lonlat_to_healpix(Longitude(ra * u.deg), Latitude(dec * u.deg))
            lon, lat = hp.healpix_to_lonlat(cells)
            worst = SkyCoord(ra * u.deg, dec * u.deg).separation(SkyCoord(lon, lat)).deg.max()
            self.assertLess(worst * 1.1, bounds[order], f"order {order}: measured {worst:.3f} deg")

    def test_feeds_are_valid_and_match_the_javascript_engine(self):
        gallery = json.loads((ROOT / "tools/follow/gallery.json").read_text())
        feeds = {f["id"]: f for f in self.manifest["feeds"]}
        self.assertEqual(set(feeds), {f["id"] for f in gallery["filters"]})
        for flt in gallery["filters"]:
            doc = json.loads((self.tmp / feeds[flt["id"]]["json"]["path"]).read_text())
            self.assertEqual(doc["version"], "https://jsonfeed.org/version/1.1")
            self.assertTrue(all("id" in i and ("content_text" in i or "content_html" in i) for i in doc["items"]))
            self.assertLessEqual(len(doc["items"]), bvp.FEED_ITEMS)
            self.assertEqual(doc["_visitor"]["match_count"], feeds[flt["id"]]["match_count"])
            atom = xml.dom.minidom.parse(str(self.tmp / feeds[flt["id"]]["atom"]["path"]))
            self.assertEqual(atom.documentElement.tagName, "feed")
            self.assertEqual(len(atom.getElementsByTagName("entry")), len(doc["items"]))
        if not NODE:
            self.skipTest("node is not installed")
        script = r"""
import { readFileSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { applyFilter, inflateCtasRows, ID_FIELD } from "./tools/lib/visitor-filters.js";
const root = process.argv[1];
const gallery = JSON.parse(readFileSync(`${root}/tools/follow/gallery.json`));
const ctasIndex = JSON.parse(readFileSync(`${root}/ctas/data/catalog-index.json`));
const status = JSON.parse(readFileSync(`${root}/ctas/data/status.json`));
const manifest = JSON.parse(readFileSync(`${root}/worldsindex/data/manifest.json`));
const worlds = JSON.parse(gunzipSync(readFileSync(`${root}/worldsindex/data/catalog-index.json.gz`))).objects;
const data = { ctas: { records: inflateCtasRows(ctasIndex), now: status.last_successful_update || ctasIndex.latest_record_update },
               worldsindex: { records: worlds, now: manifest.generatedAt } };
const out = {};
for (const f of gallery.filters) out[f.id] = applyFilter(f, data[f.product].records, { now: data[f.product].now }).map((r) => String(r[ID_FIELD[f.product]])).sort();
process.stdout.write(JSON.stringify(out));
"""
        done = subprocess.run([NODE, "--input-type=module", "-e", script, str(ROOT)], cwd=ROOT, capture_output=True, text=True, timeout=300)
        self.assertEqual(done.returncode, 0, done.stderr)
        js = json.loads(done.stdout)
        for flt in gallery["filters"]:
            python_ids = sorted(str(r[vf.ID_FIELD[flt["product"]]]) for r in vf.apply_filter(flt, self.inputs[flt["product"]]["records"], self.inputs[flt["product"]]["now"]))
            self.assertEqual(python_ids, js[flt["id"]], flt["id"])

    def test_every_filter_field_exists_in_the_published_catalogs(self):
        ctas_cols = set(json.loads((ROOT / "ctas/data/catalog-index.json").read_text())["candidate_columns"])
        worlds_keys = set().union(*(o.keys() for o in self.inputs["worldsindex"]["records"][:5000]))
        self.assertLessEqual(set(vf.FIELDS["ctas"]), ctas_cols)
        self.assertLessEqual(set(vf.FIELDS["worldsindex"]), worlds_keys)
        js = (ROOT / "tools/lib/visitor-filters.js").read_text()
        for product, fields in vf.FIELDS.items():
            block = js[js.index(f"  {product}: {{"):]
            block = block[:block.index("\n  },")]
            self.assertEqual(dict(re.findall(r"^\s+(\w+): \[\"(\w+)\"", block, re.MULTILINE)), fields, f"{product}: the Python and JavaScript field lists differ")

    def test_notebooks_run_against_this_tree(self):
        if not NODE:
            self.skipTest("node is not installed")
        os.environ.setdefault("MPLBACKEND", "Agg")
        try:
            import matplotlib  # noqa: F401
        except ImportError:
            self.skipTest("matplotlib is not installed")
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):  # noqa: D401 - keep the test output readable
                pass

        handler = functools.partial(Quiet, directory=str(ROOT))
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            sample = next(r for r in self.inputs["ctas"]["records"] if r.get("n_observations", 0) > 10 and str(r.get("name", "")).startswith("SN"))
            for product, ident, chunk in (("ctas", sample["event_id"], sample["detail_chunk"]), ("worldsindex", "object-hd-209458-b", None)):
                script = r"""
import { makeNotebook } from "./tools/notebook/notebook.js";
const [product, id, chunk] = process.argv.slice(1);
process.stdout.write(JSON.stringify(makeNotebook(product, id, "test", null, chunk || null)));
"""
                done = subprocess.run([NODE, "--input-type=module", "-e", script, product, ident, chunk or ""], cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(done.returncode, 0, done.stderr)
                notebook = json.loads(done.stdout)
                namespace: dict = {}
                for cell in notebook["cells"]:
                    if cell["cell_type"] != "code":
                        continue
                    code = "".join(cell["source"]).replace(bvp.SITE, base)
                    # The JupyterLite branch awaits micropip; CPython never enters it.
                    code = code.replace("await micropip.install", "micropip.install")
                    if "plt.show()" in code:
                        code = code.replace("plt.show()", "plt.close('all')")
                    with contextlib.redirect_stdout(io.StringIO()):
                        exec(compile(code, f"<{product} notebook>", "exec"), namespace)
                if product == "ctas":
                    self.assertEqual(namespace["dossier"]["event_id"], ident)
                    self.assertGreater(len(namespace["photometry"]), 0)
                    self.assertGreater(len(namespace["links"]), 0)
                else:
                    self.assertGreater(len(namespace["mine"]), 10)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    if "--write-fixture" in sys.argv:
        write_fixture()
        print("wrote scripts/test_visitor_tools_fixture.json")
        sys.exit(0)
    unittest.main(verbosity=2)

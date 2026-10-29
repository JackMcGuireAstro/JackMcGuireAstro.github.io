// Unit checks for the visitor tools (Framework v2 Track 5): HEALPix and MOC (tools/lib/healpix.js)
// against astropy-healpix reference values, the filter / watchlist / sharing engine
// (tools/lib/visitor-filters.js), the cone search and VOTable writer (tools/cone/cone.js), the
// notebook generator (tools/notebook/notebook.js) and the SQL example file. No network, no DOM.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  ang2pixNest, pix2angNest, separationDeg, cellsTouchingCone, maxCellRadiusDeg, parseMoc, mocContains, mocAreaSqDeg,
  mocToAscii, coneMoc,
} from "../tools/lib/healpix.js";
import {
  FIELDS, FILTER_SCHEMA, validateFilter, applyFilter, describeFilter, inflateCtasRows, parseWatchLine, watchMatches,
  aliasMapFromCtas, newSince, encodeFilterForUrl, decodeFilterFromUrl, exportState, importState,
} from "../tools/lib/visitor-filters.js";
import { selectInCone, toVOTable, toCsv } from "../tools/cone/cone.js";
import { makeNotebook, notebookCode, replLink } from "../tools/notebook/notebook.js";

const fixture = JSON.parse(readFileSync(new URL("./test_visitor_tools_fixture.json", import.meta.url)));
let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };

// ------------------------------------------------------------------ HEALPix
test("ang2pixNest agrees with astropy-healpix at orders 0–20", () => {
  for (const [order, pixels] of Object.entries(fixture.pix)) {
    pixels.forEach((p, i) => assert.equal(ang2pixNest(Number(order), fixture.ra[i], fixture.dec[i]), p, `order ${order} point ${i}`));
  }
});
test("pix2angNest agrees with astropy-healpix cell centres", () => {
  for (const [order, c] of Object.entries(fixture.centres)) {
    c.pix.forEach((p, i) => {
      const got = pix2angNest(Number(order), p);
      assert.ok(separationDeg(got.ra, got.dec, c.ra[i], c.dec[i]) < 1e-9, `order ${order} pixel ${p}`);
    });
  }
});
test("a cell's centre lies in the cell, and the cone cell list is a superset", () => {
  for (const order of [2, 4, 7]) for (let p = 0; p < 12 * 4 ** order; p += 37) { const c = pix2angNest(order, p); assert.equal(ang2pixNest(order, c.ra, c.dec), p); }
  let seed = 7;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
  for (let k = 0; k < 400; k += 1) {
    const ra = rnd() * 360, dec = Math.asin(2 * rnd() - 1) * 180 / Math.PI, r = 0.01 + rnd() * 9;
    const cells = new Set(cellsTouchingCone(2, ra, dec, r));
    for (let j = 0; j < 20; j += 1) { // points inside the cone
      const rho = r * Math.sqrt(rnd()) * Math.PI / 180, theta = rnd() * 2 * Math.PI, d0 = dec * Math.PI / 180;
      const sd = Math.sin(d0) * Math.cos(rho) + Math.cos(d0) * Math.sin(rho) * Math.cos(theta);
      const d = Math.asin(Math.max(-1, Math.min(1, sd)));
      const a = ra * Math.PI / 180 + Math.atan2(Math.sin(theta) * Math.sin(rho) * Math.cos(d0), Math.cos(rho) - Math.sin(d0) * sd);
      const pra = ((a * 180 / Math.PI) % 360 + 360) % 360, pdec = d * 180 / Math.PI;
      assert.ok(cells.has(ang2pixNest(2, pra, pdec)), `cone (${ra},${dec},${r}) point (${pra},${pdec})`);
    }
  }
  assert.ok(maxCellRadiusDeg(2) > 0 && maxCellRadiusDeg(9) < maxCellRadiusDeg(5));
});
test("MOC JSON and ASCII parse to the same coverage; area, containment and text round trip", () => {
  const a = parseMoc('{"3":[1,2,3],"4":[100]}'), b = parseMoc("s 3/1-3 4/100");
  assert.equal(mocToAscii(a), "3/1-3 4/100");
  assert.equal(mocToAscii(b), mocToAscii(a));
  assert.ok(Math.abs(mocAreaSqDeg(parseMoc("0/0")) - 41252.96 / 12) < 0.01);
  assert.ok(Math.abs(mocAreaSqDeg(parseMoc("0/0 1/0-3")) - 41252.96 / 12) < 0.01, "children of a listed cell are not counted twice");
  const c = pix2angNest(4, 100);
  assert.ok(mocContains(a, c.ra, c.dec));
  const outside = pix2angNest(4, 2000);
  assert.ok(!mocContains(a, outside.ra, outside.dec));
  assert.throws(() => parseMoc("1/99"), /does not exist/);
  assert.throws(() => parseMoc(""), /empty/);
  assert.throws(() => parseMoc("1-3"), /order/);
});
test("a cone MOC covers its centre and about the right area", () => {
  const moc = coneMoc(83.82, -5.39, 2, 8);
  assert.ok(mocContains(moc, 83.82, -5.39));
  assert.ok(!mocContains(moc, 83.82, -8.0));
  const area = mocAreaSqDeg(moc), expected = 2 * Math.PI * (1 - Math.cos(2 * Math.PI / 180)) * (180 / Math.PI) ** 2;
  assert.ok(Math.abs(area - expected) / expected < 0.05, `${area} vs ${expected}`);
});

// ------------------------------------------------------------------ filters
const NOW = "2026-10-29T06:00:00Z";
const ctasRows = [
  { event_id: "a", name: "SN2026abc", classification: "SN Ia", discovery_time: "2026-10-25T01:00:00Z", discovery_magnitude: 17.2, messenger_channels: ["electromagnetic"], ctas_score: 40, primary_messenger: "electromagnetic", latest_spectrum_at: null, ra_deg: 10, dec_deg: 20 },
  { event_id: "b", name: "AT2026xyz", classification: "Unclassified", discovery_time: "2026-09-01T00:00:00Z", discovery_magnitude: 19.5, messenger_channels: ["electromagnetic"], ctas_score: 70, primary_messenger: "electromagnetic", ra_deg: 10.001, dec_deg: 20.001 },
  { event_id: "c", name: "IceCube-261020A", classification: "Astrophysical neutrino", discovery_time: "2026-10-20T00:00:00.123456Z", discovery_magnitude: null, messenger_channels: ["neutrino"], ctas_score: null, primary_messenger: "neutrino", ra_deg: 200, dec_deg: -30 },
];
const f = (clauses, match = "all") => ({ schema: FILTER_SCHEMA, title: "t", product: "ctas", match, clauses });
test("filters validate and select with every operator", () => {
  assert.deepEqual(validateFilter(f([{ field: "classification", op: "contains", value: "ia" }])), []);
  assert.ok(validateFilter(f([{ field: "nope", op: "eq", value: 1 }]))[0].includes("not a field"));
  assert.ok(validateFilter(f([{ field: "ctas_score", op: "contains", value: 1 }]))[0].includes("does not apply"));
  assert.ok(validateFilter(f([])).length);
  const ids = (flt) => applyFilter(flt, ctasRows, { now: NOW }).map((r) => r.event_id);
  assert.deepEqual(ids(f([{ field: "classification", op: "contains", value: "SN Ia" }])), ["a"]);
  assert.deepEqual(ids(f([{ field: "classification", op: "in", value: ["unclassified", "SN Ia"] }])), ["a", "b"]);
  assert.deepEqual(ids(f([{ field: "discovery_magnitude", op: "lte", value: 18 }])), ["a"]);
  assert.deepEqual(ids(f([{ field: "discovery_magnitude", op: "missing" }])), ["c"]);
  assert.deepEqual(ids(f([{ field: "discovery_time", op: "within_days", value: 7 }])), ["a"]);
  assert.deepEqual(ids(f([{ field: "discovery_time", op: "within_days", value: 9.25 }])), ["a", "c"]);
  assert.deepEqual(ids(f([{ field: "discovery_time", op: "after", value: "2026-10-01" }])), ["a", "c"]);
  assert.deepEqual(ids(f([{ field: "messenger_channels", op: "contains", value: "NEUTRINO" }])), ["c"]);
  assert.deepEqual(ids(f([{ field: "ctas_score", op: "gte", value: 50 }, { field: "primary_messenger", op: "eq", value: "neutrino" }], "any")), ["b", "c"]);
  assert.equal(describeFilter(f([{ field: "discovery_time", op: "within_days", value: 7 }])), "Discovery time within the last 7 days of the release");
  assert.throws(() => applyFilter(f([{ field: "x", op: "eq", value: 1 }]), ctasRows, { now: NOW }));
});
test("the gallery file holds valid filters with unique ids", () => {
  const gallery = JSON.parse(readFileSync(new URL("../tools/follow/gallery.json", import.meta.url)));
  const ids = new Set();
  for (const flt of gallery.filters) {
    assert.deepEqual(validateFilter(flt), [], flt.id);
    assert.match(flt.id, /^[a-z0-9-]+$/);
    assert.ok(!ids.has(flt.id), flt.id); ids.add(flt.id);
  }
  assert.ok(gallery.filters.some((x) => x.product === "ctas") && gallery.filters.some((x) => x.product === "worldsindex"));
});
test("inflate CTAS rows, watch lines, names and aliases, positions", () => {
  assert.deepEqual(inflateCtasRows({ candidate_columns: ["event_id", "name"], candidate_rows: [["a", "SN1"]] }), [{ event_id: "a", name: "SN1" }]);
  assert.deepEqual(parseWatchLine("14:03:38.6 +54:18:42 10").kind, "position");
  assert.equal(parseWatchLine("51 Peg b").kind, "name");
  assert.equal(parseWatchLine("# comment"), null);
  assert.equal(parseWatchLine("361 10").kind, "name", "an impossible RA is a name");
  const aliases = aliasMapFromCtas({ columns: ["event_id", "source_key", "designation", "ambiguous"], rows: [["b", "ztf", "ZTF26aaaaaaa", false], ["a", "tns", "2026abc", false]] });
  assert.deepEqual(watchMatches(parseWatchLine("ztf26aaaaaaa"), "ctas", ctasRows, aliases).map((r) => r.event_id), ["b"]);
  assert.deepEqual(watchMatches(parseWatchLine("SN 2026abc"), "ctas", ctasRows, aliases).map((r) => r.event_id), ["a"]);
  assert.deepEqual(watchMatches(parseWatchLine("2026abc"), "ctas", ctasRows, null).map((r) => r.event_id), ["a"], "TNS prefix is optional");
  assert.deepEqual(watchMatches(parseWatchLine("10 20 4"), "ctas", ctasRows).map((r) => r.event_id), ["a"]);
  assert.deepEqual(watchMatches(parseWatchLine("10 20 6"), "ctas", ctasRows).map((r) => r.event_id), ["a", "b"]);
  const worlds = [{ objectId: "o1", name: "TRAPPIST-1 e", hostName: "TRAPPIST-1", aliases: ["2MASS J23062928-0502285 e"], raDeg: 346.6, decDeg: -5.04 }];
  assert.equal(watchMatches(parseWatchLine("trappist 1 e"), "worldsindex", worlds).length, 1);
  assert.equal(watchMatches(parseWatchLine("2MASS J23062928-0502285 e"), "worldsindex", worlds).length, 1);
});
test("new since you last looked", () => {
  assert.deepEqual(newSince(["b", "a"], null), { ids: ["a", "b"], newIds: [], firstLook: true });
  assert.deepEqual(newSince(["a", "b", "c"], { ids: ["a", "b"] }).newIds, ["c"]);
  assert.deepEqual(newSince(["a"], { ids: ["a", "b"] }).newIds, []);
});
test("shared links carry the filter only; import keeps only valid content", () => {
  const flt = { ...f([{ field: "classification", op: "contains", value: "SN Ia ★" }]), id: "mine-1", private: "x" };
  const back = decodeFilterFromUrl(encodeFilterForUrl(flt));
  assert.deepEqual(back.clauses, flt.clauses);
  assert.equal(back.private, undefined);
  assert.equal(back.id, undefined);
  assert.throws(() => decodeFilterFromUrl(encodeFilterForUrl({ ...flt, product: "nope" })));
  const text = exportState({ watchlist: ["SN 2024ggi"], regions: [{ title: "r", moc: "3/1" }], filters: [flt, { bad: 1 }] });
  const imported = importState(text);
  assert.deepEqual(imported.watchlist, ["SN 2024ggi"]);
  assert.equal(imported.filters.length, 1);
  assert.throws(() => importState("{}"), /export/);
  assert.throws(() => importState("not json"), /JSON/);
});
test("every filter field names a CTAS catalog column or WorldsIndex index key that exists", () => {
  // Checked against the real files by scripts/test_visitor_products.py; here only the shape.
  for (const [product, fields] of Object.entries(FIELDS)) for (const [name, spec] of Object.entries(fields)) {
    assert.ok(["text", "list", "number", "time"].includes(spec[0]), `${product}.${name}`);
  }
});

// ------------------------------------------------------------------ cone search
test("cone selection is exact and nearest first; the VOTable is SCS-shaped", () => {
  const cells = [{ product: "ctas", columns: ["id", "name", "ra", "dec", "event_type", "classification", "discovery_time"],
                   rows: [["a", "A", 10, 20, "optical-transient", "SN Ia", "2026-10-01"], ["b", "B & <b>", 10.2, 20, "optical-transient", "", ""], ["c", "C", 11, 20, "x", "y", "z"]] },
                 { product: "worldsindex", columns: ["id", "name", "ra", "dec", "status", "method", "discovery_year"], rows: [["o", "Planet", 10.05, 20, "CONFIRMED", "radial-velocity", 2001]] }];
  const rows = selectInCone(cells, 10, 20, 0.2);
  assert.deepEqual(rows.map((r) => r.id), ["a", "o", "b"]);
  assert.equal(rows[1].kind, "CONFIRMED");
  assert.equal(rows[1].label, "radial-velocity");
  const vot = toVOTable(rows, { ra: 10, dec: 20, radiusDeg: 0.2, releases: "x" });
  assert.match(vot, /ucd="meta\.id;meta\.main"/);
  assert.match(vot, /ucd="pos\.eq\.ra;meta\.main"/);
  assert.match(vot, /ucd="pos\.eq\.dec;meta\.main"/);
  assert.equal((vot.match(/<TR>/g) || []).length, 3);
  assert.ok(vot.includes("B &amp; &lt;b&gt;"));
  assert.equal(toCsv(rows).trim().split("\n").length, 4);
});

// ------------------------------------------------------------------ notebook
test("the notebook is nbformat 4.5 and carries the record id; the REPL link carries the code", () => {
  const nb = makeNotebook("ctas", "uuid-1", "SN2026abc", "2026-10-29T06:00:00Z", "candidate-chunks/abc.json");
  assert.equal(nb.nbformat, 4); assert.equal(nb.nbformat_minor, 5);
  assert.ok(nb.cells.every((c) => typeof c.id === "string" && Array.isArray(c.source)));
  const code = notebookCode(nb);
  assert.ok(code.includes('EVENT_ID = "uuid-1"') && code.includes('DETAIL_CHUNK = "candidate-chunks/abc.json"'));
  assert.ok(notebookCode(makeNotebook("ctas", "u", "n", null)).includes("DETAIL_CHUNK = None"));
  assert.ok(notebookCode(makeNotebook("worldsindex", "object-x", "X", null)).includes('OBJECT_ID = "object-x"'));
  const link = new URL(replLink(nb));
  assert.equal(link.hostname, "jupyterlite.github.io");
  assert.equal(link.searchParams.get("code"), code);
});

// ------------------------------------------------------------------ SQL examples
test("the SQL examples file is well formed", () => {
  const ex = JSON.parse(readFileSync(new URL("../tools/sql/examples.json", import.meta.url)));
  assert.equal(ex.schema, "jm.sql-examples@1");
  assert.ok(ex.examples.length >= 6);
  for (const e of ex.examples) { assert.ok(e.title && /^\s*SELECT/i.test(e.sql), e.title); assert.ok(!/;\s*\S/.test(e.sql), "one statement each"); }
});

console.log(`${passed} visitor-tool checks passed`);

// Checks for the Tonight's sky planner (tonight/). Reference values from astropy 7
// for Laramie, Wyoming (41.3114 N, 105.5911 W) on the night of 2026-10-03
// (Sun crossings without refraction, at -0.833, -12 and -18 degrees).
import assert from "node:assert/strict";
import { readFileSync, existsSync } from "node:fs";
import { gunzipSync } from "node:zlib";
import { nightGrid, visibility, localNoonUtc, airmass, separationDeg, sunPosition, moonPosition, julianDate } from "../tonight/astro.js";
import { ctasTargets, worldsSystems, altitudeChartSvg, SITES } from "../tonight/planner.js";

const minutesApart = (ms, iso) => Math.abs(ms - Date.parse(iso)) / 60000;
let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };

test("local noon in Denver is 18:00 UTC during daylight time", () => {
  assert.equal(new Date(localNoonUtc("2026-10-03", "America/Denver")).toISOString(), "2026-10-03T18:00:00.000Z");
  assert.equal(new Date(localNoonUtc("2026-12-03", "America/Denver")).toISOString(), "2026-12-03T19:00:00.000Z");
  assert.equal(new Date(localNoonUtc("2026-10-03", "UTC")).toISOString(), "2026-10-03T12:00:00.000Z");
});

const grid = nightGrid({ noonUtcMs: localNoonUtc("2026-10-03", "America/Denver"), latDeg: 41.3114, lonDeg: -105.5911, twilightDeg: -12 });

test("sunset, nautical dark and sunrise within 3 minutes of astropy", () => {
  assert.ok(minutesApart(grid.sunset, "2026-10-04T00:40:00Z") <= 3, new Date(grid.sunset).toISOString());
  assert.ok(minutesApart(grid.darkStart, "2026-10-04T01:40:00Z") <= 3, new Date(grid.darkStart).toISOString());
  assert.ok(minutesApart(grid.darkEnd, "2026-10-04T12:02:00Z") <= 3, new Date(grid.darkEnd).toISOString());
  assert.ok(minutesApart(grid.sunrise, "2026-10-04T13:02:00Z") <= 3, new Date(grid.sunrise).toISOString());
  const astro = nightGrid({ noonUtcMs: localNoonUtc("2026-10-03", "America/Denver"), latDeg: 41.3114, lonDeg: -105.5911, twilightDeg: -18 });
  assert.ok(minutesApart(astro.darkStart, "2026-10-04T02:12:00Z") <= 3, new Date(astro.darkStart).toISOString());
  assert.ok(minutesApart(astro.darkEnd, "2026-10-04T11:30:00Z") <= 3, new Date(astro.darkEnd).toISOString());
});

test("Sun and Moon positions match astropy (true equator and equinox of date)", () => {
  const jd = julianDate(Date.parse("2026-10-03T18:00:00Z"));
  const sun = sunPosition(jd), moon = moonPosition(jd);
  assert.ok(separationDeg(sun.ra, sun.dec, 189.6909, -4.1737) < 0.02, JSON.stringify(sun));
  assert.ok(separationDeg(moon.ra, moon.dec, 104.5893, 26.5034) < 0.3, JSON.stringify(moon));
});

test("Vega is up for hours in the evening; a far-southern target never rises", () => {
  const vega = visibility(grid, 279.2347, 38.7837, 41.3114, 30);
  assert.ok(vega.hours > 3 && vega.maxAlt > 60, JSON.stringify(vega));
  const south = visibility(grid, 90, -70, 41.3114, 30);
  assert.equal(south.hours, 0);
  assert.ok(south.maxAlt < 0);
});

test("airmass is 1 at the zenith, about 2 at 30 degrees, and null below the horizon", () => {
  assert.ok(Math.abs(airmass(90) - 1) < 0.001);
  assert.ok(Math.abs(airmass(30) - 1.995) < 0.01);
  assert.equal(airmass(-5), null);
});

test("polar summer has no dark window and no visible targets", () => {
  const g = nightGrid({ noonUtcMs: localNoonUtc("2026-06-21", "UTC"), latDeg: 78.2, lonDeg: 15.6, twilightDeg: -12 });
  assert.equal(g.usableCount, 0);
  const v = visibility(g, 0, 89, 78.2, 30);
  assert.equal(v.hours, 0);
  assert.equal(v.maxAlt, null);
});

test("CTAS rows: only follow-up targets with coordinates, linked to their dossier", () => {
  const summary = {
    candidate_columns: ["event_id", "name", "ra_deg", "dec_deg", "classification", "discovery_survey", "discovery_magnitude", "discovery_time", "ctas_score", "record_role"],
    candidate_rows: [
      ["ev/1", "SN 2026abc", 10, 20, "SN Ia", "ZTF", 18.2, "2026-10-01", 71, "follow-up-target-candidate"],
      ["ev2", "GRB 261001A", 11, 21, null, "Swift", null, "2026-10-01", 40, "localization-region-alert"],
      ["ev3", "AT 2026x", null, 5, null, "ATLAS", 19, "2026-10-01", 30, "follow-up-target-candidate"],
    ],
  };
  const rows = ctasTargets(summary);
  assert.equal(rows.length, 1);
  assert.equal(rows[0].name, "SN 2026abc");
  assert.equal(rows[0].detail, "SN Ia · ZTF");
  assert.equal(rows[0].href, "../ctas.html?event=ev%2F1#dossier");
});

test("WorldsIndex objects are grouped by system; candidates only when asked", () => {
  const index = { objects: [
    { objectId: "a-b", systemId: "sysA", name: "A b", hostName: "A", raDeg: 1, decDeg: 2, normalizedStatus: "CONFIRMED" },
    { objectId: "a-c", systemId: "sysA", name: "A c", hostName: "A", raDeg: 1, decDeg: 2, normalizedStatus: "CONFIRMED" },
    { objectId: "k-1", systemId: "sysK", name: "KOI-1.01", hostName: "KOI-1", raDeg: 3, decDeg: 4, normalizedStatus: "CANDIDATE" },
    { objectId: "fp", systemId: "sysF", name: "FP", hostName: "F", raDeg: 5, decDeg: 6, normalizedStatus: "FALSE_POSITIVE" },
    { objectId: "nopos", systemId: "sysN", name: "N b", hostName: "N", raDeg: null, decDeg: null, normalizedStatus: "CONFIRMED" },
  ] };
  const confirmed = worldsSystems(index);
  assert.deepEqual(confirmed.map((s) => s.name), ["A"]);
  assert.equal(confirmed[0].detail, "2 planets: A b, A c");
  assert.equal(confirmed[0].href, "../worldsindex/?object=a-b&section=object");
  assert.deepEqual(worldsSystems(index, { includeCandidates: true }).map((s) => s.name).sort(), ["A", "KOI-1"]);
});

test("the altitude chart is an accessible SVG with the target and Moon curves", () => {
  const svg = altitudeChartSvg(grid, { name: "Vega <test>", ra: 279.2347, dec: 38.7837 }, 41.3114, 30, "America/Denver");
  assert.match(svg, /<svg [^>]*role="img"/);
  assert.match(svg, /aria-label="Altitude of Vega &lt;test&gt; through the night"/);
  assert.match(svg, /class="target"/);
  assert.match(svg, /class="moon"/);
  assert.ok(!svg.includes("<test>"));
});

test("every preset site has a valid time zone and coordinates", () => {
  for (const s of SITES) {
    assert.ok(Math.abs(s.lat) <= 90 && Math.abs(s.lon) <= 180, s.id);
    assert.doesNotThrow(() => new Intl.DateTimeFormat("en", { timeZone: s.tz }), s.id);
  }
});

// The published catalogs, when this checkout has them (the data branches are overlaid
// in CI): the fields the planner reads must still exist.
if (existsSync("ctas/data/live-summary.json")) {
  test("the live CTAS summary still has the columns the planner reads", () => {
    const summary = JSON.parse(readFileSync("ctas/data/live-summary.json", "utf8"));
    for (const c of ["event_id", "name", "ra_deg", "dec_deg", "classification", "discovery_survey", "discovery_magnitude", "ctas_score", "record_role"]) {
      assert.ok(summary.candidate_columns.includes(c), c);
    }
    assert.ok(ctasTargets(summary).length > 0);
  });
}
if (existsSync("worldsindex/data/catalog-index.json.gz")) {
  test("the WorldsIndex catalog still has the fields the planner reads", () => {
    const index = JSON.parse(gunzipSync(readFileSync("worldsindex/data/catalog-index.json.gz")).toString("utf8"));
    for (const f of ["objectId", "systemId", "name", "hostName", "raDeg", "decDeg", "normalizedStatus"]) assert.ok(f in index.objects[0], f);
    assert.ok(worldsSystems(index).length > 1000);
  });
}
console.log(`${passed} planner checks passed`);

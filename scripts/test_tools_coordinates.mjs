// Checks for the coordinate converter (tools/coordinates/) and the shared astrometry
// module (tools/lib/astrometry.js). The reference values in test_tools_coordinates_fixture.json
// come from astropy (regenerate with scripts/test_tools_coordinates_fixture.py).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  parseAngle, parseCoordinates, formatRA, formatDec, formatHours, galactic, ecliptic, fromGalactic,
  jdFromMs, mjdFromMs, msFromJd, msFromMjd, siderealTime, horizontal, apparentPlace, precess, julianYearToJd,
  constellation, separationDeg, offsetMove, tangentOffset, refractionDeg,
} from "../tools/lib/astrometry.js";
import { parseBatch, toCsv, readUrlTarget } from "../tools/coordinates/coordinates.js";

const fixture = JSON.parse(readFileSync(new URL("./test_tools_coordinates_fixture.json", import.meta.url)));
let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg ?? ""} ${a} vs ${b} (tol ${tol})`);
const worst = {};
const track = (key, v) => { worst[key] = Math.max(worst[key] ?? 0, v); };
const wrapH = (d) => ((d + 12) % 24 + 24) % 24 - 12;

// ------------------------------------------------------------------ parsing and formatting
test("decimal degrees, decimal hours and sexagesimal angles parse", () => {
  near(parseAngle("187.25", "ra").deg, 187.25, 1e-12);
  near(parseAngle("12.5h", "ra").deg, 187.5, 1e-12);
  near(parseAngle("12 30 00", "ra").deg, 187.5, 1e-12);
  near(parseAngle("12:30:00.0", "ra").deg, 187.5, 1e-12);
  near(parseAngle("12h30m00s", "ra").deg, 187.5, 1e-12);
  near(parseAngle("12h 30.5m", "ra").deg, 187.625, 1e-12);
  near(parseAngle("-05 30 00", "dec").deg, -5.5, 1e-12);
  near(parseAngle("−05:30:00", "dec").deg, -5.5, 1e-12);
  near(parseAngle("+41d16m09s", "dec").deg, 41 + 16 / 60 + 9 / 3600, 1e-12);
  near(parseAngle("41°16′09″", "dec").deg, 41 + 16 / 60 + 9 / 3600, 1e-12);
  near(parseAngle("-12.25", "dec").deg, -12.25, 1e-12);
});

test("a negative declination with zero degrees keeps its sign", () => {
  near(parseAngle("-00 30 00", "dec").deg, -0.5, 1e-12);
  near(parseAngle("-0:30", "dec").deg, -0.5, 1e-12);
  assert.equal(formatDec(-0.5, 0), "−00:30:00");
});

test("bad angles are refused with a message", () => {
  for (const [text, kind] of [["24 00 00", "ra"], ["-10", "ra"], ["400", "ra"], ["91", "dec"], ["10 61 00", "dec"],
    ["12.5 30 00", "ra"], ["abc", "dec"], ["", "ra"], ["1 2 3 4", "dec"]]) {
    assert.ok(parseAngle(text, kind).error, `${kind} ${text} should fail`);
  }
});

test("RA and Dec together, in the usual layouts", () => {
  const expect = { ra: 10.684708333, dec: 41.269166667 };
  for (const text of ["00 42 44.33 +41 16 09.0", "00:42:44.33 +41:16:09.0", "00h42m44.33s +41d16m09.0s", "00h42m44.33s+41d16m09.0s",
    "00:42:44.33, 41:16:09.0", "00 42 44.33 41 16 09.0", "10.684708333 41.269166667", "10.684708333,41.269166667"]) {
    const c = parseCoordinates(text);
    assert.ok(!c.error, `${text}: ${c.error}`);
    near(c.ra, expect.ra, 2e-6, text); near(c.dec, expect.dec, 2e-6, text);
  }
  near(parseCoordinates("05 35 17.3 -05 23 28").dec, -(5 + 23 / 60 + 28 / 3600), 1e-9);
  assert.ok(parseCoordinates("00 42 44 41 16").error, "five fields are ambiguous");
});

test("formatting rounds and carries correctly", () => {
  assert.equal(formatRA(187.5), "12:30:00.00");
  assert.equal(formatRA(359.9999999), "00:00:00.00");
  assert.equal(formatRA(10.684708333, 2, "hms"), "00h42m44.33s");
  assert.equal(formatDec(41.269166667), "+41:16:09.0");
  assert.equal(formatDec(-89.99999999, 1), "−90:00:00.0");
  assert.equal(formatDec(41.269166667, 0, "dms"), "+41°16′09″");
  assert.equal(formatHours(1.5, 0), "01:30:00");
  assert.equal(formatRA(359.99999999 , 1), "00:00:00.0");
  assert.equal(formatHours(-1.5, 0), "−01:30:00");
});

test("parse(format(x)) round-trips to 0.01″", () => {
  for (let i = 0; i < 500; i++) {
    const ra = Math.random() * 360, dec = Math.asin(2 * Math.random() - 1) * 180 / Math.PI;
    const c = parseCoordinates(`${formatRA(ra, 4)} ${formatDec(dec, 3).replace("−", "-")}`);
    assert.ok(separationDeg(c.ra, c.dec, ra, dec) * 3600 < 0.01, `${ra} ${dec}`);
  }
});

// ------------------------------------------------------------------ against astropy
const cases = fixture.cases;
test(`fixture has ${cases.length} astropy cases (at least 20)`, () => assert.ok(cases.length >= 20));

test("Galactic l, b within 1e-4° of astropy", () => {
  for (const c of cases) {
    const g = galactic(c.ra, c.dec), sep = separationDeg(g.l, g.b, c.l, c.b);
    track("galactic_deg", sep);
    assert.ok(sep < 1e-4, `${c.ra},${c.dec}: ${JSON.stringify(g)} vs ${c.l},${c.b}`);
    near(g.b, c.b, 1e-4);
    const back = fromGalactic(g.l, g.b);
    assert.ok(separationDeg(back.ra, back.dec, c.ra, c.dec) < 1e-9);
  }
});

test("mean J2000 ecliptic λ, β within 1e-4° of astropy", () => {
  for (const c of cases) {
    const e = ecliptic(c.ra, c.dec), sep = separationDeg(e.lambda, e.beta, c.lambda, c.beta);
    track("ecliptic_deg", sep);
    assert.ok(sep < 1e-4, `${c.ra},${c.dec}: ${JSON.stringify(e)} vs ${c.lambda},${c.beta}`);
    near(e.beta, c.beta, 1e-4);
  }
});

test("JD and MJD match astropy to double precision, and invert", () => {
  for (const c of cases) {
    track("jd_days", Math.abs(jdFromMs(c.unix_ms) - c.jd));
    near(jdFromMs(c.unix_ms), c.jd, 1e-9, c.utc);
    near(mjdFromMs(c.unix_ms), c.mjd, 1e-10, c.utc);
    assert.equal(new Date(c.unix_ms).toISOString().slice(0, 19), c.utc.slice(0, 19));
    near(msFromJd(c.jd), c.unix_ms, 0.1);
    near(msFromMjd(c.mjd), c.unix_ms, 0.01);
  }
  assert.equal(jdFromMs(Date.UTC(2000, 0, 1, 12)), 2451545.0);
  assert.equal(mjdFromMs(Date.UTC(1858, 10, 17)), 0);
});

test("mean and apparent sidereal time within 0.5 s of astropy", () => {
  for (const c of cases) {
    const st = siderealTime(c.unix_ms, c.lon);
    for (const [mine, ref, key] of [[st.gmst, c.gmst_h, "gmst"], [st.gast, c.gast_h, "gast"], [st.lmst, c.lmst_h, "lmst"], [st.last, c.last_h, "last"]]) {
      const ds = Math.abs(wrapH(mine / 15 - ref)) * 3600;
      track(`${key}_s`, ds);
      assert.ok(ds < 0.5, `${key} ${c.utc}: ${ds.toFixed(3)} s`);
    }
  }
});

test("altitude within 0.05° and azimuth within 0.05° of astropy (no refraction)", () => {
  for (const c of cases) {
    const h = horizontal(c.ra, c.dec, c.unix_ms, c.lat, c.lon);
    track("alt_deg", Math.abs(h.alt - c.alt));
    near(h.alt, c.alt, 0.05, `${c.site} ${c.utc}`);
    if (Math.abs(c.alt) < 85 && Math.abs(c.lat) < 89) {
      const daz = Math.abs(((h.az - c.az + 540) % 360) - 180);
      track("az_deg", daz);
      assert.ok(daz < 0.05, `${c.site} az ${h.az} vs ${c.az}`);
    }
  }
});

test("apparent place of date within 2″ of astropy TETE", () => {
  for (const c of cases) {
    const a = apparentPlace(c.ra, c.dec, c.unix_ms), sep = separationDeg(a.ra, a.dec, c.tete_ra, c.tete_dec) * 3600;
    track("apparent_arcsec", sep);
    assert.ok(sep < 2, `${c.utc}: ${sep.toFixed(2)}″`);
  }
});

test("precession from another equinox to J2000 within 0.5″ of astropy FK5", () => {
  for (const c of cases) {
    const p = precess(c.ra, c.dec, julianYearToJd(c.equinox), julianYearToJd(2000));
    const sep = separationDeg(p.ra, p.dec, c.j2000_ra, c.j2000_dec) * 3600;
    track("precession_arcsec", sep);
    assert.ok(sep < 0.5, `J${c.equinox}: ${sep.toFixed(3)}″`);
  }
});

test("constellations match astropy away from boundaries", () => {
  for (const c of cases) {
    if (c.near_boundary) continue;
    assert.equal(constellation(c.ra, c.dec).abbr, c.constellation, `${c.ra},${c.dec}`);
  }
  assert.equal(constellation(279.2347, 38.7837).name, "Lyra");      // Vega
  assert.equal(constellation(88.7929, 7.4071).abbr, "Ori");        // Betelgeuse
  assert.equal(constellation(37.9546, 89.2641).abbr, "UMi");       // Polaris
  assert.equal(constellation(10, -89.5).abbr, "Oct");
});

// ------------------------------------------------------------------ helpers shared with the finder
test("offset-star moves: east and north arcseconds, small-field and exact", () => {
  const m = offsetMove(150, 20, 150 + 30 / 3600 / Math.cos(20 * Math.PI / 180), 20 + 15 / 3600);
  near(m.east, 30, 0.01); near(m.north, 15, 0.01);
  near(m.dRaSeconds, (30 / Math.cos(20 * Math.PI / 180)) / 15, 0.001);
  const w = offsetMove(359.999, 0, 0.001, 0);  // across RA = 0
  near(w.east, 7.2, 0.001); near(w.north, 0, 1e-6);
  const t = tangentOffset(0, 89.9, 180, 89.9);  // across the pole
  near(t.eta, 0.2, 1e-4); near(t.xi, 0, 1e-9);
});

test("refraction (from the airless altitude) is about 29′ at the horizon and 1′ at 45°", () => {
  near(refractionDeg(0) * 60, 29.0, 0.5);
  near(refractionDeg(45) * 60, 0.99, 0.05);
});

// ------------------------------------------------------------------ batch mode and URL params
test("batch lines: names with spaces, sexagesimal, CSV, comments and errors", () => {
  const rows = parseBatch([
    "# my targets",
    "M 31 00 42 44.3 +41 16 09",
    "SN 2026abc  187.25  -12.5",
    "Vega,18:36:56.3,+38:47:01",
    "\tOrion Nebula\t05:35:17.3\t-05:23:28",
    "",
    "broken 12 99 00 +10 00 00",
    "nameonly",
  ].join("\n"));
  assert.equal(rows.length, 6);
  assert.deepEqual(rows.map((r) => r.name), ["M 31", "SN 2026abc", "Vega", "Orion Nebula", "broken", "nameonly"]);
  near(rows[0].ra, 10.68458333, 1e-6); near(rows[1].dec, -12.5, 1e-12); near(rows[2].dec, 38.78361111, 1e-6);
  near(rows[3].ra, 83.82208333, 1e-6);
  assert.ok(rows[4].error && rows[5].error);
  const unnamed = parseBatch("10.5 -20.25");
  assert.equal(unnamed[0].name, "");
  near(unnamed[0].dec, -20.25, 1e-12);
});

test("CSV quotes names and uses ASCII signs", () => {
  const csv = toCsv([{ name: 'A, "quoted"', ra: 10, dec: -0.5 }], { ms: Date.UTC(2026, 9, 3, 6), site: { lat: 41.1, lon: -105.9 } });
  const [header, line] = csv.trim().split("\r\n");
  assert.ok(header.startsWith("name,ra_hms,dec_dms,ra_deg,dec_deg,l_deg,b_deg,ecl_lon_deg,ecl_lat_deg,constellation"), header);
  assert.ok(line.startsWith('"A, ""quoted""",00:40:00.00,-00:30:00.0,10.000000,-0.500000,'), line);
  assert.ok(!csv.includes("−"));
});

test("URL parameters ?ra=&dec=&name= are read in any supported format", () => {
  const t = readUrlTarget("?ra=00:42:44.3&dec=%2B41:16:09&name=M%2031");
  assert.equal(t.name, "M 31"); assert.equal(t.ra, "00:42:44.3"); assert.equal(t.dec, "+41:16:09");
  assert.equal(readUrlTarget("?ra=10.68&dec=41.27").name, "");
  assert.equal(readUrlTarget(""), null);
});

console.log("\nlargest differences from astropy:");
for (const [k, v] of Object.entries(worst)) console.log(`  ${k.padEnd(18)} ${v.toExponential(2)}`);
console.log(`\n${passed} coordinate checks passed`);

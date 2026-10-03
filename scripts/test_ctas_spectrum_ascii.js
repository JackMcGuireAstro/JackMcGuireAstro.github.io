// Tests for ctas/spectrum-ascii.js: reading public ASCII spectrum files (TNS / WISeREP
// dialects in tests/fixtures/spectra), plot arithmetic, and the plain-words messages.
// No network: the relay fetch is exercised with a stubbed fetch at the end.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const spectrum = require("../ctas/spectrum-ascii.js");

const FIXTURES = path.join(__dirname, "..", "tests", "fixtures", "spectra");
const read = (name) => fs.readFileSync(path.join(FIXTURES, name), "utf8");
const close = (a, b, tolerance, message) => assert.ok(Math.abs(a - b) <= tolerance, `${message}: ${a} vs ${b}`);
const range = (parsed) => [parsed.points[0].wavelength, parsed.points[parsed.points.length - 1].wavelength];

// --- realistic fixtures ------------------------------------------------------------
{ // SEDM (ZTF): long '#' header, a flux-unit line ending in /A, three unnamed columns
  const parsed = spectrum.parse(read("sedm-ztf.ascii"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.points.length, 137);
  assert.equal(parsed.unit, "Å");
  assert.equal(parsed.unitBasis, "values", "the flux-unit line (erg/s/cm2/A) is not read as the wavelength unit");
  assert.equal(parsed.columns, 3);
  assert.equal(parsed.hasError, true, "a three-column file whose third column looks like an error keeps it");
  assert.deepEqual(range(parsed), [3780, 9220]);
  assert.equal(parsed.comments, 11);
  assert.ok(parsed.points[0].error > 0 && parsed.points[0].error < parsed.points[0].flux);
}
{ // ePESSTO+ EFOSC2: two bare columns, no header
  const parsed = spectrum.parse(read("efosc2-epessto.asci"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.columns, 2);
  assert.equal(parsed.hasError, false);
  assert.equal(parsed.unit, "Å");
  close(parsed.points[0].wavelength, 3650.04833984, 1e-6, "first wavelength kept exactly");
  assert.ok(parsed.points.every((p) => p.flux > 0 && p.flux < 1e-15));
}
{ // LT SPRAT: tab separated, CRLF line endings, commented header with [A]
  const parsed = spectrum.parse(read("sprat-lt.txt"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.points.length, 133);
  assert.equal(parsed.unit, "Å");
  assert.equal(parsed.unitBasis, "header");
  assert.deepEqual(parsed.columnNames, ["wavelength[A]", "flux[normalised]"]);
  assert.deepEqual(range(parsed), [4020, 7980]);
}
{ // BlackGEM / Mookodi CSV: header row, NaN rows skipped, an empty error tolerated
  const parsed = spectrum.parse(read("mookodi-blackgem.csv"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.points.length, 99, "101 rows minus two NaN rows");
  assert.equal(parsed.skipped, 2);
  assert.deepEqual(parsed.columnNames, ["wavelength", "flux", "flux_err"]);
  assert.equal(parsed.points.find((p) => p.wavelength === 4250).error, null, "the row with an empty error has no error");
  assert.ok(parsed.points.find((p) => p.wavelength === 3800).error > 0);
}
{ // nm declared in a '!' header, descending order, Fortran D exponents, an Inf placeholder
  const parsed = spectrum.parse(read("longslit-nm.dat"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.unit, "nm");
  assert.equal(parsed.unitBasis, "header");
  assert.equal(parsed.reordered, true);
  assert.equal(parsed.skipped, 1, "the Inf flux row is ignored");
  assert.equal(parsed.points.length, 100);
  assert.deepEqual(range(parsed), [3500, 9500], "nm converted to Angstrom and sorted ascending");
  assert.ok(parsed.points.every((p, i, all) => i === 0 || p.wavelength > all[i - 1].wavelength));
  close(parsed.points[0].flux / parsed.points[0].error, 1 / 0.03, 3, "D exponents read as powers of ten");
}
{ // named columns with a variance: the error is its square root
  const parsed = spectrum.parse(read("fors2-variance.txt"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.errorFromVariance, true);
  assert.ok(parsed.points.every((p) => Math.abs(p.error / p.flux - 0.05) < 0.02), "sqrt(var) is about 5% of the flux");
}
{ // near-infrared in microns with no unit written anywhere: judged from the values
  const parsed = spectrum.parse(read("nir-microns.txt"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.unit, "µm");
  assert.equal(parsed.unitBasis, "values");
  close(parsed.points[0].wavelength, 9500, 1e-6, "0.95 µm");
  close(parsed.points[parsed.points.length - 1].wavelength, 24500, 1e-6, "2.45 µm");
}
{ // four unnamed columns: the third is sky, not an error, so no error is claimed
  const parsed = spectrum.parse(read("four-columns.asci"));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.columns, 4);
  assert.equal(parsed.hasError, false);
  assert.ok(parsed.points[0].flux < 1e-14, "flux is the second column, not the sky");
}

// --- small inline dialects ----------------------------------------------------------
const rows = (n, f) => Array.from({length: n}, (_, i) => f(i)).join("\n");
{ // whitespace runs, a bare header line and a BOM; a third column is an error only when
  // its name says so (named) or it looks like one (unnamed)
  const parsed = spectrum.parse("﻿Wave Flux Err\n" + rows(20, (i) => `${5000 + 10 * i}    ${1 + i / 100}   ${1e6}`));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.points.length, 20);
  assert.equal(parsed.hasError, true, "a column named as an error is trusted as the file says");
  assert.equal(spectrum.parse("Wave Flux Sky\n" + rows(20, (i) => `${5000 + 10 * i} 1 0.1`)).hasError, false, "a named non-error column is not an error");
}
{
  const parsed = spectrum.parse(rows(20, (i) => `${5000 + 10 * i} ${1 + i / 100} ${1e6}`));
  assert.equal(parsed.hasError, false, "an unnamed third column far larger than the flux is not an error");
}
{ // wavelength given in nm by the values alone (no header)
  const parsed = spectrum.parse(rows(30, (i) => `${400 + 10 * i} ${2 + Math.sin(i)}`));
  assert.equal(parsed.unit, "nm");
  assert.equal(parsed.points[0].wavelength, 4000);
}
{ // a header that says nm but values that are plainly Angstrom: the values win
  const parsed = spectrum.parse("# wavelength (nm)\n" + rows(30, (i) => `${4000 + 100 * i} 1`));
  assert.equal(parsed.unit, "Å");
  assert.equal(parsed.unitBasis, "values");
}
{ // log10 wavelength declared in the header
  const parsed = spectrum.parse("# log10(wavelength/A) flux\n" + rows(20, (i) => `${(3.6 + i * 0.01).toFixed(2)} 1`));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.logWavelength, true);
  close(parsed.points[0].wavelength, Math.pow(10, 3.6), 1e-6, "10^3.6 A");
}
{ // quoted CSV values and a units row after the header
  const parsed = spectrum.parse('"wavelength","flux"\n"Angstrom","erg/s/cm2/A"\n' + rows(12, (i) => `"${6000 + i}","${1e-16}"`));
  assert.equal(parsed.ok, true);
  assert.equal(parsed.points.length, 12);
  assert.equal(parsed.headers, 2);
}

// --- failures in plain words ---------------------------------------------------------
assert.deepEqual(spectrum.parse(""), {ok: false, reason: "empty"});
assert.equal(spectrum.parse("<!DOCTYPE html><html><head><title>TNS login</title></head><body>Sign in</body></html>").reason, "html");
assert.equal(spectrum.parse("SIMPLE  =                    T / conforms to FITS standard\nBITPIX  = -32").reason, "fits");
assert.equal(spectrum.parse("PK\u0003\u0004\u0000\u0000binary").reason, "binary");
assert.deepEqual(spectrum.parse("4000 1\n4001 2\n4002 3"), {ok: false, reason: "too-few", count: 3});
assert.equal(spectrum.parse("# no data here\nwavelength flux\n").reason, "no-numbers");
assert.equal(spectrum.parse(rows(20, (i) => `${1e7 + i} 1`)).reason, "implausible");
for (const reason of ["html", "fits", "binary", "empty", "too-few", "no-numbers", "implausible"]) {
  const message = spectrum.failureMessage({reason, count: 3});
  assert.ok(message.length > 30 && !/undefined|NaN/.test(message), reason);
}
assert.match(spectrum.failureMessage({kind: "no-relay"}), /relay is not configured/);
assert.match(spectrum.failureMessage({kind: "network"}), /could not be reached/);
assert.match(spectrum.failureMessage({kind: "timeout"}), /did not answer in time/);
assert.match(spectrum.failureMessage({kind: "http", status: 403}), /not public/);
assert.match(spectrum.failureMessage({kind: "http", status: 404}), /no file at this address/);
assert.match(spectrum.failureMessage({kind: "http", status: 400}), /does not accept this file address/);
assert.match(spectrum.failureMessage({kind: "http", status: 413}), /40 MB/);
assert.match(spectrum.failureMessage({kind: "http", status: 502}), /HTTP 502/);
assert.match(spectrum.failureMessage({reason: "fits"}), /isn’t supported here yet/);

// --- which TNS files can be plotted ---------------------------------------------------
const tns = "https://www.wis-tns.org/system/files/uploaded/";
assert.deepEqual(spectrum.tnsFile(tns + "ePESSTO%2B/tns_2021zon_2021-10-04_EFOSC2-NTT_ePESSTO%2B.asci"),
  {url: tns + "ePESSTO%2B/tns_2021zon_2021-10-04_EFOSC2-NTT_ePESSTO%2B.asci", fileName: "tns_2021zon_2021-10-04_EFOSC2-NTT_ePESSTO+.asci", format: "ascii"});
assert.equal(spectrum.tnsFile(tns + "ZTF/tns_2026abc_P60_SEDM_ZTF.fits").format, "fits");
assert.equal(spectrum.tnsFile(tns + "ZTF/tns_2026abc.fits.gz").format, "fits");
assert.equal(spectrum.tnsFile(tns + "ZTF/tns_2026abc.tar.gz").format, "archive");
assert.equal(spectrum.tnsFile(tns + "Supernova%20Alliance/x.spec").format, "ascii");
assert.equal(spectrum.tnsFile("https://www.wiserep.org/search/spectra?name=2026wpd"), null, "only TNS uploads go through the relay");
assert.equal(spectrum.tnsFile("http://www.wis-tns.org/system/files/uploaded/a.txt"), null);
assert.equal(spectrum.tnsFile(tns + "../../etc/passwd?x=1"), null);

// --- plot arithmetic -----------------------------------------------------------------
{
  const points = Array.from({length: 11}, (_, i) => ({wavelength: 5000 + i, flux: 2e-16 * (i + 1), error: 1e-17}));
  const normal = spectrum.normalise(points);
  close(normal.scale, 1.2e-15, 1e-25, "median flux");
  close(normal.points[5].flux, 1, 1e-12, "median point becomes 1");
  close(normal.points[0].error, 1e-17 / 1.2e-15, 1e-15, "errors scale with the flux");
  const noisy = spectrum.normalise([-3, -1, 1, 2, -2, 4].map((f, i) => ({wavelength: 5000 + i, flux: f, error: null})));
  close(noisy.scale, 2, 1e-12, "mostly negative flux falls back to the median absolute flux");
  const rest = spectrum.toFrame([{wavelength: 6563 * 1.05, flux: 1, error: null}], 0.05, "rest");
  close(rest[0].wavelength, 6563, 1e-9, "rest frame divides by 1+z");
  assert.equal(spectrum.toFrame(points, NaN, "rest"), points, "unknown redshift leaves the axis alone");
}
{
  const many = Array.from({length: 10000}, (_, i) => ({wavelength: 3000 + i, flux: i === 5003 ? 50 : Math.sin(i / 50), error: null}));
  const kept = spectrum.decimate(many, 1000);
  assert.ok(kept.length <= 1000 && kept.length >= 900);
  assert.ok(kept.some((p) => p.flux === 50), "a one-pixel spike survives decimation");
  assert.ok(kept.every((p, i) => i === 0 || p.wavelength >= kept[i - 1].wavelength), "order preserved");
  assert.equal(spectrum.bin(many, 200).length, 200);
  const [low, high] = spectrum.robustRange(many.map((p) => p.flux));
  assert.ok(high < 5, "the robust range ignores the single spike");
  assert.ok(low < -0.99 && high > 0.99);
}
assert.deepEqual(spectrum.ticks(3650, 9245, 6), [4000, 5000, 6000, 7000, 8000, 9000]);
assert.deepEqual(spectrum.ticks(0, 1.2, 4), [0, 0.5, 1]);
{
  const observed = spectrum.lineMarkers(3600, 9300, 0.044, "observed");
  const ha = observed.find((m) => m.id === "h-alpha");
  close(ha.positions[0], 6563 * 1.044, 1e-9, "observed-frame marker shifted by 1+z");
  const rest = spectrum.lineMarkers(3600, 9300, 0.044, "rest");
  assert.equal(rest.find((m) => m.id === "h-alpha").positions[0], 6563);
  assert.equal(rest.find((m) => m.id === "ca-nir").positions.length, 3);
  assert.deepEqual(spectrum.lineMarkers(4000, 5000, 0, "observed").map((m) => m.id), ["h-gamma", "h-beta"]);
  const labels = ["He I", "Na I D"].map((label, i) => ({label, positions: [5876 + 14 * i]}));
  assert.deepEqual(spectrum.labelRows(labels, (w) => w / 10, 30).map((l) => l.row), [0, 1], "neighbouring labels go to different rows");
  const edge = spectrum.labelRows([{label: "O I", positions: [7774]}, {label: "Ca II NIR", positions: [8498]}], (w) => w / 10, 4, 6, 830);
  assert.equal(edge[1].x, 830 - 9 * 6, "a label near the right edge is pulled left to fit");
  assert.equal(edge[1].row, 1, "and then moves to another row instead of covering its neighbour");
  const ids = spectrum.LINES.map((line) => line.id);
  assert.equal(new Set(ids).size, ids.length);
  for (const rest of [6563, 4861, 4340, 5876, 6678, 6355, 5454, 5640, 5169, 3934, 3969, 8498, 8542, 8662, 7774, 5890, 5896]) {
    assert.ok(spectrum.LINES.some((line) => line.rest.includes(rest)), `line list includes ${rest} Å`);
  }
}

// --- the relay fetch used by the "Plot spectrum" control (stubbed network) -----------
(async () => {
  const live = require("../ctas/live-sources.js");
  const calls = [];
  let answer = {status: 200, body: read("sprat-lt.txt")};
  global.fetch = async (url) => {
    calls.push(String(url));
    if (String(url) === "live-config.json") return new Response(JSON.stringify({relay: "https://relay.example/"}), {status: 200});
    return new Response(answer.body, {status: answer.status});
  };
  const file = tns + "OxQUB/tns_2026wpd_2026-08-04_LT_SPRAT_OxQUB.txt";
  const fetched = await live.fetchViaRelay(file);
  assert.equal(calls[1], "https://relay.example/?url=" + encodeURIComponent(file));
  assert.equal(spectrum.parse(fetched.text).points.length, 133);
  answer = {status: 404, body: "not found"};
  await assert.rejects(live.fetchViaRelay(file), (error) => error.kind === "http" && error.status === 404);
  global.fetch = async (url) => {
    if (String(url) === "live-config.json") return new Response("{}", {status: 200});
    throw new TypeError("Failed to fetch");
  };
  // the config is read once per page, so a relay-less page is a fresh module instance
  delete require.cache[require.resolve("../ctas/live-sources.js")];
  const fresh = require("../ctas/live-sources.js");
  await assert.rejects(fresh.fetchViaRelay(file), (error) => error.kind === "no-relay");
  // the older live-panel parser now reads the same dialects
  assert.equal(fresh.parseAsciiSpectrum(read("longslit-nm.dat")).length, 100);
  console.log("Spectrum ASCII parsing (8 fixture dialects), units, errors, markers, plot arithmetic and relay fetch passed.");
})().catch((error) => { console.error(error); process.exit(1); });

// Checks for the exposure-time calculator (tools/exposure/ccd.js): the CCD equation against
// hand-computed cases, its limits, and the inversions (time for a S/N, limiting magnitude).
import assert from "node:assert/strict";
import { FILTERS, SKY, TELESCOPES, rates, snrFor, timeFor, sourceFor, magnitudeFor, noiseBudget, peakPixel, solve, erf, FWHM_PER_SIGMA } from "../tools/exposure/ccd.js";
import { chartSvg, readParams } from "../tools/exposure/exposure.js";

let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };
const rel = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol * Math.abs(b), `${msg ?? ""} ${a} vs ${b} (rel ${tol})`);
const near = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg ?? ""} ${a} vs ${b} (tol ${tol})`);
const V = FILTERS.find((f) => f.id === "V");
const base = { diameter: 1, obstruction: 0, zeroPoint: V.zeroPoint, throughput: 0.5, extinction: 0.2, airmass: 1.5, sky: 21.8,
  plateScale: 0.5, seeing: 1.5, apertureFactor: 1, dark: 0.1, readNoise: 5, gain: 2, fullWell: 100000, mag: 15 };

test("the CCD equation for one hand-computed case", () => {
  // S = 1000 e/s, t = 10 s, n = 50 px, B = 10 e/s/px, D = 0.1 e/s/px, R = 5 e:
  // signal 10000; variance 10000 + 50·(100 + 1 + 25) = 16300; S/N = 10000/√16300 = 78.3260
  const r = { npix: 50, sky: 10, dark: 0.1, readNoise: 5 };
  near(snrFor(1000, 10, r), 78.32604, 1e-4);
});

test("bright star, no sky/dark/read noise: S/N = √(S·t)", () => {
  const r = { npix: 30, sky: 0, dark: 0, readNoise: 0 };
  for (const [S, t] of [[100, 1], [5000, 30], [2.5e5, 0.1]]) rel(snrFor(S, t, r), Math.sqrt(S * t), 1e-12);
  // and nearly so when the star swamps realistic noise
  const r2 = { npix: 30, sky: 1, dark: 0.01, readNoise: 5 };
  rel(snrFor(1e6, 10, r2), Math.sqrt(1e7), 1e-4);
});

test("sky-limited faint source: S/N ≈ S·√t / √(n·B)", () => {
  const r = { npix: 100, sky: 1000, dark: 0, readNoise: 0 };
  for (const t of [1, 60, 3600]) {
    rel(snrFor(1, t, r), Math.sqrt(t) / Math.sqrt(100 * 1000), 1e-5);
    rel(snrFor(1, t, r), t / Math.sqrt(t + 1e5 * t), 1e-12);
  }
});

test("read-noise-limited short exposure: S/N ≈ S·t / (R·√n)", () => {
  const r = { npix: 40, sky: 0, dark: 0, readNoise: 10 };
  rel(snrFor(0.5, 2, r), 1 / Math.sqrt(1 + 40 * 100), 1e-12);
  rel(snrFor(0.5, 2, r), 1 / (10 * Math.sqrt(40)), 2e-4);
});

test("rates from magnitude: area × zero point × throughput × extinction × enclosed light", () => {
  const r = rates(base);
  rel(r.area, Math.PI / 4 * 100 * 100, 1e-12);
  rel(V.zeroPoint, 996 * 890, 1e-12);
  near(r.enclosed, 15 / 16, 1e-12, "a radius of one FWHM holds 15/16 of a Gaussian");
  const expectSource = (Math.PI / 4 * 1e4) * 996 * 890 * 0.5 * 10 ** (-0.4 * (15 + 0.2 * 1.5)) * (15 / 16);
  rel(r.source, expectSource, 1e-12);
  near(r.source, 2475.6, 0.1);
  const expectSky = (Math.PI / 4 * 1e4) * 996 * 890 * 0.5 * 10 ** (-0.4 * 21.8) * 0.25;
  rel(r.sky, expectSky, 1e-12);
  near(r.sky, 1.6583, 1e-3);
  rel(r.npix, Math.PI * 1.5 ** 2 / 0.25, 1e-12);
  // a central obstruction removes its share of the area
  rel(rates({ ...base, obstruction: 0.5 }).area, r.area * 0.75, 1e-12);
});

test("magnitudes: 5 mag fainter is 100× fainter; each airmass costs k", () => {
  rel(rates({ ...base, mag: 20 }).source * 100, rates(base).source, 1e-12);
  rel(rates({ ...base, airmass: 2.5 }).source, rates(base).source * 10 ** (-0.4 * 0.2), 1e-12);
});

test("AB zero points: f_ν = 3631 Jy gives 5.48e6·Δλ/λ photons s⁻¹ cm⁻²", () => {
  const g = FILTERS.find((f) => f.id === "g");
  rel(g.zeroPoint, 5.4799e6 * 1379 / 4825, 1e-4);
});

test("time for a S/N inverts the CCD equation (round trip)", () => {
  for (let i = 0; i < 300; i++) {
    const r = { npix: 5 + Math.random() * 200, sky: Math.random() * 50, dark: Math.random() * 0.5, readNoise: Math.random() * 15 };
    const S = 10 ** (Math.random() * 6 - 1), t = 10 ** (Math.random() * 4 - 1);
    const snr = snrFor(S, t, r);
    rel(timeFor(S, snr, r), t, 1e-8, JSON.stringify({ r, S, t }));
  }
});

test("limiting magnitude inverts too: the limiting star reaches the S/N in the time", () => {
  for (const mode of ["dark", "bright"]) {
    const p = { ...base, sky: SKY.V[mode], exptime: 300, snr: 5 };
    const out = solve("maglim", p);
    const back = solve("snr", { ...p, mag: out.mag });
    rel(back.snr, 5, 1e-9);
    rel(solve("time", { ...p, mag: out.mag }).t, 300, 1e-9);
  }
  const r = rates({ ...base, mag: undefined });
  rel(magnitudeFor(sourceFor(60, 10, r), r, base), solve("maglim", { ...base, exptime: 60, snr: 10 }).mag, 1e-12);
});

test("a darker sky and a bigger telescope go deeper", () => {
  const p = { ...base, exptime: 300, snr: 5 };
  assert.ok(solve("maglim", { ...p, sky: SKY.V.dark }).mag > solve("maglim", { ...p, sky: SKY.V.bright }).mag);
  const wiro = TELESCOPES.find((t) => t.id === "wiro"), rbo = TELESCOPES.find((t) => t.id === "rbo");
  assert.ok(solve("maglim", { ...p, diameter: wiro.diameter, obstruction: wiro.obstruction }).mag >
            solve("maglim", { ...p, diameter: rbo.diameter, obstruction: rbo.obstruction }).mag);
  // photon-limited: twice the diameter (four times the area) needs a quarter of the time
  const bright = { ...base, mag: 8, sky: 40, dark: 0, readNoise: 0, snr: 300 };
  rel(solve("time", { ...bright, diameter: 2 }).t * 4, solve("time", bright).t, 1e-9);
});

test("noise budget fractions add to one and name the dominant term", () => {
  const r = { npix: 50, sky: 10, dark: 0.1, readNoise: 5 };
  const b = noiseBudget(1000, 10, r);
  near(b.fractions.source + b.fractions.sky + b.fractions.dark + b.fractions.read, 1, 1e-12);
  assert.equal(b.dominant, "source");
  assert.equal(noiseBudget(0.1, 1, r).dominant, "read");
  assert.equal(noiseBudget(0.1, 3600, r).dominant, "sky");
});

test("peak pixel: erf is accurate and a bright star saturates", () => {
  near(erf(0.5), 0.5204998778, 2e-7); near(erf(-1.2), -0.9103139782, 2e-7); near(erf(3), 0.9999779095, 2e-7);
  near(FWHM_PER_SIGMA, 2.354820045, 1e-9);
  const r = rates({ ...base, mag: 10 });
  const pk = peakPixel(60, r, base);
  assert.ok(pk.saturated, JSON.stringify(pk));
  assert.ok(!peakPixel(60, rates({ ...base, mag: 18 }), base).saturated);
  const sigma = 1.5 / FWHM_PER_SIGMA;  // small pixels: fraction ≈ p²/(2πσ²)
  rel(rates({ ...base, plateScale: 0.05 }).peakFraction, 0.05 ** 2 / (2 * Math.PI * sigma * sigma), 1e-3);
});

test("presets are complete and positive", () => {
  assert.equal(FILTERS.length, 10);
  for (const t of TELESCOPES) {
    assert.ok(t.placeholder, `${t.id} values are placeholders until checked`);
    for (const f of FILTERS) {
      assert.ok(t.throughput[f.id] > 0 && t.throughput[f.id] < 1, `${t.id} ${f.id}`);
      assert.ok(t.extinction[f.id] > 0 && t.extinction[f.id] < 1, `${t.id} ${f.id}`);
      assert.ok(SKY[f.id].dark > SKY[f.id].grey && SKY[f.id].grey > SKY[f.id].bright, f.id);
    }
  }
  assert.deepEqual(TELESCOPES.map((t) => t.diameter), [2.3, 0.6]);
});

test("the S/N curve is an SVG with the chosen point marked", () => {
  const out = solve("time", { ...base, snr: 100 });
  const svg = chartSvg(out);
  assert.ok(svg.startsWith("<svg") && svg.includes('class="curve"') && svg.includes('class="marker"') && svg.includes('class="goal"'));
  assert.ok(/aria-label="[^"]*S\/N[^"]*"/.test(svg));
});

test("readParams turns form strings into numbers and flags bad ones", () => {
  const ok = readParams({ diameter: "2.3", obstruction: "0.6", zeroPoint: "886440", throughput: "0.35", extinction: "0.13", airmass: "1.2",
    sky: "21.8", plateScale: "0.5", seeing: "1.5", apertureFactor: "1", dark: "0.002", readNoise: "5", gain: "2", fullWell: "100000",
    mag: "18", exptime: "300", snr: "50" }, "snr");
  assert.equal(ok.errors.length, 0); assert.equal(ok.params.diameter, 2.3);
  const bad = readParams({ ...ok.raw, airmass: "0.5", obstruction: "3", throughput: "1.5" }, "snr");
  assert.deepEqual(bad.errors.map((e) => e.field).sort(), ["airmass", "obstruction", "throughput"]);
});

console.log(`\n${passed} exposure checks passed`);

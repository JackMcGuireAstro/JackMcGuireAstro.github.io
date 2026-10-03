// Tests for ctas/lightcurve-summary.js with synthetic light curves whose answers are known.
"use strict";
const assert = require("node:assert/strict");
const lc = require("../ctas/lightcurve-summary.js");

const DISCOVERY = "2026-08-01T00:00:00Z";
const MJD0 = 61253; // 2026-08-01
const close = (a, b, tolerance, message) => assert.ok(Math.abs(a - b) <= tolerance, `${message}: ${a} vs ${b}`);
// r: rises 0.2 mag/day from 19.0 at day 0 to a 17.0 peak at day 10, then fades 0.05 mag/day
const rModel = (day) => (day <= 10 ? 19 - 0.2 * day : 17 + 0.05 * (day - 10));
const row = (day, band, magnitude, extra) => Object.assign({mjd: MJD0 + day, band, magnitude, magnitude_error: 0.05, detection: 1,
  photometry_method: "difference-psf", provider: "rubin-fink", magnitude_system: "AB"}, extra || {});

// --- a well-sampled supernova -----------------------------------------------------------
const rows = [];
for (let day = 0; day <= 30; day += 2) rows.push(row(day, "r", rModel(day)));
rows.push(row(1, "r", rModel(1)));                                   // r has the most points
for (let day = 0; day <= 30; day += 2) rows.push(row(day + 0.05, "g", rModel(day + 0.05) - 0.2));
rows.push(row(-5, "r", null, {detection: 0, limiting_magnitude: 20.5, magnitude_error: null}));
rows.push(row(-3, "r", null, {detection: 0, limiting_magnitude: 20.4, magnitude_error: null}));
rows.push(row(4, "r", rModel(4) + 0.01, {provider: "rubin-lasair"}));  // same alert through a second broker
rows.push(row(6, "r", 15.0, {superseded: 1}));                       // superseded revision: ignored
for (let day = 3; day <= 15; day += 4) rows.push(row(day, "o", rModel(day) + 0.1, {photometry_method: "forced-psf", provider: "atlas", flux: 100, flux_unit: "uJy"}));
rows.push(row(20, "c", null, {magnitude: undefined, flux: 100, flux_unit: "uJy", flux_error: 10, photometry_method: "forced-psf"})); // flux only: 18.9 mag
rows.push(row(21, "c", null, {magnitude: undefined, flux: -40, flux_unit: "uJy", flux_error: 10, photometry_method: "forced-psf"})); // negative flux: not drawn
rows.push({band: "r", magnitude: 18, detection: 1});                // no time: not drawn

const summary = lc.summarise(rows, {discovery: DISCOVERY});
assert.equal(summary.reference.basis, "discovery");
assert.equal(summary.reference.iso, "2026-08-01T00:00:00.000Z");
assert.equal(summary.counts.duplicates, 1, "the second broker's copy counts once");
assert.equal(summary.counts.superseded, 1);
assert.equal(summary.counts.unplottable, 2, "negative flux and missing time");
assert.equal(summary.counts.converted, 1);
assert.equal(summary.counts.limits, 2);
assert.equal(summary.counts.forced, 5);
assert.deepEqual(summary.bands.map((b) => b.band), ["r", "g", "o", "c"], "ordered by number of detections");
const r = summary.bands[0], g = summary.bands[1];
assert.equal(r.detections, 17);
assert.equal(r.limits, 2);
close(r.peak.magnitude, 17.0, 1e-9, "r peak");
close(r.peak.days, 10, 1e-6, "r peak day");
assert.equal(r.peak.iso, "2026-08-11T00:00:00.000Z");
close(g.peak.magnitude, rModel(10.05) - 0.2, 1e-9, "g peak");
close(g.peak.days, 10.05, 1e-6, "g peak day");
close(summary.bands[3].peak.magnitude, 18.9, 1e-9, "100 uJy is AB 18.9");
close(summary.peak.magnitude, rModel(10.05) - 0.2, 1e-9, "overall peak is the brightest detection in any band");
assert.equal(summary.peak.band, "g");

assert.equal(summary.rise.band, "r");
close(summary.rise.days, 10, 1e-6, "first r detection (day 0) to r peak (day 10)");
assert.equal(summary.rise.peakIsFirst, false);
assert.equal(summary.rise.peakIsLast, false);
close(summary.rise.lastLimitBefore.daysBeforeFirst, 3, 1e-6, "last r limit three days before the first r detection");
assert.equal(summary.rise.lastLimitBefore.magnitude, 20.4);

const rDecline = summary.decline.find((d) => d.band === "r"), gDecline = summary.decline.find((d) => d.band === "g");
close(rDecline.rate, 0.05, 1e-9, "r fades 0.05 mag/day");
assert.equal(rDecline.points, 8, "days 10-24 inside the 15-day window");
close(rDecline.spanDays, 14, 1e-6, "span");
close(rDecline.rateError, 0, 1e-9, "exact line");
close(gDecline.rate, 0.05, 1e-9, "g fades at the same rate");
assert.equal(summary.decline.find((d) => d.band === "o"), undefined, "o has fewer than three points after its peak");

close(summary.colour.value, -0.2 + 0.05 * 0.05, 1e-9, "g-r from the pair 0.05 d apart nearest the r peak");
close(summary.colour.separationDays, 0.05, 1e-6, "pair separation");
close(summary.colour.offsetDays, 0.025, 1e-6, "pair midpoint relative to the r peak");
assert.equal(summary.colour.referenceBand, "r");
close(summary.colour.error, Math.sqrt(2) * 0.05, 1e-9, "errors added in quadrature");

// --- points for the plot ------------------------------------------------------------------
const prepared = lc.toPoints(rows, {discovery: DISCOVERY});
assert.equal(prepared.points.length, rows.length - 3, "superseded, negative-flux and timeless rows are not drawn (broker duplicates are)");
assert.ok(prepared.points.every((p, i, all) => i === 0 || p.time >= all[i - 1].time), "time ordered");
const limit = prepared.points.find((p) => p.kind === "limit");
assert.equal(limit.magnitude, 20.5);
close(limit.days, -5, 1e-6, "pre-discovery limits have negative days");
assert.ok(prepared.points.filter((p) => p.band === "o").every((p) => p.forced));
assert.equal(prepared.points.find((p) => p.band === "c").converted, true);
close(prepared.points.find((p) => p.band === "c").error, 1.0857 * 0.1, 1e-9, "flux error to magnitude error");

// --- edge cases ---------------------------------------------------------------------------
{ // declining from the first detection: the rise was not observed
  const fading = [0, 1, 2, 4, 6].map((day) => row(day, "r", 17 + 0.1 * day));
  const s = lc.summarise(fading, {discovery: DISCOVERY});
  assert.equal(s.rise.peakIsFirst, true);
  assert.equal(s.rise.days, 0);
  close(s.decline[0].rate, 0.1, 1e-9, "decline from the first point");
  assert.equal(s.colour, null, "no g band, no colour");
}
{ // a single spurious bright point (bad subtraction) is not the peak; it is reported as skipped
  const spiky = [0, 1, 2, 4, 6, 8].map((day) => row(day, "o", 17 + 0.1 * day, {photometry_method: "forced-psf"})).concat([row(30, "o", 13.1)]);
  const s = lc.summarise(spiky, {discovery: DISCOVERY});
  const o = s.bands[0];
  close(o.peak.magnitude, 17.0, 1e-9, "the brightest point with a neighbour");
  assert.equal(o.peak.confirmed, true);
  close(o.unconfirmedBrighter.magnitude, 13.1, 1e-9, "the lone point is named, not used");
  assert.equal(s.rise.peakIsFirst, true);
  close(s.peak.magnitude, 17.0, 1e-9, "overall peak skips it too");
  // a band with one detection still gets a peak, marked unconfirmed
  const lone = lc.summarise([row(0, "g", 18.2), row(0, "r", 18.0), row(1, "r", 18.1)], {discovery: DISCOVERY});
  const g = lone.bands.find((band) => band.band === "g");
  assert.equal(g.peak.magnitude, 18.2);
  assert.equal(g.peak.confirmed, false);
  assert.equal(g.unconfirmedBrighter, null);
}
{ // several exposures on the discovery night, the brightest not the first: still "at peak"
  const night = [0, 0.01, 0.02, 0.03, 3, 6].map((day, i) => row(day, "o", [17.3, 17.2, 17.25, 17.3, 17.6, 17.9][i], {photometry_method: "forced-psf"}));
  const s = lc.summarise(night, {discovery: DISCOVERY});
  assert.equal(s.rise.peakIsFirst, true);
  close(s.rise.days, 0.01, 1e-6, "minutes after the first exposure");
}
{ // still rising at the last detection: no decline
  const rising = [0, 2, 4].map((day) => row(day, "g", 19 - 0.3 * day));
  const s = lc.summarise(rising, {discovery: DISCOVERY});
  assert.equal(s.rise.peakIsLast, true);
  close(s.rise.days, 4, 1e-6, "rise so far");
  assert.deepEqual(s.decline, []);
}
{ // three points after the peak but all in one night: no decline rate
  const night = [0, 0.1, 0.2, 0.3].map((day) => row(day, "r", 17 + day));
  assert.deepEqual(lc.summarise(night, {discovery: DISCOVERY}).decline, []);
}
{ // g and r more than a day apart, or a pair far from the peak: no colour
  const apart = [row(0, "r", 17), row(1.5, "g", 17.2), row(3, "r", 17.3)];
  assert.equal(lc.summarise(apart, {discovery: DISCOVERY}).colour, null);
  const late = [row(0, "r", 16), row(2, "r", 16.5), row(20, "r", 18), row(20.5, "g", 18.4)];
  assert.equal(lc.summarise(late, {discovery: DISCOVERY}).colour, null, "only pairs within ±10 days of the peak count");
  const g0 = lc.colourNearPeak(lc.toPoints(late).points, "g", "r", Date.parse("2026-08-21T00:00:00Z"));
  close(g0.value, 0.4, 1e-9, "the same pair counts when the peak is near it");
}
{ // no discovery time: day 0 is the first detection, never a limit
  const s = lc.toPoints([row(-2, "r", null, {detection: 0, limiting_magnitude: 20}), row(0, "r", 18), row(3, "r", 17.5)]);
  assert.equal(s.reference.basis, "first detection");
  close(s.points[0].days, -2, 1e-6, "the earlier limit sits at day -2");
  assert.equal(lc.summarise([]).rise, null);
  assert.deepEqual(lc.summarise([]).bands, []);
}
{ // provider shapes: ISO time only, string booleans, live rows without a detection field
  const s = lc.toPoints([
    {observed_at: "2026-09-15T09:23:36.000970Z", band: "g", magnitude: "18.5", detection: "t"},
    {observed_at: "2026-09-16T09:23:36Z", band: "g", magnitude: 18.4},
    {observed_at: "2026-09-17T09:23:36Z", band: "g", limiting_magnitude: 20.1, detection: false},
    {observed_at: "2026-09-18T09:23:36Z", band: "g", magnitude: "NaN", detection: 1},
    {observed_at: "2026-09-19T09:23:36Z", band: "g", magnitude: 99, detection: 1},
  ]);
  assert.deepEqual(s.points.map((p) => p.kind), ["detection", "detection", "limit"]);
  assert.equal(s.skipped.unplottable, 2, "NaN and implausible magnitudes are not drawn");
  assert.equal(s.points[0].time, Date.parse("2026-09-15T09:23:36.000Z"));
}

{ // years-old detections at the same position (host activity, an earlier flare) do not
  // become the "peak": estimates use the episode around day 0, the plot keeps everything
  const history = [row(-1400, "r", 16.5), row(-1398, "r", 16.6), row(-1396, "r", 16.4)];
  const recent = [0, 2, 4, 8, 10, 12, 16].map((day) => row(day, "r", rModel(day)));
  const s = lc.summarise(history.concat(recent), {discovery: DISCOVERY});
  close(s.bands[0].peak.magnitude, 17.0, 1e-9, "the peak comes from the current episode");
  close(s.rise.days, 10, 1e-6, "rise within the current episode");
  assert.equal(s.episode.episodes, 2);
  assert.equal(s.episode.leftOut, 3);
  assert.equal(s.episode.detections, 7);
  close(s.episode.fromDays, 0, 1e-6, "episode starts at day 0");
  assert.equal(s.counts.detections, 10, "counts still describe every retained detection");
  assert.equal(lc.toPoints(history.concat(recent), {discovery: DISCOVERY}).points.length, 10, "all are plotted");
  // a discovery inside a coverage gap picks the nearest episode
  const gap = lc.summarise(history.concat(recent), {discovery: "2026-07-10T00:00:00Z"});
  assert.equal(gap.episode.detections, 7);
  // without a discovery time the episode with most detections is used
  const unnamed = lc.summarise(history.concat(recent));
  assert.equal(unnamed.reference.basis, "first detection");
  assert.equal(unnamed.episode.detections, 7);
  assert.deepEqual(lc.episodes([]), []);
  assert.equal(lc.chooseEpisode([], {basis: "discovery", time: 0}), null);
}

// --- axes ---------------------------------------------------------------------------------
assert.equal(lc.symlog(0), 0);
close(lc.symlog(9), 1, 1e-12, "symlog(9)");
close(lc.symlog(-99), -2, 1e-12, "symlog(-99)");
for (const days of [-400, -3.5, 0, 0.2, 12, 5000]) close(lc.symlogInverse(lc.symlog(days)), days, 1e-9, "round trip");
assert.deepEqual(lc.dayTicks(-5, 40, false, 5), [0, 10, 20, 30, 40]);
assert.deepEqual(lc.dayTicks(-20, 400, true, 12), [-10, -3, -1, 0, 1, 3, 10, 30, 100, 300]);
assert.deepEqual(lc.dayTicks(-20, 400, true), [-10, -1, 0, 1, 10, 100], "crowded log ticks thin to decades, keeping 0");
assert.ok(lc.dayTicks(-3000, 10000, true, 4).length <= 6);
assert.deepEqual(lc.niceTicks(16.5, 21.2, 6), [17, 18, 19, 20, 21]);
assert.deepEqual(lc.niceTicks(16.5, 21.2, 4), [18, 20], "never more ticks than asked for");
assert.equal(lc.zeroPoint("nJy"), 31.4);
assert.equal(lc.zeroPoint("counts"), null);
{ // a straight line fit with scatter has a slope error
  const fit = lc.linearFit([{days: 0, magnitude: 17}, {days: 1, magnitude: 17.2}, {days: 2, magnitude: 17.1}, {days: 3, magnitude: 17.4}]);
  close(fit.slope, 0.11, 1e-9, "least-squares slope");
  assert.ok(fit.slopeError > 0);
}
console.log("CTAS light-curve summary: points, peaks, rise, decline, colour and axes passed.");

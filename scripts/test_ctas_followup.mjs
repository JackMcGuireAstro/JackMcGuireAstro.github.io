// Checks for the suggested follow-up card (ctas/followup.js): the browser's fallback derivation,
// tonight's windows at WIRO and Red Buttes from the browser observability model, S/N-20
// exposure estimates from the CCD model, the planner rows' feasibility and the rendered card.
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import * as ccd from "../tools/exposure/ccd.js";

const require = createRequire(import.meta.url);
const followup = require("../ctas/followup.js");
const observability = require("../ctas/observability.js");

let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };

// 2026-10-17 19:00 UTC: an October afternoon in Wyoming, so "tonight" is the UTC date 2026-10-18.
const NOW = Date.parse("2026-10-17T19:00:00Z");
const target = (extra = {}) => ({
  event_id: "123e4567-e89b-42d3-a456-426614174000", name: "AT2026abvo", ra_deg: 66.133125, dec_deg: -0.727111,
  discovery_time: "2026-09-13T08:00:00Z", discovery_survey: "ZTF", discovery_magnitude: 20.33, classification: "Unclassified",
  status: "candidate", record_role: "follow-up-target-candidate", ranking_channel: "optical",
  follow_up_counts: {observations: 2, spectra: 0, classifications: 0}, photometry_outcomes: {detections: 2},
  follow_up: {observations: [
    {observed_at: "2026-09-14T09:00:00Z", detection: 1, magnitude: 19.95, band: "g", magnitude_system: "AB", provider: "rubin-fink"},
    {observed_at: "2026-09-15T09:23:36.000970Z", detection: 1, magnitude: 19.789606, band: "r", magnitude_system: "AB", provider: "rubin-fink"},
  ]},
  ...extra,
});

test("the fallback derivation: unclassified optical target gets imaging then spectroscopy, nightly", () => {
  const block = followup.derive(target());
  assert.equal(block.schema, "ctas-suggested-follow-up.v1");
  assert.deepEqual(block.objectives.map((o) => [o.kind, o.priority]), [["imaging", 1], ["spectroscopy", 2]]);
  assert.equal(block.cadence.code, "nightly");
  assert.deepEqual(block.latest_magnitude, {value: 19.79, band: "r", system: "AB", observed_at: "2026-09-15T09:23:36.000970Z", provider: "rubin-fink", basis: "latest-retained-detection"});
  assert.deepEqual(block.basis, ["Discovered 2026-09-13.", "No public classification is retained.", "No public spectrum is retained.", "No retained observation since 2026-09-15."]);
  assert.equal(block.eligibility.newest_evidence_at, "2026-09-15T09:23:36.000970Z");
  assert.match(block.claim_boundary, /not an exposure-time calculation or observing request/);
});

test("a compact catalog row derives the same objectives from its counts", () => {
  const row = {event_id: "x", name: "AT2026yvy", ra_deg: 66.1, dec_deg: -0.7, discovery_magnitude: 12, status: "candidate",
    record_role: "follow-up-target-candidate", ranking_channel: "optical", classification: "Unclassified", n_spectra: 0, n_observations: 17,
    photometry_outcomes: {detections: 0, forced: 17, limits: 17}};
  const block = followup.derive(row);
  assert.deepEqual(block.objectives.map((o) => o.kind), ["imaging", "spectroscopy"]);
  assert.equal(block.objectives[0].closes_gap, null, "17 retained observations");
  assert.equal(block.latest_magnitude.basis, "discovery-magnitude");
  const retracted = followup.derive({...row, status: "retracted", record_role: "retracted-event"});
  assert.deepEqual(retracted.objectives, []);
  assert.equal(retracted.cadence.code, "none");
  const alert = followup.derive({...row, record_role: "localization-region-alert", ranking_channel: "neutrino-multimessenger", discovery_magnitude: null});
  assert.deepEqual(alert.objectives.map((o) => o.kind), ["counterpart"]);
  assert.equal(alert.cadence.code, "rapid");
});

test("a published block is preferred over the browser derivation, and the reader is told which", () => {
  const published = {...followup.derive(target()), basis: ["published"]};
  assert.equal(followup.blockFor(target({suggested_follow_up: published})).source, "published");
  assert.equal(followup.blockFor(target({suggested_follow_up: published})).block.basis[0], "published");
  assert.equal(followup.blockFor(target({suggested_follow_up: {schema: "other"}})).source, "browser");
});

test("tonight at WIRO and Red Buttes: dark hours above 30 degrees, local Mountain times, Moon", () => {
  assert.equal(followup.tonightDate(NOW), "2026-10-18");
  const windows = followup.tonight(target(), {observability, now: NOW});
  assert.deepEqual(windows.map((w) => w.site.telescope), ["wiro", "rbo"]);
  for (const w of windows) {
    assert.equal(w.status, "COMPLETE");
    assert.ok(w.hours > 4 && w.hours < 7, `${w.site.id}: ${w.hours} h`);
    assert.ok(w.max_altitude_deg >= 45 && w.max_altitude_deg <= 50, `${w.site.id}: highest ${w.max_altitude_deg}`);
    assert.match(w.intervals[0].start_local, /^\d\d:\d\d$/);
    assert.match(w.best_local, /^0[2-4]:\d\d$/, "transit after local midnight in October");
    assert.ok(w.moon_separation_deg > 0 && w.moon_illumination >= 0 && w.moon_illumination <= 1);
    assert.ok(["dark", "grey", "bright"].includes(w.sky_preset));
    assert.ok(w.best_airmass > 1.3 && w.best_airmass < 1.4, String(w.best_airmass));
  }
  const south = followup.tonight(target({dec_deg: -75}), {observability, now: NOW});
  assert.equal(south[0].intervals.length, 0, "a far-southern target never clears 30 degrees from Wyoming");
  assert.equal(followup.tonight(target({ra_deg: null, dec_deg: null}), {observability, now: NOW}), null);
});

test("exposure estimates: S/N 20 per filter on both telescopes, feasibility within 3 x 600 s", () => {
  const block = followup.derive(target());
  const windows = followup.tonight(target(), {observability, now: NOW});
  const plan = followup.exposurePlan(block, ccd, {windows});
  assert.deepEqual(plan.map((p) => p.id), ["wiro", "rbo"]);
  const [wiro, rbo] = plan;
  assert.ok(wiro.placeholder && rbo.placeholder, "both profiles are flagged placeholder in ccd.js");
  assert.deepEqual(wiro.imaging.map((f) => f.filter), ["g", "r", "i", "V", "R"]);
  assert.equal(wiro.airmass, windows[0].best_airmass);
  for (const row of wiro.imaging) assert.ok(row.seconds > 0 && Number.isFinite(row.seconds), JSON.stringify(row));
  const wiroR = wiro.imaging.find((f) => f.filter === "r"), rboR = rbo.imaging.find((f) => f.filter === "r");
  assert.ok(rboR.seconds > wiroR.seconds * 5, `0.6 m needs far longer than 2.3 m: ${rboR.seconds} vs ${wiroR.seconds}`);
  assert.equal(wiroR.feasible, wiroR.seconds <= followup.MAX_TOTAL_S);
  assert.equal(rboR.feasible, rboR.seconds <= followup.MAX_TOTAL_S);
  assert.ok(wiroR.feasible && rboR.feasible, "a 19.8 mag transient reaches S/N 20 in r on both within 3 x 600 s under the placeholder profiles");
  assert.equal(wiroR.exposures, 1);
  assert.equal(rboR.exposures, Math.ceil(rboR.seconds / 600));
  const faint = followup.exposurePlan(followup.derive(target({discovery_magnitude: 22.5, follow_up: {}, follow_up_counts: {}})), ccd, {windows});
  assert.ok(faint[0].imaging.find((f) => f.filter === "r").feasible, "22.5 mag in r stays within 3 x 600 s on WIRO");
  assert.ok(!faint[1].imaging.find((f) => f.filter === "r").feasible, "but not on the 0.6 m");
  assert.equal(faint[1].imaging_feasible, false);
  // Same physics as the calculator page: solve("time") with the preset values.
  const filter = ccd.FILTERS.find((f) => f.id === "r"), telescope = ccd.TELESCOPES[0];
  const direct = ccd.solve("time", {mag: 19.79, snr: 20, airmass: wiro.airmass, sky: ccd.SKY.r[wiro.sky_preset], diameter: telescope.diameter,
    obstruction: telescope.obstruction, throughput: telescope.throughput.r, extinction: telescope.extinction.r, plateScale: telescope.plateScale,
    seeing: telescope.seeing, apertureFactor: 1, gain: telescope.gain, readNoise: telescope.readNoise, dark: telescope.dark, fullWell: telescope.fullWell, zeroPoint: filter.zeroPoint});
  assert.equal(wiroR.seconds, followup.roundExposure(direct.t));
  assert.equal(wiro.spectroscopy.state, "too-faint", "19.8 mag is beyond the 19.0 placeholder threshold");
  assert.equal(rbo.spectroscopy.state, "not-available");
  // Without windows the geometry is assumed and said so.
  const blind = followup.exposurePlan(block, ccd, {});
  assert.ok(blind[0].assumed_geometry && blind[0].airmass === 1.5 && blind[0].sky_preset === "grey");
  // A bright target is feasible everywhere and the sub-second case is kept honest.
  const bright = followup.exposurePlan(followup.derive(target({discovery_magnitude: 12, follow_up: {}, follow_up_counts: {}})), ccd, {windows});
  assert.ok(bright[1].imaging.every((f) => f.feasible));
  assert.equal(followup.roundExposure(0.4), 0.4);
  assert.equal(followup.roundExposure(2345), 2340);
  // No magnitude, no estimate.
  assert.deepEqual(followup.exposurePlan(followup.derive(target({discovery_magnitude: null, follow_up: {}, follow_up_counts: {}})), ccd, {windows})[0].imaging, []);
});

test("planner feasibility verdicts name the telescopes that can take the observation", () => {
  const faint = followup.feasibility(followup.derive(target({discovery_magnitude: 22.5, follow_up: {}, follow_up_counts: {}})), ccd, {});
  assert.deepEqual(faint.map((f) => [f.label, f.text, f.feasible]), [["WIRO", "imaging", true], ["RBO", "—", false]]);
  const bright = followup.feasibility(followup.derive(target({discovery_magnitude: 15, follow_up: {}, follow_up_counts: {}})), ccd, {});
  assert.deepEqual(bright.map((f) => f.text), ["imaging + spectrum", "imaging"]);
  assert.ok(bright.every((f) => f.placeholder));
});

test("the links prefill the Tonight planner and the exposure calculator for this target", () => {
  const planner = new URL(followup.plannerHref(target(), "rbo", "https://example.test/"));
  assert.equal(planner.pathname, "/tonight/");
  assert.deepEqual(Object.fromEntries(planner.searchParams), {site: "rbo", list: "ctas", name: "AT2026abvo", event: "123e4567-e89b-42d3-a456-426614174000", ra: "66.133125", dec: "-0.727111", mag: "19.79"});
  const calculator = new URL(followup.calculatorHref(target(), "wiro", "r", 1.337, "https://example.test/", "grey"));
  assert.equal(calculator.pathname, "/tools/exposure/");
  assert.deepEqual(Object.fromEntries(calculator.searchParams), {telescope: "wiro", filter: "r", mode: "time", snr: "20", target: "AT2026abvo", mag: "19.79", airmass: "1.34", sky: "grey"});
});

test("the rendered card: heading, badge, both telescopes, escaped text, claim boundary", () => {
  const html = followup.render(target({name: "<b>AT2026abvo</b>"}), {observability, ccd, now: NOW});
  assert.match(html, /<section class="ctas-followup" data-followup-card data-followup-event="123e4567-e89b-42d3-a456-426614174000" data-followup-source="browser"/);
  assert.match(html, /<h4 id="ctas-followup-title">Suggested follow-up<\/h4>/);
  assert.equal((html.match(/data-followup-placeholder-badge/g) || []).length, 1, "one placeholder badge per card");
  assert.match(html, /href="tools\/exposure\/#placeholder-note"/);
  assert.match(html, /data-followup-telescope="wiro"/);
  assert.match(html, /data-followup-telescope="rbo"/);
  assert.doesNotMatch(html, /<b>AT2026abvo/, "the name only reaches the page URL-encoded inside the links");
  assert.match(html, /name=%3Cb%3EAT2026abvo%3C%2Fb%3E/);
  assert.match(html, /not an exposure-time calculation or observing request/);
  assert.match(html, /Nothing here is sent anywhere or requests telescope time/);
  assert.match(html, /19\.79 r \(AB\), 32 d ago/);
  assert.match(followup.render(target(), {observability, now: NOW}), /Loading the exposure model/, "without the model the card says it is loading");
  assert.doesNotMatch(html, /Loading the exposure model/);
  const archival = followup.render(target({discovery_time: "2024-01-01T00:00:00Z", follow_up: {}, follow_up_counts: {}}), {observability, ccd, now: NOW});
  assert.match(archival, /Archival:/);
  const retracted = followup.render(target({status: "retracted", record_role: "retracted-event"}), {observability, ccd, now: NOW});
  assert.doesNotMatch(retracted, /data-followup-telescope/);
  assert.match(retracted, /No objective: Status retracted/);
});

console.log(`${passed} follow-up checks passed`);

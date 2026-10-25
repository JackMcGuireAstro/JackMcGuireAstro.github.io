// ctas/screens.js in a bare JavaScript runtime (Track 3a): the population-deviation badge, the
// colour/temperature screen and the GW ranking and tiling drawn from blocks shaped like
// scripts/ctas_analysis_screens.py writes them, every panel absent when its run is absent,
// and the follow-up card carrying the GW panels. The GW block is a fixture: CTAS has retained
// no sky map since the analysis existed (no LVK observing run), see scripts/test_ctas_screens.py.
const assert = require("node:assert/strict");
const path = require("node:path");

const screens = require(path.join(__dirname, "..", "ctas", "screens.js"));
let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };

const SHA = "a".repeat(64);
const base = (type, extra) => Object.assign({
  analysisRunId: "run-" + type, analysisType: type, methodName: "m-" + type, methodVersion: "1.0.0", status: "COMPLETE",
  inputs: {n: 1}, inputChecksumSha256: SHA, parameters: {}, softwareVersions: {}, result: {}, resultChecksumSha256: "b".repeat(64),
  omittedResultFields: [], quality: {}, warnings: ["stated <limitation>"], reviewState: "MACHINE", completedAt: "2026-10-25T07:50:16Z",
  claimBoundary: "boundary of " + type,
}, extra);
const anomaly = (score, unusual) => base("population-anomaly", {
  result: {robust_deviation_score: score},
  badge: {rule: "unusual when the robust deviation score is at least 2.0", unusual, score,
    drivingFeatures: [{feature: "discovery_magnitude", robustZ: 1.07}, {feature: "discovery_to_ingest_hours", robustZ: -0.67}]},
});
const multiband = base("multiband-light-curve", {
  omittedResultFields: ["residuals", "parameter_covariance"],
  result: {tau_rise_days: 1.464, tau_fall_days: 15.02, parameter_uncertainties: {tau_rise_days: 0.29, tau_fall_days: 3.58},
    model_peak_mjd: 61279.94, reduced_chi_square: 0.498, peak_ab_magnitudes: {g: 19.299, i: 19.366, r: 19.377},
    model_peak_colors_mag: {"g-i": -0.067, "i-r": -0.011}, band_parameters: {g: {point_count: 36}, i: {point_count: 13}, r: {point_count: 38}},
    sed_screen: {status: "complete", temperature_k: 6362.88, bands: ["g", "i", "r"], epoch_mjd: 61283.32, temperature_semantics: "observer-frame single-temperature color screen"}},
});
const ranking = (retracted) => base("gw-counterpart-ranking", {eventRetracted: retracted, result: {ranking_truncated: false, rankings: [
  {eventId: "e1", name: "AT2027aaa", candidateState: "public", rank2d: 1, rank3d: 1, credibleLevel: 0.31, probabilityDensityPerSr: 12.5, distanceLikelihood: 0.004},
  {eventId: "e2", name: "AT2027bbb", candidateState: "retracted", rank2d: 2, rank3d: null, credibleLevel: 0.62, probabilityDensityPerSr: 4.1, distanceLikelihood: null},
  {eventId: "e3", name: "internal", candidateState: "not-in-public-catalog", rank2d: 3, rank3d: 2, credibleLevel: 0.88, probabilityDensityPerSr: 0.5, distanceLikelihood: 0.001},
]}});
const tiling = (id, fov) => base("gw-tiling-plan", {eventRetracted: false,
  parameters: {field_of_view_sq_deg: fov, target_probability: 0.9, field: {id, name: id.toUpperCase(), placeholder: true, basis: "placeholder: assumed detector"}},
  result: {cumulative_probability: 0.031, target_probability_reached: false,
    tiles: [{rank: 1, ra_deg: 40, dec_deg: 10, incremental_probability: 0.02, cumulative_probability: 0.02}, {rank: 2, ra_deg: 41, dec_deg: 10.5, incremental_probability: 0.011, cumulative_probability: 0.031}]},
});
const candidate = (runs, extra) => Object.assign({event_id: "123e4567-e89b-42d3-a456-426614174000", name: "AT2026xyz"},
  runs ? {analysis_screens: {schema: "ctas.analysis-screens@1", runs}} : {}, extra || {});

test("every panel is absent when its run is absent", () => {
  for (const c of [candidate(null), candidate([]), {analysis_screens: {schema: "other", runs: [anomaly(3, true)]}}]) {
    assert.equal(screens.badge(c), "");
    assert.equal(screens.colourScreen(c), "");
    assert.equal(screens.gwFollowup(c), "");
  }
  assert.equal(screens.gwFollowup(candidate([anomaly(0.5, false), multiband])), "");
});

test("the badge says unusual only under the stated rule and lists the driving features", () => {
  const calm = screens.badge(candidate([anomaly(0.79, false)]));
  assert.match(calm, /data-unusual="false"/);
  assert.match(calm, /Not unusual · population deviation 0\.79/);
  assert.match(calm, /discovery magnitude \+1\.07σ; hours from discovery to CTAS receipt −0\.67σ/);
  assert.match(calm, /at least 2\.0/);
  assert.match(calm, /m-population-anomaly v1\.0\.0/);
  const odd = screens.badge(candidate([anomaly(2.4, true)]));
  assert.match(odd, /data-unusual="true"/);
  assert.match(odd, /Unusual · population deviation 2\.40/);
  assert.match(odd, /stated &lt;limitation&gt;/, "warnings are escaped");
});

test("the colour screen shows the fit, the peak colours, the temperature and what is not drawn", () => {
  const html = screens.colourScreen(candidate([multiband]));
  assert.match(html, /rise 1\.5 ± 0\.3 d, fall 15\.0 ± 3\.6 d/);
  assert.match(html, /g-i −0\.07 mag · i-r −0\.01 mag/);
  assert.match(html, /6,363 K at MJD 61283\.32 from g, i, r/);
  assert.match(html, /not drawn here: residuals, parameter_covariance/);
  assert.match(html, /<th scope="row">r<\/th><td>19\.38<\/td><td>38<\/td>/);
  const none = screens.colourScreen(candidate([base("multiband-light-curve", {result: {peak_ab_magnitudes: {g: 20}, sed_screen: {status: "insufficient-data"}}})]));
  assert.match(none, /Colour temperature:<\/strong> not computed \(insufficient-data\)/);
});

test("the GW ranking shows 2-D and 3-D ranks and each candidate's state; tiling per field with the placeholder badge", () => {
  const html = screens.gwFollowup(candidate([ranking(false), tiling("rbo", 0.1165), tiling("wiro", 0.0809)]));
  assert.match(html, /3 localized records/);
  assert.match(html, /data-open-event="e1">AT2027aaa<\/button>/);
  assert.match(html, /<tr class="is-retracted"><td>2<\/td><td>—<\/td>/);
  assert.match(html, /<td>internal<\/td><td>not in the public catalog<\/td>/);
  assert.doesNotMatch(html, /data-gw-retracted/);
  assert.match(html, /data-gw-tiling="rbo"/);
  assert.match(html, /data-gw-tiling="wiro"/);
  assert.match(html, /2 tiles of 0\.081 deg² cover 3\.1% of the map probability; target 90% not reached/);
  assert.match(html, /placeholder field of view/);
  const retracted = screens.gwFollowup(candidate([ranking(true)]));
  assert.match(retracted, /data-gw-retracted/);
});

test("the follow-up card carries the GW panels when the screens are loaded", () => {
  globalThis.CTASScreens = screens;
  const followup = require(path.join(__dirname, "..", "ctas", "followup.js"));
  const gw = candidate([ranking(false), tiling("wiro", 0.0809)], {
    ra_deg: 40, dec_deg: 10, discovery_time: "2027-01-20T00:00:00Z", event_type: "compact-binary-merger",
    primary_messenger: "gravitational-wave", status: "candidate", record_role: "localization-region-alert",
    follow_up_counts: {}, follow_up: {},
  });
  const card = followup.render(gw, {now: Date.parse("2027-01-20T12:00:00Z"), windows: []});
  assert.match(card, /data-followup-card/);
  assert.match(card, /Gravitational-wave localization/);
  assert.ok(card.indexOf("Gravitational-wave localization") < card.lastIndexOf("ctas-claim-boundary"));
  const plain = followup.render(candidate(null, {ra_deg: 40, dec_deg: 10, follow_up_counts: {}, follow_up: {}}), {now: Date.parse("2027-01-20T12:00:00Z"), windows: []});
  assert.doesNotMatch(plain, /Gravitational-wave localization/);
});

console.log(`${passed} collector-screen checks passed`);

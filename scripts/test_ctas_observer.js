#!/usr/bin/env node
// Tests of the CTAS observer tools (ctas/observer.js, Framework v2 Track 4): the information-gap
// ranking, the prefilled requests, the report drafts, the observing-log parser, the HEALPix cell
// centres and the coverage of a localisation region. With a path argument (a CTAS release's
// ctas/data) it also writes every draft of a sample of real dossiers to stdout as JSON lines, which
// scripts/test_ctas_observer.py validates against the draft schemas.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const observer = require(path.join(__dirname, "..", "ctas", "observer.js"));

const NOW = Date.parse("2026-10-29T12:00:00Z");
let passed = 0;
function test(name, fn) { fn(); passed += 1; if (!process.argv[2]) console.log("ok  " + name); }

function dossier(over) {
  return Object.assign({
    event_id: "30617eb0-8278-4d8b-9111-0b28dc019757", name: "AT2026woo", event_type: "optical-transient", classification: "Unclassified",
    ra_deg: 242.8160146, dec_deg: -24.9312202, discovery_time: "2026-10-20T04:31:50.998000Z", discovery_survey: "ZTF", discovery_magnitude: 17.74,
    designations: [{designation: "AT2026woo", source: "TNS", source_key: "tns"}],
    follow_up_counts: {spectra: 0, host_context: 0},
    lightcurve_science: {inputs: {detectionsByBand: {atlaso: 32}, lastMjd: 61340.0}},
    suggested_follow_up: {latest_magnitude: {value: 18.56, band: "o", observed_at: "2026-10-27T19:02:25Z", basis: "latest-retained-detection", provider: "atlas"}}
  }, over || {});
}

if (!process.argv[2]) {
  test("gaps: an unclassified one-band record lacks a spectrum, a redshift, a colour and a host, ranked by the stated weights", () => {
    const r = observer.gaps(dossier(), {now: NOW});
    assert.equal(r.rule, "ctas-information-gap.v1");
    assert.deepEqual(r.gaps.map((g) => g.id), ["classification-spectrum", "redshift", "second-band", "host-context"]);
    assert.equal(r.gaps[0].reach, "jack", "18.6 mag is inside the placeholder 19.0 mag spectroscopy limit");
    assert.deepEqual(r.gaps.map((g) => g.rank), [1, 2, 3, 4]);
  });
  test("gaps: a faint record's spectrum gap is halved (only a larger telescope), so imaging gaps it can close rank first", () => {
    const faint = dossier({suggested_follow_up: {latest_magnitude: {value: 20.4, band: "r", observed_at: "2026-10-27T19:02:25Z", basis: "latest-retained-detection"}}});
    const r = observer.gaps(faint, {now: NOW});
    const spectrum = r.gaps.find((g) => g.id === "classification-spectrum");
    assert.equal(spectrum.reach, "larger");
    assert.equal(spectrum.score, 2.5);
    assert.equal(r.gaps[0].id, "second-band", "weight 3, reachable at 20.4 mag");
  });
  test("gaps: an old last point is a recent-point gap; a classified record with a redshift, colour and host has none of those", () => {
    const stale = observer.gaps(dossier({lightcurve_science: {inputs: {detectionsByBand: {ztfg: 4, ztfr: 5}, lastMjd: 61300.0}}}), {now: NOW});
    assert.ok(stale.gaps.some((g) => g.id === "recent-point" && /days old/.test(g.gap)));
    assert.ok(!stale.gaps.some((g) => g.id === "second-band"));
    const full = observer.gaps(dossier({classification: "SN Ia", follow_up_counts: {spectra: 1, host_context: 1}, redshift: 0.031,
      lightcurve_science: {inputs: {detectionsByBand: {ztfg: 4, ztfr: 5}, lastMjd: 61341.0}}}), {now: NOW});
    assert.deepEqual(full.gaps, []);
  });
  test("gaps: a localisation event's first gap is the counterpart search", () => {
    const r = observer.gaps({event_id: "x", name: "IceCube-220509A", event_type: "neutrino", multimessenger: {schema: "ctas.multimessenger@1", kind: "neutrino"}}, {now: NOW});
    assert.deepEqual(r.gaps.map((g) => g.id), ["counterpart-search"]);
  });
  test("requests: ATLAS and ZTF forced photometry cover 50 days before discovery to now with placeholders for the credentials", () => {
    const fp = observer.forcedPhotometry(dossier(), {now: NOW});
    assert.equal(fp.atlas.params.ra, 242.816015);
    assert.equal(fp.atlas.params.mjd_max, 61342.5);
    assert.ok(Math.abs(fp.atlas.params.mjd_min - (61333.18878 - 50)) < 0.01);
    assert.match(fp.atlas.curl, /^curl -X POST -H "Authorization: Token \$ATLAS_TOKEN"/);
    assert.match(fp.ztf.url, /^https:\/\/ztfweb\.ipac\.caltech\.edu\/cgi-bin\/requestForcedPhotometry\.cgi\?ra=242\.816015&dec=-24\.93122&jdstart=/);
    assert.ok(fp.ztf.url.includes("YOUR_ZFPS_PASSWORD") && !/dontgo/i.test(JSON.stringify(fp)), "no shared credential is embedded");
    assert.equal(observer.forcedPhotometry({event_id: "x"}), null);
  });
  test("drafts: a TNS-registered record gets no AT report; the classification draft names it; sexagesimal is rounded safely", () => {
    const at = observer.tnsAtReport(dossier());
    assert.equal(at.report, null);
    assert.equal(at.registered, "2026woo");
    assert.equal(observer.tnsClassificationReport(dossier()).report.classification_report["0"].name, "2026woo");
    assert.equal(observer.sexagesimal(242.8160146, true), "16:11:15.844");
    assert.equal(observer.sexagesimal(-24.9312202, false), "-24:55:52.39");
    assert.equal(observer.sexagesimal(359.99999999, true), "24:00:00.000");
    const fresh = observer.tnsAtReport(dossier({designations: [], name: "CTAS-candidate-1"}));
    assert.equal(fresh.report.at_report["0"].ra.value, "16:11:15.844");
    assert.equal(fresh.report.at_report["0"].discovery_datetime, "2026-10-20 04:31:50");
  });
  test("observing log: degrees, sexagesimal, MJD, ISO and profile footprints are read; bad rows are listed", () => {
    const csv = "ra,dec,time,filter,depth,telescope\n150.0,20.0,61342.25,r,21.3,wiro\n10:00:00.0,+20:30:00,2026-10-29T03:00:00Z,g,,rbo\n999,0,,r,,wiro\n151,21,,i,,unknown\n";
    const telescopes = [{id: "wiro", name: "WIRO", fieldOfViewDeg: [0.284, 0.284], placeholderFields: ["fieldOfViewDeg"]}, {id: "rbo", name: "RBO", fieldOfViewDeg: [0.341, 0.341], placeholderFields: []}];
    const out = observer.parseObservationLog(csv, {telescopes});
    assert.equal(out.pointings.length, 2);
    assert.equal(out.pointings[0].timeMs, Date.parse("2026-10-29T06:00:00Z"));
    assert.equal(out.pointings[0].footprint.widthDeg, 0.284);
    assert.match(out.pointings[0].footprint.basis, /placeholder field of view/);
    assert.equal(out.pointings[1].ra, 150);
    assert.equal(out.pointings[1].dec, 20.5);
    assert.equal(out.errors.length, 2);
    assert.match(observer.parseObservationLog("time,filter\n1,2\n").errors[0], /ra and dec/);
  });
  test("HEALPix: NESTED cell centres match astropy-healpix", () => {
    const ref = [[0, 0, 45.0, 41.810315], [0, 5, 90.0, 0.0], [0, 11, 315.0, -41.810315], [3, 100, 123.75, 35.685335], [6, 4427, 150.46875, 17.582776],
      [9, 1234567, 344.003906, 9.367157], [12, 123456789, 298.377686, 3.209645]];
    for (const [order, ipix, ra, dec] of ref) {
      const c = observer.nestPix2Ang(order, ipix);
      assert.ok(Math.abs(c.ra - ra) < 1e-5 && Math.abs(c.dec - dec) < 1e-5, `${order}/${ipix}: ${c.ra},${c.dec}`);
    }
  });
  test("coverage: four 1-degree boxes inside a 3-degree MOC agree with mocpy's box MOCs to 5 percent", () => {
    const moc = {"5": [1112, 1113, 1115, 1116], "6": [4427, 4430, 4431, 4442, 4456, 4457, 4459, 4472, 4473, 4474, 4548, 4549],
      "7": [17703, 17707, 17714, 17715, 17718, 17719, 17762, 17774, 17832, 17833, 17835, 17880, 17882, 17900, 17901, 17902, 17904, 18180, 18181, 18240, 18241, 18242, 18244],
      "8": [70487, 70493, 70495, 70517, 70519, 70525, 70527, 70613, 70615, 70621, 70810, 70811, 70821, 70822, 70823, 70825, 70826, 70827, 70851, 70854, 70855, 70866, 70867,
        71054, 71088, 71090, 71091, 71100, 71102, 71103, 71337, 71490, 71496, 71498, 71499, 71612, 71613, 71614, 71624, 71625, 71626, 71648, 71650, 72709, 72729, 72732,
        72733, 72735, 72804, 72805, 72816, 72817, 72820, 72972, 72973, 72980]};
    const pts = [[149.5, 20], [150.5, 20], [150, 21], [152, 19]].map(([ra, dec]) => ({ra, dec, pa: 0, footprint: {kind: "box", widthDeg: 1, heightDeg: 1}}));
    const r = observer.coverage(pts, {kind: "moc", moc});
    // mocpy: (MOC of the boxes at depth 12 ∩ region) / region = 0.1299, slightly high because a box MOC covers its boundary cells.
    assert.ok(Math.abs(r.fraction - 0.1299) / 0.1299 < 0.05, String(r.fraction));
    assert.equal(r.regionSqDeg, 31.26);
    const circle = observer.coverage([{ra: 150, dec: 20, pa: 0, footprint: {kind: "circle", radiusDeg: 3}}], {kind: "circle", ra: 150, dec: 20, radiusDeg: 3});
    assert.ok(circle.fraction > 0.999, "a pointing as big as the circle covers it");
    const none = observer.coverage([{ra: 10, dec: -40, pa: 0, footprint: {kind: "box", widthDeg: 1, heightDeg: 1}}], {kind: "circle", ra: 150, dec: 20, radiusDeg: 3});
    assert.equal(none.fraction, 0);
  });
  test("Treasure Map: pointings only for a gravitational-wave event, ISO times, the token never in the payload", () => {
    const gw = {event_id: "g", name: "S250206dm", multimessenger: {schema: "ctas.multimessenger@1", kind: "gravitational-wave"}};
    const p = observer.treasureMapPayload(gw, [{ra: 1, dec: 2, timeMs: Date.parse("2026-10-29T03:00:00Z"), filter: "r", depth: 21, pa: 0}], {instrumentId: "47"});
    assert.deepEqual(p, {graceid: "S250206dm", pointings: [{ra: 1, dec: 2, band: "r", instrumentid: "47", depth: 21, depth_unit: "ab_mag", time: "2026-10-29T03:00:00", status: "completed", pos_angle: 0}]});
    assert.equal(observer.treasureMapPayload(dossier(), [], {}), null);
  });
  test("panel: gaps, actions and the claim boundary; the run-sheet link carries the event", () => {
    const html = observer.panel(dossier(), {now: NOW});
    assert.match(html, /data-observer-panel="30617eb0-8278-4d8b-9111-0b28dc019757"/);
    assert.match(html, /tonight\/runsheet\.html\?site=wiro&amp;events=30617eb0/);
    assert.ok(!/data-observer-download="tns-at"/.test(html), "registered on TNS: no AT report button");
    assert.match(html, /Nothing is submitted, stored or requested by CTAS\./);
    assert.ok(!/data-observer-log/.test(html), "no localisation region: no log import");
  });
  console.log(passed + " observer checks passed");
} else {
  // Draft output for scripts/test_ctas_observer.py: real dossiers from a release.
  const dir = process.argv[2];
  const files = fs.readdirSync(path.join(dir, "candidate-chunks")).filter((f) => /^[0-9a-f]{3}\.json$/.test(f)).sort();
  let count = 0;
  for (const f of files) {
    const chunk = JSON.parse(fs.readFileSync(path.join(dir, "candidate-chunks", f), "utf8"));
    for (const c of chunk.candidates || []) {
      if (count >= Number(process.argv[3] || 400)) break;
      if (!Number.isFinite(c.ra_deg)) continue;
      const fp = observer.forcedPhotometry(c, {now: NOW}), swift = observer.swiftToo(c), at = observer.tnsAtReport(c);
      const designations = c.designations || [];
      process.stdout.write(JSON.stringify({event_id: c.event_id, swift: swift && swift.request, at: at && at.report, classification: observer.tnsClassificationReport(c).report,
        astronote: observer.astroNoteDraft(c), gaps: observer.gaps(c, {now: NOW}).gaps.map((g) => g.id), forced: Boolean(fp),
        fresh: observer.tnsAtReport(Object.assign({}, c, {designations: designations.filter(() => false), name: "CTAS-test"})).report}) + "\n");
      count += 1;
    }
  }
}

// Parser and matching tests for ctas/live-sources.js (no network).
"use strict";
const assert = require("node:assert/strict");
const live = require("../ctas/live-sources.js");

// ALeRCE: positive detections become magnitudes, negative subtractions are dropped, limits kept
const alerce = live.parseAlerceLightcurve({
  detections: [
    {mjd: 61200.5, fid: 1, magpsf: 18.2, sigmapsf: 0.05, isdiffpos: "t"},
    {mjd: 61201.5, fid: 2, magpsf: 18.4, sigmapsf: 0.06, isdiffpos: 1},
    {mjd: 61202.5, fid: 2, magpsf: 19.0, sigmapsf: 0.1, isdiffpos: "f"},
  ],
  non_detections: [{mjd: 61199.5, fid: 1, diffmaglim: 20.1}],
}, "ZTF26aaaaaaa");
assert.equal(alerce.length, 3);
assert.deepEqual(alerce.map((r) => r.band), ["g", "r", "g"]);
assert.equal(alerce[0].observed_at, "2026-06-09T12:00:00.000Z");
assert.equal(alerce[2].limiting_magnitude, 20.1);
assert.equal(alerce[2].detection, 0);
assert.match(alerce[0].provider, /ZTF26aaaaaaa/);

// position matching keeps only objects within 2 arcsec and picks the nearest
const near = live.nearestAlerceObject({items: [
  {oid: "far", meanra: 10.001, meandec: 20}, {oid: "near", meanra: 10.0002, meandec: 20.0001},
]}, 10, 20);
assert.equal(near.oid, "near");
assert.ok(near.sep < 1);
assert.equal(live.nearestAlerceObject({items: [{oid: "far", meanra: 10.01, meandec: 20}]}, 10, 20), null);
assert.ok(Math.abs(live.separationArcsec(0, 0, 0, 1 / 3600) - 1) < 1e-6);

// Fink/Rubin: nJy fluxes to AB magnitudes; weak points become limits; neighbours dropped
const fink = live.parseFinkSources([
  {"r:ra": 10, "r:dec": 20, "r:midpointMjdTai": 61300, "r:band": "r", "r:psfFlux": 10000, "r:psfFluxErr": 100},
  {"r:ra": 10, "r:dec": 20, "r:midpointMjdTai": 61301, "r:band": "g", "r:psfFlux": 50, "r:psfFluxErr": 40},
  {"r:ra": 10.01, "r:dec": 20, "r:midpointMjdTai": 61302, "r:band": "g", "r:psfFlux": 10000, "r:psfFluxErr": 100},
], 10, 20);
assert.equal(fink.length, 2);
assert.ok(Math.abs(fink[0].magnitude - 21.4) < 1e-9, "10,000 nJy is AB 21.4");
assert.equal(fink[1].detection, 0);
assert.ok(Math.abs(fink[1].limiting_magnitude - (31.4 - 2.5 * Math.log10(120))) < 1e-9);

// Gaia Science Alerts CSV: header, untrusted rows skipped
const gaia = live.parseGaiaCsv("Gaia15adl\n#Date,JD(TCB),averagemag.\n2014-08-02 20:49:51,2456872.36795,19.12\n2014-08-03 02:44:29,2456872.61422,untrusted\n", "Gaia15adl");
assert.equal(gaia.length, 1);
assert.equal(gaia[0].band, "G");
assert.equal(gaia[0].magnitude, 19.12);

// Pan-STARRS: Jy to AB magnitude, filter IDs to bands
const ps = live.parsePanstarrs({data: [{obsTime: 56000, filterID: 2, psfFlux: 3631e-8, psfFluxErr: 3631e-10}, {obsTime: 56001, filterID: 3, psfFlux: -1}]});
assert.equal(ps.length, 1);
assert.equal(ps[0].band, "r");
assert.ok(Math.abs(ps[0].magnitude - 20) < 1e-9);

// ASCII spectra: comments and headers skipped, needs at least 10 points
const spec = live.parseAsciiSpectrum("# TNS\nwavelength flux\n" + Array.from({length: 12}, (_, i) => `${4000 + i} ${1e-16 * (i + 1)}`).join("\n"));
assert.equal(spec.length, 12);
assert.equal(spec[0].wavelength, 4000);
assert.deepEqual(live.parseAsciiSpectrum("1 2\n3 4"), []);

// relay addresses and CSV export
assert.equal(live.relayUrl("https://r.example/", "https://a.b/c?d=1"), "https://r.example/?url=https%3A%2F%2Fa.b%2Fc%3Fd%3D1");
const text = live.csv(alerce);
assert.match(text, /not part of the verified snapshot/);
assert.equal(text.trim().split("\n").length, 2 + alerce.length);
// Lasair (Rubin): sources found wherever the object response keeps them; duplicates dropped
const lasairObj = {objectId: 1, objectData: {}, diaSourcesList: [
  {midpointMjdTai: 61300.1, band: "g", psfFlux: 10000, psfFluxErr: 100, diaSourceId: 1},
  {midpointMjdTai: 61300.1, band: "g", psfFlux: 10000, psfFluxErr: 100, diaSourceId: 1},
], extra: {forced: [{midpointMjdTai: 61301.1, band: "r", psfFlux: 20, psfFluxErr: 30, diaForcedSourceId: 9}]}};
const las = live.parseLasairObject(lasairObj, "1234");
assert.equal(las.length, 2);
assert.ok(Math.abs(las[0].magnitude - 21.4) < 1e-9);
assert.equal(las[1].detection, 0);
assert.match(las[1].photometry_method, /forced/);
assert.deepEqual(live.lasairNearest({object: "170657518965489670", separation: 0.4}), {objectId: "170657518965489670", sep: 0.4});
assert.deepEqual(live.lasairNearest([{objectId: 42, separation: 1.2}]), {objectId: "42", sep: 1.2});
assert.equal(live.lasairNearest({}), null);
// real Lasair-LSST answers (2026-09-28): {"nearest": {...}}, bare NaN, 18-digit ids
const realCone = live.parseLasairText('{"nearest":{"object":170028512255672336,"separation":8.894137537394414}}');
assert.deepEqual(live.lasairNearest(realCone), {objectId: "170028512255672336", sep: 8.894137537394414});
const realObj = live.parseLasairText('{"diaObjectId":"170028512255672336","diaSourcesList":[{"diaObjectId":170028512255672336,"midpointMjdTai":61090.18,"diaSourceId":170028512255672336,"band":"g","psfFlux":968.7,"psfFluxErr":50.1,"dipoleAngle":NaN}],"diaForcedSourcesList":[{"midpointMjdTai":61091.2,"band":"r","diaForcedSourceId":314003015460192583,"psfFlux":-12.5,"psfFluxErr":20.0,"scienceFlux":NaN}]}');
assert.equal(realObj.diaSourcesList[0].dipoleAngle, null);
const realRows = live.parseLasairObject(realObj, "170028512255672336");
assert.equal(realRows.length, 2);
assert.deepEqual(live.sherlockSummary({classifications: {query0: ["SN", "The transient is possibly associated with <em><a href=\"https://ned.example\">COSMOS1660609</a></em>; a J=20.5 mag galaxy."]}}),
  {label: "SN", text: "The transient is possibly associated with COSMOS1660609; a J=20.5 mag galaxy."});
assert.deepEqual(live.sherlockSummary({classifications: [{classification: "SN", description: "offset from galaxy"}]}), {label: "SN", text: "offset from galaxy"});
assert.deepEqual(live.sherlockSummary({classification: "NT"}), {label: "NT", text: ""});

// ADS: TNS names searched by their year+suffix; other names quoted exactly
const q = live.adsQuery({name: "SN2026pel", designations: [{designation: "AT2026pel"}, {designation: "ZTF26abblsak"}, {designation: "GRB 260926A"}]});
assert.match(q, /^full:\(/);
assert.ok(q.includes('"2026pel"') && q.includes('"ZTF26abblsak"') && q.includes('"GRB 260926A"'));
assert.equal(q.split('"2026pel"').length, 2, "the same TNS name is searched once");
assert.equal(live.adsQuery({designations: []}), "");
const papers = live.parseAds({response: {docs: [{bibcode: "2026TNSAN.254....1K", title: ["Follow-up"], author: ["A", "B", "C", "D"], pubdate: "2026-06-00", doctype: "circular"}]}});
assert.equal(papers[0].authors, "A; B; C et al.");
assert.match(papers[0].url, /ui\.adsabs\.harvard\.edu\/abs\/2026TNSAN/);

// ---- the source registry, release comparison and "Check all sources now" (2026-10-17)
assert.ok(live.SOURCES.length >= 16, "every live-capable source is registered");
const keys = live.SOURCES.map((s) => s.key);
for (const k of ["alerce", "fink", "lasair", "gaia", "panstarrs", "irsa-ztf", "wiserep", "ads", "simbad", "ned", "gaia-dr3", "heasarc", "vsx", "tns-page", "asassn", "gcn"]) assert.ok(keys.includes(k), "source " + k);
assert.equal(new Set(keys).size, keys.length, "keys are unique");
// CSV, TAP JSON, IRSA ZTF, the Gaia alerts index and VSX readers
const csvt = live.parseCsv('a,b,"c, d"\n1,"x ""y""",3\n# comment\n2,,\n');
assert.deepEqual(csvt.columns, ["a", "b", "c, d"]);
assert.deepEqual(csvt.rows, [{a: "1", b: 'x "y"', "c, d": "3"}, {a: "2", b: "", "c, d": ""}]);
assert.ok(live.parseCsv("<?xml version='1.0'?><VOTABLE/>").markup, "a VOTable answer is recognised, not mis-read as rows");
assert.deepEqual(live.tapRows({metadata: [{name: "main_id"}, {name: "otype"}], data: [["M 31", "G"]]}), [{main_id: "M 31", otype: "G"}]);
assert.deepEqual(live.tapRows([{x: 1}]), [{x: 1}]);
const irsa = live.parseIrsaZtf("oid,mjd,mag,magerr,catflags,filtercode\n1,59000.5,18.1,0.02,0,zg\n1,59001.5,18.2,0.02,32768,zr\n1,59002.5,18.0,0.03,0,zi\n");
assert.equal(irsa.length, 2, "rows flagged 32768 are dropped");
assert.deepEqual(irsa.map((r) => r.band), ["g", "i"]);
assert.equal(irsa[0].difference_photometry, 0);
assert.equal(irsa[0].source_key, "ztf-irsa");
const gaiaIndex = "#Name,Date,RaDeg,DecDeg,AlertMag,HistoricMag,HistoricStdDev,Class,Published,Comment,TNSid\nGaia26abc,2026-10-01 01:02:03,10.0001,20.0001,18.1,19.0,0.1,SN,2026-10-01,\"a, comment\",AT2026x\nGaia26abd,2026-10-01 01:02:03,10.1,20.0,18.1,19.0,0.1,unknown,2026-10-01,,\n";
const ga = live.nearestGaiaAlert(gaiaIndex, 10, 20, 2);
assert.equal(ga.name, "Gaia26abc");
assert.ok(ga.sep < 1);
assert.equal(live.nearestGaiaAlert(gaiaIndex, 11, 20, 2), null);
assert.deepEqual(live.parseVsx({VSXObjects: ""}), []);
assert.deepEqual(live.parseVsx({VSXObjects: {VSXObject: {Name: "SS Cyg", VariabilityType: "UG", Period: "0.27", Distance: "0.5"}}}).map((v) => v.name), ["SS Cyg"]);
// the release baseline from a record: per-source retained counts and check dates, latest observation
const record = {event_id: "e1", name: "AT2026abc", ra_deg: 10, dec_deg: 20, updated_at: "2026-10-10T00:00:00Z",
  designations: [{designation: "AT2026abc"}, {designation: "ZTF26aaaaaaa"}],
  follow_up_counts: {observations: 5, spectra: 1, publications: 2, catalog_counterparts: 1},
  follow_up: {observations: [{observed_at: "2026-09-01T00:00:00Z", provider: "atlas"}, {observed_at: "2026-09-10T00:00:00Z", provider: "rubin-fink"}]},
  source_coverage: [
    {source_id: "ztf-alerce", disposition: "searched-with-data", checked_at: "2026-09-12T00:00:00Z", retained_record_count: 5},
    {source_id: "ned", disposition: "ambiguous-identity", checked_at: "2026-09-11T00:00:00Z", retained_record_count: 0},
    {source_id: "ned", disposition: "searched-with-data", checked_at: "2026-09-12T00:00:00Z", retained_record_count: 1},
    {source_id: "ads", disposition: "searched-with-data", checked_at: "2026-09-12T00:00:00Z", retained_record_count: 2},
  ]};
const base = live.releaseBaseline(record);
assert.equal(base.latestObserved, "2026-09-10T00:00:00Z");
assert.equal(base.bySource["ztf-alerce"].count, 5);
assert.equal(base.bySource.ned.count, 1, "the richer of two receipts for one source");
assert.equal(base.bySource.ned.checkedAt, "2026-09-12T00:00:00Z");
const byKey = (k) => live.SOURCES.find((s) => s.key === k);
assert.equal(live.compareWithRelease(byKey("alerce"), {status: "ok", count: 5, latest: "2026-09-10T12:00:00Z"}, base).state, "same", "same count, nothing newer than 12 h");
let cmp = live.compareWithRelease(byKey("alerce"), {status: "ok", count: 9, latest: "2026-10-15T00:00:00Z"}, base);
assert.equal(cmp.state, "new");
assert.match(cmp.text, /newer measurements upstream \(latest 2026-10-15/);
assert.match(cmp.text, /9 upstream vs 5 retained \(\+4\)/);
assert.equal(live.compareWithRelease(byKey("alerce"), {status: "ok", count: 3, latest: "2026-09-01T00:00:00Z"}, base).state, "fewer");
cmp = live.compareWithRelease(byKey("fink"), {status: "ok", count: 2, latest: "2026-09-02T00:00:00Z"}, base);
assert.equal(cmp.state, "new", "a source the release never asked, now answering rows, is new");
assert.match(cmp.text, /none of this source retained/);
assert.equal(live.compareWithRelease(byKey("fink"), {status: "none", count: 0}, base).state, "none");
assert.equal(live.compareWithRelease(byKey("wiserep"), {status: "ok", count: 1}, base).state, "same", "spectra compare with the retained spectra count");
assert.equal(live.compareWithRelease(byKey("wiserep"), {status: "ok", count: 2}, base).state, "new");
assert.equal(live.compareWithRelease(byKey("ads"), {status: "ok", count: 2}, base).state, "same");
assert.equal(live.compareWithRelease(byKey("ned"), {status: "ok", count: 3}, base).text, "3 upstream vs 1 retained (+2)");
assert.equal(live.compareWithRelease(byKey("ned"), {status: "unavailable", count: 0}, base).state, "unknown", "no verdict without an answer");
// rows before any check say what the release holds and when it last asked
let html = live.rowHtml(byKey("ned"), null, base, null);
assert.match(html, /not checked yet/);
assert.match(html, /release last asked 2026-09-12 · 1 retained/);
html = live.rowHtml(byKey("fink"), null, base, null);
assert.match(html, /never asked this source/);
html = live.rowHtml(byKey("ned"), {status: "ok", count: 1, unit: "objects", fetchedAt: "2026-10-17T12:00:00.000Z", comparison: {state: "same", text: "matches the release (1 retained)"}, items: [{name: "NGC 1", type: "G", sep: 2.5}]}, base, null);
assert.match(html, /1 object<\/span>/, "singular unit");
assert.match(html, /fetched <time datetime="2026-10-17T12:00:00.000Z">12:00:00 UTC/);
assert.match(html, /ctas-live__same/);
assert.ok(!/ctas-live__new/.test(html));
html = live.rowHtml(byKey("ned"), {status: "ok", count: 3, unit: "objects", fetchedAt: "2026-10-17T12:00:00.000Z", comparison: {state: "new", text: "3 upstream vs 1 retained (+2)"}}, base, null);
assert.match(html, /<mark class="ctas-live__new">New since the release: 3 upstream vs 1 retained \(\+2\)<\/mark>/);
html = live.rowHtml(byKey("ned"), {status: "ok", count: 3, unit: "objects", fetchedAt: "2026-10-17T12:00:00.000Z", key: "ned", comparison: {state: "same", text: ""}}, base, {at: "2026-10-01T00:00:00Z", rows: {ned: {status: "ok", count: 1, latest: null}}});
assert.match(html, /changed since you last looked \(2026-10-01\): was 1, now 3/);
assert.equal(live.changeSinceSeen({key: "ned", status: "ok", count: 1}, {at: "2026-10-01T00:00:00Z", rows: {ned: {status: "ok", count: 1, latest: null}}}), "");
assert.match(live.changeSinceSeen({key: "a", status: "ok", count: 1, latest: "2026-10-10"}, {at: "2026-10-01T00:00:00Z", rows: {a: {status: "ok", count: 1, latest: "2026-10-05"}}}), /newer since you last looked/);
assert.match(live.changeSinceSeen({key: "a", status: "none", count: 0}, {at: "2026-10-01T00:00:00Z", rows: {a: {status: "ok", count: 4, latest: null}}}), /now none/);
assert.equal(live.changeSinceSeen({key: "a", status: "ok", count: 1}, null), "");
html = live.rowHtml(byKey("tns-page"), {status: "link", count: 0, link: "https://www.wis-tns.org/object/2026abc", note: "x"}, base, null);
assert.match(html, /Link only/);
assert.match(html, /href="https:\/\/www\.wis-tns\.org\/object\/2026abc"/);
(async () => {
// the pool runs at most `limit` jobs at a time and reports each as it finishes
{
  let running = 0, peak = 0, order = [];
  const job = (i, ms) => () => new Promise((resolve) => { running += 1; peak = Math.max(peak, running); setTimeout(() => { running -= 1; resolve(i); }, ms); });
  const results = await live.pool([job(0, 30), job(1, 5), job(2, 5), job(3, 5), job(4, 5)], 2, (index) => order.push(index));
  assert.deepEqual(results, [0, 1, 2, 3, 4], "results keep the job order");
  assert.equal(peak, 2, "never more than the cap in flight");
  assert.equal(order[0], 1, "a quick job reports before a slow earlier one");
  const settled = await live.pool([() => Promise.reject(new Error("boom"))], 4);
  assert.equal(settled[0].status, "unavailable");
  assert.match(settled[0].note, /boom/);
}
// load(): every source runs, relay-only sources say so without a relay, `fresh` reaches fetch as cache: "reload"
{
  const calls = [];
  const reply = (body, type = "application/json") => new Response(typeof body === "string" ? body : JSON.stringify(body), {status: 200, headers: {"Content-Type": type}});
  globalThis.fetch = async (url, init) => {
    calls.push({url: String(url), init});
    const u = String(url);
    if (u.endsWith("live-config.json")) return reply({relay: "https://relay.test"});
    if (u.includes("/ztf/v1/objects/?")) return reply({items: [{oid: "ZTF26aaaaaaa", meanra: 10, meandec: 20}]});
    if (u.includes("/lightcurve")) return reply({detections: [{mjd: 61280.5, fid: 1, magpsf: 18.2, sigmapsf: 0.05, isdiffpos: "t"}], non_detections: []});
    if (u.includes("fink-portal")) return reply([]);
    if (u.includes("simbad")) return reply({metadata: [{name: "main_id"}, {name: "otype"}, {name: "sep"}], data: [["NGC 1", "G", 0.0007]]});
    const target = decodeURIComponent(u.split("?url=")[1] || "");
    if (target.includes("alerts.csv")) return reply(gaiaIndex, "text/csv");
    if (target.includes("lightcurve.csv")) return reply("Gaia26abc\n#Date,JD(TCB),averagemag.\n2026-10-02 20:49:51,2461316.36795,18.12\n", "text/csv");
    if (target.includes("ned.ipac")) return reply("prefname,pretype,ra,dec,z\nWISEA J1,G,10,20,0.1\n", "text/csv");
    if (target.includes("heasarc")) return reply("name,obsid,start_time\n", "text/csv");
    if (target.includes("tapvizier")) return reply("Source,RA_ICRS,DE_ICRS,Gmag,Plx,PM\n", "text/csv");
    if (target.includes("adsabs")) return new Response("key", {status: 503});
    if (target.includes("lasair")) return new Response("key", {status: 503});
    if (target.includes("wiserep")) return reply([{"Spec. ID": 1}, {"Spec. ID": 2}]);
    if (target.includes("aavso")) return reply({VSXObjects: ""});
    if (target.includes("panstarrs")) return reply({data: []});
    if (target.includes("irsa")) return reply("oid,mjd,mag,magerr,catflags,filtercode\n", "text/csv");
    return new Response("", {status: 404});
  };
  const seenRows = [];
  const result = await live.load(record, {fresh: true, onRow: (r) => seenRows.push(r.key)});
  assert.equal(result.results.length, live.SOURCES.length);
  assert.equal(seenRows.length, live.SOURCES.length, "every source reports once");
  const by = Object.fromEntries(result.results.map((r) => [r.key, r]));
  assert.equal(by.alerce.status, "ok"); assert.equal(by.alerce.count, 1); assert.equal(by.alerce.comparison.state, "fewer");
  assert.equal(by.gaia.status, "ok"); assert.match(by.gaia.how, /by position in the alerts index: Gaia26abc/);
  assert.equal(by.gaia.comparison.state, "new", "Gaia alert rows the release never retained");
  assert.equal(by.simbad.count, 1); assert.ok(Math.abs(by.simbad.items[0].sep - 2.52) < 0.01, "SIMBAD separations in arcsec");
  assert.equal(by.ned.comparison.state, "same");
  assert.equal(by.lasair.status, "needs-key"); assert.match(by.lasair.note, /Lasair key/);
  assert.equal(by.ads.status, "needs-key");
  assert.equal(by.wiserep.count, 2); assert.equal(by.wiserep.comparison.state, "new");
  assert.equal(by.heasarc.status, "none"); assert.equal(by.vsx.status, "none"); assert.equal(by["gaia-dr3"].status, "none");
  assert.equal(by["tns-page"].status, "link"); assert.equal(by["tns-page"].link, "https://www.wis-tns.org/object/2026abc");
  assert.equal(by.gcn.status, "link");
  assert.ok(result.results.every((r) => r.fetchedAt && r.source && r.unit !== undefined));
  assert.equal(result.photometry.filter((r) => r.status === "ok").length, 2, "ALeRCE and Gaia rows feed the plot");
  const providerCalls = calls.filter((c) => !String(c.url).endsWith("live-config.json"));
  assert.ok(providerCalls.length > 10);
  assert.ok(providerCalls.every((c) => c.init && c.init.cache === "reload"), "fresh bypasses only the browser cache");
  assert.ok(providerCalls.every((c) => !/fresh=/.test(String(c.url))), "the relay address never changes, so its edge cache stands");
  assert.ok(providerCalls.every((c) => c.init.credentials === "omit"), "no credentials ever leave the browser");
  const summary = live.summarize(result);
  assert.deepEqual(summary.rows.alerce, {status: "ok", count: 1, latest: "2026-08-28T12:00:00.000Z"});
  // without a relay a relay-only source says so (never guesses), a direct one still runs,
  // and a source without what it needs says why
  const noRelay = {relay: "", fresh: false, json: () => Promise.reject(new Error("should not be called")), text: () => Promise.reject(new Error("should not be called"))};
  assert.equal((await live.runSource(byKey("ned"), record, noRelay)).status, "no-relay");
  assert.match((await live.runSource(byKey("ned"), record, noRelay)).note, /relay/);
  assert.equal((await live.runSource(byKey("ned"), {event_id: "x"}, noRelay)).status, "not-applicable");
  const direct = await live.runSource(byKey("simbad"), record, {relay: "", json: async () => ({metadata: [{name: "main_id"}, {name: "otype"}, {name: "sep"}], data: []}), text: async () => ""});
  assert.equal(direct.status, "none");
  const timedOut = await live.runSource(byKey("simbad"), record, {relay: "", json: async () => { const e = new Error("x"); e.name = "AbortError"; throw e; }, text: async () => ""});
  assert.equal(timedOut.status, "unavailable"); assert.match(timedOut.note, /timed out/);
}
console.log("CTAS live sources: all checks passed");
})().catch((error) => { console.error(error); process.exit(1); });

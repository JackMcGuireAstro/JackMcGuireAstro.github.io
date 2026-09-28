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
console.log("CTAS live sources: all checks passed");

// ctas/host-association.js in a bare JavaScript runtime (Track 3c part 2): the host panel drawn
// from a host_association block shaped as scripts/ctas_host_association.py writes it, the
// population-anomaly reading, and the alert-stamp addresses the relay accepts.
const assert = require("node:assert/strict");
const path = require("node:path");

const panels = require(path.join(__dirname, "..", "ctas", "host-association.js"));
let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };
const sha = "a".repeat(64);
const run = (type, extra) => Object.assign({analysisType: type, methodName: "m", methodVersion: "1.0.0", inputs: {x: 1},
  inputChecksumSha256: sha, parameters: {}, warnings: [], claimBoundary: "A ranking, not a host identification."}, extra);

const block = {schema: "ctas.host-association@1", runs: [
  run("host-posterior", {status: "complete", resultChecksumSha256: sha, result: {verdict: "probable", probableHost: "NGC 1", hostMissedPosterior: 0.05,
    candidates: [{rank: 1, name: "NGC 1", types: ["G"], catalogs: ["NED", "SIMBAD"], separationArcsec: 2.1, fractionalOffset: 0.3, offsetBasis: "directional-light-radius",
      redshifts: [{value: 0.0213, error: 0.0001, catalog: "NED", reference: "2000ApJ...1..1X"}], posterior: 0.9, sourceUrl: "https://ned.ipac.caltech.edu/byname?objname=NGC+1"},
    {rank: 2, name: "Gaia DR3 1", types: ["galaxy candidate"], catalogs: ["Gaia DR3"], separationArcsec: 25, fractionalOffset: 5, offsetBasis: "angular-scale", redshifts: [], posterior: 0.05}]}}),
  run("prior-activity", {status: "not-computed", notComputedReason: "no retained photometry more than 30 days before discovery"}),
  run("extinction-and-point-source", {status: "complete", resultChecksumSha256: sha, warnings: ["Pan-STARRS point-source classifications are not retained"],
    result: {ebvRecalibrated: 0.42, highExtinction: true, pointSourceFlag: false, pointSource: null, gaiaSourcesAtPosition: 0}}),
  run("stellar-counterpart", {status: "complete", resultChecksumSha256: sha, result: {gaiaSourceId: "42", absoluteG: 4.2, bpMinusRp: 0.9, parallaxMas: 2, parallaxErrorMas: 0.1, distancePc: 500}}),
], externalLinks: [{label: "Blast host SED and properties (SCIMMA)", url: "https://blast.scimma.org/transients/2026abc", note: "External service."}]};

test("the panel is absent without a block, light-curve science or a ZTF alert", () => {
  assert.equal(panels.panel({}), "");
  assert.equal(panels.panel({host_association: {schema: "other", runs: []}}), "");
});

test("the panel ranks the host candidates with the missed hypothesis, flags and provenance", () => {
  const html = panels.panel({event_id: "e1", name: "SN2026abc", host_association: block});
  assert.match(html, /Host galaxy and context/);
  assert.match(html, /probable host/);
  assert.match(html, /NGC 1/);
  assert.match(html, /host not catalogued: 0\.050/);
  assert.match(html, /DLR/);
  assert.match(html, /2000ApJ\.\.\.1\.\.1X/);
  assert.match(html, /high extinction/);
  assert.match(html, /not computed — no retained photometry/);
  assert.match(html, /M_G = 4\.20/);
  assert.match(html, /blast\.scimma\.org\/transients\/2026abc/);
  assert.match(html, /inputs sha256 <code>a{12}/);
  assert.match(html, /not a host identification/);
});

test("the population reading names the rank, drivers and neighbours", () => {
  const doc = {schema: "ctas.population-anomaly@1", status: "complete", population: 3, methodName: "coniferest", methodVersion: "1.0.0",
    featureTableChecksumSha256: sha, claimBoundary: "Not a classification.",
    events: [{eventId: "e1", rank: 1, percentile: 100, drivers: [{feature: "log10HalfMaxRiseDays", value: -0.3, robustZ: -7.0}],
      neighbours: [{eventId: "e2", name: "SN2026x", classification: "SN II", distance: 1.25}]}]};
  const html = panels.populationHtml(doc, "e1");
  assert.match(html, /rank 1 of 3/);
  assert.match(html, /rise time \(-7\.0 robust σ\)/);
  assert.match(html, /data-open-event="e2"/);
  assert.match(panels.populationHtml(doc, "missing"), /not in the release's feature table/);
  assert.match(panels.populationHtml({schema: "ctas.population-anomaly@1", status: "not-computed", notComputedReason: "too few"}, "e1"), /too few/);
});

test("stamp addresses match the relay's routes and the latest ALeRCE alert is used", () => {
  const alerce = panels.stampUrls("alerce", "ZTF26abxwrmm", "3558142162315015006").map((r) => r.url);
  assert.deepEqual(alerce.map((u) => new URL(u).searchParams.get("type")), ["science", "template", "difference"]);
  assert.ok(alerce.every((u) => u.startsWith("https://avro.alerce.online/get_stamp?oid=ZTF26abxwrmm&candid=3558142162315015006&type=") && u.endsWith("&format=png")));
  const fink = panels.stampUrls("fink", "ZTF26abljbah").map((r) => r.url);
  assert.ok(fink.every((u) => u.startsWith("https://api.ztf.fink-portal.org/api/v1/cutouts?objectId=ZTF26abljbah&kind=") && u.endsWith("&output-format=PNG")));
  const ztf = panels.ztfObject({follow_up: {observations: [
    {provider_observation_id: "alerce:ZTF26abxwrmm:1111111111", detection: 1, mjd: 61000},
    {provider_observation_id: "alerce:ZTF26abxwrmm:2222222222", detection: 1, mjd: 61010},
    {provider_observation_id: "fink:ZTF26abxwrmm:3333333333:valid", detection: 1, mjd: 61020}]}});
  assert.deepEqual(ztf, {oid: "ZTF26abxwrmm", candid: "2222222222", mjd: 61010});
  assert.match(panels.panel({event_id: "e9", follow_up: {observations: [{provider_observation_id: "alerce:ZTF26abxwrmm:2222222222", detection: 1, mjd: 1}]}}), /Alert stamps/);
});

console.log(`host association panels: ${passed} checks passed`);

// ctas/lightcurve-science.js in a bare JavaScript runtime (Track 3c part 1): the light-curve
// science panel and the broker-classes panel drawn from blocks written by
// scripts/ctas_lightcurve_science.py and scripts/ctas_broker_classes.py
// (tests/fixtures/ctas-brokers/dossier-blocks.json: a synthetic two-band light curve with a
// redshift, and the recorded real ALeRCE, Fink and ANTARES responses for ZTF26abljbah).
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const panels = require(path.join(__dirname, "..", "ctas", "lightcurve-science.js"));
const blocks = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "tests", "fixtures", "ctas-brokers", "dossier-blocks.json"), "utf8"));
let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };
const candidate = Object.assign({name: "SN2026x"}, JSON.parse(JSON.stringify(blocks)));

test("panels are absent when their blocks are absent", () => {
  assert.equal(panels.panel({}), "");
  assert.equal(panels.brokerPanel({}), "");
  assert.equal(panels.panel({lightcurve_science: {schema: "other", runs: []}}), "");
});

test("the light-curve panel shows the shared-shape features, peaks and provenance", () => {
  const html = panels.panel(candidate);
  assert.match(html, /Light-curve science/);
  assert.match(html, /Half-maximum rise/);
  assert.match(html, /ZTF g/);
  assert.match(html, /ZTF r/);
  assert.match(html, /shared shape across bands/);
  assert.match(html, /Planck 2018/);
  assert.match(html, /inputs sha256 <code>[0-9a-f]{12}/);
  assert.match(html, /not computed: no band has 20 detections/);
  assert.match(html, /phenomenological fit/);
});

test("the broker panel puts the three brokers side by side with readings and credits", () => {
  const html = panels.brokerPanel(candidate);
  for (const broker of ["ALeRCE", "Fink", "ANTARES"]) assert.match(html, new RegExp("<th scope=\"row\">" + broker));
  assert.match(html, /bogus or artefact/);
  assert.match(html, /SN \(type unknown\)/);
  assert.match(html, /readings disagree/);
  assert.match(html, /TNS: SN Ia/);
  assert.match(html, /stamp_classifier_2025_beta/);
  assert.match(html, /ANT20260s3q9ygj2hn9/);
  assert.match(html, /Forster et al\. 2021/);
  assert.match(html, /not the broker's word/);
});

test("text from providers is escaped", () => {
  const evil = JSON.parse(JSON.stringify(candidate));
  evil.broker_classes.brokers[2].output.tags = ["<img src=x onerror=alert(1)>"];
  assert.doesNotMatch(panels.brokerPanel(evil), /<img/);
});

test("dates come from MJD", () => {
  assert.equal(panels.mjdDate(60000), "2023-02-25 (MJD 60000.00)");
  assert.equal(panels.mjdDate(null), "—");
});

console.log(`${passed} lightcurve-science panel checks passed`);

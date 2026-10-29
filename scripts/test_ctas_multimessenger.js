// ctas/multimessenger.js in a bare JavaScript runtime (Track 3d): the multimessenger panel drawn
// from blocks built by scripts/ctas_multimessenger.py (tests/fixtures/ctas/multimessenger-blocks.json:
// the collector's real S190814bv map run and a real-shape IceCube notice), the MOC overlay data,
// and the CSV / MOC exports.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const mm = require(path.join(__dirname, "..", "ctas", "multimessenger.js"));
const fixture = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "tests", "fixtures", "ctas", "multimessenger-blocks.json"), "utf8"));
let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };

test("the panel is absent without a block or with another schema", () => {
  assert.equal(mm.panel({}), "");
  assert.equal(mm.panel({event_id: "x", multimessenger: {schema: "other"}}), "");
});

test("the gravitational-wave panel shows the LVK fields, the localization, galaxies and registry", () => {
  const html = mm.panel(fixture.gravitationalWave);
  assert.match(html, /Gravitational-wave event/);
  assert.match(html, /Significant<\/dt><dd>yes/);
  assert.match(html, /NSBH 99%/);
  assert.match(html, /Has a sub-solar-mass object: not in alert/);
  assert.match(html, /90% credible area 2\d\.\d deg²/);
  assert.match(html, /Show the sky map in Aladin Lite/);
  assert.match(html, /incomplete: galaxies already on file/);
  assert.match(html, /AT2019illus/);
  assert.match(html, /Treasure Map public candidates not read/);
  assert.match(html, /observation log not imported yet/);
  assert.doesNotMatch(html, /undefined|NaN/);
});

test("the MOC overlays carry the three credible regions in Aladin's JSON form", () => {
  const overlays = mm.mocOverlays(fixture.gravitationalWave.multimessenger);
  assert.deepEqual(overlays.map((row) => row.level), [0.1, 0.5, 0.9]);
  overlays.forEach((row) => { assert.ok(Object.keys(row.json).length > 0); assert.match(row.color, /^#/); });
});

test("exports: 90% MOC, STMOC and the registry CSV", () => {
  const block = fixture.gravitationalWave.multimessenger;
  const moc = mm.exportPayload(block, "moc90");
  assert.match(moc.body, /^\d+\/\d+/);
  assert.match(mm.exportPayload(block, "stmoc").body, /^t\d+\//);
  const registry = mm.exportPayload(block, "registry").body.split("\n");
  assert.equal(registry[0], "rank2d,rank3d,eventId,name,state,credibleLevel,probabilityDensityPerSr,discoveryTime,peakMagnitude,impliedAbsoluteMagnitude,priorActivity");
  assert.match(registry[1], /AT2019illus,public/);
  assert.equal(mm.csv([{a: 'x,"y"'}], ["a"]), 'a\n"x,""y"""\n');
});

test("the neutrino panel shows the published circle and the events inside it", () => {
  const html = mm.panel(fixture.neutrino);
  assert.match(html, /Neutrino alert/);
  assert.match(html, /radius 0\.513°/);
  assert.match(html, /provider containment_probability, 90%/);
  assert.match(html, /AT2021inside/);
  assert.doesNotMatch(html, /AT2021outside|AT2022late/);
  assert.match(mm.exportPayload(fixture.neutrino.multimessenger, "region").body, /AT2021inside,candidate/);
});

console.log(`${passed} multimessenger panel checks passed`);

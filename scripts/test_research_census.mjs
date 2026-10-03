// Checks for the barium dwarf census page (research/barium-dwarfs/).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { rows, median, distanceBins, toCsv, filterRows, sortRows } from "../research/barium-dwarfs/census.js";

const data = JSON.parse(readFileSync(new URL("../research/barium-dwarfs/census-data.json", import.meta.url)));
let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };

test("the data file matches the report's table sizes and statistics", () => {
  assert.equal(data.bariumDwarfs.length, 71);            // report: 71 confirmed barium dwarfs
  assert.equal(data.localWdCompanionStars.length, 81);
  const d = data.bariumDwarfs.map((r) => r.distancePc), t = data.bariumDwarfs.map((r) => r.teffK);
  // Report Table 1: distance 12.89-745.54 pc, Teff 4502-7057 K, mean distance 144.05 pc, mean Teff 5867 K
  assert.equal(Math.min(...d), 12.89); assert.equal(Math.max(...d), 745.54);
  assert.equal(Math.min(...t), 4502); assert.equal(Math.max(...t), 7057);
  assert.ok(Math.abs(d.reduce((a, b) => a + b) / d.length - 144.05) < 0.1);
  assert.ok(Math.abs(t.reduce((a, b) => a + b) / t.length - 5867) < 1);
  assert.equal(data.bariumDwarfSeparations.length, 11);
  assert.equal(data.candidates.length, 7);
  assert.match(data.status, /manuscript is in preparation/);
});

test("median, bins and filters", () => {
  assert.equal(median([3, 1, 2]), 2); assert.equal(median([1, 2, 3, 4]), 2.5); assert.equal(median([null]), null);
  const all = rows(data);
  const bins = distanceBins(all);
  assert.equal(bins.reduce((s, b) => s + b.ba, 0), 71);
  assert.equal(bins.reduce((s, b) => s + b.wd, 0), all.filter((r) => r.sample === "wd" && Number.isFinite(r.distancePc)).length);
  assert.equal(filterRows(all, { sample: "ba", maxDist: 30 }).length, 5);
  assert.deepEqual(filterRows(all, { q: "40 eri" }).map((r) => r.name), ["40 Eri C"]);
  assert.equal(sortRows(all, "distancePc", "ascending")[0].name, "Sirius A");
  assert.equal(sortRows(all, "teffK", "descending")[0].name, "Regulus A");
});

test("CSV quotes values and labels samples", () => {
  const csv = toCsv([{ name: 'A, "b"', sample: "ba", teffK: 5000, distancePc: null, bpRp: 0.8, gMag: 7, absG: 3 }]);
  assert.equal(csv, 'name,sample,teffK,distancePc,bpRp,gMag,absG\n"A, ""b""",Barium dwarf,5000,,0.8,7,3\n');
});
console.log(`${passed} census checks passed`);

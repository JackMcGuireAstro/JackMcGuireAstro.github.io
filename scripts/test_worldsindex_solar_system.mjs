#!/usr/bin/env node
// Regression tests for the Solar System exclusion (scripts/worldsindex_solar_system.mjs), on real
// rows from the published release of 2026-11-04 (tests/fixtures/worldsindex/solar-system-rows.json):
// Neptune (Open Exoplanet Catalogue, host "Sun", no coordinates), ISO 1I (Exoplanet.eu, host "Sun",
// published as a CONFIRMED direct-imaging planet at RA 0, Dec 0), ISO CNEOS 2014-01-08 (already on
// the builder's exceptions list as controversial) and HD 209458 b (kept).
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as ss from './worldsindex_solar_system.mjs';

const fixture = JSON.parse(readFileSync(new URL('../tests/fixtures/worldsindex/solar-system-rows.json', import.meta.url), 'utf8'));
const byName = Object.fromEntries(fixture.detections.map((d) => [d.name, d]));
let checks = 0;
const ok = (label) => { checks += 1; console.log(`ok  ${label}`); };

// The rule: host name is the Sun, nothing else.
assert.equal(ss.isSolarSystem(byName.Neptune), true);
assert.equal(ss.isSolarSystem(byName['ISO 1I']), true);
assert.equal(ss.isSolarSystem(byName['HD 209458 b']), false);
assert.equal(ss.isSolarSystem({ hostName: '  SUN ' }), true);
assert.equal(ss.isSolarSystem({ hostName: 'Sun-like star' }), false, 'a star named after the Sun is not the Sun');
assert.equal(ss.isSolarSystem({ hostName: null }), false);
ok('host "Sun" (any case, trimmed) is excluded; other hosts, a missing host and look-alike names are kept');

const { kept, excluded } = ss.partitionDetections(fixture.detections);
assert.deepEqual(kept.map((d) => d.name), ['HD 209458 b']);
assert.deepEqual(excluded.map((d) => d.name), ['Neptune', 'ISO 1I', 'ISO CNEOS 2014-01-08']);
ok('partition keeps order and splits 1 kept / 3 excluded');

// Coverage: recomputed by the builder's definitions; refuses an atlas whose block it cannot reproduce.
const full = ss.coverageFor(fixture.detections);
const atlas = { coverage: full, detections: fixture.detections, projectionId: 'x' };
const { atlas: trimmed } = ss.excludeFromAtlas(atlas);
assert.equal(trimmed.detections.length, 1);
assert.equal(trimmed.coverage.objects, 1);
assert.equal(trimmed.coverage.missingCoordinates, 0, 'Neptune had no coordinates; it is gone');
assert.equal(full.missingCoordinates, 1);
assert.equal(trimmed.coverage.sourceCounts['open-exoplanet-catalogue'] ?? 0, (byName['HD 209458 b'].sourceIds.includes('open-exoplanet-catalogue') ? 1 : 0));
assert.throws(() => ss.excludeFromAtlas({ ...atlas, coverage: { ...full, objects: full.objects + 1 } }), /cannot recompute/);
assert.equal(ss.excludeFromAtlas({ coverage: ss.coverageFor(kept), detections: kept }).excluded.length, 0, 'nothing excluded → the atlas is returned as it is');
ok('coverage recomputed exactly; a builder coverage it cannot reproduce stops the build');

// Exceptions list: new entries with source rows; an object already listed gains the category.
const merged = ss.withExcluded(fixture.exceptions, excluded);
const entry = (name) => merged.objects.find((item) => item.name === name);
assert.deepEqual(entry('Neptune').categories, ['excluded-solar-system']);
assert.equal(entry('Neptune').reasons[0].sourceId, 'open-exoplanet-catalogue');
assert.equal(entry('Neptune').reasons[0].sourceRecordId, byName.Neptune.sourceObjectIds[0]);
assert.equal(entry('Neptune').reasons[0].normalized, 'EXCLUDED');
assert.equal(entry('ISO 1I').atlasStatus, 'CONFIRMED');
assert.deepEqual(entry('ISO CNEOS 2014-01-08').categories, ['controversial', 'excluded-solar-system']);
assert.equal(entry('ISO CNEOS 2014-01-08').reasons.length, 2, 'the builder reason is kept beside ours');
assert.deepEqual(merged.counts, { controversial: 1, 'excluded-solar-system': 3 });
assert.match(merged.categories['excluded-solar-system'], /not an exoplanet/);
assert.equal(fixture.exceptions.objects.length, 1, 'the builder list is not mutated');
assert.equal(ss.withExcluded(null, excluded), null);
ok('exceptions list: 3 entries in excluded-solar-system, counts recomputed, builder reasons kept');

// Summary: subtracts exactly what the excluded products contributed.
const products = fixture.products;
const rows = products.reduce((sum, p) => sum + p.properties.reduce((s, b) => s + b.rows.length, 0), 0);
const blocks = products.reduce((sum, p) => sum + p.properties.length, 0);
const summary = { objects: 100, objectsWithRows: 90, assertionRows: 1000, conflictSets: 500, byProperty: {}, bySource: {}, flags: { controversial: 5 }, exceptions: { controversial: 1 } };
for (const p of products) for (const b of p.properties) {
  summary.byProperty[b.code] ??= { sets: 50, rows: 50 };
  for (const row of b.rows) summary.bySource[p.records[row.record].sourceId] = 100;
}
const adjusted = ss.adjustSummary(summary, products, merged);
assert.equal(adjusted.objects, 97);
assert.equal(adjusted.assertionRows, 1000 - rows);
assert.equal(adjusted.conflictSets, 500 - blocks);
assert.equal(adjusted.flags.controversial, 4, 'ISO CNEOS carried the controversial flag');
assert.deepEqual(adjusted.exceptions, merged.counts);
assert.equal(adjusted.solarSystemExclusion.objects, 3);
assert.equal(summary.objects, 100, 'the builder summary is not mutated');
const withSet = structuredClone(products[0]); withSet.properties[0].set = { conflict: false };
assert.throws(() => ss.adjustSummary(summary, [withSet], merged), /comparable rows/);
ok('summary: objects, rows, property blocks, rows by source and flags subtracted; a product with sets stops the build');

const block = ss.manifestBlock(excluded);
assert.equal(block.objects, 3);
assert.deepEqual(block.names, ['ISO 1I', 'ISO CNEOS 2014-01-08', 'Neptune']);
assert.equal(block.ruleVersion, 'worldsindex-solar-system-exclusion.v1');
ok('manifest block names the rule and the excluded objects');

console.log(`Solar System exclusion: ${checks} checks passed`);

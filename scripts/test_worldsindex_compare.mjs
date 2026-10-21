#!/usr/bin/env node
// Unit tests for the WorldsIndex Compare tab (worldsindex/assets/compare.js), the static builder's
// conflict-set consumer (scripts/worldsindex_conflicts.mjs) and the changelog's new / resolved
// conflicts (scripts/worldsindex_changelog.mjs). Framework v2, Track 2a. Pure functions and a tiny
// fixture product only; the browser behaviour is in scripts/test_worldsindex_compare_browser.py.
// Run: node --test scripts/test_worldsindex_compare.mjs
import assert from 'node:assert/strict';
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { gunzipSync, gzipSync } from 'node:zlib';

import * as compare from '../worldsindex/assets/compare.js';
import { buildConflictArtifacts, bucketFor, conflictIndexEntries, conflictRows, rejectHeader, selectionRows } from './worldsindex_conflicts.mjs';
import { diffConflicts, parseEcsv } from './worldsindex_changelog.mjs';

// A fixture in the published worldsindex-conflict-sets.v1 form (values invented for the test).
const set = (code, rowCount, extra = {}) => ({
  id: `conflict-object-test-1-b-${code}`, targetId: 'object-test-1-b', propertyCode: code, assertionIds: Array.from({ length: rowCount }, (_, i) => `a${i}`), relation: 'DISAGREEMENT', explanation: 'x',
  comparisonUnit: 'day', comparisonPolicyVersion: 'worldsindex-conflict-policy.v1', maxPairwiseSigma: 12.5, statisticallyAssessedPairs: 3, schemaVersion: 'worldsindex-conflict-sets.v1', label: 'Orbital period',
  canonicalUnit: 'day', sigmaThreshold: 3, relationLabel: 'statistically-inconsistent', conflict: true, rowCount, sourceIds: ['nasa-koi', 'nasa-ps'], definitionCodes: ['orbital-period'],
  pairCounts: { total: rowCount * (rowCount - 1) / 2, byRelation: { none: 1, 'statistically-inconsistent': 2 }, byVerdict: { consistent: 1, inconsistent: 2, 'not-comparable': 0 } }, notablePairs: [[0, 2, 12.5, 'statistically-inconsistent']], ...extra,
});
const selection = (code, definitionCode, value, chosenRow, extra = {}) => ({
  id: `selection-nasa-pscomppars-like.v1-object-test-1-b-${code}-${definitionCode}`, targetId: 'object-test-1-b', purpose: 'DISPLAY_DEFAULT', policyVersion: 'nasa-pscomppars-like.v1', actor: 'builder', softwareVersion: 'worldsindex-conflict-policy.v1',
  selectedAt: '2026-10-05T14:43:07.649Z', ruleId: 'nasa-pscomppars-like', ruleVersion: 'v1', selector: 'builder', reviewState: 'unreviewed', propertyCode: code, definitionCode, label: 'selection, not a measurement',
  basis: 'default-solution', value, unit: code === 'orbital_period' ? 'day' : 'R_earth', errPlus: 0.001, errMinus: 0.001, limitType: 'NONE', chosenRow, chosenSourceRecordIds: ['source-ps-000001-aaaa'], selectedAssertionIds: ['x'],
  rejectedAssertionIds: [], rejectedRowCount: 2, reason: 'Value from the most recent default parameter set (Author 2020).', rationale: 'r', calculated: null, selectionState: 'SELECTED', archiveComposite: null, ...extra,
});
const product = {
  objectId: 'object-test-1-b', name: 'Test-1 b', hostName: 'Test-1', identityState: 'CANONICAL', atlasStatus: 'CONFIRMED',
  references: [{ label: 'Author et al. 2020', url: 'https://ui.adsabs.harvard.edu/abs/2020ApJ...900..100A/abstract', bibcode: '2020ApJ...900..100A' }, { label: 'Other et al. 2012', url: null }],
  records: [
    { sourceId: 'nasa-ps', sourceRecordId: 'source-ps-000001-aaaa', recordType: 'published solution', sourceTable: 'ps', reference: 0, published: '2020-05', updated: '2020-06-01', isSourceDefault: true, candidate: false, sourceStatus: 'Planetary Systems member', identityBasis: 'SAME_OBJECT_RECORD', method: 'Transit' },
    { sourceId: 'nasa-ps', sourceRecordId: 'source-ps-000002-bbbb', recordType: 'published solution', sourceTable: 'ps', reference: 1, published: '2012-03', isSourceDefault: false, candidate: false, identityBasis: 'SAME_OBJECT_RECORD', method: 'Transit' },
    { sourceId: 'nasa-koi', sourceRecordId: 'cumulative:7', recordType: 'mission candidate row', sourceTable: 'cumulative', candidate: true, sourceStatus: 'CANDIDATE', identityBasis: 'PROVIDER_CROSS_IDENTIFIER', instrument: 'Kepler', method: 'Kepler pipeline (q1_q17_dr25_tce)' },
    { sourceId: 'exoplanet-eu', sourceRecordId: 'exoplanet-eu-test-1-b-abc', recordType: 'catalog row', sourceTable: 'exoplanet.eu catalog', candidate: false, identityBasis: 'ATLAS_LINKED' },
  ],
  properties: [
    { code: 'orbital_period', label: 'Orbital period', unit: 'day', conversions: { day: 'Source unit day is the canonical unit' },
      rows: [
        { record: 0, field: 'pl_orbper', definition: 'orbital-period', limit: 'NONE', original: { value: '3.5000', unit: 'day', errPlus: 0.001, errMinus: 0.001 }, normalized: { value: 3.5, errPlus: 0.001, errMinus: 0.001 }, compat: { verdict: 'not-comparable', reason: 'This row is the selected row.', counts: [1, 1, 0] } },
        { record: 1, field: 'pl_orbper', definition: 'orbital-period', limit: 'NONE', original: { value: '3.501', unit: 'day', errPlus: 0.001, errMinus: 0.001 }, normalized: { value: 3.501, errPlus: 0.001, errMinus: 0.001 }, compat: { against: 0, verdict: 'consistent', sigma: 0.707, counts: [1, 1, 0] } },
        { record: 2, field: 'koi_period', definition: 'orbital-period', limit: 'NONE', original: { value: '3.55', unit: 'day', errPlus: 0.002, errMinus: 0.002 }, normalized: { value: 3.55, errPlus: 0.002, errMinus: 0.002 }, compat: { against: 0, relation: 'statistically-inconsistent', verdict: 'inconsistent', sigma: 22.36, reason: 'Differ by 22.36 combined sigma (threshold 3).', counts: [0, 2, 0] } },
      ],
      set: set('orbital_period', 3), selections: [selection('orbital_period', 'orbital-period', 3.5, 0)],
      evolution: [{ definitionCode: 'orbital-period', unit: 'day', points: [{ row: 1, date: '2012-03', value: 3.501 }, { row: 0, date: '2020-05', value: 3.5 }] }] },
    { code: 'planet_radius', label: 'Planet radius', unit: 'R_earth', conversions: { R_jupiter: 'Converted' },
      rows: [
        { record: 3, field: 'radius', definition: 'planet-radius', limit: 'NONE', original: { value: '1.2', unit: 'R_jupiter', errPlus: 0.1, errMinus: 0.1 }, normalized: { value: 13.450777, errPlus: 1.120898, errMinus: 1.120898 }, compat: { verdict: 'not-comparable', reason: 'Reference row for this definition.', counts: [0, 0, 1] } },
        { record: 2, field: 'koi_prad', definition: 'planet-radius', limit: 'UPPER_LIMIT', original: { value: '15', unit: 'R_earth' }, normalized: { value: 15 }, compat: { against: 0, verdict: 'not-comparable', relation: 'not-yet-assessed', reason: 'x', counts: [0, 0, 1] } },
      ],
      set: set('planet_radius', 2, { conflict: false, relationLabel: 'not-yet-assessed', relation: 'UNASSESSED', label: 'Planet radius', canonicalUnit: 'R_earth', pairCounts: { total: 1, byRelation: { 'not-yet-assessed': 1 }, byVerdict: { consistent: 0, inconsistent: 0, 'not-comparable': 1 } }, notablePairs: [] }),
      selections: [selection('planet_radius', 'planet-radius', 11.2, null, { basis: 'calculated', chosenRow: null, errPlus: null, errMinus: null, calculated: { label: 'calculated', formulaId: 'kepler-third-law.v1', formula: 'f', inputs: [{ name: 'orbital_period', value: 3.5, unit: 'day', sourceId: 'nasa-ps', sourceRecordId: 'source-ps-000001-aaaa', reference: null }] } })],
      evolution: [] },
  ],
  status: { assertions: [{ sourceId: 'nasa-pscomppars', sourceRecordId: 'c1', sourceStatus: 'Planetary Systems member', normalized: 'CONFIRMED', rule: 'r', candidateCatalog: false }, { sourceId: 'nasa-koi', sourceRecordId: 'cumulative:7', sourceStatus: 'CANDIDATE', normalized: 'CANDIDATE', rule: 'r', candidateCatalog: true }], relation: 'status-conflict', conflict: true },
  flags: [{ flag: 'status_conflict', rule: 'Exo-MerCat status reconciliation', rows: [{ sourceId: 'nasa-koi', sourceRecordId: 'cumulative:7', value: 'CANDIDATE' }], detail: 'CANDIDATE vs CONFIRMED' }],
};
const defaultView = () => ({ filters: { method: 'all', source: 'all', state: 'all', from: '', to: '' }, units: { ...compare.DEFAULT_UNITS }, showCandidates: false });

test('rendering: one h2, one h3 per property plus status, the selection row labelled, limits by text and shape, epochs with their time system', () => {
  const html = compare.compareHtml(product, defaultView());
  assert.equal((html.match(/<h2[\s>]/g) || []).length, 1);
  assert.equal((html.match(/<h3[\s>]/g) || []).length, 3);
  assert.equal((html.match(/class="selection-row"/g) || []).length, 2);
  assert.match(html, /Selection, not a measurement/);
  const all = compare.compareHtml(product, { ...defaultView(), showCandidates: true });
  assert.match(all, /upper limit: ≤ 15 R⊕/, 'an upper limit says so in words');
  assert.match(all, /class="limit-shape" aria-hidden="true">▼/, 'and with a shape, not colour alone');
  assert.match(html, /calculated values carry no uncertainty/);
  assert.match(html, /kepler-third-law\.v1/);
  assert.match(html, /href="https:\/\/ui\.adsabs\.harvard\.edu\/abs\/2020ApJ\.\.\.900\.\.100A\/abstract"[^>]*>2020ApJ\.\.\.900\.\.100A</, 'bibcode link');
  assert.match(html, /Conflict: every row is shown/);
  assert.match(html, /Time evolution: Orbital period/);
  assert.match(html, /role="img" aria-label="Orbital period by publication date, 2 publications/);
  assert.match(html, /status conflict/);
  assert.match(html, /tabindex="0" role="region"/, 'scrollable tables are keyboard reachable');
  const levels = [...html.matchAll(/<h([1-6])[\s>]/g)].map((match) => Number(match[1]));
  assert.ok(levels.every((level, index) => index === 0 || level - levels[index - 1] <= 1), `outline ${levels}`);
});

test('candidate solutions: hidden by default, always shown for a property in conflict, shown everywhere when toggled', () => {
  const view = defaultView();
  const visible = compare.visibleRows(product, view).map(({ block, index }) => `${block.code}:${index}`);
  assert.ok(visible.includes('orbital_period:2'), 'the KOI candidate row is shown because the period set is in conflict');
  assert.ok(!visible.includes('planet_radius:1'), 'the KOI candidate radius is hidden: that set is not in conflict');
  view.showCandidates = true;
  assert.ok(compare.visibleRows(product, view).some(({ block, index }) => block.code === 'planet_radius' && index === 1));
  assert.match(compare.compareHtml(product, defaultView()), /1 candidate solution is hidden/);
});

test('filters: method, source, state and publication year', () => {
  const count = (filters) => compare.visibleRows(product, { ...defaultView(), showCandidates: true, filters: { ...defaultView().filters, ...filters } }).length;
  assert.equal(count({}), 5);
  assert.equal(count({ source: 'nasa-ps' }), 2);
  assert.equal(count({ method: 'Transit' }), 2);
  assert.equal(count({ state: 'inconsistent' }), 1);
  assert.equal(count({ state: 'limit' }), 1);
  assert.equal(count({ state: 'default parameter set' }), 1);
  assert.equal(count({ from: '2015' }), 1, 'only the 2020 publication; undated rows drop out when a date filter is set');
  assert.equal(count({ from: '2010', to: '2013' }), 1);
  const options = compare.filterOptions(product);
  assert.deepEqual(options.sources, ['exoplanet-eu', 'nasa-koi', 'nasa-ps']);
  assert.deepEqual([options.minYear, options.maxYear], [2012, 2020]);
});

test('unit toggle changes the normalised view only; the original value and unit never change', () => {
  const block = product.properties[1];
  const row = block.rows[0];
  const earth = compare.normalizedText(block, row, compare.DEFAULT_UNITS);
  const jupiter = compare.normalizedText(block, row, { ...compare.DEFAULT_UNITS, radius: 'R_jup' });
  assert.equal(earth, '13.4508 R⊕');
  assert.equal(jupiter, '1.2 R♃', 'the conversion back to Jupiter radii recovers the source value');
  assert.equal(compare.originalText(row), '1.2 R♃');
  assert.equal(compare.originalText(row), compare.originalText(structuredClone(row)));
  const years = compare.displayValue('orbital_period', 365.25, { ...compare.DEFAULT_UNITS, period: 'year' });
  assert.deepEqual([years.value, years.unit], [1, 'years (Julian, 365.25 d)']);
  assert.ok(Math.abs(compare.displayValue('semimajor_axis', 1, { ...compare.DEFAULT_UNITS, axis: 'R_sun' }).value - 215.032) < 0.01);
  const html = compare.compareHtml(product, { ...defaultView(), units: { ...compare.DEFAULT_UNITS, radius: 'R_jup' } });
  assert.match(html, /<td><span class="row-number">1\.<\/span> 1\.2 R♃/, 'original column still shows the source value');
  const view = defaultView(); view.units.radius = 'R_jup';
  const exported = compare.exportRows(product, view);
  const at = (name) => compare.EXPORT_COLUMNS.findIndex((column) => column[0] === name);
  const radius = exported.find((row) => row[at('source_field')] === 'radius');
  assert.equal(radius[at('value')], 1.2);
  assert.equal(radius[at('unit')], '71492000m');
  assert.equal(radius[at('normalized_value')], 13.450777, 'exports carry the canonical normalised value whatever the display toggle');
});

test('export: exactly the visible rows (plus the selection rows), as ECSV and VOTable with a manifest and citations', async () => {
  const view = defaultView();
  const built = await compare.buildExport(product, view, { manifest: { generatedAt: '2026-10-20T00:00:00Z', atlasGeneratedAt: '2026-10-05T14:43:07.649Z', atlasSha256: 'a'.repeat(64), conflictSets: { policyVersion: 'p', selectionRule: 'nasa-pscomppars-like.v1' } } });
  const visible = compare.visibleRows(product, view);
  const sourceRows = built.rows.filter((row) => row[3] === 'source assertion');
  assert.equal(sourceRows.length, visible.length);
  assert.deepEqual(sourceRows.map((row) => `${row[7]}/${row[10]}`), visible.map(({ row }) => `${product.records[row.record].sourceRecordId}/${row.field}`));
  assert.equal(built.rows.filter((row) => row[3] === 'selection, not a measurement').length, 2);
  const parsed = parseEcsv(built.ecsv);
  assert.equal(parsed.length, built.rows.length, 'ECSV data lines == exported rows');
  assert.deepEqual(Object.keys(parsed[0]), compare.EXPORT_COLUMNS.map((column) => column[0]));
  assert.match(built.ecsv, /^# %ECSV 1\.0/);
  assert.match(built.ecsv, /"ucd": ?"meta\.id;meta\.main"/);
  assert.match(built.votable, new RegExp(`nrows="${built.rows.length}"`));
  assert.equal((built.votable.match(/<TR>/g) || []).length, built.rows.length);
  const manifest = JSON.parse(built.manifest);
  assert.equal(manifest.rowCount, built.rows.length);
  assert.equal(manifest.files.ecsv.sha256, await compare.sha256Hex(built.ecsv));
  assert.equal(manifest.release.atlasSha256, 'a'.repeat(64));
  assert.match(built.citations, /Author et al\. 2020 \[2020ApJ\.\.\.900\.\.100A\]/);
  assert.match(built.citations, /NASA Exoplanet Archive/);
  view.filters.source = 'exoplanet-eu';
  assert.equal(compare.exportRows(product, view).filter((row) => row[3] === 'source assertion').length, 1, 'a filter narrows the export to what is shown');
  const tsv = compare.subsectionText(product, product.properties[0], defaultView());
  assert.equal(tsv.trim().split('\n').length, 1 + 1 + 3, 'header, selection, three rows');
});

test('evolution plot: circles for measurements, triangles for limits, values in the display unit', () => {
  const block = product.properties[0];
  const points = compare.evolutionPoints(product, block, block.evolution[0], compare.DEFAULT_UNITS);
  assert.deepEqual(points.map((p) => [p.date, p.value]), [['2012-03', 3.501], ['2020-05', 3.5]]);
  const svg = compare.evolutionSvg([...points, { ...points[0], year: 2016, limit: 'UPPER_LIMIT', value: 3.6, errPlus: null, errMinus: null }], 'label');
  assert.equal((svg.match(/<circle /g) || []).length, 2);
  assert.equal((svg.match(/class="evo-limit"/g) || []).length, 1);
});

test('static builder: flat conflict and selection rows, the conflict index, header acceptance and sharding', async () => {
  const detection = { objectId: 'object-test-1-b', hostName: 'Test-1', raDeg: 10, decDeg: 20 };
  const conflicts = conflictRows(product, detection, 'worldsindex-conflict-policy.v1');
  assert.equal(conflicts.length, 3, 'two multi-row sets and a disputed status');
  assert.deepEqual(conflicts.map((row) => row[7]), ['orbital_period', 'planet_radius', 'object_status']);
  const selections = selectionRows(product, detection);
  assert.equal(selections.length, 2);
  assert.equal(selections[1][29], true, 'calculated flag');
  assert.deepEqual(conflictIndexEntries(product), [['object-test-1-b', 'orbital_period', 'statistically-inconsistent', 12.5], ['object-test-1-b', 'object_status', 'status-conflict', null]]);
  assert.equal(rejectHeader({ schemaVersion: 'worldsindex-conflict-sets.v1', atlasProjectionId: 'sky-a' }, 'sky-a'), null);
  assert.match(rejectHeader({ schemaVersion: 'worldsindex-conflict-sets.v1', atlasProjectionId: 'sky-b' }, 'sky-a'), /built for atlas sky-b/);
  assert.match(rejectHeader({ schemaVersion: 'v0' }, 'sky-a'), /unsupported schema/);
  const root = await mkdtemp(join(tmpdir(), 'worldsindex-conflicts-'));
  try {
    await mkdir(join(root, 'source/public/data'), { recursive: true });
    const header = { schemaVersion: 'worldsindex-conflict-sets.v1', generatedAt: '2026-10-05T14:43:07.649Z', atlasProjectionId: 'sky-a', policy: { version: 'worldsindex-conflict-policy.v1', sigmaThreshold: 3, selectionRule: 'nasa-pscomppars-like.v1', relationVocabulary: [] }, boundary: 'b' };
    await writeFile(join(root, 'source/public/data/conflict-sets.ndjson.gz'), gzipSync(`${JSON.stringify(header)}\n${JSON.stringify(product)}\n`));
    const { artifacts, reason } = await buildConflictArtifacts({ sourceRoot: join(root, 'source'), outputRoot: join(root, 'out'), atlas: { projectionId: 'sky-a' }, detectionsById: new Map([[product.objectId, detection]]) });
    assert.equal(reason, null);
    assert.equal(artifacts.paths.filter((path) => path.startsWith('conflicts/')).length, 256);
    const shard = JSON.parse(gunzipSync(await readFile(join(root, 'out/conflicts', `${bucketFor(product.objectId)}.json.gz`))).toString('utf8'));
    assert.deepEqual(shard[product.objectId], product, 'the shard carries the product unchanged');
    const empty = JSON.parse(gunzipSync(await readFile(join(root, 'out/conflicts', `${bucketFor(product.objectId) === '00' ? '01' : '00'}.json.gz`))).toString('utf8'));
    assert.deepEqual(empty, {}, 'every bucket exists, empty ones as {}');
    assert.equal(artifacts.tables.conflicts.rows.length, 3);
    assert.equal(artifacts.counts.setsInConflict, 1);
    const missing = await buildConflictArtifacts({ sourceRoot: join(root, 'nowhere'), outputRoot: join(root, 'out2'), atlas: { projectionId: 'sky-a' }, detectionsById: new Map() });
    assert.equal(missing.artifacts, null, 'no product, no conflict block');
  } finally { await rm(root, { recursive: true, force: true }); }
});

test('changelog: new and resolved conflicts between releases; not comparable without a previous index', () => {
  const now = { sets: [['a', 'orbital_period', 'statistically-inconsistent', 5], ['b', 'object_status', 'status-conflict', null]] };
  const before = { sets: [['a', 'orbital_period', 'statistically-inconsistent', 4], ['c', 'planet_radius', 'statistically-inconsistent', 9]] };
  const diff = diffConflicts(now, before, new Map([['b', { name: 'B b' }]]));
  assert.equal(diff.comparable, true);
  assert.deepEqual(diff.new.entries.map((entry) => [entry.objectId, entry.name, entry.propertyCode]), [['b', 'B b', 'object_status']]);
  assert.deepEqual(diff.resolved.entries.map((entry) => entry.objectId), ['c']);
  assert.equal(diffConflicts(now, null).comparable, false);
  assert.equal(diffConflicts(null, before), null);
});

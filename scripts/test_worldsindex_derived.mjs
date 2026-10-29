#!/usr/bin/env node
// Track 3b: calculated quantities, the limb-darkened transit model, population plots and the
// ephemeris staleness index. Unit tests run everywhere; the release tests run when a built
// release with derived files is present (worldsindex/data/derived-summary.json).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';
import {
  deriveObject, habitableZone, chenKippingRadius, chenKippingMass, orbitFractionBetween, stalenessRows, radiusClass, massClass,
  FORMULAS, FORMULA_BY_CODE, scatterSvg, PLOTS, zengRadius, selectedInput, samePublicationInput,
} from './worldsindex_derived.mjs';
import { grayAtmosphereLimbDarkening, transitFlux, transitFluxQuadratic, occultedFractionQuadratic } from '../worldsindex/assets/photometry.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const DATA = join(ROOT, 'worldsindex', 'data');
const close = (actual, expected, tolerance, message) => assert.ok(Math.abs(actual - expected) <= tolerance, `${message}: ${actual} vs ${expected}`);

// A conflict-set product shaped like the builder's (worldsindex-conflict-sets.v1).
function selection(value, { errPlus = null, errMinus = null, unit, limitType = 'NONE', definitionCode = null, record = 'ps-1' } = {}) {
  return { purpose: 'DISPLAY_DEFAULT', selectionState: 'SELECTED', value, errPlus, errMinus, unit, limitType, definitionCode, basis: 'default-solution', chosenSourceRecordIds: [record], id: `sel-${value}` };
}
function product(properties, extraRows = {}) {
  return {
    objectId: 'object-test-b', name: 'Test b', records: [{ sourceId: 'nasa-ps', sourceRecordId: 'ps-1', reference: 0 }, { sourceId: 'nasa-transiting-planets', sourceRecordId: 'td-1', reference: 0 }],
    references: [{ label: 'Author et al. 2020', bibcode: '2020X....1....1A' }],
    properties: Object.entries(properties).map(([code, sel]) => ({ code, unit: sel?.unit ?? null, rows: extraRows[code] ?? [], selections: sel ? [sel] : [] })),
  };
}
const star = (values) => ({ records: [{ sourceId: 'nasa-stellar-hosts', sourceRecordId: 'sh-1', reference: { label: 'Author et al. 2020' }, values }] });
const hotJupiter = () => product({
  orbital_period: selection(3.52474859, { errPlus: 3.8e-7, errMinus: 3.8e-7, unit: 'day', definitionCode: 'orbital-period' }),
  planet_radius: selection(15.58, { errPlus: 0.22, errMinus: 0.22, unit: 'R_earth', definitionCode: 'planet-radius' }),
  planet_mass: selection(232.0, { errPlus: 12.7, errMinus: 12.7, unit: 'M_earth', definitionCode: 'mass' }),
  semimajor_axis: selection(0.04634, { errPlus: 0.00069, errMinus: 0.0007, unit: 'au', definitionCode: 'semimajor-axis' }),
  eccentricity: selection(0, { unit: 'dimensionless', definitionCode: 'eccentricity' }),
});
const sunLike = { st_teff: 6091, st_tefferr1: 10, st_tefferr2: -10, st_rad: 1.19, st_raderr1: 0.02, st_raderr2: -0.02, st_mass: 1.23, st_masserr1: 0.09, st_masserr2: -0.09, sy_dist: 48.3 };
const quantity = (block, code) => block.quantities.find((q) => q.code === code);

test('every formula is registered once with a citation, and codes are unique', () => {
  assert.equal(new Set(FORMULAS.map((f) => f.id)).size, FORMULAS.length);
  assert.equal(new Set(FORMULAS.map((f) => f.code)).size, FORMULAS.length);
  for (const f of FORMULAS) { assert.match(f.id, /^[a-z0-9-]+\.v\d+$/); assert.ok(f.citation?.text, f.id); assert.ok(f.formula && f.inputs.length, f.id); }
});

test('Sun and Earth reproduce the textbook values', () => {
  const block = deriveObject(product({ semimajor_axis: selection(1, { unit: 'au' }), planet_radius: selection(1, { unit: 'R_earth' }), planet_mass: selection(1, { unit: 'M_earth', definitionCode: 'mass' }), eccentricity: selection(0.0167, { unit: 'dimensionless' }), orbital_period: selection(365.25, { unit: 'day' }) }),
    star({ st_teff: 5772, st_rad: 1, st_mass: 1, sy_dist: 10 }));
  close(quantity(block, 'stellar_luminosity').value, 1, 1e-9, 'L');
  close(quantity(block, 'insolation').value, 1, 1e-9, 'S');
  close(quantity(block, 'equilibrium_temperature').value, 278.3, 0.5, 'Teq (zero albedo)');
  close(quantity(block, 'density').value, 5.514, 1e-6, 'density');
  close(quantity(block, 'earth_similarity_index').value, 1, 1e-9, 'ESI');
  close(quantity(block, 'snow_line').value, 2.7, 1e-9, 'snow line');
  close(quantity(block, 'angular_separation').value, 100, 1e-9, 'angular separation mas');
  close(quantity(block, 'rv_semi_amplitude').value, 0.0895, 0.002, 'K of the Earth (sin i = 1)');
  close(quantity(block, 'hill_radius').value, 0.0098, 0.0004, 'Earth Hill radius');
  const zone = quantity(block, 'habitable_zone');
  close(zone.edges.conservativeInner, 0.95, 0.01, 'runaway greenhouse');
  close(zone.edges.conservativeOuter, 1.67, 0.01, 'maximum greenhouse');
  assert.equal(zone.semimajorAxisIn, 'conservative');
  assert.equal(quantity(block, 'habitable_zone_orbit_fraction').conservative, 1);
  assert.equal(quantity(block, 'planet_class').value, 'Earth-size');
});

test('a hot Jupiter: values, archive values beside ours, asymmetric propagation', () => {
  const p = hotJupiter();
  p.properties.push({ code: 'equilibrium_temperature', unit: 'K', rows: [], selections: [selection(1448, { unit: 'K', record: 'ps-2' })] });
  const block = deriveObject(p, star(sunLike));
  close(quantity(block, 'density').value, 0.3378, 0.002, 'density');
  const teq = quantity(block, 'equilibrium_temperature');
  close(teq.value, 1488, 3, 'Teq');
  assert.equal(teq.archive.value, 1448);
  assert.ok(teq.errPlus > 0 && teq.errMinus > 0);
  // a with errors 0.00069 / 0.0007 makes the insolation uncertainty asymmetric.
  const s = quantity(block, 'insolation');
  assert.notEqual(s.errPlus, s.errMinus);
  assert.equal(block.inputs.stellar_teff.source, 'nasa-stellar-hosts');
  assert.equal(block.inputs.orbital_period.source, 'selection');
  assert.equal(quantity(block, 'planet_class').value, 'very large (15-22.4 R_earth)', 'Borucki et al. 2011: Jupiter-size is 6-15 R_earth');
  assert.equal(quantity(block, 'tsm').state, 'not-computed', 'no J magnitude');
  assert.equal(quantity(block, 'tsm').reason.input, 'j_mag');
});

test('limits and minimum masses give not-computed with the reason, never a value', () => {
  const p = hotJupiter();
  p.properties.find((b) => b.code === 'planet_mass').selections = [selection(300, { unit: 'M_earth', limitType: 'UPPER_LIMIT', definitionCode: 'mass' })];
  const limited = deriveObject(p, star(sunLike));
  assert.equal(quantity(limited, 'density').state, 'not-computed');
  assert.equal(quantity(limited, 'density').reason.code, 'input-is-limit');
  assert.match(quantity(limited, 'density').reason.text, /upper limit/);
  assert.equal(quantity(limited, 'density').value, undefined);
  const q = hotJupiter();
  q.properties.find((b) => b.code === 'planet_mass').selections = [selection(200, { unit: 'M_earth', definitionCode: 'minimum mass (msini)' })];
  const minimum = deriveObject(q, star(sunLike));
  assert.equal(minimum.massIsMinimum, true);
  assert.equal(quantity(minimum, 'density').reason.code, 'minimum-mass');
  assert.equal(quantity(minimum, 'roche_limit').reason.code, 'minimum-mass');
  assert.match(quantity(minimum, 'rv_semi_amplitude').massBasis, /minimum mass/);
  const noStar = deriveObject(hotJupiter(), { records: [] });
  for (const code of ['stellar_luminosity', 'insolation', 'equilibrium_temperature', 'habitable_zone', 'snow_line', 'limb_darkening']) {
    assert.equal(quantity(noStar, code).state, 'not-computed', code);
    assert.equal(quantity(noStar, code).reason.code, 'input-missing', code);
  }
});

test('stellar inputs follow the stated rule: same reference first, then most complete, then Exoplanet.eu', () => {
  const records = [
    { sourceId: 'nasa-stellar-hosts', sourceRecordId: 'sh-a', reference: { label: 'Other 2019' }, values: { st_teff: 5000, st_rad: 0.8, st_mass: 0.8 } },
    { sourceId: 'nasa-stellar-hosts', sourceRecordId: 'sh-b', reference: { label: 'Author et al. 2020' }, values: { st_teff: 6000 } },
    { sourceId: 'nasa-ps', sourceRecordId: 'ps-1', reference: { label: 'Author et al. 2020' }, values: {} },
    { sourceId: 'exoplanet-eu', sourceRecordId: 'eu-1', values: { star_distance: 55 } },
  ];
  const block = deriveObject(hotJupiter(), { records });
  assert.equal(block.inputs.stellar_teff.value, 6000, 'the parameter set of the same publication');
  assert.equal(block.inputs.stellar_radius.value, 0.8, 'a field it lacks comes from the next ranked set');
  assert.equal(block.inputs.distance.value, 55);
  assert.equal(block.inputs.distance.source, 'exoplanet-eu');
});

test('transit geometry comes only from the same publication as the selected period', () => {
  const rows = { inclination: [{ record: 1, field: 'pl_orbincl', limit: 'NONE', normalized: { value: 86.7 }, original: { value: '86.7' } }] };
  const p = product({ ...Object.fromEntries(hotJupiter().properties.map((b) => [b.code, b.selections[0]])), inclination: null }, rows);
  const input = samePublicationInput(p.properties.find((b) => b.code === 'inclination'), p, ['ps-1']);
  assert.equal(input.value, 86.7, 'same bibcode as the period row');
  const other = structuredClone(p); other.references.push({ label: 'Someone 2015' }); other.records[1].reference = 1;
  assert.equal(samePublicationInput(other.properties.find((b) => b.code === 'inclination'), other, ['ps-1']), null);
  const block = deriveObject(p, star(sunLike));
  const duration = quantity(block, 'transit_duration');
  assert.equal(duration.state, 'calculated');
  close(duration.value, 3.1, 0.25, 'T14 hours (HD 209458 b is about 3.1 h)');
});

test('Chen & Kipping branches are continuous and the inverse round-trips', () => {
  for (const m of [2.04, 131.6]) close(chenKippingRadius(m * (1 - 1e-9)), chenKippingRadius(m * (1 + 1e-9)), 1e-6, `continuity at ${m}`);
  for (const m of [0.5, 1.5, 5, 50]) close(chenKippingMass(chenKippingRadius(m)), m, 1e-9, `round trip ${m}`);
  assert.equal(chenKippingMass(20), null, 'degenerate Jovian branch');
  assert.equal(massClass(1), 'Terran'); assert.equal(massClass(300), 'Jovian'); assert.equal(radiusClass(3), 'Neptune-size');
});

test('habitable zone calibration and orbit fractions', () => {
  assert.equal(habitableZone(2500, 0.01), null);
  assert.equal(habitableZone(7300, 5), null);
  assert.equal(orbitFractionBetween(1, 0, 0.9, 1.1), 1);
  assert.equal(orbitFractionBetween(1, 0, 1.2, 1.5), 0);
  const f = orbitFractionBetween(1, 0.5, 0.9, 1.1);
  assert.ok(f > 0 && f < 0.5, `eccentric orbit spends part of its time in the band: ${f}`);
});

test('limb darkening: gray Eddington coefficients and the quadratic transit model', () => {
  const sun = grayAtmosphereLimbDarkening(5772, 786.5);
  assert.ok(sun.u1 > 0.3 && sun.u1 < 0.8 && sun.u2 > -0.2 && sun.u2 < 0.4, JSON.stringify(sun));
  const blue = grayAtmosphereLimbDarkening(5772, 551);
  assert.ok(blue.u1 > sun.u1, 'stronger limb darkening at shorter wavelengths');
  const params = { period: 3.5, epoch: 0, ratio: 0.12, scaledAxis: 8.8, impact: 0.3 };
  close(transitFluxQuadratic(0, params, 0, 0), transitFlux(0, params), 1e-9, 'u = 0 is the uniform disc');
  assert.ok(1 - transitFluxQuadratic(0, params, sun.u1, sun.u2) > 0.0144, 'a limb-darkened centre transit is deeper than k^2');
  assert.equal(occultedFractionQuadratic(2, 0.1, 0.4, 0.2), 0);
  assert.equal(transitFluxQuadratic(3.5 / 2, params, sun.u1, sun.u2), 1, 'out of transit');
});

test('staleness: propagated uncertainty, priority rule and ranking', () => {
  const artifact = { referenceBjd: 2461000, columns: ['objectId', 'name', 'status', 'periodDays', 'periodErrDays', 't0Bjd', 't0ErrDays', 'durationHours', 'sourceId', 'sourceRecordId'],
    rows: [['a', 'A', 'CONFIRMED', 2, 1e-6, 2460000, 1e-4, 2, 'nasa-ps', 'r1'], ['b', 'B', 'CONFIRMED', 2, 1e-3, 2455000, 1e-3, 2, 'nasa-koi', 'r2'], ['c', 'C', 'CANDIDATE', 2, null, 2460000, 1e-4, 2, 'nasa-toi', 'r3']] };
  const rows = stalenessRows(artifact);
  assert.deepEqual(rows.map((r) => r.objectId), ['b', 'a', 'c']);
  assert.equal(rows[0].priority, 'alert');
  assert.equal(rows[1].priority, 'low');
  assert.equal(rows[2].priority, 'unranked');
  assert.equal(rows[2].rank, null);
  const n = Math.round((2461000 + 365.25 - 2455000) / 2);
  close(rows[0].sigma12MonthMin, Math.hypot(1e-3, n * 1e-3) * 1440, 1, 'sigma at +12 months (4 significant digits)');
});

test('a scatter plot is a titled, described SVG with its points', () => {
  const svg = scatterSvg('mass-radius', PLOTS['mass-radius'], [{ x: 1, y: 1 }, { x: 300, y: 11 }]);
  assert.match(svg, /^<svg [^>]*role="img"/);
  assert.match(svg, /<title id="mass-radius-title">/);
  assert.match(svg, /Zeng et al\. 2016/);
  close(zengRadius(1, 0.325), 1.0, 0.01, 'Earth-like composition at 1 M_earth');
});

test('selectedInput reads the display selection and its limit flag', () => {
  const input = selectedInput({ unit: 'day', selections: [selection(3, { unit: 'day', limitType: 'upper_limit' })] });
  assert.equal(input.limit, 'UPPER_LIMIT');
  assert.equal(selectedInput({ selections: [] }), null);
});

const release = existsSync(join(DATA, 'derived-summary.json')) && existsSync(join(DATA, 'manifest.json'));
test('the released derived files agree with the manifest and the formula registry', { skip: !release && 'no built release with derived files' }, () => {
  const manifest = JSON.parse(readFileSync(join(DATA, 'manifest.json'), 'utf8'));
  const summary = JSON.parse(readFileSync(join(DATA, 'derived-summary.json'), 'utf8'));
  assert.ok(manifest.derivedQuantities, 'manifest.derivedQuantities');
  assert.equal(manifest.derivedQuantities.formulas, summary.formulas.length);
  const formulaIds = new Set(summary.formulas.map((f) => f.id));
  let objects = 0;
  const files = readdirSync(join(DATA, 'derived')).filter((f) => /^[0-9a-f]{2}\.json\.gz$/.test(f));
  assert.equal(files.length, 256);
  for (const file of files) {
    const shard = JSON.parse(gunzipSync(readFileSync(join(DATA, 'derived', file))).toString('utf8'));
    for (const [id, block] of Object.entries(shard.objects)) {
      objects += 1;
      assert.equal(block.objectId, id);
      for (const q of block.quantities) {
        assert.ok(formulaIds.has(q.formulaId), `${id}: ${q.formulaId}`);
        if (q.state === 'not-computed') { assert.ok(q.reason?.text, `${id} ${q.code} has a reason`); assert.equal(q.value, undefined, `${id} ${q.code} has no value`); }
      }
    }
  }
  assert.equal(objects, manifest.derivedQuantities.objects);
  assert.equal(objects, summary.objects);
  for (const [code, counts] of Object.entries(summary.quantities)) assert.equal(counts.calculated + counts.notComputed, summary.objects, code);
  for (const plot of manifest.derivedQuantities.populationPlots ? Object.values(summary.plots) : []) assert.match(readFileSync(join(DATA, plot.path), 'utf8'), /^<svg /);
  const staleness = JSON.parse(readFileSync(join(DATA, 'staleness.json'), 'utf8'));
  const ranks = staleness.rows.map((r) => r[0]).filter((r) => r !== null);
  assert.deepEqual(ranks, ranks.slice().sort((a, b) => a - b));
  assert.equal(staleness.rows.length, manifest.transitEphemerides.count);
  for (const code of ['tsm', 'esm', 'equilibrium_temperature', 'density', 'habitable_zone']) assert.ok(summary.quantities[code].calculated > 0, `${code} is calculated for some planets`);
  assert.ok(FORMULA_BY_CODE.get('tsm'));
});

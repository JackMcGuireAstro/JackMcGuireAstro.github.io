// Tests for worldsindex/assets/ephemeris.js: one catalog row -> one transit ephemeris
// (units, time standards, rejected rows), the selection policy, and uncertainty propagation.
import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { ephemerisFromRecord, selectEphemeris, propagate, transitsBetween, symmetricSigma, BKJD_OFFSET } from '../worldsindex/assets/ephemeris.js';
import { extractTransitEphemerides, ephemerisRows, COLUMNS, SCHEMA_VERSION } from './worldsindex_transit_ephemerides.mjs';

let passed = 0;
const test = (name, fn) => { fn(); passed += 1; console.log(`ok  ${name}`); };
const close = (a, b, tol, msg = '') => assert.ok(Math.abs(a - b) <= tol, `${msg} ${a} vs ${b}`);

const toi = { sourceId: 'nasa-toi', sourceRecordId: 'toi:1', values: { toi: '1150.01', tid: 16740101, tfopwg_disp: 'KP', pl_orbper: 1.0914203, pl_orbpererr1: 3e-7, pl_orbpererr2: -3e-7,
  pl_tranmid: 2459423.6, pl_tranmiderr1: 0.0002, pl_tranmiderr2: -0.0003, pl_trandurh: 3.0, pl_trandep: 14000, st_tmag: 11.2 } };
const koi = { sourceId: 'nasa-koi', sourceRecordId: 'cumulative:0', values: { kepoi_name: 'K00001.01', koi_disposition: 'CONFIRMED', koi_period: 2.470613377, koi_period_err1: 2.7e-8,
  koi_period_err2: -2.7e-8, koi_time0bk: 122.763305, koi_time0bk_err1: 8.7e-6, koi_time0bk_err2: -8.7e-6, koi_duration: 1.743, koi_depth: 14230 } };
const k2 = { sourceId: 'nasa-k2', sourceRecordId: 'k2pandc:7', values: { pl_name: 'K2-3 b', disposition: 'CONFIRMED', default_flag: 1, pl_orbper: 10.05449, pl_orbpererr1: null, pl_orbpererr2: null,
  pl_tranmid: 2456813.4177, pl_tranmiderr1: null, pl_tranmiderr2: null, pl_trandur: 2.6, pl_trandep: 0.12, pl_pubdate: '2015-01' } };
const ps = (extra = {}, values = {}) => ({ sourceId: 'nasa-ps', sourceRecordId: 'ps:' + Math.random(), isSourceDefault: false, published: '2020-01', reference: { label: 'Someone et al. 2020', href: 'https://ui.adsabs.harvard.edu/abs/x' },
  ...extra, values: { pl_orbper: 3.5, pl_orbpererr1: 1e-5, pl_orbpererr2: -1e-5, pl_tranmid: 2458000.5, pl_tranmiderr1: 0.001, pl_tranmiderr2: -0.001, pl_trandur: 2.5, pl_trandep: 1.1, sy_vmag: 9.8, ...values } });

test('TOI rows: BJD epoch, ppm depth to ppt, TESS magnitude, larger error as sigma', () => {
  const e = ephemerisFromRecord(toi);
  assert.equal(e.periodDays, 1.0914203); assert.equal(e.t0Bjd, 2459423.6); assert.equal(e.t0ErrDays, 0.0003);
  assert.equal(e.periodErrDays, 3e-7); assert.equal(e.durationHours, 3); assert.equal(e.depthPpt, 14);
  assert.deepEqual([e.hostMag, e.hostMagBand, e.timeSystem, e.sourceTable], [11.2, 'TESS', 'BJD_TDB', 'toi']);
});
test('KOI rows: BKJD epoch converted to BJD_TDB, ppm depth', () => {
  const e = ephemerisFromRecord(koi);
  close(e.t0Bjd, 122.763305 + BKJD_OFFSET, 1e-8); assert.equal(e.t0Bjd, 2454955.763305);
  assert.equal(e.depthPpt, 14.23); assert.equal(e.durationHours, 1.743); assert.equal(e.sourceTable, 'cumulative');
});
test('K2 rows: percent depth to ppt; unreported uncertainties stay null', () => {
  const e = ephemerisFromRecord(k2);
  assert.equal(e.depthPpt, 1.2); assert.equal(e.periodErrDays, null); assert.equal(e.t0ErrDays, null); assert.equal(e.isSourceDefault, true);
  assert.equal(propagate(e, 2461300).sigmaDays, null);
});
test('PS rows: percent depth, V magnitude, time-system label kept, period limits refused', () => {
  const e = ephemerisFromRecord(ps({}, { pl_tsystemref: 'BJD-TDB' }));
  assert.equal(e.depthPpt, 11); assert.equal(e.hostMagBand, 'V'); assert.equal(e.timeSystem, 'BJD-TDB'); assert.equal(e.reference.label, 'Someone et al. 2020');
  assert.equal(ephemerisFromRecord(ps({}, { pl_orbperlim: 1 })), null);
});
test('rows without period or epoch, with a reduced epoch, or marked false positive are refused', () => {
  assert.equal(ephemerisFromRecord(ps({}, { pl_tranmid: null })), null);
  assert.equal(ephemerisFromRecord(ps({}, { pl_orbper: 0 })), null);
  assert.equal(ephemerisFromRecord(ps({}, { pl_tranmid: 58000.5 })), null, 'MJD-like epochs are not BJD');
  assert.equal(ephemerisFromRecord({ ...toi, values: { ...toi.values, tfopwg_disp: 'FP' } }), null);
  assert.equal(ephemerisFromRecord({ ...toi, values: { ...toi.values, tfopwg_disp: 'FA' } }), null);
  assert.equal(ephemerisFromRecord({ ...koi, values: { ...koi.values, koi_disposition: 'FALSE POSITIVE' } }), null);
  assert.equal(ephemerisFromRecord({ ...k2, values: { ...k2.values, disposition: 'REFUTED' } }), null);
  assert.equal(ephemerisFromRecord({ sourceId: 'exoplanet-eu', values: { orbital_period: 3 } }), null, 'only NASA tables are read');
  assert.equal(ephemerisFromRecord({ ...toi, values: { ...toi.values, pl_trandurh: -0.1 } }).durationHours, null);
});
test('symmetric sigma is the larger magnitude', () => {
  assert.equal(symmetricSigma(0.1, -0.3), 0.3); assert.equal(symmetricSigma(null, -0.2), 0.2); assert.equal(symmetricSigma(null, undefined), null);
});

test('selection: PSCompPars, then PS default, then newest PS row, then the most precise mission row', () => {
  const composite = { sourceId: 'nasa-pscomppars', sourceRecordId: 'c', values: { pl_orbper: 3.5000001, pl_tranmid: 2458000.6 } };
  const def = ps({ isSourceDefault: true, published: '2010-01' }), recent = ps({ published: '2024-05' }), older = ps({ published: '2012-03' });
  assert.equal(selectEphemeris([toi, older, recent, def, composite]).ephemeris.sourceId, 'nasa-pscomppars');
  assert.equal(selectEphemeris([toi, older, recent, def]).ephemeris.sourceRecordId, def.sourceRecordId);
  assert.equal(selectEphemeris([toi, older, recent]).ephemeris.sourceRecordId, recent.sourceRecordId);
  const loose = { ...toi, sourceRecordId: 'toi:2', values: { ...toi.values, pl_orbpererr1: 3e-5, pl_orbpererr2: -3e-5 } };
  assert.equal(selectEphemeris([loose, toi, k2]).ephemeris.sourceRecordId, 'toi:1', 'smallest propagated uncertainty wins');
  assert.equal(selectEphemeris([k2, loose]).ephemeris.sourceRecordId, 'toi:2', 'rows with unreported uncertainty rank last');
  assert.equal(selectEphemeris([]).ephemeris, null);
  const { candidates } = selectEphemeris([k2, toi, koi]);
  assert.equal(candidates.length, 3);
});
test('the period and epoch always come from the same row', () => {
  const a = ps({ isSourceDefault: true }, { pl_tranmid: null, pl_orbper: 3.4 }), b = ps({ published: '2023-01' }, { pl_orbper: 3.6, pl_tranmid: 2459000.1 });
  const { ephemeris } = selectEphemeris([a, b]);
  assert.deepEqual([ephemeris.periodDays, ephemeris.t0Bjd], [3.6, 2459000.1]);
});

test('propagation: sigma = sqrt(sigmaT0^2 + (n sigmaP)^2) at the nearest transit', () => {
  const e = ephemerisFromRecord(toi), target = e.t0Bjd + 1234 * e.periodDays + 0.3;
  const p = propagate(e, target);
  assert.equal(p.epoch, 1234); close(p.midBjd, e.t0Bjd + 1234 * e.periodDays, 1e-9);
  close(p.sigmaDays, Math.sqrt(0.0003 ** 2 + (1234 * 3e-7) ** 2), 1e-15);
  assert.equal(propagate(e, e.t0Bjd - 2 * e.periodDays).epoch, -2);
});
test('transitsBetween lists every mid-time in range with ingress and egress', () => {
  const e = ephemerisFromRecord(toi), start = e.t0Bjd + 100.2 * e.periodDays, end = start + 3 * e.periodDays;
  const list = transitsBetween(e, start, end);
  assert.deepEqual(list.map((t) => t.epoch), [101, 102, 103]);
  close(list[0].egressBjd - list[0].ingressBjd, 3 / 24, 1e-12);
  close(list[0].midBjd - list[0].ingressBjd, 1.5 / 24, 1e-12);
  assert.ok(transitsBetween({ ...e, durationHours: null }, start, end).every((t) => t.ingressBjd === null));
  assert.deepEqual(transitsBetween({ ...e, periodDays: 0 }, start, end), []);
});

// ---- the release extraction over a small synthetic atlas
function atlasFixture() {
  const obj = (objectId, extra) => ({ objectId, name: objectId, hostName: 'Host', raDeg: 10, decDeg: 20, normalizedStatus: 'CONFIRMED', identityState: 'UNREVIEWED',
    methodCode: 'primary-transit-photometry', methodClaims: [{ methodCode: 'primary-transit-photometry' }], primarySourceId: 'nasa-toi', ...extra });
  const koiA = { sourceId: 'nasa-koi', sourceRecordId: 'cumulative:1', values: { kepid: 5, koi_disposition: 'CONFIRMED', koi_period: 3.0, koi_period_err1: 1e-5, koi_period_err2: -1e-5,
    koi_time0bk: 130.1, koi_time0bk_err1: 0.001, koi_time0bk_err2: -0.001, koi_duration: 2, koi_depth: 500 } };
  const toiA = { sourceId: 'nasa-toi', sourceRecordId: 'toi:1', values: { toi: '100.01', tid: 100, tfopwg_disp: 'KP', pl_orbper: 3.0001, pl_orbpererr1: 1e-6, pl_orbpererr2: -1e-6,
    pl_tranmid: 2459500.2, pl_tranmiderr1: 0.0002, pl_tranmiderr2: -0.0002, pl_trandurh: 2, pl_trandep: 520, st_tmag: 9.5 } };
  const toiOther = { sourceId: 'nasa-toi', sourceRecordId: 'toi:2', values: { toi: '100.02', tid: 100, tfopwg_disp: 'PC', pl_orbper: 6.5, pl_orbpererr1: 1e-5, pl_orbpererr2: -1e-5,
    pl_tranmid: 2459501.0, pl_tranmiderr1: 0.001, pl_tranmiderr2: -0.001, pl_trandurh: 3, pl_trandep: 300 } };
  const toiFp = { sourceId: 'nasa-toi', sourceRecordId: 'toi:3', values: { toi: '101.01', tid: 101, tfopwg_disp: 'FP', pl_orbper: 1.5, pl_tranmid: 2459400.0 } };
  const compA = { sourceId: 'nasa-pscomppars', sourceRecordId: 'c:A', values: { tic_id: 'TIC 100', pl_orbper: 3.00005, pl_tranmid: null } };
  const compB = { sourceId: 'nasa-pscomppars', sourceRecordId: 'c:B', references: { pl_tranmid: { label: 'Comp et al. 2025', href: 'https://ui.adsabs.harvard.edu/abs/b' } },
    values: { tic_id: 'TIC 200', pl_orbper: 4.2, pl_orbpererr1: 2e-6, pl_tranmid: 2460100.5, pl_tranmiderr1: 3e-4, pl_trandur: 2.2, pl_trandep: 0.9, sy_vmag: 8.1 } };
  const psB = { sourceId: 'nasa-ps', sourceRecordId: 'ps:B', isSourceDefault: true, values: { pl_orbper: 4.19, pl_tranmid: 2455000.5 } };
  const rvC = { sourceId: 'nasa-ps', sourceRecordId: 'ps:C', isSourceDefault: true, values: { pl_orbper: 50, pl_tranmid: 2455000.5 } };
  const detections = [
    obj('koi-A', { primarySourceId: 'nasa-koi' }), obj('toi-A', { name: 'TOI-100.01', hostName: 'TIC 100' }), obj('toi-A2', { name: 'TOI-100.02', normalizedStatus: 'CANDIDATE' }),
    obj('toi-fp', { normalizedStatus: 'FALSE_POSITIVE' }), obj('planet-A', { identityState: 'CANONICAL', primarySourceId: 'nasa-ps', name: 'Star b' }),
    obj('planet-B', { identityState: 'CANONICAL', primarySourceId: 'nasa-ps' }), obj('rv-C', { identityState: 'CANONICAL', methodCode: 'radial-velocity', methodClaims: [{ methodCode: 'radial-velocity' }] }),
    obj('nopos', { raDeg: null, decDeg: null, primarySourceId: 'nasa-k2' }),
  ];
  const detailsById = new Map([
    ['koi-A', { records: [koiA] }], ['toi-A', { records: [toiA] }], ['toi-A2', { records: [toiOther] }], ['toi-fp', { records: [toiFp] }],
    ['planet-A', { records: [compA, koiA] }], ['planet-B', { records: [psB, compB] }], ['rv-C', { records: [rvC] }],
    ['nopos', { records: [{ sourceId: 'nasa-k2', sourceRecordId: 'k2pandc:9', values: { pl_orbper: 2, pl_tranmid: 2457000.1, disposition: 'CONFIRMED' } }] }],
  ]);
  return { detections, detailsById };
}
test('extraction: one row per planet, rows used once, TOI rows joined by TIC and period', () => {
  const { detections, detailsById } = atlasFixture();
  const { artifact, stats } = extractTransitEphemerides({ detections, detailsById, generatedAt: '2026-10-04T00:00:00Z',
    sourceSnapshots: [{ sourceId: 'nasa-toi+nasa-koi+nasa-k2', snapshotId: 'cand-v1' }, { sourceId: 'nasa-ps+nasa-pscomppars', snapshotId: 'full-v1' }] });
  assert.equal(artifact.schemaVersion, SCHEMA_VERSION); assert.deepEqual(artifact.columns, COLUMNS); assert.equal(artifact.count, artifact.rows.length);
  assert.deepEqual(artifact.sources['nasa-koi'], { table: 'cumulative', snapshotId: 'cand-v1' });
  assert.equal(artifact.sources['nasa-pscomppars'].snapshotId, 'full-v1');
  const rows = Object.fromEntries(ephemerisRows(artifact).map((r) => [r.objectId, r]));
  assert.deepEqual(Object.keys(rows).sort(), ['planet-A', 'planet-B', 'toi-A2']);
  // planet-A: its KOI row and the TOI row of the same TIC/period compete; the TOI's recent, precise epoch wins.
  assert.equal(rows['planet-A'].sourceRecordId, 'toi:1'); assert.equal(rows['planet-A'].matchedVia, 'tic+period');
  assert.equal(rows['planet-A'].name, 'Star b'); assert.equal(rows['planet-A'].hostMag, 9.5); assert.equal(rows['planet-A'].depthPpt, 0.52);
  // planet-B: PSCompPars beats the PS default row; V magnitude and reference come with it.
  assert.equal(rows['planet-B'].sourceRecordId, 'c:B'); assert.equal(rows['planet-B'].hostMagBand, 'V'); assert.equal(rows['planet-B'].reference, 'Comp et al. 2025');
  assert.equal(rows['planet-B'].depthPpt, 9);
  // a TOI with a different period on the same star stays its own candidate
  assert.equal(rows['toi-A2'].status, 'CANDIDATE'); assert.equal(rows['toi-A2'].matchedVia, null); assert.equal(rows['toi-A2'].hostMag, 9.5, 'TESS magnitude by TIC');
  assert.equal(stats.emitted, 3); assert.equal(stats.crossMatchedToi, 1); assert.deepEqual(stats.bySource, { 'nasa-toi': 2, 'nasa-pscomppars': 1 });
  assert.equal(stats.rowAlreadyUsed, 2, 'the separate KOI and TOI records of planet A are not listed again');
});
test('extraction: without the TOI row the KOI ephemeris is used and converted from BKJD', () => {
  const { detections, detailsById } = atlasFixture();
  const { artifact } = extractTransitEphemerides({ detections: detections.filter((d) => d.objectId !== 'toi-A'), detailsById, generatedAt: '2026-10-04T00:00:00Z' });
  const a = ephemerisRows(artifact).find((r) => r.objectId === 'planet-A');
  assert.equal(a.sourceId, 'nasa-koi'); assert.equal(a.t0Bjd, 130.1 + BKJD_OFFSET); assert.equal(a.periodDays, 3.0);
});
test('extraction output is deterministic and columnar', () => {
  const { detections, detailsById } = atlasFixture();
  const once = JSON.stringify(extractTransitEphemerides({ detections, detailsById, generatedAt: '2026-10-04T00:00:00Z' }).artifact);
  const twice = JSON.stringify(extractTransitEphemerides({ detections: [...detections].reverse(), detailsById, generatedAt: '2026-10-04T00:00:00Z' }).artifact);
  assert.equal(once, twice, 'input order does not change the release');
});
if (existsSync('worldsindex/data/transit-ephemerides.json.gz')) {
  test('the built release: every row propagates to a finite mid-time tonight', () => {
    const artifact = JSON.parse(gunzipSync(readFileSync('worldsindex/data/transit-ephemerides.json.gz')).toString('utf8'));
    const rows = ephemerisRows(artifact);
    assert.ok(rows.length > 5000, `${rows.length} rows`);
    for (const r of rows.slice(0, 2000)) assert.ok(Number.isFinite(propagate(r, 2461318).midBjd), r.objectId);
  });
}

console.log(`${passed} ephemeris checks passed`);

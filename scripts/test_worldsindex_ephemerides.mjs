// Tests for worldsindex/assets/ephemeris.js: one catalog row -> one transit ephemeris
// (units, time standards, rejected rows), the selection policy, and uncertainty propagation.
import assert from 'node:assert/strict';
import { ephemerisFromRecord, selectEphemeris, propagate, transitsBetween, symmetricSigma, BKJD_OFFSET } from '../worldsindex/assets/ephemeris.js';

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

console.log(`${passed} ephemeris checks passed`);

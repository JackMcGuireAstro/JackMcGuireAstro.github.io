// Tests of the WorldsIndex transit planner (worldsindex/assets/observe.js, Framework v2 Track 4)
// and the release's precomputed 30-night list (scripts/worldsindex_observing.mjs).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { transitWindows, phaseEvents, toICS, toCSV, CSV_COLUMNS, neverHighEnough, externalLinks } from '../worldsindex/assets/observe.js';
import { observingWindows, nightOf, eligible, COLUMNS, NIGHTS } from './worldsindex_observing.mjs';
import { localNoonUtc } from '../tonight/astro.js';
import { BUILT_IN_SITES, validSite } from '../tonight/sites.js';

const fixture = JSON.parse(readFileSync(new URL('../tests/fixtures/tonight-transits.json', import.meta.url), 'utf8'));
const sec = (ms, iso) => Math.abs(ms - Date.parse(iso)) / 1000;

test('transit windows match astropy (the Tonight fixture): times within 30 s, altitudes within 0.5 degrees, full and fraction', () => {
  let checked = 0;
  for (const c of fixture.transits) {
    const s = fixture.sites[c.site], site = { id: c.site, lat: s.lat, lon: s.lon, tz: s.tz };
    const startMs = localNoonUtc(s.date, s.tz);
    const found = transitWindows(c.ephemeris, site, { startMs, nights: 1, altLimit: fixture.altLimitDeg, twilight: fixture.twilightDeg, padMinutes: 0, padSigma: 0, stepMinutes: 2 });
    if (!c.expected.listed) { assert.equal(found.length, 0, c.ephemeris.name); continue; }
    assert.equal(found.length, 1, c.ephemeris.name);
    const w = found[0], e = c.expected;
    for (const [ms, iso] of [[w.ingressMs, e.ingressUtc], [w.midMs, e.midUtc], [w.egressMs, e.egressUtc]]) assert.ok(sec(ms, iso) < 30, `${c.ephemeris.name} ${iso}`);
    assert.ok(Math.abs(w.altMid - e.altMid) < 0.5, `${c.ephemeris.name} altitude`);
    assert.equal(w.state === 'full', e.full, `${c.ephemeris.name} full`);
    assert.ok(Math.abs(w.fraction - e.observableFraction) <= 0.05, `${c.ephemeris.name} fraction ${w.fraction} vs ${e.observableFraction}`);
    assert.ok(Math.abs(w.sigmaMinutes - e.sigmaMinutes) < 1e-6);
    checked += 1;
  }
  assert.ok(checked >= 2);
});

test('the window is padded by the baseline plus k sigma; padding into twilight makes a full transit "transit only"', () => {
  const c = fixture.transits.find((t) => t.expected.full && t.expected.listed), s = fixture.sites[c.site];
  const site = { id: c.site, lat: s.lat, lon: s.lon, tz: s.tz }, startMs = localNoonUtc(s.date, s.tz);
  const base = { startMs, nights: 1, altLimit: 30, twilight: -12 };
  const [a] = transitWindows(c.ephemeris, site, { ...base, padMinutes: 30, padSigma: 2 });
  assert.ok(Math.abs((a.ingressMs - a.windowStartMs) / 60000 - (30 + 2 * a.sigmaMinutes)) < 1e-6);
  const [huge] = transitWindows(c.ephemeris, site, { ...base, padMinutes: 600, padSigma: 0 });
  assert.equal(huge.state, 'transit only', 'ten hours of padding reach daylight');
  const [limited] = transitWindows(c.ephemeris, site, { ...base, padMinutes: 0, padSigma: 0, maxAirmass: 1.01 });
  assert.ok(!limited || limited.state !== 'full', 'an airmass limit of 1.01 cannot be met throughout');
});

test('secondary eclipse sits half a period after the transit (circular estimate); quadratures at a quarter period', () => {
  const eph = { objectId: 'x', name: 'Test b', raDeg: 300, decDeg: 40, periodDays: 2, periodErrDays: 1e-6, t0Bjd: 2461300.0, t0ErrDays: 1e-4, durationHours: 2.5 };
  const site = BUILT_IN_SITES[0], startMs = Date.parse('2026-11-01T12:00:00Z');
  const opts = { startMs, nights: 20, altLimit: -90, twilight: 90 };
  const transits = transitWindows(eph, site, opts), phases = phaseEvents(eph, site, opts);
  const eclipse = phases.find((p) => p.kind === 'secondary eclipse'), next = transits.find((t) => t.midMs > eclipse.midMs);
  assert.ok(Math.abs((next.midMs - eclipse.midMs) / 86400000 - 1) < 0.01, 'one day (half of the 2-day period) apart');
  assert.ok(phases.every((p) => p.kind === 'secondary eclipse' || /quadrature/.test(p.kind)));
  assert.ok(phases.every((p) => p.estimate === 'circular orbit'));
});

test('a star that never rises above the limit gives nothing; links name the host', () => {
  const site = BUILT_IN_SITES[0];
  assert.ok(neverHighEnough({ decDeg: -60 }, site, 30));
  assert.deepEqual(transitWindows({ objectId: 'x', name: 'South b', raDeg: 10, decDeg: -60, periodDays: 1, t0Bjd: 2461300, durationHours: 2 }, site, { startMs: Date.now(), nights: 10 }), []);
  assert.match(externalLinks({ host: 'HD 209458' })[0].href, /overview\/HD%20209458$/);
});

test('iCal: CRLF lines folded at 75 octets, one VEVENT per event, escaped text; CSV has the stated columns', () => {
  const site = BUILT_IN_SITES[0];
  const events = [{ kind: 'transit', site: 'wiro', objectId: 'object-hd-209458-b', name: 'HD 209458 b, test; one', epoch: 12, midMs: Date.parse('2026-11-02T04:00:00Z'), ingressMs: Date.parse('2026-11-02T02:30:00Z'),
    egressMs: Date.parse('2026-11-02T05:30:00Z'), windowStartMs: Date.parse('2026-11-02T02:00:00Z'), windowEndMs: Date.parse('2026-11-02T06:00:00Z'), padMinutes: 30, sigmaMinutes: 0.4,
    state: 'full', fraction: 1, altMid: 55.2, airmassMid: 1.22, moonSep: 80, moonIllumination: 0.3, depthPpt: 15 }];
  const ics = toICS(events, site, { nowMs: Date.parse('2026-10-29T00:00:00Z') });
  assert.ok(ics.endsWith('\r\n') && !/[^\r]\n/.test(ics), 'CRLF only');
  for (const line of ics.split('\r\n')) assert.ok(Buffer.byteLength(line) <= 75, line);
  assert.equal((ics.match(/BEGIN:VEVENT/g) || []).length, 1);
  assert.match(ics, /DTSTART:20261102T020000Z/);
  assert.match(ics.replace(/\r\n /g, ''), /SUMMARY:HD 209458 b\\, test\\; one transit/);
  const csv = toCSV(events).split('\n');
  assert.equal(csv[0], CSV_COLUMNS.join(','));
  assert.match(csv[1], /^"HD 209458 b, test; one",transit,wiro,2026-11-02T02:00:00Z/);
});

test('custom sites: invalid profiles are refused; valid ones get a stable id', () => {
  assert.equal(validSite({ name: '', lat: 1, lon: 2 }), null);
  assert.equal(validSite({ name: 'X', lat: 91, lon: 2 }), null);
  assert.equal(validSite({ name: 'X', lat: 1, lon: 2, tz: 'Mars/Olympus' }), null);
  assert.deepEqual(validSite({ name: 'My Backyard', lat: '41.3', lon: '-105.6', elevation: '2200', tz: 'America/Denver' }),
    { id: 'custom-my-backyard', name: 'My Backyard', lat: 41.3, lon: -105.6, elevation: 2200, tz: 'America/Denver', custom: true });
});

test('the 30-night list: same rule as the page, nights in local dates, deepest first, capped, deterministic for a fixed start', () => {
  const path = new URL('../worldsindex/data/transit-ephemerides.json.gz', import.meta.url);
  const artifact = existsSync(path) ? JSON.parse(gunzipSync(readFileSync(path))) : { columns: fixture.transits.length ? Object.keys(fixture.transits[0].ephemeris) : [], rows: fixture.transits.map((t) => Object.values(t.ephemeris)) };
  const ephemerides = artifact.rows.map((row) => Object.fromEntries(artifact.columns.map((c, i) => [c, row[i]])));
  const startMs = Date.parse('2026-10-29T18:00:00Z');
  const a = observingWindows(ephemerides, { startMs }), b = observingWindows(ephemerides, { startMs });
  assert.deepEqual(a, b);
  assert.equal(COLUMNS.length, 15);
  const at = Object.fromEntries(COLUMNS.map((c, i) => [c, i]));
  const byKey = new Map();
  for (const row of a.rows) {
    const key = `${row[at.site]} ${row[at.night]}`;
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key).push(row);
    assert.ok(row[at.altMidDeg] >= 30, 'above 30 degrees at mid-transit');
    assert.ok(row[at.sigmaMinutes] <= 30);
    assert.equal(nightOf(Date.parse(row[at.midUtc]), BUILT_IN_SITES.find((s) => s.id === row[at.site]).tz), row[at.night]);
  }
  for (const rows of byKey.values()) {
    assert.ok(rows.length <= 40);
    for (let i = 1; i < rows.length; i++) assert.ok((rows[i - 1][at.depthPpt] ?? -1) >= (rows[i][at.depthPpt] ?? -1), 'deepest first');
  }
  if (existsSync(path)) {
    assert.ok(a.rows.length > 100, `${a.rows.length} windows from the published ephemerides`);
    assert.ok(eligible(ephemerides, startMs).length > 500);
    assert.ok(new Set(a.rows.map((r) => r[at.night])).size >= NIGHTS - 1);
  }
});

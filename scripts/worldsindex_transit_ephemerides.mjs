#!/usr/bin/env node
// Transit ephemerides for the WorldsIndex release (worldsindex/data/transit-ephemerides.json.gz).
//
// extractTransitEphemerides() takes the atlas detections and each object's retained catalog
// rows (the builder's detail records) and returns one row per transiting planet with a
// usable ephemeris: period and mid-transit epoch from ONE catalog row, chosen by the policy in
// worldsindex/assets/ephemeris.js (PSCompPars, then the NASA PS default row, then another PS
// row, then the most precise TOI / K2 / KOI row). Nothing is averaged across rows or sources.
//
// The same catalog row can be attached to several WorldsIndex objects (a confirmed Kepler
// planet and its separate KOI record; a TESS planet and its TOI record). Each row is used at
// most once, by the most authoritative object, and a TOI row is attached to the NASA planet
// whose host has the same TIC number and whose period agrees within 0.5%.
//
// Command line (measures a built release, e.g. after running the builder):
//   node scripts/worldsindex_transit_ephemerides.mjs worldsindex/data [--out file.json]
import { readFileSync, readdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { gunzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { selectEphemeris, ephemerisFromRecord, propagate, num, SOURCE_TABLES } from '../worldsindex/assets/ephemeris.js';

export const SCHEMA_VERSION = 'worldsindex-transit-ephemerides.v1';
export const COLUMNS = [
  'objectId', 'name', 'host', 'raDeg', 'decDeg', 'status',
  'periodDays', 'periodErrDays', 't0Bjd', 't0ErrDays', 'durationHours', 'depthPpt',
  'hostMag', 'hostMagBand', 'timeSystem', 'sourceId', 'sourceTable', 'sourceRecordId', 'reference', 'referenceUrl', 'matchedVia',
];
export const USABLE_STATUSES = new Set(['CONFIRMED', 'CANDIDATE', 'CONTROVERSIAL']);
const MISSION_SOURCES = new Set(['nasa-toi', 'nasa-koi', 'nasa-k2']);
const IDENTITY_RANK = { CANONICAL: 0, RESOLVED: 1, AMBIGUOUS: 2, UNREVIEWED: 3 };
const PRIMARY_RANK = { 'nasa-ps': 0, 'nasa-toi': 1, 'nasa-koi': 1, 'nasa-k2': 1 };
export const PERIOD_MATCH = 0.005; // relative period agreement for a TIC cross-match
export const POLICY = 'Per planet, the period and mid-transit epoch come from one catalog row, never combined across rows or sources: '
  + 'NASA PSCompPars if it reports both; else the NASA Planetary Systems default parameter set; else the most recently published PS row; '
  + 'else the TOI, K2 or KOI row with the smallest mid-time uncertainty propagated to the release date. Rows a source marks as false positive '
  + 'are never used. A TOI row is attached to a NASA planet only when both name the same TIC and their periods agree within 0.5%. '
  + 'Uncertainties are the larger of the reported upper and lower errors; null means not reported.';
export const UNITS = { periodDays: 'day', periodErrDays: 'day (1 sigma)', t0Bjd: 'BJD_TDB unless timeSystem says otherwise', t0ErrDays: 'day (1 sigma)',
  durationHours: 'hour', depthPpt: 'parts per thousand', hostMag: 'magnitude in hostMagBand', raDeg: 'degree (ICRS)', decDeg: 'degree (ICRS)' };

const ticOf = (value) => { const m = /^(?:TIC\s*)?(\d{1,12})$/i.exec(String(value ?? '').trim()); return m ? String(Number(m[1])) : null; };
const recordKey = (record) => record.sourceRecordId ?? `${record.sourceId}:${record.name}`;
const roundTo = (value, digits) => (value === null || value === undefined ? null : Number(Number(value).toFixed(digits)));
const julian = (iso) => Date.parse(iso) / 86400000 + 2440587.5;

function hostTic(records) {
  for (const r of records) {
    const tic = r.sourceId === 'nasa-toi' ? ticOf(r.values?.tid) : ticOf(r.values?.tic_id);
    if (tic) return tic;
  }
  return null;
}
function compositePeriod(object, records) {
  const composite = records.find((r) => r.sourceId === 'nasa-pscomppars' && num(r.values?.pl_orbper) > 0);
  return composite ? num(composite.values.pl_orbper) : num(object.population?.orbitalPeriodDays?.value);
}
function transiting(object, records) {
  const methods = new Set([object.methodCode, ...(object.methodClaims || []).map((c) => c.methodCode)]);
  return methods.has('primary-transit-photometry') || records.some((r) => MISSION_SOURCES.has(r.sourceId));
}

// detections: atlas detections; detailsById: Map objectId -> {records}; generatedAt: ISO time
// of the release (uncertainties are propagated to it when choosing between mission rows).
export function extractTransitEphemerides({ detections, detailsById, generatedAt = new Date().toISOString(), sourceSnapshots = [] }) {
  const referenceBjd = julian(generatedAt);
  const recordsOf = (object) => detailsById.get(object.objectId)?.records || [];
  const stats = { considered: detections.length, usableStatus: 0, withCoordinates: 0, transiting: 0, emitted: 0, crossMatchedToi: 0,
    noCandidateRow: 0, rowAlreadyUsed: 0, bySource: {}, byStatus: {}, sigmaAtRelease: {}, durationMissing: 0, depthMissing: 0, hostMagMissing: 0 };

  // TESS magnitudes by TIC, from any TOI row (a host property, used only when the chosen row has no magnitude).
  const tessMagByTic = new Map();
  for (const object of detections) for (const r of recordsOf(object)) {
    if (r.sourceId === 'nasa-toi' && ticOf(r.values?.tid) && num(r.values?.st_tmag) !== null) tessMagByTic.set(ticOf(r.values.tid), num(r.values.st_tmag));
  }

  // TOI rows of separate TOI records -> the NASA planet with the same TIC and period.
  const canonicalByTic = new Map();
  for (const object of detections) {
    if (object.identityState !== 'CANONICAL') continue;
    const records = recordsOf(object), tic = hostTic(records), period = compositePeriod(object, records);
    if (!tic || !(period > 0)) continue;
    if (!canonicalByTic.has(tic)) canonicalByTic.set(tic, []);
    canonicalByTic.get(tic).push({ objectId: object.objectId, period });
  }
  const linkedToi = new Map(); // canonical objectId -> [TOI records]
  const linkedRecordKeys = new Set();
  for (const object of detections) {
    if (object.identityState === 'CANONICAL') continue;
    for (const r of recordsOf(object)) {
      if (r.sourceId !== 'nasa-toi') continue;
      const tic = ticOf(r.values?.tid), period = num(r.values?.pl_orbper), planets = canonicalByTic.get(tic) || [];
      if (!(period > 0) || linkedRecordKeys.has(recordKey(r))) continue;
      const best = planets.map((p) => ({ ...p, off: Math.abs(period / p.period - 1) })).filter((p) => p.off < PERIOD_MATCH).sort((a, b) => a.off - b.off)[0];
      if (!best) continue;
      if (!linkedToi.has(best.objectId)) linkedToi.set(best.objectId, []);
      linkedToi.get(best.objectId).push(r);
      linkedRecordKeys.add(recordKey(r));
    }
  }

  const ordered = detections.map((object, index) => ({ object, index })).sort((a, b) =>
    (IDENTITY_RANK[a.object.identityState] ?? 9) - (IDENTITY_RANK[b.object.identityState] ?? 9)
    || (PRIMARY_RANK[a.object.primarySourceId] ?? 2) - (PRIMARY_RANK[b.object.primarySourceId] ?? 2)
    || a.index - b.index);
  const used = new Set(), rows = [];
  for (const { object } of ordered) {
    if (!USABLE_STATUSES.has(object.normalizedStatus)) continue;
    stats.usableStatus += 1;
    if (!Number.isFinite(object.raDeg) || !Number.isFinite(object.decDeg)) continue;
    stats.withCoordinates += 1;
    const own = recordsOf(object), linked = linkedToi.get(object.objectId) || [];
    if (!transiting(object, own)) continue;
    stats.transiting += 1;
    const pool = [...own.filter((r) => object.identityState === 'CANONICAL' || !linkedRecordKeys.has(recordKey(r))), ...linked];
    const fresh = pool.filter((r) => SOURCE_TABLES[r.sourceId] && !used.has(recordKey(r)));
    const { ephemeris, candidates } = selectEphemeris(fresh, { referenceBjd });
    if (!ephemeris) {
      const elsewhere = (r) => used.has(recordKey(r)) || (object.identityState !== 'CANONICAL' && linkedRecordKeys.has(recordKey(r)));
      if ([...own, ...linked].some((r) => SOURCE_TABLES[r.sourceId] && ephemerisFromRecord(r) && elsewhere(r))) stats.rowAlreadyUsed += 1;
      else stats.noCandidateRow += 1;
      continue;
    }
    for (const c of candidates) used.add(c.sourceRecordId ?? `${c.sourceId}:?`);
    for (const r of fresh) if (r.sourceId === 'nasa-toi' || r.sourceId === 'nasa-koi' || r.sourceId === 'nasa-k2') used.add(recordKey(r));
    let { hostMag, hostMagBand } = ephemeris;
    if (hostMag === null) { const t = tessMagByTic.get(hostTic([...own, ...linked])); if (t !== undefined) { hostMag = t; hostMagBand = 'TESS'; } }
    const matched = linked.some((r) => recordKey(r) === ephemeris.sourceRecordId);
    const row = {
      objectId: object.objectId, name: object.name, host: object.hostName, raDeg: roundTo(object.raDeg, 6), decDeg: roundTo(object.decDeg, 6),
      status: object.normalizedStatus, periodDays: ephemeris.periodDays, periodErrDays: ephemeris.periodErrDays, t0Bjd: ephemeris.t0Bjd,
      t0ErrDays: ephemeris.t0ErrDays, durationHours: ephemeris.durationHours, depthPpt: ephemeris.depthPpt, hostMag, hostMagBand,
      timeSystem: ephemeris.timeSystem, sourceId: ephemeris.sourceId, sourceTable: ephemeris.sourceTable, sourceRecordId: ephemeris.sourceRecordId,
      reference: ephemeris.reference?.label ?? null, referenceUrl: ephemeris.reference?.href ?? null, matchedVia: matched ? 'tic+period' : null,
    };
    rows.push(row);
    stats.emitted += 1;
    if (matched) stats.crossMatchedToi += 1;
    stats.bySource[row.sourceId] = (stats.bySource[row.sourceId] ?? 0) + 1;
    stats.byStatus[row.status] = (stats.byStatus[row.status] ?? 0) + 1;
    if (row.durationHours === null) stats.durationMissing += 1;
    if (row.depthPpt === null) stats.depthMissing += 1;
    if (row.hostMag === null) stats.hostMagMissing += 1;
    const sigma = propagate(ephemeris, referenceBjd).sigmaDays;
    const bucket = sigma === null ? 'unreported' : sigma * 1440 <= 10 ? '<=10 min' : sigma * 1440 <= 30 ? '<=30 min' : sigma * 1440 <= 60 ? '<=1 h' : sigma * 1440 <= 360 ? '<=6 h' : '>6 h';
    stats.sigmaAtRelease[bucket] = (stats.sigmaAtRelease[bucket] ?? 0) + 1;
  }
  rows.sort((a, b) => a.objectId.localeCompare(b.objectId));
  const snapshots = Object.fromEntries(sourceSnapshots.map((s) => [s.sourceId, s.snapshotId]));
  const sources = {};
  for (const sourceId of Object.keys(SOURCE_TABLES)) {
    sources[sourceId] = { table: SOURCE_TABLES[sourceId], snapshotId: Object.entries(snapshots).find(([k]) => k.split('+').includes(sourceId))?.[1] ?? null };
  }
  const artifact = {
    schemaName: 'WorldsIndex transit ephemerides', schemaVersion: SCHEMA_VERSION, generatedAt, referenceBjd: roundTo(referenceBjd, 5),
    timeStandard: 'BJD_TDB', selectionPolicy: POLICY, units: UNITS, sources, count: rows.length, columns: COLUMNS,
    rows: rows.map((row) => COLUMNS.map((c) => row[c] ?? null)),
  };
  return { artifact, stats };
}

// Rows back to objects (for consumers and tests).
export function ephemerisRows(artifact) {
  return (artifact.rows || []).map((r) => Object.fromEntries(artifact.columns.map((c, i) => [c, r[i]])));
}

// ---- command line: measure a built release directory
function loadRelease(dir) {
  const atlas = JSON.parse(gunzipSync(readFileSync(join(dir, 'sky-detections.json.gz'))).toString('utf8'));
  const detailsById = new Map();
  for (const file of readdirSync(join(dir, 'details')).filter((f) => /^[0-9a-f]{2}\.json\.gz$/.test(f))) {
    for (const [id, detail] of Object.entries(JSON.parse(gunzipSync(readFileSync(join(dir, 'details', file))).toString('utf8')))) detailsById.set(id, detail);
  }
  return { atlas, detailsById };
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const dir = process.argv[2] || 'worldsindex/data', outIndex = process.argv.indexOf('--out');
  const { atlas, detailsById } = loadRelease(dir);
  const { artifact, stats } = extractTransitEphemerides({ detections: atlas.detections, detailsById, generatedAt: new Date().toISOString(), sourceSnapshots: atlas.sourceSnapshots || [] });
  console.log(JSON.stringify({ release: dir, atlasGeneratedAt: atlas.generatedAt, ...stats }, null, 2));
  if (outIndex > 0) writeFileSync(process.argv[outIndex + 1], JSON.stringify(artifact));
}

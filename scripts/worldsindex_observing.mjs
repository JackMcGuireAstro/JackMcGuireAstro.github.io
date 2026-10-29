// The precomputed 30-night transit list for WIRO and Red Buttes (Framework v2, Track 4), written
// each build as worldsindex/data/observing-windows.json from the release's transit ephemerides with
// the same functions the planet page uses (worldsindex/assets/observe.js).
//
// Rule (stated in the file): confirmed or controversial planets with a catalog duration whose
// mid-time 1-sigma uncertainty at the start of the window is at most 30 minutes; a transit is kept
// when its window (ingress to egress padded by 30 minutes plus 1 sigma each side) is "full": dark
// (Sun below -12 degrees) with the star above 30 degrees throughout. Per site and night, the deepest
// transits first (then earliest), at most 40.
import { writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { transitWindows, OBSERVE_SCHEMA, RULE } from '../worldsindex/assets/observe.js';
import { propagate } from '../worldsindex/assets/ephemeris.js';
import { utcMsToBjdTdb, localNoonUtc } from '../tonight/astro.js';
import { BUILT_IN_SITES } from '../tonight/sites.js';

export const ARTIFACT = 'observing-windows.json';
export const NIGHTS = 30;
export const MAX_SIGMA_MINUTES = 30;
export const PER_NIGHT = 40;
export const OPTIONS = { altLimit: 30, twilight: -12, padMinutes: 30, padSigma: 1, maxAirmass: null, haLimitHours: null, stepMinutes: 5 };
export const SELECTION = `Confirmed or controversial planets with a catalog duration and a mid-time uncertainty of at most ${MAX_SIGMA_MINUTES} minutes at the start; ` +
  `windows that are "full" (dark, Sun below -12 degrees, star above 30 degrees throughout the padded window); per site and night the deepest first, then earliest, at most ${PER_NIGHT}.`;
export const COLUMNS = ['site', 'night', 'objectId', 'name', 'host', 'windowStartUtc', 'ingressUtc', 'midUtc', 'egressUtc', 'windowEndUtc', 'sigmaMinutes', 'depthPpt', 'altMidDeg', 'moonSepDeg', 'moonIllumination'];

const iso = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
const round = (value, digits) => (value === null || value === undefined ? null : Number(Number(value).toFixed(digits)));

// The local calendar date (YYYY-MM-DD) whose night contains `ms`: the night starting at local noon.
export function nightOf(ms, timeZone) {
  const parts = Object.fromEntries(new Intl.DateTimeFormat('en-US', { timeZone, hourCycle: 'h23', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit' })
    .formatToParts(new Date(ms - 12 * 3600000)).map((p) => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export function eligible(ephemerides, startMs) {
  return ephemerides.filter((e) => (e.status === 'CONFIRMED' || e.status === 'CONTROVERSIAL') && e.durationHours > 0 && e.periodDays > 0 && Number.isFinite(e.t0Bjd)
    && Number.isFinite(e.raDeg) && Number.isFinite(e.decDeg)).filter((e) => {
    const sigma = propagate(e, utcMsToBjdTdb(startMs, e.raDeg, e.decDeg)).sigmaDays;
    return sigma !== null && sigma * 1440 <= MAX_SIGMA_MINUTES;
  });
}

// `ephemerides`: rows of transit-ephemerides.json.gz as objects. `startMs`: the build time; the
// window starts at local noon of that day at each site.
export function observingWindows(ephemerides, { startMs = Date.now(), sites = BUILT_IN_SITES } = {}) {
  const pool = eligible(ephemerides, startMs);
  const rows = [], perSite = {};
  for (const site of sites) {
    const startNoon = localNoonUtc(nightOf(startMs, site.tz), site.tz);
    const kept = [];
    for (const eph of pool) {
      for (const w of transitWindows(eph, site, { ...OPTIONS, startMs: startNoon, nights: NIGHTS })) if (w.state === 'full') kept.push(w);
    }
    const byNight = new Map();
    for (const w of kept) {
      const night = nightOf(w.midMs, site.tz);
      if (!byNight.has(night)) byNight.set(night, []);
      byNight.get(night).push(w);
    }
    let count = 0;
    for (const night of [...byNight.keys()].sort()) {
      const list = byNight.get(night).sort((a, b) => (b.depthPpt ?? -1) - (a.depthPpt ?? -1) || a.midMs - b.midMs || a.name.localeCompare(b.name)).slice(0, PER_NIGHT);
      for (const w of list) {
        rows.push([site.id, night, w.objectId, w.name, w.host ?? null, iso(w.windowStartMs), iso(w.ingressMs), iso(w.midMs), iso(w.egressMs), iso(w.windowEndMs),
          round(w.sigmaMinutes, 1), round(w.depthPpt, 3), round(w.altMid, 1), round(w.moonSep, 1), round(w.moonIllumination, 2)]);
        count += 1;
      }
    }
    perSite[site.id] = { nights: byNight.size, transits: count, fullWindowsBeforeCap: kept.length };
  }
  return { pool: pool.length, rows, perSite };
}

export async function buildObservingArtifacts({ outputRoot, transitEphemerides, generatedAt, startMs = Date.now() }) {
  const ephemerides = (transitEphemerides?.rows ?? []).map((row) => Object.fromEntries(transitEphemerides.columns.map((c, i) => [c, row[i]])));
  const { pool, rows, perSite } = observingWindows(ephemerides, { startMs });
  const document = {
    schemaName: OBSERVE_SCHEMA, schemaVersion: '1.0.0', generatedAt, windowStart: iso(startMs), nights: NIGHTS,
    sites: BUILT_IN_SITES.map((s) => ({ id: s.id, name: s.name, latitudeDeg: s.lat, longitudeDeg: s.lon, elevationM: s.elevation, timezone: s.tz })),
    selection: SELECTION, windowRule: RULE, parameters: { ...OPTIONS, maxSigmaMinutes: MAX_SIGMA_MINUTES, perNight: PER_NIGHT },
    ephemerisSource: 'transit-ephemerides.json.gz (one catalog row per planet; see its selectionPolicy)', eligiblePlanets: pool, perSite,
    claimBoundary: 'Geometric predictions from catalog ephemerides with almanac-accuracy Sun and Moon; no weather, refraction, horizon or telescope limits. Check the ephemeris before observing.',
    columns: COLUMNS, rows,
  };
  await writeFile(join(outputRoot, ARTIFACT), JSON.stringify(document) + '\n');
  return { path: ARTIFACT, counts: { eligible: pool, rows: rows.length, perSite } };
}

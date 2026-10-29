// Observing a planet from WIRO, Red Buttes or the reader's own site (Framework v2, Track 4):
// the transits (and, as circular-orbit estimates, the secondary eclipse and quadratures) of the
// coming nights that fall in the dark with the star high enough, with the baseline padded by the
// ephemeris uncertainty; iCal and CSV export. The same functions build the release's
// precomputed 30-night list for WIRO and Red Buttes (scripts/worldsindex_observing.mjs).
// No DOM below the "page" section, no network: it runs the same in Node and in the browser.
import { julianDate, sunPosition, moonPosition, moonIllumination, separationDeg, altitudeDeg, airmass,
  utcMsToBjdTdb, bjdTdbToUtcMs, localSiderealDeg } from '../../tonight/astro.js?v=20261004';
import { transitsBetween } from './ephemeris.js?v=20261017';
import { BUILT_IN_SITES, savedSites } from '../../tonight/sites.js?v=20261029';

export const OBSERVE_SCHEMA = 'worldsindex.observing-windows.v1';
export const DEFAULTS = { nights: 30, altLimit: 30, maxAirmass: null, twilight: -12, haLimitHours: null, padMinutes: 30, padSigma: 1, stepMinutes: 2 };
export const RULE = 'A window is the transit from ingress to egress padded on each side by a fixed baseline plus k times the 1-sigma mid-time uncertainty ' +
  '(sigma = sqrt(sigma_T0^2 + (n sigma_P)^2)). It is "full" when every sample of the padded window (every 2 minutes) is in the dark (Sun below the twilight ' +
  'limit) with the star above the altitude limit, below the airmass limit and within the hour-angle limit when those are set; "transit only" when the ' +
  'transit itself is but the padding is not; "partial" otherwise, with the share of the transit that is. Secondary eclipses and quadratures are ' +
  'circular-orbit estimates at phases 0.5, 0.25 and 0.75 (no eccentricity is applied).';
const DAY = 86400000;

function hourAngleHours(raDeg, lonDeg, ms) {
  let ha = (localSiderealDeg(ms, lonDeg) - raDeg) / 15;
  while (ha > 12) ha -= 24;
  while (ha < -12) ha += 24;
  return ha;
}
function sunAltDeg(ms, site) {
  const sun = sunPosition(julianDate(ms));
  return altitudeDeg(sun.ra, sun.dec, site.lat, localSiderealDeg(ms, site.lon));
}
// Whether the sky and the star allow observing at `ms`.
function goodAt(ms, eph, site, o) {
  if (sunAltDeg(ms, site) >= o.twilight) return false;
  const alt = altitudeDeg(eph.raDeg, eph.decDeg, site.lat, localSiderealDeg(ms, site.lon));
  if (alt < o.altLimit) return false;
  if (o.maxAirmass !== null && o.maxAirmass !== undefined) { const x = airmass(alt); if (x === null || x > o.maxAirmass) return false; }
  if (o.haLimitHours !== null && o.haLimitHours !== undefined && Math.abs(hourAngleHours(eph.raDeg, site.lon, ms)) > o.haLimitHours) return false;
  return true;
}
function share(fromMs, toMs, eph, site, o) {
  const n = Math.max(2, Math.round((toMs - fromMs) / (o.stepMinutes * 60000)) + 1);
  let good = 0;
  for (let i = 0; i < n; i++) if (goodAt(fromMs + ((toMs - fromMs) * i) / (n - 1), eph, site, o)) good += 1;
  return good / n;
}
// The star cannot reach the altitude limit from this latitude.
export function neverHighEnough(eph, site, altLimit) { return 90 - Math.abs(site.lat - eph.decDeg) < altLimit; }

// Transit windows of one ephemeris at one site from `startMs` for `nights` nights.
export function transitWindows(eph, site, options = {}) {
  const o = { ...DEFAULTS, ...options };
  const startMs = options.startMs ?? Date.now(), endMs = startMs + o.nights * DAY;
  if (!(eph?.periodDays > 0) || !(eph.durationHours > 0) || !Number.isFinite(eph.t0Bjd) || neverHighEnough(eph, site, o.altLimit)) return [];
  const out = [];
  const startBjd = utcMsToBjdTdb(startMs, eph.raDeg, eph.decDeg), endBjd = utcMsToBjdTdb(endMs, eph.raDeg, eph.decDeg);
  for (const t of transitsBetween(eph, startBjd, endBjd)) {
    const ingressMs = bjdTdbToUtcMs(t.ingressBjd, eph.raDeg, eph.decDeg), midMs = bjdTdbToUtcMs(t.midBjd, eph.raDeg, eph.decDeg), egressMs = bjdTdbToUtcMs(t.egressBjd, eph.raDeg, eph.decDeg);
    if (midMs < startMs || midMs > endMs) continue;
    const sigmaMinutes = t.sigmaDays === null ? null : t.sigmaDays * 1440;
    const padMs = (o.padMinutes + (sigmaMinutes === null ? 0 : o.padSigma * sigmaMinutes)) * 60000;
    const transitShare = share(ingressMs, egressMs, eph, site, o);
    if (!transitShare) continue;
    const paddedShare = share(ingressMs - padMs, egressMs + padMs, eph, site, o);
    const lst = localSiderealDeg(midMs, site.lon), altMid = altitudeDeg(eph.raDeg, eph.decDeg, site.lat, lst);
    const moon = moonPosition(julianDate(midMs));
    out.push({
      kind: 'transit', objectId: eph.objectId, name: eph.name, host: eph.host, site: site.id, epoch: t.epoch, midBjd: t.midBjd,
      ingressMs, midMs, egressMs, windowStartMs: ingressMs - padMs, windowEndMs: egressMs + padMs, padMinutes: padMs / 60000,
      sigmaMinutes, depthPpt: eph.depthPpt ?? null, durationHours: eph.durationHours,
      state: paddedShare === 1 ? 'full' : transitShare === 1 ? 'transit only' : 'partial', fraction: transitShare,
      altMid, airmassMid: airmass(altMid), hourAngleMid: hourAngleHours(eph.raDeg, site.lon, midMs),
      moonSep: separationDeg(eph.raDeg, eph.decDeg, moon.ra, moon.dec), moonIllumination: moonIllumination(julianDate(midMs)),
    });
  }
  return out;
}

// Secondary eclipse and quadratures (circular orbit) that fall in the dark with the star high enough.
export function phaseEvents(eph, site, options = {}) {
  const o = { ...DEFAULTS, ...options };
  const startMs = options.startMs ?? Date.now(), endMs = startMs + o.nights * DAY;
  if (!(eph?.periodDays > 0) || !Number.isFinite(eph.t0Bjd) || neverHighEnough(eph, site, o.altLimit)) return [];
  const out = [];
  for (const [kind, phase] of [['secondary eclipse', 0.5], ['quadrature (0.25)', 0.25], ['quadrature (0.75)', 0.75]]) {
    const shifted = { ...eph, t0Bjd: eph.t0Bjd + phase * eph.periodDays, durationHours: kind === 'secondary eclipse' ? eph.durationHours : null };
    const startBjd = utcMsToBjdTdb(startMs, eph.raDeg, eph.decDeg), endBjd = utcMsToBjdTdb(endMs, eph.raDeg, eph.decDeg);
    for (const t of transitsBetween(shifted, startBjd, endBjd)) {
      const midMs = bjdTdbToUtcMs(t.midBjd, eph.raDeg, eph.decDeg);
      if (midMs < startMs || midMs > endMs || !goodAt(midMs, eph, site, o)) continue;
      const altMid = altitudeDeg(eph.raDeg, eph.decDeg, site.lat, localSiderealDeg(midMs, site.lon));
      out.push({ kind, objectId: eph.objectId, name: eph.name, site: site.id, epoch: t.epoch, midMs, sigmaMinutes: t.sigmaDays === null ? null : t.sigmaDays * 1440,
        ingressMs: t.ingressBjd === null ? null : bjdTdbToUtcMs(t.ingressBjd, eph.raDeg, eph.decDeg), egressMs: t.egressBjd === null ? null : bjdTdbToUtcMs(t.egressBjd, eph.raDeg, eph.decDeg),
        altMid, airmassMid: airmass(altMid), estimate: 'circular orbit' });
    }
  }
  return out.sort((a, b) => a.midMs - b.midMs);
}

// ------------------------------------------------------------------ export
const isoUtc = (ms) => new Date(ms).toISOString().replace(/\.\d{3}Z$/, 'Z');
const icsTime = (ms) => isoUtc(ms).replace(/[-:]/g, '');
const icsText = (s) => String(s ?? '').replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n');
function fold(line) {
  const out = [];
  let rest = line;
  while (rest.length > 74) { out.push(rest.slice(0, 74)); rest = ' ' + rest.slice(74); }
  out.push(rest);
  return out.join('\r\n');
}
export function toICS(events, site, { nowMs = Date.now() } = {}) {
  const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//jackmcguireastro.github.io//WorldsIndex transit planner//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH'];
  for (const e of events) {
    const start = e.windowStartMs ?? e.ingressMs ?? e.midMs, end = e.windowEndMs ?? e.egressMs ?? e.midMs + 15 * 60000;
    lines.push('BEGIN:VEVENT', `UID:${icsText(`${e.objectId}-${e.kind.replace(/[^a-z0-9]+/gi, '')}-${e.epoch}-${site.id}`)}@jackmcguireastro.github.io`, `DTSTAMP:${icsTime(nowMs)}`,
      `DTSTART:${icsTime(start)}`, `DTEND:${icsTime(end)}`, `SUMMARY:${icsText(`${e.name} ${e.kind} (${site.name})`)}`,
      `DESCRIPTION:${icsText(`Mid ${isoUtc(e.midMs)}${e.sigmaMinutes === null || e.sigmaMinutes === undefined ? ' (timing uncertainty not reported)' : ` ± ${Math.max(1, Math.round(e.sigmaMinutes))} min (1 sigma)`}` +
        (e.kind === 'transit' ? `; ingress ${isoUtc(e.ingressMs)}, egress ${isoUtc(e.egressMs)}; window padded by ${Math.round(e.padMinutes)} min each side; ${e.state}` : `; ${e.estimate} estimate`) +
        `; altitude at mid ${Math.round(e.altMid)} deg. Prediction from WorldsIndex; check before observing.`)}`,
      `LOCATION:${icsText(`${site.name} (${site.lat.toFixed(4)}, ${site.lon.toFixed(4)})`)}`, 'END:VEVENT');
  }
  lines.push('END:VCALENDAR');
  return lines.map(fold).join('\r\n') + '\r\n';
}
const csvCell = (v) => { const s = v === null || v === undefined ? '' : String(v); return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
export const CSV_COLUMNS = ['planet', 'kind', 'site', 'window_start_utc', 'ingress_utc', 'mid_utc', 'egress_utc', 'window_end_utc', 'sigma_min', 'state', 'fraction_seen', 'alt_mid_deg', 'airmass_mid', 'moon_sep_deg', 'depth_ppt'];
export function toCSV(events) {
  const rows = events.map((e) => [e.name, e.kind, e.site, e.windowStartMs ? isoUtc(e.windowStartMs) : '', e.ingressMs ? isoUtc(e.ingressMs) : '', isoUtc(e.midMs), e.egressMs ? isoUtc(e.egressMs) : '',
    e.windowEndMs ? isoUtc(e.windowEndMs) : '', e.sigmaMinutes === null || e.sigmaMinutes === undefined ? '' : e.sigmaMinutes.toFixed(1), e.state ?? e.estimate ?? '',
    e.fraction === undefined ? '' : e.fraction.toFixed(2), e.altMid.toFixed(1), e.airmassMid === null ? '' : e.airmassMid.toFixed(3), e.moonSep === undefined ? '' : e.moonSep.toFixed(1), e.depthPpt ?? '']);
  return [CSV_COLUMNS.join(','), ...rows.map((r) => r.map(csvCell).join(','))].join('\n') + '\n';
}

// ------------------------------------------------------------------ links
export function externalLinks(eph) {
  const host = encodeURIComponent(String(eph?.host ?? '').trim());
  return [
    { label: 'NASA Exoplanet Archive overview', href: host ? `https://exoplanetarchive.ipac.caltech.edu/overview/${host}` : 'https://exoplanetarchive.ipac.caltech.edu/' },
    { label: 'NASA Transit and Ephemeris Service', href: 'https://exoplanetarchive.ipac.caltech.edu/cgi-bin/TransitView/nph-visibletbls?dataset=transits' },
    { label: 'JWST target visibility tools', href: 'https://jwst-docs.stsci.edu/jwst-other-tools/target-visibility-tools' },
  ];
}

// ------------------------------------------------------------------ page
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
let ephemeridesPromise = null;
async function loadEphemerides() {
  ephemeridesPromise ??= (async () => {
    const r = await fetch(new URL('../data/transit-ephemerides.json.gz', import.meta.url));
    if (!r.ok) throw new Error(`transit ephemerides unavailable (${r.status})`);
    const text = r.headers.get('content-encoding') === 'gzip' ? await r.text() : await new Response(r.body.pipeThrough(new DecompressionStream('gzip'))).text();
    const artifact = JSON.parse(text);
    return (artifact.rows || []).map((row) => Object.fromEntries(artifact.columns.map((c, i) => [c, row[i]])));
  })();
  return ephemeridesPromise;
}
export function sitesForPlanner() { return [...BUILT_IN_SITES, ...savedSites()]; }

function eventsTable(events, site) {
  const tz = site.tz;
  const t = (ms) => ms == null ? '—' : new Intl.DateTimeFormat('en-GB', { timeZone: tz, month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }).format(new Date(ms));
  return `<div class="table-wrap" role="region" tabindex="0" aria-label="Observable events at ${esc(site.name)}"><table class="observe-table"><caption class="sr-only">Observable events at ${esc(site.name)}</caption><thead><tr><th scope="col">Event</th><th scope="col">Window start</th><th scope="col">Mid ±1σ</th><th scope="col">Window end</th><th scope="col">State</th><th scope="col">Alt (mid)</th><th scope="col">Moon</th></tr></thead><tbody>` +
    events.map((e) => `<tr><th scope="row">${esc(e.kind)}</th><td>${t(e.windowStartMs ?? e.ingressMs)}</td><td>${t(e.midMs)} <small>${e.sigmaMinutes == null ? 'σ unknown' : `±${Math.max(1, Math.round(e.sigmaMinutes))} min`}</small></td>` +
      `<td>${t(e.windowEndMs ?? e.egressMs)}</td><td>${esc(e.state ?? `${e.estimate} estimate`)}${e.kind === 'transit' && e.state === 'partial' ? ` <small>(${Math.round(e.fraction * 100)}%)</small>` : ''}</td>` +
      `<td>${Math.round(e.altMid)}° <small>X ${e.airmassMid ? e.airmassMid.toFixed(2) : '—'}</small></td><td>${e.moonSep === undefined ? '—' : `${Math.round(e.moonSep)}°, ${Math.round(e.moonIllumination * 100)}%`}</td></tr>`).join('') +
    `</tbody></table></div>`;
}

/** Mounts the "Observe this planet" section on the System & orbit tab (absent without an ephemeris). */
export async function mountObserve(panel, object) {
  const ephemerides = await loadEphemerides().catch(() => null);
  const eph = ephemerides?.find((row) => row.objectId === object.objectId) ?? null;
  let section = panel.querySelector('#observe-section');
  if (!eph) { section?.remove(); return false; }
  if (!section) { section = document.createElement('section'); section.id = 'observe-section'; section.className = 'observe-section'; section.setAttribute('aria-labelledby', 'observe-title'); panel.appendChild(section); }
  if (section.dataset.objectId === object.objectId) return true;
  section.dataset.objectId = object.objectId;
  const sites = sitesForPlanner();
  section.innerHTML = `<h2 id="observe-title">Observe this planet</h2><p class="plain-language-summary">Transits${eph.durationHours ? '' : ' (no catalog duration, so only the phase events)'} over the coming nights at a site, with the baseline padded by the ephemeris uncertainty; secondary eclipses and quadratures are circular-orbit estimates. Computed in your browser.</p>` +
    `<form class="observe-controls" data-observe-form><label>Site <select name="site">${sites.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join('')}</select></label>` +
    `<label>Nights <select name="nights"><option>7</option><option selected>30</option><option>90</option></select></label>` +
    `<label>Lowest altitude <select name="alt"><option>20</option><option selected>30</option><option>40</option><option>50</option></select></label>` +
    `<label>Highest airmass <select name="airmass"><option value="">no limit</option><option>2.0</option><option>1.6</option><option>1.3</option></select></label>` +
    `<label>Twilight <select name="twilight"><option value="-6">civil</option><option value="-12" selected>nautical</option><option value="-18">astronomical</option></select></label>` +
    `<label>Hour angle within <select name="ha"><option value="">no limit</option><option>4</option><option>3</option><option>2</option></select></label>` +
    `<label>Baseline (min) <input name="pad" type="number" min="0" max="240" step="5" value="30"></label>` +
    `<label>+ k × σ <select name="k"><option>0</option><option selected>1</option><option>2</option><option>3</option></select></label>` +
    `<label class="observe-check"><input name="phases" type="checkbox"> secondary eclipse and quadratures</label></form>` +
    `<div data-observe-result role="status" aria-live="polite"></div><p class="fineprint">${esc(RULE)} Links: ${externalLinks(eph).map((l) => `<a href="${esc(l.href)}" rel="noopener">${esc(l.label)}</a>`).join(' · ')}.</p>`;
  const form = section.querySelector('[data-observe-form]'), out = section.querySelector('[data-observe-result]');
  const draw = () => {
    const f = new FormData(form), site = sites.find((s) => s.id === f.get('site')) || sites[0];
    const options = { nights: Number(f.get('nights')), altLimit: Number(f.get('alt')), maxAirmass: f.get('airmass') ? Number(f.get('airmass')) : null,
      twilight: Number(f.get('twilight')), haLimitHours: f.get('ha') ? Number(f.get('ha')) : null, padMinutes: Number(f.get('pad')) || 0, padSigma: Number(f.get('k')) };
    const events = [...transitWindows(eph, site, options), ...(f.get('phases') ? phaseEvents(eph, site, options) : [])].sort((a, b) => a.midMs - b.midMs);
    out.innerHTML = events.length
      ? `<p>${events.filter((e) => e.kind === 'transit').length} transit window(s) in ${options.nights} nights at ${esc(site.name)} (${events.filter((e) => e.state === 'full').length} full). Times in ${esc(site.tz)}.</p>${eventsTable(events, site)}` +
        `<p><button type="button" class="text-link" data-observe-download="ics">Download as iCal</button> · <button type="button" class="text-link" data-observe-download="csv">Download as CSV</button></p>`
      : `<p>No event fits at ${esc(site.name)} in ${options.nights} nights with these limits${neverHighEnough(eph, site, options.altLimit) ? ` (the star never rises above ${options.altLimit}° there)` : ''}.</p>`;
    out.querySelectorAll('[data-observe-download]').forEach((button) => button.addEventListener('click', () => {
      const ics = button.dataset.observeDownload === 'ics';
      const body = ics ? toICS(events, site) : toCSV(events);
      const link = document.createElement('a');
      link.href = URL.createObjectURL(new Blob([body], { type: ics ? 'text/calendar' : 'text/csv' }));
      link.download = `${String(eph.name).replace(/[^A-Za-z0-9_.-]+/g, '_')}-${site.id}.${ics ? 'ics' : 'csv'}`;
      document.body.appendChild(link); link.click(); link.remove();
    }));
  };
  form.addEventListener('change', draw);
  form.addEventListener('submit', (event) => { event.preventDefault(); draw(); });
  draw();
  return true;
}

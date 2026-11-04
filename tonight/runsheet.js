// The printable run sheet (Framework v2 Track 4, with the shared run list since 2026-11-04): for a
// list of CTAS transients and WorldsIndex planets and one site and night, each target's
// coordinates, tonight's window and airmass curve, a finder chart, the exposure at WIRO and Red
// Buttes, and (for a planet) tonight's transit window. The list is, in order of precedence:
//   1. a link's own list (?list=<share code>, ?events=<uuid,…>, ?planets=<objectId,…>);
//   2. the visitor's run list (tools/lib/run-cart.js, kept in this browser only);
//   3. the ten CTAS follow-up targets up longest tonight.
// Computed in the browser from the published CTAS summary and WorldsIndex ephemerides; nothing is
// sent anywhere (the finder images are plain <img> requests to CDS hips2fits).
import "../tools/lib/run-cart.js?v=20261104";
import { nightGrid, visibility, localNoonUtc } from "./astro.js?v=20261004";
import { SITES, ctasTargets, followupFor, altitudeChartSvg, darkWindow } from "./planner.js?v=20261104";
import { savedSites } from "./sites.js?v=20261029";
import { transitWindows } from "../worldsindex/assets/observe.js?v=20261104";
import { SURVEYS, hips2fitsUrl, overlaySvg } from "../tools/finder/finder.js?v=20261003";
import { formatRA, formatDec } from "../tools/lib/astrometry.js?v=20261003";
import * as ccd from "../tools/exposure/ccd.js?v=20261029";

export const MAX_TARGETS = 20;   // the "top N tonight" sheet
export const MAX_LIST = 50;      // a listed sheet (the run list holds at most 50)
export const DEFAULT_TOP = 10;
export const FINDER_ARCMIN = 8;
const SPECTRUM_LABELS = { feasible: "feasible", marginal: "marginal", "too-faint": "too faint", "not-available": "no spectrograph", unknown: "undetermined" };
const Followup = globalThis.CTASFollowup;
const Cart = () => globalThis.RunCart;
const $ = (selector) => document.querySelector(selector);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const OBJECT_ID = /^[A-Za-z0-9._:+-]{1,120}$/;
const DAY = 86400000;

// ?events=<uuid>,…&planets=<objectId>,…&list=<share code>&site=wiro&date=YYYY-MM-DD&top=10&alt=30.
// Unknown values are dropped. `list` is the decoded share code (items as the run list keeps them).
export function parseQuery(params, sites = SITES) {
  const out = { events: [], planets: [], list: [], top: null };
  const events = (params.get("events") || "").split(",").map((s) => s.trim()).filter((s) => UUID.test(s));
  out.events = [...new Set(events)].slice(0, MAX_LIST);
  const planets = (params.get("planets") || "").split(",").map((s) => s.trim()).filter((s) => OBJECT_ID.test(s));
  out.planets = [...new Set(planets)].slice(0, MAX_LIST);
  if (params.get("list") && Cart()) out.list = Cart().decode(params.get("list"));
  const top = Number(params.get("top"));
  if (!out.events.length && !out.planets.length && !out.list.length) out.top = Number.isInteger(top) && top > 0 ? Math.min(top, MAX_TARGETS) : DEFAULT_TOP;
  if (sites.some((s) => s.id === params.get("site"))) out.site = params.get("site");
  if (/^\d{4}-\d{2}-\d{2}$/.test(params.get("date") || "")) out.date = params.get("date");
  const alt = Number(params.get("alt"));
  out.altLimit = [20, 30, 40, 50].includes(alt) ? alt : 30;
  return out;
}

// Which list the sheet shows: the link's own ("link"), the visitor's run list ("cart") or the
// top targets tonight ("top"). Items are {kind, id, name?, ra?, dec?, note?} in sheet order.
export function sheetSource(query, cartItems) {
  if (query.events.length || query.planets.length || query.list.length) {
    const items = [...query.list, ...query.events.map((id) => ({ kind: "ctas", id })), ...query.planets.map((id) => ({ kind: "planet", id }))];
    const seen = new Set();
    return { mode: "link", items: items.filter((x) => { const k = `${x.kind}\u0000${x.id}`; if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, MAX_LIST) };
  }
  if (cartItems && cartItems.length) return { mode: "cart", items: cartItems.slice(0, MAX_LIST) };
  return { mode: "top", items: [] };
}

// The finder survey for a declination: Pan-STARRS colour where it exists (Dec > −30°), else DSS2 red.
export function finderSurvey(dec) {
  return dec > -29.5 ? SURVEYS.find((s) => s.id === "ps1-color") : SURVEYS.find((s) => s.id === "dss2-red");
}

const skyPreset = (moonIllumination) => moonIllumination == null ? "grey" : moonIllumination < 0.25 ? "dark" : moonIllumination < 0.75 ? "grey" : "bright";

// S/N 20 exposure in r at both telescopes for one transient, from the CTAS follow-up rules and the
// instrument profiles (with the target's best airmass tonight and the Moon's sky preset).
export function exposures(target, { airmass, moonIllumination }) {
  if (!Followup || !target.followup) return [];
  const sky = skyPreset(moonIllumination);
  const windows = ccd.TELESCOPES.map((t) => ({ site: { telescope: t.id }, best_airmass: Number.isFinite(airmass) ? Math.max(1, airmass) : 1.5, sky_preset: sky }));
  const plan = Followup.exposurePlan(target.followup.block, ccd, { windows }) || [];
  return plan.map((row) => {
    const r = row.imaging.find((x) => x.filter === "r");
    return { id: row.id, name: row.name, placeholder: row.placeholder, r: r ? r.text : "—", feasible: r ? r.feasible : null,
      spectroscopy: row.spectroscopy ? row.spectroscopy.state : null };
  });
}

// For a planet's host star (transit photometry): in the band of the catalog magnitude, the longest
// exposure before the brightest pixel reaches 80 % of full well or of the converter's range (in
// focus; defocusing is not modelled), capped at 300 s, and the S/N and precision of one such frame.
// A V magnitude uses Johnson V; a TESS magnitude stands in for Cousins I (the TESS band, 600–1000 nm,
// is centred close to I; an approximation, said so on the sheet). Any other band: not computed.
export const HOST_EXPOSURE_CAP_S = 300;
export const HOST_BANDS = {
  V: { filter: "V", note: null },
  TESS: { filter: "I", note: "the TESS magnitude stands in for Cousins I (similar bands; an approximation)" },
};
export function hostExposures({ hostMag, hostMagBand }, { airmass, moonIllumination }) {
  if (!Number.isFinite(hostMag)) return { reason: "no host magnitude in the WorldsIndex ephemeris", rows: [] };
  const band = HOST_BANDS[hostMagBand];
  if (!band) return { reason: `the host magnitude is in the ${hostMagBand || "unknown"} band, which the instrument profiles do not cover`, rows: [] };
  const filter = ccd.FILTERS.find((f) => f.id === band.filter), sky = ccd.SKY[band.filter][skyPreset(moonIllumination)];
  const x = Number.isFinite(airmass) ? Math.max(1, airmass) : 1.5;
  const rows = ccd.TELESCOPES.filter((t) => Number.isFinite(t.throughput?.[band.filter])).map((t) => {
    const p = { mag: hostMag, airmass: x, sky, diameter: t.diameter, obstruction: t.obstruction, throughput: t.throughput[band.filter], extinction: t.extinction[band.filter],
      plateScale: t.plateScale, seeing: t.seeing, apertureFactor: 1, gain: t.gain, readNoise: t.readNoise, dark: t.dark, fullWell: t.fullWell, zeroPoint: filter.zeroPoint };
    const r = ccd.rates(p);
    const perSecond = ccd.peakPixel(1, r, p).electrons;
    const limit = 0.8 * Math.min(p.fullWell, ccd.ADC_MAX * p.gain);
    const tSat = perSecond > 0 ? limit / perSecond : Infinity;
    const t1 = Math.min(HOST_EXPOSURE_CAP_S, tSat);
    const snr = t1 > 0 ? ccd.snrFor(r.source, t1, r) : null;
    return { id: t.id, placeholder: ccd.isPlaceholder(t, `throughput.${band.filter}`) || ccd.isPlaceholder(t, `extinction.${band.filter}`) || t.placeholder,
      seconds: t1, capped: tSat > HOST_EXPOSURE_CAP_S, saturatesBelowOneSecond: tSat < 1, snr, pptPerFrame: snr ? 1000 / snr : null };
  });
  return { reason: null, filter: band.filter, note: band.note, rows };
}

// Every CTAS row with a position (not only the follow-up targets), keyed by event id, so a
// listed dossier is found whatever its role.
export function ctasIndex(summary) {
  const cols = summary.candidate_columns || [], at = Object.fromEntries(cols.map((c, i) => [c, i]));
  const followTargets = new Map(ctasTargets(summary).map((t) => [t.id, t]));
  const column = (r, name) => (name in at ? r[at[name]] : undefined);
  const out = new Map();
  for (const r of summary.candidate_rows || []) {
    const id = r[at.event_id];
    if (followTargets.has(id)) { out.set(id, followTargets.get(id)); continue; }
    if (!Number.isFinite(r[at.ra_deg]) || !Number.isFinite(r[at.dec_deg])) continue;
    out.set(id, { kind: "ctas", id, name: r[at.name], ra: r[at.ra_deg], dec: r[at.dec_deg],
      detail: [r[at.classification], r[at.discovery_survey]].filter(Boolean).join(" · "), mag: r[at.discovery_magnitude], score: r[at.ctas_score],
      role: r[at.record_role], href: `../ctas.html?event=${encodeURIComponent(id)}#dossier`,
      followup: followupFor({ event_id: id, name: r[at.name], ra_deg: r[at.ra_deg], dec_deg: r[at.dec_deg], status: column(r, "status"), record_role: r[at.record_role],
        ranking_channel: column(r, "ranking_channel") ?? "optical", classification: r[at.classification], discovery_time: r[at.discovery_time],
        discovery_magnitude: r[at.discovery_magnitude], discovery_survey: r[at.discovery_survey], n_spectra: column(r, "n_spectra") ?? 0,
        n_observations: column(r, "n_observations") ?? 0, photometry_outcomes: column(r, "photometry_outcomes") ?? {} }) });
  }
  return out;
}

// The sheet's targets for a listed source, in list order. A CTAS item missing from the summary,
// or a planet without an ephemeris, keeps the name and position the list carries.
export function resolveItems(items, { ctas = new Map(), ephemerides = new Map() } = {}) {
  return items.map((item) => {
    if (item.kind === "ctas") {
      const found = ctas.get(item.id);
      if (found) return { ...found, note: item.note || null };
      if (!Number.isFinite(item.ra) || !Number.isFinite(item.dec)) return null;
      return { kind: "ctas", id: item.id, name: item.name || item.id, ra: item.ra, dec: item.dec, detail: "not in the current CTAS summary; position as listed",
        mag: null, score: null, followup: null, href: `../ctas.html?event=${encodeURIComponent(item.id)}#dossier`, note: item.note || null };
    }
    const eph = ephemerides.get(item.id) || null;
    const ra = eph ? eph.raDeg : item.ra, dec = eph ? eph.decDeg : item.dec;
    if (!Number.isFinite(ra) || !Number.isFinite(dec)) return null;
    return { kind: "planet", id: item.id, name: eph?.name || item.name || item.id, ra, dec, eph, host: eph?.host ?? null, hostMag: eph?.hostMag ?? null,
      hostMagBand: eph?.hostMagBand ?? null, storedWindow: item.transitWindow || null, note: item.note || null,
      href: `../worldsindex/?object=${encodeURIComponent(item.id)}&section=object` };
  }).filter(Boolean);
}

// The targets for a "top N" sheet or an old-style ?events= list: the listed events in their given
// order, or the top `top` follow-up targets that are up longest tonight (then by CTAS score).
export function chooseTargets(all, query, grid, latDeg) {
  if (query.events.length) {
    const byId = new Map(all.map((t) => [t.id, t]));
    return query.events.map((id) => byId.get(id)).filter(Boolean).map((t) => ({ ...t, vis: visibility(grid, t.ra, t.dec, latDeg, query.altLimit) }));
  }
  return all.map((t) => ({ ...t, vis: visibility(grid, t.ra, t.dec, latDeg, query.altLimit) }))
    .filter((t) => t.vis.hours > 0)
    .sort((a, b) => b.vis.hours - a.vis.hours || (b.score ?? 0) - (a.score ?? 0))
    .slice(0, query.top);
}

// A planet's transits during this night (from local noon to the next) at the site, and, when none
// falls tonight, the next one within 30 nights. Same rules as the WorldsIndex planner.
export function planetWindows(target, { site, noonUtcMs, altLimit }) {
  if (!target.eph) return { tonight: [], next: null };
  const options = { altLimit, twilight: -12, padMinutes: 30, padSigma: 1 };
  const tonight = transitWindows(target.eph, site, { ...options, startMs: noonUtcMs, nights: 1 });
  const next = tonight.length ? null : transitWindows(target.eph, site, { ...options, startMs: noonUtcMs + DAY, nights: 30 })[0] || null;
  return { tonight, next };
}

const timeFmt = (ms, tz) => ms == null ? "—" : new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" }).format(new Date(ms));
const dayFmt = (ms, tz) => new Intl.DateTimeFormat("en-GB", { timeZone: tz, weekday: "short", day: "numeric", month: "short" }).format(new Date(ms));
const noteHtml = (t) => t.note ? `<p class="sheet-note"><strong>Note:</strong> ${esc(t.note)}</p>` : "";
const upTonight = (t, site, altLimit) => `<div><dt>Up tonight</dt><dd>${t.vis.hours.toFixed(1)} h above ${altLimit}°; highest ${t.vis.maxAlt == null ? "—" : Math.round(t.vis.maxAlt) + "°"} at ${timeFmt(t.vis.bestMs, site.tz)} (X ${t.vis.bestAirmass ? t.vis.bestAirmass.toFixed(2) : "—"}); Moon ${t.vis.moonSep == null ? "—" : Math.round(t.vis.moonSep) + "°"} away</dd></div>`;
function finderHtml(t) {
  const survey = finderSurvey(t.dec);
  const img = hips2fitsUrl({ hips: survey.hips, ra: t.ra, dec: t.dec, fovArcmin: FINDER_ARCMIN, size: 600 });
  return `<figure class="sheet-finder"><div class="sheet-finder__frame"><img src="${esc(img)}" alt="Finder chart of ${esc(t.name)}, ${FINDER_ARCMIN} arcminutes, north up, east left (${esc(survey.label)})" width="600" height="600" loading="lazy">${overlaySvg({ ra: t.ra, dec: t.dec, fovArcmin: FINDER_ARCMIN, size: 600 })}</div>` +
    `<figcaption>${FINDER_ARCMIN}′ × ${FINDER_ARCMIN}′, north up, east left; ${esc(survey.label)} via CDS hips2fits.</figcaption></figure>`;
}
const finderLink = (t) => `<a href="../tools/finder/?ra=${t.ra.toFixed(6)}&amp;dec=${t.dec.toFixed(6)}&amp;name=${encodeURIComponent(t.name)}">full finder chart</a>`;

export function targetHtml(t, i, { site, grid, altLimit }) {
  const exp = exposures(t, { airmass: t.vis.bestAirmass, moonIllumination: grid.moonIllumination });
  return `<article class="sheet-target" data-sheet-target="${esc(t.id)}" data-sheet-kind="ctas">` +
    `<h2>${i + 1}. ${esc(t.name)} <small>${esc(t.detail || "")}</small></h2>` + noteHtml(t) +
    `<div class="sheet-grid"><div><dl class="sheet-facts">` +
    `<div><dt>RA, Dec (J2000)</dt><dd>${esc(formatRA(t.ra, 2))} ${esc(formatDec(t.dec, 1))}<br><small>${t.ra.toFixed(5)}°, ${t.dec.toFixed(5)}°</small></dd></div>` +
    `<div><dt>Latest mag</dt><dd>${t.mag == null ? "—" : Number(t.mag).toFixed(2)}</dd></div>` +
    `<div><dt>Objective</dt><dd>${esc(t.followup ? t.followup.text : "—")}${t.followup && t.followup.cadence !== "none" ? ` <small>${esc(t.followup.cadence)}</small>` : ""}</dd></div>` +
    upTonight(t, site, altLimit) + `</dl>` +
    (exp.length ? `<div class="table-wrap" role="region" tabindex="0" aria-label="Exposure estimates for ${esc(t.name)}"><table class="sheet-exp"><caption>S/N 20 in r at the best airmass (instrument profiles; placeholders marked)</caption><thead><tr><th scope="col">Telescope</th><th scope="col">Time</th><th scope="col">≤ 3 × 600 s</th><th scope="col">Spectrum</th></tr></thead><tbody>` +
      exp.map((e) => `<tr><th scope="row">${esc(e.id.toUpperCase())}${e.placeholder ? " <small>(placeholder values)</small>" : ""}</th><td>${esc(e.r)}</td><td>${e.feasible == null ? "—" : e.feasible ? "yes" : "no"}</td><td>${esc(SPECTRUM_LABELS[e.spectroscopy] || "—")}</td></tr>`).join("") +
      `</tbody></table></div>` : "") +
    `<p class="sheet-links"><a href="${esc(t.href)}">CTAS dossier</a> · ${finderLink(t)}</p></div>` +
    finderHtml(t) + `</div>` +
    `<div class="chart">${altitudeChartSvg(grid, { ...t, kind: "ctas" }, site.lat, altLimit, site.tz)}</div></article>`;
}

export function planetHtml(t, i, { site, grid, altLimit, noonUtcMs }) {
  const { tonight, next } = planetWindows(t, { site, noonUtcMs, altLimit });
  const tz = site.tz;
  const sigma = (m) => m == null ? "σ not reported" : `±${Math.max(1, Math.round(m))} min (1σ)`;
  const host = hostExposures(t, { airmass: t.vis.bestAirmass, moonIllumination: grid.moonIllumination });
  const facts = [t.host ? `host ${t.host}` : "", t.eph?.periodDays ? `P = ${Number(t.eph.periodDays).toPrecision(8)} d` : "", t.eph?.durationHours ? `${Number(t.eph.durationHours).toFixed(2)} h long` : "",
    t.eph?.depthPpt != null ? `${Number(t.eph.depthPpt).toPrecision(t.eph.depthPpt < 1 ? 2 : 3)} ppt deep` : ""].filter(Boolean).join(" · ");
  let transits;
  if (!t.eph) transits = `<p>No transit ephemeris in WorldsIndex for this planet, so no window is computed${t.storedWindow ? `; the window kept with your run list was ${esc(dayFmt(t.storedWindow.midMs, tz))} ${timeFmt(t.storedWindow.midMs, tz)} mid-transit` : ""}.</p>`;
  else if (tonight.length) transits = `<div class="table-wrap" role="region" tabindex="0" aria-label="Transit windows of ${esc(t.name)} tonight"><table class="sheet-transits"><caption>Tonight’s transit window${tonight.length > 1 ? "s" : ""} at ${esc(site.name)} (${esc(tz)}): 30 min baseline plus 1σ each side</caption><thead><tr><th scope="col">Window start</th><th scope="col">Ingress</th><th scope="col">Mid</th><th scope="col">Egress</th><th scope="col">Window end</th><th scope="col">Seen</th></tr></thead><tbody>` +
    tonight.map((w) => `<tr><td>${timeFmt(w.windowStartMs, tz)}</td><td>${timeFmt(w.ingressMs, tz)}</td><td>${timeFmt(w.midMs, tz)} <small>${esc(sigma(w.sigmaMinutes))}</small></td><td>${timeFmt(w.egressMs, tz)}</td><td>${timeFmt(w.windowEndMs, tz)}</td><td>${esc(w.state)}${w.state === "partial" ? ` (${Math.round(w.fraction * 100)}%)` : ""}</td></tr>`).join("") +
    `</tbody></table></div>`;
  else transits = `<p><strong>No transit tonight</strong> at ${esc(site.name)} with the star above ${altLimit}° in the dark${next ? `; the next is ${esc(dayFmt(next.midMs, tz))}, mid-transit ${timeFmt(next.midMs, tz)} ${esc(sigma(next.sigmaMinutes))} (${esc(next.state)})` : " in the next 30 nights either"}.</p>`;
  const windows = tonight.flatMap((w) => [{ from: w.windowStartMs, to: w.windowEndMs, cls: "transit-sigma" }, { from: w.ingressMs, to: w.egressMs, cls: "transit" }]);
  const first = tonight[0];
  return `<article class="sheet-target" data-sheet-target="${esc(t.id)}" data-sheet-kind="planet">` +
    `<h2>${i + 1}. ${esc(t.name)} <small>${esc(facts || "WorldsIndex planet")}</small></h2>` + noteHtml(t) +
    `<div class="sheet-grid"><div><dl class="sheet-facts">` +
    `<div><dt>RA, Dec (J2000)</dt><dd>${esc(formatRA(t.ra, 2))} ${esc(formatDec(t.dec, 1))}<br><small>${t.ra.toFixed(5)}°, ${t.dec.toFixed(5)}°</small></dd></div>` +
    `<div><dt>Host mag</dt><dd>${Number.isFinite(t.hostMag) ? `${Number(t.hostMag).toFixed(2)} ${esc(t.hostMagBand || "")}` : "—"}</dd></div>` +
    upTonight(t, site, altLimit) + `</dl>` + transits +
    (host.rows.length ? `<div class="table-wrap" role="region" tabindex="0" aria-label="Host-star exposures for ${esc(t.name)}"><table class="sheet-exp"><caption>Host star in ${esc(host.filter)} at the best airmass${host.note ? ` (${esc(host.note)})` : ""}: longest in-focus frame before 80 % of full well (at most ${HOST_EXPOSURE_CAP_S} s) and its precision; defocusing not modelled</caption><thead><tr><th scope="col">Telescope</th><th scope="col">Frame</th><th scope="col">S/N</th><th scope="col">Per frame</th></tr></thead><tbody>` +
      host.rows.map((e) => `<tr><th scope="row">${esc(e.id.toUpperCase())}${e.placeholder ? " <small>(placeholder values)</small>" : ""}</th><td>${e.saturatesBelowOneSecond ? "saturates in < 1 s" : `${e.seconds >= 10 ? Math.round(e.seconds) : e.seconds.toFixed(1)} s${e.capped ? " (cap)" : ""}`}</td><td>${e.snr ? Math.round(e.snr).toLocaleString() : "—"}</td><td>${e.pptPerFrame ? `${e.pptPerFrame.toPrecision(2)} ppt` : "—"}</td></tr>`).join("") +
      `</tbody></table></div>` : `<p class="muted">Exposure: not computed (${esc(host.reason)}).</p>`) +
    `<p class="sheet-links"><a href="${esc(t.href)}">WorldsIndex page</a> · ${finderLink(t)}</p></div>` +
    finderHtml(t) + `</div>` +
    `<div class="chart">${altitudeChartSvg(grid, first ? { ...t, kind: "transit", ingressMs: first.ingressMs, egressMs: first.egressMs } : { ...t, kind: "planet" }, site.lat, altLimit, site.tz, { windows })}</div></article>`;
}

export const STANDARDS_NOTE = "Standard stars are not listed yet: the Landolt (1992, 2009) and Smith et al. (2002) standard-star catalogues are not part of this site's release, and nothing is filled in from memory. Pick a field near the target's airmass from the catalogues: ";

// ------------------------------------------------------------------ the run list panel
export function cartPanelHtml(items, { persistent = true } = {}) {
  const kindLabel = (k) => k === "planet" ? "planet" : "CTAS transient";
  if (!items.length) return `<p>Your run list is empty. Add targets with <strong>Add to run list</strong> on a <a href="../ctas.html">CTAS</a> dossier, a <a href="../worldsindex/">WorldsIndex</a> planet page or a row of the <a href="./">Tonight planner</a>. Until then this sheet shows the ten CTAS follow-up targets up longest tonight.</p>`;
  return `<ol class="cart-list">` + items.map((x, i) => {
    const key = `${x.kind}:${x.id}`;
    return `<li data-cart-item="${esc(key)}"><span class="cart-name">${i + 1}. ${esc(x.name)} <small>${kindLabel(x.kind)}</small></span>` +
      `<input type="text" maxlength="200" value="${esc(x.note || "")}" placeholder="Note (optional)" aria-label="Note for ${esc(x.name)}" data-cart-note>` +
      `<button type="button" class="cart-small" data-cart-move="-1" aria-label="Move ${esc(x.name)} up"${i === 0 ? " disabled" : ""}>↑</button>` +
      `<button type="button" class="cart-small" data-cart-move="1" aria-label="Move ${esc(x.name)} down"${i === items.length - 1 ? " disabled" : ""}>↓</button>` +
      `<button type="button" class="cart-small" data-cart-remove aria-label="Remove ${esc(x.name)} from the run list">Remove</button></li>`;
  }).join("") + `</ol>` + (persistent ? "" : `<p class="muted">This browser is not keeping the list after you leave (a private window or blocked storage); use <strong>Share this list</strong> to keep it.</p>`);
}

// ------------------------------------------------------------------ page
const page = { building: 0, source: null, noteEditing: false, ctasIndex: null, ephemerides: null };

async function loadCtasIndex() {
  if (page.ctasIndex) return page.ctasIndex;
  const r = await fetch("../ctas/data/live-summary.json", { cache: "no-cache" });
  if (!r.ok) throw Error(`CTAS summary unavailable (${r.status})`);
  const summary = await r.json();
  page.ctasIndex = { summary, index: ctasIndex(summary) };
  return page.ctasIndex;
}
async function loadEphemerides() {
  if (page.ephemerides) return page.ephemerides;
  const r = await fetch("../worldsindex/data/transit-ephemerides.json.gz", { cache: "no-cache" });
  if (!r.ok) { page.ephemerides = new Map(); return page.ephemerides; }
  const text = r.headers.get("content-encoding") === "gzip" ? await r.text() : await new Response(r.body.pipeThrough(new DecompressionStream("gzip"))).text();
  const artifact = JSON.parse(text);
  page.ephemerides = new Map((artifact.rows || []).map((row) => Object.fromEntries(artifact.columns.map((c, i) => [c, row[i]]))).map((e) => [e.objectId, e]));
  return page.ephemerides;
}

function currentSite(query) {
  const all = [...SITES, ...savedSites()];
  return all.find((s) => s.id === ($("#site").value || query.site)) || SITES[0];
}

async function run() {
  const build = ++page.building;
  const all = [...SITES, ...savedSites()];
  const query = parseQuery(new URL(location.href).searchParams, all);
  const site = currentSite(query);
  const date = $("#date").value;
  const status = $("#status");
  const source = sheetSource(query, Cart() ? Cart().list() : []);
  page.source = source;
  renderPanel(source, query);
  status.textContent = "Computing…";
  try {
    const noonUtcMs = localNoonUtc(date, site.tz);
    const grid = nightGrid({ noonUtcMs, latDeg: site.lat, lonDeg: site.lon, twilightDeg: -12 });
    const night = darkWindow(grid);
    let targets, intro;
    if (source.mode === "top") {
      const { summary } = await loadCtasIndex();
      targets = chooseTargets(ctasTargets(summary), query, grid, site.lat);
      intro = ` (the ${query.top} CTAS follow-up targets up longest tonight; your run list is empty)`;
    } else {
      const needPlanets = source.items.some((x) => x.kind === "planet");
      const [{ index }, ephemerides] = await Promise.all([loadCtasIndex(), needPlanets ? loadEphemerides() : Promise.resolve(new Map())]);
      targets = resolveItems(source.items, { ctas: index, ephemerides }).map((t) => ({ ...t, vis: visibility(grid, t.ra, t.dec, site.lat, query.altLimit) }));
      intro = source.mode === "cart" ? " from your run list" : " from the link";
    }
    if (build !== page.building) return;
    const transients = targets.filter((t) => t.kind !== "planet").length, planets = targets.length - transients;
    $("#sheet-head").innerHTML = `<p><strong>${esc(site.name)}</strong> · night of ${esc(date)} · dark (Sun below −12°) ${night ? `${timeFmt(night.from, site.tz)}–${timeFmt(night.to, site.tz)}` : "never"} ${esc(site.tz)} · Moon ${Math.round(grid.moonIllumination * 100)}% lit · ${targets.length} target${targets.length === 1 ? "" : "s"}` +
      `${planets && transients ? ` (${transients} transient${transients === 1 ? "" : "s"}, ${planets} planet${planets === 1 ? "" : "s"})` : ""}${intro}.</p>`;
    $("#sheet").innerHTML = targets.length
      ? targets.map((t, i) => t.kind === "planet" ? planetHtml(t, i, { site, grid, altLimit: query.altLimit, noonUtcMs }) : targetHtml(t, i, { site, grid, altLimit: query.altLimit })).join("")
      : (source.mode === "top" ? "<p>No CTAS follow-up target is up tonight at this site.</p>" : "<p>None of the listed targets has a position this sheet can use.</p>");
    status.textContent = "";
  } catch (error) {
    if (build === page.building) status.textContent = `Could not build the run sheet: ${error.message}`;
  }
}

function sheetBase() { const url = new URL(location.href); url.search = ""; url.hash = ""; return url.href; }

function renderPanel(source, query) {
  const banner = $("#cart-banner"), body = $("#cart-body"), tools = $("#cart-tools");
  if (!body) return;
  const cart = Cart();
  const items = cart ? cart.list() : [];
  if (source.mode === "link") {
    banner.hidden = false;
    banner.innerHTML = `<p>This sheet shows the ${source.items.length} target${source.items.length === 1 ? "" : "s"} carried by the link you opened, not your own run list.</p>` +
      `<p><button type="button" class="button" data-cart-load>Add ${source.items.length === 1 ? "it" : "them"} to my run list</button> <a href="${esc(sheetBase())}${query.site ? `?site=${esc(query.site)}` : ""}">Show my own run list (${items.length})</a> <span data-cart-load-status role="status" aria-live="polite"></span></p>`;
  } else { banner.hidden = true; banner.innerHTML = ""; }
  if (page.noteEditing) return; // keep the field being typed in
  // Re-rendering replaces the list: put the keyboard back on the same control of the same item.
  const active = document.activeElement, activeLi = active && body.contains(active) ? active.closest("[data-cart-item]") : null;
  const control = activeLi ? ["data-cart-note", "data-cart-remove", "data-cart-move"].find((a) => active.hasAttribute(a)) : null;
  const restore = activeLi && control ? `[data-cart-item="${CSS.escape(activeLi.getAttribute("data-cart-item"))}"] [${control}${control === "data-cart-move" ? `="${active.getAttribute(control)}"` : ""}]` : null;
  body.innerHTML = cartPanelHtml(items, { persistent: cart ? cart.persistent() : true });
  if (restore) { const el = body.querySelector(restore); if (el && !el.disabled) el.focus(); }
  const site = $("#site")?.value, date = $("#date")?.value;
  tools.hidden = !items.length;
  if (items.length && cart) {
    const share = cart.shareUrl(sheetBase(), items, { site, date });
    $("#cart-share").href = share;
    $("#cart-share-text").value = share;
  }
}

function itemOf(el) {
  const li = el.closest("[data-cart-item]"); if (!li) return null;
  const key = li.getAttribute("data-cart-item"), at = key.indexOf(":");
  return { kind: key.slice(0, at), id: key.slice(at + 1) };
}

export function init() {
  const all = [...SITES, ...savedSites()];
  const query = parseQuery(new URL(location.href).searchParams, all);
  $("#site").innerHTML = all.map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`).join("");
  if (query.site) $("#site").value = query.site;
  const today = new Date();
  $("#date").value = query.date || `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  $("#sheet-form").addEventListener("submit", (event) => { event.preventDefault(); run(); });
  $("#print").addEventListener("click", () => window.print());
  const cart = Cart();
  const panel = $("#cart");
  if (panel && cart) {
    panel.addEventListener("click", (event) => {
      const move = event.target.closest("[data-cart-move]"), remove = event.target.closest("[data-cart-remove]");
      if (move) {
        const it = itemOf(move), delta = Number(move.getAttribute("data-cart-move"));
        if (it && cart.move(it.kind, it.id, delta)) {
          // keep the keyboard on the item that moved
          const sel = `[data-cart-item="${CSS.escape(`${it.kind}:${it.id}`)}"] [data-cart-move="${delta}"]`;
          requestAnimationFrame(() => { const b = document.querySelector(sel); (b && !b.disabled ? b : document.querySelector(`[data-cart-item="${CSS.escape(`${it.kind}:${it.id}`)}"] [data-cart-remove]`))?.focus(); });
        }
        return;
      }
      if (remove) {
        const it = itemOf(remove), li = remove.closest("li"), next = li?.nextElementSibling || li?.previousElementSibling;
        const nextKey = next?.getAttribute("data-cart-item");
        if (it) cart.remove(it.kind, it.id);
        requestAnimationFrame(() => (nextKey ? document.querySelector(`[data-cart-item="${CSS.escape(nextKey)}"] [data-cart-remove]`) : $("#cart-title"))?.focus());
        return;
      }
      if (event.target.closest("#cart-clear")) {
        if (cart.count() && window.confirm(`Remove all ${cart.count()} targets from your run list?`)) cart.clear();
        return;
      }
      if (event.target.closest("#cart-copy")) {
        const text = $("#cart-share-text").value;
        const done = () => { $("#cart-status").textContent = "Link copied."; };
        if (navigator.clipboard?.writeText) navigator.clipboard.writeText(text).then(done, () => { $("#cart-share-text").select(); $("#cart-status").textContent = "Select and copy the link above."; });
        else { $("#cart-share-text").select(); $("#cart-status").textContent = "Select and copy the link above."; }
      }
    });
    panel.addEventListener("focusin", (event) => { if (event.target.matches("[data-cart-note]")) page.noteEditing = true; });
    panel.addEventListener("focusout", (event) => { if (event.target.matches("[data-cart-note]")) { page.noteEditing = false; } });
    panel.addEventListener("change", (event) => {
      if (!event.target.matches("[data-cart-note]")) return;
      const it = itemOf(event.target); if (it) cart.setNote(it.kind, it.id, event.target.value);
    });
    $("#cart-banner")?.addEventListener("click", (event) => {
      if (!event.target.closest("[data-cart-load]")) return;
      const out = cart.addMany(page.source?.items || []);
      const msg = `${out.added} added to your run list${out.full ? `; ${out.full} did not fit (it holds ${cart.MAX_ITEMS})` : ""}.`;
      const host = document.querySelector("[data-cart-load-status]"); if (host) host.textContent = msg;
    });
    let pending = null;
    cart.onChange(() => {
      clearTimeout(pending);
      pending = setTimeout(() => { if (page.source?.mode === "link") renderPanel(page.source, query); else run(); }, 50);
    });
  } else if (panel) {
    panel.hidden = true;
  }
  $("#site").addEventListener("change", () => { if (page.source) renderPanel(page.source, query); });
  $("#date").addEventListener("change", () => { if (page.source) renderPanel(page.source, query); });
  run();
}

if (typeof document !== "undefined" && document.getElementById("sheet")) init();

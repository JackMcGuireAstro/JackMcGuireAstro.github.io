// The printable run sheet (Framework v2 Track 4): for a list of CTAS transients and one site and
// night, each target's coordinates, tonight's window and airmass curve, a finder chart, the S/N-20
// exposure at WIRO and Red Buttes, and the CTAS objective. Computed in the browser from the
// published CTAS summary; nothing is sent anywhere or stored (the finder images are plain <img>
// requests to CDS hips2fits).
import { nightGrid, visibility, localNoonUtc } from "./astro.js?v=20261004";
import { SITES, ctasTargets, altitudeChartSvg, darkWindow } from "./planner.js?v=20261029";
import { savedSites } from "./sites.js?v=20261029";
import { SURVEYS, hips2fitsUrl, overlaySvg } from "../tools/finder/finder.js?v=20261003";
import { formatRA, formatDec } from "../tools/lib/astrometry.js?v=20261003";
import * as ccd from "../tools/exposure/ccd.js?v=20261029";

export const MAX_TARGETS = 20;
export const DEFAULT_TOP = 10;
export const FINDER_ARCMIN = 8;
const SPECTRUM_LABELS = { feasible: "feasible", marginal: "marginal", "too-faint": "too faint", "not-available": "no spectrograph", unknown: "undetermined" };
const Followup = globalThis.CTASFollowup;
const $ = (selector) => document.querySelector(selector);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// ?events=<uuid>,<uuid>&site=wiro&date=YYYY-MM-DD&top=10&alt=30. Unknown values are dropped.
export function parseQuery(params, sites = SITES) {
  const out = { events: [], top: null };
  const events = (params.get("events") || "").split(",").map((s) => s.trim()).filter((s) => UUID.test(s));
  out.events = [...new Set(events)].slice(0, MAX_TARGETS);
  const top = Number(params.get("top"));
  if (!out.events.length) out.top = Number.isInteger(top) && top > 0 ? Math.min(top, MAX_TARGETS) : DEFAULT_TOP;
  if (sites.some((s) => s.id === params.get("site"))) out.site = params.get("site");
  if (/^\d{4}-\d{2}-\d{2}$/.test(params.get("date") || "")) out.date = params.get("date");
  const alt = Number(params.get("alt"));
  out.altLimit = [20, 30, 40, 50].includes(alt) ? alt : 30;
  return out;
}

// The finder survey for a declination: Pan-STARRS colour where it exists (Dec > −30°), else DSS2 red.
export function finderSurvey(dec) {
  return dec > -29.5 ? SURVEYS.find((s) => s.id === "ps1-color") : SURVEYS.find((s) => s.id === "dss2-red");
}

// S/N 20 exposure in r at both telescopes for one target, from the CTAS follow-up rules and the
// instrument profiles (with the target's best airmass tonight and the Moon's sky preset).
export function exposures(target, { airmass, moonIllumination }) {
  if (!Followup || !target.followup) return [];
  const sky = moonIllumination == null ? "grey" : moonIllumination < 0.25 ? "dark" : moonIllumination < 0.75 ? "grey" : "bright";
  const windows = ccd.TELESCOPES.map((t) => ({ site: { telescope: t.id }, best_airmass: Number.isFinite(airmass) ? Math.max(1, airmass) : 1.5, sky_preset: sky }));
  const plan = Followup.exposurePlan(target.followup.block, ccd, { windows }) || [];
  return plan.map((row) => {
    const r = row.imaging.find((x) => x.filter === "r");
    return { id: row.id, name: row.name, placeholder: row.placeholder, r: r ? r.text : "—", feasible: r ? r.feasible : null,
      spectroscopy: row.spectroscopy ? row.spectroscopy.state : null };
  });
}

// The targets for the sheet: the listed events in their given order, or the top `top` follow-up
// targets that are up longest tonight (then by CTAS score).
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

const timeFmt = (ms, tz) => ms == null ? "—" : new Intl.DateTimeFormat("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" }).format(new Date(ms));

export function targetHtml(t, i, { site, grid, altLimit }) {
  const survey = finderSurvey(t.dec);
  const exp = exposures(t, { airmass: t.vis.bestAirmass, moonIllumination: grid.moonIllumination });
  const img = hips2fitsUrl({ hips: survey.hips, ra: t.ra, dec: t.dec, fovArcmin: FINDER_ARCMIN, size: 600 });
  return `<article class="sheet-target" data-sheet-target="${esc(t.id)}">` +
    `<h2>${i + 1}. ${esc(t.name)} <small>${esc(t.detail || "")}</small></h2>` +
    `<div class="sheet-grid"><div><dl class="sheet-facts">` +
    `<div><dt>RA, Dec (J2000)</dt><dd>${esc(formatRA(t.ra, 2))} ${esc(formatDec(t.dec, 1))}<br><small>${t.ra.toFixed(5)}°, ${t.dec.toFixed(5)}°</small></dd></div>` +
    `<div><dt>Latest mag</dt><dd>${t.mag == null ? "—" : Number(t.mag).toFixed(2)}</dd></div>` +
    `<div><dt>Objective</dt><dd>${esc(t.followup ? t.followup.text : "—")}${t.followup && t.followup.cadence !== "none" ? ` <small>${esc(t.followup.cadence)}</small>` : ""}</dd></div>` +
    `<div><dt>Up tonight</dt><dd>${t.vis.hours.toFixed(1)} h above ${altLimit}°; highest ${t.vis.maxAlt == null ? "—" : Math.round(t.vis.maxAlt) + "°"} at ${timeFmt(t.vis.bestMs, site.tz)} (X ${t.vis.bestAirmass ? t.vis.bestAirmass.toFixed(2) : "—"}); Moon ${t.vis.moonSep == null ? "—" : Math.round(t.vis.moonSep) + "°"} away</dd></div>` +
    `</dl>` +
    (exp.length ? `<div class="table-wrap" role="region" tabindex="0" aria-label="Exposure estimates for ${esc(t.name)}"><table class="sheet-exp"><caption>S/N 20 in r at the best airmass (instrument profiles; placeholders marked)</caption><thead><tr><th scope="col">Telescope</th><th scope="col">Time</th><th scope="col">≤ 3 × 600 s</th><th scope="col">Spectrum</th></tr></thead><tbody>` +
      exp.map((e) => `<tr><th scope="row">${esc(e.id.toUpperCase())}${e.placeholder ? " <small>(placeholder values)</small>" : ""}</th><td>${esc(e.r)}</td><td>${e.feasible == null ? "—" : e.feasible ? "yes" : "no"}</td><td>${esc(SPECTRUM_LABELS[e.spectroscopy] || "—")}</td></tr>`).join("") +
      `</tbody></table></div>` : "") +
    `<p class="sheet-links"><a href="${esc(t.href)}">CTAS dossier</a> · <a href="../tools/finder/?ra=${t.ra.toFixed(6)}&amp;dec=${t.dec.toFixed(6)}&amp;name=${encodeURIComponent(t.name)}">full finder chart</a></p></div>` +
    `<figure class="sheet-finder"><div class="sheet-finder__frame"><img src="${esc(img)}" alt="Finder chart of ${esc(t.name)}, ${FINDER_ARCMIN} arcminutes, north up, east left (${esc(survey.label)})" width="600" height="600" loading="lazy">${overlaySvg({ ra: t.ra, dec: t.dec, fovArcmin: FINDER_ARCMIN, size: 600 })}</div>` +
    `<figcaption>${FINDER_ARCMIN}′ × ${FINDER_ARCMIN}′, north up, east left; ${esc(survey.label)} via CDS hips2fits.</figcaption></figure></div>` +
    `<div class="chart">${altitudeChartSvg(grid, { ...t, kind: "ctas" }, site.lat, altLimit, site.tz)}</div></article>`;
}

export const STANDARDS_NOTE = "Standard stars are not listed yet: the Landolt (1992, 2009) and Smith et al. (2002) standard-star catalogues are not part of this site's release, and nothing is filled in from memory. Pick a field near the target's airmass from the catalogues: ";

async function run() {
  const all = [...SITES, ...savedSites()];
  const query = parseQuery(new URL(location.href).searchParams, all);
  const site = all.find((s) => s.id === ($("#site").value || query.site)) || SITES[0];
  const date = $("#date").value;
  const status = $("#status");
  status.textContent = "Computing…";
  try {
    const grid = nightGrid({ noonUtcMs: localNoonUtc(date, site.tz), latDeg: site.lat, lonDeg: site.lon, twilightDeg: -12 });
    const r = await fetch("../ctas/data/live-summary.json", { cache: "no-cache" });
    if (!r.ok) throw Error(`CTAS summary unavailable (${r.status})`);
    const targets = chooseTargets(ctasTargets(await r.json()), query, grid, site.lat);
    const night = darkWindow(grid);
    $("#sheet-head").innerHTML = `<p><strong>${esc(site.name)}</strong> · night of ${esc(date)} · dark (Sun below −12°) ${night ? `${timeFmt(night.from, site.tz)}–${timeFmt(night.to, site.tz)}` : "never"} ${esc(site.tz)} · Moon ${Math.round(grid.moonIllumination * 100)}% lit · ${targets.length} target${targets.length === 1 ? "" : "s"}` +
      `${query.events.length ? "" : ` (the ${query.top} CTAS follow-up targets up longest tonight)`}.</p>`;
    $("#sheet").innerHTML = targets.length ? targets.map((t, i) => targetHtml(t, i, { site, grid, altLimit: query.altLimit })).join("") : "<p>No listed target is in the current CTAS follow-up list, or none is up tonight at this site.</p>";
    status.textContent = "";
  } catch (error) {
    status.textContent = `Could not build the run sheet: ${error.message}`;
  }
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
  run();
}

if (typeof document !== "undefined" && document.getElementById("sheet")) init();

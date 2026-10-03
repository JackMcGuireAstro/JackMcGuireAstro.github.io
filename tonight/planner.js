// Tonight's sky: which CTAS transients and WorldsIndex planetary systems are up tonight
// from a chosen observatory. Everything is computed in the visitor's browser from the
// site's own published catalogs; nothing is sent anywhere or stored.
import { nightGrid, visibility, localNoonUtc, altitudeDeg, airmass } from "./astro.js?v=20261003";

export const SITES = [
  { id: "wiro", name: "WIRO (Wyoming Infrared Observatory)", lat: 41.0979, lon: -105.9767, tz: "America/Denver" },
  { id: "rbo", name: "Red Buttes Observatory (UW)", lat: 41.1764, lon: -105.5739, tz: "America/Denver" },
  { id: "kpno", name: "Kitt Peak, Arizona", lat: 31.9583, lon: -111.5967, tz: "America/Phoenix" },
  { id: "apo", name: "Apache Point, New Mexico", lat: 32.7803, lon: -105.8203, tz: "America/Denver" },
  { id: "mko", name: "Maunakea, Hawaiʻi", lat: 19.8207, lon: -155.4681, tz: "Pacific/Honolulu" },
  { id: "lco", name: "Las Campanas, Chile", lat: -29.0146, lon: -70.6926, tz: "America/Santiago" },
  { id: "rubin", name: "Cerro Pachón (Rubin), Chile", lat: -30.2446, lon: -70.7494, tz: "America/Santiago" },
  { id: "lapalma", name: "Roque de los Muchachos, La Palma", lat: 28.7606, lon: -17.8816, tz: "Atlantic/Canary" },
  { id: "saao", name: "Sutherland (SAAO), South Africa", lat: -32.3794, lon: 20.8106, tz: "Africa/Johannesburg" },
  { id: "sso", name: "Siding Spring, Australia", lat: -31.2733, lon: 149.0617, tz: "Australia/Sydney" },
];

const $ = (selector) => document.querySelector(selector);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const state = { ctas: null, worlds: null, grid: null, rows: [], selected: null };

// ------------------------------------------------------------------ catalogs
export function ctasTargets(summary) {
  const cols = summary.candidate_columns || [], at = Object.fromEntries(cols.map((c, i) => [c, i]));
  return (summary.candidate_rows || []).map((r) => ({
    kind: "ctas", id: r[at.event_id], name: r[at.name], ra: r[at.ra_deg], dec: r[at.dec_deg],
    detail: [r[at.classification], r[at.discovery_survey]].filter(Boolean).join(" · "),
    mag: r[at.discovery_magnitude], discovered: r[at.discovery_time], score: r[at.ctas_score], role: r[at.record_role],
    href: `../ctas.html?event=${encodeURIComponent(r[at.event_id])}#dossier`,
  })).filter((t) => t.role === "follow-up-target-candidate" && Number.isFinite(t.ra) && Number.isFinite(t.dec));
}

export function worldsSystems(index, { includeCandidates = false } = {}) {
  const systems = new Map();
  for (const o of index.objects || []) {
    if (!Number.isFinite(o.raDeg) || !Number.isFinite(o.decDeg)) continue;
    if (!(o.normalizedStatus === "CONFIRMED" || (includeCandidates && o.normalizedStatus === "CANDIDATE"))) continue;
    const key = o.systemId || o.hostName || o.objectId;
    const system = systems.get(key) || { kind: "worlds", id: key, name: o.hostName || o.name, ra: o.raDeg, dec: o.decDeg,
      planets: [], href: `../worldsindex/?object=${encodeURIComponent(o.objectId)}&section=object` };
    system.planets.push(o.name);
    systems.set(key, system);
  }
  return [...systems.values()].map((s) => ({ ...s, detail: `${s.planets.length} ${s.planets.length === 1 ? "planet" : "planets"}: ${s.planets.slice(0, 3).join(", ")}${s.planets.length > 3 ? "…" : ""}` }));
}

async function loadCtas() {
  if (state.ctas) return state.ctas;
  const r = await fetch("../ctas/data/live-summary.json", { cache: "no-cache" });
  if (!r.ok) throw Error(`CTAS summary unavailable (${r.status})`);
  state.ctas = ctasTargets(await r.json());
  return state.ctas;
}
async function loadWorlds() {
  if (state.worlds) return state.worlds;
  const r = await fetch("../worldsindex/data/catalog-index.json.gz", { cache: "no-cache" });
  if (!r.ok) throw Error(`WorldsIndex catalog unavailable (${r.status})`);
  let text;
  if (r.headers.get("content-encoding") === "gzip") text = await r.text();
  else text = await new Response(r.body.pipeThrough(new DecompressionStream("gzip"))).text();
  state.worlds = JSON.parse(text);
  return state.worlds;
}

// ------------------------------------------------------------------ settings
function settings() {
  const siteId = $("#site").value;
  const site = siteId === "custom"
    ? { id: "custom", name: "Custom site", lat: Number($("#lat").value), lon: Number($("#lon").value), tz: $("#tz").value || "UTC" }
    : SITES.find((s) => s.id === siteId);
  return { site, date: $("#date").value, twilight: Number($("#twilight").value), altLimit: Number($("#altlimit").value),
           list: $("#list").value, magLimit: $("#maglimit").value === "" ? NaN : Number($("#maglimit").value), candidates: $("#withcandidates").checked };
}
const timeFmt = (ms, tz) => ms == null ? "—" : new Intl.DateTimeFormat(undefined, { timeZone: tz, hour: "2-digit", minute: "2-digit" }).format(new Date(ms));

// ------------------------------------------------------------------ compute + render
async function run() {
  const s = settings(), status = $("#status");
  if (!s.site || !Number.isFinite(s.site.lat) || !Number.isFinite(s.site.lon)) { status.textContent = "Enter a latitude and longitude."; return; }
  status.textContent = "Computing…";
  try {
    const noon = localNoonUtc(s.date, s.site.tz);
    state.grid = nightGrid({ noonUtcMs: noon, latDeg: s.site.lat, lonDeg: s.site.lon, twilightDeg: s.twilight });
    const g = state.grid;
    $("#night").innerHTML = g.usableCount
      ? `<dl class="facts"><div><dt>Sunset</dt><dd>${timeFmt(g.sunset, s.site.tz)}</dd></div><div><dt>Dark from</dt><dd>${timeFmt(g.darkStart, s.site.tz)}</dd></div>` +
        `<div><dt>Dark until</dt><dd>${timeFmt(g.darkEnd, s.site.tz)}</dd></div><div><dt>Sunrise</dt><dd>${timeFmt(g.sunrise, s.site.tz)}</dd></div>` +
        `<div><dt>Moon</dt><dd>${Math.round(g.moonIllumination * 100)}% lit</dd></div></dl><p class="muted">Times in ${esc(s.site.tz)}. "Dark" means the Sun is more than ${-s.twilight}° below the horizon.</p>`
      : `<p>The Sun never gets ${-s.twilight}° below the horizon at this site on this night.</p>`;
    let targets;
    if (s.list === "ctas") {
      targets = (await loadCtas()).filter((t) => !Number.isFinite(s.magLimit) || t.mag == null || t.mag <= s.magLimit);
    } else {
      targets = worldsSystems(await loadWorlds(), { includeCandidates: s.candidates });
    }
    state.rows = targets.map((t) => ({ ...t, vis: visibility(g, t.ra, t.dec, s.site.lat, s.altLimit) }))
      .filter((t) => t.vis.hours > 0)
      .sort((a, b) => b.vis.hours - a.vis.hours || (b.score ?? 0) - (a.score ?? 0) || b.vis.maxAlt - a.vis.maxAlt);
    renderTable(s, targets.length);
    status.textContent = "";
  } catch (error) {
    status.textContent = `Could not compute: ${error.message}`;
  }
}

function renderTable(s, considered) {
  const shown = state.rows.slice(0, 200);
  const ctas = s.list === "ctas";
  $("#results").innerHTML = `<p class="muted">${state.rows.length.toLocaleString()} of ${considered.toLocaleString()} ${ctas ? "CTAS transients" : "planetary systems"} are above ${s.altLimit}° while it is dark${state.rows.length > 200 ? "; showing the 200 up longest" : ""}. Select a row for its altitude curve.</p>` +
    `<div class="table-wrap" role="region" aria-label="Observable targets" tabindex="0"><table><caption class="visually-hidden">Targets observable tonight</caption><thead><tr>` +
    `<th scope="col">${ctas ? "Transient" : "Host star"}</th><th scope="col">${ctas ? "Class · survey" : "Planets"}</th>${ctas ? '<th scope="col">Disc. mag</th><th scope="col">CTAS score</th>' : ""}` +
    `<th scope="col">Hours up</th><th scope="col">Highest</th><th scope="col">Best time</th><th scope="col">Moon sep.</th><th scope="col"><span class="visually-hidden">Open</span></th></tr></thead><tbody>` +
    shown.map((t, i) => `<tr data-row="${i}" tabindex="0" aria-label="Show the altitude curve for ${esc(t.name)}"><th scope="row">${esc(t.name)}</th><td>${esc(t.detail)}</td>` +
      (ctas ? `<td>${t.mag == null ? "—" : Number(t.mag).toFixed(1)}</td><td>${t.score == null ? "—" : Number(t.score).toFixed(0)}</td>` : "") +
      `<td>${t.vis.hours.toFixed(1)}</td><td>${Math.round(t.vis.maxAlt)}° <small>(X=${t.vis.bestAirmass ? t.vis.bestAirmass.toFixed(2) : "—"})</small></td>` +
      `<td>${timeFmt(t.vis.bestMs, s.site.tz)}</td><td>${t.vis.moonSep == null ? "—" : Math.round(t.vis.moonSep) + "°"}</td>` +
      `<td><a href="${esc(t.href)}">${ctas ? "Dossier" : "WorldsIndex"}<span class="visually-hidden"> for ${esc(t.name)}</span></a></td></tr>`).join("") +
    `</tbody></table></div>`;
  $("#chart").innerHTML = "";
}

export function altitudeChartSvg(grid, target, latDeg, altLimit, tz) {
  const usable = grid.samples.filter((s) => s.sunAlt < 0);
  if (!usable.length) return "";
  const w = 720, h = 260, L = 44, R = 12, T = 12, B = 34;
  const t0 = usable[0].ms, t1 = usable[usable.length - 1].ms;
  const X = (ms) => L + ((ms - t0) / Math.max(1, t1 - t0)) * (w - L - R), Y = (alt) => h - B - (Math.max(0, alt) / 90) * (h - T - B);
  const shade = usable.filter((s) => s.sunAlt >= grid.twilightDeg).map((s) => `<rect x="${X(s.ms).toFixed(1)}" y="${T}" width="${(X(s.ms + grid.stepMinutes * 60000) - X(s.ms)).toFixed(1)}" height="${h - T - B}" class="twilight"/>`).join("");
  const line = (fn) => usable.map((s, i) => `${i ? "L" : "M"}${X(s.ms).toFixed(1)},${Y(fn(s)).toFixed(1)}`).join("");
  const ticks = [];
  for (let ms = Math.ceil(t0 / 3600000) * 3600000; ms <= t1; ms += 7200000) {
    ticks.push(`<line x1="${X(ms).toFixed(1)}" x2="${X(ms).toFixed(1)}" y1="${h - B}" y2="${h - B + 4}" class="axis"/><text x="${X(ms).toFixed(1)}" y="${h - 12}" text-anchor="middle">${timeFmt(ms, tz)}</text>`);
  }
  const yt = [0, 30, 60, 90].map((a) => `<text x="${L - 6}" y="${(Y(a) + 4).toFixed(1)}" text-anchor="end">${a}°</text><line x1="${L}" x2="${w - R}" y1="${Y(a).toFixed(1)}" y2="${Y(a).toFixed(1)}" class="grid"/>`).join("");
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="Altitude of ${esc(target.name)} through the night">${shade}${yt}` +
    `<line x1="${L}" x2="${w - R}" y1="${Y(altLimit).toFixed(1)}" y2="${Y(altLimit).toFixed(1)}" class="limit"/>` +
    `<path d="${line((s) => s.moonAlt)}" class="moon"/><path d="${line((s) => altitudeDeg(target.ra, target.dec, latDeg, s.lst))}" class="target"/>${ticks.join("")}</svg>` +
    `<p class="legend"><span class="key target"></span>${esc(target.name)} <span class="key moon"></span>Moon <span class="key limit"></span>${altLimit}° limit <span class="key twilight"></span>twilight</p>`;
}

function select(index) {
  const s = settings(), t = state.rows[index];
  if (!t) return;
  document.querySelectorAll("#results tr[data-row]").forEach((tr) => tr.classList.toggle("is-selected", Number(tr.dataset.row) === index));
  const minAirmass = t.vis.bestAirmass ? t.vis.bestAirmass.toFixed(2) : "—";
  $("#chart").innerHTML = `<h3>${esc(t.name)}</h3><p class="muted">RA ${t.ra.toFixed(4)}°, Dec ${t.dec.toFixed(4)}° · up ${t.vis.hours.toFixed(1)} h above ${s.altLimit}° · best airmass ${minAirmass}</p>` +
    altitudeChartSvg(state.grid, t, s.site.lat, s.altLimit, s.site.tz);
  $("#chart").scrollIntoView({ block: "nearest", behavior: "smooth" });
}

export function init() {
  const siteSelect = $("#site");
  siteSelect.innerHTML = SITES.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("") + '<option value="custom">Custom latitude/longitude…</option>';
  const today = new Date();
  $("#date").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
  $("#tz").value = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  const toggleCustom = () => { $("#custom").hidden = siteSelect.value !== "custom"; };
  siteSelect.addEventListener("change", toggleCustom);
  $("#list").addEventListener("change", () => { $("#ctas-only").hidden = $("#list").value !== "ctas"; $("#worlds-only").hidden = $("#list").value !== "worlds"; });
  $("#locate").addEventListener("click", () => {
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition((p) => {
      siteSelect.value = "custom"; toggleCustom();
      $("#lat").value = p.coords.latitude.toFixed(4); $("#lon").value = p.coords.longitude.toFixed(4); run();
    }, () => { $("#status").textContent = "Location not available; choose a site instead."; });
  });
  $("#form").addEventListener("submit", (event) => { event.preventDefault(); run(); });
  $("#results").addEventListener("click", (event) => {
    if (event.target.closest("a")) return;
    const row = event.target.closest("tr[data-row]"); if (row) select(Number(row.dataset.row));
  });
  $("#results").addEventListener("keydown", (event) => {
    const row = event.target.closest("tr[data-row]");
    if (row && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(Number(row.dataset.row)); }
  });
  toggleCustom();
  run();
}

if (typeof document !== "undefined" && document.getElementById("form")) init();

// Finder charts: a survey image from CDS hips2fits (loaded as a plain <img>, so no CORS is
// involved) with an SVG overlay drawn on top. A typed object name is looked up with CDS
// Sesame. Those two requests are the only ones this page makes; nothing is stored.
import { parseCoordinates, formatRA, formatDec, tangentOffset, offsetMove } from "../lib/astrometry.js?v=20261003";

export const SURVEYS = [
  { id: "dss2-red", label: "DSS2 red", hips: "CDS/P/DSS2/red", decMin: -90 },
  { id: "dss2-blue", label: "DSS2 blue", hips: "CDS/P/DSS2/blue", decMin: -90 },
  { id: "ps1-color", label: "Pan-STARRS DR1 colour", hips: "CDS/P/PanSTARRS/DR1/color-z-zg-g", decMin: -30 },
  { id: "ps1-g", label: "Pan-STARRS DR1 g", hips: "CDS/P/PanSTARRS/DR1/g", decMin: -30 },
  { id: "2mass-j", label: "2MASS J", hips: "CDS/P/2MASS/J", decMin: -90 },
];
export const IMAGE_SIZE = 1000;   // pixels requested, and the overlay's viewBox
export const FOV_MIN = 2, FOV_MAX = 60, FOV_DEFAULT = 10;
const HIPS2FITS = "https://alasky.cds.unistra.fr/hips-image-services/hips2fits";
const SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oI/A?";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ------------------------------------------------------------------ requests
export function hips2fitsUrl({ hips, ra, dec, fovArcmin, size = IMAGE_SIZE }) {
  return `${HIPS2FITS}?hips=${hips}&width=${size}&height=${size}&fov=${(fovArcmin / 60).toFixed(6)}` +
    `&projection=TAN&coordsys=icrs&ra=${ra.toFixed(6)}&dec=${dec.toFixed(6)}&format=jpg`;
}

export const sesameUrl = (name) => SESAME + encodeURIComponent(name.trim());

// Sesame's plain-text answer: the first "%J ra dec" line (J2000 degrees), and "%I.0" for the
// main identifier. Returns null when nothing was found.
export function parseSesame(text) {
  const j = /^%J\s+([+-]?\d+(?:\.\d+)?)\s+([+-]?\d+(?:\.\d+)?)/m.exec(text || "");
  if (!j) return null;
  const ra = Number(j[1]), dec = Number(j[2]);
  if (!(ra >= 0 && ra < 360 && Math.abs(dec) <= 90)) return null;
  const id = /^%I\.0\s+(.+)$/m.exec(text);
  return { ra, dec, id: id ? id[1].replace(/\s+/g, " ").trim() : "" };
}

// True when the text is made of coordinate characters only (so a parse error should be shown
// rather than a name lookup): digits, separators and signs, with h/m/s/d marks after numbers.
export function looksLikeCoordinates(text) {
  const rest = String(text).replace(/\d+(?:\.\d*)?\s*(?:deg|[hmsd°'"′″])/gi, " ");
  return /\d/.test(text) && /^[\d\s:.,+\-\u2212]*$/.test(rest);
}

export function readUrlParams(search) {
  const p = new URLSearchParams(search || "");
  const fov = Number(p.get("fov"));
  return {
    ra: p.get("ra") || "", dec: p.get("dec") || "", name: p.get("name") || "",
    fov: Number.isFinite(fov) && fov > 0 ? Math.min(FOV_MAX, Math.max(FOV_MIN, fov)) : null,
    survey: SURVEYS.some((s) => s.id === p.get("survey")) ? p.get("survey") : null,
  };
}

// ------------------------------------------------------------------ overlay
// A scale bar of a round length no longer than a quarter of the field.
export function scaleBar(fovArcmin) {
  const quarter = (fovArcmin * 60) / 4;
  const steps = [5, 10, 15, 20, 30, 60, 120, 180, 300, 600, 900, 1200];
  const arcsec = steps.filter((s) => s <= quarter).pop() || steps[0];
  return { arcsec, label: arcsec < 60 ? `${arcsec}″` : `${arcsec / 60}′` };
}

const fmtArcmin = (x) => `${Number(x.toFixed(1))}′`;

// SVG overlay in image pixels (north up, east left, as hips2fits draws a TAN image in ICRS).
// `offset`, when given, is {ra, dec, label} of an offset star to mark.
export function overlaySvg({ ra, dec, fovArcmin, offset = null, size = IMAGE_SIZE }) {
  const c = size / 2, pxPerArcsec = size / (fovArcmin * 60);
  const rin = size * 0.03, rout = size * 0.1;
  const parts = [
    `<line x1="${c}" y1="${c - rin}" x2="${c}" y2="${c - rout}"/>`, `<line x1="${c}" y1="${c + rin}" x2="${c}" y2="${c + rout}"/>`,
    `<line x1="${c - rin}" y1="${c}" x2="${c - rout}" y2="${c}"/>`, `<line x1="${c + rin}" y1="${c}" x2="${c + rout}" y2="${c}"/>`,
  ];
  // compass: N up, E left, in the top-right corner
  const ox = size * 0.9, oy = size * 0.15, arm = size * 0.09, head = size * 0.018;
  parts.push(`<line x1="${ox}" y1="${oy}" x2="${ox}" y2="${oy - arm}"/>`,
    `<path class="arrowhead" d="M${ox},${oy - arm - head} L${ox - head * 0.7},${oy - arm + head * 0.4} L${ox + head * 0.7},${oy - arm + head * 0.4} Z"/>`,
    `<text x="${ox}" y="${oy - arm - head - 8}" text-anchor="middle">N</text>`,
    `<line x1="${ox}" y1="${oy}" x2="${ox - arm}" y2="${oy}"/>`,
    `<path class="arrowhead" d="M${ox - arm - head},${oy} L${ox - arm + head * 0.4},${oy - head * 0.7} L${ox - arm + head * 0.4},${oy + head * 0.7} Z"/>`,
    `<text x="${ox - arm - head - 10}" y="${oy + 10}" text-anchor="end">E</text>`);
  // scale bar, bottom left
  const bar = scaleBar(fovArcmin), len = bar.arcsec * pxPerArcsec, bx = size * 0.05, by = size * 0.94;
  parts.push(`<line x1="${bx}" y1="${by}" x2="${bx + len}" y2="${by}"/>`, `<line x1="${bx}" y1="${by - 12}" x2="${bx}" y2="${by + 12}"/>`,
    `<line x1="${bx + len}" y1="${by - 12}" x2="${bx + len}" y2="${by + 12}"/>`,
    `<text x="${bx + len / 2}" y="${by - 20}" text-anchor="middle">${bar.label}</text>`);
  // field size, top left
  parts.push(`<text x="${size * 0.04}" y="${size * 0.07}">${fmtArcmin(fovArcmin)} × ${fmtArcmin(fovArcmin)}</text>`);
  if (offset) {
    const t = tangentOffset(ra, dec, offset.ra, offset.dec);
    const x = c - t.xi * 3600 * pxPerArcsec, y = c - t.eta * 3600 * pxPerArcsec, edge = size * 0.03;
    if (t.front && x > edge && x < size - edge && y > edge && y < size - edge) {
      const s = size * 0.025;
      parts.push(`<rect class="offset" x="${(x - s).toFixed(1)}" y="${(y - s).toFixed(1)}" width="${2 * s}" height="${2 * s}"/>`,
        `<line class="offset" x1="${x.toFixed(1)}" y1="${y.toFixed(1)}" x2="${c}" y2="${c}" stroke-dasharray="10 8"/>`,
        `<text class="offset" x="${(x + s + 6).toFixed(1)}" y="${(y - s - 6).toFixed(1)}">${esc(offset.label || "offset star")}</text>`);
    } else {
      const ang = Math.atan2(y - c, x - c), ex = c + Math.cos(ang) * size * 0.42, ey = c + Math.sin(ang) * size * 0.42;
      parts.push(`<line class="offset" x1="${(c + Math.cos(ang) * rout * 1.3).toFixed(1)}" y1="${(c + Math.sin(ang) * rout * 1.3).toFixed(1)}" x2="${ex.toFixed(1)}" y2="${ey.toFixed(1)}" stroke-dasharray="10 8"/>`,
        `<text class="offset" x="${ex.toFixed(1)}" y="${(ey + (Math.sin(ang) < 0 ? -10 : 34)).toFixed(1)}" text-anchor="middle">offset star (outside)</text>`);
    }
  }
  return `<svg class="overlay" viewBox="0 0 ${size} ${size}" aria-hidden="true" focusable="false">${parts.join("")}</svg>`;
}

// How to get from the offset star to the target, as words.
export function describeMove(m) {
  const ew = `${Math.abs(m.east).toFixed(1)}″ ${m.east >= 0 ? "east" : "west"}`, ns = `${Math.abs(m.north).toFixed(1)}″ ${m.north >= 0 ? "north" : "south"}`;
  const dra = `${m.dRaSeconds >= 0 ? "+" : "−"}${Math.abs(m.dRaSeconds).toFixed(2)} s of RA`;
  return { text: `${ew}, ${ns}`, detail: `ΔRA·cos δ = ${m.east >= 0 ? "+" : "−"}${Math.abs(m.east).toFixed(1)}″ (${dra}), ΔDec = ${m.north >= 0 ? "+" : "−"}${Math.abs(m.north).toFixed(1)}″; ${(m.separation / 60).toFixed(2)}′ apart` };
}

export function captionHtml({ name, ra, dec, fovArcmin, survey, date, move }) {
  const title = name ? `<strong>${esc(name)}</strong><br>` : "";
  const bar = scaleBar(fovArcmin);
  return `${title}RA ${formatRA(ra, 2)}, Dec ${formatDec(dec, 1)} (J2000; ${ra.toFixed(5)}°, ${dec >= 0 ? "+" : "−"}${Math.abs(dec).toFixed(5)}°)<br>` +
    `${fmtArcmin(fovArcmin)} field, north up, east left; scale bar ${bar.label}. ${esc(survey.label)} via CDS hips2fits. Made ${esc(date)} UTC.` +
    (move ? `<br>From the offset star, move ${esc(move.text)} to the target (${esc(move.detail)}).` : "");
}

// ------------------------------------------------------------------ page
const $ = (selector) => document.querySelector(selector);
const state = { target: null, offset: null };

function status(text, ok = false) { const el = $("#finder-status"); el.textContent = text; el.classList.toggle("ok", ok); }

async function lookUp(name) {
  const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 12000);
  try {
    const r = await fetch(sesameUrl(name), { signal: controller.signal });
    if (!r.ok) throw Error(`HTTP ${r.status}`);
    return { found: parseSesame(await r.text()) };
  } catch (error) {
    return { failed: true, error };
  } finally {
    clearTimeout(timer);
  }
}

function currentSurvey() { return SURVEYS.find((s) => s.id === $("#survey").value) || SURVEYS[0]; }
function currentFov() {
  const v = Number($("#fov").value);
  return Number.isFinite(v) && v >= FOV_MIN && v <= FOV_MAX ? v : null;
}

function drawOverlay() {
  const t = state.target;
  if (!t) return;
  const fov = t.fov, move = state.offset ? describeMove(offsetMove(state.offset.ra, state.offset.dec, t.ra, t.dec)) : null;
  $("#overlay-slot").innerHTML = overlaySvg({ ra: t.ra, dec: t.dec, fovArcmin: fov, offset: state.offset });
  $("#caption").innerHTML = captionHtml({ name: t.name, ra: t.ra, dec: t.dec, fovArcmin: fov, survey: t.survey, date: t.date, move });
}

function showChart(target) {
  const survey = currentSurvey(), fov = currentFov();
  state.target = { ...target, survey, fov, date: new Date().toISOString().slice(0, 10) };
  const img = $("#chart-img"), frame = $("#chart-frame");
  $("#chart-placeholder").hidden = true;
  img.hidden = false;
  img.alt = `${survey.label} survey image, ${fov} arcminutes across, centred on ${target.name || `RA ${formatRA(target.ra, 1)}, Dec ${formatDec(target.dec, 0)}`}. North is up and east is left.`;
  frame.setAttribute("aria-busy", "true");
  status(`Loading the ${survey.label} image from CDS…`);
  img.onload = () => {
    frame.removeAttribute("aria-busy");
    status(target.dec < survey.decMin ? `${survey.label} does not cover declinations south of ${survey.decMin}°, so this image is probably blank. Try DSS2 or 2MASS.` : "Chart ready.", target.dec >= survey.decMin);
  };
  img.onerror = () => {
    frame.removeAttribute("aria-busy");
    status("The survey image did not load. Check your connection, or try another survey. The overlay still shows the scale and orientation.");
  };
  img.src = hips2fitsUrl({ hips: survey.hips, ra: target.ra, dec: target.dec, fovArcmin: fov });
  drawOverlay();
  $("#print").disabled = false;
  const q = new URLSearchParams({ ra: target.ra.toFixed(6), dec: target.dec.toFixed(6), ...(target.name ? { name: target.name } : {}) });
  $("#coords-link").href = `../coordinates/?${q}`;
  $("#coords-link").hidden = false;
}

async function make() {
  const text = $("#target").value.trim(), label = $("#label").value.trim();
  $("#fov").toggleAttribute("aria-invalid", currentFov() === null);
  if (currentFov() === null) { status(`The field of view must be between ${FOV_MIN}′ and ${FOV_MAX}′.`); return; }
  $("#target").removeAttribute("aria-invalid");
  if (!text) { $("#target").setAttribute("aria-invalid", "true"); status("Type an object name or coordinates."); return; }
  const coords = parseCoordinates(text);
  if (!coords.error) { showChart({ ra: coords.ra, dec: coords.dec, name: label }); return; }
  if (looksLikeCoordinates(text)) { $("#target").setAttribute("aria-invalid", "true"); status(coords.error); return; }
  status(`Looking up “${text}” with CDS Sesame…`);
  const result = await lookUp(text);
  if (result.failed) {
    $("#target").setAttribute("aria-invalid", "true");
    status(`The name service could not be reached from this page, so “${text}” was not looked up. Type coordinates instead, for example 13 29 52.7 +47 11 43.`);
    return;
  }
  if (!result.found) {
    $("#target").setAttribute("aria-invalid", "true");
    status(`CDS Sesame did not find “${text}”. Check the spelling, or type coordinates instead.`);
    return;
  }
  showChart({ ra: result.found.ra, dec: result.found.dec, name: label || text });
}

function setOffset() {
  const text = $("#offset").value.trim();
  if (!text) { state.offset = null; $("#offset").removeAttribute("aria-invalid"); $("#offset-result").innerHTML = ""; drawOverlay(); return; }
  const c = parseCoordinates(text);
  if (c.error) { $("#offset").setAttribute("aria-invalid", "true"); $("#offset-result").innerHTML = `<p class="status">${esc(c.error)}</p>`; return; }
  $("#offset").removeAttribute("aria-invalid");
  if (!state.target) { $("#offset-result").innerHTML = `<p class="status">Make a chart for the target first.</p>`; return; }
  state.offset = { ra: c.ra, dec: c.dec, label: "offset star" };
  const m = offsetMove(c.ra, c.dec, state.target.ra, state.target.dec), d = describeMove(m);
  $("#offset-result").innerHTML = `<p>After centring the offset star, move the telescope <span class="move">${esc(d.text)}</span> to reach the target.</p><p class="muted">${esc(d.detail)}. Check the sign convention of your telescope’s offset command before you use these numbers.</p>`;
  drawOverlay();
}

export function init() {
  $("#survey").innerHTML = SURVEYS.map((s) => `<option value="${s.id}">${esc(s.label)}</option>`).join("");
  const p = readUrlParams(location.search);
  if (p.fov) $("#fov").value = p.fov;
  if (p.survey) $("#survey").value = p.survey;
  $("#finder-form").addEventListener("submit", (event) => { event.preventDefault(); make(); });
  $("#offset-form").addEventListener("submit", (event) => { event.preventDefault(); setOffset(); });
  $("#invert").addEventListener("change", () => $("#chart-figure").classList.toggle("is-inverted", $("#invert").checked));
  $("#print").addEventListener("click", () => window.print());
  for (const id of ["#fov", "#survey"]) $(id).addEventListener("change", () => { if (state.target && currentFov() !== null) showChart(state.target); });
  if (p.ra && p.dec) {
    $("#target").value = `${p.ra}, ${p.dec}`;
    $("#label").value = p.name;
    make();
  } else if (p.name) {
    $("#target").value = p.name;
    make();
  }
}

if (typeof document !== "undefined" && document.getElementById("finder-form")) init();

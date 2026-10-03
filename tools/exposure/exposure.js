// Exposure-time calculator page. The physics is in ccd.js; this file reads the form, checks
// it, and draws the result and the S/N curve. Nothing is stored or sent anywhere.
import { FILTERS, SKY, TELESCOPES, solve, snrFor, noiseBudget, peakPixel } from "./ccd.js?v=20261003";

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// key, label, test, message; `modes` limits a field to some calculations.
const FIELDS = [
  { key: "mag", label: "Source magnitude", ok: (v) => v > -5 && v < 35, msg: "between −5 and 35", modes: ["snr", "time"] },
  { key: "exptime", label: "Exposure time", ok: (v) => v > 0 && v <= 1e6, msg: "above 0 s", modes: ["snr", "maglim"] },
  { key: "snr", label: "Target S/N", ok: (v) => v > 0 && v <= 1e5, msg: "above 0", modes: ["time", "maglim"] },
  { key: "airmass", label: "Airmass", ok: (v) => v >= 1 && v <= 40, msg: "1 or more" },
  { key: "sky", label: "Sky brightness", ok: (v) => v >= 10 && v <= 30, msg: "between 10 and 30 mag/arcsec²" },
  { key: "diameter", label: "Mirror diameter", ok: (v) => v > 0 && v <= 50, msg: "above 0 m" },
  { key: "obstruction", label: "Central obstruction", ok: (v, p) => v >= 0 && !(v >= p.diameter), msg: "0 or more, and smaller than the mirror" },
  { key: "throughput", label: "Throughput", ok: (v) => v > 0 && v <= 1, msg: "between 0 and 1" },
  { key: "extinction", label: "Extinction", ok: (v) => v >= 0 && v <= 5, msg: "between 0 and 5 mag/airmass" },
  { key: "plateScale", label: "Plate scale", ok: (v) => v > 0 && v <= 60, msg: "above 0″/pixel" },
  { key: "seeing", label: "Seeing", ok: (v) => v > 0 && v <= 30, msg: "above 0″" },
  { key: "apertureFactor", label: "Aperture radius", ok: (v) => v > 0 && v <= 10, msg: "above 0 × FWHM" },
  { key: "gain", label: "Gain", ok: (v) => v > 0, msg: "above 0" },
  { key: "readNoise", label: "Read noise", ok: (v) => v >= 0, msg: "0 or more" },
  { key: "dark", label: "Dark current", ok: (v) => v >= 0, msg: "0 or more" },
  { key: "fullWell", label: "Full well", ok: (v) => v > 0, msg: "above 0" },
  { key: "zeroPoint", label: "Zero-point photon flux", ok: (v) => v > 0, msg: "above 0" },
];
const TELESCOPE_KEYS = ["diameter", "obstruction", "plateScale", "gain", "readNoise", "dark", "fullWell", "seeing"];

// Strings from the form → numbers, with a list of {field, message} for anything unusable.
export function readParams(raw, mode) {
  const params = {}, errors = [];
  for (const f of FIELDS) params[f.key] = raw[f.key] === "" || raw[f.key] == null ? NaN : Number(raw[f.key]);
  for (const f of FIELDS) {
    if (f.modes && !f.modes.includes(mode)) continue;
    const v = params[f.key];
    if (!Number.isFinite(v) || !f.ok(v, params)) errors.push({ field: f.key, message: `${f.label} must be ${f.msg}.` });
  }
  return { params, errors, raw };
}

// ------------------------------------------------------------------ formatting
const sig = (x, n = 3) => {
  if (!Number.isFinite(x)) return "—";
  if (x === 0) return "0";
  const a = Math.abs(x);
  if (a >= 1e6 || a < 1e-3) return x.toExponential(n - 1).replace("e+", " × 10^").replace("e-", " × 10^−");
  return Number(x.toPrecision(n)).toLocaleString("en-US", { maximumFractionDigits: 6 });
};
export function formatTime(t) {
  if (!Number.isFinite(t)) return "—";
  if (t < 1) return `${t.toPrecision(2)} s`;
  if (t < 120) return `${t.toPrecision(3)} s`;
  if (t < 7200) return `${Math.round(t)} s (${(t / 60).toFixed(1)} min)`;
  return `${Math.round(t).toLocaleString("en-US")} s (${(t / 3600).toFixed(2)} h)`;
}

function niceStep(span, target = 5) {
  const raw = span / target, p = 10 ** Math.floor(Math.log10(raw)), m = raw / p;
  return (m >= 5 ? 10 : m >= 2 ? 5 : m >= 1 ? 2 : 1) * p;
}

// S/N against exposure time from 0 to 2.5× the chosen time, with the chosen point marked
// and, when a S/N was asked for, the goal as a dashed line.
export function chartSvg(out) {
  if (!Number.isFinite(out.t) || !(out.t > 0) || !(out.source > 0)) return "";
  const w = 640, h = 300, L = 54, R = 16, T = 14, B = 46;
  const tMax = out.t * 2.5, n = 120;
  const pts = Array.from({ length: n + 1 }, (_, i) => { const t = (tMax * i) / n; return [t, snrFor(out.source, t, out.r)]; });
  const yTop = Math.max(...pts.map((p) => p[1]), out.snr) * 1.08;
  const yStep = niceStep(yTop), xStep = niceStep(tMax);
  const X = (t) => L + (t / tMax) * (w - L - R), Y = (s) => h - B - (s / yTop) * (h - T - B);
  const grid = [], labels = [];
  for (let s = 0; s <= yTop + 1e-9; s += yStep) {
    grid.push(`<line class="grid" x1="${L}" x2="${w - R}" y1="${Y(s).toFixed(1)}" y2="${Y(s).toFixed(1)}"/>`);
    labels.push(`<text x="${L - 6}" y="${(Y(s) + 4).toFixed(1)}" text-anchor="end">${sig(s, 3)}</text>`);
  }
  for (let t = 0; t <= tMax + 1e-9; t += xStep) {
    labels.push(`<line class="axis" x1="${X(t).toFixed(1)}" x2="${X(t).toFixed(1)}" y1="${h - B}" y2="${h - B + 4}"/><text x="${X(t).toFixed(1)}" y="${h - B + 18}" text-anchor="middle">${sig(t, 3)}</text>`);
  }
  const d = pts.map(([t, s], i) => `${i ? "L" : "M"}${X(t).toFixed(1)},${Y(s).toFixed(1)}`).join("");
  const goal = out.mode === "snr" ? "" : `<line class="goal" x1="${L}" x2="${w - R}" y1="${Y(out.snr).toFixed(1)}" y2="${Y(out.snr).toFixed(1)}"/>`;
  const label = `S/N against exposure time for this setup: S/N ${out.snr.toFixed(1)} at ${formatTime(out.t)}, rising to ${pts[n][1].toFixed(1)} at ${formatTime(tMax)}.`;
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}">${grid.join("")}` +
    `<line class="axis" x1="${L}" x2="${w - R}" y1="${h - B}" y2="${h - B}"/><line class="axis" x1="${L}" x2="${L}" y1="${T}" y2="${h - B}"/>` +
    `${labels.join("")}${goal}<path class="curve" d="${d}"/><circle class="marker" cx="${X(out.t).toFixed(1)}" cy="${Y(out.snr).toFixed(1)}" r="6"/>` +
    `<text x="${(L + w - R) / 2}" y="${h - 8}" text-anchor="middle">Exposure time (s)</text>` +
    `<text x="14" y="${(T + h - B) / 2}" text-anchor="middle" transform="rotate(-90 14 ${(T + h - B) / 2})">S/N</text></svg>`;
}

// ------------------------------------------------------------------ page
const $ = (selector) => document.querySelector(selector);
const mode = () => document.querySelector('input[name="mode"]:checked').value;
const filter = () => FILTERS.find((f) => f.id === $("#filter").value);
const telescope = () => TELESCOPES.find((t) => t.id === $("#telescope").value);

function setValue(key, value) { $(`#${key}`).value = Number.isFinite(value) ? String(Number(value.toPrecision(6))) : ""; }

function applyFilter() {
  const f = filter(), t = telescope();
  setValue("zeroPoint", f.zeroPoint);
  $("#zp-hint").textContent = `${f.id}: ${f.system} system, λ ≈ ${f.lambda} Å, width ${f.width} Å`;
  if (t) { setValue("throughput", t.throughput[f.id]); setValue("extinction", t.extinction[f.id]); }
  applySky();
}
function applySky() {
  const preset = $("#skyPreset").value;
  if (preset !== "custom") setValue("sky", SKY[filter().id][preset]);
}
function applyTelescope() {
  const t = telescope();
  if (!t) return;
  for (const k of TELESCOPE_KEYS) setValue(k, t[k]);
  setValue("apertureFactor", 1);
  applyFilter();
}

function rawForm() { return Object.fromEntries(FIELDS.map((f) => [f.key, $(`#${f.key}`).value.trim()])); }

function factsHtml(out, p) {
  const r = out.r, budget = noiseBudget(out.source, out.t, r), pk = peakPixel(out.t, r, p);
  const pct = (x) => `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%`;
  const names = { source: "the source itself (photon noise)", sky: "the sky", dark: "dark current", read: "read noise" };
  const fact = (label, value, note = "") => `<div><dt>${label}</dt><dd>${value}${note ? `<small>${note}</small>` : ""}</dd></div>`;
  return `<dl class="facts">` +
    fact("Source signal", `${sig(out.source)} e⁻/s`, `${sig(out.source * out.t)} e⁻ in the aperture`) +
    fact("Sky", `${sig(r.sky)} e⁻/s/pixel`, `${p.sky} mag/arcsec²`) +
    fact("Aperture", `${r.radius.toFixed(2)}″ radius`, `${r.radiusPix.toFixed(1)} pixels; ${Math.round(r.npix)} pixels hold ${pct(r.enclosed)} of the light`) +
    fact("Noise budget", `source ${pct(budget.fractions.source)}, sky ${pct(budget.fractions.sky)}`, `dark ${pct(budget.fractions.dark)}, read ${pct(budget.fractions.read)} of the variance; mostly ${names[budget.dominant]}`) +
    fact("Peak pixel", `${sig(pk.adu)} ADU`, pk.saturated ? `<span class="warn">${pct(pk.wellFraction)} of full well: likely saturated or non-linear. Use shorter exposures.</span>` : `${pct(pk.wellFraction)} of full well, without bias`) +
    fact("Magnitude error", `±${(1.0857 / out.snr).toFixed(out.snr > 100 ? 4 : 3)} mag`, "1.0857 / (S/N)") +
    fact("Collecting area", `${(r.area / 1e4).toFixed(3)} m²`, `throughput ${p.throughput}`) +
    fact("Instrumental zero point", `${(2.5 * Math.log10(r.perMag0)).toFixed(2)} mag`, "gives 1 e⁻/s above the atmosphere") +
    `</dl>`;
}

function run() {
  const m = mode(), f = filter();
  document.querySelectorAll("[data-modes]").forEach((el) => { el.hidden = !el.dataset.modes.split(" ").includes(m); });
  const { params, errors } = readParams(rawForm(), m);
  for (const fd of FIELDS) {
    const el = $(`#${fd.key}`);
    if (errors.some((e) => e.field === fd.key)) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid");
  }
  if (errors.length) {
    $("#etc-status").textContent = errors.map((e) => e.message).join(" ");
    return;
  }
  $("#etc-status").textContent = "";
  const out = solve(m, params);
  if (!Number.isFinite(out.t) || !Number.isFinite(out.snr) || !Number.isFinite(out.mag)) { $("#etc-status").textContent = "These values do not give a usable result."; return; }
  const band = `${f.id} (${f.system})`;
  const headline = m === "snr" ? `S/N ${out.snr.toFixed(1)} <small>in ${esc(formatTime(out.t))}</small>`
    : m === "time" ? `${esc(formatTime(out.t))} <small>to reach S/N ${sig(out.snr)}</small>`
      : `${f.id} = ${out.mag.toFixed(2)} <small>at S/N ${sig(out.snr)} in ${esc(formatTime(out.t))}</small>`;
  const subject = m === "maglim" ? "the faintest star you can measure" : `a ${band} ${out.mag} mag star`;
  const longNote = out.t > 3600 ? ` <span class="warn">That is longer than an hour. Split it into several exposures: each extra read-out adds read noise, so the total S/N will be a little lower.</span>` : "";
  $("#result").innerHTML = `<div class="headline"><p class="big">${headline}</p><p class="muted">For ${esc(subject)} through ${esc(telescope()?.name ?? "a custom telescope")}, at airmass ${params.airmass}.${longNote}</p></div>` + factsHtml(out, params);
  $("#curve").innerHTML = chartSvg(out) + `<p class="muted">The marked point is the result above.${m === "snr" ? "" : " The dashed line is the target S/N."} S/N grows roughly as √t once the source or the sky dominates the noise.</p>`;
}

export function init() {
  const groups = [...new Set(FILTERS.map((f) => f.group))];
  $("#filter").innerHTML = groups.map((g) => `<optgroup label="${esc(g)}">${FILTERS.filter((f) => f.group === g).map((f) => `<option value="${f.id}">${f.id}</option>`).join("")}</optgroup>`).join("");
  $("#filter").value = "V";
  $("#telescope").innerHTML = TELESCOPES.map((t) => `<option value="${t.id}">${esc(t.name)}</option>`).join("") + `<option value="custom">Custom: every value is yours</option>`;
  applyTelescope();
  let lastPreset = TELESCOPES[0].id, pending = 0;
  const schedule = () => { cancelAnimationFrame(pending); pending = requestAnimationFrame(run); };
  $("#filter").addEventListener("change", () => { applyFilter(); run(); });
  $("#telescope").addEventListener("change", () => { if (telescope()) lastPreset = telescope().id; applyTelescope(); run(); });
  $("#skyPreset").addEventListener("change", () => { applySky(); run(); });
  $("#reset").addEventListener("click", () => { $("#telescope").value = lastPreset; applyTelescope(); run(); });
  document.querySelectorAll('input[name="mode"]').forEach((el) => el.addEventListener("change", run));
  for (const f of FIELDS) {
    $(`#${f.key}`).addEventListener("input", () => {
      if (f.key === "sky") $("#skyPreset").value = "custom";
      if ((TELESCOPE_KEYS.includes(f.key) || ["throughput", "extinction", "apertureFactor", "zeroPoint"].includes(f.key)) && telescope()) $("#telescope").value = "custom";
      schedule();
    });
  }
  $("#etc-form").addEventListener("submit", (event) => { event.preventDefault(); run(); });
  run();
}

if (typeof document !== "undefined" && document.getElementById("etc-form")) init();

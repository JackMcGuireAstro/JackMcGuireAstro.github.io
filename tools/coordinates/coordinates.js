// Coordinate & time converter. Everything runs in the visitor's browser; nothing is stored
// or sent. The maths lives in ../lib/astrometry.js; this file is the page and the batch mode.
import {
  parseAngle, formatRA, formatDec, formatHours, galactic, ecliptic, constellation, precess, julianYearToJd,
  jdFromMs, mjdFromMs, msFromJd, msFromMjd, siderealTime, horizontal, refractionDeg,
} from "../lib/astrometry.js?v=20261003";
import { SITES } from "../../tonight/planner.js?v=20261003";

const J2000_JD = 2451545.0;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const ascii = (s) => String(s).replace(/−/g, "-");
const fixed = (x, n) => (x == null || !Number.isFinite(x) ? "" : x.toFixed(n));
const signed = (x, n) => (x < 0 ? "−" : "+") + Math.abs(x).toFixed(n);
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"];
const compass = (az) => COMPASS[Math.round(az / 22.5) % 16];

// ------------------------------------------------------------------ pure helpers (tested in node)
const COORD_TOKEN = /^[+-]?(?:\d|\.\d)[\d.:]*(?:(?:deg|[hdms°'"])[\d.:]*)*$/i;

function splitTarget(tokens) {
  let start = tokens.length;
  while (start > 0 && COORD_TOKEN.test(tokens[start - 1])) start -= 1;
  const run = tokens.length - start;
  if (run < 2) return null;
  const attempt = (raCount, decCount) => {
    const decAt = tokens.length - decCount, raAt = decAt - raCount;
    if (raAt < start || raCount < 1 || decCount < 1) return null;
    const ra = parseAngle(tokens.slice(raAt, decAt).join(" "), "ra"), dec = parseAngle(tokens.slice(decAt).join(" "), "dec");
    return { name: tokens.slice(0, raAt).join(" "), ra, dec };
  };
  const signedAt = tokens.findLastIndex((t, i) => i > start && /^[+-]/.test(t));
  if (signedAt > 0) {
    const decCount = tokens.length - signedAt;
    if (decCount > 3) return { name: tokens.slice(0, start).join(" "), ra: { error: "too many fields after the Dec sign" }, dec: {} };
    return attempt(Math.min(decCount, signedAt - start), decCount) || attempt(1, decCount);
  }
  const order = run >= 6 ? [3] : run >= 4 ? [2, 1] : [1];
  let last = null;
  for (const n of order) {
    last = attempt(n, n);
    if (last && !last.ra.error && !last.dec.error) return last;
  }
  return last;
}

// Parse a pasted list: one "name ra dec" per line. Returns [{name, ra, dec, line} | {name, error, line}].
export function parseBatch(text) {
  const rows = [];
  String(text ?? "").replace(/−/g, "-").split(/\r?\n/).forEach((raw, i) => {
    const line = raw.trim();
    if (!line || line.startsWith("#") || /^name\b.*\bra\b.*\bdec\b/i.test(line)) return;
    let parsed;
    if (/[\t,]/.test(line)) {
      const fields = line.split(/\s*[\t,]\s*/).filter((f) => f !== "");
      if (fields.length >= 2) {
        const ra = parseAngle(fields[fields.length - 2], "ra"), dec = parseAngle(fields[fields.length - 1], "dec");
        parsed = { name: fields.slice(0, -2).join(", "), ra, dec };
      }
    } else {
      parsed = splitTarget(line.split(/\s+/));
    }
    if (!parsed) { rows.push({ name: line, error: "no RA and Dec found", line: i + 1 }); return; }
    const err = parsed.ra.error || parsed.dec.error;
    if (err) rows.push({ name: parsed.name || line.split(/\s+/)[0], error: err, line: i + 1 });
    else rows.push({ name: parsed.name, ra: parsed.ra.deg, dec: parsed.dec.deg, line: i + 1 });
  });
  return rows;
}

// Everything the page shows for one ICRS/J2000 position at one time and site.
export function describe(ra, dec, ms, site) {
  const g = galactic(ra, dec), e = ecliptic(ra, dec), c = constellation(ra, dec);
  const out = { ra, dec, l: g.l, b: g.b, lambda: e.lambda, beta: e.beta, constellation: c };
  if (Number.isFinite(ms) && site && Number.isFinite(site.lat) && Number.isFinite(site.lon)) out.sky = horizontal(ra, dec, ms, site.lat, site.lon);
  return out;
}

const csvCell = (v) => {
  const s = String(v ?? "");
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
};

export function toCsv(rows, { ms, site }) {
  const header = ["name", "ra_hms", "dec_dms", "ra_deg", "dec_deg", "l_deg", "b_deg", "ecl_lon_deg", "ecl_lat_deg", "constellation",
    "alt_deg", "az_deg", "airmass", "hour_angle_h", "utc", "site_lat_deg", "site_lon_deg", "error"];
  const utc = Number.isFinite(ms) ? new Date(ms).toISOString() : "";
  const lines = [header.join(",")];
  for (const r of rows) {
    if (r.error) { lines.push([r.name, ...Array(header.length - 2).fill(""), `line ${r.line}: ${r.error}`].map(csvCell).join(",")); continue; }
    const d = describe(r.ra, r.dec, ms, site), s = d.sky;
    lines.push([r.name, formatRA(r.ra, 2), ascii(formatDec(r.dec, 1)), fixed(r.ra, 6), fixed(r.dec, 6), fixed(d.l, 6), fixed(d.b, 6),
      fixed(d.lambda, 6), fixed(d.beta, 6), d.constellation.abbr, s ? fixed(s.alt, 3) : "", s ? fixed(s.az, 3) : "",
      s && s.airmass ? fixed(s.airmass, 3) : "", s ? fixed(s.hourAngle / 15, 4) : "", utc,
      site ? fixed(site.lat, 4) : "", site ? fixed(site.lon, 4) : "", ""].map(csvCell).join(","));
  }
  return lines.join("\r\n") + "\r\n";
}

export function readUrlTarget(search) {
  const p = new URLSearchParams(search || "");
  if (!p.has("ra") && !p.has("dec")) return null;
  return { ra: p.get("ra") || "", dec: p.get("dec") || "", name: p.get("name") || "" };
}

// ------------------------------------------------------------------ page
const $ = (selector) => document.querySelector(selector);
const state = { ms: Date.now(), batch: [] };

function siteFromForm() {
  const id = $("#site").value;
  if (id === "custom") {
    const lat = Number($("#lat").value), lon = Number($("#lon").value);
    return { id, name: "Custom site", lat, lon: lon > 180 ? lon - 360 : lon };
  }
  return SITES.find((s) => s.id === id);
}

const utcInputValue = (ms) => new Date(Math.floor(ms / 1000) * 1000).toISOString().slice(0, 19);
function parseUtcInput(value) {
  const m = /^(\d{4,})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2})(?:\.(\d+))?)?$/.exec(value || "");
  if (!m) return NaN;
  return Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0), Math.round(Number(`0.${m[7] || 0}`) * 1000));
}

function syncTime(source) {
  if (source !== "utc") $("#utc").value = utcInputValue(state.ms);
  if (source !== "jd") $("#jd").value = jdFromMs(state.ms).toFixed(6);
  if (source !== "mjd") $("#mjd").value = mjdFromMs(state.ms).toFixed(6);
}

function setInvalid(el, bad) { if (bad) el.setAttribute("aria-invalid", "true"); else el.removeAttribute("aria-invalid"); }

function readTarget({ quiet = false } = {}) {
  const raText = $("#ra").value, decText = $("#dec").value;
  if (!raText.trim() && !decText.trim()) { setInvalid($("#ra"), false); setInvalid($("#dec"), false); return { empty: true }; }
  const ra = parseAngle(raText, "ra"), dec = parseAngle(decText, "dec");
  setInvalid($("#ra"), !quiet && !!ra.error); setInvalid($("#dec"), !quiet && !!dec.error);
  if (ra.error || dec.error) return { error: ra.error || dec.error };
  const equinox = $("#equinox").value === "" ? 2000 : Number($("#equinox").value);
  const badEquinox = !(equinox >= 1800 && equinox <= 2200);
  setInvalid($("#equinox"), badEquinox);
  if (badEquinox) return { error: "The equinox must be a year between 1800 and 2200." };
  const entered = { ra: ra.deg, dec: dec.deg };
  const j2000 = equinox === 2000 ? entered : precess(ra.deg, dec.deg, julianYearToJd(equinox), J2000_JD);
  return { ...j2000, entered, equinox, name: $("#name").value.trim() };
}

const fact = (label, value, note = "") => `<div><dt>${label}</dt><dd>${value}${note ? `<small>${note}</small>` : ""}</dd></div>`;

function render() {
  const status = $("#coord-status");
  const site = siteFromForm();
  const siteOk = site && Number.isFinite(site.lat) && Math.abs(site.lat) <= 90 && Number.isFinite(site.lon);
  if ($("#site").value === "custom") { setInvalid($("#lat"), !siteOk); setInvalid($("#lon"), !siteOk); }
  const st = siteOk ? siderealTime(state.ms, site.lon) : null;
  $("#time").innerHTML = `<dl class="facts">` +
    fact("UTC", esc(new Date(state.ms).toISOString().replace("T", " ").replace("Z", "")), "ISO 8601, UTC") +
    fact("Julian Date", jdFromMs(state.ms).toFixed(6)) + fact("Modified JD", mjdFromMs(state.ms).toFixed(6)) +
    fact("Greenwich mean sidereal", formatRA(siderealTime(state.ms, 0).gmst, 1)) +
    (st ? fact("Local mean sidereal", formatRA(st.lmst, 1), esc(site.name)) + fact("Local apparent sidereal", formatRA(st.last, 1), "use this for hour angles") : "") +
    `</dl>`;

  const t = readTarget();
  const finder = $("#finder-link");
  if (t.empty) { status.textContent = siteOk ? "" : "Enter a latitude between −90 and 90 and a longitude."; finder.href = "../finder/"; return; }
  if (t.error) { status.textContent = t.error; return; }
  status.textContent = siteOk ? "" : "Enter a latitude between −90 and 90 and a longitude.";
  const d = describe(t.ra, t.dec, state.ms, siteOk ? site : null);
  const q = new URLSearchParams({ ra: t.ra.toFixed(6), dec: t.dec.toFixed(6), ...(t.name ? { name: t.name } : {}) });
  finder.href = `../finder/?${q}`;
  $("#position").innerHTML = (t.name ? `<h3>${esc(t.name)}</h3>` : "") + `<dl class="facts">` +
    fact("RA (J2000)", formatRA(t.ra, 3), formatRA(t.ra, 2, "hms")) +
    fact("Dec (J2000)", formatDec(t.dec, 2), formatDec(t.dec, 1, "dms")) +
    fact("RA (degrees)", `${t.ra.toFixed(6)}°`) + fact("Dec (degrees)", `${signed(t.dec, 6)}°`) +
    fact("Galactic l", `${d.l.toFixed(5)}°`) + fact("Galactic b", `${signed(d.b, 5)}°`) +
    fact("Ecliptic λ (J2000)", `${d.lambda.toFixed(5)}°`) + fact("Ecliptic β (J2000)", `${signed(d.beta, 5)}°`) +
    fact("Constellation", esc(d.constellation.name), esc(d.constellation.abbr)) +
    (t.equinox !== 2000 ? fact(`As entered (J${t.equinox})`, `${formatRA(t.entered.ra, 2)} ${formatDec(t.entered.dec, 1)}`, "precessed to J2000 above") : "") +
    `</dl>`;
  if (!d.sky) { $("#sky").innerHTML = `<p class="muted">Needs a valid site.</p>`; return; }
  const s = d.sky, ha = s.hourAngle / 15;
  const refracted = s.alt + refractionDeg(s.alt);
  $("#sky").innerHTML = `<dl class="facts">` +
    fact("Altitude", `${signed(s.alt, 3)}°`, s.alt > -1 ? `${signed(refracted, 2)}° with refraction` : "below the horizon") +
    fact("Azimuth", `${s.az.toFixed(3)}°`, `${compass(s.az)}, from north through east`) +
    fact("Airmass", s.airmass ? s.airmass.toFixed(3) : "—", s.airmass ? "Kasten &amp; Young (1989)" : "target is below the horizon") +
    fact("Hour angle", formatHours(ha, 0), Math.abs(ha) < 1 / 120 ? "on the meridian" : ha < 0 ? "east of the meridian (rising)" : "west of the meridian (setting)") +
    fact("Apparent RA (of date)", formatRA(s.apparent.ra, 2), "true equator and equinox") +
    fact("Apparent Dec (of date)", formatDec(s.apparent.dec, 1)) +
    fact("Site", `${signed(site.lat, 4)}°, ${signed(site.lon, 4)}°`, esc(site.name)) +
    `</dl>`;
}

function renderBatch() {
  const rows = state.batch, site = siteFromForm();
  const good = rows.filter((r) => !r.error).length, bad = rows.length - good;
  $("#csv").disabled = rows.length === 0;
  $("#batch-status").classList.toggle("ok", rows.length > 0 && bad === 0);
  $("#batch-status").textContent = rows.length ? `${good} ${good === 1 ? "target" : "targets"} converted${bad ? `, ${bad} ${bad === 1 ? "line" : "lines"} could not be read` : ""}.` : "Paste at least one line.";
  if (!rows.length) { $("#batch-results").innerHTML = ""; return; }
  const body = rows.map((r) => {
    if (r.error) return `<tr class="bad"><th scope="row">${esc(r.name)}</th><td colspan="11">Line ${r.line}: ${esc(r.error)}</td></tr>`;
    const d = describe(r.ra, r.dec, state.ms, site), s = d.sky;
    return `<tr><th scope="row">${esc(r.name || "—")}</th><td>${formatRA(r.ra, 2)}</td><td>${formatDec(r.dec, 1)}</td>` +
      `<td class="num">${r.ra.toFixed(5)}</td><td class="num">${signed(r.dec, 5)}</td><td class="num">${d.l.toFixed(4)}</td><td class="num">${signed(d.b, 4)}</td>` +
      `<td class="num">${d.lambda.toFixed(4)}</td><td class="num">${signed(d.beta, 4)}</td><td>${d.constellation.abbr}</td>` +
      `<td class="num">${s ? signed(s.alt, 2) : "—"}</td><td class="num">${s ? s.az.toFixed(2) : "—"}</td><td class="num">${s && s.airmass ? s.airmass.toFixed(3) : "—"}</td></tr>`;
  }).join("");
  $("#batch-results").innerHTML = `<p class="muted">Altitude, azimuth and airmass at ${esc(new Date(state.ms).toISOString().slice(0, 19).replace("T", " "))} UTC from ${esc(site ? site.name : "—")}.</p>` +
    `<div class="table-wrap" role="region" aria-label="Converted targets" tabindex="0"><table><caption class="visually-hidden">Converted targets</caption><thead><tr>` +
    `<th scope="col">Name</th><th scope="col">RA</th><th scope="col">Dec</th><th scope="col" class="num">RA°</th><th scope="col" class="num">Dec°</th>` +
    `<th scope="col" class="num">l°</th><th scope="col" class="num">b°</th><th scope="col" class="num">λ°</th><th scope="col" class="num">β°</th><th scope="col">Con.</th>` +
    `<th scope="col" class="num">Alt°</th><th scope="col" class="num">Az°</th><th scope="col" class="num">Airmass</th></tr></thead><tbody>${body}</tbody></table></div>`;
}

function downloadCsv() {
  if (!state.batch.length) return;
  const blob = new Blob([toCsv(state.batch, { ms: state.ms, site: siteFromForm() })], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url; a.download = "coordinates.csv"; a.hidden = true;
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10000);
}

export function init() {
  const site = $("#site");
  site.innerHTML = SITES.map((s) => `<option value="${s.id}">${esc(s.name)}</option>`).join("") + '<option value="custom">Custom latitude/longitude…</option>';
  const toggleCustom = () => { $("#custom").hidden = site.value !== "custom"; };
  site.addEventListener("change", () => { toggleCustom(); render(); if (state.batch.length) renderBatch(); });
  const target = readUrlTarget(location.search);
  if (target) { $("#ra").value = target.ra; $("#dec").value = target.dec; $("#name").value = target.name; }
  syncTime();
  const timeChanged = (source, ms) => {
    const el = $(`#${source}`);
    setInvalid(el, !Number.isFinite(ms));
    if (!Number.isFinite(ms) || Math.abs(ms) > 8.64e15) { $("#coord-status").textContent = "That date or Julian Date could not be read."; return; }
    state.ms = ms; syncTime(source); render(); if (state.batch.length) renderBatch();
  };
  $("#utc").addEventListener("change", () => timeChanged("utc", parseUtcInput($("#utc").value)));
  $("#jd").addEventListener("change", () => timeChanged("jd", $("#jd").value.trim() === "" ? NaN : msFromJd(Number($("#jd").value))));
  $("#mjd").addEventListener("change", () => timeChanged("mjd", $("#mjd").value.trim() === "" ? NaN : msFromMjd(Number($("#mjd").value))));
  $("#now").addEventListener("click", () => { state.ms = Date.now(); syncTime(); render(); if (state.batch.length) renderBatch(); });
  for (const id of ["#lat", "#lon", "#equinox", "#ra", "#dec", "#name"]) $(id).addEventListener("change", render);
  $("#coord-form").addEventListener("submit", (event) => { event.preventDefault(); render(); });
  $("#batch-form").addEventListener("submit", (event) => { event.preventDefault(); state.batch = parseBatch($("#batch").value); renderBatch(); });
  $("#csv").addEventListener("click", downloadCsv);
  toggleCustom();
  render();
}

if (typeof document !== "undefined" && document.getElementById("coord-form")) init();

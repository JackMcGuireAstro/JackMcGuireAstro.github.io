// Positional astronomy shared by the coordinate converter and the finder chart.
// Pure functions only: no network, no storage, no DOM. Angles are in degrees unless a
// name says otherwise. Sidereal time and airmass come from the Tonight's sky module.
import { gmstDeg, airmass } from "../../tonight/astro.js?v=20261003";
import { ABBR, NAMES, BOUNDS } from "./constellations.js?v=20261003";

export { airmass };
export const D2R = Math.PI / 180, R2D = 180 / Math.PI, AS2R = D2R / 3600;
export const norm360 = (x) => ((x % 360) + 360) % 360;
const J2000 = 2451545.0;

// ------------------------------------------------------------------ parsing
const MINUS = /[−‒–—‐﹣－]/g;

function cleanAngleText(text) {
  return String(text ?? "").replace(MINUS, "-").replace(/[′’´]/g, "'").replace(/[″”]|''/g, '"').trim();
}

// One angle. kind "ra" or "dec". A single plain number is decimal degrees; a number with
// an "h" suffix is decimal hours. Two or three fields (spaces, colons or h/m/s, d/°/'/"
// marks) are sexagesimal: hours for RA, degrees for Dec. Returns {deg} or {error}.
export function parseAngle(text, kind) {
  const isRa = kind === "ra";
  const t = cleanAngleText(text);
  if (!t) return { error: `Enter ${isRa ? "a right ascension" : "a declination"}.` };
  const m = t.match(/^([+-]?)\s*(.*)$/);
  const sign = m[1] === "-" ? -1 : 1;
  const body = m[2];
  if (isRa && m[1] === "-") return { error: "Right ascension cannot be negative." };
  const decimalHours = /^(\d+(?:\.\d*)?|\.\d+)\s*h$/i.exec(body);
  if (decimalHours && isRa) {
    const h = Number(decimalHours[1]);
    return h < 24 ? { deg: h * 15 } : { error: "Hours must be below 24." };
  }
  if (/^(\d+(?:\.\d*)?|\.\d+)(?:\s*(?:d|deg|°))?$/i.test(body)) {
    const v = sign * parseFloat(body);
    if (isRa) return v >= 0 && v < 360 ? { deg: v } : { error: "Right ascension in degrees must be from 0 to 360." };
    return Math.abs(v) <= 90 ? { deg: v } : { error: "Declination must be between −90° and +90°." };
  }
  const parts = body.split(/\s*(?:deg|[hdms°:'"])\s*|\s+/i).filter((p) => p !== "");
  if (parts.length < 2 || parts.length > 3 || !parts.every((p) => /^(\d+(?:\.\d*)?|\.\d+)$/.test(p))) {
    return { error: `Could not read “${String(text).trim()}” as ${isRa ? "a right ascension" : "a declination"}.` };
  }
  const [a, b, c = 0] = parts.map(Number);
  if (parts.slice(0, -1).some((p) => p.includes("."))) return { error: "Only the last field may have decimals." };
  if (b >= 60 || c >= 60) return { error: "Minutes and seconds must be below 60." };
  const value = a + b / 60 + c / 3600;
  if (isRa) return value < 24 ? { deg: value * 15 } : { error: "Hours must be below 24." };
  return value <= 90 ? { deg: sign * value } : { error: "Declination must be between −90° and +90°." };
}

// "RA Dec" in one string: comma-separated, or whitespace tokens split where the Dec sign
// starts, or split in half (2, 4 or 6 fields).
export function parseCoordinates(text) {
  const t = cleanAngleText(text);
  if (!t) return { error: "Enter coordinates." };
  let ra, dec;
  if (t.includes(",")) {
    const bits = t.split(",");
    if (bits.length !== 2) return { error: "Use one comma, between RA and Dec." };
    [ra, dec] = bits;
  } else {
    const tokens = t.replace(/([hms°'"dD])\s*(?=[+-])/g, "$1 ").split(/\s+/);
    let cut = tokens.findIndex((tok, i) => i > 0 && /^[+-]/.test(tok));
    if (cut < 0) {
      const joined = tokens.join(" ");
      const hms = /^(\S+\s*h\s*\S*\s*m?\s*\S*\s*s?)\s+(.+)$/i.exec(joined);
      if (tokens.length % 2 === 0) cut = tokens.length / 2;
      else if (hms) { ra = hms[1]; dec = hms[2]; }
      else return { error: "Could not tell where RA ends and Dec starts; put a sign (+ or −) on the Dec or a comma between them." };
    }
    if (ra === undefined) { ra = tokens.slice(0, cut).join(" "); dec = tokens.slice(cut).join(" "); }
  }
  const a = parseAngle(ra, "ra");
  if (a.error) return a;
  const d = parseAngle(dec, "dec");
  if (d.error) return d;
  return { ra: a.deg, dec: d.deg };
}

// ------------------------------------------------------------------ formatting
function sexagesimal(value, decimals) {
  const scale = 10 ** decimals;
  let total = Math.round(Math.abs(value) * 3600 * scale);
  const s = (total % (60 * scale)) / scale; total = Math.floor(total / (60 * scale));
  const m = total % 60, d = Math.floor(total / 60);
  return [d, m, s];
}
const pad = (n, w) => String(n).padStart(w, "0");
const secs = (s, decimals) => decimals > 0 ? s.toFixed(decimals).padStart(3 + decimals, "0") : pad(Math.round(s), 2);

export function formatRA(deg, decimals = 2, sep = ":") {
  let [h, m, s] = sexagesimal(norm360(deg) / 15, decimals);
  if (h >= 24) h -= 24;
  return sep === "hms" ? `${pad(h, 2)}h${pad(m, 2)}m${secs(s, decimals)}s` : [pad(h, 2), pad(m, 2), secs(s, decimals)].join(sep);
}

export function formatDec(deg, decimals = 1, sep = ":") {
  const [d, m, s] = sexagesimal(deg, decimals);
  const sign = deg < 0 && (d || m || s) ? "−" : "+";
  return sep === "dms" ? `${sign}${pad(d, 2)}°${pad(m, 2)}′${secs(s, decimals)}″` : sign + [pad(d, 2), pad(m, 2), secs(s, decimals)].join(sep);
}

// Hours as h:m:s (sidereal time, hour angle).
export function formatHours(hours, decimals = 1) {
  const sign = hours < 0 ? "−" : "";
  const [h, m, s] = sexagesimal(hours, decimals);
  return `${sign}${pad(h, 2)}:${pad(m, 2)}:${secs(s, decimals)}`;
}

// ------------------------------------------------------------------ time
export const jdFromMs = (ms) => ms / 86400000 + 2440587.5;
export const mjdFromMs = (ms) => ms / 86400000 + 40587;
export const msFromJd = (jd) => (jd - 2440587.5) * 86400000;
export const msFromMjd = (mjd) => (mjd - 40587) * 86400000;

// ------------------------------------------------------------------ vectors and matrices
export const toVec = (ra, dec) => {
  const a = ra * D2R, d = dec * D2R;
  return [Math.cos(d) * Math.cos(a), Math.cos(d) * Math.sin(a), Math.sin(d)];
};
export const fromVec = (v) => {
  const r = Math.hypot(v[0], v[1]);
  return { lon: norm360(Math.atan2(v[1], v[0]) * R2D), lat: Math.atan2(v[2], r) * R2D };
};
export const mulMV = (m, v) => m.map((row) => row[0] * v[0] + row[1] * v[1] + row[2] * v[2]);
const mulMM = (a, b) => a.map((row) => [0, 1, 2].map((j) => row[0] * b[0][j] + row[1] * b[1][j] + row[2] * b[2][j]));
const transpose = (m) => [0, 1, 2].map((i) => [m[0][i], m[1][i], m[2][i]]);
const rot1 = (a) => [[1, 0, 0], [0, Math.cos(a), Math.sin(a)], [0, -Math.sin(a), Math.cos(a)]];
const rot2 = (a) => [[Math.cos(a), 0, -Math.sin(a)], [0, 1, 0], [Math.sin(a), 0, Math.cos(a)]];
const rot3 = (a) => [[Math.cos(a), Math.sin(a), 0], [-Math.sin(a), Math.cos(a), 0], [0, 0, 1]];

// ICRS to Galactic: the Hipparcos matrix (ESA 1997, vol. 1, eq. 1.5.11).
export const ICRS_TO_GALACTIC = [
  [-0.0548755604162154, -0.8734370902348850, -0.4838350155487132],
  [+0.4941094278755837, -0.4448296299600112, +0.7469822444972189],
  [-0.8676661490190047, -0.1980763734312015, +0.4559837761750669],
];
// ICRS to mean ecliptic and equinox of J2000: IAU 2006 (frame bias, then a rotation by the
// mean obliquity 84381.406″), as computed by SOFA/ERFA ecm06 at J2000.
export const ICRS_TO_ECLIPTIC_J2000 = [
  [9.9999999999999412e-01, -7.0783689609715561e-08, 8.0562139776131861e-08],
  [3.2897004077419646e-08, 9.1748212991495837e-01, 3.9777699944404793e-01],
  [-1.0207044725484355e-07, -3.9777699944404304e-01, 9.1748212991495559e-01],
];

export function galactic(ra, dec) {
  const g = fromVec(mulMV(ICRS_TO_GALACTIC, toVec(ra, dec)));
  return { l: g.lon, b: g.lat };
}
export function ecliptic(ra, dec) {
  const e = fromVec(mulMV(ICRS_TO_ECLIPTIC_J2000, toVec(ra, dec)));
  return { lambda: e.lon, beta: e.lat };
}
export function fromGalactic(l, b) {
  const v = fromVec(mulMV(transpose(ICRS_TO_GALACTIC), toVec(l, b)));
  return { ra: v.lon, dec: v.lat };
}

// IAU 1976 precession (Lieske et al. 1977) between two Julian dates (TT ≈ UTC here).
export function precessionMatrix(jdFrom, jdTo) {
  const T = (jdFrom - J2000) / 36525, t = (jdTo - jdFrom) / 36525;
  const w = 2306.2181 + 1.39656 * T - 0.000139 * T * T;
  const zeta = (w * t + (0.30188 - 0.000344 * T) * t * t + 0.017998 * t ** 3) * AS2R;
  const z = (w * t + (1.09468 + 0.000066 * T) * t * t + 0.018203 * t ** 3) * AS2R;
  const theta = ((2004.3109 - 0.85330 * T - 0.000217 * T * T) * t - (0.42665 + 0.000217 * T) * t * t - 0.041833 * t ** 3) * AS2R;
  return mulMM(rot3(-z), mulMM(rot2(theta), rot3(-zeta)));
}
export const julianYearToJd = (year) => J2000 + (year - 2000) * 365.25;
export const besselianYearToJd = (year) => 2415020.31352 + (year - 1900) * 365.242198781;

export function precess(ra, dec, jdFrom, jdTo) {
  const p = fromVec(mulMV(precessionMatrix(jdFrom, jdTo), toVec(ra, dec)));
  return { ra: p.lon, dec: p.lat };
}

// Mean obliquity (IAU 1980) and the four largest nutation terms (Meeus, Astronomical
// Algorithms ch. 22: about 0.5″ in longitude and 0.1″ in obliquity).
export function meanObliquityDeg(jd) {
  const T = (jd - J2000) / 36525;
  return 23.439291111 - (46.8150 * T + 0.00059 * T * T - 0.001813 * T ** 3) / 3600;
}
export function nutation(jd) {
  const T = (jd - J2000) / 36525;
  const om = (125.04452 - 1934.136261 * T) * D2R, L = (280.4665 + 36000.7698 * T) * D2R, Lp = (218.3165 + 481267.8813 * T) * D2R;
  const dpsi = -17.20 * Math.sin(om) - 1.32 * Math.sin(2 * L) - 0.23 * Math.sin(2 * Lp) + 0.21 * Math.sin(2 * om);
  const deps = 9.20 * Math.cos(om) + 0.57 * Math.cos(2 * L) + 0.10 * Math.cos(2 * Lp) - 0.09 * Math.cos(2 * om);
  return { dpsi: dpsi / 3600, deps: deps / 3600 };
}

// Sidereal time in degrees for UTC (UT1 − UTC, under 0.9 s, is ignored).
export function siderealTime(ms, lonDeg = 0) {
  const jd = jdFromMs(ms), gmst = gmstDeg(jd), n = nutation(jd);
  const eqeq = n.dpsi * Math.cos(meanObliquityDeg(jd) * D2R);
  return { gmst, gast: norm360(gmst + eqeq), lmst: norm360(gmst + lonDeg), last: norm360(gmst + eqeq + lonDeg) };
}

// Apparent geocentric place (true equator and equinox of date) of an ICRS/J2000 direction:
// annual aberration (circular-orbit approximation, ≤0.3″ off), IAU 1976 precession, and the
// truncated nutation above. Light deflection and the ICRS frame bias (0.02″) are ignored.
export function apparentPlace(ra, dec, ms) {
  const jd = jdFromMs(ms), n = jd - J2000;
  const L = norm360(280.460 + 0.9856474 * n), g = norm360(357.528 + 0.9856003 * n) * D2R;
  const sunLon = (L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g)) * D2R;
  const eps0 = 23.4392911 * D2R, kappa = 20.49552 * AS2R;
  const v = [kappa * Math.sin(sunLon), -kappa * Math.cos(sunLon) * Math.cos(eps0), -kappa * Math.cos(sunLon) * Math.sin(eps0)];
  const p = toVec(ra, dec).map((x, i) => x + v[i]);
  const nu = nutation(jd), eps = meanObliquityDeg(jd) * D2R;
  const N = mulMM(rot1(-(eps + nu.deps * D2R)), mulMM(rot3(-nu.dpsi * D2R), rot1(eps)));
  const out = fromVec(mulMV(mulMM(N, precessionMatrix(J2000, jd)), p));
  return { ra: out.lon, dec: out.lat };
}

// Altitude and azimuth (north = 0°, east = 90°) without refraction, plus hour angle (deg,
// −180..180) and airmass (Kasten & Young 1989; null below the horizon).
export function horizontal(ra, dec, ms, latDeg, lonDeg) {
  const app = apparentPlace(ra, dec, ms), st = siderealTime(ms, lonDeg);
  const H = ((st.last - app.ra + 540) % 360) - 180;
  const h = H * D2R, d = app.dec * D2R, phi = latDeg * D2R;
  const alt = Math.asin(Math.min(1, Math.max(-1, Math.sin(phi) * Math.sin(d) + Math.cos(phi) * Math.cos(d) * Math.cos(h)))) * R2D;
  const az = norm360(Math.atan2(-Math.cos(d) * Math.sin(h), Math.sin(d) * Math.cos(phi) - Math.cos(d) * Math.sin(phi) * Math.cos(h)) * R2D);
  return { alt, az, hourAngle: H, airmass: airmass(alt), apparent: app, sidereal: st };
}

// Sæmundsson (1986) refraction for an airless (true) altitude, in degrees, at 1010 hPa and 10 °C.
export function refractionDeg(altDeg) {
  if (altDeg < -1) return 0;
  const a = altDeg + 10.3 / (altDeg + 5.11);
  return 1.02 / Math.tan(a * D2R) / 60;
}

// ------------------------------------------------------------------ constellations
const B1875 = besselianYearToJd(1875);
export function constellation(ra, dec) {
  const p = precess(ra, dec, J2000, B1875), raH = p.ra / 15;
  for (const [lo, hi, decLo, i] of BOUNDS) {
    if (raH >= lo && raH < hi && p.dec >= decLo) return { abbr: ABBR[i], name: NAMES[ABBR[i]] };
  }
  return { abbr: "Oct", name: NAMES.Oct };
}

// ------------------------------------------------------------------ offsets on the sky
// Standard (gnomonic) coordinates of (ra, dec) about (ra0, dec0), in degrees: xi east, eta north.
export function tangentOffset(ra0, dec0, ra, dec) {
  const a = (ra - ra0) * D2R, d0 = dec0 * D2R, d = dec * D2R;
  const den = Math.sin(d0) * Math.sin(d) + Math.cos(d0) * Math.cos(d) * Math.cos(a);
  return {
    xi: (Math.cos(d) * Math.sin(a) / den) * R2D,
    eta: ((Math.cos(d0) * Math.sin(d) - Math.sin(d0) * Math.cos(d) * Math.cos(a)) / den) * R2D,
    front: den > 0,
  };
}

export function separationDeg(ra1, dec1, ra2, dec2) {
  const a = dec1 * D2R, b = dec2 * D2R, dr = (ra2 - ra1) * D2R;
  const h = Math.sin((b - a) / 2) ** 2 + Math.cos(a) * Math.cos(b) * Math.sin(dr / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) * R2D;
}

// The move from an offset star to the target: east and north offsets in arcseconds on the
// sky (tangent plane centred on the offset star), and the RA difference in seconds of time.
export function offsetMove(starRa, starDec, ra, dec) {
  const t = tangentOffset(starRa, starDec, ra, dec);
  const dRa = ((ra - starRa + 540) % 360) - 180;
  return { east: t.xi * 3600, north: t.eta * 3600, dRaSeconds: (dRa / 15) * 3600, separation: separationDeg(starRa, starDec, ra, dec) * 3600 };
}

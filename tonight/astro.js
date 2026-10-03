// Low-precision positional astronomy for night planning (no network, no storage).
// Sun: Astronomical Almanac low-precision formulae (~0.01°). Moon: truncated series
// (~0.3°), ample for altitude curves, moon separation and illumination.
const D2R = Math.PI / 180, R2D = 180 / Math.PI;
const norm360 = (x) => ((x % 360) + 360) % 360;

export const julianDate = (ms) => ms / 86400000 + 2440587.5;

// Greenwich mean sidereal time in degrees.
export function gmstDeg(jd) {
  const d = jd - 2451545.0, t = d / 36525;
  return norm360(280.46061837 + 360.98564736629 * d + 0.000387933 * t * t - (t * t * t) / 38710000);
}

function eclipticToEquatorial(lambdaDeg, betaDeg, jd) {
  const eps = (23.439291 - 0.0130042 * ((jd - 2451545.0) / 36525)) * D2R;
  const l = lambdaDeg * D2R, b = betaDeg * D2R;
  const ra = Math.atan2(Math.sin(l) * Math.cos(eps) - Math.tan(b) * Math.sin(eps), Math.cos(l));
  const dec = Math.asin(Math.sin(b) * Math.cos(eps) + Math.cos(b) * Math.sin(eps) * Math.sin(l));
  return { ra: norm360(ra * R2D), dec: dec * R2D };
}

export function sunPosition(jd) {
  const n = jd - 2451545.0;
  const L = norm360(280.460 + 0.9856474 * n), g = norm360(357.528 + 0.9856003 * n) * D2R;
  const lambda = L + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g);
  return eclipticToEquatorial(lambda, 0, jd);
}

export function moonPosition(jd) {
  const T = (jd - 2451545.0) / 36525, s = (deg) => Math.sin(norm360(deg) * D2R);
  const lambda = 218.32 + 481267.881 * T + 6.29 * s(134.9 + 477198.85 * T) - 1.27 * s(259.2 - 413335.38 * T)
    + 0.66 * s(235.7 + 890534.23 * T) + 0.21 * s(269.9 + 954397.70 * T) - 0.19 * s(357.5 + 35999.05 * T)
    - 0.11 * s(186.6 + 966404.05 * T);
  const beta = 5.13 * s(93.3 + 483202.03 * T) + 0.28 * s(228.2 + 960400.87 * T)
    - 0.28 * s(318.3 + 6003.18 * T) - 0.17 * s(217.6 - 407332.20 * T);
  return eclipticToEquatorial(norm360(lambda), beta, jd);
}

export function separationDeg(ra1, dec1, ra2, dec2) {
  const a = dec1 * D2R, b = dec2 * D2R, dr = (ra2 - ra1) * D2R;
  const h = Math.sin((b - a) / 2) ** 2 + Math.cos(a) * Math.cos(b) * Math.sin(dr / 2) ** 2;
  return 2 * Math.asin(Math.min(1, Math.sqrt(h))) * R2D;
}

// Fraction of the Moon's disk lit (0 new .. 1 full), from the Sun-Moon elongation.
export function moonIllumination(jd) {
  const sun = sunPosition(jd), moon = moonPosition(jd);
  const psi = separationDeg(sun.ra, sun.dec, moon.ra, moon.dec) * D2R;
  return (1 - Math.cos(psi)) / 2;
}

export function altitudeDeg(raDeg, decDeg, latDeg, lstDeg) {
  const h = (lstDeg - raDeg) * D2R, phi = latDeg * D2R, dec = decDeg * D2R;
  return Math.asin(Math.sin(phi) * Math.sin(dec) + Math.cos(phi) * Math.cos(dec) * Math.cos(h)) * R2D;
}

// Kasten & Young (1989) relative air mass; null below the horizon.
export function airmass(altDeg) {
  if (!(altDeg > 0)) return null;
  return 1 / (Math.sin(altDeg * D2R) + 0.50572 * Math.pow(altDeg + 6.07995, -1.6364));
}

// One night sampled every `stepMinutes`, from local noon of the chosen date to the next
// noon, in UTC milliseconds. `noonUtcMs` is that local noon. Returns the samples and
// the usable window (Sun below `twilightDeg`).
export function nightGrid({ noonUtcMs, latDeg, lonDeg, stepMinutes = 5, twilightDeg = -12 }) {
  const steps = Math.round((24 * 60) / stepMinutes);
  const samples = [];
  for (let i = 0; i <= steps; i++) {
    const ms = noonUtcMs + i * stepMinutes * 60000, jd = julianDate(ms);
    const lst = norm360(gmstDeg(jd) + lonDeg);
    const sun = sunPosition(jd), moon = moonPosition(jd);
    samples.push({ ms, lst, sunAlt: altitudeDeg(sun.ra, sun.dec, latDeg, lst), moonAlt: altitudeDeg(moon.ra, moon.dec, latDeg, lst), moon });
  }
  const crossing = (level, falling) => {
    for (let i = 1; i < samples.length; i++) {
      const a = samples[i - 1].sunAlt, b = samples[i].sunAlt;
      if (falling ? a >= level && b < level : a < level && b >= level) {
        return samples[i - 1].ms + ((level - a) / (b - a)) * (samples[i].ms - samples[i - 1].ms);
      }
    }
    return null;
  };
  const usable = samples.filter((s) => s.sunAlt < twilightDeg);
  return {
    samples, stepMinutes, twilightDeg,
    sunset: crossing(-0.833, true), sunrise: crossing(-0.833, false),
    darkStart: crossing(twilightDeg, true), darkEnd: crossing(twilightDeg, false),
    usableCount: usable.length,
    moonIllumination: moonIllumination(julianDate(noonUtcMs + 12 * 3600000)),
  };
}

// Visibility of one target over the usable part of a night grid.
export function visibility(grid, raDeg, decDeg, latDeg, altLimitDeg) {
  let hours = 0, maxAlt = -90, bestMs = null, moonSep = null;
  const stepHours = grid.stepMinutes / 60;
  for (const s of grid.samples) {
    if (s.sunAlt >= grid.twilightDeg) continue;
    const alt = altitudeDeg(raDeg, decDeg, latDeg, s.lst);
    if (alt >= altLimitDeg) hours += stepHours;
    if (alt > maxAlt) { maxAlt = alt; bestMs = s.ms; moonSep = separationDeg(raDeg, decDeg, s.moon.ra, s.moon.dec); }
  }
  return { hours: Math.round(hours * 10) / 10, maxAlt: grid.usableCount ? maxAlt : null, bestMs, moonSep,
           bestAirmass: grid.usableCount ? airmass(maxAlt) : null };
}

// UTC milliseconds of local noon on `isoDate` (YYYY-MM-DD) in IANA `timeZone`.
export function localNoonUtc(isoDate, timeZone) {
  const [y, m, d] = isoDate.split("-").map(Number);
  let guess = Date.UTC(y, m - 1, d, 12, 0, 0);
  for (let i = 0; i < 3; i++) {
    const parts = Object.fromEntries(new Intl.DateTimeFormat("en-US", {
      timeZone, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    }).formatToParts(new Date(guess)).map((p) => [p.type, p.value]));
    const shown = Date.UTC(+parts.year, +parts.month - 1, +parts.day, +parts.hour, +parts.minute);
    guess += Date.UTC(y, m - 1, d, 12, 0, 0) - shown;
  }
  return guess;
}

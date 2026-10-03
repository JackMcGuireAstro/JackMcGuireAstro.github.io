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

// ------------------------------------------------------------------ barycentric time
// Transit ephemerides are given in BJD_TDB: the time the light would reach the solar-system
// barycentre, on the TDB scale. A clock on Earth sees the same event up to ~8.3 minutes
// earlier or later (the light-travel time across Earth's orbit, projected on the target's
// direction), plus TT-UTC. These convert between the two to a few seconds.
export const TT_MINUS_UTC_SECONDS = 69.184; // 37 leap seconds (since 2017-01-01) + 32.184 s
export const AU_LIGHT_SECONDS = 499.004784;
const EPS_J2000 = 23.4392911 * D2R;

// Planet's heliocentric ecliptic (J2000) position in au from mean elements (Standish 1992),
// with the equation of centre to first order in e. Used only for the Sun's wobble.
function planetEcliptic(T, L0, L1, w0, w1, e, a) {
  const L = L0 + L1 * T, w = w0 + w1 * T, M = norm360(L - w) * D2R;
  const nu = M + 2 * e * Math.sin(M) + 1.25 * e * e * Math.sin(2 * M), r = a * (1 - e * Math.cos(M));
  const lon = nu + w * D2R;
  return [r * Math.cos(lon), r * Math.sin(lon), 0];
}
// Earth's barycentric position (au, ICRS/J2000 equatorial axes) at TDB Julian date `jd`:
// minus the Sun's geocentric vector from the almanac formulae (longitude precessed to J2000),
// plus the Sun's offset from the barycentre caused by Jupiter and Saturn (up to ~0.008 au).
export function earthBarycentric(jd) {
  const n = jd - 2451545.0, T = n / 36525;
  const g = norm360(357.528 + 0.9856003 * n) * D2R;
  const lambda = (norm360(280.460 + 0.9856474 * n) + 1.915 * Math.sin(g) + 0.020 * Math.sin(2 * g) - 1.3972 * T) * D2R;
  const R = 1.00014 - 0.01671 * Math.cos(g) - 0.00014 * Math.cos(2 * g);
  const jup = planetEcliptic(T, 34.39644051, 3034.74612775, 14.72847983, 0.21252668, 0.04838624, 5.20288700);
  const sat = planetEcliptic(T, 49.95424423, 1222.49362201, 92.59887831, -0.41897216, 0.05386179, 9.53667594);
  // Earth heliocentric = -(Sun geocentric); Sun barycentric = -sum(m_p / M_sun * r_p).
  const ex = -R * Math.cos(lambda) - (jup[0] / 1047.3486 + sat[0] / 3497.898);
  const ey = -R * Math.sin(lambda) - (jup[1] / 1047.3486 + sat[1] / 3497.898);
  const ez = 0;
  return [ex, ey * Math.cos(EPS_J2000) - ez * Math.sin(EPS_J2000), ey * Math.sin(EPS_J2000) + ez * Math.cos(EPS_J2000)];
}
// Seconds to add to a TDB time at Earth to get BJD_TDB for a target at (ra, dec) J2000:
// the Rømer delay r_earth · n / c. Ranges over about ±499 s through the year.
export function barycentricCorrectionSeconds(jdTdb, raDeg, decDeg) {
  const [x, y, z] = earthBarycentric(jdTdb), ra = raDeg * D2R, dec = decDeg * D2R;
  return AU_LIGHT_SECONDS * (x * Math.cos(dec) * Math.cos(ra) + y * Math.cos(dec) * Math.sin(ra) + z * Math.sin(dec));
}
// UTC milliseconds -> BJD_TDB for a target, and back (two fixed-point steps converge to
// well under a millisecond: the correction drifts by at most ~0.1 s per minute).
export function utcMsToBjdTdb(ms, raDeg, decDeg) {
  const jdTdb = julianDate(ms) + TT_MINUS_UTC_SECONDS / 86400;
  return jdTdb + barycentricCorrectionSeconds(jdTdb, raDeg, decDeg) / 86400;
}
export function bjdTdbToUtcMs(bjd, raDeg, decDeg) {
  let jdTdb = bjd;
  for (let i = 0; i < 3; i++) jdTdb = bjd - barycentricCorrectionSeconds(jdTdb, raDeg, decDeg) / 86400;
  return (jdTdb - TT_MINUS_UTC_SECONDS / 86400 - 2440587.5) * 86400000;
}
export const localSiderealDeg = (ms, lonDeg) => norm360(gmstDeg(julianDate(ms)) + lonDeg);

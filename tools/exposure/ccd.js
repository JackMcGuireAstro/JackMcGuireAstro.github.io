// The CCD equation and the presets for the exposure-time calculator. Pure functions, no DOM.
//
//   S/N = S·t / sqrt( S·t + n_pix·(B_sky·t + D·t + R²) )
//
// S: source electrons per second inside the aperture; B_sky: sky electrons per second per
// pixel; D: dark current (e⁻/s/pixel); R: read noise (e⁻/pixel); n_pix: pixels in the aperture.

// ------------------------------------------------------------------ filters
// Photon flux of a magnitude-0 source, per Å, at the top of the atmosphere.
// Johnson–Cousins (Vega system): Bessell, Castelli & Plez (1998) flux densities, converted to
// photons with N = f_λ·λ/(hc); widths are the passbands' FWHM (Bessell 1990).
// SDSS (AB system): N = f_ν/(h·λ) with f_ν = 3631 Jy; λ and FWHM from Fukugita et al. (1996).
const AB_PHOTONS_PER_A_TIMES_A = 3.631e-20 / 6.62607015e-27;  // f_ν/h, photons s⁻¹ cm⁻² (divide by λ in Å)
export const FILTERS = [
  { id: "U", group: "Johnson/Bessell", system: "Vega", lambda: 3600, width: 680, perA: 756 },
  { id: "B", group: "Johnson/Bessell", system: "Vega", lambda: 4380, width: 980, perA: 1393 },
  { id: "V", group: "Johnson/Bessell", system: "Vega", lambda: 5450, width: 890, perA: 996 },
  { id: "R", group: "Johnson/Bessell", system: "Vega", lambda: 6410, width: 1380, perA: 702 },
  { id: "I", group: "Johnson/Bessell", system: "Vega", lambda: 7980, width: 1490, perA: 452 },
  ...[["u", 3557, 599], ["g", 4825, 1379], ["r", 6261, 1382], ["i", 7672, 1535], ["z", 9097, 1370]].map(([id, lambda, width]) =>
    ({ id, group: "SDSS", system: "AB", lambda, width, perA: AB_PHOTONS_PER_A_TIMES_A / lambda })),
].map((f) => ({ ...f, zeroPoint: f.perA * f.width }));  // photons s⁻¹ cm⁻² for magnitude 0 across the band

// Night-sky surface brightness at the zenith (mag/arcsec²) for a dark site: new Moon, about
// 7 days from new, and full Moon. Johnson values follow the CTIO/KPNO tables (Walker 1987);
// SDSS values are typical of Apache Point. Generic numbers: replace with measured site values.
export const SKY = {
  U: { dark: 22.0, grey: 19.9, bright: 17.0 }, B: { dark: 22.7, grey: 21.6, bright: 19.5 },
  V: { dark: 21.8, grey: 21.4, bright: 20.0 }, R: { dark: 20.9, grey: 20.6, bright: 19.9 },
  I: { dark: 19.9, grey: 19.7, bright: 19.2 },
  u: { dark: 22.1, grey: 20.5, bright: 18.0 }, g: { dark: 22.0, grey: 21.0, bright: 19.2 },
  r: { dark: 21.1, grey: 20.6, bright: 19.7 }, i: { dark: 20.3, grey: 20.0, bright: 19.4 },
  z: { dark: 19.2, grey: 19.0, bright: 18.7 },
};

// ------------------------------------------------------------------ telescopes
// PLACEHOLDERS. Only the apertures are the telescopes' real sizes. Every other number
// (obstruction, throughput, plate scale, gain, read noise, dark current, full well, seeing,
// extinction) is a typical value for this class of telescope and CCD, to be replaced with the
// values from the instrument manuals and site measurements.
export const TELESCOPES = [
  {
    id: "wiro", name: "WIRO 2.3 m (Wyoming Infrared Observatory)", placeholder: true,
    diameter: 2.3, obstruction: 0.6, plateScale: 0.5, gain: 2.0, readNoise: 5, dark: 0.002, fullWell: 100000, seeing: 1.5,
    throughput: { U: 0.12, B: 0.30, V: 0.35, R: 0.38, I: 0.25, u: 0.10, g: 0.33, r: 0.38, i: 0.30, z: 0.14 },
    extinction: { U: 0.45, B: 0.22, V: 0.13, R: 0.09, I: 0.06, u: 0.45, g: 0.17, r: 0.09, i: 0.06, z: 0.05 },
  },
  {
    id: "rbo", name: "Red Buttes Observatory 0.6 m (University of Wyoming)", placeholder: true,
    diameter: 0.6, obstruction: 0.25, plateScale: 0.6, gain: 1.5, readNoise: 9, dark: 0.05, fullWell: 80000, seeing: 2.0,
    throughput: { U: 0.08, B: 0.25, V: 0.32, R: 0.35, I: 0.22, u: 0.06, g: 0.28, r: 0.34, i: 0.27, z: 0.12 },
    extinction: { U: 0.50, B: 0.25, V: 0.15, R: 0.10, I: 0.07, u: 0.50, g: 0.19, r: 0.10, i: 0.07, z: 0.06 },
  },
];
export const ADC_MAX = 65535;

// ------------------------------------------------------------------ model
// Abramowitz & Stegun 7.1.26 (|error| < 1.5e-7).
export function erf(x) {
  const s = Math.sign(x), a = Math.abs(x), t = 1 / (1 + 0.3275911 * a);
  const y = 1 - ((((1.061405429 * t - 1.453152027) * t + 1.421413741) * t - 0.284496736) * t + 0.254829592) * t * Math.exp(-a * a);
  return s * y;
}

export const FWHM_PER_SIGMA = 2 * Math.sqrt(2 * Math.log(2));  // 2.3548

// Rates for one setup. Inputs: diameter and obstruction in metres, zeroPoint in photons
// s⁻¹ cm⁻², throughput 0–1, extinction k (mag/airmass), sky (mag/arcsec²), plateScale
// (″/pixel), seeing FWHM (″), apertureFactor (aperture radius in units of the FWHM), dark
// (e⁻/s/pixel), readNoise (e⁻). mag may be omitted (limiting-magnitude mode).
export function rates(p) {
  const area = (Math.PI / 4) * ((p.diameter * 100) ** 2 - (p.obstruction * 100) ** 2);  // cm²
  const perMag0 = p.zeroPoint * area * p.throughput;                                    // e⁻/s from a mag-0 source above the atmosphere
  const radius = p.apertureFactor * p.seeing;                                            // ″
  const sigma = p.seeing / FWHM_PER_SIGMA;
  const enclosed = 1 - Math.exp(-(radius * radius) / (2 * sigma * sigma));               // Gaussian PSF inside the aperture
  const npix = (Math.PI * radius * radius) / (p.plateScale * p.plateScale);
  const sky = perMag0 * 10 ** (-0.4 * p.sky) * p.plateScale * p.plateScale;            // e⁻/s/pixel
  const peakFraction = erf(p.plateScale / (2 * Math.SQRT2 * sigma)) ** 2;              // PSF centred on a pixel
  const out = { area, perMag0, radius, radiusPix: radius / p.plateScale, enclosed, npix, sky, dark: p.dark, readNoise: p.readNoise, peakFraction };
  if (Number.isFinite(p.mag)) {
    out.total = perMag0 * 10 ** (-0.4 * (p.mag + p.extinction * p.airmass));            // e⁻/s from the whole PSF
    out.source = out.total * enclosed;                                                   // e⁻/s inside the aperture
  }
  return out;
}

export function snrFor(source, t, r) {
  return (source * t) / Math.sqrt(source * t + r.npix * (r.sky * t + r.dark * t + r.readNoise ** 2));
}

// Exposure time for a target S/N: the positive root of
// S²t² − snr²(S + n(B+D))t − snr²·n·R² = 0.
export function timeFor(source, snr, r) {
  const a = source * source, b = -(snr ** 2) * (source + r.npix * (r.sky + r.dark)), c = -(snr ** 2) * r.npix * r.readNoise ** 2;
  return (-b + Math.sqrt(b * b - 4 * a * c)) / (2 * a);
}

// Source rate (e⁻/s in the aperture) that reaches `snr` in `t` seconds: the positive root of
// (St)² − snr²(St) − snr²·n((B+D)t + R²) = 0.
export function sourceFor(t, snr, r) {
  const q = snr * snr, St = (q + Math.sqrt(q * q + 4 * q * r.npix * ((r.sky + r.dark) * t + r.readNoise ** 2))) / 2;
  return St / t;
}

export function magnitudeFor(source, r, p) {
  return -2.5 * Math.log10(source / (r.perMag0 * r.enclosed)) - p.extinction * p.airmass;
}

// Where the variance comes from, as fractions of the total, for exposure t.
export function noiseBudget(source, t, r) {
  const parts = { source: source * t, sky: r.npix * r.sky * t, dark: r.npix * r.dark * t, read: r.npix * r.readNoise ** 2 };
  const total = parts.source + parts.sky + parts.dark + parts.read;
  const fractions = Object.fromEntries(Object.entries(parts).map(([k, v]) => [k, v / total]));
  const dominant = Object.entries(fractions).sort((a, b) => b[1] - a[1])[0][0];
  return { parts, fractions, total, dominant };
}

// Brightest pixel for exposure t: the source's central pixel plus sky and dark (no bias).
export function peakPixel(t, r, p) {
  const electrons = (r.total ?? 0) * t * r.peakFraction + (r.sky + r.dark) * t;
  return { electrons, adu: electrons / p.gain, wellFraction: electrons / p.fullWell, saturated: electrons > 0.8 * p.fullWell || electrons / p.gain > 0.8 * ADC_MAX };
}

// One full calculation for a mode: "snr" (given t), "time" (given S/N), "maglim" (given t and S/N).
export function solve(mode, p) {
  if (mode === "maglim") {
    const r0 = rates({ ...p, mag: undefined });
    const source = sourceFor(p.exptime, p.snr, r0);
    const mag = magnitudeFor(source, r0, p);
    const r = rates({ ...p, mag });
    return { mode, r, mag, t: p.exptime, snr: p.snr, source: r.source };
  }
  const r = rates(p);
  if (mode === "time") {
    const t = timeFor(r.source, p.snr, r);
    return { mode, r, mag: p.mag, t, snr: p.snr, source: r.source };
  }
  return { mode, r, mag: p.mag, t: p.exptime, snr: snrFor(r.source, p.exptime, r), source: r.source };
}

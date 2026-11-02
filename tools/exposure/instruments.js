// The instrument profiles of Jack's two telescopes, in ONE file: the exposure calculator, the CTAS
// suggested follow-up, the run sheet, the Tonight planner and the WorldsIndex transit planner all
// read these values (through ccd.js). Edit a value here and every page follows.
//
// HOW TO FILL IN A VALUE: replace the number, then move its key from `placeholderFields` into
// `measured` with a short note of where it comes from (a page, a log, a measurement). A profile is
// shown as "placeholder" on every page until `placeholderFields` is empty.
//
// Units: diameter and obstruction in metres; plateScale ″/pixel; gain e⁻/ADU; readNoise e⁻;
// dark e⁻/s/pixel; fullWell e⁻; seeing FWHM ″; throughput 0–1 per filter (telescope + camera +
// filter, top of the atmosphere); extinction mag/airmass per filter; fieldOfViewDeg [width, height]
// of the imaging field in degrees.

export const INSTRUMENT_PROFILES_SCHEMA = "site-instrument-profiles.v1";

// WIRO + DoublePrime (prime focus): measured values from the WIRO DoublePrime page
// (physics.uwyo.edu/~WIRO/DoublePrime/doubleprime.html): plate scale 0.58″/pixel; gain 2.62–2.66 e⁻/ADU
// (mean 2.64); read noise 5.2–5.4 e⁻ (5.3); dark < 0.01 ADU/s at −110 °C (< 0.026 e⁻/s, an upper limit);
// linear to at least 60,000 ADU (≈ 158,000 e⁻). Seeing: "typical seeing about 1 arcsec" (WIRO design notes,
// physics.uwyo.edu/~mpierce/WIRO/Design_Considerations.html). u g r i z throughputs and extinctions: Findlay et
// al. 2016 (PASP 128, 115003, arXiv:1604.02446) Table 3 / §4.5: zero points 24.52 / 25.31 / 24.90 / 24.37 / 23.74 mag
// for 1 ADU/s EXTRAPOLATED TO ZERO AIRMASS (one photometric night, standard stars at airmass 1.27–2.5) and extinction
// 0.47 / 0.17 / 0.09 / 0.07 / 0.06 mag/airmass. Each throughput is the value that reproduces that zero point with this
// calculator's band (λ, FWHM above), area and gain: an effective number, not a transmission curve. (The r value before
// 2026-11-02 read the zero point as being at airmass ~1 and was 12 % too high.) Johnson bands: placeholders.
// Red Buttes: all placeholders except the aperture.
export const TELESCOPES = [
  {
    id: "wiro", name: "WIRO 2.3 m (Wyoming Infrared Observatory)",
    diameter: 2.3, obstruction: 0.6, plateScale: 0.58, gain: 2.64, readNoise: 5.3, dark: 0.026, fullWell: 158000, seeing: 1.0,
    throughput: { U: 0.12, B: 0.30, V: 0.35, R: 0.38, I: 0.25, u: 0.475, g: 0.579, r: 0.514, i: 0.348, z: 0.259 },
    extinction: { U: 0.45, B: 0.22, V: 0.13, R: 0.09, I: 0.06, u: 0.47, g: 0.17, r: 0.09, i: 0.07, z: 0.06 },
    fieldOfViewDeg: [0.284, 0.284],
    measured: {
      diameter: "WIRO primary mirror, 2.3 m",
      plateScale: "DoublePrime page: 0.58″/pixel",
      gain: "DoublePrime page: 2.62–2.66 e⁻/ADU, mean 2.64",
      readNoise: "DoublePrime page: 5.2–5.4 e⁻, mean 5.3",
      dark: "DoublePrime page: < 0.01 ADU/s at −110 °C (an upper limit, × gain)",
      fullWell: "DoublePrime page: linear to at least 60,000 ADU (× gain)",
      seeing: "WIRO design notes: typical seeing about 1″",
      ...Object.fromEntries(["u", "g", "r", "i", "z"].flatMap((b) => [
        [`throughput.${b}`, `Findlay et al. 2016 Table 3: ${b}′ zero point (1 ADU/s, zero airmass), as an effective throughput for this calculator's band`],
        [`extinction.${b}`, `Findlay et al. 2016 §4.5: ${b}′ extinction measured at WIRO (one photometric night)`]])),
    },
    placeholderFields: ["obstruction", "throughput.U", "throughput.B", "throughput.V", "throughput.R", "throughput.I",
      "extinction.U", "extinction.B", "extinction.V", "extinction.R", "extinction.I", "fieldOfViewDeg"],
  },
  {
    id: "rbo", name: "Red Buttes Observatory 0.6 m (University of Wyoming)",
    diameter: 0.6, obstruction: 0.25, plateScale: 0.6, gain: 1.5, readNoise: 9, dark: 0.05, fullWell: 80000, seeing: 2.0,
    throughput: { U: 0.08, B: 0.25, V: 0.32, R: 0.35, I: 0.22, u: 0.06, g: 0.28, r: 0.34, i: 0.27, z: 0.12 },
    extinction: { U: 0.50, B: 0.25, V: 0.15, R: 0.10, I: 0.07, u: 0.50, g: 0.19, r: 0.10, i: 0.07, z: 0.06 },
    fieldOfViewDeg: [0.341, 0.341],
    measured: { diameter: "Red Buttes 0.6 m telescope" },
    placeholderFields: ["obstruction", "plateScale", "gain", "readNoise", "dark", "fullWell", "seeing", "throughput", "extinction", "fieldOfViewDeg"],
  },
].map((t) => ({ ...t, placeholder: t.placeholderFields.length > 0 }));

// Spectroscopy: the faintest magnitude at which a classification spectrum is practical in one
// night (null: no spectrograph). Placeholder thresholds until measured.
export const SPECTROSCOPY = {
  wiro: { limit_mag: 19.0, placeholder: true, text: "classification spectrum practical to about 19.0 mag" },
  rbo: { limit_mag: null, placeholder: true, text: "imaging only; no spectrograph in the profile" },
};

// True when this telescope's value at `path` ("gain", "throughput.r") is still a placeholder.
export function isPlaceholder(telescope, path) {
  const fields = (telescope && telescope.placeholderFields) || [];
  return fields.includes(path) || fields.includes(String(path).split(".")[0]);
}

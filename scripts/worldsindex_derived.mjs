#!/usr/bin/env node
// WorldsIndex derived quantities, population plots and the ephemeris staleness index
// (Framework v2, Track 3b part 1).
//
// Every number here is CALCULATED by this release from values the release already selects;
// nothing is a measurement and nothing is a default:
//   * planet inputs are the builder's selections (rule nasa-pscomppars-like.v1, the Compare
//     tab's "selection, not a measurement" values, worldsindex-conflict-sets.v1);
//   * stellar inputs (effective temperature, radius, mass, distance) come from ONE NASA
//     stellar-host parameter set chosen by the stated rule STELLAR_RULE, field by field falling
//     back to the next ranked set and then to Exoplanet.eu, with the source of every input kept;
//   * J and K magnitudes (for TSM and ESM) come from the Exoplanet.eu row of the object;
//   * an input that is a limit, missing, or outside a formula's calibration gives
//     state "not-computed" with the reason, never a substituted value.
// Uncertainties are propagated by asymmetric finite differences (each input moved by its own
// upper and lower error, contributions added in quadrature separately upward and downward);
// an input without a reported error contributes nothing and the quantity says so.
//
// Outputs (all regenerable from the release inputs; nothing enters the builder state):
//   derived/<xx>.json.gz      per-object blocks, sharded like details/ (first two hex digits of
//                             sha256(objectId)); objects with no computable input are omitted
//   derived-summary.json      the formula registry, rules, counts per quantity and state
//   populations.json          the population plot tables (confirmed planets) and axis specs
//   populations/<plot>.svg    static plots regenerated each build (the page marks the planet)
//   staleness.json            every transiting planet ranked by predicted transit-time
//                             uncertainty at +1, +6 and +12 months, with a priority class
// Command line (computes and summarises from a built release directory):
//   node scripts/worldsindex_derived.mjs worldsindex/data [--write]

import { createHash } from 'node:crypto';
import { createReadStream, readFileSync, readdirSync } from 'node:fs';
import { mkdir, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { createGunzip, gunzipSync, gzipSync } from 'node:zlib';
import { fileURLToPath } from 'node:url';
import { grayAtmosphereLimbDarkening, LIMB_DARKENING_BANDS } from '../worldsindex/assets/photometry.js';

export const DERIVED_SCHEMA_VERSION = 'worldsindex-derived-quantities.v1';
export const DERIVED_SUMMARY_SCHEMA_VERSION = 'worldsindex-derived-summary.v1';
export const POPULATIONS_SCHEMA_VERSION = 'worldsindex-populations.v1';
export const STALENESS_SCHEMA_VERSION = 'worldsindex-ephemeris-staleness.v1';
export const FORMULA_REGISTRY_VERSION = 'worldsindex-derived-formulas.v1';
export const DERIVED_SHARD_PATTERN = 'derived/{shard}.json.gz';
export const CLAIM_BOUNDARY = 'Calculated by WorldsIndex from the values this release selects (a selection, not a measurement) and one stated stellar parameter set. '
  + 'Not a fitted solution, not a self-consistent publication solution and not a completeness-corrected population. Every value names its formula, inputs and their sources.';
export const STELLAR_RULE = 'stellar-inputs.v1: the NASA stellar-host parameter set (stellarhosts) whose reference equals the reference of the selected planet period or radius; '
  + 'otherwise the set stating the most of effective temperature, radius and mass (ties by source record id). A field that set lacks is taken from the next set in the same ranking, '
  + 'then from the Exoplanet.eu row of the object. Distances follow the same chain (sy_dist, then Exoplanet.eu star_distance). Magnitudes (J, K) come only from Exoplanet.eu.';
export const SAME_PUBLICATION_RULE = 'Inclination, impact parameter and radius ratio are not selected by the selection rule; when used, they are the row of the same publication as the selected period or radius (the same source record, else a row citing the same bibcode or reference), never a row of another publication.';
export const PROPAGATION = 'asymmetric finite differences: f is re-evaluated with each input at value + upper error and value - lower error; positive and negative changes are added in quadrature separately. '
  + 'Correlations between inputs are ignored. Inputs without a reported error contribute nothing (listed in uncertaintyOmits).';
export const SKIPPED_STATUSES = new Set(['FALSE_POSITIVE', 'REFUTED']);

// IAU 2015 Resolution B2/B3 nominal values and CODATA 2018 constants.
const AU_M = 1.495978707e11;
const R_SUN_M = 6.957e8;
const R_EARTH_M = 6.3781e6;
const R_JUP_M = 7.1492e7;
const GM_SUN = 1.3271244e20;
const GM_EARTH = 3.986004e14;
const GM_JUP = 1.2668653e17;
const G = 6.6743e-11;
const PC_M = 3.0856775814913673e16;
const T_SUN = 5772;
const EARTH_DENSITY = 5.514; // g cm^-3, mean density of the Earth
const SECONDS_PER_DAY = 86400;
const R_SUN_AU = R_SUN_M / AU_M;
const R_EARTH_PER_R_SUN = R_EARTH_M / R_SUN_M;
const M_EARTH_PER_M_SUN = GM_EARTH / GM_SUN;
const M_EARTH_PER_M_JUP = GM_EARTH / GM_JUP;
const YEAR_DAYS = 365.25;

// ------------------------------------------------------------------ the formula registry
// One entry per calculation. `inputs` names the inputs the formula needs; `assumptions` are the
// fixed model parameters of an estimate (stated, never hidden); `citation` is the published source.
const cite = (text, url) => ({ text, url });
export const FORMULAS = [
  { id: 'stellar-luminosity-stefan-boltzmann.v1', code: 'stellar_luminosity', label: 'Stellar luminosity', unit: 'L_sun', inputs: ['stellar_teff', 'stellar_radius'],
    formula: 'L/L_sun = (R*/R_sun)^2 (Teff / 5772 K)^4', citation: cite('IAU 2015 Resolution B3 (nominal solar effective temperature 5772 K)', 'https://arxiv.org/abs/1510.07674') },
  { id: 'insolation-luminosity.v1', code: 'insolation', label: 'Insolation', unit: 'S_earth', inputs: ['stellar_teff', 'stellar_radius', 'semimajor_axis'],
    formula: 'S/S_earth = (L/L_sun) / (a/au)^2', citation: cite('Inverse-square law with the stellar luminosity above', null), archiveProperty: 'insolation' },
  { id: 'teq-zero-albedo-full-redistribution.v1', code: 'equilibrium_temperature', label: 'Equilibrium temperature (zero albedo, full redistribution)', unit: 'K', inputs: ['stellar_teff', 'stellar_radius', 'semimajor_axis'],
    formula: 'Teq = Teff sqrt(R* / (2a))', citation: cite('Kempton et al. 2018, PASP 130, 114401, eq. 3', 'https://doi.org/10.1088/1538-3873/aadf6f'), archiveProperty: 'equilibrium_temperature' },
  { id: 'bulk-density.v1', code: 'density', label: 'Bulk density', unit: 'g/cm3', inputs: ['planet_mass', 'planet_radius'],
    formula: 'rho = 5.514 g/cm3 (M/M_earth) / (R/R_earth)^3', citation: cite('Mean density of the Earth 5.514 g/cm3 (IAU 2015 nominal radius and mass)', null) },
  { id: 'angular-separation.v1', code: 'angular_separation', label: 'Maximum angular separation', unit: 'mas', inputs: ['semimajor_axis', 'distance'],
    formula: 'theta = 1000 (a/au) / (d/pc) mas (semi-major axis; the apastron separation is larger for an eccentric orbit)', citation: cite('Definition of the parsec', null) },
  { id: 'chen-kipping-2017-mass-radius.v1', code: 'radius_from_mass', label: 'Radius estimated from mass (Chen & Kipping 2017)', unit: 'R_earth', inputs: ['planet_mass'],
    formula: 'R = C M^S, piecewise: Terran M < 2.04 M_earth (C 1.008, S 0.279); Neptunian < 131.6 M_earth (S 0.589); Jovian < 0.08 M_sun (S -0.044); stellar (S 0.881); C continuous',
    citation: cite('Chen & Kipping 2017, ApJ 834, 17 (mean relation, Table 1)', 'https://doi.org/10.3847/1538-4357/834/1/17') },
  { id: 'chen-kipping-2017-radius-mass.v1', code: 'mass_from_radius', label: 'Mass estimated from radius (Chen & Kipping 2017)', unit: 'M_earth', inputs: ['planet_radius'],
    formula: 'inverse of the Terran and Neptunian branches; not computed above 14.3 R_earth, where the Jovian branch is degenerate',
    citation: cite('Chen & Kipping 2017, ApJ 834, 17', 'https://doi.org/10.3847/1538-4357/834/1/17') },
  { id: 'kopparapu-2014-habitable-zone.v1', code: 'habitable_zone', label: 'Habitable-zone edges (Kopparapu et al. 2014)', unit: 'au', inputs: ['stellar_teff', 'stellar_radius'],
    formula: 'Seff = S0 + a T + b T^2 + c T^3 + d T^4, T = Teff - 5780 K, d = sqrt((L/L_sun)/Seff); conservative: runaway greenhouse (1 M_earth) to maximum greenhouse; optimistic: recent Venus to early Mars; valid 2600-7200 K',
    citation: cite('Kopparapu et al. 2014, ApJL 787, L29 (Table 1)', 'https://doi.org/10.1088/2041-8205/787/2/L29') },
  { id: 'habitable-zone-orbit-fraction.v1', code: 'habitable_zone_orbit_fraction', label: 'Fraction of the orbit inside the habitable zone', unit: 'fraction', inputs: ['stellar_teff', 'stellar_radius', 'semimajor_axis', 'eccentricity'],
    formula: 'fraction of orbital time with r = a(1 - e cos E) between the zone edges, 7200 steps in mean anomaly', citation: cite('Kepler\'s equation over one orbit; edges from Kopparapu et al. 2014', null) },
  { id: 'earth-similarity-index-flux.v1', code: 'earth_similarity_index', label: 'Earth Similarity Index (radius and stellar flux)', unit: 'dimensionless', inputs: ['planet_radius', 'stellar_teff', 'stellar_radius', 'semimajor_axis'],
    formula: 'ESI = 1 - sqrt(0.5 [((S - 1)/(S + 1))^2 + ((R - 1)/(R + 1))^2]), S and R relative to Earth',
    citation: cite('Schulze-Makuch et al. 2011, Astrobiology 11, 1041 (flux form of the Planetary Habitability Laboratory)', 'https://doi.org/10.1089/ast.2010.0592') },
  { id: 'snow-line-hayashi-1981.v1', code: 'snow_line', label: 'Snow line (minimum-mass solar nebula scaling)', unit: 'au', inputs: ['stellar_teff', 'stellar_radius'],
    formula: 'a_snow = 2.7 au (L/L_sun)^(1/2)', citation: cite('Hayashi 1981, PThPS 70, 35', 'https://doi.org/10.1143/PTPS.70.35') },
  { id: 'tidal-locking-gladman-1996.v1', code: 'tidal_locking_timescale', label: 'Tidal locking timescale (order-of-magnitude estimate)', unit: 'yr', inputs: ['planet_mass', 'planet_radius', 'semimajor_axis', 'stellar_mass'],
    formula: 't = omega a^6 I Q / (3 G M*^2 k2 R^5), I = 0.4 M R^2', assumptions: { initialSpinPeriodHours: 12, Q: 100, k2: 0.3, momentOfInertiaFactor: 0.4 },
    citation: cite('Gladman et al. 1996, Icarus 122, 166, eq. 9 (assumed initial spin, Q and k2 stated in `assumptions`)', 'https://doi.org/10.1006/icar.1996.0117') },
  { id: 'rv-semi-amplitude.v1', code: 'rv_semi_amplitude', label: 'Radial-velocity semi-amplitude', unit: 'm/s', inputs: ['planet_mass', 'orbital_period', 'stellar_mass', 'eccentricity'],
    formula: 'K = 28.4329 m/s (M_p sin i / M_jup) ((M* + M_p)/M_sun)^(-2/3) (P/yr)^(-1/3) / sqrt(1 - e^2)', citation: cite('Lovis & Fischer 2010, in Exoplanets (Seager ed.), eq. 14', null) },
  { id: 'astrometric-amplitude.v1', code: 'astrometric_amplitude', label: 'Astrometric signature', unit: 'uas', inputs: ['planet_mass', 'semimajor_axis', 'stellar_mass', 'distance'],
    formula: 'alpha = (M_p / M*) (a/au) / (d/pc) arcsec', citation: cite('Perryman 2011, The Exoplanet Handbook, eq. 3.4', null) },
  { id: 'transit-depth-geometric.v1', code: 'transit_depth', label: 'Transit depth (geometric)', unit: 'ppm', inputs: ['radius_ratio'],
    formula: 'delta = (Rp/R*)^2 (no limb darkening)', citation: cite('Winn 2010, in Exoplanets (Seager ed.), eq. 1', 'https://arxiv.org/abs/1001.2010'), archiveProperty: 'transit_depth' },
  { id: 'transit-duration-winn-2010.v1', code: 'transit_duration', label: 'Transit duration T14', unit: 'hour', inputs: ['orbital_period', 'scaled_semimajor_axis', 'radius_ratio', 'impact_parameter', 'eccentricity'],
    formula: 'T14 = (P/pi) asin[(R*/a) sqrt((1 + k)^2 - b^2) / sin i] (circular orbits; eccentric orbits need the argument of periastron)', citation: cite('Winn 2010, eq. 14', 'https://arxiv.org/abs/1001.2010'), archiveProperty: 'transit_duration' },
  { id: 'transit-probability-winn-2010.v1', code: 'transit_probability', label: 'Geometric transit probability', unit: 'fraction', inputs: ['stellar_radius', 'planet_radius', 'semimajor_axis', 'eccentricity'],
    formula: 'p = ((R* + Rp)/a) / (1 - e^2) (averaged over the argument of periastron)', citation: cite('Winn 2010, eq. 9', 'https://arxiv.org/abs/1001.2010') },
  { id: 'hill-radius.v1', code: 'hill_radius', label: 'Hill radius', unit: 'au', inputs: ['semimajor_axis', 'eccentricity', 'planet_mass', 'stellar_mass'],
    formula: 'r_H = a (1 - e) (M_p / (3 M*))^(1/3) (at periastron)', citation: cite('Hamilton & Burns 1992, Icarus 96, 43', 'https://doi.org/10.1016/0019-1035(92)90005-R') },
  { id: 'roche-limit-fluid.v1', code: 'roche_limit', label: 'Roche limit (fluid planet)', unit: 'au', inputs: ['stellar_radius', 'stellar_mass', 'planet_mass', 'planet_radius'],
    formula: 'a_R = 2.44 R* (rho* / rho_p)^(1/3); a / a_R is given beside it', citation: cite('Chandrasekhar 1969, Ellipsoidal Figures of Equilibrium', null) },
  { id: 'planet-class-borucki-2011.v1', code: 'planet_class', label: 'Size or mass class', unit: 'class', inputs: ['planet_radius'],
    formula: 'by radius (Borucki et al. 2011): Earth-size < 1.25, super-Earth 1.25-2, Neptune-size 2-6, Jupiter-size 6-15, very large 15-22.4 R_earth; without a radius, by mass (Chen & Kipping 2017): Terran < 2.04, Neptunian < 131.6, Jovian < 26,600 M_earth',
    citation: cite('Borucki et al. 2011, ApJ 736, 19; Chen & Kipping 2017, ApJ 834, 17', 'https://doi.org/10.1088/0004-637X/736/1/19') },
  { id: 'tsm-kempton-2018.v1', code: 'tsm', label: 'Transmission spectroscopy metric (TSM)', unit: 'dimensionless', inputs: ['planet_radius', 'planet_mass', 'stellar_radius', 'stellar_teff', 'semimajor_axis', 'j_mag'],
    formula: 'TSM = s Rp^3 Teq / (Mp R*^2) 10^(-mJ/5); s = 0.190 (Rp < 1.5), 1.26 (1.5-2.75), 1.28 (2.75-4), 1.15 (4-10 R_earth); mass from Chen & Kipping 2017 when none is selected, as in the paper',
    citation: cite('Kempton et al. 2018, PASP 130, 114401, eq. 1 and Table 1', 'https://doi.org/10.1088/1538-3873/aadf6f') },
  { id: 'esm-kempton-2018.v1', code: 'esm', label: 'Emission spectroscopy metric (ESM)', unit: 'dimensionless', inputs: ['radius_ratio', 'stellar_teff', 'stellar_radius', 'semimajor_axis', 'k_mag'],
    formula: 'ESM = 4.29e6 B(7.5 um, 1.10 Teq) / B(7.5 um, Teff) (Rp/R*)^2 10^(-mK/5)', citation: cite('Kempton et al. 2018, PASP 130, 114401, eq. 4', 'https://doi.org/10.1088/1538-3873/aadf6f') },
  { id: 'limb-darkening-gray-eddington.v1', code: 'limb_darkening', label: 'Quadratic limb-darkening coefficients (gray Eddington atmosphere)', unit: 'dimensionless', inputs: ['stellar_teff'],
    formula: 'I(mu) = B_lambda(T(tau = mu)), T^4 = (3/4) Teff^4 (tau + 2/3); quadratic law fitted by least squares on 101 mu points at each band\'s effective wavelength',
    citation: cite('Eddington approximation of a gray atmosphere (Mihalas 1978, Stellar Atmospheres, ch. 3); quadratic law of Kopal 1950', null) },
];
export const FORMULA_BY_CODE = new Map(FORMULAS.map((f) => [f.code, f]));

// Kopparapu et al. 2014 Table 1: recent Venus, runaway greenhouse (1 M_earth), maximum greenhouse, early Mars.
const HZ = {
  recentVenus: [1.776, 2.136e-4, 2.533e-8, -1.332e-11, -3.097e-15],
  runawayGreenhouse: [1.107, 1.332e-4, 1.580e-8, -8.308e-12, -1.931e-15],
  maximumGreenhouse: [0.356, 6.171e-5, 1.698e-9, -3.198e-12, -5.575e-16],
  earlyMars: [0.320, 5.547e-5, 1.526e-9, -2.874e-12, -5.011e-16],
};
const TSM_SCALE = [[1.5, 0.190], [2.75, 1.26], [4.0, 1.28], [10.0, 1.15]];
const CK = { terranMax: 2.04, neptunianMax: 131.6, jovianMax: 0.08 / M_EARTH_PER_M_SUN, terran: [1.008, 0.279], neptunianS: 0.589, jovianS: -0.044, stellarS: 0.881 };
CK.neptunianC = CK.terran[0] * CK.terranMax ** (CK.terran[1] - CK.neptunianS);
CK.jovianC = CK.neptunianC * CK.neptunianMax ** (CK.neptunianS - CK.jovianS);
CK.stellarC = CK.jovianC * CK.jovianMax ** (CK.jovianS - CK.stellarS);
CK.neptunianRadiusMax = CK.neptunianC * CK.neptunianMax ** CK.neptunianS;

// ------------------------------------------------------------------ inputs
const num = (value) => {
  if (value === null || value === undefined || value === '') return null;
  const n = typeof value === 'number' ? value : Number(String(value).trim());
  return Number.isFinite(n) ? n : null;
};
const positiveErr = (value) => { const n = num(value); return n === null ? null : Math.abs(n); };
const round = (value, digits = 6) => (value === null || value === undefined || !Number.isFinite(value) ? null : Number(value.toPrecision(digits)));

// Selected planet properties: worldsindex-conflict-sets.v1 property code -> input name.
const PLANET_PROPERTIES = {
  orbital_period: 'orbital_period', planet_radius: 'planet_radius', planet_mass: 'planet_mass', semimajor_axis: 'semimajor_axis',
  eccentricity: 'eccentricity', inclination: 'inclination', impact_parameter: 'impact_parameter', planet_star_radius_ratio: 'radius_ratio_selected',
  equilibrium_temperature: 'archive_equilibrium_temperature', insolation: 'archive_insolation', transit_depth: 'archive_transit_depth', transit_duration: 'archive_transit_duration',
};
const MINIMUM_MASS = /minimum|sin\s*i|msini/i;
const SAME_PUBLICATION = new Set(['inclination', 'impact_parameter', 'planet_star_radius_ratio']);

const referenceKey = (product, record) => {
  const index = record?.reference;
  const ref = Number.isInteger(index) ? product.references?.[index] : null;
  return ref ? (ref.bibcode || ref.label || null) : null;
};
/**
 * The row of `block` from the same publication as the selected period or radius: first a row of
 * the same source record, else a row whose reference (bibcode, else label) is the same publication.
 */
export function samePublicationInput(block, product, sourceRecordIds) {
  const records = product.records ?? [];
  const ids = new Set(sourceRecordIds);
  const references = new Set(records.filter((r) => ids.has(r.sourceRecordId)).map((r) => referenceKey(product, r)).filter(Boolean));
  const rows = block.rows ?? [];
  const pick = rows.find((r) => ids.has(records[r.record]?.sourceRecordId)) ?? rows.find((r) => references.has(referenceKey(product, records[r.record])));
  if (!pick) return null;
  const value = num(pick.normalized?.value ?? pick.original?.value);
  if (value === null) return null;
  const limit = String(pick.limit ?? 'NONE').toUpperCase();
  const record = records[pick.record];
  return { value, errPlus: positiveErr(pick.normalized?.errPlus ?? pick.original?.errPlus), errMinus: positiveErr(pick.normalized?.errMinus ?? pick.original?.errMinus), unit: block.unit ?? null,
    limit: limit === '' ? 'NONE' : limit, definition: pick.definition ?? null, basis: 'same-publication', calculated: false, sourceRecordIds: record ? [record.sourceRecordId] : [], selectionId: null,
    samePublication: true, sourceId: record?.sourceId ?? null, reference: referenceKey(product, record) };
}

/** The selected value of one property block, or a reason it cannot be used. */
export function selectedInput(block) {
  const selection = (block.selections ?? []).find((s) => s.selectionState === 'SELECTED' && s.purpose === 'DISPLAY_DEFAULT') ?? (block.selections ?? []).find((s) => s.selectionState === 'SELECTED');
  if (!selection) return null;
  const value = num(selection.value);
  if (value === null) return null;
  const limit = String(selection.limitType ?? 'NONE').toUpperCase();
  return {
    value, errPlus: positiveErr(selection.errPlus), errMinus: positiveErr(selection.errMinus), unit: selection.unit ?? block.unit ?? null,
    limit: limit === '' ? 'NONE' : limit, definition: selection.definitionCode ?? null, basis: selection.basis ?? null,
    calculated: Boolean(selection.calculated), sourceRecordIds: selection.chosenSourceRecordIds ?? [], selectionId: selection.id ?? null,
  };
}

const referenceLabel = (record) => (record?.reference && typeof record.reference === 'object' ? record.reference.label : typeof record?.reference === 'string' ? record.reference : null) ?? null;

/** Stellar inputs under STELLAR_RULE: {name: input} plus the ordered parameter sets considered. */
export function stellarInputs(records, planetSourceRecordIds, euRow) {
  const byId = new Map(records.map((r) => [r.sourceRecordId, r]));
  const planetReferences = new Set(planetSourceRecordIds.map((id) => referenceLabel(byId.get(id))).filter(Boolean));
  const sets = records.filter((r) => r.sourceId === 'nasa-stellar-hosts' && r.values);
  const filled = (r) => ['st_teff', 'st_rad', 'st_mass'].filter((k) => num(r.values[k]) !== null).length;
  const ranked = sets.slice().sort((a, b) => {
    const am = planetReferences.has(referenceLabel(a)) ? 1 : 0, bm = planetReferences.has(referenceLabel(b)) ? 1 : 0;
    if (am !== bm) return bm - am;
    if (filled(a) !== filled(b)) return filled(b) - filled(a);
    return String(a.sourceRecordId) < String(b.sourceRecordId) ? -1 : 1;
  });
  const out = {};
  const take = (name, key, unit, euKey, euUnit, euScale = 1) => {
    for (const r of ranked) {
      const value = num(r.values[key]);
      if (value === null || value <= 0) continue;
      out[name] = { value, errPlus: positiveErr(r.values[`${key}err1`]), errMinus: positiveErr(r.values[`${key}err2`]), unit, limit: 'NONE',
        sourceId: r.sourceId, sourceRecordIds: [r.sourceRecordId], reference: referenceLabel(r), matchedPlanetReference: planetReferences.has(referenceLabel(r)) };
      return;
    }
    const eu = euRow ? num(euRow.values?.[euKey]) : null;
    if (eu !== null && eu > 0) {
      out[name] = { value: eu * euScale, errPlus: positiveErr(euRow.values?.[`${euKey}_error_max`]), errMinus: positiveErr(euRow.values?.[`${euKey}_error_min`]),
        unit: euUnit, limit: 'NONE', sourceId: 'exoplanet-eu', sourceRecordIds: [euRow.sourceRecordId], reference: null, matchedPlanetReference: false };
    }
  };
  take('stellar_teff', 'st_teff', 'K', 'star_teff', 'K');
  take('stellar_radius', 'st_rad', 'R_sun', 'star_radius', 'R_sun');
  take('stellar_mass', 'st_mass', 'M_sun', 'star_mass', 'M_sun');
  take('distance', 'sy_dist', 'pc', 'star_distance', 'pc');
  return { inputs: out, setsConsidered: ranked.length };
}

// ------------------------------------------------------------------ propagation
function evaluate(fn, inputs, names) {
  const values = Object.fromEntries(names.map((n) => [n, inputs[n].value]));
  const central = fn(values);
  if (central === null || !Number.isFinite(central)) return null;
  let up = 0, down = 0;
  const omits = [];
  for (const name of names) {
    const input = inputs[name];
    if (input.errPlus === null && input.errMinus === null) { omits.push(name); continue; }
    for (const delta of [input.errPlus, input.errMinus === null ? null : -input.errMinus]) {
      if (delta === null || delta === 0) continue;
      const shifted = fn({ ...values, [name]: values[name] + delta });
      if (shifted === null || !Number.isFinite(shifted)) continue;
      const change = shifted - central;
      if (change > 0) up += change * change; else down += change * change;
    }
  }
  return { value: central, errPlus: Math.sqrt(up), errMinus: Math.sqrt(down), omits };
}

const reasonMissing = (name) => ({ code: 'input-missing', input: name, text: `${INPUT_LABEL[name] ?? name} is not available` });
const reasonLimit = (name, limit) => ({ code: 'input-is-limit', input: name, text: `${INPUT_LABEL[name] ?? name} is ${limit === 'UPPER_LIMIT' || limit === 'UPPER' ? 'an upper limit' : limit === 'LOWER_LIMIT' || limit === 'LOWER' ? 'a lower limit' : `a limit (${limit})`}` });
const INPUT_LABEL = {
  orbital_period: 'The orbital period', planet_radius: 'The planet radius', planet_mass: 'The planet mass', semimajor_axis: 'The semi-major axis', eccentricity: 'The eccentricity',
  inclination: 'The inclination', impact_parameter: 'The impact parameter', radius_ratio: 'The radius ratio', scaled_semimajor_axis: 'a/R*', stellar_teff: 'The stellar effective temperature',
  stellar_radius: 'The stellar radius', stellar_mass: 'The stellar mass', distance: 'The distance', j_mag: 'The J magnitude', k_mag: 'The K magnitude',
};

function requireInputs(inputs, names) {
  for (const name of names) {
    const input = inputs[name];
    if (!input) return reasonMissing(name);
    if (input.limit && input.limit !== 'NONE') return reasonLimit(name, input.limit);
  }
  return null;
}

function result(code, computation, extra = {}) {
  const formula = FORMULA_BY_CODE.get(code);
  const out = { code, formulaId: formula.id, state: 'calculated', value: round(computation.value), errPlus: round(computation.errPlus, 3), errMinus: round(computation.errMinus, 3), ...extra };
  if (computation.omits?.length) out.uncertaintyOmits = computation.omits;
  if (out.errPlus === 0 && out.errMinus === 0 && computation.omits?.length) { delete out.errPlus; delete out.errMinus; }
  return out;
}
const notComputed = (code, reason) => ({ code, formulaId: FORMULA_BY_CODE.get(code).id, state: 'not-computed', reason });

// ------------------------------------------------------------------ physics
const luminosity = (v) => (v.stellar_radius > 0 && v.stellar_teff > 0 ? v.stellar_radius ** 2 * (v.stellar_teff / T_SUN) ** 4 : null);
const insolation = (v) => { const l = luminosity(v); return l !== null && v.semimajor_axis > 0 ? l / v.semimajor_axis ** 2 : null; };
const teq = (v) => (v.stellar_teff > 0 && v.stellar_radius > 0 && v.semimajor_axis > 0 ? v.stellar_teff * Math.sqrt((v.stellar_radius * R_SUN_AU) / (2 * v.semimajor_axis)) : null);
export function chenKippingRadius(massEarth) {
  if (!(massEarth > 0)) return null;
  if (massEarth < CK.terranMax) return CK.terran[0] * massEarth ** CK.terran[1];
  if (massEarth < CK.neptunianMax) return CK.neptunianC * massEarth ** CK.neptunianS;
  if (massEarth < CK.jovianMax) return CK.jovianC * massEarth ** CK.jovianS;
  return CK.stellarC * massEarth ** CK.stellarS;
}
export function chenKippingMass(radiusEarth) {
  if (!(radiusEarth > 0) || radiusEarth >= CK.neptunianRadiusMax) return null;
  const terranRadiusMax = CK.terran[0] * CK.terranMax ** CK.terran[1];
  if (radiusEarth < terranRadiusMax) return (radiusEarth / CK.terran[0]) ** (1 / CK.terran[1]);
  return (radiusEarth / CK.neptunianC) ** (1 / CK.neptunianS);
}
export function habitableZone(teffK, luminositySun) {
  if (!(teffK >= 2600 && teffK <= 7200) || !(luminositySun > 0)) return null;
  const t = teffK - 5780;
  const edge = ([s0, a, b, c, d]) => Math.sqrt(luminositySun / (s0 + a * t + b * t ** 2 + c * t ** 3 + d * t ** 4));
  return { optimisticInner: edge(HZ.recentVenus), conservativeInner: edge(HZ.runawayGreenhouse), conservativeOuter: edge(HZ.maximumGreenhouse), optimisticOuter: edge(HZ.earlyMars) };
}
export function orbitFractionBetween(a, e, inner, outer, steps = 7200) {
  if (!(a > 0) || !(e >= 0 && e < 1) || !(outer > inner)) return null;
  let inside = 0;
  for (let i = 0; i < steps; i += 1) {
    const mean = (2 * Math.PI * (i + 0.5)) / steps;
    let E = e < 0.8 ? mean : Math.PI;
    for (let k = 0; k < 50; k += 1) { const step = (E - e * Math.sin(E) - mean) / (1 - e * Math.cos(E)); E -= step; if (Math.abs(step) < 1e-12) break; }
    const r = a * (1 - e * Math.cos(E));
    if (r >= inner && r <= outer) inside += 1;
  }
  return inside / steps;
}
const planck = (wavelengthM, temperatureK) => 1 / (wavelengthM ** 5 * (Math.exp(0.0143877688 / (wavelengthM * temperatureK)) - 1));
export function radiusClass(radiusEarth) {
  if (!(radiusEarth > 0)) return null;
  if (radiusEarth < 1.25) return 'Earth-size';
  if (radiusEarth < 2) return 'super-Earth';
  if (radiusEarth < 6) return 'Neptune-size';
  if (radiusEarth < 15) return 'Jupiter-size';
  if (radiusEarth <= 22.4) return 'very large (15-22.4 R_earth)';
  return 'larger than the Borucki et al. 2011 classes';
}
export function massClass(massEarth) {
  if (!(massEarth > 0)) return null;
  if (massEarth < CK.terranMax) return 'Terran';
  if (massEarth < CK.neptunianMax) return 'Neptunian';
  if (massEarth < CK.jovianMax) return 'Jovian';
  return 'stellar-mass';
}

// ------------------------------------------------------------------ one object
/**
 * Derived quantities of one object. `product` is its worldsindex-conflict-sets.v1 product,
 * `detail` its details/ entry ({records}), `euMags` the {j, k} magnitudes of its Exoplanet.eu
 * row (or null). Returns null when nothing about the object can be used.
 */
export function deriveObject(product, detail, euMags = null) {
  const records = detail?.records ?? [];
  const planet = {};
  for (const block of product?.properties ?? []) {
    const name = PLANET_PROPERTIES[block.code];
    if (!name) continue;
    const input = selectedInput(block);
    if (input) planet[name] = input;
  }
  if (!Object.keys(planet).length) return null;
  const planetIds = [...(planet.orbital_period?.sourceRecordIds ?? []), ...(planet.planet_radius?.sourceRecordIds ?? [])];
  // Transit geometry the selection rule does not select (inclination, impact parameter, radius
  // ratio): the row of the SAME publication as the selected period or radius, never another one.
  for (const block of product?.properties ?? []) {
    const name = PLANET_PROPERTIES[block.code];
    if (!SAME_PUBLICATION.has(block.code) || planet[name]) continue;
    const input = samePublicationInput(block, product, planetIds);
    if (input) planet[name] = input;
  }
  const euRow = records.find((r) => r.sourceId === 'exoplanet-eu') ?? null;
  const { inputs: stellar, setsConsidered } = stellarInputs(records, planetIds, euRow);
  const inputs = { ...planet, ...stellar };
  if (planet.radius_ratio_selected) inputs.radius_ratio = { ...planet.radius_ratio_selected };
  if (euMags?.j !== null && euMags?.j !== undefined) inputs.j_mag = { value: euMags.j, errPlus: null, errMinus: null, unit: 'mag', limit: 'NONE', sourceId: 'exoplanet-eu', sourceRecordIds: euRow ? [euRow.sourceRecordId] : [] };
  if (euMags?.k !== null && euMags?.k !== undefined) inputs.k_mag = { value: euMags.k, errPlus: null, errMinus: null, unit: 'mag', limit: 'NONE', sourceId: 'exoplanet-eu', sourceRecordIds: euRow ? [euRow.sourceRecordId] : [] };
  const massIsMinimum = planet.planet_mass ? MINIMUM_MASS.test(planet.planet_mass.definition ?? '') : false;
  const quantities = [];
  const add = (code, names, fn, extra = {}) => {
    const missing = requireInputs(inputs, names);
    if (missing) { quantities.push(notComputed(code, missing)); return null; }
    const computation = evaluate(fn, inputs, names);
    if (!computation) { quantities.push(notComputed(code, { code: 'outside-domain', text: 'The inputs fall outside the formula\'s domain' })); return null; }
    const row = result(code, computation, extra);
    quantities.push(row);
    return row;
  };
  const archive = (property) => {
    const input = planet[`archive_${property}`];
    return input && !input.calculated && input.basis !== 'calculated' ? { value: round(input.value), unit: input.unit, sourceRecordIds: input.sourceRecordIds } : null;
  };

  add('stellar_luminosity', ['stellar_teff', 'stellar_radius'], luminosity);
  add('insolation', ['stellar_teff', 'stellar_radius', 'semimajor_axis'], insolation, archive('insolation') ? { archive: archive('insolation') } : {});
  const teqRow = add('equilibrium_temperature', ['stellar_teff', 'stellar_radius', 'semimajor_axis'], teq, archive('equilibrium_temperature') ? { archive: archive('equilibrium_temperature') } : {});
  if (massIsMinimum) quantities.push(notComputed('density', { code: 'minimum-mass', text: 'The selected mass is a minimum mass (M sin i)' }));
  else add('density', ['planet_mass', 'planet_radius'], (v) => (v.planet_radius > 0 && v.planet_mass > 0 ? (EARTH_DENSITY * v.planet_mass) / v.planet_radius ** 3 : null));
  add('angular_separation', ['semimajor_axis', 'distance'], (v) => (v.distance > 0 ? (1000 * v.semimajor_axis) / v.distance : null));
  if (inputs.planet_radius) quantities.push(notComputed('radius_from_mass', { code: 'measured-radius-selected', text: 'A radius is selected for this planet; no estimate is needed' }));
  else if (massIsMinimum) quantities.push(notComputed('radius_from_mass', { code: 'minimum-mass', text: 'The selected mass is a minimum mass (M sin i)' }));
  else add('radius_from_mass', ['planet_mass'], (v) => chenKippingRadius(v.planet_mass), { label: 'estimated' });
  if (inputs.planet_mass) quantities.push(notComputed('mass_from_radius', { code: 'measured-mass-selected', text: 'A mass is selected for this planet; no estimate is needed' }));
  else if (inputs.planet_radius && inputs.planet_radius.limit === 'NONE' && inputs.planet_radius.value >= CK.neptunianRadiusMax) quantities.push(notComputed('mass_from_radius', { code: 'degenerate-branch', text: 'Above 14.3 R_earth the Chen & Kipping relation is nearly flat, so a radius does not constrain the mass' }));
  else add('mass_from_radius', ['planet_radius'], (v) => chenKippingMass(v.planet_radius), { label: 'estimated' });

  // Habitable zone: edges, whether a falls inside, and the orbital fraction (needs e).
  const hzMissing = requireInputs(inputs, ['stellar_teff', 'stellar_radius']);
  if (hzMissing) { quantities.push(notComputed('habitable_zone', hzMissing)); quantities.push(notComputed('habitable_zone_orbit_fraction', hzMissing)); }
  else {
    const l = luminosity({ stellar_teff: inputs.stellar_teff.value, stellar_radius: inputs.stellar_radius.value });
    const zone = habitableZone(inputs.stellar_teff.value, l);
    if (!zone) {
      const reason = { code: 'outside-calibration', text: `Kopparapu et al. 2014 covers 2600-7200 K; this star is ${Math.round(inputs.stellar_teff.value)} K` };
      quantities.push(notComputed('habitable_zone', reason));
      quantities.push(notComputed('habitable_zone_orbit_fraction', reason));
    } else {
      const edges = Object.fromEntries(Object.entries(zone).map(([k, v]) => [k, round(v, 4)]));
      const a = inputs.semimajor_axis && inputs.semimajor_axis.limit === 'NONE' ? inputs.semimajor_axis.value : null;
      const zoneRow = { code: 'habitable_zone', formulaId: FORMULA_BY_CODE.get('habitable_zone').id, state: 'calculated', edges };
      if (a !== null) zoneRow.semimajorAxisIn = a >= zone.conservativeInner && a <= zone.conservativeOuter ? 'conservative' : a >= zone.optimisticInner && a <= zone.optimisticOuter ? 'optimistic' : a < zone.optimisticInner ? 'inside the inner edge' : 'beyond the outer edge';
      quantities.push(zoneRow);
      const fractionMissing = requireInputs(inputs, ['semimajor_axis', 'eccentricity']);
      if (fractionMissing) quantities.push(notComputed('habitable_zone_orbit_fraction', fractionMissing));
      else {
        const e = inputs.eccentricity.value;
        quantities.push({ code: 'habitable_zone_orbit_fraction', formulaId: FORMULA_BY_CODE.get('habitable_zone_orbit_fraction').id, state: 'calculated',
          conservative: round(orbitFractionBetween(a, e, zone.conservativeInner, zone.conservativeOuter), 4), optimistic: round(orbitFractionBetween(a, e, zone.optimisticInner, zone.optimisticOuter), 4) });
      }
    }
  }
  add('earth_similarity_index', ['planet_radius', 'stellar_teff', 'stellar_radius', 'semimajor_axis'], (v) => {
    const s = insolation(v);
    if (s === null || !(v.planet_radius > 0)) return null;
    return 1 - Math.sqrt(0.5 * (((s - 1) / (s + 1)) ** 2 + ((v.planet_radius - 1) / (v.planet_radius + 1)) ** 2));
  });
  add('snow_line', ['stellar_teff', 'stellar_radius'], (v) => { const l = luminosity(v); return l === null ? null : 2.7 * Math.sqrt(l); });
  if (massIsMinimum) quantities.push(notComputed('tidal_locking_timescale', { code: 'minimum-mass', text: 'The selected mass is a minimum mass (M sin i)' }));
  else {
    const f = FORMULA_BY_CODE.get('tidal_locking_timescale').assumptions;
    add('tidal_locking_timescale', ['planet_mass', 'planet_radius', 'semimajor_axis', 'stellar_mass'], (v) => {
      if (!(v.planet_mass > 0 && v.planet_radius > 0 && v.semimajor_axis > 0 && v.stellar_mass > 0)) return null;
      const m = (v.planet_mass * GM_EARTH) / G, r = v.planet_radius * R_EARTH_M, a = v.semimajor_axis * AU_M, mStar = (v.stellar_mass * GM_SUN) / G;
      const omega = (2 * Math.PI) / (f.initialSpinPeriodHours * 3600);
      const seconds = (omega * a ** 6 * f.momentOfInertiaFactor * m * r ** 2 * f.Q) / (3 * G * mStar ** 2 * f.k2 * r ** 5);
      return seconds / (YEAR_DAYS * SECONDS_PER_DAY);
    }, { label: 'estimate', assumptions: f });
  }
  // RV semi-amplitude: the selected mass as stated (M sin i when the source gives a minimum mass);
  // a true mass is projected with the selected inclination, or labelled an upper bound without one.
  const rvMissing = requireInputs(inputs, ['planet_mass', 'orbital_period', 'stellar_mass', 'eccentricity']);
  if (rvMissing) quantities.push(notComputed('rv_semi_amplitude', rvMissing));
  else {
    const useInclination = !massIsMinimum && inputs.inclination && inputs.inclination.limit === 'NONE';
    const names = ['planet_mass', 'orbital_period', 'stellar_mass', 'eccentricity', ...(useInclination ? ['inclination'] : [])];
    const computation = evaluate((v) => {
      if (!(v.planet_mass > 0 && v.orbital_period > 0 && v.stellar_mass > 0) || !(v.eccentricity >= 0 && v.eccentricity < 1)) return null;
      const sini = useInclination ? Math.sin((v.inclination * Math.PI) / 180) : 1;
      return (28.4329 * (v.planet_mass * sini * M_EARTH_PER_M_JUP) * (v.stellar_mass + v.planet_mass * M_EARTH_PER_M_SUN) ** (-2 / 3) * (v.orbital_period / YEAR_DAYS) ** (-1 / 3)) / Math.sqrt(1 - v.eccentricity ** 2);
    }, inputs, names);
    if (!computation) quantities.push(notComputed('rv_semi_amplitude', { code: 'outside-domain', text: 'The inputs fall outside the formula\'s domain' }));
    else quantities.push(result('rv_semi_amplitude', computation, { massBasis: massIsMinimum ? 'minimum mass (M sin i) as selected' : useInclination ? 'true mass projected with the selected inclination' : 'true mass with sin i = 1 (an upper bound; no inclination is selected)' }));
  }
  add('astrometric_amplitude', ['planet_mass', 'semimajor_axis', 'stellar_mass', 'distance'], (v) => (v.stellar_mass > 0 && v.distance > 0 ? ((v.planet_mass * M_EARTH_PER_M_SUN) / v.stellar_mass) * (v.semimajor_axis / v.distance) * 1e6 : null),
    massIsMinimum ? { massBasis: 'minimum mass (M sin i): a lower bound' } : {});

  // Transit geometry: the radius ratio is the selected one, else Rp/R* from the selected radii.
  if (!inputs.radius_ratio && inputs.planet_radius && inputs.stellar_radius && inputs.planet_radius.limit === 'NONE') {
    const k = (v) => (v.planet_radius * R_EARTH_PER_R_SUN) / v.stellar_radius;
    const computation = evaluate(k, inputs, ['planet_radius', 'stellar_radius']);
    if (computation) inputs.radius_ratio = { value: computation.value, errPlus: computation.errPlus || null, errMinus: computation.errMinus || null, unit: 'dimensionless', limit: 'NONE', derivedFrom: ['planet_radius', 'stellar_radius'] };
  }
  if (inputs.semimajor_axis && inputs.stellar_radius && inputs.semimajor_axis.limit === 'NONE') {
    const computation = evaluate((v) => v.semimajor_axis / (v.stellar_radius * R_SUN_AU), inputs, ['semimajor_axis', 'stellar_radius']);
    if (computation) inputs.scaled_semimajor_axis = { value: computation.value, errPlus: computation.errPlus || null, errMinus: computation.errMinus || null, unit: 'dimensionless', limit: 'NONE', derivedFrom: ['semimajor_axis', 'stellar_radius'] };
  }
  add('transit_depth', ['radius_ratio'], (v) => (v.radius_ratio > 0 ? v.radius_ratio ** 2 * 1e6 : null), archive('transit_depth') ? { archive: archive('transit_depth') } : {});
  // Impact parameter: the selected one, else from the selected inclination (circular orbits only).
  if (!inputs.impact_parameter && inputs.inclination && inputs.scaled_semimajor_axis && inputs.inclination.limit === 'NONE' && inputs.eccentricity && inputs.eccentricity.value === 0) {
    const computation = evaluate((v) => v.scaled_semimajor_axis * Math.cos((v.inclination * Math.PI) / 180), inputs, ['scaled_semimajor_axis', 'inclination']);
    if (computation) inputs.impact_parameter = { value: computation.value, errPlus: computation.errPlus || null, errMinus: computation.errMinus || null, unit: 'dimensionless', limit: 'NONE', derivedFrom: ['scaled_semimajor_axis', 'inclination'] };
  }
  const durationMissing = requireInputs(inputs, ['orbital_period', 'scaled_semimajor_axis', 'radius_ratio', 'impact_parameter', 'eccentricity']);
  if (durationMissing) quantities.push(notComputed('transit_duration', durationMissing));
  else if (inputs.eccentricity.value !== 0) quantities.push(notComputed('transit_duration', { code: 'eccentric-orbit', text: 'The orbit is eccentric and the release selects no argument of periastron' }));
  else add('transit_duration', ['orbital_period', 'scaled_semimajor_axis', 'radius_ratio', 'impact_parameter'], (v) => {
    const { orbital_period: p, scaled_semimajor_axis: aR, radius_ratio: k, impact_parameter: b } = v;
    if (!(p > 0 && aR > 1 && k > 0) || b < 0) return null;
    const chord = (1 + k) ** 2 - b ** 2;
    if (chord <= 0) return null;
    const sinI = Math.sqrt(Math.max(0, 1 - (b / aR) ** 2));
    const argument = Math.sqrt(chord) / (aR * sinI);
    if (!(argument <= 1)) return null;
    return ((p / Math.PI) * Math.asin(argument)) * 24;
  }, archive('transit_duration') ? { archive: archive('transit_duration') } : {});
  add('transit_probability', ['stellar_radius', 'planet_radius', 'semimajor_axis', 'eccentricity'], (v) => (v.semimajor_axis > 0 && v.eccentricity >= 0 && v.eccentricity < 1
    ? ((v.stellar_radius * R_SUN_AU + v.planet_radius * R_EARTH_PER_R_SUN * R_SUN_AU) / v.semimajor_axis) / (1 - v.eccentricity ** 2) : null));
  add('hill_radius', ['semimajor_axis', 'eccentricity', 'planet_mass', 'stellar_mass'], (v) => (v.stellar_mass > 0 && v.eccentricity >= 0 && v.eccentricity < 1
    ? v.semimajor_axis * (1 - v.eccentricity) * ((v.planet_mass * M_EARTH_PER_M_SUN) / (3 * v.stellar_mass)) ** (1 / 3) : null), massIsMinimum ? { massBasis: 'minimum mass (M sin i): a lower bound' } : {});
  if (massIsMinimum) quantities.push(notComputed('roche_limit', { code: 'minimum-mass', text: 'The selected mass is a minimum mass (M sin i)' }));
  else {
    const rocheFn = (v) => {
      if (!(v.stellar_radius > 0 && v.stellar_mass > 0 && v.planet_mass > 0 && v.planet_radius > 0)) return null;
      const rhoStar = v.stellar_mass / v.stellar_radius ** 3, rhoPlanet = (v.planet_mass * M_EARTH_PER_M_SUN) / (v.planet_radius * R_EARTH_PER_R_SUN) ** 3;
      return 2.44 * v.stellar_radius * R_SUN_AU * (rhoStar / rhoPlanet) ** (1 / 3);
    };
    const row = add('roche_limit', ['stellar_radius', 'stellar_mass', 'planet_mass', 'planet_radius'], rocheFn);
    if (row && inputs.semimajor_axis && inputs.semimajor_axis.limit === 'NONE' && row.value > 0) row.semimajorAxisOverRocheLimit = round(inputs.semimajor_axis.value / row.value, 4);
  }
  // Class: by radius when one is selected (a limit is not a size), else by true mass.
  if (inputs.planet_radius && inputs.planet_radius.limit === 'NONE') quantities.push({ code: 'planet_class', formulaId: FORMULA_BY_CODE.get('planet_class').id, state: 'calculated', value: radiusClass(inputs.planet_radius.value), basis: 'radius' });
  else if (inputs.planet_mass && inputs.planet_mass.limit === 'NONE' && !massIsMinimum) quantities.push({ code: 'planet_class', formulaId: FORMULA_BY_CODE.get('planet_class').id, state: 'calculated', value: massClass(inputs.planet_mass.value), basis: 'mass' });
  else quantities.push(notComputed('planet_class', inputs.planet_radius ? reasonLimit('planet_radius', inputs.planet_radius.limit) : reasonMissing('planet_radius')));

  // TSM (Kempton et al. 2018): the paper's own mass estimate when no mass is selected.
  const tsmMissing = requireInputs(inputs, ['planet_radius', 'stellar_radius', 'stellar_teff', 'semimajor_axis', 'j_mag']);
  if (tsmMissing) quantities.push(notComputed('tsm', tsmMissing));
  else if (inputs.planet_radius.value > 10) quantities.push(notComputed('tsm', { code: 'outside-calibration', text: 'Kempton et al. 2018 give scale factors for planets up to 10 R_earth' }));
  else {
    const massUsable = inputs.planet_mass && inputs.planet_mass.limit === 'NONE' && !massIsMinimum;
    const names = ['planet_radius', 'stellar_radius', 'stellar_teff', 'semimajor_axis', 'j_mag', ...(massUsable ? ['planet_mass'] : [])];
    const computation = evaluate((v) => {
      const t = teq(v), mass = massUsable ? v.planet_mass : chenKippingMass(v.planet_radius);
      if (t === null || !(mass > 0) || !(v.planet_radius > 0)) return null;
      const scale = TSM_SCALE.find(([max]) => v.planet_radius < max)?.[1] ?? TSM_SCALE[TSM_SCALE.length - 1][1];
      return (scale * v.planet_radius ** 3 * t) / (mass * v.stellar_radius ** 2) * 10 ** (-v.j_mag / 5);
    }, inputs, names);
    if (!computation) quantities.push(notComputed('tsm', { code: 'outside-domain', text: 'No mass could be used or estimated for this radius' }));
    else quantities.push(result('tsm', computation, { massBasis: massUsable ? 'selected mass' : 'mass estimated from the radius (Chen & Kipping 2017), as in Kempton et al. 2018' }));
  }
  add('esm', ['radius_ratio', 'stellar_teff', 'stellar_radius', 'semimajor_axis', 'k_mag'], (v) => {
    const t = teq(v);
    if (t === null || !(v.radius_ratio > 0)) return null;
    return 4.29e6 * (planck(7.5e-6, 1.10 * t) / planck(7.5e-6, v.stellar_teff)) * v.radius_ratio ** 2 * 10 ** (-v.k_mag / 5);
  });
  // Limb darkening for the light-curve model, per band.
  if (inputs.stellar_teff && inputs.stellar_teff.limit === 'NONE') {
    const coefficients = {};
    for (const band of Object.keys(LIMB_DARKENING_BANDS)) {
      const ld = grayAtmosphereLimbDarkening(inputs.stellar_teff.value, LIMB_DARKENING_BANDS[band].wavelengthNm);
      if (ld) coefficients[band] = [round(ld.u1, 4), round(ld.u2, 4)];
    }
    quantities.push({ code: 'limb_darkening', formulaId: FORMULA_BY_CODE.get('limb_darkening').id, state: 'calculated', law: 'quadratic', coefficients });
  } else quantities.push(notComputed('limb_darkening', reasonMissing('stellar_teff')));

  const serialInputs = Object.fromEntries(Object.entries(inputs).filter(([name]) => !name.startsWith('archive_') && name !== 'radius_ratio_selected').sort(([a], [b]) => (a < b ? -1 : 1)).map(([name, input]) => [name, {
    value: round(input.value, 10), ...(input.errPlus !== null && input.errPlus !== undefined ? { errPlus: round(input.errPlus, 6) } : {}), ...(input.errMinus !== null && input.errMinus !== undefined ? { errMinus: round(input.errMinus, 6) } : {}),
    unit: input.unit ?? null, ...(input.limit && input.limit !== 'NONE' ? { limit: input.limit } : {}),
    ...(input.derivedFrom ? { derivedFrom: input.derivedFrom } : { source: input.sourceId ?? (input.selectionId ? 'selection' : input.samePublication ? 'same-publication' : null), sourceRecordIds: input.sourceRecordIds ?? [] }),
    ...(input.selectionId ? { selectionId: input.selectionId } : {}), ...(input.reference ? { reference: input.reference } : {}),
    ...(input.definition ? { definition: input.definition } : {}), ...(name === 'radius_ratio' && planet.radius_ratio_selected ? { selected: true } : {}),
  }]));
  return { objectId: product.objectId, name: product.name, inputs: serialInputs, massIsMinimum, stellarSetsConsidered: setsConsidered, quantities };
}

// ------------------------------------------------------------------ staleness index
export const STALENESS_RULE = 'Predicted 1-sigma mid-transit uncertainty sqrt(sigma_T0^2 + (n sigma_P)^2) at +1, +6 and +12 months after the release reference time, from the release\'s one chosen ephemeris per planet. '
  + 'Priority (our rule, not ExoClock\'s): alert when the +1 month uncertainty exceeds 30 minutes or half the transit duration; high when the +6 month uncertainty does; medium when the +12 month uncertainty does; low otherwise; '
  + 'unranked when the ephemeris states no uncertainty. Rank 1 is the largest +12 month uncertainty.';
const MONTH_DAYS = YEAR_DAYS / 12;
export function stalenessRows(ephemerisArtifact) {
  const columns = ephemerisArtifact?.columns ?? [];
  const at = (row, name) => row[columns.indexOf(name)];
  const ref = num(ephemerisArtifact?.referenceBjd);
  if (ref === null) return [];
  const rows = [];
  for (const row of ephemerisArtifact.rows ?? []) {
    const period = num(at(row, 'periodDays')), t0 = num(at(row, 't0Bjd')), sp = num(at(row, 'periodErrDays')), st = num(at(row, 't0ErrDays')), duration = num(at(row, 'durationHours'));
    if (!(period > 0) || t0 === null) continue;
    const sigma = (offsetDays) => {
      if (sp === null || st === null) return null;
      const n = Math.round((ref + offsetDays - t0) / period);
      return Math.hypot(st, n * sp) * 1440;
    };
    const s1 = sigma(MONTH_DAYS), s6 = sigma(6 * MONTH_DAYS), s12 = sigma(12 * MONTH_DAYS);
    const threshold = duration > 0 ? Math.min(30, (duration * 60) / 2) : 30;
    const priority = s12 === null ? 'unranked' : s1 > threshold ? 'alert' : s6 > threshold ? 'high' : s12 > threshold ? 'medium' : 'low';
    rows.push({ objectId: at(row, 'objectId'), name: at(row, 'name'), status: at(row, 'status'), sigma1MonthMin: round(s1, 4), sigma6MonthMin: round(s6, 4), sigma12MonthMin: round(s12, 4),
      thresholdMin: round(threshold, 4), priority, durationHours: duration, sourceId: at(row, 'sourceId'), sourceRecordId: at(row, 'sourceRecordId') });
  }
  const ranked = rows.filter((r) => r.sigma12MonthMin !== null).sort((a, b) => b.sigma12MonthMin - a.sigma12MonthMin || (a.objectId < b.objectId ? -1 : 1));
  ranked.forEach((r, i) => { r.rank = i + 1; });
  for (const r of rows) if (r.rank === undefined) r.rank = null;
  rows.sort((a, b) => (a.rank ?? Infinity) - (b.rank ?? Infinity) || (a.objectId < b.objectId ? -1 : 1));
  return rows;
}

// ------------------------------------------------------------------ population plots (static SVG)
const PLOT_SIZE = { width: 720, height: 480, left: 70, right: 20, top: 30, bottom: 56 };
export const PLOTS = {
  'mass-period': { title: 'Mass and orbital period of confirmed planets', x: { key: 'periodDays', label: 'Orbital period (days)', log: true, min: 0.1, max: 1e6 }, y: { key: 'massEarth', label: 'Mass or minimum mass (Earth masses)', log: true, min: 0.01, max: 1e5 } },
  'radius-period': { title: 'Radius and orbital period of confirmed planets', x: { key: 'periodDays', label: 'Orbital period (days)', log: true, min: 0.1, max: 1e6 }, y: { key: 'radiusEarth', label: 'Radius (Earth radii)', log: true, min: 0.1, max: 50 } },
  'mass-radius': { title: 'Mass and radius of confirmed planets (true masses only)', x: { key: 'massEarth', label: 'Mass (Earth masses)', log: true, min: 0.01, max: 1e5 }, y: { key: 'radiusEarth', label: 'Radius (Earth radii)', log: true, min: 0.1, max: 50 }, trueMassOnly: true,
    curves: [
      { label: 'Earth-like rock and iron (32.5% iron core)', cmf: 0.325 },
      { label: 'Pure rock (no iron core)', cmf: 0 },
    ] },
  'discovery-method': { title: 'Confirmed planets by discovery method', histogram: 'methodCode' },
  'discovery-year': { title: 'Confirmed planets by discovery year', histogram: 'discoveryYear' },
};
// Zeng, Sasselov & Jacobsen 2016 (ApJ 819, 127) rocky-planet fit, valid for 1-8 Earth masses and core mass fraction 0-0.4.
export const ZENG_2016 = { citation: 'Zeng, Sasselov & Jacobsen 2016, ApJ 819, 127, eq. 10: R/R_earth = (1.07 - 0.21 CMF) (M/M_earth)^(1/3.7), valid 1-8 M_earth', url: 'https://doi.org/10.3847/0004-637X/819/2/127', minMass: 1, maxMass: 8 };
export const zengRadius = (massEarth, cmf) => (1.07 - 0.21 * cmf) * massEarth ** (1 / 3.7);

const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
export function axisTransform(axis, pixelMin, pixelMax) {
  const lo = axis.log ? Math.log10(axis.min) : axis.min, hi = axis.log ? Math.log10(axis.max) : axis.max;
  return (value) => { if (!(value > 0) && axis.log) return null; const v = axis.log ? Math.log10(value) : value; if (v < lo || v > hi) return null; return pixelMin + ((v - lo) / (hi - lo)) * (pixelMax - pixelMin); };
}
function logTicks(min, max) { const out = []; for (let e = Math.ceil(Math.log10(min)); e <= Math.floor(Math.log10(max)); e += 1) out.push(10 ** e); return out; }
const tickLabel = (v) => (v >= 1e4 || v < 0.01 ? `1e${Math.round(Math.log10(v))}` : String(Number(v.toPrecision(3))));

export function scatterSvg(plotId, spec, points) {
  const { width, height, left, right, top, bottom } = PLOT_SIZE;
  const fx = axisTransform(spec.x, left, width - right), fy = axisTransform(spec.y, height - bottom, top);
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="${plotId}-title ${plotId}-desc" data-plot="${plotId}">`,
    `<title id="${plotId}-title">${esc(spec.title)}</title>`, `<desc id="${plotId}-desc">${esc(`${points.length} confirmed planets. ${spec.x.label} against ${spec.y.label}, logarithmic axes. The data table and a text summary accompany this plot.`)}</desc>`,
    `<rect x="${left}" y="${top}" width="${width - left - right}" height="${height - top - bottom}" fill="none" stroke="currentColor" stroke-opacity="0.4"/>`];
  for (const t of logTicks(spec.x.min, spec.x.max)) { const x = fx(t); parts.push(`<line x1="${x.toFixed(1)}" y1="${height - bottom}" x2="${x.toFixed(1)}" y2="${height - bottom + 5}" stroke="currentColor"/><text x="${x.toFixed(1)}" y="${height - bottom + 18}" font-size="11" text-anchor="middle" fill="currentColor">${tickLabel(t)}</text>`); }
  for (const t of logTicks(spec.y.min, spec.y.max)) { const y = fy(t); parts.push(`<line x1="${left - 5}" y1="${y.toFixed(1)}" x2="${left}" y2="${y.toFixed(1)}" stroke="currentColor"/><text x="${left - 8}" y="${(y + 4).toFixed(1)}" font-size="11" text-anchor="end" fill="currentColor">${tickLabel(t)}</text>`); }
  parts.push(`<text x="${(left + width - right) / 2}" y="${height - 14}" font-size="12" text-anchor="middle" fill="currentColor">${esc(spec.x.label)}</text>`);
  parts.push(`<text transform="translate(16 ${(top + height - bottom) / 2}) rotate(-90)" font-size="12" text-anchor="middle" fill="currentColor">${esc(spec.y.label)}</text>`);
  const circles = [], squares = [];
  for (const p of points) {
    const x = fx(p.x), y = fy(p.y);
    if (x === null || y === null) continue;
    (p.minimum ? squares : circles).push(p.minimum ? `M${(x - 1.6).toFixed(1)} ${(y - 1.6).toFixed(1)}h3.2v3.2h-3.2z` : `M${x.toFixed(1)} ${y.toFixed(1)}m-1.6 0a1.6 1.6 0 1 0 3.2 0a1.6 1.6 0 1 0-3.2 0`);
  }
  if (circles.length) parts.push(`<path d="${circles.join('')}" fill="#3a78c2" fill-opacity="0.45"/>`);
  if (squares.length) parts.push(`<path d="${squares.join('')}" fill="#c27a3a" fill-opacity="0.45"/>`);
  for (const curve of spec.curves ?? []) {
    const path = [];
    for (let i = 0; i <= 60; i += 1) { const m = ZENG_2016.minMass * (ZENG_2016.maxMass / ZENG_2016.minMass) ** (i / 60); const x = fx(m), y = fy(zengRadius(m, curve.cmf)); if (x !== null && y !== null) path.push(`${path.length ? 'L' : 'M'}${x.toFixed(1)} ${y.toFixed(1)}`); }
    parts.push(`<path d="${path.join('')}" fill="none" stroke="currentColor" stroke-width="1.5" stroke-dasharray="${curve.cmf ? '6 3' : '2 3'}"><title>${esc(curve.label)} (${esc(ZENG_2016.citation)})</title></path>`);
  }
  const legend = [['#3a78c2', 'circle', spec.trueMassOnly ? 'true mass' : 'mass'], ...(spec.trueMassOnly ? [] : [['#c27a3a', 'square', 'minimum mass (M sin i)']])];
  legend.forEach(([colour, shape, label], i) => { const y = top + 14 + i * 16; parts.push(shape === 'circle' ? `<circle cx="${left + 12}" cy="${y}" r="3" fill="${colour}"/>` : `<rect x="${left + 9}" y="${y - 3}" width="6" height="6" fill="${colour}"/>`, `<text x="${left + 22}" y="${y + 4}" font-size="11" fill="currentColor">${esc(label)}</text>`); });
  if (spec.curves) spec.curves.forEach((curve, i) => { const y = top + 14 + (legend.length + i) * 16; parts.push(`<line x1="${left + 6}" y1="${y}" x2="${left + 18}" y2="${y}" stroke="currentColor" stroke-dasharray="${curve.cmf ? '6 3' : '2 3'}"/><text x="${left + 22}" y="${y + 4}" font-size="11" fill="currentColor">${esc(curve.label)}, Zeng et al. 2016</text>`); });
  parts.push('</svg>');
  return `${parts.join('')}\n`;
}
export function histogramSvg(plotId, spec, counts) {
  const { width, height, left, right, top, bottom } = PLOT_SIZE;
  const entries = Object.entries(counts);
  const max = Math.max(1, ...entries.map(([, c]) => c));
  const barWidth = (width - left - right) / Math.max(1, entries.length);
  const parts = [`<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="${plotId}-title ${plotId}-desc" data-plot="${plotId}">`,
    `<title id="${plotId}-title">${esc(spec.title)}</title>`, `<desc id="${plotId}-desc">${esc(`${entries.reduce((s, [, c]) => s + c, 0)} confirmed planets in ${entries.length} bins; tallest bin ${max}. The data table accompanies this plot.`)}</desc>`,
    `<line x1="${left}" y1="${height - bottom}" x2="${width - right}" y2="${height - bottom}" stroke="currentColor"/>`];
  entries.forEach(([label, count], i) => {
    const h = ((height - top - bottom) * count) / max, x = left + i * barWidth;
    parts.push(`<rect x="${(x + 1).toFixed(1)}" y="${(height - bottom - h).toFixed(1)}" width="${Math.max(1, barWidth - 2).toFixed(1)}" height="${h.toFixed(1)}" fill="#3a78c2" fill-opacity="0.7" data-bin="${esc(label)}"><title>${esc(label)}: ${count}</title></rect>`);
    if (entries.length <= 16 || i % Math.ceil(entries.length / 16) === 0) parts.push(`<text transform="translate(${(x + barWidth / 2).toFixed(1)} ${height - bottom + 12}) rotate(${entries.length > 16 ? 45 : 30})" font-size="10" fill="currentColor">${esc(label.length > 22 ? `${label.slice(0, 21)}…` : label)}</text>`);
  });
  parts.push(`<text x="${left - 8}" y="${top + 4}" font-size="11" text-anchor="end" fill="currentColor">${max}</text><text x="${left - 8}" y="${height - bottom}" font-size="11" text-anchor="end" fill="currentColor">0</text>`);
  parts.push('</svg>');
  return `${parts.join('')}\n`;
}

// ------------------------------------------------------------------ release artifacts
export function bucketFor(objectId) { return createHash('sha256').update(objectId).digest('hex').slice(0, 2); }

async function* productsFromNdjson(path) {
  const lines = createInterface({ input: createReadStream(path).pipe(createGunzip()), crlfDelay: Infinity });
  let header = null;
  for await (const line of lines) {
    if (!line.trim()) continue;
    if (header === null) { header = JSON.parse(line); continue; }
    yield JSON.parse(line);
  }
}

/**
 * Build every derived artifact. `products` is an async iterable of conflict-set products (or the
 * path of the builder's conflict-sets.ndjson.gz), `details` the Map objectId -> detail, `catalogObjects`
 * the catalog index objects, `euMagsByRecordId` Exoplanet.eu {j, k} by source record id,
 * `transitEphemerides` the release's ephemeris artifact. Writes under outputRoot; returns paths and counts.
 */
export async function buildDerivedArtifacts({ outputRoot, products, details, catalogObjects, euMagsByRecordId = new Map(), transitEphemerides, generatedAt }) {
  const statusById = new Map(catalogObjects.map((o) => [o.objectId, o]));
  const buckets = new Map(Array.from({ length: 256 }, (_, i) => [i.toString(16).padStart(2, '0'), {}]));
  const counts = { objects: 0, skippedStatus: 0, quantities: {} };
  const population = [];
  const iterable = typeof products === 'string' ? productsFromNdjson(products) : products;
  for await (const product of iterable) {
    const object = statusById.get(product.objectId);
    if (object && SKIPPED_STATUSES.has(object.normalizedStatus)) { counts.skippedStatus += 1; continue; }
    const detail = details.get(product.objectId);
    const euRecord = detail?.records?.find((r) => r.sourceId === 'exoplanet-eu');
    const block = deriveObject(product, detail, euRecord ? euMagsByRecordId.get(euRecord.sourceRecordId) ?? null : null);
    if (!block) continue;
    counts.objects += 1;
    for (const q of block.quantities) {
      const c = (counts.quantities[q.code] ??= { calculated: 0, notComputed: 0, reasons: {} });
      if (q.state === 'calculated') c.calculated += 1; else { c.notComputed += 1; c.reasons[q.reason.code] = (c.reasons[q.reason.code] ?? 0) + 1; }
    }
    buckets.get(bucketFor(product.objectId))[product.objectId] = block;
    if (object?.normalizedStatus === 'CONFIRMED') {
      const i = block.inputs;
      population.push({ objectId: product.objectId, name: product.name, periodDays: i.orbital_period && !i.orbital_period.limit ? i.orbital_period.value : null,
        radiusEarth: i.planet_radius && !i.planet_radius.limit ? i.planet_radius.value : null, massEarth: i.planet_mass && !i.planet_mass.limit ? i.planet_mass.value : null,
        massIsMinimum: block.massIsMinimum, methodCode: object.methodCode ?? null, discoveryYear: object.discoveryYear ?? null });
    }
  }
  // Staleness: rank and priority attached to the planet's block.
  const staleness = stalenessRows(transitEphemerides);
  for (const row of staleness) {
    const shard = buckets.get(bucketFor(row.objectId));
    if (shard[row.objectId]) shard[row.objectId].ephemerisStaleness = { rank: row.rank, priority: row.priority, sigma1MonthMin: row.sigma1MonthMin, sigma6MonthMin: row.sigma6MonthMin, sigma12MonthMin: row.sigma12MonthMin, thresholdMin: row.thresholdMin, sourceRecordId: row.sourceRecordId };
  }
  await mkdir(join(outputRoot, 'derived'), { recursive: true });
  await mkdir(join(outputRoot, 'populations'), { recursive: true });
  const paths = [];
  for (const [bucket, payload] of buckets) {
    await writeFile(join(outputRoot, 'derived', `${bucket}.json.gz`), gzipSync(JSON.stringify({ schemaVersion: DERIVED_SCHEMA_VERSION, objects: payload }), { level: 9 }));
    paths.push(`derived/${bucket}.json.gz`);
  }
  // Population plots and tables (confirmed planets).
  population.sort((a, b) => (a.objectId < b.objectId ? -1 : 1));
  const plots = {};
  for (const [plotId, spec] of Object.entries(PLOTS)) {
    let svg, rows;
    if (spec.histogram) {
      const counter = {};
      for (const p of population) { const key = p[spec.histogram] === null || p[spec.histogram] === undefined ? 'not stated' : String(p[spec.histogram]); counter[key] = (counter[key] ?? 0) + 1; }
      const ordered = Object.fromEntries(Object.entries(counter).sort(spec.histogram === 'discoveryYear' ? ([a], [b]) => (a < b ? -1 : 1) : ([, a], [, b]) => b - a));
      svg = histogramSvg(plotId, spec, ordered);
      rows = Object.entries(ordered);
      plots[plotId] = { title: spec.title, path: `populations/${plotId}.svg`, kind: 'histogram', field: spec.histogram, columns: ['bin', 'count'], rows,
        summary: `${population.length.toLocaleString('en-US')} confirmed planets; the largest bin is ${rows[0]?.[0] ?? 'none'} with ${(rows[0]?.[1] ?? 0).toLocaleString('en-US')}.` };
    } else {
      const points = population.filter((p) => p[spec.x.key] > 0 && p[spec.y.key] > 0 && (!spec.trueMassOnly || !p.massIsMinimum)).map((p) => ({ objectId: p.objectId, x: p[spec.x.key], y: p[spec.y.key], minimum: (spec.x.key === 'massEarth' || spec.y.key === 'massEarth') && p.massIsMinimum }));
      svg = scatterSvg(plotId, spec, points);
      const xs = points.map((p) => p.x).sort((a, b) => a - b), ys = points.map((p) => p.y).sort((a, b) => a - b), med = (v) => (v.length ? v[Math.floor(v.length / 2)] : null);
      plots[plotId] = { title: spec.title, path: `populations/${plotId}.svg`, kind: 'scatter', x: spec.x, y: spec.y, frame: PLOT_SIZE, columns: ['objectId', spec.x.key, spec.y.key, 'massIsMinimum'],
        rows: points.map((p) => [p.objectId, round(p.x, 8), round(p.y, 8), p.minimum]),
        ...(spec.curves ? { curves: spec.curves.map((c) => ({ ...c, citation: ZENG_2016.citation, url: ZENG_2016.url, massRange: [ZENG_2016.minMass, ZENG_2016.maxMass] })) } : {}),
        summary: `${points.length.toLocaleString('en-US')} confirmed planets with both values; median ${spec.x.label.toLowerCase()} ${round(med(xs), 3)}, median ${spec.y.label.toLowerCase()} ${round(med(ys), 3)}.` };
    }
    await writeFile(join(outputRoot, plots[plotId].path), svg);
    paths.push(plots[plotId].path);
  }
  const populations = { schemaVersion: POPULATIONS_SCHEMA_VERSION, generatedAt, population: 'Confirmed planets (normalized status CONFIRMED) with the values this release selects; minimum masses are shown as squares and left out of the mass-radius plot.',
    claimBoundary: 'A catalog projection, not a completeness-corrected population: detection biases of each method shape every plot.', plots };
  await writeFile(join(outputRoot, 'populations.json'), `${JSON.stringify(populations)}\n`);
  paths.push('populations.json');
  const stalenessArtifact = { schemaVersion: STALENESS_SCHEMA_VERSION, generatedAt, referenceBjd: transitEphemerides?.referenceBjd ?? null, rule: STALENESS_RULE,
    counts: staleness.reduce((acc, r) => { acc[r.priority] = (acc[r.priority] ?? 0) + 1; return acc; }, {}),
    columns: ['rank', 'objectId', 'name', 'status', 'priority', 'sigma1MonthMin', 'sigma6MonthMin', 'sigma12MonthMin', 'thresholdMin', 'durationHours', 'sourceId', 'sourceRecordId'],
    rows: staleness.map((r) => [r.rank, r.objectId, r.name, r.status, r.priority, r.sigma1MonthMin, r.sigma6MonthMin, r.sigma12MonthMin, r.thresholdMin, r.durationHours, r.sourceId, r.sourceRecordId]) };
  await writeFile(join(outputRoot, 'staleness.json'), `${JSON.stringify(stalenessArtifact)}\n`);
  paths.push('staleness.json');
  const summary = { schemaVersion: DERIVED_SUMMARY_SCHEMA_VERSION, generatedAt, formulaRegistryVersion: FORMULA_REGISTRY_VERSION, claimBoundary: CLAIM_BOUNDARY, stellarRule: STELLAR_RULE, samePublicationRule: SAME_PUBLICATION_RULE, stalenessRule: STALENESS_RULE, propagation: PROPAGATION,
    skippedStatuses: [...SKIPPED_STATUSES], shardPathPattern: DERIVED_SHARD_PATTERN, shards: 256, objects: counts.objects, skippedForStatus: counts.skippedStatus,
    formulas: FORMULAS.map((f) => ({ id: f.id, code: f.code, label: f.label, unit: f.unit, inputs: f.inputs, formula: f.formula, citation: f.citation, ...(f.assumptions ? { assumptions: f.assumptions } : {}), ...(f.archiveProperty ? { archiveProperty: f.archiveProperty } : {}) })),
    limbDarkeningBands: LIMB_DARKENING_BANDS, quantities: counts.quantities, stalenessPriorities: stalenessArtifact.counts, populationPlots: Object.keys(plots),
    plots: Object.fromEntries(Object.entries(plots).map(([plotId, plot]) => [plotId, Object.fromEntries(Object.entries(plot).filter(([key]) => key !== 'rows'))])) };
  await writeFile(join(outputRoot, 'derived-summary.json'), `${JSON.stringify(summary, null, 2)}\n`);
  paths.push('derived-summary.json');
  return { paths: paths.sort(), counts, summary, population: population.length, staleness: stalenessArtifact.counts };
}

/** The manifest block for a built release. */
export function manifestBlock(artifacts) {
  return { schemaVersion: DERIVED_SCHEMA_VERSION, summaryPath: 'derived-summary.json', shardPathPattern: DERIVED_SHARD_PATTERN, shards: 256, objects: artifacts.counts.objects,
    formulaRegistryVersion: FORMULA_REGISTRY_VERSION, formulas: FORMULAS.length, populationsPath: 'populations.json', populationPlots: Object.keys(PLOTS).length, stalenessPath: 'staleness.json',
    stalenessPriorities: artifacts.staleness, claimBoundary: CLAIM_BOUNDARY };
}

// ------------------------------------------------------------------ command line
async function* productsFromShards(dir) {
  for (const file of readdirSync(join(dir, 'conflicts')).filter((f) => /^[0-9a-f]{2}\.json\.gz$/.test(f)).sort()) {
    const shard = JSON.parse(gunzipSync(readFileSync(join(dir, 'conflicts', file))).toString('utf8'));
    for (const id of Object.keys(shard).sort()) yield shard[id];
  }
}
if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const dir = process.argv[2] || 'worldsindex/data';
  const details = new Map();
  for (const file of readdirSync(join(dir, 'details')).filter((f) => /^[0-9a-f]{2}\.json\.gz$/.test(f))) {
    for (const [id, detail] of Object.entries(JSON.parse(gunzipSync(readFileSync(join(dir, 'details', file))).toString('utf8')))) details.set(id, detail);
  }
  const catalog = JSON.parse(gunzipSync(readFileSync(join(dir, 'catalog-index.json.gz'))).toString('utf8'));
  const ephemerides = JSON.parse(gunzipSync(readFileSync(join(dir, 'transit-ephemerides.json.gz'))).toString('utf8'));
  const out = process.argv.includes('--write') ? dir : join(process.env.TMPDIR || '/tmp', 'worldsindex-derived-check');
  const artifacts = await buildDerivedArtifacts({ outputRoot: out, products: productsFromShards(dir), details, catalogObjects: catalog.objects, transitEphemerides: ephemerides, generatedAt: catalog.generatedAt });
  console.log(JSON.stringify({ output: out, objects: artifacts.counts.objects, skippedForStatus: artifacts.counts.skippedStatus, confirmedInPlots: artifacts.population, staleness: artifacts.staleness, quantities: artifacts.counts.quantities }, null, 2));
}

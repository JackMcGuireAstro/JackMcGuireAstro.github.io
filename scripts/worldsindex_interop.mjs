#!/usr/bin/env node
// WorldsIndex interoperability exports (Framework v2, Track 1, part B).
//
// The static builder (scripts/build_worldsindex_static.mjs) already projects every public
// table as JSON. This module turns the same rows into the formats astronomy tooling reads
// without a parser of its own, with nothing but Node's standard library:
//
//   * ECSV 1.0 (Astropy APE 6) and VOTable 1.5 for every public table: objects (the catalog
//     index), measurements (one row per retained catalog value, per publication),
//     transit ephemerides, source coverage, identity states and references;
//   * the column-metadata registry those files are written from: name, datatype, UCD1+,
//     VOUnits unit (null for a column that carries no physical unit) and a description for
//     every column. A table that emits a column without an entry stops the build;
//   * HEALPix order 8 (nested) indices, carried as a column and used as the row order, so
//     the VOParquet copies (scripts/worldsindex_parquet.py, built from the ECSV files) are
//     sorted the way TOPCAT, DuckDB and pyarrow row-group pruning expect.
//
// Absent values are explicit: numbers and booleans are empty cells (masked when read with
// Astropy, null in Parquet); text columns use the empty string. Limit flags travel as the
// source rows carry them (NASA *lim flags: 1 = lower limit, -1 = upper limit), never as a
// dropped value or a NaN. Nothing here is averaged or converted: the measurements table
// keeps each source's own value, unit and uncertainty beside its reference and record id.
//
// The ECSV/VOTable writers and the registry are tested by scripts/test_worldsindex_interop.mjs
// (unit) and scripts/test_worldsindex_interop.py (Astropy/pyarrow round trip of a built release).

import { createHash } from 'node:crypto';

export const SITE_BASE_URL = 'https://jackmcguireastro.github.io';
export const EXPORTS_SCHEMA_VERSION = 'worldsindex-exports.v1';
export const VOTABLE_VERSION = '1.5';
export const VOTABLE_NAMESPACE = 'http://www.ivoa.net/xml/VOTable/v1.3';
export const HEALPIX_ORDER = 8;
export const HEALPIX_NSIDE = 2 ** HEALPIX_ORDER;
export const HEALPIX_COLUMN = 'healpix_8_nested';
export const INTEROP_CLAIM = 'Formats and metadata follow the IVOA documents named in each file; the content is the same retained public catalog projection as the JSON release, not an independent measurement. Source membership is not independent confirmation; nothing is averaged across sources.';
export const ECSV_FORMAT_NOTE = 'ECSV 1.0 (Astropy APE 6): CSV with a YAML header carrying datatype, unit, UCD and description per column';
export const VOTABLE_FORMAT_NOTE = 'IVOA VOTable 1.5 TABLEDATA with a UCD and a VOUnits unit per FIELD';

// VOUnits 1.1 renderings (astropy.units ... .to_string("vounit")). Earth and Jupiter units are
// not VOUnits known units, so they are written as the IAU 2015 nominal values in SI, which the
// VOUnits grammar allows as a scale factor on a base unit; the column description names them.
export const VOUNIT = {
  day: 'd', hour: 'h', kelvin: 'K', degree: 'deg', au: 'AU', solRad: 'solRad',
  earthRadius: '6378100m', jupiterRadius: '71492000m', earthMass: '5.9721679e+24kg', jupiterMass: '1.8981246e+27kg',
};
const ISO = 'ISO 8601 text as the source states it; empty when not retained.';

const column = (name, datatype, ucd, unit, description) => ({ name, datatype, ucd, unit, description });
const RA = column('ra_deg', 'float64', 'pos.eq.ra;meta.main', VOUNIT.degree, 'Source-retained right ascension (ICRS) of the object as the atlas projects it.');
const DEC = column('dec_deg', 'float64', 'pos.eq.dec;meta.main', VOUNIT.degree, 'Source-retained declination (ICRS) of the object as the atlas projects it.');
const HEALPIX = column(HEALPIX_COLUMN, 'int64', 'pos.healpix', null, `HEALPix order ${HEALPIX_ORDER} (nside ${HEALPIX_NSIDE}) nested index of (ra_deg, dec_deg); empty without coordinates. Rows are sorted by it.`);
const OBJECT_ID = column('object_id', 'string', 'meta.id;meta.main', null, 'Stable WorldsIndex object identifier (the key of the catalog index and the detail shards).');
const NAME = column('name', 'string', 'meta.id', null, 'Preferred public designation in this release.');
const HOST = column('host_name', 'string', 'meta.id.parent', null, 'Host star or system name as the primary source states it.');

// Population values the atlas carries per object (one selected source row each, with the
// source record it came from and its limit flag); see the catalog index.
const POPULATION_AXES = [
  ['orbital_period_days', 'orbitalPeriodDays', 'time.period', VOUNIT.day, 'Orbital period'],
  ['radius_earth', 'radiusEarth', 'phys.size.radius', VOUNIT.earthRadius, 'Planet radius in Earth radii (IAU 2015 nominal 6378100 m)'],
  ['mass_earth', 'massEarth', 'phys.mass', VOUNIT.earthMass, 'Planet mass in Earth masses (IAU 2015 nominal, 5.9721679e24 kg)'],
  ['minimum_mass_earth', 'minimumMassEarth', 'phys.mass', VOUNIT.earthMass, 'Minimum mass (M sin i) in Earth masses'],
  ['estimated_mass_earth', 'estimatedMassEarth', 'phys.mass', VOUNIT.earthMass, 'Mass estimated by the source from a mass-radius relation, in Earth masses (not a measurement)'],
  ['eccentricity', 'eccentricity', 'src.orbital.eccentricity', null, 'Orbital eccentricity'],
  ['equilibrium_temperature_k', 'equilibriumTemperatureK', 'phys.temperature', VOUNIT.kelvin, 'Equilibrium temperature as the source states it'],
  ['insolation_earth', 'insolationEarth', 'phot.flux.bol;arith.ratio', null, 'Insolation relative to Earth (dimensionless)'],
];
function populationColumns() {
  const out = [];
  for (const [name, , ucd, unit, label] of POPULATION_AXES) {
    out.push(column(name, 'float64', ucd, unit, `${label}; the value of the one source row the atlas selected for this object (see the matching _source_record_id), never an average.`));
    out.push(column(`${name}_limit`, 'string', 'meta.code;stat', null, `Limit flag of ${name} as the source carries it: NONE, LOWER_LIMIT, UPPER_LIMIT, INTERVAL or UNKNOWN_LIMIT.`));
    out.push(column(`${name}_source_id`, 'string', 'meta.ref', null, `Source of ${name}.`));
    out.push(column(`${name}_source_record_id`, 'string', 'meta.id.cross', null, `Stable source record id ${name} was read from.`));
  }
  return out;
}

// --------------------------------------------------------------------------- registry
export const COLUMN_REGISTRY = {
  objects: [
    OBJECT_ID, NAME, HOST,
    column('system_id', 'string', 'meta.id.parent', null, 'WorldsIndex planetary-system identifier.'),
    RA, DEC, HEALPIX,
    column('status', 'string', 'meta.code.status', null, 'Normalized status: CONFIRMED, CANDIDATE, CONTROVERSIAL, FALSE_POSITIVE, REFUTED or UNKNOWN; the source-native status stays in the detail rows.'),
    column('identity_state', 'string', 'meta.code.qual', null, 'Identity state under the pinned identity rules: CANONICAL, RESOLVED, AMBIGUOUS or UNREVIEWED.'),
    column('method_code', 'string', 'meta.code.class', null, 'Primary detection-method code (the method registry in registry.json.gz).'),
    column('method_codes', 'string', 'meta.code.class', null, 'Every detection-method code any source claims for the object, space-separated.'),
    column('discovery_year', 'int64', 'time.epoch', null, 'Discovery year as the primary source states it.'),
    column('discovery_facility', 'string', 'instr.tel', null, 'Discovery facility as the primary source states it.'),
    column('primary_source_id', 'string', 'meta.ref', null, 'Source whose record names and positions the object.'),
    column('source_ids', 'string', 'meta.ref', null, 'Every source carrying a record for the object, space-separated. Membership is not independent confirmation.'),
    column('source_object_ids', 'string', 'meta.id.cross', null, 'Source-native object identifiers linked to this object, space-separated.'),
    column('source_record_count', 'int64', 'meta.number', null, 'Number of source record occurrences the atlas links to the object.'),
    column('detail_record_count', 'int64', 'meta.number', null, 'Number of retained native catalog rows in the object\'s detail shard.'),
    column('spectrum_record_count', 'int64', 'meta.number', null, 'Number of retained atmospheric-spectroscopy rows (source nasa-atmospheric-spectroscopy) in the object\'s detail shard.'),
    column('aliases', 'string', 'meta.id.cross', null, 'Provider-stated aliases (NASA name tables), " | " separated; empty when none.'),
    ...populationColumns(),
  ],
  measurements: [
    OBJECT_ID, NAME, HOST, RA, DEC, HEALPIX,
    column('source_id', 'string', 'meta.ref', null, 'Source of the retained row.'),
    column('source_record_id', 'string', 'meta.id.cross', null, 'Stable identifier of the retained native row (detail shard records[].sourceRecordId).'),
    column('record_type', 'string', 'meta.code', null, 'published solution (one publication), source composite (the source\'s own composite), mission candidate row or catalog row.'),
    column('is_source_default', 'bool', 'meta.code', null, 'True when the source marks this row as its default parameter set; empty when the source has no such flag.'),
    column('property_code', 'string', 'meta.code', null, 'WorldsIndex property code (orbital_period, planet_radius, planet_mass, semi_major_axis, eccentricity, inclination, transit_midpoint, transit_duration, transit_depth, equilibrium_temperature, insolation, planet_star_radius_ratio, semi_major_axis_over_stellar_radius, impact_parameter, stellar_radius).'),
    column('source_field', 'string', 'meta.id', null, 'The source\'s own column the value was read from (its definition pointer).'),
    column('value', 'float64', 'stat.value', null, 'Value exactly as the source states it, in unit. Units vary by row; never converted here.'),
    column('error_upper', 'float64', 'stat.error;stat.max', null, 'Positive uncertainty as the source states it (absolute value), in unit; empty when not stated.'),
    column('error_lower', 'float64', 'stat.error;stat.min', null, 'Negative uncertainty as the source states it (absolute value), in unit; empty when not stated.'),
    column('unit', 'string', 'meta.unit', null, 'Unit of value and its errors: a VOUnits string (d, h, K, deg, AU, solRad, 6378100m = Earth radius, 71492000m = Jupiter radius, 5.9721679e+24kg = Earth mass, 1.8981246e+27kg = Jupiter mass), empty for a dimensionless quantity, or percent / ppm for a transit depth the source states on that scale (not VOUnits).'),
    column('limit_type', 'string', 'meta.code;stat', null, 'NONE, LOWER_LIMIT or UPPER_LIMIT from the source\'s own limit flag (NASA *lim: 1 lower, -1 upper); UNKNOWN_LIMIT for a flag outside that convention; empty when the source has no flag.'),
    column('mass_definition', 'string', 'meta.code', null, 'For planet_mass: MASS, MINIMUM_MASS_M_SIN_I or MASS_RADIUS_RELATION_ESTIMATE as the source provenance states; empty otherwise.'),
    column('time_system', 'string', 'time.scale', null, 'For transit_midpoint: the time system the source states (BJD_TDB; BKJD for Kepler KOI rows, BJD - 2454833.0); empty otherwise.'),
    column('reference', 'string', 'meta.bib', null, 'Publication reference the source cites for the row (or, for a composite, for this value); empty when the source gives none.'),
    column('reference_url', 'string', 'meta.ref.url', null, 'URL of that reference when the source gives one.'),
    column('published', 'string', 'time.publiYear', null, 'Publication date the source states for the row. ' + ISO),
    column('updated', 'string', 'time.processing', null, 'Row update date the source states. ' + ISO),
  ],
  transit_ephemerides: [
    OBJECT_ID, NAME, HOST, RA, DEC, HEALPIX,
    column('status', 'string', 'meta.code.status', null, 'Normalized status of the planet (CONFIRMED, CANDIDATE or CONTROVERSIAL).'),
    column('period_days', 'float64', 'time.period', VOUNIT.day, 'Orbital period from the one cited catalog row.'),
    column('period_err_days', 'float64', 'stat.error;time.period', VOUNIT.day, 'Period uncertainty (the larger of the reported upper and lower errors, 1 sigma); empty when not reported.'),
    column('t0_bjd', 'float64', 'time.epoch', VOUNIT.day, 'Mid-transit epoch from the same row, BJD_TDB unless time_system says otherwise.'),
    column('t0_err_days', 'float64', 'stat.error;time.epoch', VOUNIT.day, 'Epoch uncertainty (1 sigma); empty when not reported.'),
    column('duration_hours', 'float64', 'time.duration', VOUNIT.hour, 'Transit duration as the row states it; empty when not reported.'),
    column('depth_ppt', 'float64', 'phot.flux;arith.ratio', null, 'Transit depth in parts per thousand as the row states it (converted only in scale); empty when not reported.'),
    column('host_mag', 'float64', 'phot.mag', 'mag', 'Host magnitude in host_mag_band; empty when not reported.'),
    column('host_mag_band', 'string', 'instr.bandpass', null, 'V or TESS.'),
    column('time_system', 'string', 'time.scale', null, 'Time system of t0_bjd.'),
    column('source_id', 'string', 'meta.ref', null, 'Source of the cited row.'),
    column('source_table', 'string', 'meta.dataset', null, 'Source table of the cited row (pscomppars, ps, toi, cumulative, k2pandc).'),
    column('source_record_id', 'string', 'meta.id.cross', null, 'Stable identifier of the cited row.'),
    column('reference', 'string', 'meta.bib', null, 'Reference the source cites for the row; empty when none.'),
    column('reference_url', 'string', 'meta.ref.url', null, 'URL of that reference; empty when none.'),
    column('matched_via', 'string', 'meta.code', null, '"tic+period" when a TOI row was attached to a NASA planet by TIC and period agreement; empty otherwise.'),
  ],
  source_coverage: [
    column('source_id', 'string', 'meta.id;meta.main', null, 'Source identifier in the WorldsIndex source universe.'),
    column('coverage_class', 'string', 'meta.code.class', null, 'ATLAS_INGESTED, FROZEN_CONTEXT, LIVE_PER_TARGET or NOT_CONFIGURED.'),
    column('state', 'string', 'meta.code.status', null, 'Source state in this release.'),
    column('snapshot_id', 'string', 'meta.id', null, 'Frozen snapshot the release read; empty when none.'),
    column('retrieved_at', 'string', 'time.epoch', null, 'When that snapshot was retrieved. ' + ISO),
    column('keyed_on', 'string', 'meta.note', null, 'Identifier kinds the source is keyed on, " | " separated.'),
    column('eligible', 'int64', 'meta.number', null, 'Objects carrying an identifier of the kind the source is keyed on.'),
    column('attached', 'int64', 'meta.number', null, 'Objects a row of the source reached by that exact identifier.'),
    column('missing_at_provider', 'int64', 'meta.number', null, 'Eligible objects the snapshot holds no row for.'),
    column('blocked', 'int64', 'meta.number', null, 'Eligible objects not looked up (no snapshot, no credential, or live-only).'),
    column('blocked_reason', 'string', 'meta.note', null, 'Why, in the words of the last recorded attempt when there is one.'),
    column('identifier_missing', 'int64', 'meta.number', null, 'Objects with no identifier of a usable kind.'),
    column('rows', 'int64', 'meta.number', null, 'Rows in the snapshot (or the ingested catalog).'),
    column('attached_rows', 'int64', 'meta.number', null, 'Rows attached to objects in this release.'),
    column('via_cross_identifier', 'int64', 'meta.number', null, 'Rows attached through an identifier another source stated.'),
    column('declared', 'bool', 'meta.code', null, 'Four denominators (Professional Framework 4): in the versioned source universe.'),
    column('applicable', 'bool', 'meta.code', null, 'Scientifically relevant and queryable for at least one object of this release.'),
    column('executed', 'bool', 'meta.code', null, 'A bounded query, snapshot lookup or retrieval attempt occurred for this release; empty when the builder cannot know (live per-target sources).'),
    column('data_bearing', 'bool', 'meta.code', null, 'Valid rows were returned and retained for at least one object.'),
    column('objects_declared', 'int64', 'meta.number', null, 'Objects in the release (the declared denominator per source).'),
    column('objects_applicable', 'int64', 'meta.number', null, 'Objects the source is applicable to (= eligible).'),
    column('objects_executed', 'int64', 'meta.number', null, 'Objects a lookup was executed for; empty when not computable.'),
    column('objects_data_bearing', 'int64', 'meta.number', null, 'Objects with retained rows (= attached).'),
    column('not_computable', 'string', 'meta.note', null, 'Which denominator the builder cannot compute for this source and why; empty when all four are computed.'),
    column('last_attempt_at', 'string', 'time.epoch', null, 'When the follow-up last tried this source. ' + ISO),
    column('last_attempt_outcome', 'string', 'meta.code.status', null, 'RETRIEVED, UNCHANGED, REFUSED, FAILED or SKIPPED_*; empty when none recorded.'),
    column('last_attempt_category', 'string', 'meta.code', null, 'Failure category of that attempt; empty when none.'),
    column('last_attempt_detail', 'string', 'meta.note', null, 'The provider\'s words or the follow-up\'s reason.'),
  ],
  identity_states: [
    OBJECT_ID, NAME, HOST, RA, DEC, HEALPIX,
    column('identity_state', 'string', 'meta.code.qual', null, 'CANONICAL, RESOLVED, AMBIGUOUS or UNREVIEWED under the pinned identity rules.'),
    column('system_id', 'string', 'meta.id.parent', null, 'WorldsIndex planetary-system identifier.'),
    column('primary_source_id', 'string', 'meta.ref', null, 'Source whose record names the object.'),
    column('source_ids', 'string', 'meta.ref', null, 'Sources linked to the object, space-separated.'),
    column('source_object_ids', 'string', 'meta.id.cross', null, 'Source-native identifiers linked to the object, space-separated.'),
    column('source_record_count', 'int64', 'meta.number', null, 'Source record occurrences linked to the object.'),
    column('identity_rules_version', 'string', 'meta.version', null, 'Identity rules the release was built under (the reviewed atlas release contract).'),
  ],
  references: [
    column('reference_key', 'string', 'meta.id;meta.main', null, 'Stable key: sha256 of source_id, label and url (first 16 hex digits).'),
    column('source_id', 'string', 'meta.ref', null, 'Source that cites the reference.'),
    column('label', 'string', 'meta.bib', null, 'Reference text as the source states it.'),
    column('url', 'string', 'meta.ref.url', null, 'URL the source gives for the reference; empty when none.'),
    column('record_count', 'int64', 'meta.number', null, 'Retained native rows citing the reference (whole rows, or composite values for NASA PSCompPars).'),
    column('object_count', 'int64', 'meta.number', null, 'Objects with at least one such row.'),
    column('example_object_ids', 'string', 'meta.id.cross', null, 'Up to five object ids citing the reference, space-separated.'),
  ],
};

export const TABLE_DESCRIPTIONS = {
  objects: 'One row per WorldsIndex object (the catalog index): names, position, status, identity state, methods, sources and the population values the atlas selected, each with its source record id and limit flag.',
  measurements: 'One row per retained catalog value per publication: every source\'s own value, uncertainty, unit, limit flag and reference, keyed by the object and the native source record. Nothing is averaged or converted.',
  transit_ephemerides: 'One transit ephemeris per transiting planet, period and epoch from one cited catalog row (transit-ephemerides.json.gz).',
  source_coverage: 'Per declared source: eligibility, attachment, the four denominators and the last recorded retrieval attempt (source-coverage.json).',
  identity_states: 'Identity state of every object under the pinned identity rules, with the sources and source-native identifiers linked to it.',
  references: 'Every publication reference the retained native rows cite, with how many rows and objects cite it.',
};

/** Fail the build when a table emits a column the registry does not describe, or omits one it does. */
export function assertRegistered(table, names) {
  const specs = COLUMN_REGISTRY[table];
  if (!specs) throw new Error(`interop: no column registry for table ${table}`);
  const registered = specs.map((spec) => spec.name);
  const missing = names.filter((name) => !registered.includes(name));
  const extra = registered.filter((name) => !names.includes(name));
  if (missing.length || extra.length) throw new Error(`interop: column registry for ${table} disagrees with the emitted columns (unregistered: ${missing.join(', ') || 'none'}; registered but not emitted: ${extra.join(', ') || 'none'})`);
  for (const spec of specs) {
    if (!/^[a-z][a-z0-9_]*$/.test(spec.name)) throw new Error(`interop: column name ${spec.name} is not lower snake case`);
    if (!['string', 'float64', 'int64', 'bool'].includes(spec.datatype)) throw new Error(`interop: ${table}.${spec.name} has unsupported datatype ${spec.datatype}`);
    if (!spec.ucd || !spec.description) throw new Error(`interop: ${table}.${spec.name} lacks a UCD or a description`);
  }
  return specs;
}

// --------------------------------------------------------------------------- HEALPix
// ang2pix for the nested scheme (Gorski et al. 2005; the HEALPix C library algorithm).
function spreadBits(value) {
  let v = BigInt(value);
  v = (v | (v << 16n)) & 0x0000FFFF0000FFFFn;
  v = (v | (v << 8n)) & 0x00FF00FF00FF00FFn;
  v = (v | (v << 4n)) & 0x0F0F0F0F0F0F0F0Fn;
  v = (v | (v << 2n)) & 0x3333333333333333n;
  v = (v | (v << 1n)) & 0x5555555555555555n;
  return v;
}
export function healpixNested(raDeg, decDeg, order = HEALPIX_ORDER) {
  if (!Number.isFinite(raDeg) || !Number.isFinite(decDeg) || Math.abs(decDeg) > 90) return null;
  const nside = 2 ** order;
  const theta = (90 - decDeg) * Math.PI / 180;
  let phi = ((raDeg % 360) + 360) % 360 * Math.PI / 180;
  const z = Math.cos(theta);
  const za = Math.abs(z);
  const tt = (2 * phi / Math.PI) % 4; // in [0, 4)
  let face, ix, iy;
  if (za <= 2 / 3) {
    const temp1 = nside * (0.5 + tt);
    const temp2 = nside * z * 0.75;
    const jp = Math.floor(temp1 - temp2);
    const jm = Math.floor(temp1 + temp2);
    const ifp = Math.floor(jp / nside);
    const ifm = Math.floor(jm / nside);
    face = ifp === ifm ? (ifp | 4) : ifp < ifm ? ifp : ifm + 8;
    ix = jm & (nside - 1);
    iy = nside - (jp & (nside - 1)) - 1;
  } else {
    const ntt = Math.min(3, Math.floor(tt));
    const tp = tt - ntt;
    const tmp = nside * Math.sqrt(3 * (1 - za));
    let jp = Math.floor(tp * tmp);
    let jm = Math.floor((1 - tp) * tmp);
    jp = Math.min(jp, nside - 1);
    jm = Math.min(jm, nside - 1);
    if (z >= 0) { face = ntt; ix = nside - jm - 1; iy = nside - jp - 1; } else { face = ntt + 8; ix = jp; iy = jm; }
  }
  const pixelInFace = spreadBits(ix) | (spreadBits(iy) << 1n);
  return Number(BigInt(face) * BigInt(nside) * BigInt(nside) + pixelInFace);
}

// --------------------------------------------------------------------------- cells and sorting
function isAbsent(value) {
  return value === null || value === undefined || value === '' || (typeof value === 'number' && !Number.isFinite(value));
}
/** Coerce one cell to its column datatype; returns null for an absent value. */
export function coerce(spec, value) {
  if (isAbsent(value)) return null;
  switch (spec.datatype) {
    case 'string': return typeof value === 'string' ? value : String(value);
    case 'float64': { const n = typeof value === 'number' ? value : Number(value); if (!Number.isFinite(n)) throw new Error(`interop: ${spec.name} is not a number: ${JSON.stringify(value)}`); return n; }
    case 'int64': { const n = typeof value === 'number' ? value : Number(value); if (!Number.isInteger(n)) throw new Error(`interop: ${spec.name} is not an integer: ${JSON.stringify(value)}`); return n; }
    case 'bool': if (typeof value === 'boolean') return value; throw new Error(`interop: ${spec.name} is not a boolean: ${JSON.stringify(value)}`);
    default: throw new Error(`interop: unsupported datatype ${spec.datatype}`);
  }
}
/** Rows as arrays in column order, every cell coerced; the registry is asserted first. */
export function materialize(table, rows, names) {
  const specs = assertRegistered(table, names);
  const position = new Map(names.map((name, index) => [name, index]));
  return { specs, rows: rows.map((row) => specs.map((spec) => coerce(spec, row[position.get(spec.name)]))) };
}
const compareCell = (a, b) => (a === null) - (b === null) || (typeof a === 'number' && typeof b === 'number' ? a - b : String(a) < String(b) ? -1 : String(a) > String(b) ? 1 : 0);
/** Sort by the HEALPix column (rows without one last), then by the tie-break columns, in place. */
export function sortByHealpix(specs, rows, tieBreak) {
  const at = (name) => { const index = specs.findIndex((spec) => spec.name === name); if (index < 0) throw new Error(`interop: no column ${name} to sort by`); return index; };
  const keys = [at(HEALPIX_COLUMN), ...tieBreak.map(at)];
  rows.sort((a, b) => { for (const key of keys) { const c = compareCell(a[key], b[key]); if (c) return c; } return 0; });
  return rows;
}

// --------------------------------------------------------------------------- ECSV 1.0
function numberText(value) {
  // Shortest round-trip decimal; Astropy and pyarrow read both forms.
  return Number.isInteger(value) && Math.abs(value) < 1e15 ? String(value) : String(value);
}
function cellText(spec, value) {
  if (value === null) return '';
  if (spec.datatype === 'bool') return value ? 'True' : 'False';
  if (spec.datatype === 'string') return value;
  return numberText(value);
}
function csvQuote(text) {
  return /[",\r\n]|^\s|\s$/.test(text) || text === '' ? `"${text.replaceAll('"', '""')}"` : text;
}
const ECSV_QUOTE_EMPTY = false;
export function ecsv(table, specs, rows, meta = {}) {
  const lines = ['# %ECSV 1.0', '# ---', '# datatype:'];
  for (const spec of specs) {
    const entry = { name: spec.name, datatype: spec.datatype };
    if (spec.unit) entry.unit = spec.unit;
    entry.description = spec.description;
    entry.meta = { ucd: spec.ucd };
    lines.push(`# - ${JSON.stringify(entry)}`); // JSON is a YAML flow mapping
  }
  const tableMeta = { name: table, description: TABLE_DESCRIPTIONS[table], format: ECSV_FORMAT_NOTE, claim_boundary: INTEROP_CLAIM, ...meta };
  lines.push(`# meta: ${JSON.stringify(tableMeta)}`);
  lines.push('# schema: astropy-2.0');
  lines.push("# delimiter: ','");
  lines.push(specs.map((spec) => spec.name).join(','));
  for (const row of rows) {
    lines.push(row.map((value, index) => {
      const text = cellText(specs[index], value);
      if (text === '') return ECSV_QUOTE_EMPTY ? '""' : '';
      return csvQuote(text);
    }).join(','));
  }
  return `${lines.join('\n')}\n`;
}

// --------------------------------------------------------------------------- VOTable 1.5
export function xmlEscape(value) {
  return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
}
const VO_DATATYPE = { string: 'char', float64: 'double', int64: 'long', bool: 'boolean' };
export function votable(table, specs, rows, params = {}) {
  const hasCoordinates = specs.some((spec) => spec.ucd === 'pos.eq.ra;meta.main');
  const fields = specs.map((spec) => {
    const attributes = [`name="${xmlEscape(spec.name)}"`, `datatype="${VO_DATATYPE[spec.datatype]}"`];
    if (spec.datatype === 'string') attributes.push('arraysize="*"');
    attributes.push(`ucd="${xmlEscape(spec.ucd)}"`);
    if (spec.unit) attributes.push(`unit="${xmlEscape(spec.unit)}"`);
    if (hasCoordinates && (spec.ucd === 'pos.eq.ra;meta.main' || spec.ucd === 'pos.eq.dec;meta.main')) attributes.push('ref="ICRS"');
    return `<FIELD ${attributes.join(' ')}><DESCRIPTION>${xmlEscape(spec.description)}</DESCRIPTION></FIELD>`;
  });
  const paramLines = Object.entries(params).map(([name, value]) => `<PARAM name="${xmlEscape(name)}" datatype="char" arraysize="*" value="${xmlEscape(value)}"/>`);
  const head = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    `<VOTABLE version="${VOTABLE_VERSION}" xmlns="${VOTABLE_NAMESPACE}" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="${VOTABLE_NAMESPACE} http://www.ivoa.net/xml/VOTable/VOTable-${VOTABLE_VERSION}.xsd">`,
    `<DESCRIPTION>${xmlEscape(INTEROP_CLAIM)}</DESCRIPTION>`,
    '<RESOURCE type="results">',
    '<INFO name="QUERY_STATUS" value="OK"/>',
    `<INFO name="format" value="${xmlEscape(VOTABLE_FORMAT_NOTE)}"/>`,
    ...(hasCoordinates ? ['<COOSYS ID="ICRS" system="ICRS"/>'] : []),
    `<TABLE name="${xmlEscape(table)}" nrows="${rows.length}">`,
    `<DESCRIPTION>${xmlEscape(TABLE_DESCRIPTIONS[table])}</DESCRIPTION>`,
    ...paramLines,
    ...fields,
    '<DATA><TABLEDATA>',
  ];
  const body = rows.map((row) => `<TR>${row.map((value, index) => {
    const spec = specs[index];
    if (value === null) return '<TD/>';
    if (spec.datatype === 'bool') return value ? '<TD>true</TD>' : '<TD>false</TD>';
    return `<TD>${xmlEscape(spec.datatype === 'string' ? value : numberText(value))}</TD>`;
  }).join('')}</TR>`);
  return `${[...head, ...body, '</TABLEDATA></DATA>', '</TABLE>', '</RESOURCE>', '</VOTABLE>'].join('\n')}\n`;
}

// --------------------------------------------------------------------------- table builders
const join = (values, separator = ' ') => [...new Set((values ?? []).filter((value) => value !== null && value !== undefined && value !== ''))].join(separator);
const number = (value) => { if (value === null || value === undefined || value === '') return null; const n = Number(value); return Number.isFinite(n) ? n : null; };
const abs = (value) => { const n = number(value); return n === null ? null : Math.abs(n); };

export function objectsTable(catalogObjects, detectionsById, detailCountById) {
  // detailCountById: Map objectId -> { records, spectra }
  const names = COLUMN_REGISTRY.objects.map((spec) => spec.name);
  const rows = catalogObjects.map((object) => {
    const detection = detectionsById.get(object.objectId);
    const population = detection?.population ?? {};
    const row = {
      object_id: object.objectId, name: object.name, host_name: object.hostName, system_id: object.systemId,
      ra_deg: object.raDeg, dec_deg: object.decDeg, healpix_8_nested: healpixNested(object.raDeg, object.decDeg),
      status: object.normalizedStatus, identity_state: object.identityState, method_code: object.methodCode, method_codes: join(object.methodCodes),
      discovery_year: object.discoveryYear, discovery_facility: object.discoveryFacility, primary_source_id: object.primarySourceId,
      source_ids: join(object.sourceIds), source_object_ids: join(object.sourceObjectIds), source_record_count: object.sourceRecordCount,
      detail_record_count: detailCountById.get(object.objectId)?.records ?? 0, spectrum_record_count: detailCountById.get(object.objectId)?.spectra ?? 0, aliases: join(object.aliases, ' | '),
    };
    for (const [name, key] of POPULATION_AXES) {
      const value = population[key];
      row[name] = value?.value ?? null; row[`${name}_limit`] = value?.limitType ?? null;
      row[`${name}_source_id`] = value?.sourceId ?? null; row[`${name}_source_record_id`] = value?.sourceRecordId ?? null;
    }
    return names.map((name) => row[name]);
  });
  const { specs, rows: cells } = materialize('objects', rows, names);
  return { specs, rows: sortByHealpix(specs, cells, ['object_id']) };
}

// Which native fields feed which property, per source table. [property, value, errUpper, errLower, limitFlag, unit, timeSystem]
const NASA_PS_FIELDS = [
  ['orbital_period', 'pl_orbper', 'pl_orbpererr1', 'pl_orbpererr2', 'pl_orbperlim', VOUNIT.day, null],
  ['planet_radius', 'pl_rade', 'pl_radeerr1', 'pl_radeerr2', 'pl_radelim', VOUNIT.earthRadius, null],
  ['planet_mass', 'pl_bmasse', 'pl_bmasseerr1', 'pl_bmasseerr2', 'pl_bmasselim', VOUNIT.earthMass, null],
  ['semi_major_axis', 'pl_orbsmax', 'pl_orbsmaxerr1', 'pl_orbsmaxerr2', 'pl_orbsmaxlim', VOUNIT.au, null],
  ['eccentricity', 'pl_orbeccen', 'pl_orbeccenerr1', 'pl_orbeccenerr2', 'pl_orbeccenlim', '', null],
  ['transit_midpoint', 'pl_tranmid', 'pl_tranmiderr1', 'pl_tranmiderr2', null, VOUNIT.day, 'BJD_TDB'],
  ['transit_duration', 'pl_trandur', 'pl_trandurerr1', 'pl_trandurerr2', null, VOUNIT.hour, null],
  ['transit_depth', 'pl_trandep', 'pl_trandeperr1', 'pl_trandeperr2', null, 'percent', null],
  ['equilibrium_temperature', 'pl_eqt', null, null, null, VOUNIT.kelvin, null],
  ['insolation', 'pl_insol', null, null, null, '', null],
  ['planet_star_radius_ratio', 'pl_ratror', 'pl_ratrorerr1', 'pl_ratrorerr2', null, '', null],
  ['semi_major_axis_over_stellar_radius', 'pl_ratdor', 'pl_ratdorerr1', 'pl_ratdorerr2', null, '', null],
  ['impact_parameter', 'pl_imppar', 'pl_impparerr1', 'pl_impparerr2', null, '', null],
  ['stellar_radius', 'st_rad', 'st_raderr1', 'st_raderr2', null, VOUNIT.solRad, null],
];
const NASA_TOI_FIELDS = [
  ['orbital_period', 'pl_orbper', 'pl_orbpererr1', 'pl_orbpererr2', null, VOUNIT.day, null],
  ['planet_radius', 'pl_rade', 'pl_radeerr1', 'pl_radeerr2', null, VOUNIT.earthRadius, null],
  ['transit_midpoint', 'pl_tranmid', 'pl_tranmiderr1', 'pl_tranmiderr2', null, VOUNIT.day, 'BJD_TDB'],
  ['transit_duration', 'pl_trandurh', 'pl_trandurherr1', 'pl_trandurherr2', null, VOUNIT.hour, null],
  ['transit_depth', 'pl_trandep', 'pl_trandeperr1', 'pl_trandeperr2', null, 'ppm', null],
  ['equilibrium_temperature', 'pl_eqt', null, null, null, VOUNIT.kelvin, null],
  ['insolation', 'pl_insol', null, null, null, '', null],
  ['planet_star_radius_ratio', 'pl_ratror', 'pl_ratrorerr1', 'pl_ratrorerr2', null, '', null],
  ['semi_major_axis_over_stellar_radius', 'pl_ratdor', 'pl_ratdorerr1', 'pl_ratdorerr2', null, '', null],
  ['impact_parameter', 'pl_imppar', 'pl_impparerr1', 'pl_impparerr2', null, '', null],
  ['stellar_radius', 'st_rad', 'st_raderr1', 'st_raderr2', null, VOUNIT.solRad, null],
];
const NASA_KOI_FIELDS = [
  ['orbital_period', 'koi_period', 'koi_period_err1', 'koi_period_err2', null, VOUNIT.day, null],
  ['planet_radius', 'koi_prad', 'koi_prad_err1', 'koi_prad_err2', null, VOUNIT.earthRadius, null],
  ['semi_major_axis', 'koi_sma', 'koi_sma_err1', 'koi_sma_err2', null, VOUNIT.au, null],
  ['transit_midpoint', 'koi_time0bk', 'koi_time0bk_err1', 'koi_time0bk_err2', null, VOUNIT.day, 'BKJD'],
  ['transit_duration', 'koi_duration', 'koi_duration_err1', 'koi_duration_err2', null, VOUNIT.hour, null],
  ['transit_depth', 'koi_depth', 'koi_depth_err1', 'koi_depth_err2', null, 'ppm', null],
  ['equilibrium_temperature', 'koi_teq', null, null, null, VOUNIT.kelvin, null],
  ['insolation', 'koi_insol', null, null, null, '', null],
  ['impact_parameter', 'koi_impact', 'koi_impact_err1', 'koi_impact_err2', null, '', null],
];
const NASA_K2_FIELDS = NASA_PS_FIELDS.filter(([property]) => !['planet_mass', 'semi_major_axis', 'eccentricity', 'equilibrium_temperature', 'insolation'].includes(property));
const EU_FIELDS = [
  ['planet_mass', 'mass', 'mass_error_max', 'mass_error_min', null, VOUNIT.jupiterMass, null],
  ['planet_mass', 'mass_sini', 'mass_sini_error_max', 'mass_sini_error_min', null, VOUNIT.jupiterMass, null],
  ['planet_radius', 'radius', 'radius_error_max', 'radius_error_min', null, VOUNIT.jupiterRadius, null],
  ['orbital_period', 'orbital_period', 'orbital_period_error_max', 'orbital_period_error_min', null, VOUNIT.day, null],
  ['semi_major_axis', 'semi_major_axis', 'semi_major_axis_error_max', 'semi_major_axis_error_min', null, VOUNIT.au, null],
  ['eccentricity', 'eccentricity', 'eccentricity_error_max', 'eccentricity_error_min', null, '', null],
  ['inclination', 'inclination', 'inclination_error_max', 'inclination_error_min', null, VOUNIT.degree, null],
  ['equilibrium_temperature', 'temp_calculated', null, null, null, VOUNIT.kelvin, null],
  ['equilibrium_temperature', 'temp_measured', null, null, null, VOUNIT.kelvin, null],
];
export const MEASUREMENT_FIELDS = { 'nasa-ps': NASA_PS_FIELDS, 'nasa-pscomppars': NASA_PS_FIELDS, 'nasa-toi': NASA_TOI_FIELDS, 'nasa-koi': NASA_KOI_FIELDS, 'nasa-k2': NASA_K2_FIELDS, 'exoplanet-eu': EU_FIELDS };

function nasaLimit(flag) {
  const n = number(flag);
  if (n === null || n === 0) return 'NONE';
  if (n === 1) return 'LOWER_LIMIT';
  if (n === -1) return 'UPPER_LIMIT';
  return 'UNKNOWN_LIMIT';
}
function massDefinition(sourceId, field, provenance) {
  if (sourceId === 'exoplanet-eu') return field === 'mass_sini' ? 'MINIMUM_MASS_M_SIN_I' : 'MASS';
  if (provenance === 'Msini' || provenance === 'M*sini') return 'MINIMUM_MASS_M_SIN_I';
  if (provenance === 'M-R relationship') return 'MASS_RADIUS_RELATION_ESTIMATE';
  return provenance ? 'MASS' : null;
}

/** Long-format measurement rows for every retained native row of every object. */
export function measurementsTable(detailsById, detectionsById) {
  const names = COLUMN_REGISTRY.measurements.map((spec) => spec.name);
  const rows = [];
  for (const [objectId, detail] of detailsById) {
    const object = detectionsById.get(objectId);
    if (!object) continue;
    const healpix = healpixNested(object.raDeg, object.decDeg);
    for (const record of detail.records) {
      const fields = MEASUREMENT_FIELDS[record.sourceId];
      if (!fields) continue; // rows of sources without a declared field map are not flattened (they stay in the detail shards)
      const values = record.values ?? {};
      for (const [property, field, errUp, errLow, limitField, unit, timeSystem] of fields) {
        const value = number(values[field]);
        if (value === null) continue;
        const reference = record.sourceId === 'nasa-pscomppars' ? (record.references?.[field] ?? null) : record.reference ?? null;
        const publication = record.sourceId === 'exoplanet-eu' ? (values.publication ?? null) : null;
        rows.push([
          objectId, object.name, object.hostName, object.raDeg, object.decDeg, healpix,
          record.sourceId, record.sourceRecordId, record.recordType,
          record.sourceId === 'nasa-ps' ? record.isSourceDefault === true : record.sourceId === 'nasa-k2' && values.default_flag !== undefined && values.default_flag !== null && values.default_flag !== '' ? Number(values.default_flag) === 1 : null,
          property, field, value, errUp ? abs(values[errUp]) : null, errLow ? abs(values[errLow]) : null, unit,
          limitField ? nasaLimit(values[limitField]) : null,
          property === 'planet_mass' ? massDefinition(record.sourceId, field, values.pl_bmassprov ?? null) : null,
          timeSystem,
          reference?.label ?? publication ?? null, reference?.href ?? null,
          record.published ?? values.pl_pubdate ?? values.release_date ?? null, record.updated ?? values.rowupdate ?? values.updated ?? null,
        ]);
      }
    }
  }
  const { specs, rows: cells } = materialize('measurements', rows, names);
  return { specs, rows: sortByHealpix(specs, cells, ['object_id', 'source_id', 'source_record_id', 'property_code', 'source_field']) };
}

export function transitEphemeridesTable(artifact) {
  const names = COLUMN_REGISTRY.transit_ephemerides.map((spec) => spec.name);
  const camel = ['objectId', 'name', 'host', 'raDeg', 'decDeg', 'status', 'periodDays', 'periodErrDays', 't0Bjd', 't0ErrDays', 'durationHours', 'depthPpt', 'hostMag', 'hostMagBand', 'timeSystem', 'sourceId', 'sourceTable', 'sourceRecordId', 'reference', 'referenceUrl', 'matchedVia'];
  if (JSON.stringify(artifact.columns) !== JSON.stringify(camel)) throw new Error('interop: transit ephemeris columns changed; update the export registry');
  const rows = artifact.rows.map((row) => {
    const r = Object.fromEntries(camel.map((key, index) => [key, row[index]]));
    return [r.objectId, r.name, r.host, r.raDeg, r.decDeg, healpixNested(r.raDeg, r.decDeg), r.status, r.periodDays, r.periodErrDays, r.t0Bjd, r.t0ErrDays, r.durationHours, r.depthPpt,
      r.hostMag, r.hostMagBand, r.timeSystem, r.sourceId, r.sourceTable, r.sourceRecordId, r.reference, r.referenceUrl, r.matchedVia];
  });
  const { specs, rows: cells } = materialize('transit_ephemerides', rows, names);
  return { specs, rows: sortByHealpix(specs, cells, ['object_id']) };
}

export function sourceCoverageTable(coverage) {
  const names = COLUMN_REGISTRY.source_coverage.map((spec) => spec.name);
  const rows = Object.values(coverage.sources).map((source) => {
    const d = source.denominators ?? null;
    return [source.sourceId, source.class, source.state, source.snapshotId, source.retrievedAt, join(source.keyedOn, ' | '), source.eligible, source.attached, source.missingAtProvider, source.blocked, source.blockedReason, source.identifierMissing, source.rows, source.attachedRows, source.viaCrossIdentifier,
      d ? d.declared : null, d ? d.applicable : null, d ? d.executed : null, d ? d.dataBearing : null,
      d ? d.objects.declared : null, d ? d.objects.applicable : null, d ? d.objects.executed : null, d ? d.objects.dataBearing : null, d ? d.notComputable : null,
      source.lastAttempt?.at ?? null, source.lastAttempt?.outcome ?? null, source.lastAttempt?.category ?? null, source.lastAttempt?.detail ?? null];
  });
  const { specs, rows: cells } = materialize('source_coverage', rows, names);
  cells.sort((a, b) => compareCell(a[0], b[0]));
  return { specs, rows: cells };
}

export function identityStatesTable(catalogObjects, identityRulesVersion) {
  const names = COLUMN_REGISTRY.identity_states.map((spec) => spec.name);
  const rows = catalogObjects.map((object) => [object.objectId, object.name, object.hostName, object.raDeg, object.decDeg, healpixNested(object.raDeg, object.decDeg), object.identityState, object.systemId, object.primarySourceId, join(object.sourceIds), join(object.sourceObjectIds), object.sourceRecordCount, identityRulesVersion]);
  const { specs, rows: cells } = materialize('identity_states', rows, names);
  return { specs, rows: sortByHealpix(specs, cells, ['object_id']) };
}

export function referencesTable(detailsById) {
  const names = COLUMN_REGISTRY.references.map((spec) => spec.name);
  const index = new Map();
  const add = (sourceId, reference, objectId) => {
    if (!reference) return;
    const label = typeof reference === 'string' ? reference : reference.label ?? null;
    const url = typeof reference === 'string' ? null : reference.href ?? null;
    if (!label && !url) return;
    const key = createHash('sha256').update(`${sourceId}\u0000${label ?? ''}\u0000${url ?? ''}`).digest('hex').slice(0, 16);
    let entry = index.get(key);
    if (!entry) { entry = { key, sourceId, label: label ?? '', url, records: 0, objects: new Set() }; index.set(key, entry); }
    entry.records += 1; entry.objects.add(objectId);
  };
  for (const [objectId, detail] of detailsById) {
    for (const record of detail.records) {
      if (record.sourceId === 'nasa-pscomppars') { for (const reference of Object.values(record.references ?? {})) add(record.sourceId, reference, objectId); continue; }
      if (record.reference) add(record.sourceId, record.reference, objectId);
      else if (record.sourceId === 'exoplanet-eu' && record.values?.publication) add(record.sourceId, record.values.publication, objectId);
    }
  }
  const rows = [...index.values()].map((entry) => [entry.key, entry.sourceId, entry.label, entry.url, entry.records, entry.objects.size, [...entry.objects].sort().slice(0, 5).join(' ')]);
  const { specs, rows: cells } = materialize('references', rows, names);
  cells.sort((a, b) => compareCell(a[1], b[1]) || compareCell(a[2], b[2]) || compareCell(a[0], b[0]));
  return { specs, rows: cells };
}

export function sha256(bytes) { return createHash('sha256').update(bytes).digest('hex'); }

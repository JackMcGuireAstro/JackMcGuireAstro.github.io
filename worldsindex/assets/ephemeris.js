// Transit ephemerides read from one catalog row at a time. A period and a mid-transit epoch
// always come from the same row: values are never combined or averaged across rows or
// sources. Shared by the static builder (worldsindex/data/transit-ephemerides.json.gz), the
// light-curve viewer (folding on the orbital period) and the Tonight's sky planner. No DOM,
// no network: it runs the same in Node and in the browser.

export const BKJD_OFFSET = 2454833.0; // Kepler barycentric time: BKJD = BJD_TDB - 2454833
export const SOURCE_TABLES = {
  'nasa-pscomppars': 'pscomppars', 'nasa-ps': 'ps', 'nasa-toi': 'toi', 'nasa-koi': 'cumulative', 'nasa-k2': 'k2pandc',
};
export const SOURCE_LABELS = {
  'nasa-pscomppars': 'NASA PSCompPars', 'nasa-ps': 'NASA Planetary Systems', 'nasa-toi': 'NASA TESS Objects of Interest',
  'nasa-koi': 'NASA Kepler Objects of Interest (cumulative)', 'nasa-k2': 'NASA K2 Planets and Candidates',
};
// Plausible BJD range (1858-2132); anything else is a reduced or mislabelled time.
const BJD_MIN = 2400000, BJD_MAX = 2500000;

export function num(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}
// Symmetric 1-sigma from an asymmetric pair: the larger magnitude (the conservative choice).
export function symmetricSigma(upper, lower) {
  const both = [num(upper), num(lower)].filter((v) => v !== null).map(Math.abs);
  return both.length ? Math.max(...both) : null;
}
const round = (value, digits) => (value === null ? null : Number(value.toFixed(digits)));
const positive = (value) => (value !== null && value > 0 ? value : null);
const significant = (value, digits = 4) => (value === null ? null : Number(value.toPrecision(digits)));

// Rows a source itself marks as not a planet are never used for an ephemeris.
export function sourceRejects(record) {
  const v = record?.values || {};
  if (record?.sourceId === 'nasa-toi') return ['FP', 'FA'].includes(String(v.tfopwg_disp ?? '').toUpperCase());
  if (record?.sourceId === 'nasa-koi') return /FALSE/i.test(String(v.koi_disposition ?? ''));
  if (record?.sourceId === 'nasa-k2') return /FALSE|REFUTED/i.test(String(v.disposition ?? ''));
  return false;
}

function referenceOf(record) {
  const ref = record?.reference || record?.references?.pl_tranmid || record?.references?.pl_orbper || null;
  if (ref && (ref.label || ref.href)) return { label: ref.label || null, href: ref.href || null };
  return null;
}

// One row -> {periodDays, periodErrDays, t0Bjd, t0ErrDays, durationHours, depthPpt, hostMag,
// hostMagBand, timeSystem, sourceId, sourceRecordId, ...} or null when the row has no usable
// period and mid-transit epoch. Units: period and epoch in days, epoch as BJD_TDB unless
// `timeSystem` says otherwise, duration in hours, depth in parts per thousand (ppt).
export function ephemerisFromRecord(record) {
  if (!record || !SOURCE_TABLES[record.sourceId] || sourceRejects(record)) return null;
  const v = record.values || {};
  let period, periodErr, t0, t0Err, duration, depthPpt = null, hostMag = null, hostMagBand = null, timeSystem = 'BJD_TDB';
  switch (record.sourceId) {
    case 'nasa-ps':
    case 'nasa-pscomppars': {
      if (num(v.pl_orbperlim)) return null; // a limit, not a measured period
      period = num(v.pl_orbper); periodErr = symmetricSigma(v.pl_orbpererr1, v.pl_orbpererr2);
      t0 = num(v.pl_tranmid); t0Err = symmetricSigma(v.pl_tranmiderr1, v.pl_tranmiderr2);
      duration = num(v.pl_trandur);
      depthPpt = num(v.pl_trandep) === null ? null : num(v.pl_trandep) * 10; // PS depth is in percent
      if (num(v.sy_vmag) !== null) { hostMag = num(v.sy_vmag); hostMagBand = 'V'; }
      else if (num(v.sy_tmag) !== null) { hostMag = num(v.sy_tmag); hostMagBand = 'TESS'; }
      if (v.pl_tsystemref) timeSystem = String(v.pl_tsystemref).trim();
      break;
    }
    case 'nasa-toi':
      period = num(v.pl_orbper); periodErr = symmetricSigma(v.pl_orbpererr1, v.pl_orbpererr2);
      t0 = num(v.pl_tranmid); t0Err = symmetricSigma(v.pl_tranmiderr1, v.pl_tranmiderr2);
      duration = num(v.pl_trandurh);
      depthPpt = num(v.pl_trandep) === null ? null : num(v.pl_trandep) / 1000; // TOI depth is in ppm
      if (num(v.st_tmag) !== null) { hostMag = num(v.st_tmag); hostMagBand = 'TESS'; }
      break;
    case 'nasa-koi':
      period = num(v.koi_period); periodErr = symmetricSigma(v.koi_period_err1, v.koi_period_err2);
      t0 = num(v.koi_time0bk) === null ? null : num(v.koi_time0bk) + BKJD_OFFSET;
      t0Err = symmetricSigma(v.koi_time0bk_err1, v.koi_time0bk_err2);
      duration = num(v.koi_duration);
      depthPpt = num(v.koi_depth) === null ? null : num(v.koi_depth) / 1000; // KOI depth is in ppm
      break;
    case 'nasa-k2':
      period = num(v.pl_orbper); periodErr = symmetricSigma(v.pl_orbpererr1, v.pl_orbpererr2);
      t0 = num(v.pl_tranmid); t0Err = symmetricSigma(v.pl_tranmiderr1, v.pl_tranmiderr2);
      duration = num(v.pl_trandur);
      depthPpt = num(v.pl_trandep) === null ? null : num(v.pl_trandep) * 10; // K2 table depth is in percent
      if (v.pl_tsystemref) timeSystem = String(v.pl_tsystemref).trim();
      break;
    default:
      return null;
  }
  if (!(period > 0) || t0 === null || t0 < BJD_MIN || t0 > BJD_MAX) return null;
  return {
    periodDays: period,
    periodErrDays: positive(periodErr),
    t0Bjd: round(t0, 8),
    t0ErrDays: positive(t0Err),
    durationHours: positive(round(duration, 5)),
    depthPpt: positive(significant(depthPpt)),
    hostMag, hostMagBand, timeSystem,
    sourceId: record.sourceId,
    sourceTable: SOURCE_TABLES[record.sourceId],
    sourceRecordId: record.sourceRecordId ?? null,
    isSourceDefault: record.sourceId === 'nasa-pscomppars' ? null : record.isSourceDefault === true || num(v.default_flag) === 1,
    published: record.published ?? v.pl_pubdate ?? null,
    reference: referenceOf(record),
  };
}

// Whole orbits from T0 to the transit nearest `bjd`, that transit's mid-time, and its 1-sigma
// timing uncertainty sqrt(sigmaT0^2 + (n sigmaP)^2) (null when either input is unreported).
export function propagate(eph, bjd) {
  const n = Math.round((bjd - eph.t0Bjd) / eph.periodDays);
  const midBjd = eph.t0Bjd + n * eph.periodDays;
  const sigmaDays = eph.t0ErrDays === null || eph.periodErrDays === null ? null : Math.hypot(eph.t0ErrDays, n * eph.periodErrDays);
  return { epoch: n, midBjd, sigmaDays };
}

// Every transit whose mid-time falls within [startBjd - pad, endBjd + pad], with ingress and
// egress from the catalog duration (null when no duration is reported).
export function transitsBetween(eph, startBjd, endBjd, padDays = 0) {
  if (!(eph?.periodDays > 0) || !Number.isFinite(startBjd) || !Number.isFinite(endBjd)) return [];
  const half = eph.durationHours ? eph.durationHours / 48 : 0;
  const first = Math.ceil((startBjd - padDays - half - eph.t0Bjd) / eph.periodDays);
  const last = Math.floor((endBjd + padDays + half - eph.t0Bjd) / eph.periodDays);
  const out = [];
  for (let n = first; n <= last && out.length < 10000; n += 1) {
    const midBjd = eph.t0Bjd + n * eph.periodDays;
    const sigmaDays = eph.t0ErrDays === null || eph.periodErrDays === null ? null : Math.hypot(eph.t0ErrDays, n * eph.periodErrDays);
    out.push({ epoch: n, midBjd, sigmaDays, ingressBjd: half ? midBjd - half : null, egressBjd: half ? midBjd + half : null });
  }
  return out;
}

const MISSION_ORDER = { 'nasa-toi': 0, 'nasa-k2': 1, 'nasa-koi': 2 };
export function selectionTier(eph) {
  if (eph.sourceId === 'nasa-pscomppars') return 0;
  if (eph.sourceId === 'nasa-ps') return eph.isSourceDefault ? 1 : 2;
  return 3;
}

// Choose one row's ephemeris. Policy, in order:
//  1. NASA PSCompPars (the archive's composite row for the planet);
//  2. the NASA Planetary Systems default parameter set;
//  3. another NASA Planetary Systems row, most recently published first;
//  4. a mission row (TOI, K2, KOI): the smallest timing uncertainty propagated to
//     `referenceBjd`, rows with unreported uncertainties last.
// Ties fall back to a fixed source order and the source-record id, so the choice is
// reproducible. Returns {ephemeris, candidates} (candidates sorted best first).
export function selectEphemeris(records, { referenceBjd = 2461300 } = {}) {
  const candidates = [];
  for (const record of records || []) {
    const eph = record && 'periodDays' in record && 't0Bjd' in record ? record : ephemerisFromRecord(record);
    if (eph) candidates.push(eph);
  }
  const sigmaAt = (eph) => propagate(eph, referenceBjd).sigmaDays;
  candidates.sort((a, b) => {
    const tier = selectionTier(a) - selectionTier(b);
    if (tier) return tier;
    if (selectionTier(a) === 2) {
      const date = String(b.published ?? '').localeCompare(String(a.published ?? ''));
      if (date) return date;
    }
    if (selectionTier(a) === 3) {
      const sa = sigmaAt(a), sb = sigmaAt(b);
      if (sa === null && sb !== null) return 1;
      if (sb === null && sa !== null) return -1;
      if (sa !== null && sb !== null && sa !== sb) return sa - sb;
      const mission = (MISSION_ORDER[a.sourceId] ?? 9) - (MISSION_ORDER[b.sourceId] ?? 9);
      if (mission) return mission;
      if (a.isSourceDefault !== b.isSourceDefault) return a.isSourceDefault ? -1 : 1;
    }
    return String(a.sourceRecordId ?? '').localeCompare(String(b.sourceRecordId ?? ''));
  });
  return { ephemeris: candidates[0] ?? null, candidates };
}

export function describeEphemeris(eph) {
  if (!eph) return '';
  const ref = eph.reference?.label ? ` · ${eph.reference.label}` : '';
  return `${SOURCE_LABELS[eph.sourceId] || eph.sourceId}${ref}`;
}

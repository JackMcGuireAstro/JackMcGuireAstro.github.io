// Solar System bodies are not exoplanets (2026-11-04).
//
// The Open Exoplanet Catalogue carries the Solar System as a system (Mercury … Neptune and
// Pluto, host "Sun", no coordinates), and Exoplanet.eu lists interstellar objects and a
// retrograde co-orbital asteroid under host "Sun" (ISO 1I, 2I, 3I, ISO CNEOS 2014-01-08,
// Ka`epaoka`awela). The builder takes both catalogues as they are, so until this date those 14
// objects were in the WorldsIndex atlas, catalogue, search, populations and exports (ISO 1I even
// as a CONFIRMED direct-imaging planet at RA 0, Dec 0).
//
// The website build (scripts/build_worldsindex_static.mjs) leaves them out of the atlas and of
// every product built from it, and lists them in controversial-retracted-excluded.json.gz under
// the category below with their source rows. The builder state is untouched, so the rule can be
// changed or lifted without a builder release.

export const RULE_VERSION = 'worldsindex-solar-system-exclusion.v1';
export const CATEGORY = 'excluded-solar-system';
export const CATEGORY_TEXT = 'Solar System body or interstellar object — not an exoplanet. The host is the Sun (host name "Sun", "Sol", "The Sun" or "Solar System", any case); the object is left out of the atlas and of every product built from it.';
export const REASON_RULE = `${RULE_VERSION}: host name is the Sun`;
const SUN_HOSTS = new Set(['sun', 'sol', 'the sun', 'solar system']);

export function isSolarSystem(detection) {
  return SUN_HOSTS.has(String(detection?.hostName ?? '').trim().toLowerCase());
}

/** Splits atlas detections into the ones kept and the Solar System ones left out. */
export function partitionDetections(detections) {
  const kept = [], excluded = [];
  for (const detection of detections ?? []) (isSolarSystem(detection) ? excluded : kept).push(detection);
  return { kept, excluded };
}

function countInto(counts, key) { counts[key] = (counts[key] ?? 0) + 1; }
function finiteCoordinate(value) { return typeof value === 'number' && Number.isFinite(value); }

/**
 * The atlas coverage block for a set of detections, by the builder's definitions (verified
 * against the builder's own block on the full atlas before it is used: see coverageMatches).
 */
export function coverageFor(detections) {
  const statusCounts = {}, methodCounts = {}, sourceCounts = {}, identityCounts = {};
  let renderable = 0, occurrences = 0;
  for (const d of detections) {
    if (finiteCoordinate(d.raDeg) && finiteCoordinate(d.decDeg)) renderable += 1;
    occurrences += Number(d.sourceRecordCount ?? 0);
    countInto(statusCounts, d.normalizedStatus);
    for (const code of new Set((d.methodClaims ?? []).map((claim) => claim.methodCode).filter(Boolean))) countInto(methodCounts, code);
    for (const source of new Set(d.sourceIds ?? [])) countInto(sourceCounts, source);
    countInto(identityCounts, d.identityState);
  }
  return { objects: detections.length, renderableObjects: renderable, missingCoordinates: detections.length - renderable,
    sourceRecordOccurrences: occurrences, statusCounts, methodCounts, sourceCounts, identityCounts };
}

function sameCounts(a = {}, b = {}) {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  for (const key of keys) if ((a[key] ?? 0) !== (b[key] ?? 0)) return false;
  return true;
}

/** True when coverageFor reproduces every field of the builder's coverage block it recomputes. */
export function coverageMatches(builderCoverage, recomputed) {
  for (const key of ['objects', 'renderableObjects', 'missingCoordinates', 'sourceRecordOccurrences']) {
    if (key in builderCoverage && builderCoverage[key] !== recomputed[key]) return false;
  }
  for (const key of ['statusCounts', 'methodCounts', 'sourceCounts', 'identityCounts']) {
    if (key in builderCoverage && !sameCounts(builderCoverage[key], recomputed[key])) return false;
  }
  return true;
}

/**
 * The atlas without the Solar System objects, its coverage recomputed by the builder's
 * definitions (fields the recomputation does not cover are carried over unchanged).
 * Throws if the recomputation does not reproduce the builder's block on the full atlas,
 * so a builder change to a coverage definition stops the build instead of publishing
 * inconsistent counts.
 */
export function excludeFromAtlas(atlas) {
  const { kept, excluded } = partitionDetections(atlas.detections);
  if (!excluded.length) return { atlas, excluded };
  if (!coverageMatches(atlas.coverage ?? {}, coverageFor(atlas.detections))) {
    throw new Error('the Solar System exclusion cannot recompute the builder\'s atlas coverage; check scripts/worldsindex_solar_system.mjs coverageFor');
  }
  return { atlas: { ...atlas, coverage: { ...atlas.coverage, ...coverageFor(kept) }, detections: kept }, excluded };
}

/** One exceptions-list entry per excluded object, one reason per source record. */
export function exceptionEntry(detection) {
  const claims = detection.methodClaims ?? [];
  const reasons = (detection.sourceObjectIds ?? [detection.objectId]).map((sourceObjectId) => {
    const claim = claims.find((item) => item.sourceObjectId === sourceObjectId) ?? {};
    return { sourceId: claim.sourceId ?? detection.primarySourceId ?? 'unknown', sourceRecordId: sourceObjectId,
      sourceStatus: `host: ${detection.hostName}`, normalized: 'EXCLUDED', rule: REASON_RULE };
  });
  return { objectId: detection.objectId, name: detection.name, hostName: detection.hostName ?? null,
    atlasStatus: detection.normalizedStatus, categories: [CATEGORY], reasons, discoveryYear: detection.discoveryYear ?? null };
}

/**
 * The builder's exceptions list with the Solar System objects added (an object the builder
 * already listed, e.g. as controversial, keeps its reasons and gains the category).
 */
export function withExcluded(exceptions, excluded) {
  if (!exceptions || !excluded.length) return exceptions;
  const objects = exceptions.objects.map((item) => ({ ...item }));
  const byId = new Map(objects.map((item) => [item.objectId, item]));
  for (const detection of excluded) {
    const entry = exceptionEntry(detection);
    const existing = byId.get(detection.objectId);
    if (existing) {
      if (!existing.categories.includes(CATEGORY)) existing.categories = [...existing.categories, CATEGORY];
      existing.reasons = [...existing.reasons, ...entry.reasons];
    } else { objects.push(entry); byId.set(entry.objectId, entry); }
  }
  const counts = {};
  for (const item of objects) for (const category of item.categories) counts[category] = (counts[category] ?? 0) + 1;
  return { ...exceptions, categories: { ...exceptions.categories, [CATEGORY]: CATEGORY_TEXT }, counts, objects };
}

/**
 * The builder's conflict-set summary without the excluded objects' products. Each excluded
 * product is checked to have no multi-row set, no conflict and no status pairs (true of every
 * Solar System object in the builder state of 2026-11); the counts it did contribute (objects,
 * rows, property blocks, rows by property and by source, flags) are subtracted, and the
 * exceptions counts are replaced by those of the list actually published. A product outside
 * that shape stops the build rather than leave a summary that disagrees with the shards.
 */
export function adjustSummary(summary, excludedProducts, exceptions) {
  if (!summary || !excludedProducts.length) return summary;
  const out = structuredClone(summary);
  const less = (object, key, by) => { if (object && typeof object[key] === 'number') object[key] -= by; };
  for (const product of excludedProducts) {
    if ((product.properties ?? []).some((block) => block.set) || product.status?.conflict || (product.status?.assertions ?? []).length > 1) {
      throw new Error(`the Solar System object ${product.objectId} has comparable rows; the summary adjustment does not cover it`);
    }
    const rows = (product.properties ?? []).reduce((sum, block) => sum + block.rows.length, 0);
    less(out, 'objects', 1);
    if (rows) less(out, 'objectsWithRows', 1);
    less(out, 'assertionRows', rows);
    less(out, 'conflictSets', (product.properties ?? []).length);
    for (const block of product.properties ?? []) {
      const entry = out.byProperty?.[block.code];
      less(entry, 'sets', 1);
      less(entry, 'rows', block.rows.length);
      for (const row of block.rows) less(out.bySource, product.records?.[row.record]?.sourceId, 1);
    }
    for (const flag of product.flags ?? []) less(out.flags, flag.flag, 1);
  }
  if (exceptions) out.exceptions = { ...exceptions.counts };
  out.solarSystemExclusion = { ruleVersion: RULE_VERSION, objects: excludedProducts.length,
    note: 'Counts exclude the Solar System objects left out of the atlas by the website build; the builder\'s own summary includes them.' };
  return out;
}

/** The manifest's additive `solarSystemExclusion` block. */
export function manifestBlock(excluded) {
  return { ruleVersion: RULE_VERSION, rule: CATEGORY_TEXT, category: CATEGORY, objects: excluded.length,
    names: excluded.map((d) => d.name).sort((a, b) => a.localeCompare(b)) };
}

#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync, gzipSync } from 'node:zlib';
import { extractTransitEphemerides, SCHEMA_VERSION as TRANSIT_EPHEMERIDES_SCHEMA } from './worldsindex_transit_ephemerides.mjs';
import { tmpdir } from 'node:os';
import * as interop from './worldsindex_interop.mjs';
import { atomFeed, CHANGELOG_SCHEMA_VERSION, diffReleases, jsonFeed, loadPreviousRelease, renderChangelogMarkdown } from './worldsindex_changelog.mjs';
import { provenanceDocument, PROVENANCE_SCHEMA_VERSION } from './worldsindex_provenance.mjs';

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = resolve(process.env.WORLDSINDEX_SOURCE_DIR ?? '/Users/johnmcguire/Documents/Codex/CTAS and WorldsIndex/WorldsIndex Development/work/worldsindex');
const outputRoot = join(siteRoot, 'worldsindex', 'data');
const detailRoot = join(outputRoot, 'details');
// One release clock for the manifest, the changelog, the feeds and the provenance document.
const releaseGeneratedAt = new Date().toISOString();

await mkdir(detailRoot, { recursive: true });
await copyFile(join(sourceRoot, 'public/data/sky-detections.json.gz'), join(outputRoot, 'sky-detections.json.gz'));
if (process.env.WORLDSINDEX_SKIP_MONITOR_COPY !== '1') {
  // The monitor's public status, plus the latest recorded outcome of each source-specific
  // promotion gate (the monitor cannot promote; the gate runs after it in the publisher).
  const monitorStatus = JSON.parse(await readFile(join(sourceRoot, 'public/data/sync/latest.json'), 'utf8'));
  const promotionLatest = await readFile(join(sourceRoot, 'outputs/promotion/exoplanet-eu/latest.json'), 'utf8').then(JSON.parse).catch(() => null);
  if (monitorStatus.datasetPromotion && typeof monitorStatus.datasetPromotion === 'object') {
    monitorStatus.datasetPromotion.latestOutcome = promotionLatest;
  }
  await writeFile(join(outputRoot, 'source-monitor.json'), `${JSON.stringify(monitorStatus, null, 2)}\n`);
}
// Every consumer of the Exoplanet.eu projection reads the active-snapshot pointer.
const exoplanetEuPointer = JSON.parse(await readFile(join(sourceRoot, 'data/snapshots/exoplanet-eu/ACTIVE.json'), 'utf8'));
if (exoplanetEuPointer.schemaVersion !== 'worldsindex-active-snapshot.v1' || !/^[A-Za-z0-9][A-Za-z0-9._-]*$/.test(exoplanetEuPointer.directory)) throw new Error('Unsupported Exoplanet.eu active-snapshot pointer');

function loadGzipJson(relativePath) {
  return readFile(join(sourceRoot, relativePath)).then((bytes) => JSON.parse(gunzipSync(bytes).toString('utf8')));
}

function splitJsonArrayRecords(raw) {
  const container = JSON.parse(raw);
  if (!Array.isArray(container)) throw new Error('Expected a JSON array snapshot');
  const records = [];
  let depth = 0, start = -1, inString = false, escaped = false;
  for (let index = 0; index < raw.length; index += 1) {
    const char = raw[index];
    if (inString) {
      if (escaped) escaped = false;
      else if (char === '\\') escaped = true;
      else if (char === '"') inString = false;
      continue;
    }
    if (char === '"') { inString = true; continue; }
    if (char === '{') { if (depth === 0) start = index; depth += 1; }
    else if (char === '}') {
      depth -= 1;
      if (depth === 0 && start >= 0) {
        const rawPayload = raw.slice(start, index + 1);
        records.push({ rawPayload, value: JSON.parse(rawPayload) });
        start = -1;
      }
    }
  }
  if (depth !== 0 || inString || records.length !== container.length) throw new Error('Could not preserve source JSON row boundaries');
  return records;
}

function loadGzipJsonRecords(relativePath) {
  return readFile(join(sourceRoot, relativePath)).then((bytes) => splitJsonArrayRecords(gunzipSync(bytes).toString('utf8')));
}

function clean(value) {
  return value === '' || value === undefined ? null : value;
}

function stripLink(value) {
  if (!value) return null;
  const href = String(value).match(/href=([^ >]+)/)?.[1] ?? null;
  const label = String(value).replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ').trim();
  return { label, href };
}

function selected(row, fields) {
  return Object.fromEntries(fields.map((field) => [field, clean(row[field])]));
}

// Transit columns the frozen NASA PS / PSCompPars projections do not carry yet (the
// 2026-08-23 snapshot has no pl_tranmid, durations, depths or magnitudes). They are copied
// only when a snapshot row has them, so older snapshots produce byte-identical shards.
const OPTIONAL_TRANSIT_FIELDS = ['pl_trandur','pl_trandurerr1','pl_trandurerr2','pl_trandep','pl_trandeperr1','pl_trandeperr2','pl_tsystemref','sy_vmag','sy_tmag'];
function optional(row, fields) {
  return Object.fromEntries(fields.filter((field) => Object.hasOwn(row, field)).map((field) => [field, clean(row[field])]));
}

function nasaPublished(row, sourceRecordId) {
  const record = {
    sourceId: 'nasa-ps',
    sourceRecordId,
    recordType: 'published solution',
    name: row.pl_name,
    hostName: row.hostname,
    reference: stripLink(row.pl_refname),
    updated: row.rowupdate,
    published: row.pl_pubdate,
    isSourceDefault: row.default_flag === 1,
    values: selected(row, [
      'pl_tranmid','pl_tranmiderr1','pl_tranmiderr2','pl_ratror','pl_ratrorerr1','pl_ratrorerr2','pl_ratdor','pl_ratdorerr1','pl_ratdorerr2','pl_imppar','pl_impparerr1','pl_impparerr2','st_rad','st_raderr1','st_raderr2','pl_orbper','pl_orbpererr1','pl_orbpererr2','pl_orbperlim',
      'pl_rade','pl_radeerr1','pl_radeerr2','pl_radelim',
      'pl_bmasse','pl_bmasseerr1','pl_bmasseerr2','pl_bmasselim','pl_bmassprov',
      'pl_orbsmax','pl_orbsmaxerr1','pl_orbsmaxerr2','pl_orbsmaxlim',
      'pl_orbeccen','pl_orbeccenerr1','pl_orbeccenerr2','pl_orbeccenlim','pl_insol','pl_eqt',
    ]),
  };
  Object.assign(record.values, optional(row, OPTIONAL_TRANSIT_FIELDS));
  return record;
}

function nasaComposite(row, sourceRecordId) {
  const references = {};
  for (const key of ['pl_orbper','pl_rade','pl_bmasse','pl_orbsmax','pl_orbeccen','pl_insol','pl_eqt']) {
    references[key] = stripLink(row[`${key}_reflink`]);
  }
  if (Object.hasOwn(row, 'pl_tranmid_reflink')) references.pl_tranmid = stripLink(row.pl_tranmid_reflink);
  const record = {
    sourceId: 'nasa-pscomppars', sourceRecordId, recordType: 'source composite', name: row.pl_name, hostName: row.hostname,
    references,
    // tic_id (the host's TESS Input Catalog number) lets the light-curve viewer find the
    // host's TESS/Kepler/K2 files at MAST by identifier rather than by position.
    values: selected(row, ['tic_id',
      'pl_tranmid','pl_tranmiderr1','pl_tranmiderr2','pl_ratror','pl_ratrorerr1','pl_ratrorerr2','pl_ratdor','pl_ratdorerr1','pl_ratdorerr2','pl_imppar','pl_impparerr1','pl_impparerr2','st_rad','st_raderr1','st_raderr2','pl_orbper','pl_orbpererr1','pl_orbpererr2','pl_orbperlim',
      'pl_rade','pl_radeerr1','pl_radeerr2','pl_radelim',
      'pl_bmasse','pl_bmasseerr1','pl_bmasseerr2','pl_bmasselim','pl_bmassprov',
      'pl_orbsmax','pl_orbsmaxerr1','pl_orbsmaxerr2','pl_orbsmaxlim',
      'pl_orbeccen','pl_orbeccenerr1','pl_orbeccenerr2','pl_orbeccenlim','pl_insol','pl_eqt',
    ]),
  };
  Object.assign(record.values, optional(row, OPTIONAL_TRANSIT_FIELDS));
  return record;
}

function candidateName(table, row) {
  if (table === 'toi') return `TOI-${row.toi}`;
  if (table === 'cumulative') return row.kepler_name || row.kepoi_name;
  return row.pl_name || row.k2_name || row.epic_candname;
}

function candidateAttachmentNames(table, row) {
  if (table === 'toi') return [`TOI-${row.toi}`, row.toipfx];
  if (table === 'cumulative') return [row.kepoi_name, row.kepler_name];
  return [row.epic_candname, row.pl_name, row.k2_name];
}

function candidateRecord(table, row, sourceRecordId) {
  const fieldSets = {
    toi: ['toi','tid','ctoi_alias','tfopwg_disp','pl_tranmid','pl_tranmiderr1','pl_tranmiderr2','pl_ratror','pl_ratrorerr1','pl_ratrorerr2','pl_ratdor','pl_ratdorerr1','pl_ratdorerr2','pl_imppar','pl_impparerr1','pl_impparerr2','st_rad','st_raderr1','st_raderr2','pl_orbper','pl_orbpererr1','pl_orbpererr2','pl_tranmid','pl_tranmiderr1','pl_tranmiderr2','pl_trandurh','pl_trandurherr1','pl_trandurherr2','pl_trandep','pl_trandeperr1','pl_trandeperr2','pl_rade','pl_radeerr1','pl_radeerr2','pl_insol','pl_eqt','st_tmag','st_dist','st_teff','st_logg','st_rad','sectors','rowupdate','release_date'],
    cumulative: ['kepid','kepoi_name','kepler_name','koi_disposition','koi_pdisposition','koi_score','koi_delivname','koi_quarters','koi_num_transits','koi_model_snr','koi_time0bk','koi_time0bk_err1','koi_time0bk_err2','koi_prad','koi_prad_err1','koi_prad_err2','koi_sma','koi_sma_err1','koi_sma_err2','koi_impact','koi_impact_err1','koi_impact_err2','koi_duration','koi_duration_err1','koi_duration_err2','koi_depth','koi_depth_err1','koi_depth_err2','koi_period','koi_period_err1','koi_period_err2','koi_teq','koi_insol','koi_tce_plnt_num','koi_tce_delivname'],
    k2pandc: ['pl_name','hostname','epic_candname','epic_hostname','k2_name','tic_id','disposition','default_flag','disc_year','discoverymethod','disc_facility','k2_campaigns','pl_tranmid','pl_tranmiderr1','pl_tranmiderr2','pl_ratror','pl_ratrorerr1','pl_ratrorerr2','pl_ratdor','pl_ratdorerr1','pl_ratdorerr2','pl_imppar','pl_impparerr1','pl_impparerr2','st_rad','st_raderr1','st_raderr2','pl_orbper','pl_orbpererr1','pl_orbpererr2','pl_tranmid','pl_tranmiderr1','pl_tranmiderr2','pl_trandur','pl_trandurerr1','pl_trandurerr2','pl_trandep','pl_trandeperr1','pl_trandeperr2','pl_rade','pl_radeerr1','pl_radeerr2','rowupdate','pl_pubdate','releasedate'],
  };
  return { sourceId: table === 'toi' ? 'nasa-toi' : table === 'cumulative' ? 'nasa-koi' : 'nasa-k2', sourceRecordId, recordType: 'mission candidate row', name: candidateName(table, row), values: selected(row, fieldSets[table]) };
}

function parseCsv(text) {
  const rows = [];
  let row = [], field = '', quoted = false;
  for (let i = 0; i < text.length; i += 1) {
    const c = text[i];
    if (quoted) {
      if (c === '"' && text[i + 1] === '"') { field += '"'; i += 1; }
      else if (c === '"') quoted = false;
      else field += c;
    } else if (c === '"') quoted = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n') { row.push(field.replace(/\r$/, '')); rows.push(row); row = []; field = ''; }
    else field += c;
  }
  if (field || row.length) { row.push(field); rows.push(row); }
  const header = rows.shift() ?? [];
  return rows.filter((values) => values.some(Boolean)).map((values) => Object.fromEntries(header.map((key, i) => [key, values[i] ?? ''])));
}

function normalized(value) {
  return String(value ?? '').normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function bucketFor(value) {
  return createHash('sha256').update(value).digest('hex').slice(0, 2);
}

const [atlas, psRecords, compositeRecords, toiRecords, koiRecords, k2Records] = await Promise.all([
  loadGzipJson('public/data/sky-detections.json.gz'),
  loadGzipJsonRecords('data/snapshots/nasa/2026-08-23-full/ps.json.gz'),
  loadGzipJsonRecords('data/snapshots/nasa/2026-08-23-full/pscomppars.json.gz'),
  loadGzipJsonRecords('data/snapshots/nasa/2026-08-23-expanded/toi.json.gz'),
  loadGzipJsonRecords('data/snapshots/nasa/2026-08-23-expanded/cumulative.json.gz'),
  loadGzipJsonRecords('data/snapshots/nasa/2026-08-23-expanded/k2pandc.json.gz'),
]);
function countBy(items) {
  const counts = {};
  for (const item of items) counts[item] = (counts[item] ?? 0) + 1;
  return counts;
}

// NASA context the builder attached by exact NASA keys (ExoNexus scripts/build-source-contributions.ts):
// provider-stated aliases plus stellar-host, transit, microlensing and spectrum rows. Optional, so a
// builder without it still produces a valid release.
const loadedContributions = await readFile(join(sourceRoot, 'public/data/source-contributions.json.gz'))
  .then((bytes) => JSON.parse(gunzipSync(bytes).toString('utf8')))
  .catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
// A file left over from another atlas projection (or an unknown schema) is ignored, never half-applied.
const contributions = loadedContributions && loadedContributions.schemaVersion === 'worldsindex-source-contributions.v1' && loadedContributions.atlasProjectionId === atlas.projectionId ? loadedContributions : null;
if (loadedContributions && !contributions) console.warn('source-contributions.json.gz ignored: built for another atlas projection or schema');
// The builder's object × source coverage summary (ExoNexus packages/exonexus/archive/source-coverage.ts):
// per source, objects eligible / attached / missing at the provider / blocked. Optional; published
// beside the release so the gap between "could carry" and "carries" is visible over time.
const loadedCoverage = await readFile(join(sourceRoot, 'public/data/source-coverage.json'))
  .then((bytes) => JSON.parse(bytes.toString('utf8')))
  .catch((error) => { if (error.code === 'ENOENT') return null; throw error; });
const sourceCoverage = loadedCoverage && loadedCoverage.schemaVersion === 'worldsindex-source-coverage.v1' ? loadedCoverage : null;
if (loadedCoverage && !sourceCoverage) console.warn('source-coverage.json ignored: unknown schema');
const contributedAliases = (objectId) => [...new Set((contributions?.objects?.[objectId]?.aliases ?? []).map((alias) => alias.value))];
const catalogObjects = atlas.detections.map((detection) => ({
  objectId: detection.objectId,
  sourceObjectIds: detection.sourceObjectIds,
  systemId: detection.systemId,
  name: detection.name,
  hostName: detection.hostName,
  raDeg: detection.raDeg,
  decDeg: detection.decDeg,
  normalizedStatus: detection.normalizedStatus,
  methodCode: detection.methodCode,
  methodCodes: [...new Set((detection.methodClaims ?? []).map((claim) => claim.methodCode).filter(Boolean))],
  discoveryYear: detection.discoveryYear,
  discoveryFacility: detection.discoveryFacility,
  identityState: detection.identityState,
  primarySourceId: detection.primarySourceId,
  sourceIds: detection.sourceIds,
  sourceRecordCount: detection.sourceRecordCount,
  ...(contributedAliases(detection.objectId).length ? { aliases: contributedAliases(detection.objectId) } : {}),
}));
const primaryMethodCounts = countBy(catalogObjects.map((object) => object.methodCode).filter(Boolean));
const claimMethodCounts = countBy(catalogObjects.flatMap((object) => object.methodCodes));
const catalogIndex = {
  schemaName: 'WorldsIndex catalog index',
  schemaVersion: 'worldsindex-catalog-index.v1',
  generatedAt: atlas.generatedAt,
  projectionId: atlas.projectionId,
  identityNotice: atlas.identityNotice,
  coordinateNotice: atlas.coordinateNotice,
  coverage: {
    ...atlas.coverage,
    primaryMethodCounts,
    claimMethodCounts,
  },
  objects: catalogObjects,
};
const catalogIndexBytes = gzipSync(JSON.stringify(catalogIndex), { level: 9 });
await writeFile(join(outputRoot, 'catalog-index.json.gz'), catalogIndexBytes);

const details = new Map(atlas.detections.map((detection) => [detection.objectId, { objectId: detection.objectId, atlasRecord: detection, records: [] }]));
const objectIdsByName = new Map();
for (const detection of atlas.detections) {
  for (const alias of [detection.name, ...detection.sourceObjectIds]) {
    const key = normalized(alias);
    if (!objectIdsByName.has(key)) objectIdsByName.set(key, new Set());
    objectIdsByName.get(key).add(detection.objectId);
  }
}
function attachByName(name, record, alsoObjectAlias = null) {
  const ids = new Set(objectIdsByName.get(normalized(name)) ?? []);
  // A row the builder linked under another name (e.g. an exoplanet.eu row resolved to its
  // NASA planet) is found through the builder's own source-object id for it.
  if (alsoObjectAlias) for (const id of objectIdsByName.get(normalized(alsoObjectAlias)) ?? []) ids.add(id);
  for (const id of ids) details.get(id)?.records.push(record);
}
for (const [index, record] of psRecords.entries()) {
  const sourceRecordId = `source-ps-${String(index + 1).padStart(6, '0')}-${createHash('sha256').update(record.rawPayload).digest('hex').slice(0, 16)}`;
  attachByName(record.value.pl_name, nasaPublished(record.value, sourceRecordId));
}
for (const [index, record] of compositeRecords.entries()) {
  const sourceRecordId = `source-pscomppars-${String(index + 1).padStart(6, '0')}-${createHash('sha256').update(record.rawPayload).digest('hex').slice(0, 16)}`;
  attachByName(record.value.pl_name, nasaComposite(record.value, sourceRecordId));
}
for (const [table, records] of [['toi', toiRecords], ['cumulative', koiRecords], ['k2pandc', k2Records]]) {
  for (const [index, record] of records.entries()) {
    const detailRecord = candidateRecord(table, record.value, `${table}:${index}`);
    for (const name of new Set(candidateAttachmentNames(table, record.value).filter(Boolean))) attachByName(name, detailRecord);
  }
}

const exoplanetEu = parseCsv(await readFile(join(sourceRoot, 'data/snapshots/exoplanet-eu', exoplanetEuPointer.directory, 'catalog.csv'), 'utf8'));
const euFields = ['planet_status','mass','mass_error_min','mass_error_max','mass_sini','mass_sini_error_min','mass_sini_error_max','radius','radius_error_min','radius_error_max','orbital_period','orbital_period_error_min','orbital_period_error_max','semi_major_axis','semi_major_axis_error_min','semi_major_axis_error_max','eccentricity','eccentricity_error_min','eccentricity_error_max','inclination','inclination_error_min','inclination_error_max','discovered','updated','temp_calculated','temp_measured','publication','detection_type','mass_measurement_type','radius_measurement_type','alternate_names','molecules','star_name','ra','dec','star_distance','star_mass','star_radius','star_teff'];
for (const row of exoplanetEu) {
  const slug = row.name.normalize('NFKD').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
  const sourceRecordId = `exoplanet-eu-${slug}-${createHash('sha256').update(JSON.stringify(row)).digest('hex').slice(0, 12)}`;
  attachByName(row.name, { sourceId: 'exoplanet-eu', sourceRecordId, recordType: 'catalog row', name: row.name, values: selected(row, euFields) }, `object-${sourceRecordId}`);
}

for (const [objectId, contribution] of Object.entries(contributions?.objects ?? {})) {
  const detail = details.get(objectId);
  if (!detail) throw new Error(`source contributions name an object absent from the atlas: ${objectId}`);
  if (contribution.aliases?.length) detail.aliases = contribution.aliases;
  for (const record of contribution.records ?? []) detail.records.push(record);
}

// Materialize every bucket on every release. This prevents a removed record
// from leaving an obsolete shard behind in the static site checkout.
const buckets = new Map(Array.from({ length: 256 }, (_, index) => [index.toString(16).padStart(2, '0'), {}]));
for (const [objectId, detail] of details) {
  const bucket = bucketFor(objectId);
  buckets.get(bucket)[objectId] = detail;
}
for (const [bucket, payload] of buckets) {
  await writeFile(join(detailRoot, `${bucket}.json.gz`), gzipSync(JSON.stringify(payload), { level: 9 }));
}

// One transit ephemeris per transiting planet (period and mid-transit epoch from one
// catalog row; see scripts/worldsindex_transit_ephemerides.mjs). Stamped with the atlas time
// so the file changes only when the atlas or its catalog rows change.
const { artifact: transitEphemerides, stats: transitStats } = extractTransitEphemerides({
  detections: atlas.detections, detailsById: details, generatedAt: atlas.generatedAt, sourceSnapshots: atlas.sourceSnapshots ?? [],
});
await writeFile(join(outputRoot, 'transit-ephemerides.json.gz'), gzipSync(JSON.stringify(transitEphemerides), { level: 9 }));

// A context source whose frozen snapshot attached rows in this release is presented as INGESTED
// (releaseSourceRegistry); builders without that helper keep the declared registry.
const registryCode = `import * as sourceRegistry from './packages/exonexus/archive/source-registry.ts'; import { DETECTION_METHODS, DETECTION_METHOD_REGISTRY_VERSION } from './packages/exonexus/methods/registry.ts'; const contributionSources = JSON.parse(process.env.WORLDSINDEX_CONTRIBUTION_SOURCES || 'null'); const entries = typeof sourceRegistry.releaseSourceRegistry === 'function' ? sourceRegistry.releaseSourceRegistry(sourceRegistry.SOURCE_REGISTRY, contributionSources) : sourceRegistry.SOURCE_REGISTRY; const stateCounts = Object.fromEntries([...new Set(entries.map((entry) => entry.state))].sort().map((state) => [state, entries.filter((entry) => entry.state === state).length])); console.log(JSON.stringify({sources:{version:sourceRegistry.SOURCE_UNIVERSE_VERSION,stateCounts,entries},methods:{version:DETECTION_METHOD_REGISTRY_VERSION,entries:DETECTION_METHODS}}));`;
const registry = JSON.parse(execFileSync(process.execPath, ['--import', 'tsx', '--input-type=module', '-e', registryCode], { cwd: sourceRoot, encoding: 'utf8', maxBuffer: 20_000_000, env: { ...process.env, WORLDSINDEX_CONTRIBUTION_SOURCES: JSON.stringify(contributions?.sources ?? null) } }));
await writeFile(join(outputRoot, 'registry.json.gz'), gzipSync(JSON.stringify(registry), { level: 9 }));

async function describeArtifact(relativePath) {
  const bytes = await readFile(join(outputRoot, relativePath));
  return [relativePath, { bytes: bytes.byteLength, sha256: createHash('sha256').update(bytes).digest('hex') }];
}
// Every file this builder leaves under worldsindex/data, keyed by path relative to that
// directory. The publisher refuses to commit any release whose declared artifacts are not
// all in its allowlist, and the static test verifies each hash, so a future artifact cannot
// be shipped as a manifest reference without the bytes that back it.
// Validated observation products are copied from the local scientific archive.
const lightcurvePaths = [];
const indexPath = join(sourceRoot, 'public/data/lightcurves/index.json');
const indexText = await readFile(indexPath, 'utf8').catch(error => { if (error.code === 'ENOENT') return null; throw error; });
if (indexText !== null) {
  const index = JSON.parse(indexText);
  await mkdir(join(outputRoot, 'lightcurves'), { recursive: true });
  for (const entry of Object.values(index.entries)) {
    if (!/^(?:HAT-P-\d+b|K2-EPIC246199087-C12)\.json\.gz$/.test(entry.path)) throw new Error('Invalid observation artifact path');
    const bytes = await readFile(join(sourceRoot, 'public/data/lightcurves', entry.path));
    if (createHash('sha256').update(bytes).digest('hex') !== entry.sha256) throw new Error('Light curve checksum mismatch');
    await writeFile(join(outputRoot, 'lightcurves', entry.path), bytes);
    lightcurvePaths.push(`lightcurves/${entry.path}`);
  }
  await copyFile(indexPath, join(outputRoot, 'lightcurves/index.json'));
  lightcurvePaths.push('lightcurves/index.json');
}
const coveragePaths = [];
if (sourceCoverage) {
  await writeFile(join(outputRoot, 'source-coverage.json'), `${JSON.stringify(sourceCoverage, null, 2)}\n`);
  coveragePaths.push('source-coverage.json');
}
// ---- Interoperability exports (Framework v2, Track 1) ---------------------------------------
// ECSV 1.0 and VOTable 1.5 of every public table (scripts/worldsindex_interop.mjs), gzip-
// compressed like the JSON artifacts (Astropy and TOPCAT read .gz transparently); VOParquet
// copies of the objects and measurements tables are written outside the Pages tree
// (WORLDSINDEX_RELEASE_ASSETS_DIR) for the publisher to upload as release assets, and
// bulk-downloads.json names them with sizes and SHA-256 digests. Every export is a manifest
// artifact; none is an input to anything above.
const exportsRoot = join(outputRoot, 'exports');
await mkdir(exportsRoot, { recursive: true });
const detectionsById = new Map(atlas.detections.map((detection) => [detection.objectId, detection]));
const detailCounts = new Map([...details].map(([objectId, detail]) => [objectId, { records: detail.records.length, spectra: detail.records.filter((record) => record.sourceId === 'nasa-atmospheric-spectroscopy').length }]));
const identityRulesVersion = await readFile(join(sourceRoot, 'data/atlas/release-contract.json'), 'utf8').then((text) => JSON.parse(text)?.sources?.['exoplanet-eu']?.identityRulesVersion ?? null).catch(() => null);
const exportTables = {
  objects: interop.objectsTable(catalogObjects, detectionsById, detailCounts),
  measurements: interop.measurementsTable(details, detectionsById),
  transit_ephemerides: interop.transitEphemeridesTable(transitEphemerides),
  ...(sourceCoverage ? { source_coverage: interop.sourceCoverageTable(sourceCoverage) } : {}),
  identity_states: interop.identityStatesTable(catalogObjects, identityRulesVersion ?? ''),
  references: interop.referencesTable(details),
};
const exportMeta = { release_atlas_generated_at: atlas.generatedAt, atlas_projection_id: atlas.projectionId, source_universe_version: registry.sources.version, site: `${interop.SITE_BASE_URL}/worldsindex/` };
const exportIndex = { schemaVersion: interop.EXPORTS_SCHEMA_VERSION, generatedAt: atlas.generatedAt, atlasProjectionId: atlas.projectionId, formats: { ecsv: interop.ECSV_FORMAT_NOTE, votable: interop.VOTABLE_FORMAT_NOTE }, claimBoundary: interop.INTEROP_CLAIM, tables: {} };
const exportPaths = ['exports/index.json'];
for (const [table, { specs, rows }] of Object.entries(exportTables)) {
  const ecsvBytes = gzipSync(interop.ecsv(table, specs, rows, exportMeta), { level: 6 });
  const votBytes = gzipSync(interop.votable(table, specs, rows, exportMeta), { level: 6 }); // level 6: a quarter of the time of 9 for the 190 MB measurements VOTable, 5% larger
  await writeFile(join(exportsRoot, `${table}.ecsv.gz`), ecsvBytes);
  await writeFile(join(exportsRoot, `${table}.vot.gz`), votBytes);
  exportPaths.push(`exports/${table}.ecsv.gz`, `exports/${table}.vot.gz`);
  exportIndex.tables[table] = {
    description: interop.TABLE_DESCRIPTIONS[table], rows: rows.length,
    files: { ecsv: { path: `exports/${table}.ecsv.gz`, bytes: ecsvBytes.byteLength, sha256: interop.sha256(ecsvBytes) }, votable: { path: `exports/${table}.vot.gz`, bytes: votBytes.byteLength, sha256: interop.sha256(votBytes) } },
    columns: specs.map((spec) => ({ name: spec.name, datatype: spec.datatype, ucd: spec.ucd, unit: spec.unit, description: spec.description })),
  };
}
// VOParquet release assets, built from the ECSV files by scripts/worldsindex_parquet.py (astropy + pyarrow).
const releaseRepository = process.env.WORLDSINDEX_RELEASE_REPO || (() => { try { return execFileSync('git', ['-C', siteRoot, 'remote', 'get-url', 'origin'], { encoding: 'utf8' }).trim().replace(/^(git@github\.com:|https:\/\/github\.com\/)/, '').replace(/\.git$/, ''); } catch { return 'JackMcGuireAstro/JackMcGuireAstro.github.io'; } })();
const releaseTag = process.env.WORLDSINDEX_RELEASE_TAG || 'worldsindex-data-latest';
const releaseAssetsDir = resolve(process.env.WORLDSINDEX_RELEASE_ASSETS_DIR || join(tmpdir(), 'worldsindex-release-assets'));
const parquetScript = join(siteRoot, 'scripts', 'worldsindex_parquet.py');
let parquetAvailable = null;
try { execFileSync('python3', [parquetScript, '--check'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }); parquetAvailable = true; } catch (error) { parquetAvailable = `${String(error.stdout || error.message).trim().split('\n').pop()}`; }
const releaseAssets = [];
if (parquetAvailable === true) {
  await mkdir(releaseAssetsDir, { recursive: true });
  for (const table of ['objects', 'measurements']) {
    const output = join(releaseAssetsDir, `worldsindex-${table}.parquet`);
    const result = JSON.parse(execFileSync('python3', [parquetScript, '--ecsv', join(exportsRoot, `${table}.ecsv.gz`), '--output', output, '--name', table], { encoding: 'utf8', maxBuffer: 10_000_000 }).trim().split('\n').pop());
    releaseAssets.push({ name: result.name, table, rows: result.rows, columns: result.columns, bytes: result.bytes, sha256: result.sha256, format: 'VOParquet 1.0 (Apache Parquet, zstd, IVOA.VOTable-Parquet.version/content metadata), rows sorted by healpix_8_nested', downloadUrl: `https://github.com/${releaseRepository}/releases/download/${releaseTag}/${result.name}`, sourceEcsvSha256: exportIndex.tables[table].files.ecsv.sha256 });
  }
}
const bulkDownloads = {
  schemaVersion: 'worldsindex-bulk-downloads.v1', generatedAt: atlas.generatedAt, atlasProjectionId: atlas.projectionId,
  releaseTag, releaseUrl: `https://github.com/${releaseRepository}/releases/tag/${releaseTag}`, repository: releaseRepository,
  statement: 'Bulk tables are published as assets of one rolling GitHub Release, not in the GitHub Pages tree. The publisher replaces each asset after the release is built; verify the SHA-256 below against the downloaded file, because an asset can lag the Pages release by one cycle while an upload is in flight or has failed.',
  claimBoundary: interop.INTEROP_CLAIM,
  unavailableReason: parquetAvailable === true ? null : `VOParquet assets were not built in this run: ${parquetAvailable}`,
  assets: releaseAssets,
};
await writeFile(join(outputRoot, 'bulk-downloads.json'), `${JSON.stringify(bulkDownloads, null, 2)}\n`);

// ---- Changelog and feeds against the previous published release ----------------------------
// The publisher names the previous release (WORLDSINDEX_RELEASE_BASE_REF, resolved in
// WORLDSINDEX_RELEASE_BASE_GIT_DIR, its data store); a local build falls back to this
// checkout's origin/worldsindex-data when it exists. Without a base nothing is listed as changed.
const baseGitDir = process.env.WORLDSINDEX_RELEASE_BASE_GIT_DIR || (() => { try { return resolve(siteRoot, execFileSync('git', ['-C', siteRoot, 'rev-parse', '--git-dir'], { encoding: 'utf8' }).trim()); } catch { return null; } })();
const baseRef = process.env.WORLDSINDEX_RELEASE_BASE_REF || 'origin/worldsindex-data';
const previousRelease = loadPreviousRelease({ gitDir: baseGitDir, ref: baseRef });
const sourceMonitorNow = await readFile(join(outputRoot, 'source-monitor.json'), 'utf8').then(JSON.parse).catch(() => null);
const preManifest = { generatedAt: releaseGeneratedAt, atlasGeneratedAt: atlas.generatedAt, objectCount: atlas.coverage.objects, detailRecordCount: [...details.values()].reduce((sum, item) => sum + item.records.length, 0) };
const changelog = diffReleases({ manifest: preManifest, catalogIndex, sourceCoverage, sourceMonitor: sourceMonitorNow, detailCounts }, previousRelease);
await writeFile(join(outputRoot, 'changelog.json'), `${JSON.stringify(changelog, null, 2)}\n`);
await writeFile(join(outputRoot, 'changelog.md'), renderChangelogMarkdown(changelog));
const feed = jsonFeed(changelog, previousRelease?.feed ?? null);
await writeFile(join(outputRoot, 'feed.json'), `${JSON.stringify(feed, null, 2)}\n`);
await writeFile(join(outputRoot, 'feed.xml'), atomFeed(feed));
await writeFile(join(exportsRoot, 'index.json'), `${JSON.stringify(exportIndex, null, 2)}\n`);

const artifactPaths = [
  'sky-detections.json.gz',
  'catalog-index.json.gz',
  'registry.json.gz',
  'transit-ephemerides.json.gz',
  ...coveragePaths,
  ...lightcurvePaths,
  'source-monitor.json',
  ...exportPaths,
  'bulk-downloads.json',
  'changelog.json',
  'changelog.md',
  'feed.json',
  'feed.xml',
  ...[...buckets.keys()].sort().map((bucket) => `details/${bucket}.json.gz`),
].sort();
const artifactsBeforeProvenance = Object.fromEntries(await Promise.all(artifactPaths.map(describeArtifact)));

// ---- W3C PROV-JSON for this build (scripts/worldsindex_provenance.mjs) --------------------
const gitHead = (directory) => { try { return execFileSync('git', ['-C', directory, 'rev-parse', 'HEAD'], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim(); } catch { return null; } };
const builderPackage = await readFile(join(sourceRoot, 'package.json'), 'utf8').then(JSON.parse).catch(() => ({}));
const releaseContract = await readFile(join(sourceRoot, 'data/atlas/release-contract.json'), 'utf8').then(JSON.parse).catch(() => null);
const identityPins = [];
for (const name of ['reviewed-reidentification.json', 'reviewed-reidentification-v3.json']) {
  const pin = await readFile(join(sourceRoot, 'packages/exonexus/identity', name), 'utf8').then(JSON.parse).catch(() => null);
  if (pin && pin.schemaVersion === 'worldsindex-reviewed-reidentification.v1') identityPins.push({ fromRules: pin.fromRules, toRules: pin.toRules, snapshotId: pin.snapshotId, transitionsSha256: pin.transitionsSha256, reviewedAt: pin.reviewedAt, reviewedBy: pin.reviewedBy });
}
const cloudRunMarker = process.env.WORLDSINDEX_CLOUD_RUN_MARKER ? await readFile(process.env.WORLDSINDEX_CLOUD_RUN_MARKER, 'utf8').then(JSON.parse).catch(() => null) : null;
const provenance = provenanceDocument({
  generatedAt: releaseGeneratedAt,
  manifest: { schemaVersion: 'worldsindex-static-release.v1', objectCount: atlas.coverage.objects, detailRecordCount: preManifest.detailRecordCount },
  artifacts: artifactsBeforeProvenance,
  atlas: { projectionId: atlas.projectionId, generatedAt: atlas.generatedAt, schemaVersion: atlas.schemaVersion, sourceSnapshots: atlas.sourceSnapshots ?? [] },
  registry, sourceCoverage, sourceMonitor: sourceMonitorNow,
  identity: { rulesVersion: identityRulesVersion ?? 'unknown', contract: releaseContract ? { reviewedAt: releaseContract.reviewedAt, reviewedBy: releaseContract.reviewedBy, snapshotId: releaseContract.sources?.['exoplanet-eu']?.snapshotId ?? null } : null, pins: identityPins },
  builder: { commit: gitHead(sourceRoot), version: builderPackage.version ?? null, name: builderPackage.description ?? 'ExoNexus (WorldsIndex builder)' },
  site: { commit: gitHead(siteRoot), repository: releaseRepository },
  stateAsset: cloudRunMarker, releaseAssets,
});
await writeFile(join(outputRoot, 'provenance.json'), `${JSON.stringify(provenance, null, 2)}\n`);
artifactPaths.push('provenance.json');
artifactPaths.sort();
const artifacts = Object.fromEntries(Object.entries({ ...artifactsBeforeProvenance, ...Object.fromEntries([await describeArtifact('provenance.json')]) }).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)));

const manifest = {
  schemaVersion: 'worldsindex-static-release.v1',
  generatedAt: releaseGeneratedAt,
  atlasGeneratedAt: atlas.generatedAt,
  objectCount: atlas.coverage.objects,
  renderableObjectCount: atlas.coverage.renderableObjects,
  sourceRecordOccurrences: atlas.coverage.sourceRecordOccurrences,
  detailObjectCount: [...details.values()].filter((item) => item.records.length).length,
  detailRecordCount: [...details.values()].reduce((sum, item) => sum + item.records.length, 0),
  detailShards: [...buckets.keys()].sort(),
  coverage: catalogIndex.coverage,
  catalogIndexSchemaVersion: catalogIndex.schemaVersion,
  catalogIndexObjectCount: catalogObjects.length,
  catalogIndexBytes: catalogIndexBytes.byteLength,
  catalogIndexSha256: createHash('sha256').update(catalogIndexBytes).digest('hex'),
  sourceUniverseVersion: registry.sources.version,
  methodRegistryVersion: registry.methods.version,
  atlasSha256: createHash('sha256').update(await readFile(join(outputRoot, 'sky-detections.json.gz'))).digest('hex'),
  transitEphemerides: {
    path: 'transit-ephemerides.json.gz', schemaVersion: TRANSIT_EPHEMERIDES_SCHEMA, count: transitEphemerides.count,
    referenceBjd: transitEphemerides.referenceBjd, bySource: transitStats.bySource, crossMatchedToi: transitStats.crossMatchedToi,
  },
  sourceContributions: contributions ? { schemaVersion: contributions.schemaVersion, generatedAt: contributions.generatedAt, boundary: contributions.boundary, sources: contributions.sources } : null,
  sourceCoverage: sourceCoverage ? { path: 'source-coverage.json', schemaVersion: sourceCoverage.schemaVersion, generatedAt: sourceCoverage.generatedAt, sources: Object.keys(sourceCoverage.sources ?? {}).length } : null,
  // Framework v2 Track 1 (additive): machine description, exports, bulk assets, provenance, changelog and feeds.
  apiDescriptionUrl: `${interop.SITE_BASE_URL}/worldsindex/openapi.yaml`,
  schemasUrl: `${interop.SITE_BASE_URL}/worldsindex/schemas/`,
  exports: { path: 'exports/index.json', schemaVersion: exportIndex.schemaVersion, tables: Object.fromEntries(Object.entries(exportIndex.tables).map(([table, entry]) => [table, { rows: entry.rows, ecsv: entry.files.ecsv.path, votable: entry.files.votable.path }])) },
  bulkDownloads: { path: 'bulk-downloads.json', schemaVersion: bulkDownloads.schemaVersion, releaseTag, assets: releaseAssets.map((asset) => asset.name), unavailableReason: bulkDownloads.unavailableReason },
  provenancePath: 'provenance.json',
  provenanceSchemaVersion: PROVENANCE_SCHEMA_VERSION,
  changelog: { path: 'changelog.json', markdownPath: 'changelog.md', schemaVersion: CHANGELOG_SCHEMA_VERSION, comparable: changelog.comparable, previousReleaseCommit: previousRelease?.commit ?? null, summary: changelog.summary },
  feeds: { json: 'feed.json', atom: 'feed.xml', items: feed.items.length },
  artifacts,
  scientificBoundary: 'Static public catalog projection. Source membership is not independent confirmation; source-composite values are not self-consistent publication solutions; catalog projections are not completeness-corrected populations.',
};
await writeFile(join(outputRoot, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`WorldsIndex static data: ${manifest.objectCount.toLocaleString()} objects, ${manifest.detailRecordCount.toLocaleString()} retained native rows, ${manifest.detailShards.length} detail shards, ${transitEphemerides.count.toLocaleString()} transit ephemerides.`);

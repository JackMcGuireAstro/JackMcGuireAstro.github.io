#!/usr/bin/env node

import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { copyFile, mkdir, readFile, writeFile } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync, gzipSync } from 'node:zlib';

const siteRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = resolve(process.env.WORLDSINDEX_SOURCE_DIR ?? '/Users/johnmcguire/Documents/Codex/CTAS and WorldsIndex/WorldsIndex Development/work/worldsindex');
const outputRoot = join(siteRoot, 'worldsindex', 'data');
const detailRoot = join(outputRoot, 'details');

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

function nasaPublished(row, sourceRecordId) {
  return {
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
      'pl_orbper','pl_orbpererr1','pl_orbpererr2','pl_orbperlim',
      'pl_rade','pl_radeerr1','pl_radeerr2','pl_radelim',
      'pl_bmasse','pl_bmasseerr1','pl_bmasseerr2','pl_bmasselim','pl_bmassprov',
      'pl_orbsmax','pl_orbsmaxerr1','pl_orbsmaxerr2','pl_orbsmaxlim',
      'pl_orbeccen','pl_orbeccenerr1','pl_orbeccenerr2','pl_orbeccenlim','pl_insol','pl_eqt',
    ]),
  };
}

function nasaComposite(row, sourceRecordId) {
  const references = {};
  for (const key of ['pl_orbper','pl_rade','pl_bmasse','pl_orbsmax','pl_orbeccen','pl_insol','pl_eqt']) {
    references[key] = stripLink(row[`${key}_reflink`]);
  }
  return {
    sourceId: 'nasa-pscomppars', sourceRecordId, recordType: 'source composite', name: row.pl_name, hostName: row.hostname,
    references,
    values: selected(row, [
      'pl_orbper','pl_orbpererr1','pl_orbpererr2','pl_orbperlim',
      'pl_rade','pl_radeerr1','pl_radeerr2','pl_radelim',
      'pl_bmasse','pl_bmasseerr1','pl_bmasseerr2','pl_bmasselim','pl_bmassprov',
      'pl_orbsmax','pl_orbsmaxerr1','pl_orbsmaxerr2','pl_orbsmaxlim',
      'pl_orbeccen','pl_orbeccenerr1','pl_orbeccenerr2','pl_orbeccenlim','pl_insol','pl_eqt',
    ]),
  };
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
    toi: ['toi','tid','ctoi_alias','tfopwg_disp','pl_orbper','pl_orbpererr1','pl_orbpererr2','pl_tranmid','pl_tranmiderr1','pl_tranmiderr2','pl_trandurh','pl_trandurherr1','pl_trandurherr2','pl_trandep','pl_trandeperr1','pl_trandeperr2','pl_rade','pl_radeerr1','pl_radeerr2','pl_insol','pl_eqt','st_tmag','st_dist','st_teff','st_logg','st_rad','sectors','rowupdate','release_date'],
    cumulative: ['kepid','kepoi_name','kepler_name','koi_disposition','koi_pdisposition','koi_score','koi_delivname','koi_quarters','koi_num_transits','koi_model_snr','koi_time0bk','koi_time0bk_err1','koi_time0bk_err2','koi_prad','koi_prad_err1','koi_prad_err2','koi_sma','koi_sma_err1','koi_sma_err2','koi_impact','koi_impact_err1','koi_impact_err2','koi_duration','koi_duration_err1','koi_duration_err2','koi_depth','koi_depth_err1','koi_depth_err2','koi_period','koi_period_err1','koi_period_err2','koi_teq','koi_insol','koi_tce_plnt_num','koi_tce_delivname'],
    k2pandc: ['pl_name','hostname','epic_candname','epic_hostname','k2_name','tic_id','disposition','default_flag','disc_year','discoverymethod','disc_facility','k2_campaigns','pl_orbper','pl_orbpererr1','pl_orbpererr2','pl_tranmid','pl_tranmiderr1','pl_tranmiderr2','pl_trandur','pl_trandurerr1','pl_trandurerr2','pl_trandep','pl_trandeperr1','pl_trandeperr2','pl_rade','pl_radeerr1','pl_radeerr2','rowupdate','pl_pubdate','releasedate'],
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
function attachByName(name, record) {
  for (const id of objectIdsByName.get(normalized(name)) ?? []) details.get(id)?.records.push(record);
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
  attachByName(row.name, { sourceId: 'exoplanet-eu', sourceRecordId, recordType: 'catalog row', name: row.name, values: selected(row, euFields) });
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

const registryCode = `import { SOURCE_REGISTRY, SOURCE_UNIVERSE_VERSION, sourceStateCounts } from './packages/exonexus/archive/source-registry.ts'; import { DETECTION_METHODS, DETECTION_METHOD_REGISTRY_VERSION } from './packages/exonexus/methods/registry.ts'; console.log(JSON.stringify({sources:{version:SOURCE_UNIVERSE_VERSION,stateCounts:sourceStateCounts(),entries:SOURCE_REGISTRY},methods:{version:DETECTION_METHOD_REGISTRY_VERSION,entries:DETECTION_METHODS}}));`;
const registry = JSON.parse(execFileSync(process.execPath, ['--import', 'tsx', '--input-type=module', '-e', registryCode], { cwd: sourceRoot, encoding: 'utf8', maxBuffer: 20_000_000 }));
await writeFile(join(outputRoot, 'registry.json.gz'), gzipSync(JSON.stringify(registry), { level: 9 }));

async function describeArtifact(relativePath) {
  const bytes = await readFile(join(outputRoot, relativePath));
  return [relativePath, { bytes: bytes.byteLength, sha256: createHash('sha256').update(bytes).digest('hex') }];
}
// Every file this builder leaves under worldsindex/data, keyed by path relative to that
// directory. The publisher refuses to commit any release whose declared artifacts are not
// all in its allowlist, and the static test verifies each hash, so a future artifact cannot
// be shipped as a manifest reference without the bytes that back it.
const artifactPaths = [
  'sky-detections.json.gz',
  'catalog-index.json.gz',
  'registry.json.gz',
  'source-monitor.json',
  ...[...buckets.keys()].sort().map((bucket) => `details/${bucket}.json.gz`),
].sort();
const artifacts = Object.fromEntries(await Promise.all(artifactPaths.map(describeArtifact)));

const manifest = {
  schemaVersion: 'worldsindex-static-release.v1',
  generatedAt: new Date().toISOString(),
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
  artifacts,
  scientificBoundary: 'Static public catalog projection. Source membership is not independent confirmation; source-composite values are not self-consistent publication solutions; catalog projections are not completeness-corrected populations.',
};
await writeFile(join(outputRoot, 'manifest.json'), `${JSON.stringify(manifest, null, 2)}\n`);
console.log(`WorldsIndex static data: ${manifest.objectCount.toLocaleString()} objects, ${manifest.detailRecordCount.toLocaleString()} retained native rows, ${manifest.detailShards.length} detail shards.`);

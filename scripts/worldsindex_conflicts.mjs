#!/usr/bin/env node
// WorldsIndex conflict sets and selections in the static release (Framework v2, Track 2a).
//
// The ExoNexus builder (scripts/build-conflict-sets.ts, run by `npm run sky:generate`) writes
//   public/data/conflict-sets.ndjson.gz             one header line, then one worldsindex-conflict-sets.v1
//                                                     product per object (sorted by objectId)
//   public/data/conflict-summary.json               the release summary (counts per property, relation, source pair)
//   public/data/controversial-retracted-excluded.json  the release-wide list with reasons and sources
// This module turns them into release artifacts without holding the whole product in memory:
//   conflicts/<xx>.json.gz        256 shards keyed like details/<xx>.json.gz (first two hex digits of
//                                 sha256(objectId)), so the Compare tab fetches one small file;
//   conflict-index.json.gz        every set in conflict (object, property, relation, max sigma), the
//                                 changelog's base for "new" and "resolved" conflicts;
//   conflict-summary.json, controversial-retracted-excluded.json.gz (copies, schema-checked);
//   the flat `conflicts` and `selections` export tables (scripts/worldsindex_interop.mjs registry).
// All three inputs are optional: without them the release has no conflict block, no Compare tab and
// no new tables (the manifest's `conflictSets` is null), exactly as before Track 2a.

import { createHash } from 'node:crypto';
import { createReadStream, createWriteStream } from 'node:fs';
import { mkdir, readFile, stat, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { once } from 'node:events';
import { createInterface } from 'node:readline';
import { createGunzip, createGzip, gzipSync } from 'node:zlib';
import { healpixNested, materialize, sortByHealpix, COLUMN_REGISTRY } from './worldsindex_interop.mjs';

export const CONFLICT_SETS_SCHEMA_VERSION = 'worldsindex-conflict-sets.v1';
export const CONFLICT_SUMMARY_SCHEMA_VERSION = 'worldsindex-conflict-summary.v1';
export const STATUS_EXCEPTIONS_SCHEMA_VERSION = 'worldsindex-status-exceptions.v1';
export const CONFLICT_INDEX_SCHEMA_VERSION = 'worldsindex-conflict-index.v1';
export const CONFLICT_SHARD_PATTERN = 'conflicts/{shard}.json.gz';
export const INPUTS = { product: 'public/data/conflict-sets.ndjson.gz', summary: 'public/data/conflict-summary.json', exceptions: 'public/data/controversial-retracted-excluded.json' };
export const VOUNIT_FOR = { day: 'd', d: 'd', hour: 'h', R_earth: '6378100m', M_earth: '5.9721679e+24kg', au: 'AU', deg: 'deg', K: 'K', percent: 'percent', dimensionless: '', S_earth: '' };

export function bucketFor(objectId) { return createHash('sha256').update(objectId).digest('hex').slice(0, 2); }

/** Why a builder header cannot be consumed (null when it can). */
export function rejectHeader(header, projectionId) {
  if (!header || typeof header !== 'object') return 'no header line';
  if (header.schemaVersion !== CONFLICT_SETS_SCHEMA_VERSION) return `unsupported schema ${header.schemaVersion}`;
  if (header.atlasProjectionId !== projectionId) return `built for atlas ${header.atlasProjectionId}, not ${projectionId}`;
  return null;
}

const join_ = (values, separator = ' ') => [...new Set((values ?? []).filter((value) => value !== null && value !== undefined && value !== ''))].join(separator);

/** Conflict-table rows of one published product (sets with two or more rows, and a disputed status). */
export function conflictRows(product, detection, policyVersion) {
  const healpix = healpixNested(detection?.raDeg, detection?.decDeg);
  const head = [product.objectId, product.name, product.hostName ?? detection?.hostName ?? null, detection?.raDeg ?? null, detection?.decDeg ?? null, healpix];
  const out = [];
  for (const block of product.properties ?? []) {
    const set = block.set;
    if (!set) continue;
    const label = (index) => { const row = block.rows[index]; const record = row ? product.records[row.record] : null; return record ? `${record.sourceRecordId}/${row.field}` : String(index); };
    out.push([...head, set.id, block.code, VOUNIT_FOR[block.unit] ?? block.unit, set.rowCount, set.sourceIds.length, join_(set.sourceIds), join_(set.definitionCodes),
      set.relationLabel ?? null, set.relation, set.conflict, set.sigmaThreshold, set.maxPairwiseSigma ?? null, set.pairCounts.total,
      set.pairCounts.byVerdict.consistent, set.pairCounts.byVerdict.inconsistent, set.pairCounts.byVerdict['not-comparable'],
      Object.entries(set.pairCounts.byRelation).sort(([a], [b]) => (a < b ? -1 : 1)).map(([relation, count]) => `${relation}=${count}`).join('; '),
      (set.notablePairs ?? []).map(([a, b, sigma]) => `${label(a)} x ${label(b)}: ${sigma === null ? 'n/a' : sigma}`).join(' | '),
      set.explanation, set.comparisonPolicyVersion ?? policyVersion]);
  }
  const status = product.status;
  if (status && status.assertions.length >= 2) {
    const sources = join_(status.assertions.map((item) => item.sourceId).sort());
    const values = [...new Set(status.assertions.map((item) => item.normalized))].sort();
    const pairs = status.assertions.length * (status.assertions.length - 1) / 2;
    let disagreeing = 0;
    for (let i = 0; i < status.assertions.length; i += 1) for (let j = i + 1; j < status.assertions.length; j += 1) if (status.assertions[i].normalized !== status.assertions[j].normalized) disagreeing += 1;
    out.push([...head, `conflict-${product.objectId}-object_status`, 'object_status', '', status.assertions.length, new Set(status.assertions.map((item) => item.sourceId)).size, sources, 'status',
      status.relation ?? null, status.conflict ? 'STATUS_CONFLICT' : 'AGREEMENT', status.conflict, null, null, pairs, pairs - disagreeing, disagreeing, 0,
      status.relation ? `${status.relation}=${disagreeing}; none=${pairs - disagreeing}` : `none=${pairs}`,
      status.conflict ? status.assertions.map((item) => `${item.sourceRecordId}: ${item.sourceStatus}`).join(' | ') : '',
      status.conflict ? `The linked sources state different statuses (${values.join(' vs ')}).` : `The linked sources agree on ${values[0]}.`, policyVersion]);
  }
  return out;
}

/** Selection-table rows of one published product (values actually selected; "not available" is not a row). */
export function selectionRows(product, detection) {
  const healpix = healpixNested(detection?.raDeg, detection?.decDeg);
  const head = [product.objectId, product.name, product.hostName ?? detection?.hostName ?? null, detection?.raDeg ?? null, detection?.decDeg ?? null, healpix];
  const out = [];
  for (const block of product.properties ?? []) {
    for (const selection of block.selections ?? []) {
      if (selection.basis === 'not-available' || selection.value === null || selection.value === undefined) continue;
      const row = selection.chosenRow === null || selection.chosenRow === undefined ? null : block.rows[selection.chosenRow];
      const record = row ? product.records[row.record] : null;
      const reference = record && record.reference !== undefined ? product.references[record.reference] : null;
      const composite = selection.archiveComposite ?? null;
      out.push([...head, selection.id, block.code, selection.definitionCode, selection.label, selection.value, VOUNIT_FOR[selection.unit] ?? selection.unit,
        selection.errPlus ?? null, selection.errMinus ?? null, selection.limitType, selection.basis, record?.sourceId ?? null, join_(selection.chosenSourceRecordIds), row?.field ?? null,
        reference?.label ?? null, reference?.url ?? null, reference?.bibcode ?? null, record?.published ?? null,
        selection.ruleId, selection.ruleVersion, selection.selector, selection.selectedAt, selection.reviewState, selection.reason,
        Boolean(selection.calculated), selection.calculated?.formulaId ?? null, selection.calculated?.formula ?? null,
        selection.calculated ? selection.calculated.inputs.map((input) => `${input.name}=${input.value} ${input.unit} (${input.sourceRecordId})`).join('; ') : null,
        composite?.value ?? null, composite && composite.value !== null && composite.value !== undefined ? Boolean(composite.matches) : null]);
    }
  }
  return out;
}

/** Index entries for every set in conflict: [objectId, propertyCode, relation, maxSigma]. */
export function conflictIndexEntries(product) {
  const out = [];
  for (const block of product.properties ?? []) if (block.set?.conflict) out.push([product.objectId, block.code, block.set.relationLabel, block.set.maxPairwiseSigma ?? null]);
  if (product.status?.conflict) out.push([product.objectId, 'object_status', product.status.relation, null]);
  return out;
}

async function readJson(path) {
  try { return JSON.parse(await readFile(path, 'utf8')); } catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

/**
 * Streams the builder product into the 256 conflict shards and collects the flat-table rows.
 * Returns null (with a reason) when the product is absent or belongs to another atlas.
 */
export async function buildConflictArtifacts({ sourceRoot, outputRoot, atlas, detectionsById }) {
  const productPath = join(sourceRoot, INPUTS.product);
  try { await stat(productPath); } catch (error) { if (error.code === 'ENOENT') return { artifacts: null, reason: 'no conflict-sets.ndjson.gz from the builder' }; throw error; }
  const lines = createInterface({ input: createReadStream(productPath).pipe(createGunzip()), crlfDelay: Infinity });
  let header = null;
  const shardRoot = join(outputRoot, 'conflicts');
  await mkdir(shardRoot, { recursive: true });
  const shards = new Map();
  const conflicts = [], selections = [], index = [];
  let objects = 0, assertionRows = 0, multiRowSets = 0, setsInConflict = 0, objectsInConflict = 0, selectedValues = 0;
  const open = (bucket) => {
    let shard = shards.get(bucket);
    if (!shard) {
      const gzip = createGzip({ level: 9 });
      const sink = createWriteStream(join(shardRoot, `${bucket}.json.gz`));
      gzip.pipe(sink);
      shard = { gzip, sink, count: 0 };
      shards.set(bucket, shard);
    }
    return shard;
  };
  const write = async (shard, text) => { if (!shard.gzip.write(text)) await once(shard.gzip, 'drain'); };
  for await (const line of lines) {
    if (!line) continue;
    if (header === null) {
      header = JSON.parse(line);
      const reason = rejectHeader(header, atlas.projectionId);
      if (reason) { lines.close(); for (const shard of shards.values()) shard.gzip.destroy(); return { artifacts: null, reason }; }
      continue;
    }
    const product = JSON.parse(line);
    const detection = detectionsById.get(product.objectId);
    if (!detection) throw new Error(`conflict sets name an object absent from the atlas: ${product.objectId}`);
    const shard = open(bucketFor(product.objectId));
    await write(shard, `${shard.count ? ',' : '{'}${JSON.stringify(product.objectId)}:${line}`);
    shard.count += 1;
    objects += 1;
    let inConflict = Boolean(product.status?.conflict);
    for (const block of product.properties ?? []) {
      assertionRows += block.rows.length;
      if (block.set) multiRowSets += 1;
      if (block.set?.conflict) { setsInConflict += 1; inConflict = true; }
      selectedValues += (block.selections ?? []).filter((item) => item.basis !== 'not-available').length;
    }
    if (inConflict) objectsInConflict += 1;
    conflicts.push(...conflictRows(product, detection, header.policy?.version ?? null));
    selections.push(...selectionRows(product, detection));
    index.push(...conflictIndexEntries(product));
  }
  if (header === null) return { artifacts: null, reason: 'conflict-sets.ndjson.gz is empty' };
  // Every bucket exists on every release (as details/ does), so a shard never goes stale.
  for (let i = 0; i < 256; i += 1) {
    const bucket = i.toString(16).padStart(2, '0');
    const shard = open(bucket);
    await write(shard, shard.count ? '}' : '{}');
    shard.gzip.end();
    await once(shard.sink, 'finish');
  }
  const summary = await readJson(join(sourceRoot, INPUTS.summary));
  if (summary && (summary.schemaVersion !== CONFLICT_SUMMARY_SCHEMA_VERSION || summary.atlasProjectionId !== atlas.projectionId)) throw new Error('conflict-summary.json does not belong to this conflict product');
  const exceptions = await readJson(join(sourceRoot, INPUTS.exceptions));
  if (exceptions && (exceptions.schemaVersion !== STATUS_EXCEPTIONS_SCHEMA_VERSION || exceptions.atlasProjectionId !== atlas.projectionId)) throw new Error('controversial-retracted-excluded.json does not belong to this conflict product');
  const paths = [...Array.from({ length: 256 }, (_, i) => `conflicts/${i.toString(16).padStart(2, '0')}.json.gz`)];
  if (summary) { await writeFile(join(outputRoot, 'conflict-summary.json'), `${JSON.stringify(summary, null, 2)}\n`); paths.push('conflict-summary.json'); }
  if (exceptions) { await writeFile(join(outputRoot, 'controversial-retracted-excluded.json.gz'), gzipSync(JSON.stringify(exceptions), { level: 9 })); paths.push('controversial-retracted-excluded.json.gz'); }
  index.sort((a, b) => (a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : a[1] < b[1] ? -1 : a[1] > b[1] ? 1 : 0));
  const conflictIndex = { schemaVersion: CONFLICT_INDEX_SCHEMA_VERSION, generatedAt: header.generatedAt, atlasProjectionId: header.atlasProjectionId, sigmaThreshold: header.policy?.sigmaThreshold ?? null, columns: ['objectId', 'propertyCode', 'relation', 'maxSigma'], sets: index };
  await writeFile(join(outputRoot, 'conflict-index.json.gz'), gzipSync(JSON.stringify(conflictIndex), { level: 9 }));
  paths.push('conflict-index.json.gz');
  const conflictNames = COLUMN_REGISTRY.conflicts.map((spec) => spec.name);
  const selectionNames = COLUMN_REGISTRY.selections.map((spec) => spec.name);
  const conflictTable = materialize('conflicts', conflicts, conflictNames);
  const selectionTable = materialize('selections', selections, selectionNames);
  return {
    reason: null,
    artifacts: {
      header, summary, exceptions, conflictIndex, paths,
      tables: { conflicts: { specs: conflictTable.specs, rows: sortByHealpix(conflictTable.specs, conflictTable.rows, ['object_id', 'property_code']) }, selections: { specs: selectionTable.specs, rows: sortByHealpix(selectionTable.specs, selectionTable.rows, ['object_id', 'property_code', 'definition_code']) } },
      counts: { objects, assertionRows, multiRowSets, setsInConflict, objectsInConflict, selectedValues, conflictRows: conflicts.length, selectionRows: selections.length },
    },
  };
}

/** The manifest's `conflictSets` block (additive). */
export function manifestBlock(artifacts) {
  const { header, summary, exceptions, counts } = artifacts;
  return {
    schemaVersion: header.schemaVersion, generatedAt: header.generatedAt, policyVersion: header.policy?.version ?? null, sigmaThreshold: header.policy?.sigmaThreshold ?? null,
    selectionRule: header.policy?.selectionRule ?? null, relationVocabulary: header.policy?.relationVocabulary ?? [],
    shardPathPattern: CONFLICT_SHARD_PATTERN, shards: 256, objects: counts.objects, assertionRows: counts.assertionRows, multiRowSets: counts.multiRowSets,
    setsInConflict: counts.setsInConflict, objectsInConflict: counts.objectsInConflict, selectedValues: counts.selectedValues,
    summaryPath: summary ? 'conflict-summary.json' : null, exceptionsPath: exceptions ? 'controversial-retracted-excluded.json.gz' : null, indexPath: 'conflict-index.json.gz',
    exceptions: exceptions?.counts ?? null, boundary: header.boundary,
  };
}

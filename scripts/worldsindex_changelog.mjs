#!/usr/bin/env node
// Release-to-release changelog and feeds for WorldsIndex (Framework v2, Track 1, part B).
//
// The publisher knows the previous release (the tip of the worldsindex-data branch, read
// through the data store's git object database) and the static builder knows the release
// it has just built. diffReleases() names, in plain words and as JSON, what changed:
// objects added and removed, status changes (candidate -> confirmed promotions; demotions
// to FALSE_POSITIVE or REFUTED), identity-state changes, native rows and spectra added
// per object, and per-source state changes (snapshot retrieved or refused, quarantines,
// blocked -> attached). Everything is derived from the two releases' own files, so the
// result is deterministic and clock-free apart from the release time the manifest carries.
//
// The feeds (JSON Feed 1.1 and Atom) carry one item per changed planet and one summary item
// per release with changes, newest first; items from earlier releases are carried forward
// from the previous release's feed.json (bounded), so a reader polling the feed sees history
// without the builder keeping any state of its own.
//
// Previous-release files are optional: without a base (first release, or a store that has
// no worldsindex-data branch yet) the changelog says so and lists nothing as changed.

import { gunzipSync } from 'node:zlib';
import { execFileSync } from 'node:child_process';

export const CHANGELOG_SCHEMA_VERSION = 'worldsindex-changelog.v1';
export const SITE_BASE_URL = 'https://jackmcguireastro.github.io';
export const FEED_PATH = 'worldsindex/data/feed.json';
export const ATOM_PATH = 'worldsindex/data/feed.xml';
export const CHANGELOG_PATH = 'worldsindex/data/changelog.json';
export const LIST_CAP = 200; // named entries per section in changelog.json; counts are always complete
export const FEED_ITEMS_PER_KIND = 100; // items per kind per release
export const FEED_ITEM_CAP = 1000; // items carried in the feed in all
export const DEMOTED_STATUSES = new Set(['FALSE_POSITIVE', 'REFUTED']);
export const PLANET_STATUSES = new Set(['CONFIRMED', 'CANDIDATE', 'CONTROVERSIAL']);

// ------------------------------------------------------------------ previous release access
/** Read one file of the previous release from a git object database; null when absent. */
export function gitBlob(gitDir, ref, path) {
  try {
    return execFileSync('git', ['--git-dir', gitDir, 'cat-file', 'blob', `${ref}:${path}`], { maxBuffer: 512 * 1024 * 1024, stdio: ['ignore', 'pipe', 'ignore'] });
  } catch { return null; }
}
export function gitCommit(gitDir, ref) {
  try { return execFileSync('git', ['--git-dir', gitDir, 'rev-parse', '--verify', `${ref}^{commit}`], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim(); } catch { return null; }
}
const parseMaybeGzipJson = (bytes) => bytes === null ? null : JSON.parse((bytes[0] === 0x1f && bytes[1] === 0x8b ? gunzipSync(bytes) : bytes).toString('utf8'));

/** Minimal CSV parser for the builder's own ECSV output (RFC 4180 quoting). */
export function parseEcsv(text) {
  const lines = [];
  let row = [], field = '', quoted = false, started = false;
  const body = text.split('\n').filter((line) => !line.startsWith('#')).join('\n');
  for (let i = 0; i < body.length; i += 1) {
    const c = body[i];
    if (quoted) {
      if (c === '"' && body[i + 1] === '"') { field += '"'; i += 1; } else if (c === '"') quoted = false; else field += c;
    } else if (c === '"') { quoted = true; started = true; } else if (c === ',') { row.push(field); field = ''; started = false; } else if (c === '\n') { row.push(field); lines.push(row); row = []; field = ''; started = false; } else { field += c; started = true; }
  }
  if (field || started || row.length) { row.push(field); lines.push(row); }
  const header = lines.shift() ?? [];
  return lines.filter((values) => values.length === header.length).map((values) => Object.fromEntries(header.map((key, index) => [key, values[index]])));
}

/** Everything the diff needs from the previous release, read from git (any part may be null). */
export function loadPreviousRelease({ gitDir, ref }) {
  if (!gitDir || !ref) return null;
  const commit = gitCommit(gitDir, ref);
  if (!commit) return null;
  const manifest = parseMaybeGzipJson(gitBlob(gitDir, commit, 'worldsindex/data/manifest.json'));
  if (!manifest) return null;
  const catalogIndex = parseMaybeGzipJson(gitBlob(gitDir, commit, 'worldsindex/data/catalog-index.json.gz'));
  const sourceCoverage = parseMaybeGzipJson(gitBlob(gitDir, commit, 'worldsindex/data/source-coverage.json'));
  const sourceMonitor = parseMaybeGzipJson(gitBlob(gitDir, commit, 'worldsindex/data/source-monitor.json'));
  const feed = parseMaybeGzipJson(gitBlob(gitDir, commit, 'worldsindex/data/feed.json'));
  const objectsEcsv = gitBlob(gitDir, commit, 'worldsindex/data/exports/objects.ecsv.gz');
  const objectRows = objectsEcsv ? parseEcsv(gunzipSync(objectsEcsv).toString('utf8')) : null;
  // Track 2a: the previous release's conflict index (absent before Track 2a; then nothing is "new").
  const conflictIndex = parseMaybeGzipJson(gitBlob(gitDir, commit, 'worldsindex/data/conflict-index.json.gz'));
  return { ref, commit, manifest, catalogIndex, sourceCoverage, sourceMonitor, feed, objectRows, conflictIndex };
}

// ------------------------------------------------------------------ the diff
const cap = (list) => ({ count: list.length, truncated: list.length > LIST_CAP, entries: list.slice(0, LIST_CAP) });
const byId = (objects) => new Map((objects ?? []).map((object) => [object.objectId, object]));
const sortById = (a, b) => (a.objectId < b.objectId ? -1 : a.objectId > b.objectId ? 1 : 0);

/**
 * Conflict sets that entered or left conflict between two releases (Track 2a). Both indexes are
 * worldsindex-conflict-index.v1 ({sets: [[objectId, propertyCode, relation, maxSigma]]}); null when
 * either release has none (the comparison is then not computable, never "nothing changed").
 */
export function diffConflicts(currentIndex, previousIndex, objectsById = new Map()) {
  if (!currentIndex) return null;
  if (!previousIndex) return { comparable: false, note: 'The previous release has no conflict index (it predates conflict sets); no conflict is listed as new or resolved.', new: { count: 0, truncated: false, entries: [] }, resolved: { count: 0, truncated: false, entries: [] } };
  const key = (entry) => `${entry[0]}\u0000${entry[1]}`;
  const before = new Map(previousIndex.sets.map((entry) => [key(entry), entry]));
  const now = new Map(currentIndex.sets.map((entry) => [key(entry), entry]));
  const describe = (entry) => { const object = objectsById.get(entry[0]); return { objectId: entry[0], name: object?.name ?? entry[0], propertyCode: entry[1], relation: entry[2], maxSigma: entry[3] ?? null }; };
  const fresh = [...now].filter(([k]) => !before.has(k)).map(([, entry]) => describe(entry));
  const resolved = [...before].filter(([k]) => !now.has(k)).map(([, entry]) => describe(entry));
  return { comparable: true, note: 'A conflict is new when the set (object x property) is in conflict in this release and was not in the previous one; resolved when the reverse. A set that disappeared with its object counts as resolved.', new: cap(fresh), resolved: cap(resolved) };
}

/**
 * current: { manifest, catalogIndex, sourceCoverage, sourceMonitor, detailCounts: Map objectId -> {records, spectra}, conflictIndex? }
 * previous: loadPreviousRelease() result or null.
 */
export function diffReleases(current, previous) {
  const releaseTime = current.manifest.generatedAt;
  const release = {
    generatedAt: releaseTime, atlasGeneratedAt: current.manifest.atlasGeneratedAt, projectionId: current.catalogIndex.projectionId,
    objectCount: current.manifest.objectCount, detailRecordCount: current.manifest.detailRecordCount,
  };
  const base = previous ? {
    ref: previous.ref, commit: previous.commit, generatedAt: previous.manifest.generatedAt, atlasGeneratedAt: previous.manifest.atlasGeneratedAt ?? null,
    projectionId: previous.catalogIndex?.projectionId ?? null, objectCount: previous.manifest.objectCount ?? null, detailRecordCount: previous.manifest.detailRecordCount ?? null,
    hadObjectExport: Boolean(previous.objectRows),
  } : null;
  const empty = { count: 0, truncated: false, entries: [] };
  const changelog = {
    schemaVersion: CHANGELOG_SCHEMA_VERSION, generatedAt: releaseTime, release, previousRelease: base,
    comparable: Boolean(previous?.catalogIndex),
    note: previous ? (previous.catalogIndex ? 'Derived from this release and the previous published release (previousRelease.commit). Lists are capped; counts are complete.' : 'The previous release has no catalog index to compare with; nothing is listed as changed.') : 'No previous release was available to the builder (first release, or no worldsindex-data branch yet); nothing is listed as changed.',
    objects: { added: empty, removed: empty, statusChanges: empty, promotions: empty, demotions: empty, identityChanges: empty, rowsAdded: null, spectraAdded: null },
    sources: { changes: empty, monitor: empty },
    summary: { added: 0, removed: 0, promotions: 0, demotions: 0, otherStatusChanges: 0, identityChanges: 0, objectsWithRowsAdded: null, rowsAdded: null, objectsWithSpectraAdded: null, spectraAdded: null, sourceChanges: 0, monitorChanges: 0 },
  };
  if (current.conflictIndex) {
    changelog.conflicts = diffConflicts(current.conflictIndex, previous?.catalogIndex ? previous.conflictIndex ?? null : null, byId(current.catalogIndex.objects));
    changelog.summary.newConflicts = changelog.conflicts.comparable ? changelog.conflicts.new.count : null;
    changelog.summary.resolvedConflicts = changelog.conflicts.comparable ? changelog.conflicts.resolved.count : null;
  }
  if (!previous?.catalogIndex) return changelog;

  const now = byId(current.catalogIndex.objects);
  const before = byId(previous.catalogIndex.objects);
  const entry = (object, extra = {}) => ({ objectId: object.objectId, name: object.name, hostName: object.hostName ?? null, status: object.normalizedStatus, identityState: object.identityState, primarySourceId: object.primarySourceId, ...extra });
  const added = [], removed = [], status = [], promotions = [], demotions = [], identity = [];
  for (const object of [...now.values()].sort(sortById)) {
    const old = before.get(object.objectId);
    if (!old) { added.push(entry(object)); continue; }
    if (old.normalizedStatus !== object.normalizedStatus) {
      const change = entry(object, { from: old.normalizedStatus, to: object.normalizedStatus });
      status.push(change);
      if (DEMOTED_STATUSES.has(object.normalizedStatus) && !DEMOTED_STATUSES.has(old.normalizedStatus)) demotions.push(change);
      else if (object.normalizedStatus === 'CONFIRMED' && old.normalizedStatus !== 'CONFIRMED') promotions.push(change);
    }
    if (old.identityState !== object.identityState) identity.push(entry(object, { from: old.identityState, to: object.identityState }));
  }
  for (const object of [...before.values()].sort(sortById)) if (!now.has(object.objectId)) removed.push(entry(object));

  let rowsAdded = null, spectraAdded = null;
  if (previous.objectRows && current.detailCounts) {
    const previousCounts = new Map(previous.objectRows.map((row) => [row.object_id, { records: Number(row.detail_record_count || 0), spectra: Number(row.spectrum_record_count || 0) }]));
    const rows = [], spectra = [];
    let rowTotal = 0, spectrumTotal = 0;
    for (const object of [...now.values()].sort(sortById)) {
      const count = current.detailCounts.get(object.objectId) ?? { records: 0, spectra: 0 };
      const old = previousCounts.get(object.objectId) ?? { records: 0, spectra: 0 };
      if (count.records > old.records) { rows.push(entry(object, { from: old.records, to: count.records })); rowTotal += count.records - old.records; }
      if (count.spectra > old.spectra) { spectra.push(entry(object, { from: old.spectra, to: count.spectra })); spectrumTotal += count.spectra - old.spectra; }
    }
    rowsAdded = { ...cap(rows), rows: rowTotal };
    spectraAdded = { ...cap(spectra), rows: spectrumTotal };
  }

  const sourceChanges = [];
  const coverageNow = current.sourceCoverage?.sources ?? {};
  const coverageBefore = previous.sourceCoverage?.sources ?? null;
  if (coverageBefore) {
    for (const sourceId of Object.keys(coverageNow).sort()) {
      const a = coverageBefore[sourceId], b = coverageNow[sourceId];
      if (!a) { sourceChanges.push({ sourceId, kind: 'declared', detail: `new in the source universe (${b.class}, ${b.state})` }); continue; }
      const changes = [];
      if (a.state !== b.state) changes.push(`state ${a.state} -> ${b.state}`);
      if ((a.snapshotId ?? null) !== (b.snapshotId ?? null)) changes.push(`snapshot ${a.snapshotId ?? 'none'} -> ${b.snapshotId ?? 'none'}`);
      if ((a.lastAttempt?.outcome ?? null) !== (b.lastAttempt?.outcome ?? null) || (a.lastAttempt?.at ?? null) !== (b.lastAttempt?.at ?? null)) changes.push(`last attempt ${a.lastAttempt ? `${a.lastAttempt.outcome} at ${a.lastAttempt.at}` : 'none'} -> ${b.lastAttempt ? `${b.lastAttempt.outcome} at ${b.lastAttempt.at}` : 'none'}`);
      if (a.attached !== b.attached) changes.push(`objects with rows ${a.attached} -> ${b.attached}`);
      if (a.blocked !== b.blocked) changes.push(`blocked ${a.blocked} -> ${b.blocked}`);
      if (changes.length) sourceChanges.push({ sourceId, kind: b.snapshotId && a.snapshotId !== b.snapshotId ? 'snapshot-retrieved' : b.lastAttempt && ['REFUSED', 'FAILED'].includes(b.lastAttempt.outcome) && (a.lastAttempt?.outcome !== b.lastAttempt.outcome || a.lastAttempt?.at !== b.lastAttempt.at) ? 'attempt-refused-or-failed' : 'changed', detail: changes.join('; ') });
    }
    for (const sourceId of Object.keys(coverageBefore).sort()) if (!coverageNow[sourceId]) sourceChanges.push({ sourceId, kind: 'withdrawn', detail: 'no longer in the coverage summary' });
  }
  const monitorChanges = [];
  const observations = (monitor) => new Map((monitor?.observations ?? []).map((row) => [row.sourceId, row]));
  const monitorNow = observations(current.sourceMonitor), monitorBefore = observations(previous.sourceMonitor);
  if (previous.sourceMonitor && current.sourceMonitor) {
    if (previous.sourceMonitor.state !== current.sourceMonitor.state) monitorChanges.push({ sourceId: null, kind: 'monitor-state', detail: `${previous.sourceMonitor.state} -> ${current.sourceMonitor.state}` });
    for (const [sourceId, row] of [...monitorNow].sort()) {
      const old = monitorBefore.get(sourceId);
      if (old && old.state !== row.state) monitorChanges.push({ sourceId, kind: row.state === 'AVAILABLE' ? 'recovered' : 'degraded', detail: `${old.state} -> ${row.state}${row.httpStatus ? ` (HTTP ${row.httpStatus})` : ''}${row.error ? `: ${row.error}` : ''}` });
    }
  }

  changelog.objects = { added: cap(added), removed: cap(removed), statusChanges: cap(status), promotions: cap(promotions), demotions: cap(demotions), identityChanges: cap(identity), rowsAdded, spectraAdded };
  changelog.sources = { changes: cap(sourceChanges), monitor: cap(monitorChanges) };
  const conflictSummary = changelog.conflicts ? { newConflicts: changelog.summary.newConflicts, resolvedConflicts: changelog.summary.resolvedConflicts } : {};
  changelog.summary = {
    ...conflictSummary,
    added: added.length, removed: removed.length, promotions: promotions.length, demotions: demotions.length, otherStatusChanges: status.length - promotions.length - demotions.length,
    identityChanges: identity.length, objectsWithRowsAdded: rowsAdded ? rowsAdded.count : null, rowsAdded: rowsAdded ? rowsAdded.rows : null,
    objectsWithSpectraAdded: spectraAdded ? spectraAdded.count : null, spectraAdded: spectraAdded ? spectraAdded.rows : null,
    sourceChanges: sourceChanges.length, monitorChanges: monitorChanges.length,
  };
  return changelog;
}

// ------------------------------------------------------------------ plain words
const n = (value) => (value === null || value === undefined ? 'not computable' : Number(value).toLocaleString('en-US'));
const plural = (count, word) => `${n(count)} ${word}${count === 1 ? '' : 's'}`;
export function objectUrl(objectId) { return `${SITE_BASE_URL}/worldsindex/?object=${encodeURIComponent(objectId)}`; }

export function renderChangelogMarkdown(changelog) {
  const { release, previousRelease: base, summary, objects, sources } = changelog;
  const lines = [`# WorldsIndex release ${release.generatedAt}`, ''];
  lines.push(`Atlas projection ${release.projectionId} (generated ${release.atlasGeneratedAt}); ${n(release.objectCount)} objects, ${n(release.detailRecordCount)} retained native rows.`);
  if (!base) lines.push('', changelog.note);
  else {
    lines.push(`Compared with the previous published release of ${base.generatedAt} (commit ${base.commit?.slice(0, 12) ?? 'unknown'}; ${n(base.objectCount)} objects, ${n(base.detailRecordCount)} rows).`);
    if (!changelog.comparable) lines.push('', changelog.note);
  }
  lines.push('', '## In one paragraph', '');
  if (!changelog.comparable) lines.push('Nothing is listed as changed because there is no comparable previous release.');
  else {
    const parts = [`${plural(summary.added, 'object')} added`, `${plural(summary.removed, 'object')} removed`, `${plural(summary.promotions, 'promotion')} to confirmed`, `${plural(summary.demotions, 'demotion')} to false positive or refuted`, `${plural(summary.otherStatusChanges, 'other status change')}`, `${plural(summary.identityChanges, 'identity-state change')}`];
    parts.push(summary.rowsAdded === null ? 'native rows added: not computable (the previous release had no object export)' : `${n(summary.rowsAdded)} native rows added on ${plural(summary.objectsWithRowsAdded, 'object')}`);
    parts.push(summary.spectraAdded === null ? 'spectra added: not computable' : `${n(summary.spectraAdded)} spectrum rows added on ${plural(summary.objectsWithSpectraAdded, 'object')}`);
    parts.push(`${plural(summary.sourceChanges, 'source-state change')}`, `${plural(summary.monitorChanges, 'provider-monitor change')}`);
    if (changelog.conflicts) parts.push(changelog.conflicts.comparable ? `${plural(summary.newConflicts, 'new conflict')} and ${plural(summary.resolvedConflicts, 'resolved conflict')} between sources` : 'conflicts between sources: not comparable (the previous release has no conflict index)');
    lines.push(`${parts.join('; ')}.`);
  }
  const section = (title, block, format) => {
    if (!block || block.count === 0) return;
    lines.push('', `## ${title} (${n(block.count)})`, '');
    for (const item of block.entries) lines.push(`- ${format(item)}`);
    if (block.truncated) lines.push(`- ... and ${n(block.count - block.entries.length)} more (the complete list is in changelog.json counts; entries are capped at ${LIST_CAP}).`);
  };
  const named = (item) => `${item.name} (${item.objectId})`;
  section('New objects', objects.added, (item) => `${named(item)}: ${item.status}, ${item.identityState}, from ${item.primarySourceId}`);
  section('Objects no longer in the release', objects.removed, (item) => `${named(item)}: was ${item.status}`);
  section('Promoted to confirmed', objects.promotions, (item) => `${named(item)}: ${item.from} -> ${item.to}`);
  section('Demoted to false positive or refuted', objects.demotions, (item) => `${named(item)}: ${item.from} -> ${item.to}`);
  section('Other status changes', { count: objects.statusChanges.count - objects.promotions.count - objects.demotions.count, truncated: objects.statusChanges.truncated, entries: objects.statusChanges.entries.filter((item) => !objects.promotions.entries.some((p) => p.objectId === item.objectId) && !objects.demotions.entries.some((d) => d.objectId === item.objectId)) }, (item) => `${named(item)}: ${item.from} -> ${item.to}`);
  section('Identity-state changes', objects.identityChanges, (item) => `${named(item)}: ${item.from} -> ${item.to}`);
  section('Native rows added', objects.rowsAdded, (item) => `${named(item)}: ${item.from} -> ${item.to} rows`);
  section('Spectra added', objects.spectraAdded, (item) => `${named(item)}: ${item.from} -> ${item.to} spectrum rows`);
  section('Source-state changes', sources.changes, (item) => `${item.sourceId} (${item.kind}): ${item.detail}`);
  section('Provider-monitor changes', sources.monitor, (item) => `${item.sourceId ?? 'monitor'} (${item.kind}): ${item.detail}`);
  const conflict = (item) => `${named(item)}: ${item.propertyCode.replaceAll('_', ' ')} (${item.relation}${item.maxSigma === null || item.maxSigma === undefined ? '' : `, max ${item.maxSigma} sigma`})`;
  if (changelog.conflicts?.comparable) {
    section('New conflicts between sources', changelog.conflicts.new, conflict);
    section('Resolved conflicts between sources', changelog.conflicts.resolved, conflict);
  }
  lines.push('', '---', '', 'Generated by the WorldsIndex static builder from the two releases\' own files; no clock other than the release time is used. Source membership is not independent confirmation.');
  return `${lines.join('\n')}\n`;
}

// ------------------------------------------------------------------ feeds
const FEED_KINDS = [
  ['added', 'new', (c) => c.objects.added.entries, (item) => `New: ${item.name} (${item.status.toLowerCase().replaceAll('_', ' ')})`, (item) => `${item.name} entered WorldsIndex as ${item.status} from ${item.primarySourceId}; identity ${item.identityState}.`],
  ['promotions', 'confirmed', (c) => c.objects.promotions.entries, (item) => `Confirmed: ${item.name}`, (item) => `${item.name} changed from ${item.from} to ${item.to}.`],
  ['demotions', 'demoted', (c) => c.objects.demotions.entries, (item) => `Demoted: ${item.name} (${item.to.toLowerCase().replaceAll('_', ' ')})`, (item) => `${item.name} changed from ${item.from} to ${item.to}.`],
  ['rowsAdded', 'updated', (c) => c.objects.rowsAdded?.entries ?? [], (item) => `Updated: ${item.name} (${item.to - item.from} new row${item.to - item.from === 1 ? '' : 's'})`, (item) => `${item.name} gained ${item.to - item.from} retained native row${item.to - item.from === 1 ? '' : 's'} (${item.from} -> ${item.to}).`],
];

/** Feed items for one release, newest-first order among kinds; deterministic for a given changelog. */
export function feedItemsFor(changelog) {
  const stamp = changelog.generatedAt;
  const releaseKey = stamp.replace(/[^0-9TZ]/g, '');
  const items = [];
  if (!changelog.comparable) return items;
  const s = changelog.summary;
  if (s.added || s.removed || s.promotions || s.demotions || s.otherStatusChanges || s.identityChanges || s.rowsAdded || s.spectraAdded || s.sourceChanges || s.monitorChanges) {
    items.push({
      id: `urn:worldsindex:release:${releaseKey}`, url: `${SITE_BASE_URL}/worldsindex/data/changelog.md`, title: `WorldsIndex release ${stamp}: ${s.added} new, ${s.promotions} confirmed, ${s.demotions} demoted`,
      content_text: renderChangelogMarkdown(changelog).split('\n## In one paragraph\n\n')[1]?.split('\n')[0] ?? 'Release summary.', date_published: stamp, tags: ['release'],
    });
  }
  for (const [, tag, pick, title, text] of FEED_KINDS) {
    for (const item of pick(changelog).slice(0, FEED_ITEMS_PER_KIND)) {
      items.push({ id: `urn:worldsindex:release:${releaseKey}:${tag}:${item.objectId}`, url: objectUrl(item.objectId), title: title(item), content_text: text(item), date_published: stamp, tags: [tag], _worldsindex: { objectId: item.objectId, kind: tag } });
    }
  }
  return items;
}

/** The JSON Feed 1.1 document: this release's items first, then the previous feed's items (bounded). */
export function jsonFeed(changelog, previousFeed) {
  const fresh = feedItemsFor(changelog);
  const freshIds = new Set(fresh.map((item) => item.id));
  const carried = Array.isArray(previousFeed?.items) ? previousFeed.items.filter((item) => item && typeof item.id === 'string' && !freshIds.has(item.id)) : [];
  const items = [...fresh, ...carried].slice(0, FEED_ITEM_CAP);
  return {
    version: 'https://jsonfeed.org/version/1.1',
    title: 'WorldsIndex: new, confirmed, demoted and updated planets',
    home_page_url: `${SITE_BASE_URL}/worldsindex/`,
    feed_url: `${SITE_BASE_URL}/${FEED_PATH}`,
    description: 'One item per object that entered WorldsIndex, was promoted to confirmed, was demoted to false positive or refuted, or gained retained catalog rows in a release, plus one summary item per release with changes. Derived from release-to-release differences of the published catalog; source membership is not independent confirmation.',
    language: 'en',
    authors: [{ name: 'Jack McGuire (WorldsIndex)' }],
    items,
  };
}

const xmlEscape = (value) => String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;');
export function atomFeed(feed) {
  const updated = feed.items[0]?.date_published ?? '2026-01-01T00:00:00.000Z';
  const out = [
    '<?xml version="1.0" encoding="utf-8"?>',
    '<feed xmlns="http://www.w3.org/2005/Atom">',
    `  <title>${xmlEscape(feed.title)}</title>`,
    `  <subtitle>${xmlEscape(feed.description)}</subtitle>`,
    `  <link rel="self" type="application/atom+xml" href="${SITE_BASE_URL}/${ATOM_PATH}"/>`,
    `  <link rel="alternate" type="text/html" href="${xmlEscape(feed.home_page_url)}"/>`,
    `  <id>${SITE_BASE_URL}/${ATOM_PATH}</id>`,
    `  <updated>${xmlEscape(updated)}</updated>`,
    '  <author><name>Jack McGuire (WorldsIndex)</name></author>',
  ];
  for (const item of feed.items) {
    out.push('  <entry>', `    <title>${xmlEscape(item.title)}</title>`, `    <link rel="alternate" type="text/html" href="${xmlEscape(item.url)}"/>`, `    <id>${xmlEscape(item.id)}</id>`, `    <updated>${xmlEscape(item.date_published)}</updated>`, `    <summary>${xmlEscape(item.content_text)}</summary>`);
    for (const tag of item.tags ?? []) out.push(`    <category term="${xmlEscape(tag)}"/>`);
    out.push('  </entry>');
  }
  out.push('</feed>');
  return `${out.join('\n')}\n`;
}

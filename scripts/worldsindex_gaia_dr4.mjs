// Gaia DR4 in the WorldsIndex release (2026-11-17). The builder writes public/data/gaia-dr4.json
// (ExoNexus packages/exonexus/gaia/dr4-public.ts): Gaia DR4 rows for the known hosts' DR4
// counterparts and Gaia's exoplanet and transit candidates, fail-closed per table. This module
// accepts that file into the static release: unknown schema → ignored; objects the atlas does not
// hold are dropped (counted), never invented; nothing is computed here.
export const GAIA_DR4_SCHEMA_VERSION = 'worldsindex-gaia-dr4.v1';
export const GAIA_DR4_STATES = ['NOT_YET_RELEASED', 'WAITING_FOR_ARCHIVE', 'RETRIEVING', 'AVAILABLE', 'PARTIAL'];

/**
 * @param {object|null} loaded the builder file, or null when absent
 * @param {Set<string>} atlasObjectIds the release's object ids
 * @returns {{ file: object|null, reason: string|null, dropped: number }}
 */
export function acceptGaiaDr4(loaded, atlasObjectIds) {
  if (!loaded) return { file: null, reason: 'absent', dropped: 0 };
  if (loaded.schemaVersion !== GAIA_DR4_SCHEMA_VERSION) return { file: null, reason: `unknown schema ${String(loaded.schemaVersion)}`, dropped: 0 };
  if (!GAIA_DR4_STATES.includes(loaded.state)) return { file: null, reason: `unknown state ${String(loaded.state)}`, dropped: 0 };
  let dropped = 0;
  const objects = {};
  for (const [objectId, sourceIds] of Object.entries(loaded.objects ?? {}).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))) {
    if (!atlasObjectIds.has(objectId)) { dropped += 1; continue; }
    const kept = (sourceIds ?? []).filter((id) => loaded.sources?.[id]);
    if (kept.length) objects[objectId] = kept;
  }
  const sources = {};
  for (const [id, source] of Object.entries(loaded.sources ?? {})) {
    sources[id] = { ...source, match: { ...source.match, hosts: (source.match?.hosts ?? []).map((host) => ({ ...host, objectIds: (host.objectIds ?? []).filter((objectId) => atlasObjectIds.has(objectId)) })) } };
  }
  const discoveries = (loaded.discoveries ?? []).filter((id) => sources[id]);
  return { file: { ...loaded, sources, objects, discoveries, counts: { ...(loaded.counts ?? {}), objectsWithDr4: Object.keys(objects).length } }, reason: null, dropped };
}

/** The compact manifest block (what the status page and the app read without fetching the file). */
export function gaiaDr4ManifestBlock(file, dropped) {
  if (!file) return null;
  return {
    path: 'gaia-dr4.json', schemaVersion: file.schemaVersion, state: file.state, stateDetail: file.stateDetail, expectedReleaseAt: file.expectedReleaseAt,
    generatedAt: file.generatedAt, snapshotId: file.snapshot?.snapshotId ?? null, retrievedAt: file.snapshot?.retrievedAt ?? null,
    tables: (file.tables ?? []).map((table) => ({ key: table.key, table: table.table, state: table.state })),
    counts: file.counts ?? {}, droppedObjectIds: dropped,
  };
}

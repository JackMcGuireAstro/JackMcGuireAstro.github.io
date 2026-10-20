// Which builder source-contributions files the static builder consumes (scripts/build_worldsindex_static.mjs).
//
// The ExoNexus builder writes public/data/source-contributions.json.gz: per atlas object, the aliases and
// source-native rows the frozen context sources state for it (NASA name tables, stellar hosts, transiting
// planets, microlensing, atmospheric spectra; since 2026-10-16 also the KOI deliveries, Kepler TCEs,
// ExoFOP, VizieR, HATNet/HATSouth and ADS where a snapshot exists). Schema versions:
//   v1  2026-10-05: { schemaVersion, generatedAt, atlasProjectionId, boundary, sources, objects }
//   v2  builder commit c54691a (2026-10-16): the same shape; each row may carry `link` (the exact key that
//       attached it) and `via` (the cross-identifier another source stated), each source summary `links`,
//       `viaCrossIdentifier` and `omitted`. Additive, so both versions are consumed the same way.
// The static builder on main accepted only v1 until 2026-10-20, so every release built from a v2 file
// (the first cloud run after the 2026-10-16 landing onwards) silently carried no context rows, no
// NASA aliases and `sourceContributions: null` in its manifest. A file for another atlas projection or
// an unknown schema is still ignored whole, never half-applied, and the reason is logged.
export const CONTRIBUTIONS_SCHEMA_VERSIONS = ['worldsindex-source-contributions.v1', 'worldsindex-source-contributions.v2'];

export function acceptSourceContributions(loaded, atlasProjectionId) {
  if (!loaded || typeof loaded !== 'object') return { contributions: null, reason: null };
  if (!CONTRIBUTIONS_SCHEMA_VERSIONS.includes(loaded.schemaVersion)) return { contributions: null, reason: `schema ${loaded.schemaVersion ?? 'missing'} is not one of ${CONTRIBUTIONS_SCHEMA_VERSIONS.join(', ')}` };
  if (loaded.atlasProjectionId !== atlasProjectionId) return { contributions: null, reason: `built for atlas projection ${loaded.atlasProjectionId ?? 'missing'}, this release is ${atlasProjectionId}` };
  if (!loaded.objects || typeof loaded.objects !== 'object' || !loaded.sources || typeof loaded.sources !== 'object') return { contributions: null, reason: 'objects or sources block missing' };
  return { contributions: loaded, reason: null };
}

/** One line for the build log: version, objects with context, rows, rows attached through a cross-identifier (v2), frozen sources. */
export function describeSourceContributions(contributions) {
  const objects = Object.values(contributions.objects);
  const rows = objects.reduce((sum, item) => sum + (item.records?.length ?? 0), 0);
  const via = objects.reduce((sum, item) => sum + (item.records ?? []).filter((record) => record.via).length, 0);
  const spectra = objects.reduce((sum, item) => sum + (item.records ?? []).filter((record) => record.sourceId === 'nasa-atmospheric-spectroscopy').length, 0);
  const frozen = Object.values(contributions.sources).filter((item) => item.state === 'FROZEN_SNAPSHOT').map((item) => item.sourceId);
  return `${contributions.schemaVersion} (${objects.length.toLocaleString()} objects with context, ${rows.toLocaleString()} rows, ${via.toLocaleString()} through a stated cross-identifier, ${spectra.toLocaleString()} spectrum rows; frozen sources: ${frozen.join(', ') || 'none'})`;
}

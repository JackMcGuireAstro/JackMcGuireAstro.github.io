#!/usr/bin/env node
// W3C PROV-JSON for one WorldsIndex build (Framework v2, Track 1, part B).
//
// provenanceDocument() names, as PROV entities, activities and agents:
//   * the release (its artifacts and their SHA-256 digests), the atlas projection it was
//     built from, every declared source with its contract (adapter) and parser version,
//     the frozen snapshot it read, the retrieval time and the last recorded attempt outcome
//     (from source-coverage lastAttempt), the identity rules the release was built under
//     with the reviewed re-identification pins, the builder commit, the site commit, the
//     state archive the cloud run restored (worldsindex/data/cloud-run.json, when the
//     publisher passes the marker) and the provider-monitor run;
//   * the activities: the atlas generation, the provider-monitor run and this static build;
//   * the agents: the ExoNexus builder, the static builder and WorldsIndex (Jack McGuire).
// Deterministic apart from the times the inputs themselves carry and the release time.

export const PROVENANCE_SCHEMA_VERSION = 'worldsindex-provenance.v1';
export const SITE_BASE_URL = 'https://jackmcguireastro.github.io';

const when = (value) => (value ? { $: value, type: 'xsd:dateTime' } : undefined);
const clean = (object) => Object.fromEntries(Object.entries(object).filter(([, value]) => value !== undefined));

/**
 * input: {
 *   generatedAt, manifest (without artifacts for provenance.json itself), artifacts: {path: {bytes, sha256}},
 *   atlas: {projectionId, generatedAt, schemaVersion, sourceSnapshots}, registry: {sources: {version, entries}, methods: {version}},
 *   sourceCoverage|null, sourceMonitor|null, identity: {rulesVersion, contract: {reviewedAt, reviewedBy, snapshotId}, pins: [...]},
 *   builder: {commit, version, name}, site: {commit, repository}, stateAsset|null (cloud-run marker), releaseAssets: [{name, bytes, sha256, downloadUrl}],
 *   conflicts|null (Track 2a: the manifest's conflictSets block plus `paths`, the artifacts derived from the conflict product)
 * }
 */
export function provenanceDocument(input) {
  const prefix = { prov: 'http://www.w3.org/ns/prov#', xsd: 'http://www.w3.org/2001/XMLSchema#', wi: `${SITE_BASE_URL}/worldsindex/prov#` };
  const releaseId = `wi:release/${input.atlas.projectionId}/${input.generatedAt}`;
  const atlasId = `wi:atlas/${input.atlas.projectionId}`;
  const buildId = `wi:activity/static-build/${input.generatedAt}`;
  const atlasActivityId = `wi:activity/atlas-generation/${input.atlas.projectionId}`;
  const monitorActivityId = input.sourceMonitor?.runId ? `wi:activity/source-monitor/${input.sourceMonitor.runId}` : null;
  const exonexus = 'wi:agent/exonexus-builder';
  const staticBuilder = 'wi:agent/static-builder';
  const operator = 'wi:agent/worldsindex';

  const entity = {};
  const activity = {};
  const agent = {};
  const used = {}; const wasGeneratedBy = {}; const wasDerivedFrom = {}; const wasAssociatedWith = {}; const wasAttributedTo = {};
  let counter = 0;
  const relate = (store, name, body) => { counter += 1; store[`_:${name}${counter}`] = clean(body); };

  entity[releaseId] = clean({
    'prov:type': 'wi:Release', 'prov:label': `WorldsIndex static release ${input.generatedAt}`,
    'prov:generatedAtTime': when(input.generatedAt),
    'wi:schemaVersion': input.manifest.schemaVersion, 'wi:objectCount': input.manifest.objectCount, 'wi:detailRecordCount': input.manifest.detailRecordCount,
    'wi:sourceUniverseVersion': input.registry.sources.version, 'wi:methodRegistryVersion': input.registry.methods.version,
    'wi:artifactCount': Object.keys(input.artifacts).length,
  });
  for (const [path, artifact] of Object.entries(input.artifacts).sort()) {
    const id = `wi:artifact/${path}`;
    entity[id] = { 'prov:type': 'wi:ReleaseArtifact', 'prov:label': path, 'wi:bytes': artifact.bytes, 'wi:sha256': artifact.sha256, 'wi:url': `${SITE_BASE_URL}/worldsindex/data/${path}` };
    relate(wasDerivedFrom, 'd', { 'prov:generatedEntity': id, 'prov:usedEntity': releaseId, 'prov:type': 'wi:partOf' });
    relate(wasGeneratedBy, 'g', { 'prov:entity': id, 'prov:activity': buildId });
  }
  for (const asset of input.releaseAssets ?? []) {
    const id = `wi:release-asset/${asset.name}`;
    entity[id] = clean({ 'prov:type': 'wi:ReleaseAsset', 'prov:label': asset.name, 'wi:bytes': asset.bytes, 'wi:sha256': asset.sha256, 'wi:url': asset.downloadUrl, 'wi:format': asset.format });
    relate(wasGeneratedBy, 'g', { 'prov:entity': id, 'prov:activity': buildId });
    relate(wasDerivedFrom, 'd', { 'prov:generatedEntity': id, 'prov:usedEntity': releaseId });
  }

  entity[atlasId] = clean({
    'prov:type': 'wi:AtlasProjection', 'prov:label': `Atlas projection ${input.atlas.projectionId}`, 'prov:generatedAtTime': when(input.atlas.generatedAt),
    'wi:schemaVersion': input.atlas.schemaVersion, 'wi:projectionId': input.atlas.projectionId,
  });
  relate(wasGeneratedBy, 'g', { 'prov:entity': atlasId, 'prov:activity': atlasActivityId });
  relate(used, 'u', { 'prov:activity': buildId, 'prov:entity': atlasId });
  relate(wasDerivedFrom, 'd', { 'prov:generatedEntity': releaseId, 'prov:usedEntity': atlasId });

  const coverage = input.sourceCoverage?.sources ?? {};
  const atlasSnapshots = new Map();
  for (const snapshot of input.atlas.sourceSnapshots ?? []) for (const sourceId of String(snapshot.sourceId).split('+')) atlasSnapshots.set(sourceId, snapshot);
  const monitorObservations = new Map((input.sourceMonitor?.observations ?? []).map((row) => [row.sourceId, row]));
  for (const source of [...input.registry.sources.entries].sort((a, b) => (a.id < b.id ? -1 : 1))) {
    const id = `wi:source/${source.id}`;
    const cov = coverage[source.id];
    const atlasSnapshot = atlasSnapshots.get(source.id);
    const observation = monitorObservations.get(source.id);
    entity[id] = clean({
      'prov:type': 'wi:UpstreamSource', 'prov:label': source.name,
      'wi:sourceId': source.id, 'wi:authority': source.authority, 'wi:accessMode': source.accessMode, 'wi:state': source.state,
      'wi:contractVersion': source.adapterVersion, 'wi:parserVersion': source.parserVersion, 'wi:release': source.release ?? undefined,
      'wi:documentationUrl': source.documentationUrl, 'wi:rights': source.rights, 'wi:lastVerifiedAt': source.lastVerifiedAt,
      'wi:coverageClass': cov?.class, 'wi:snapshotId': cov?.snapshotId ?? atlasSnapshot?.snapshotId ?? undefined,
      'wi:snapshotSha256': atlasSnapshot?.artifactSha256,
      'wi:retrievedAt': cov?.retrievedAt ?? undefined,
      'wi:lastAttemptAt': cov?.lastAttempt?.at, 'wi:lastAttemptOutcome': cov?.lastAttempt?.outcome, 'wi:lastAttemptCategory': cov?.lastAttempt?.category ?? undefined, 'wi:lastAttemptDetail': cov?.lastAttempt?.detail,
      'wi:monitorState': observation?.state, 'wi:monitorCheckedAt': observation?.checkedAt, 'wi:monitorParserVersion': observation?.artifact?.parserVersion,
      'wi:objectsAttached': cov?.attached, 'wi:rowsAttached': cov?.attachedRows,
    });
    if (cov?.class === 'ATLAS_INGESTED' || (cov && cov.attached > 0)) {
      relate(used, 'u', { 'prov:activity': atlasActivityId, 'prov:entity': id });
      relate(wasDerivedFrom, 'd', { 'prov:generatedEntity': releaseId, 'prov:usedEntity': id });
    } else if (cov) {
      relate(used, 'u', { 'prov:activity': buildId, 'prov:entity': id, 'prov:role': 'declared-not-data-bearing' });
    }
    if (monitorActivityId && observation) relate(used, 'u', { 'prov:activity': monitorActivityId, 'prov:entity': id, 'prov:role': 'canary-check' });
  }

  const identityId = `wi:identity-rules/${input.identity.rulesVersion}`;
  entity[identityId] = clean({
    'prov:type': 'wi:IdentityRules', 'prov:label': `Identity rules ${input.identity.rulesVersion}`, 'wi:rulesVersion': input.identity.rulesVersion,
    'wi:contractReviewedAt': input.identity.contract?.reviewedAt, 'wi:contractReviewedBy': input.identity.contract?.reviewedBy, 'wi:contractSnapshotId': input.identity.contract?.snapshotId,
  });
  relate(used, 'u', { 'prov:activity': atlasActivityId, 'prov:entity': identityId });
  for (const pin of input.identity.pins ?? []) {
    const pinId = `wi:identity-pin/${pin.toRules}`;
    entity[pinId] = clean({ 'prov:type': 'wi:ReviewedReidentificationPin', 'prov:label': `${pin.fromRules} -> ${pin.toRules}`, 'wi:fromRules': pin.fromRules, 'wi:toRules': pin.toRules, 'wi:snapshotId': pin.snapshotId, 'wi:transitionsSha256': pin.transitionsSha256, 'wi:reviewedAt': pin.reviewedAt, 'wi:reviewedBy': pin.reviewedBy });
    relate(wasDerivedFrom, 'd', { 'prov:generatedEntity': identityId, 'prov:usedEntity': pinId });
  }

  if (input.stateAsset?.state_asset) {
    const stateId = `wi:state-asset/${input.stateAsset.state_asset}`;
    entity[stateId] = clean({ 'prov:type': 'wi:StateAsset', 'prov:label': 'Working-state archive the cloud run restored', 'wi:asset': input.stateAsset.state_asset, 'wi:savedAt': input.stateAsset.state_saved_at ?? undefined, 'wi:restoredAt': input.stateAsset.state_restored_at ?? undefined, 'wi:workflow': input.stateAsset.workflow, 'wi:runUrl': input.stateAsset.run_url ?? undefined });
    relate(used, 'u', { 'prov:activity': atlasActivityId, 'prov:entity': stateId });
    relate(used, 'u', { 'prov:activity': buildId, 'prov:entity': stateId });
  }

  if (input.conflicts) {
    // Track 2a: the conflict sets are derived by the ExoNexus builder from the atlas and the frozen
    // snapshots; the selection rule is a versioned plan (prov:Plan) the derivation followed.
    const conflictId = `wi:conflict-sets/${input.atlas.projectionId}`;
    const conflictActivityId = `wi:activity/conflict-sets/${input.atlas.projectionId}`;
    const ruleId = `wi:selection-rule/${input.conflicts.selectionRule}`;
    const policyId = `wi:conflict-policy/${input.conflicts.policyVersion}`;
    entity[conflictId] = clean({ 'prov:type': 'wi:ConflictSets', 'prov:label': `Conflict sets for atlas ${input.atlas.projectionId}`, 'prov:generatedAtTime': when(input.conflicts.generatedAt), 'wi:schemaVersion': input.conflicts.schemaVersion, 'wi:objects': input.conflicts.objects, 'wi:assertionRows': input.conflicts.assertionRows, 'wi:multiRowSets': input.conflicts.multiRowSets, 'wi:setsInConflict': input.conflicts.setsInConflict, 'wi:selectedValues': input.conflicts.selectedValues });
    entity[ruleId] = clean({ 'prov:type': 'prov:Plan', 'prov:label': `Selection rule ${input.conflicts.selectionRule}: most recent default parameter set, gaps filled from the latest publication, closed-form values labelled calculated; a selection, not a measurement`, 'wi:selector': 'builder', 'wi:reviewState': 'unreviewed' });
    entity[policyId] = clean({ 'prov:type': 'prov:Plan', 'prov:label': `Comparison policy ${input.conflicts.policyVersion}: unit, definition, frame, epoch and identity assessed before any verdict; k = ${input.conflicts.sigmaThreshold} sigma`, 'wi:sigmaThreshold': input.conflicts.sigmaThreshold, 'wi:relationVocabulary': (input.conflicts.relationVocabulary ?? []).join(' ') });
    activity[conflictActivityId] = clean({ 'prov:type': 'wi:ConflictSetDerivation', 'prov:label': 'ExoNexus conflict sets, selections and reconciliation flags (scripts/build-conflict-sets.ts)', 'prov:endedAtTime': when(input.conflicts.generatedAt), 'wi:builderCommit': input.builder.commit ?? undefined });
    relate(used, 'u', { 'prov:activity': conflictActivityId, 'prov:entity': atlasId });
    relate(used, 'u', { 'prov:activity': conflictActivityId, 'prov:entity': ruleId, 'prov:role': 'selection-rule' });
    relate(used, 'u', { 'prov:activity': conflictActivityId, 'prov:entity': policyId, 'prov:role': 'comparison-policy' });
    relate(wasGeneratedBy, 'g', { 'prov:entity': conflictId, 'prov:activity': conflictActivityId });
    relate(wasAssociatedWith, 'a', { 'prov:activity': conflictActivityId, 'prov:agent': exonexus, 'prov:plan': ruleId });
    relate(used, 'u', { 'prov:activity': buildId, 'prov:entity': conflictId });
    for (const path of input.conflicts.paths ?? []) if (input.artifacts[path]) relate(wasDerivedFrom, 'd', { 'prov:generatedEntity': `wi:artifact/${path}`, 'prov:usedEntity': conflictId });
    for (const table of ['conflicts', 'selections']) for (const format of ['ecsv', 'vot']) { const path = `exports/${table}.${format}.gz`; if (input.artifacts[path]) relate(wasDerivedFrom, 'd', { 'prov:generatedEntity': `wi:artifact/${path}`, 'prov:usedEntity': conflictId }); }
  }
  activity[atlasActivityId] = clean({ 'prov:type': 'wi:AtlasGeneration', 'prov:label': 'ExoNexus atlas generation (npm run sky:generate)', 'prov:endedAtTime': when(input.atlas.generatedAt), 'wi:builderCommit': input.builder.commit ?? undefined, 'wi:builderVersion': input.builder.version ?? undefined });
  activity[buildId] = clean({ 'prov:type': 'wi:StaticBuild', 'prov:label': 'WorldsIndex static release build (scripts/build_worldsindex_static.mjs)', 'prov:endedAtTime': when(input.generatedAt), 'wi:siteCommit': input.site.commit ?? undefined, 'wi:builderCommit': input.builder.commit ?? undefined, 'wi:repository': input.site.repository ?? undefined });
  if (monitorActivityId) {
    activity[monitorActivityId] = clean({ 'prov:type': 'wi:SourceMonitorRun', 'prov:label': 'Provider monitor run', 'prov:startedAtTime': when(input.sourceMonitor.startedAt), 'prov:endedAtTime': when(input.sourceMonitor.completedAt), 'wi:runId': input.sourceMonitor.runId, 'wi:state': input.sourceMonitor.state, 'wi:schemaVersion': input.sourceMonitor.schemaVersion, 'wi:receiptSha256': input.sourceMonitor.receiptSha256 ?? undefined });
    relate(used, 'u', { 'prov:activity': buildId, 'prov:entity': 'wi:artifact/source-monitor.json' });
  }
  agent[exonexus] = clean({ 'prov:type': 'prov:SoftwareAgent', 'prov:label': input.builder.name ?? 'ExoNexus (WorldsIndex builder)', 'wi:version': input.builder.version ?? undefined, 'wi:commit': input.builder.commit ?? undefined });
  agent[staticBuilder] = clean({ 'prov:type': 'prov:SoftwareAgent', 'prov:label': 'WorldsIndex static builder (scripts/build_worldsindex_static.mjs)', 'wi:commit': input.site.commit ?? undefined });
  agent[operator] = { 'prov:type': 'prov:Organization', 'prov:label': 'WorldsIndex (Jack McGuire)' };
  relate(wasAssociatedWith, 'a', { 'prov:activity': atlasActivityId, 'prov:agent': exonexus });
  relate(wasAssociatedWith, 'a', { 'prov:activity': buildId, 'prov:agent': staticBuilder });
  if (monitorActivityId) relate(wasAssociatedWith, 'a', { 'prov:activity': monitorActivityId, 'prov:agent': exonexus });
  relate(wasAttributedTo, 't', { 'prov:entity': releaseId, 'prov:agent': operator });
  relate(wasGeneratedBy, 'g', { 'prov:entity': releaseId, 'prov:activity': buildId });

  return {
    schemaVersion: PROVENANCE_SCHEMA_VERSION,
    generatedAt: input.generatedAt,
    note: 'W3C PROV-JSON (https://www.w3.org/Submission/prov-json/). Entities: the release and its artifacts, the atlas projection, every declared source with contract and parser version, snapshot, retrieval time and last attempt outcome, the identity rules and their reviewed pins, the restored state archive. Activities: atlas generation, provider-monitor run, static build. Deterministic apart from the times the inputs carry.',
    prefix, entity, activity, agent, used, wasGeneratedBy, wasDerivedFrom, wasAssociatedWith, wasAttributedTo,
  };
}

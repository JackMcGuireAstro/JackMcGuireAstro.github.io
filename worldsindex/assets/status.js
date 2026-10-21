/* WorldsIndex status page (Framework v2 Track 1, part D, 2026-10-20): worldsindex/status.html.
 *
 * Reads only files the release already publishes (manifest.json, source-coverage.json,
 * source-monitor.json, cloud-run.json, provenance.json, bulk-downloads.json, exports/index.json,
 * changelog.json, registry.json.gz) and shows them as they are: the four denominators per source
 * (declared / applicable / executed / data-bearing) with each source's last retrieval attempt, the
 * provider monitor's state including a quarantine and the provider's own error text, the cloud-run
 * marker, identity counts, and links to the machine-readable description of the release. Every table
 * has a text summary before it; a file that is not published says so instead of failing the page.
 * Nothing here is fetched from a provider and nothing about the reader is stored.
 */

const esc = (value) => String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const n = (value) => (value === null || value === undefined ? '—' : Number(value).toLocaleString());
const when = (value) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isFinite(date.getTime()) ? `<time datetime="${esc(value)}">${esc(date.toISOString().replace('T', ' ').replace(/\.\d+Z$/, ' UTC'))}</time>` : esc(value);
};

export const SOURCE_CLASS_LABELS = { ATLAS_INGESTED: 'ingested into the atlas', FROZEN_CONTEXT: 'frozen context snapshot', LIVE_PER_TARGET: 'live per target (browser)', NOT_CONFIGURED: 'not configured', BLOCKED: 'blocked' };
export const OUTCOME_STATES = { RETRIEVED: 'ok', UNCHANGED: 'ok', REFUSED: 'warn', FAILED: 'bad', SKIPPED_NOT_MONITORED: 'warn', SKIPPED_SOURCE_NOT_AVAILABLE: 'warn', SKIPPED_NOT_CONFIGURED: 'warn' };

/** The four denominators of the coverage summary in one sentence, from its own `denominators.sources` block. */
export function denominatorSummary(coverage) {
  const d = coverage?.denominators?.sources;
  if (!d) return 'This release carries no denominators block in source-coverage.json (builder before 2026-10-19); the per-source table below still shows eligible, attached, missing and blocked objects.';
  return `${n(d.declared)} declared sources; ${n(d.applicable)} applicable to at least one object of this release; ${n(d.executed)} executed (a bounded query, snapshot lookup or retrieval attempt happened for this release), ${n(d.executedUnknown)} not computable because they run only in the visitor's browser, ${n(d.notExecuted)} not executed; ${n(d.dataBearing)} data-bearing (valid rows retained for at least one object). Each source's own four values and its last retrieval attempt follow.`;
}

/** "RETRIEVED 2026-10-20 04:58 UTC", "REFUSED COUNT_MISMATCH: …", "no attempt recorded". */
export function attemptLabel(lastAttempt) {
  if (!lastAttempt || !lastAttempt.outcome) return { text: 'no attempt recorded for this release', state: 'none' };
  const outcome = String(lastAttempt.outcome).replace(/^SKIPPED_/, 'SKIPPED ').replaceAll('_', ' ');
  const category = lastAttempt.category ? ` ${lastAttempt.category}` : '';
  return { text: `${outcome}${category}${lastAttempt.detail ? `: ${lastAttempt.detail}` : ''}`, at: lastAttempt.at || null, state: OUTCOME_STATES[lastAttempt.outcome] || 'warn' };
}

const yesNo = (value) => (value === null || value === undefined ? 'not computable' : value ? 'yes' : 'no');

export function sourceRows(coverage, registry) {
  const names = new Map((registry?.sources?.entries || []).map((entry) => [entry.id, entry.name]));
  return Object.values(coverage?.sources || {}).sort((a, b) => String(a.sourceId).localeCompare(String(b.sourceId))).map((source) => {
    const d = source.denominators || {}, objects = d.objects || {}, attempt = attemptLabel(source.lastAttempt);
    return { id: source.sourceId, name: names.get(source.sourceId) || source.sourceId, klass: SOURCE_CLASS_LABELS[source.class] || source.class || '—', state: source.state || '—',
      declared: yesNo(d.declared), applicable: yesNo(d.applicable), executed: yesNo(d.executed), dataBearing: yesNo(d.dataBearing),
      objectsApplicable: objects.applicable, objectsExecuted: objects.executed, objectsDataBearing: objects.dataBearing,
      eligible: source.eligible, attached: source.attached, missing: source.missingAtProvider, blocked: source.blocked, attachedRows: source.attachedRows,
      attempt, blockedReason: source.blockedReason || null, notComputable: d.notComputable || null, snapshotId: source.snapshotId || null, retrievedAt: source.retrievedAt || null };
  });
}

/** The monitor's state in words, with the quarantine (and the provider's retained message) when there is one. */
export function monitorSummary(monitor) {
  if (!monitor) return { text: 'No source-monitor.json is published with this release; the atlas remains the last validated release.', state: 'warn', failing: [] };
  const summary = monitor.summary || {};
  const failing = quarantineRows(monitor);
  const base = `Monitor state ${monitor.state || 'unknown'}: ${n(summary.available)} of ${n(summary.checked)} provider checks responded in the latest attempt (${summary.changed || 0} changed payloads; ${(summary.failed || 0) + (summary.timedOut || 0) + (summary.parseFailed || 0) + (summary.schemaDrift || 0)} failed or not validated). Latest attempt completed ${monitor.completedAt || '—'}; last validated attempt ${monitor.lastGoodCompletedAt || '—'}.`;
  if (monitor.state === 'QUARANTINED') {
    return { text: `${base} QUARANTINED: ${failing.length ? `required check${failing.length === 1 ? '' : 's'} ${failing.map((row) => `${row.sourceId} (${row.state}${row.httpStatus ? `, HTTP ${row.httpStatus}` : ''}${row.error ? `: ${row.error}` : ''})`).join('; ')}` : 'a required provider check failed'}. Every catalog measurement shown on the site is from the last validated release; nothing from the failing attempt was promoted.`, state: 'bad', failing };
  }
  return { text: base, state: (summary.failed || 0) > 0 ? 'warn' : 'ok', failing };
}

/** Required observations of the latest attempt (or the published observations) that are not AVAILABLE, with the provider's words. */
export function quarantineRows(monitor) {
  const observations = monitor?.latestAttempt?.observations || monitor?.observations || [];
  return observations.filter((item) => item.required && item.state !== 'AVAILABLE').map((item) => ({ sourceId: item.sourceId, state: item.state, httpStatus: item.httpStatus || null, error: item.error || null, errorCategory: item.errorCategory || null, checkedAt: item.checkedAt || null }));
}

export function identityRows(manifest) {
  const counts = manifest?.coverage?.identityCounts || {};
  const labels = { CANONICAL: 'canonical identity', RESOLVED: 'resolved identity', AMBIGUOUS: 'ambiguous identity (promotion withheld)', UNREVIEWED: 'separate unreviewed record' };
  return Object.entries(counts).sort(([a], [b]) => a.localeCompare(b)).map(([state, count]) => ({ state, label: labels[state] || state.toLowerCase(), count }));
}

export function cloudRunSummary(marker) {
  if (!marker) return 'No cloud-run marker is published with this release (the publisher writes worldsindex/data/cloud-run.json only when a GitHub Actions run publishes).';
  const parts = [`Published by ${marker.workflow || 'a cloud run'}${marker.run_id ? ` run ${marker.run_id}` : ''} at ${marker.generated_at || '—'}`];
  if (marker.state_asset) parts.push(`working state restored from ${marker.state_asset}`);
  if (marker.state_saved_at) parts.push(`state saved at ${marker.state_saved_at}`);
  if (marker.token_status) parts.push(`state key ${marker.token_status}${marker.token_expires_at ? ` (expires ${marker.token_expires_at})` : ''}`);
  return `${parts.join('; ')}.`;
}

async function getJson(url, { gzip = false } = {}) {
  try {
    const response = await fetch(url);
    if (!response.ok) return null;
    if (!gzip) return await response.json();
    const stream = new Blob([await response.arrayBuffer()]).stream().pipeThrough(new DecompressionStream('gzip'));
    return JSON.parse(await new Response(stream).text());
  } catch { return null; }
}

function pill(state, text) { return `<span class="state-pill" data-state="${esc(state)}">${esc(text)}</span>`; }
function set(id, html) { const node = document.getElementById(id); if (node) node.innerHTML = html; }

function renderRelease(manifest, provenance, marker) {
  if (!manifest) { set('release-summary', '<p class="table-summary">manifest.json could not be read, so nothing below can be shown.</p>'); return; }
  const build = Object.values(provenance?.activity || {}).find((item) => item && item['prov:type'] === 'wi:StaticBuild') || {};
  const builderCommit = build['wi:builderCommit'] || provenance?.agent?.['wi:agent/exonexus-builder']?.['wi:commit'] || null;
  const siteCommit = build['wi:siteCommit'] || provenance?.agent?.['wi:agent/static-builder']?.['wi:commit'] || null;
  const projectionId = Object.keys(provenance?.entity || {}).map((key) => /^wi:release\/([^/]+)\//.exec(key)?.[1]).find(Boolean) || manifest.exports?.atlasProjectionId || null;
  set('release-summary', `<p class="table-summary">Release built ${when(manifest.generatedAt)} from an atlas generated ${when(manifest.atlasGeneratedAt)}: ${n(manifest.objectCount)} objects, ${n(manifest.detailRecordCount)} packaged native rows on ${n(manifest.detailObjectCount)} objects, ${n(manifest.transitEphemerides?.count)} transit ephemerides, ${Object.keys(manifest.artifacts || {}).length} declared artifacts. Source universe ${esc(manifest.sourceUniverseVersion)}, method registry ${esc(manifest.methodRegistryVersion)}.</p>` +
    `<div class="table-wrap" tabindex="0" role="region" aria-label="Release facts"><table><thead><tr><th>Fact</th><th>Value</th></tr></thead><tbody>` +
    `<tr><td>Atlas SHA-256 (release checksum)</td><td><code>${esc(manifest.atlasSha256)}</code></td></tr>` +
    `<tr><td>Atlas projection</td><td><code>${esc(projectionId || '—')}</code></td></tr>` +
    `<tr><td>Catalog index</td><td>${esc(manifest.catalogIndexSchemaVersion)} · ${n(manifest.catalogIndexObjectCount)} objects · SHA-256 <code>${esc(manifest.catalogIndexSha256)}</code></td></tr>` +
    `<tr><td>Source contributions consumed</td><td>${manifest.sourceContributions ? `${esc(manifest.sourceContributions.schemaVersion)} generated ${when(manifest.sourceContributions.generatedAt)}; frozen sources: ${esc(Object.values(manifest.sourceContributions.sources || {}).filter((item) => item.state === 'FROZEN_SNAPSHOT').map((item) => item.sourceId).join(', ') || 'none')}` : 'none in this release (no file, or a file the static builder did not accept)'}</td></tr>` +
    `<tr><td>Builder commit</td><td><code>${esc(builderCommit || '—')}</code></td></tr><tr><td>Site commit</td><td><code>${esc(siteCommit || '—')}</code></td></tr>` +
    `<tr><td>Changelog</td><td>${manifest.changelog ? `${manifest.changelog.comparable ? `compared with the previous release ${esc(manifest.changelog.previousReleaseCommit || '')}` : 'not comparable with a previous release'} · <a href="data/changelog.md">changelog.md</a> · <a href="data/changelog.json">changelog.json</a>` : 'not published'}</td></tr>` +
    `</tbody></table></div>`);
  set('cloud-run', `<p class="table-summary">${esc(cloudRunSummary(marker))}${marker?.run_url ? ` <a href="${esc(marker.run_url)}" rel="noopener">Run log ↗</a>` : ''}</p>`);
}

function renderSources(coverage, registry) {
  set('sources-summary', `<p class="table-summary">${esc(denominatorSummary(coverage))}</p>`);
  if (!coverage) { set('sources-table', '<p class="table-summary">source-coverage.json is not published with this release.</p>'); return; }
  const rows = sourceRows(coverage, registry);
  const executedKnown = rows.filter((row) => row.executed !== 'not computable').length;
  set('sources-table', `<p class="table-summary">${rows.length} sources, generated ${when(coverage.generatedAt)}; ${n(coverage.objectsWithAnyContribution)} of ${n(coverage.objects)} objects carry at least one context row and ${n(coverage.crossIdentifiers)} cross-identifiers were propagated. The executed column is known for ${executedKnown} sources; the rest run only in the visitor's browser. Object counts are per source: applicable (states an identifier of the kind the source is keyed on), executed and data-bearing; then eligible / attached / missing at the provider / blocked as the builder counted them, and the last retrieval attempt with its category and the provider's words.</p>` +
    `<div class="table-wrap" tabindex="0" role="region" aria-label="Sources and their four denominators"><table><thead><tr><th>Source</th><th>Class · state</th><th>Declared</th><th>Applicable</th><th>Executed</th><th>Data-bearing</th><th>Objects applicable / executed / data-bearing</th><th>Eligible / attached / missing / blocked</th><th>Rows</th><th>Last attempt</th></tr></thead><tbody>` +
    rows.map((row) => `<tr><td><strong>${esc(row.name)}</strong><small><code>${esc(row.id)}</code>${row.snapshotId ? `<br>snapshot ${esc(row.snapshotId)}${row.retrievedAt ? ` · ${esc(row.retrievedAt)}` : ''}` : ''}</small></td><td>${esc(row.klass)}<small>${esc(row.state)}</small></td><td>${esc(row.declared)}</td><td>${esc(row.applicable)}</td><td>${esc(row.executed)}${row.notComputable ? `<small>${esc(row.notComputable)}</small>` : ''}</td><td>${esc(row.dataBearing)}</td><td>${n(row.objectsApplicable)} / ${row.objectsExecuted === null || row.objectsExecuted === undefined ? '—' : n(row.objectsExecuted)} / ${n(row.objectsDataBearing)}</td><td>${n(row.eligible)} / ${n(row.attached)} / ${n(row.missing)} / ${n(row.blocked)}${row.blockedReason ? `<small>${esc(row.blockedReason)}</small>` : ''}</td><td>${n(row.attachedRows)}</td><td>${row.attempt.state === 'none' ? esc(row.attempt.text) : `${pill(row.attempt.state, row.attempt.text.split(':')[0])}<small>${esc(row.attempt.text)}${row.attempt.at ? `<br>${esc(row.attempt.at)}` : ''}</small>`}</td></tr>`).join('') +
    `</tbody></table></div>`);
}

function renderMonitor(monitor) {
  const summary = monitorSummary(monitor);
  set('monitor-summary', `<p class="table-summary">${pill(summary.state, monitor?.state || 'unknown')} ${esc(summary.text)}</p>`);
  if (!monitor) { set('monitor-table', ''); return; }
  const observations = monitor.latestAttempt?.observations || monitor.observations || [];
  const stateOf = (item) => (item.state === 'AVAILABLE' ? 'ok' : item.required ? 'bad' : 'warn');
  set('monitor-table', `<p class="table-summary">${observations.length} provider checks in the latest attempt${monitor.latestAttempt ? ` (${esc(monitor.latestAttempt.runId || '')}, completed ${esc(monitor.latestAttempt.completedAt || '')})` : ''}; a required check that is not AVAILABLE quarantines the attempt and keeps the last validated receipt. The error column is the provider's own message as retained.</p>` +
    `<div class="table-wrap" tabindex="0" role="region" aria-label="Provider monitor checks"><table><thead><tr><th>Source</th><th>Provider</th><th>State</th><th>Required</th><th>HTTP</th><th>Records</th><th>Latest marker</th><th>Latency</th><th>Error (provider text)</th><th>Checked</th></tr></thead><tbody>` +
    observations.map((item) => `<tr><td><code>${esc(item.sourceId)}</code></td><td>${esc(item.provider || '')}<small>${esc(item.requestDescription || '')}</small></td><td>${pill(stateOf(item), item.state)}${item.changed ? '<small>payload changed</small>' : ''}</td><td>${item.required ? 'yes' : 'no'}</td><td>${esc(item.httpStatus ?? '—')}</td><td>${n(item.recordCount)}</td><td>${esc(item.latestMarker ?? '—')}</td><td>${item.latencyMs === null || item.latencyMs === undefined ? '—' : `${n(item.latencyMs)} ms`}</td><td>${item.error ? `${esc(item.error)}${item.errorCategory ? `<small>${esc(item.errorCategory)}</small>` : ''}` : '—'}</td><td>${esc(item.checkedAt || '—')}</td></tr>`).join('') +
    `</tbody></table></div>`);
}

function renderIdentity(manifest) {
  const rows = identityRows(manifest);
  const total = rows.reduce((sum, row) => sum + Number(row.count || 0), 0);
  const statuses = Object.entries(manifest?.coverage?.statusCounts || {}).sort(([a], [b]) => a.localeCompare(b));
  set('identity-table', `<p class="table-summary">${rows.length ? `${n(total)} objects by identity state: ${rows.map((row) => `${n(row.count)} ${row.label}`).join(', ')}. Scientific status counts follow for the same release.` : 'This manifest carries no identity counts.'}</p>` +
    (rows.length ? `<div class="table-wrap" tabindex="0" role="region" aria-label="Identity states"><table><thead><tr><th>Identity state</th><th>Meaning</th><th>Objects</th></tr></thead><tbody>${rows.map((row) => `<tr><td><code>${esc(row.state)}</code></td><td>${esc(row.label)}</td><td>${n(row.count)}</td></tr>`).join('')}</tbody></table></div>` : '') +
    (statuses.length ? `<h3>Scientific status</h3><p class="table-summary">${statuses.map(([status, count]) => `${n(count)} ${status.toLowerCase().replaceAll('_', ' ')}`).join(', ')}.</p><div class="table-wrap" tabindex="0" role="region" aria-label="Scientific status counts"><table><thead><tr><th>Status</th><th>Objects</th></tr></thead><tbody>${statuses.map(([status, count]) => `<tr><td><code>${esc(status)}</code></td><td>${n(count)}</td></tr>`).join('')}</tbody></table></div>` : ''));
}

function renderMachine(manifest, exportsIndex, bulk, changelog, provenance) {
  const tables = Object.entries(exportsIndex?.tables || {});
  set('exports-table', `<p class="table-summary">${tables.length ? `${tables.length} export tables (ECSV 1.0 and VOTable 1.5, gzip) with ${n(tables.reduce((sum, [, table]) => sum + Number(table.rows || 0), 0))} rows in all; every column carries a datatype, UCD, VOUnits unit and description in <a href="data/exports/index.json">exports/index.json</a>.` : 'No exports index is published with this release.'}</p>` +
    (tables.length ? `<div class="table-wrap" tabindex="0" role="region" aria-label="Export tables"><table><thead><tr><th>Table</th><th>Rows</th><th>Columns</th><th>ECSV</th><th>VOTable</th><th>Description</th></tr></thead><tbody>${tables.map(([name, table]) => `<tr><td><code>${esc(name)}</code></td><td>${n(table.rows)}</td><td>${n(table.columns?.length)}</td><td><a href="data/${esc(table.files?.ecsv?.path)}">${esc(table.files?.ecsv?.path?.split('/').pop())}</a><small>${n(table.files?.ecsv?.bytes)} bytes</small></td><td><a href="data/${esc(table.files?.votable?.path)}">${esc(table.files?.votable?.path?.split('/').pop())}</a><small>${n(table.files?.votable?.bytes)} bytes</small></td><td>${esc(table.description || '')}</td></tr>`).join('')}</tbody></table></div>` : ''));
  const assets = bulk?.assets || [];
  set('bulk-table', `<p class="table-summary">${bulk ? (assets.length ? `${assets.length} VOParquet asset${assets.length === 1 ? '' : 's'} on the rolling GitHub Release <a href="${esc(bulk.releaseUrl)}" rel="noopener">${esc(bulk.releaseTag)}</a>; verify the SHA-256 after download because an asset can lag the Pages release by one cycle.` : `No VOParquet asset in this release: ${esc(bulk.unavailableReason || 'reason not stated')}.`) : 'No bulk-downloads.json is published with this release.'}</p>` +
    (assets.length ? `<div class="table-wrap" tabindex="0" role="region" aria-label="Bulk download assets"><table><thead><tr><th>Asset</th><th>Table</th><th>Rows</th><th>Bytes</th><th>SHA-256</th></tr></thead><tbody>${assets.map((asset) => `<tr><td><a href="${esc(asset.downloadUrl)}" rel="noopener">${esc(asset.name)}</a></td><td><code>${esc(asset.table)}</code></td><td>${n(asset.rows)}</td><td>${n(asset.bytes)}</td><td><code>${esc(asset.sha256)}</code></td></tr>`).join('')}</tbody></table></div>` : ''));
  const summary = changelog?.summary || manifest?.changelog?.summary || null;
  set('changelog-summary', `<p class="table-summary">${changelog ? (changelog.comparable ? `Compared with the previous published release${changelog.previousRelease?.generatedAt ? ` of ${esc(changelog.previousRelease.generatedAt)}` : ''}: ${esc(Object.entries(summary || {}).filter(([, value]) => typeof value === 'number' && value > 0).map(([key, value]) => `${n(value)} ${key.replace(/([A-Z])/g, ' $1').toLowerCase()}`).join(', ') || 'no counted change')}.` : `Not comparable with a previous release${changelog.reason ? `: ${esc(changelog.reason)}` : ''}.`) : 'No changelog is published with this release.'} <a href="data/changelog.md">Read the changelog</a> · <a href="data/feed.json">JSON Feed</a> · <a href="data/feed.xml">Atom</a>.</p>`);
  const artifactCount = provenance?.entity ? Object.keys(provenance.entity).length : null;
  set('provenance-summary', `<p class="table-summary">${provenance ? `W3C PROV-JSON for this build (${esc(manifest?.provenanceSchemaVersion || provenance.schemaVersion || '')}): ${n(artifactCount)} entities (the release, every artifact with its SHA-256, the atlas projection, the declared sources with contract and parser version, the identity rules and reviewed pins). ` : 'No provenance.json is published with this release. '}<a href="data/provenance.json">provenance.json</a></p>`);
}

// ---- Controversial, retracted and excluded objects (Framework v2, Track 2a) -------------------
export const EXCEPTION_LABELS = { controversial: 'controversial', retracted: 'retracted or refuted', 'excluded-false-positive': 'excluded: false positive', 'status-conflict': 'sources disagree on status', 'unresolved-source-conflict': 'unresolved source conflict' };
export const EXCEPTION_ROW_CAP = 300;
/** Objects of one category (or all), in the file's order. */
export function exceptionRows(list, category = 'all') {
  return (list?.objects || []).filter((item) => category === 'all' || item.categories.includes(category));
}
function exceptionTable(list, category) {
  const rows = exceptionRows(list, category);
  const shown = rows.slice(0, EXCEPTION_ROW_CAP);
  return `<p class="table-summary" aria-live="polite">${n(rows.length)} object${rows.length === 1 ? '' : 's'}${category === 'all' ? '' : ` in “${esc(EXCEPTION_LABELS[category] || category)}”`}; ${rows.length > shown.length ? `the first ${n(shown.length)} are listed (the file holds every one)` : 'all are listed'}.</p>`
    + `<div class="table-wrap" tabindex="0" role="region" aria-label="Controversial, retracted and excluded objects"><table><thead><tr><th scope="col">Object</th><th scope="col">Release status</th><th scope="col">Categories</th><th scope="col">Reasons (source: source status)</th></tr></thead><tbody>${shown.map((item) => `<tr><td><a href="./?object=${encodeURIComponent(item.objectId)}&amp;section=object">${esc(item.name)}</a><small>${esc(item.objectId)}</small></td><td>${esc(item.atlasStatus)}</td><td>${item.categories.map((c) => esc(EXCEPTION_LABELS[c] || c)).join('; ')}</td><td>${item.reasons.map((reason) => `${esc(reason.sourceId)}: ${esc(reason.sourceStatus)} → ${esc(reason.normalized)}`).join('<br>')}</td></tr>`).join('')}</tbody></table></div>`;
}
function renderExceptions(manifest, data) {
  const block = manifest?.conflictSets;
  if (!block?.exceptionsPath) { set('exceptions-summary', '<p class="table-summary">This release publishes no controversial, retracted and excluded list (it carries no conflict sets).</p>'); return; }
  const counts = block.exceptions || {};
  set('exceptions-summary', `<p class="table-summary">${Object.entries(EXCEPTION_LABELS).map(([key, label]) => `${n(counts[key] || 0)} ${esc(label)}`).join(' · ')}. An object can be in several categories; each reason names the source record and the source's own words. <a href="data/${esc(block.exceptionsPath)}">${esc(block.exceptionsPath)}</a></p><p><button type="button" class="text-link" id="exceptions-load">Show the list</button></p>`);
  const button = document.getElementById('exceptions-load');
  button?.addEventListener('click', async () => {
    button.disabled = true;
    set('exceptions-table', '<p class="table-summary" role="status">Reading the list…</p>');
    const list = await data(block.exceptionsPath, { gzip: true });
    if (!list) { set('exceptions-table', '<p class="table-summary">The list could not be read.</p>'); button.disabled = false; return; }
    const options = ['all', ...Object.keys(EXCEPTION_LABELS)].map((key) => `<option value="${esc(key)}">${esc(key === 'all' ? 'All categories' : EXCEPTION_LABELS[key])}</option>`).join('');
    set('exceptions-table', `<div class="exceptions-controls"><label>Category<select id="exceptions-category">${options}</select></label></div><div id="exceptions-rows">${exceptionTable(list, 'all')}</div>`);
    document.getElementById('exceptions-category')?.addEventListener('change', (event) => set('exceptions-rows', exceptionTable(list, event.target.value)));
    button.hidden = true;
  });
}

async function init() {
  const base = new URL('../', import.meta.url);
  const data = (path, options) => getJson(new URL(`data/${path}`, base), options);
  const [manifest, coverage, monitor, marker, provenance, bulk, exportsIndex, changelog, registry] = await Promise.all([
    data('manifest.json'), data('source-coverage.json'), data('source-monitor.json'), data('cloud-run.json'), data('provenance.json'), data('bulk-downloads.json'), data('exports/index.json'), data('changelog.json'), data('registry.json.gz', { gzip: true }),
  ]);
  renderRelease(manifest, provenance, marker);
  renderSources(coverage, registry);
  renderMonitor(monitor);
  renderIdentity(manifest);
  renderMachine(manifest, exportsIndex, bulk, changelog, provenance);
  renderExceptions(manifest, data);
  const loaded = document.getElementById('status-loading');
  if (loaded) loaded.hidden = true;
}

if (typeof document !== 'undefined' && document.getElementById('status-loading')) init();

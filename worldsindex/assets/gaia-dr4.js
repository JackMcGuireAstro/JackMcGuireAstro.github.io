// Gaia DR4 on WorldsIndex (2026-11-17): the "Gaia DR4" panel on an object's Evidence tab, the
// "New in Gaia DR4" list on the Discover page and the status line. A host button opens its object page
// through app.js's data-compare-open handler (select the object, show its evidence dossier). Everything is read from
// data/gaia-dr4.json (scripts/worldsindex_gaia_dr4.mjs; built by the ExoNexus fail-closed adapter);
// the browser only formats. Gaia values are shown under Gaia's column names; the only derived values
// are the builder's unit conversions, marked "calculated". Absent data shows nothing.
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const SHOWN = new Set(['AVAILABLE', 'PARTIAL']);
const NOT_GIVEN = '<span class="muted">not given</span>';

export function fmt(value, digits = 4) {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return NOT_GIVEN;
  const n = Number(value), a = Math.abs(n);
  if (a !== 0 && (a < 1e-3 || a >= 1e6)) return esc(n.toExponential(digits - 1));
  return esc(Number(n.toPrecision(digits)).toString());
}
const text = (value) => (value === null || value === undefined || value === '' ? NOT_GIVEN : esc(value));
const yesNo = (value) => (value === true ? 'yes' : value === false ? 'no' : 'not given');

/** True when the release carries DR4 rows worth fetching (from the manifest block alone). */
export function hasRows(block) { return Boolean(block && SHOWN.has(block.state) && (block.counts?.sources ?? 0) > 0); }

function linkSentence(host) {
  if (host.link === 'DR3_NEIGHBOURHOOD') {
    return `Listed in Gaia's <code>dr3_neighbourhood</code> table as a counterpart of the host's Gaia DR3 source (probable match by Gaia's rule: ${yesNo(host.probableMatch)}; angular distance ${fmt(host.angularDistanceMas, 3)} mas; G difference ${fmt(host.magnitudeDifference, 2)} mag).`;
  }
  return `Within 5″ of the host's catalogue position (${fmt(host.separationArcsec, 3)}″; DR4 positions are at epoch J2017.5, the catalogue's at J2000).`;
}

function table(headers, rows) {
  return `<div class="table-wrap"><table><thead><tr>${headers.map((h) => `<th scope="col">${h}</th>`).join('')}</tr></thead><tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${cell}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

function calculatedMass(calculated) {
  if (!calculated) return NOT_GIVEN;
  const range = calculated.m2_lower_mjup === null && calculated.m2_upper_mjup === null ? '' : `${fmt(calculated.m2_lower_mjup, 4)} – ${fmt(calculated.m2_upper_mjup, 4)}`;
  if (calculated.m2_mjup === null || calculated.m2_mjup === undefined) return range || NOT_GIVEN;
  return range ? `${fmt(calculated.m2_mjup, 4)} (${range})` : fmt(calculated.m2_mjup, 4);
}

function sourceCard(file, source, objectId) {
  const host = source.match.hosts.find((item) => (item.objectIds ?? []).includes(objectId)) ?? source.match.hosts[0] ?? null;
  const others = source.match.hosts.filter((item) => item !== host);
  const parts = [];
  parts.push(`<p>${host ? linkSentence(host) : 'No known planet host is linked to this source.'}</p>`);
  if (others.length) parts.push(`<p class="gaia-dr4-ambiguous"><strong>Ambiguous:</strong> this DR4 source also matches ${others.map((item) => esc(item.hostName)).join(', ')}. WorldsIndex does not choose between them.</p>`);
  const ruwe = source.ruwe === null || source.ruwe === undefined ? NOT_GIVEN : `${fmt(source.ruwe, 3)}${source.ruweFlag ? ' <span class="consensus-badge">elevated (above 1.4)</span>' : ''}`;
  parts.push(`<dl class="gaia-dr4-facts"><dt>RUWE</dt><dd>${ruwe}</dd><dt>Parallax</dt><dd>${source.parallaxMas === null ? NOT_GIVEN : `${fmt(source.parallaxMas, 5)} mas`}</dd><dt>Position (J2017.5)</dt><dd>${source.raDeg === null ? NOT_GIVEN : `${fmt(source.raDeg, 9)}°, ${fmt(source.decDeg, 9)}°`}</dd></dl>`);
  if (source.ruweFlag) parts.push('<p class="fineprint">An elevated RUWE means Gaia\'s single-star model fits this source poorly. A companion is one possible cause, not a conclusion.</p>');
  if (source.candidate) {
    const fields = Object.entries(source.candidate.fields ?? {});
    parts.push(`<h4>Gaia exoplanet candidate <span class="consensus-badge">candidate, not a confirmed planet</span></h4><p class="muted">As given in <code>${esc(source.candidate.table)}</code>:</p>${table(['Gaia column', 'Value'], fields.map(([key, value]) => [`<code>${esc(key)}</code>`, esc(value)]))}`);
  }
  if (source.orbits?.length) {
    parts.push(`<h4>Orbital solutions (<code>nss_two_body_orbit</code>)</h4>${table(['<code>solution_type</code>', '<code>period</code> (d)', '<code>period_error</code> (d)', '<code>eccentricity</code>', '<code>inclination</code> (°)', '<code>significance</code>', '<code>gof</code>', '<code>mass_function</code> (M☉)'],
      source.orbits.map((orbit) => [text(orbit.solution_type), fmt(orbit.period, 6), fmt(orbit.period_error, 3), fmt(orbit.eccentricity, 3), fmt(orbit.inclination, 4), fmt(orbit.significance, 3), fmt(orbit.gof, 3), text(orbit.mass_function)]))}`);
  }
  if (source.masses?.length) {
    parts.push(`<h4>Masses (<code>nss_masses</code>)</h4>${table(['<code>combination_method</code>', '<code>m1</code> (M☉)', '<code>m2</code> (M☉)', '<code>m2_lower</code> – <code>m2_upper</code> (M☉)', '<code>exoplanet_candidate_hrd_proba</code>', 'm2 in Jupiter masses <span class="consensus-badge">calculated</span>'],
      source.masses.map((mass) => [text(mass.combination_method), fmt(mass.m1, 3), fmt(mass.m2, 3), `${fmt(mass.m2_lower, 3)} – ${fmt(mass.m2_upper, 3)}`, fmt(mass.exoplanet_candidate_hrd_proba, 3),
        calculatedMass(mass.calculated)]))}<p class="fineprint">Calculated: ${esc(source.masses.find((mass) => mass.calculated)?.calculated.method ?? 'unit conversion only')}.</p>`);
  }
  if (source.transit) {
    const t = source.transit;
    parts.push(`<h4>Gaia planetary-transit candidate (<code>vari_planetary_transit</code>) <span class="consensus-badge">candidate</span></h4>${table(['<code>transit_period</code> (d)', '<code>transit_depth</code> (mmag)', '<code>transit_duration</code> (d)', '<code>transit_reference_time</code>', '<code>num_in_transit</code>'],
      [[fmt(t.transit_period, 8), fmt(t.transit_depth, 3), fmt(t.transit_duration, 3), fmt(t.transit_reference_time, 8), fmt(t.num_in_transit, 3)]])}`);
  }
  return `<article class="gaia-dr4-source"><h4 class="gaia-dr4-designation">${esc(source.designation)} <a class="text-link" href="${esc(source.archiveQueryUrl)}" rel="noopener" target="_blank">Gaia archive row<span class="visually-hidden"> (opens in a new tab)</span> ↗</a></h4>${parts.join('')}</article>`;
}

/** The object's Gaia DR4 panel, or '' when the release links no DR4 source to it. */
export function objectPanelHtml(file, objectId) {
  if (!file || !SHOWN.has(file.state)) return '';
  const ids = file.objects?.[objectId] ?? [];
  const sources = ids.map((id) => file.sources?.[id]).filter(Boolean);
  if (!sources.length) return '';
  const several = sources.length > 1 ? `<p class="gaia-dr4-ambiguous"><strong>${sources.length} DR4 sources</strong> are linked to this planet's host. WorldsIndex lists them all and does not choose one.</p>` : '';
  const snapshot = file.snapshot ? ` Snapshot <code>${esc(file.snapshot.snapshotId)}</code>, retrieved ${esc(file.snapshot.retrievedAt)}.` : '';
  return `<h3 id="gaia-dr4-title">Gaia DR4</h3><p class="plain-language-summary">Gaia Data Release 4 measured the host star of this planet. These are Gaia's own values for the host, under Gaia's column names. They are context for the host star, not new measurements of this planet.</p>${several}${sources.map((source) => sourceCard(file, source, objectId)).join('')}<p class="fineprint">${esc(file.claimBoundary)}${snapshot}${file.state === 'PARTIAL' ? ` ${esc(file.stateDetail)}` : ''}</p>`;
}

function discoveryRow(source) {
  const kind = source.candidate ? 'Exoplanet candidate' : 'Transit candidate';
  const period = source.transit?.transit_period ?? source.orbits?.[0]?.period ?? null;
  const hosts = source.match.hosts.length
    ? source.match.hosts.map((host) => (host.objectIds?.length ? `<button class="text-link" type="button" data-compare-open="${esc(host.objectIds[0])}">${esc(host.hostName)} ↗</button>` : esc(host.hostName))).join(', ') + (source.match.state === 'AMBIGUOUS' ? ' <span class="consensus-badge">ambiguous</span>' : '')
    : '<span class="muted">none known</span>';
  return [`<code>${esc(source.sourceId)}</code>`, kind, period === null ? NOT_GIVEN : `${fmt(period, 6)} d${source.transit ? '' : ' <small>(orbit)</small>'}`, source.ruwe === null || source.ruwe === undefined ? NOT_GIVEN : `${fmt(source.ruwe, 3)}${source.ruweFlag ? ' ▲' : ''}`, hosts, `<a class="text-link" href="${esc(source.archiveQueryUrl)}" rel="noopener" target="_blank">row ↗</a>`];
}

/** The "New in Gaia DR4" list, or '' when the release has no DR4 candidates. */
export function discoveriesHtml(file, { limit = 100 } = {}) {
  if (!file || !SHOWN.has(file.state)) return '';
  const all = (file.discoveries ?? []).map((id) => file.sources?.[id]).filter(Boolean);
  if (!all.length) return '';
  const near = all.filter((source) => source.match.state !== 'NO_KNOWN_HOST');
  const none = all.filter((source) => source.match.state === 'NO_KNOWN_HOST');
  const headers = ['Gaia DR4 source', 'Kind', 'Period', 'RUWE', 'Known planet host', 'Gaia archive'];
  const block = (title, rows, empty) => `<h3>${title} <span class="muted">(${rows.length.toLocaleString('en-US')})</span></h3>${rows.length ? table(headers, rows.slice(0, limit).map(discoveryRow)) + (rows.length > limit ? `<p class="muted">Showing ${limit} of ${rows.length.toLocaleString('en-US')}; every row is in <a href="data/gaia-dr4.json">gaia-dr4.json</a>.</p>` : '') : `<p class="muted">${empty}</p>`}`;
  const c = file.counts ?? {};
  return `<div class="section-heading"><p class="eyebrow">Gaia Data Release 4</p><h2 id="gaia-dr4-new-title">New in Gaia DR4</h2></div><p class="plain-language-summary">Gaia DR4 lists ${(c.exoplanetCandidates ?? 0).toLocaleString('en-US')} exoplanet candidate${c.exoplanetCandidates === 1 ? '' : 's'} and ${(c.transitCandidates ?? 0).toLocaleString('en-US')} planetary-transit candidate${c.transitCandidates === 1 ? '' : 's'} in the tables WorldsIndex has checked. <strong>A candidate is not a confirmed planet.</strong> Each row is linked to known planet hosts only through Gaia's own cross-match table or by position, and an ambiguous link is shown as ambiguous.</p>${block('Near a known planet host', near, 'None of the candidates lies at a known planet host.')}${block('No known planet host', none, 'Every candidate lies at a known planet host.')}<p class="fineprint">${esc(file.claimBoundary)} ▲ RUWE above 1.4.</p>`;
}

/** The quiet status line (from the manifest block alone). */
export function statusHtml(block) {
  if (!block) return '';
  const c = block.counts ?? {};
  const rows = hasRows(block) ? ` ${(c.objectsWithDr4 ?? 0).toLocaleString('en-US')} objects carry a Gaia DR4 panel; ${(c.exoplanetCandidates ?? 0).toLocaleString('en-US')} exoplanet and ${(c.transitCandidates ?? 0).toLocaleString('en-US')} transit candidates are listed.` : '';
  const tables = (block.tables ?? []).map((table) => `${esc(table.key)}: ${esc(table.state.toLowerCase().replaceAll('_', ' '))}`).join(' · ');
  return `<p class="table-summary">${esc(block.stateDetail)}${rows}</p>${block.state === 'NOT_YET_RELEASED' ? '' : `<p class="fineprint">${tables}${block.snapshotId ? ` · snapshot <code>${esc(block.snapshotId)}</code>` : ''}</p>`}`;
}

// The panel's few styles travel with the module (app.css keeps the site-wide cache-buster).
const STYLE = '.gaia-dr4{margin-top:1.5rem}.gaia-dr4-source{margin:1rem 0;padding:.8rem 1rem;border:1px solid var(--line);border-radius:var(--radius-small)}.gaia-dr4-designation{display:flex;flex-wrap:wrap;gap:.4rem 1rem;align-items:baseline;margin:0 0 .4rem}.gaia-dr4-facts{display:grid;grid-template-columns:max-content 1fr;gap:.2rem .8rem;margin:.5rem 0}.gaia-dr4-facts dt{color:var(--muted)}.gaia-dr4-facts dd{margin:0}.gaia-dr4-ambiguous{border-left:3px solid var(--gold);padding-left:.6rem}.gaia-dr4 table td,.gaia-dr4-new table td{overflow-wrap:anywhere}';
function addStyle() {
  if (typeof document === 'undefined' || document.getElementById('gaia-dr4-style')) return;
  const style = document.createElement('style'); style.id = 'gaia-dr4-style'; style.textContent = STYLE; document.head.append(style);
}

let filePromise = null;
export function loadGaiaDr4() {
  filePromise ??= fetch(new URL('../data/gaia-dr4.json', import.meta.url)).then((response) => (response.ok ? response.json() : null)).catch(() => null);
  return filePromise;
}

/** Appends the panel to an object tab; removes it when there is nothing to show. */
export async function mountGaiaDr4(container, object, manifestBlock, isCurrent = () => true) {
  if (container.querySelector('#gaia-dr4-section')?.dataset.objectId === object.objectId) return;
  container.querySelector('#gaia-dr4-section')?.remove();
  if (!hasRows(manifestBlock)) return;
  const file = await loadGaiaDr4();
  const html = objectPanelHtml(file, object.objectId);
  if (!html || !isCurrent() || container.querySelector('#gaia-dr4-section')) return;
  const section = document.createElement('section');
  section.id = 'gaia-dr4-section'; section.className = 'gaia-dr4'; section.dataset.objectId = object.objectId;
  section.setAttribute('aria-labelledby', 'gaia-dr4-title');
  section.innerHTML = html;
  addStyle();
  container.append(section);
}

/** Fills the Discover page's "New in Gaia DR4" section (index.html keeps it display:none until then). */
export async function mountDiscoveries(node, manifestBlock) {
  if (!node || !hasRows(manifestBlock)) return;
  const html = discoveriesHtml(await loadGaiaDr4());
  if (!html) return;
  addStyle();
  node.innerHTML = html;
  node.classList.add('has-content');
  node.style.display = '';
}

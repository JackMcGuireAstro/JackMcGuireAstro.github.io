/*
 * WorldsIndex Compare tab (Framework v2, Track 2a).
 *
 * One row per source assertion for every property of the object, from the per-object
 * worldsindex-conflict-sets.v1 product (data/conflicts/<xx>.json.gz, fetched only when the tab
 * opens): the source's original value and unit, the normalised value (unit toggle for display only;
 * the original is never altered), uncertainty or limit (limits by text and shape, never colour alone),
 * the time system and frame beside every epoch, method or instrument, source and reference (bibcode
 * link), compatibility with the selected row, and the row's state. The selection row sits above the
 * source rows and is labelled "selection, not a measurement". When a property's set is in conflict
 * every row is shown; otherwise candidate solutions can be shown or hidden. Filters by method, date,
 * source and state. "Export exactly these rows" writes ECSV and VOTable in the browser with the
 * column conventions of the release exports, plus a manifest and a citation list. Each property can
 * be copied or downloaded; properties with two or more dated publications get a time-evolution plot
 * with its table. Nothing here is fetched from a provider, and nothing about the visitor is kept.
 */

export const EXPORT_SCHEMA_VERSION = 'worldsindex-compare-export.v1';
export const SELECTION_LABEL = 'selection, not a measurement';

const SOURCE_NAMES = {
  'nasa-ps': 'NASA Planetary Systems', 'nasa-pscomppars': 'NASA PSCompPars (archive composite)', 'nasa-toi': 'NASA TESS Objects of Interest', 'nasa-koi': 'NASA Kepler Objects of Interest',
  'nasa-k2': 'NASA K2 Planets & Candidates', 'nasa-transiting-planets': 'NASA Transiting Planets table', 'nasa-microlensing': 'NASA Microlensing table', 'open-exoplanet-catalogue': 'Open Exoplanet Catalogue',
  'exoplanet-eu': 'Exoplanet.eu', 'gaia-planetary-transit': 'Gaia DR3 planetary transits', 'corot-transit-catalog': 'CoRoT transit catalogue',
};
const ACKNOWLEDGEMENTS = {
  'nasa-ps': 'This research has made use of the NASA Exoplanet Archive, which is operated by the California Institute of Technology, under contract with the National Aeronautics and Space Administration under the Exoplanet Exploration Program.',
};
for (const id of ['nasa-pscomppars', 'nasa-toi', 'nasa-koi', 'nasa-k2', 'nasa-transiting-planets', 'nasa-microlensing']) ACKNOWLEDGEMENTS[id] = ACKNOWLEDGEMENTS['nasa-ps'];

// Display units: a view of the normalised (canonical) value only.
export const DISPLAY_UNITS = {
  planet_radius: { key: 'radius', canonical: 'R_earth', options: { R_earth: { label: 'R⊕', factor: 1 }, R_jup: { label: 'R♃', factor: 1 / 11.208981 } } },
  planet_mass: { key: 'mass', canonical: 'M_earth', options: { M_earth: { label: 'M⊕', factor: 1 }, M_jup: { label: 'M♃', factor: 1 / 317.82838 } } },
  orbital_period: { key: 'period', canonical: 'day', options: { day: { label: 'days', factor: 1 }, year: { label: 'years (Julian, 365.25 d)', factor: 1 / 365.25 } } },
  semimajor_axis: { key: 'axis', canonical: 'au', options: { au: { label: 'au', factor: 1 }, R_sun: { label: 'R☉', factor: 149_597_870.7 / 695_700 } } },
};
export const DEFAULT_UNITS = { radius: 'R_earth', mass: 'M_earth', period: 'day', axis: 'au' };
const UNIT_LABELS = { day: 'days', d: 'd', hour: 'h', R_earth: 'R⊕', R_jupiter: 'R♃', M_earth: 'M⊕', M_jupiter: 'M♃', au: 'au', deg: '°', K: 'K', S_earth: 'S⊕', percent: '%', ppm: 'ppm', dimensionless: '' };
const VOUNITS = { day: 'd', d: 'd', hour: 'h', R_earth: '6378100m', R_jupiter: '71492000m', M_earth: '5.9721679e+24kg', M_jupiter: '1.8981246e+27kg', au: 'AU', deg: 'deg', K: 'K', S_earth: '', percent: 'percent', ppm: 'ppm', dimensionless: '' };
const RELATION_WORDS = {
  'statistically-inconsistent': 'statistically inconsistent', 'method-incompatible': 'different method or kind of value', 'definition-incompatible': 'different definition',
  'epoch-dependent': 'epoch or time system differs', 'identity-ambiguity': 'identity ambiguous', 'status-conflict': 'status conflict', 'revision-or-retraction': 'revision or retraction', 'not-yet-assessed': 'not assessed',
};

export function esc(value) { return String(value ?? '').replace(/[&<>'"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;' }[c])); }
export function sourceName(id) { return SOURCE_NAMES[id] || id; }
export function fmt(value, digits = 6) {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value);
  if (n === 0) return '0';
  if (Math.abs(n) >= 1e6 || Math.abs(n) < 1e-3) return n.toPrecision(digits).replace(/\.?0+e/, 'e');
  return String(Number(n.toPrecision(digits)));
}

/** A normalised (canonical-unit) quantity converted for display; unchanged for properties without a toggle. */
export function displayValue(code, value, units = DEFAULT_UNITS) {
  const spec = DISPLAY_UNITS[code];
  if (value === null || value === undefined) return { value: null, unit: spec ? spec.options[units[spec.key]]?.label ?? '' : '' };
  if (!spec) return { value, unit: null };
  const option = spec.options[units[spec.key]] ?? spec.options[DEFAULT_UNITS[spec.key]];
  return { value: value * option.factor, unit: option.label };
}

export function recordOf(product, row) { return product.records[row.record]; }
export function referenceOf(product, record) { return record && record.reference !== undefined ? product.references[record.reference] ?? null : null; }
function yearOf(record) { const match = String(record?.published || record?.updated || '').match(/(?:19|20)\d{2}/); return match ? Number(match[0]) : null; }
export function methodOf(product, row) { const record = recordOf(product, row); return row.method || record?.method || record?.instrument || 'not stated'; }

export function rowState(product, row) {
  const record = recordOf(product, row);
  const states = [];
  if (record.recordType === 'source composite') states.push('archive composite');
  if (record.isSourceDefault) states.push('default parameter set');
  if (record.candidate) states.push('candidate solution');
  if (row.limit && row.limit !== 'NONE') states.push(row.limit === 'INTERVAL' ? 'range' : 'limit');
  if (row.compat?.verdict === 'inconsistent') states.push('inconsistent');
  return states;
}

/** Whether a row passes the filters and the candidate toggle (sets in conflict always show every row). */
export function isVisible(product, block, row, view) {
  const record = recordOf(product, row);
  const conflict = Boolean(block.set?.conflict);
  if (!view.showCandidates && record.candidate && !conflict) return false;
  const f = view.filters || {};
  if (f.source && f.source !== 'all' && record.sourceId !== f.source) return false;
  if (f.method && f.method !== 'all' && methodOf(product, row) !== f.method) return false;
  if (f.state && f.state !== 'all') {
    const states = rowState(product, row);
    if (f.state === 'not-comparable' ? row.compat?.verdict !== 'not-comparable' : !states.includes(f.state)) return false;
  }
  const year = yearOf(record);
  if ((f.from || f.to) && year === null) return false;
  if (f.from && year < Number(f.from)) return false;
  if (f.to && year > Number(f.to)) return false;
  return true;
}

export function visibleRows(product, view) {
  const out = [];
  for (const block of product.properties) block.rows.forEach((row, index) => { if (isVisible(product, block, row, view)) out.push({ block, row, index }); });
  return out;
}

export function filterOptions(product) {
  const sources = new Set(), methods = new Set(), years = [];
  for (const block of product.properties) for (const row of block.rows) { const record = recordOf(product, row); sources.add(record.sourceId); methods.add(methodOf(product, row)); const year = yearOf(record); if (year) years.push(year); }
  return { sources: [...sources].sort(), methods: [...methods].sort(), minYear: years.length ? Math.min(...years) : null, maxYear: years.length ? Math.max(...years) : null };
}

// ------------------------------------------------------------------ cell text
function limitText(row, code, units) {
  const n = row.normalized || {};
  const unit = displayValue(code, 1, units).unit ?? UNIT_LABELS[n.unit] ?? '';
  const show = (value) => `${fmt(displayValue(code, value, units).value)}${unit ? ` ${unit}` : ''}`;
  if (row.limit === 'UPPER_LIMIT') return { shape: '▼', text: `upper limit: ≤ ${show(n.value)}` };
  if (row.limit === 'LOWER_LIMIT') return { shape: '▲', text: `lower limit: ≥ ${show(n.value)}` };
  if (row.limit === 'INTERVAL') return { shape: '↕', text: `range: ${show(n.lower ?? n.value)} to ${show(n.upper)}` };
  if (row.limit === 'UNKNOWN_LIMIT') return { shape: '?', text: 'limit of unstated direction' };
  if (n.errPlus === undefined && n.errMinus === undefined) return { shape: '', text: 'no uncertainty stated' };
  const plus = n.errPlus === undefined ? '—' : fmt(displayValue(code, n.errPlus, units).value, 3);
  const minus = n.errMinus === undefined ? '—' : fmt(displayValue(code, n.errMinus, units).value, 3);
  return { shape: '', text: `+${plus} / −${minus}${unit ? ` ${unit}` : ''}` };
}
function compatText(row, block) {
  const c = row.compat;
  if (!c) return 'single assertion';
  const counts = c.counts ? ` · ${c.counts[0]} consistent, ${c.counts[1]} inconsistent, ${c.counts[2]} not comparable among ${c.counts[0] + c.counts[1] + c.counts[2]} pairs` : '';
  if (c.against === undefined || c.against === null) return `${c.reason || 'reference row'}${counts}`;
  const k = block.set?.sigmaThreshold ?? 3;
  if (c.verdict === 'consistent') return `consistent with row ${c.against + 1}${c.sigma !== undefined ? ` (${fmt(c.sigma, 3)}σ, k = ${k})` : ''}${counts}`;
  if (c.verdict === 'inconsistent') return `inconsistent with row ${c.against + 1}: ${RELATION_WORDS[c.relation] || c.relation}${c.sigma !== undefined ? `, ${fmt(c.sigma, 3)}σ (k = ${k})` : ''}${counts}`;
  return `not comparable with row ${c.against + 1}: ${c.reason || RELATION_WORDS[c.relation] || c.relation}${counts}`;
}
function epochText(block, row) {
  if (block.code !== 'transit_midpoint') return 'not an epoch';
  return `time system: ${row.timeSystem || 'not stated by the source'}; frame: ${row.normalized?.frame || 'unknown (not compared)'}`;
}
function sourceCell(product, row) {
  const record = recordOf(product, row);
  const ref = referenceOf(product, record);
  const refHtml = ref ? (ref.url ? `<a href="${esc(ref.url)}" rel="noopener" target="_blank">${esc(ref.bibcode || ref.label)}</a>${ref.bibcode && ref.label && ref.label !== ref.bibcode ? ` <span class="muted">${esc(ref.label)}</span>` : ''}` : esc(ref.label)) : '<span class="muted">no reference stated</span>';
  return `<strong>${esc(sourceName(record.sourceId))}</strong><br>${refHtml}${record.published ? ` · published ${esc(record.published)}` : ''}<br><code>${esc(record.sourceRecordId)}</code> <span class="muted">${esc(row.field)}</span>`;
}
export function originalText(row) {
  const prefix = row.limit === 'UPPER_LIMIT' ? '≤ ' : row.limit === 'LOWER_LIMIT' ? '≥ ' : '';
  const unit = UNIT_LABELS[row.original.unit] ?? row.original.unit ?? '';
  return `${prefix}${row.original.value}${unit ? ` ${unit}` : ''}`;
}
export function normalizedText(block, row, units) {
  if (row.normalized?.unsupported) return 'no reviewed conversion';
  if (block.code === 'transit_midpoint') return `${fmt(row.normalized?.value, 12)} d (${row.normalized?.frame || 'frame unknown'})`;
  const shown = displayValue(block.code, row.normalized?.value, units);
  const unit = shown.unit ?? UNIT_LABELS[block.unit] ?? '';
  const prefix = row.limit === 'UPPER_LIMIT' ? '≤ ' : row.limit === 'LOWER_LIMIT' ? '≥ ' : '';
  return `${prefix}${fmt(shown.value)}${unit ? ` ${unit}` : ''}`;
}

const DEFINITION_WORDS = { mass: 'mass', 'minimum-mass': 'minimum mass M sin i', 'mass-or-minimum-mass': 'mass or M sin i (not distinguished)', 'mass-radius-estimate': 'mass from a mass-radius relation (model)', 'mass-unspecified': 'mass, provenance not stated', 'microlensing-separation': 'microlensing separation' };

function selectionRowHtml(block, selection, units) {
  const shown = displayValue(block.code, selection.value, units);
  const unit = shown.unit ?? UNIT_LABELS[selection.unit] ?? '';
  const basis = selection.basis === 'calculated' ? `calculated (${esc(selection.calculated?.formulaId)}): ${esc(selection.calculated?.formula)}; inputs ${esc((selection.calculated?.inputs || []).map((input) => `${input.name} = ${fmt(input.value)} ${input.unit} (${input.sourceRecordId})`).join('; '))}` : esc(selection.reason);
  const composite = selection.archiveComposite ? `archive composite ${selection.archiveComposite.value === undefined || selection.archiveComposite.value === null ? 'has no value' : `${fmt(displayValue(block.code, selection.archiveComposite.value, units).value)}${unit ? ` ${unit}` : ''}`}${selection.archiveComposite.matches ? ' (same value)' : ' (differs)'}` : 'no archive composite value';
  return `<tr class="selection-row" data-selection="${esc(selection.id)}"><td><strong>Selection, not a measurement</strong><br>rule ${esc(selection.ruleId)} ${esc(selection.ruleVersion)} · selector ${esc(selection.selector)} · ${esc(selection.reviewState)} · ${esc(String(selection.selectedAt).slice(0, 10))}${selection.definitionCode && block.code === 'planet_mass' ? `<br>${esc(DEFINITION_WORDS[selection.definitionCode] || selection.definitionCode)}` : ''}</td>`
    + `<td>${selection.chosenRow === null || selection.chosenRow === undefined ? (selection.basis === 'calculated' ? 'calculated' : '—') : `row ${selection.chosenRow + 1}`}</td><td>${selection.value === null || selection.value === undefined ? 'not available' : `${fmt(shown.value)}${unit ? ` ${unit}` : ''}`}${selection.basis === 'calculated' ? ' <span class="state-tag">calculated</span>' : ''}</td>`
    + `<td>${selection.errPlus === null || selection.errPlus === undefined ? (selection.basis === 'calculated' ? 'calculated values carry no uncertainty' : '—') : `+${fmt(displayValue(block.code, selection.errPlus, units).value, 3)} / −${fmt(displayValue(block.code, selection.errMinus, units).value, 3)}`}</td>`
    + `<td>${block.code === 'transit_midpoint' ? 'see the chosen row' : 'not an epoch'}</td><td>${esc(selection.basis.replaceAll('-', ' '))}</td><td>${basis}</td><td>${esc(composite)}</td></tr>`;
}

function rowHtml(product, block, row, index, units) {
  const lim = limitText(row, block.code, units);
  const states = rowState(product, row);
  return `<tr data-row="${index}" data-property="${esc(block.code)}" data-source="${esc(recordOf(product, row).sourceId)}"${row.compat?.verdict === 'inconsistent' ? ' data-inconsistent="true"' : ''}>`
    + `<td>${sourceCell(product, row)}</td>`
    + `<td><span class="row-number">${index + 1}.</span> ${esc(originalText(row))}${row.definition && block.code === 'planet_mass' ? `<br><span class="muted">${esc(DEFINITION_WORDS[row.definition] || row.definition)}</span>` : ''}</td>`
    + `<td data-normalized>${esc(normalizedText(block, row, units))}</td>`
    + `<td>${lim.shape ? `<span class="limit-shape" aria-hidden="true">${lim.shape}</span> ` : ''}${esc(lim.text)}</td>`
    + `<td>${esc(epochText(block, row))}</td>`
    + `<td>${esc(methodOf(product, row))}${row.methodClass ? `<br><span class="muted">${esc(row.methodClass)}</span>` : ''}</td>`
    + `<td>${esc(compatText(row, block))}</td>`
    + `<td>${states.length ? states.map((state) => `<span class="state-tag">${esc(state)}</span>`).join(' ') : '<span class="muted">source row</span>'}${recordOf(product, row).sourceStatus ? `<br><span class="muted">status: ${esc(recordOf(product, row).sourceStatus)}</span>` : ''}</td></tr>`;
}

function setSummary(block) {
  const set = block.set;
  if (!set) return `<p class="table-summary">One source assertion; nothing to compare.</p>`;
  const counts = set.pairCounts.byVerdict;
  const relation = set.relationLabel ? `${RELATION_WORDS[set.relationLabel] || set.relationLabel} (${set.relationLabel})` : `every assessed pair consistent within ${set.sigmaThreshold}σ`;
  return `<p class="table-summary">${set.conflict ? '<strong class="conflict-flag"><span aria-hidden="true">⚠</span> Conflict: every row is shown, none is averaged.</strong> ' : ''}${set.rowCount} source assertions from ${set.sourceIds.length} source${set.sourceIds.length === 1 ? '' : 's'}; most severe relation: ${esc(relation)}. Pairs: ${counts.consistent} consistent, ${counts.inconsistent} inconsistent, ${counts['not-comparable']} not comparable (k = ${set.sigmaThreshold}${set.maxPairwiseSigma !== undefined && set.maxPairwiseSigma !== null ? `, largest ${fmt(set.maxPairwiseSigma, 3)}σ` : ''}).</p>`;
}

// ------------------------------------------------------------------ time evolution
export function evolutionPoints(product, block, evolution, units) {
  return evolution.points.map((point) => {
    const row = block.rows[point.row];
    const record = recordOf(product, row);
    const year = Number(String(point.date).slice(0, 4)) + ((Number(String(point.date).slice(5, 7)) || 6) - 0.5) / 12;
    const shown = displayValue(block.code, point.value, units);
    return { row: point.row, date: point.date, year, value: shown.value, unit: shown.unit ?? UNIT_LABELS[evolution.unit] ?? '', limit: point.limit || 'NONE',
      errPlus: row.normalized?.errPlus === undefined ? null : displayValue(block.code, row.normalized.errPlus, units).value, errMinus: row.normalized?.errMinus === undefined ? null : displayValue(block.code, row.normalized.errMinus, units).value,
      sourceId: record.sourceId, reference: referenceOf(product, record) };
  });
}
export function evolutionSvg(points, label) {
  const width = 640, height = 260, left = 64, right = 16, top = 16, bottom = 40;
  const xs = points.map((p) => p.year), ys = points.flatMap((p) => [p.value - (p.errMinus ?? 0), p.value + (p.errPlus ?? 0)]).filter(Number.isFinite);
  let x0 = Math.min(...xs), x1 = Math.max(...xs); if (x1 - x0 < 1) { x0 -= 0.5; x1 += 0.5; }
  let y0 = Math.min(...ys), y1 = Math.max(...ys); if (!(y1 > y0)) { y0 -= Math.abs(y0 || 1) * 0.05; y1 += Math.abs(y1 || 1) * 0.05; }
  const pad = (y1 - y0) * 0.08; y0 -= pad; y1 += pad;
  const sx = (x) => left + (x - x0) / (x1 - x0) * (width - left - right), sy = (y) => top + (1 - (y - y0) / (y1 - y0)) * (height - top - bottom);
  const marks = points.map((p) => {
    const x = sx(p.year), y = sy(p.value);
    const bar = p.limit === 'NONE' && (p.errPlus || p.errMinus) ? `<line x1="${x.toFixed(1)}" x2="${x.toFixed(1)}" y1="${sy(p.value + (p.errPlus ?? 0)).toFixed(1)}" y2="${sy(p.value - (p.errMinus ?? 0)).toFixed(1)}" class="evo-bar"/>` : '';
    const shape = p.limit === 'UPPER_LIMIT' ? `<path d="M${(x - 6).toFixed(1)} ${(y - 5).toFixed(1)} L${(x + 6).toFixed(1)} ${(y - 5).toFixed(1)} L${x.toFixed(1)} ${(y + 6).toFixed(1)} Z" class="evo-limit"/>`
      : p.limit === 'LOWER_LIMIT' ? `<path d="M${(x - 6).toFixed(1)} ${(y + 5).toFixed(1)} L${(x + 6).toFixed(1)} ${(y + 5).toFixed(1)} L${x.toFixed(1)} ${(y - 6).toFixed(1)} Z" class="evo-limit"/>`
        : `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="4.5" class="evo-point"/>`;
    return `${bar}${shape}`;
  }).join('');
  const ticks = (a, b, n) => Array.from({ length: n + 1 }, (_, i) => a + (b - a) * i / n);
  const xAxis = ticks(x0, x1, 4).map((x) => `<text x="${sx(x).toFixed(1)}" y="${height - 14}" text-anchor="middle" class="evo-tick">${x.toFixed(x1 - x0 < 4 ? 1 : 0)}</text>`).join('');
  const yAxis = ticks(y0, y1, 4).map((y) => `<text x="${left - 6}" y="${(sy(y) + 3).toFixed(1)}" text-anchor="end" class="evo-tick">${esc(fmt(y, y1 - y0 < Math.abs(y1) * 1e-3 ? 7 : 4))}</text><line x1="${left}" x2="${width - right}" y1="${sy(y).toFixed(1)}" y2="${sy(y).toFixed(1)}" class="evo-grid"/>`).join('');
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label)}" class="evolution-plot" preserveAspectRatio="xMidYMid meet">${yAxis}${xAxis}<line x1="${left}" x2="${left}" y1="${top}" y2="${height - bottom}" class="evo-axis"/><line x1="${left}" x2="${width - right}" y1="${height - bottom}" y2="${height - bottom}" class="evo-axis"/>${marks}</svg>`;
}
function evolutionHtml(product, block, units) {
  return (block.evolution || []).map((evolution) => {
    const points = evolutionPoints(product, block, evolution, units);
    if (points.length < 2) return '';
    const unit = points[0].unit;
    const what = block.code === 'planet_mass' ? `${block.label.replace(' (all mass definitions)', '')}: ${DEFINITION_WORDS[evolution.definitionCode] || evolution.definitionCode}` : block.label;
    const label = `${what} by publication date, ${points.length} publications from ${points[0].date} to ${points[points.length - 1].date}${unit ? `, in ${unit}` : ''}. Circles are measurements with error bars; downward triangles are upper limits, upward triangles lower limits.`;
    const rows = points.map((p) => `<tr><td>${esc(p.date)}</td><td>${p.limit === 'UPPER_LIMIT' ? '≤ ' : p.limit === 'LOWER_LIMIT' ? '≥ ' : ''}${esc(fmt(p.value))}${unit ? ` ${esc(unit)}` : ''}${p.limit !== 'NONE' ? ` (${esc(p.limit.replace('_', ' ').toLowerCase())})` : ''}</td><td>${p.errPlus === null && p.errMinus === null ? '—' : `+${esc(fmt(p.errPlus, 3))} / −${esc(fmt(p.errMinus, 3))}`}</td><td>${p.reference?.url ? `<a href="${esc(p.reference.url)}" rel="noopener" target="_blank">${esc(p.reference.bibcode || p.reference.label)}</a>` : esc(p.reference?.label || '—')}</td><td>${esc(sourceName(p.sourceId))}</td></tr>`).join('');
    return `<figure class="evolution" data-evolution="${esc(block.code)}:${esc(evolution.definitionCode)}"><figcaption>Time evolution: ${esc(what)}</figcaption>${evolutionSvg(points, label)}<details class="plot-table-fallback"><summary>Time-evolution table (${points.length} publications)</summary><div class="table-wrap" tabindex="0" role="region" aria-label="${esc(what)} by publication date"><table><caption class="visually-hidden">${esc(what)} by publication date</caption><thead><tr><th scope="col">Published</th><th scope="col">Value</th><th scope="col">Uncertainty</th><th scope="col">Reference</th><th scope="col">Source</th></tr></thead><tbody>${rows}</tbody></table></div></details></figure>`;
  }).join('');
}

// ------------------------------------------------------------------ export
export const EXPORT_COLUMNS = [
  ['object_id', 'string', 'meta.id;meta.main', null, 'Stable WorldsIndex object identifier.'],
  ['name', 'string', 'meta.id', null, 'Preferred public designation in this release.'],
  ['host_name', 'string', 'meta.id.parent', null, 'Host star or system name.'],
  ['row_kind', 'string', 'meta.code', null, '"source assertion" (one source row) or "selection, not a measurement".'],
  ['property_code', 'string', 'meta.code', null, 'WorldsIndex property code.'],
  ['definition_code', 'string', 'meta.code', null, 'Definition of the value (rows of different definitions are never compared).'],
  ['source_id', 'string', 'meta.ref', null, 'Source of the row.'],
  ['source_record_id', 'string', 'meta.id.cross', null, 'Stable identifier of the source record (the release detail and conflict shards use the same id).'],
  ['record_type', 'string', 'meta.code', null, 'published solution, source composite, mission candidate row, catalog row, transit solution or microlensing solution.'],
  ['is_source_default', 'bool', 'meta.code', null, 'True when the source marks the row as its default parameter set; empty when it has no such flag.'],
  ['source_field', 'string', 'meta.id', null, 'The source\'s own column.'],
  ['value', 'float64', 'stat.value', null, 'Value exactly as the source states it, in unit (never converted).'],
  ['error_upper', 'float64', 'stat.error;stat.max', null, 'Positive uncertainty as the source states it (absolute), in unit.'],
  ['error_lower', 'float64', 'stat.error;stat.min', null, 'Negative uncertainty as the source states it (absolute), in unit.'],
  ['unit', 'string', 'meta.unit', null, 'Unit of value and its errors (VOUnits: d, h, deg, AU, K, 6378100m = Earth radius, 71492000m = Jupiter radius, 5.9721679e+24kg = Earth mass, 1.8981246e+27kg = Jupiter mass; percent or ppm for a depth; empty when dimensionless).'],
  ['limit_type', 'string', 'meta.code;stat', null, 'NONE, LOWER_LIMIT, UPPER_LIMIT, INTERVAL or UNKNOWN_LIMIT.'],
  ['time_system', 'string', 'time.scale', null, 'For an epoch: the time system the source states; empty otherwise.'],
  ['normalized_value', 'float64', 'stat.value', null, 'The value in normalized_unit after the reviewed conversion (canonical unit; the page\'s display toggle does not change it).'],
  ['normalized_unit', 'string', 'meta.unit', null, 'Canonical unit of the property (VOUnits as unit).'],
  ['normalized_error_upper', 'float64', 'stat.error;stat.max', null, 'Positive uncertainty in normalized_unit.'],
  ['normalized_error_lower', 'float64', 'stat.error;stat.min', null, 'Negative uncertainty in normalized_unit.'],
  ['frame', 'string', 'time.scale', null, 'For an epoch: the normalised time frame (BJD_TDB, BJD_TCB, ...); empty otherwise.'],
  ['method', 'string', 'meta.code.class', null, 'Method or instrument the source states for the row.'],
  ['compatibility_relation', 'string', 'meta.code.class', null, 'Relation to the reference row in the contract vocabulary; empty when consistent.'],
  ['compatibility_verdict', 'string', 'meta.code.qual', null, 'consistent, inconsistent or not-comparable.'],
  ['compatibility_sigma', 'float64', 'stat.value', null, 'Combined-sigma distance to the reference row; empty when not assessed.'],
  ['reference', 'string', 'meta.bib', null, 'Reference the source cites for the row.'],
  ['reference_url', 'string', 'meta.ref.url', null, 'URL of that reference.'],
  ['bibcode', 'string', 'meta.bib.bibcode', null, 'ADS bibcode of that reference.'],
  ['published', 'string', 'time.publiYear', null, 'Publication date the source states.'],
  ['updated', 'string', 'time.processing', null, 'Row update date the source states.'],
];
const num = (value) => (value === null || value === undefined || value === '' || !Number.isFinite(Number(value)) ? null : Number(value));

/** Export rows (arrays in EXPORT_COLUMNS order) for exactly the visible source rows and the selection rows shown above them. */
export function exportRows(product, view, selectionsShown = true) {
  const out = [];
  const head = [product.objectId, product.name, product.hostName ?? null];
  for (const block of product.properties) {
    const visible = block.rows.map((row, index) => ({ row, index })).filter(({ row }) => isVisible(product, block, row, view));
    if (selectionsShown) {
      for (const selection of block.selections || []) {
        if (selection.basis === 'not-available') continue;
        const chosen = selection.chosenRow === null || selection.chosenRow === undefined ? null : block.rows[selection.chosenRow];
        const record = chosen ? recordOf(product, chosen) : null;
        const ref = referenceOf(product, record);
        out.push([...head, SELECTION_LABEL, block.code, selection.definitionCode, record?.sourceId ?? null, selection.chosenSourceRecordIds.join(' '), record?.recordType ?? (selection.basis === 'calculated' ? 'calculated' : null), null, chosen?.field ?? null,
          selection.value ?? null, selection.errPlus ?? null, selection.errMinus ?? null, VOUNITS[selection.unit] ?? selection.unit, selection.limitType, null,
          selection.value ?? null, VOUNITS[selection.unit] ?? selection.unit, selection.errPlus ?? null, selection.errMinus ?? null, null, selection.basis === 'calculated' ? `calculated: ${selection.calculated?.formulaId}` : selection.basis, null, null, null,
          ref?.label ?? null, ref?.url ?? null, ref?.bibcode ?? null, record?.published ?? null, null]);
      }
    }
    for (const { row } of visible) {
      const record = recordOf(product, row);
      const ref = referenceOf(product, record);
      out.push([...head, 'source assertion', block.code, row.definition, record.sourceId, record.sourceRecordId, record.recordType, record.isSourceDefault ?? null, row.field,
        num(row.original.value), row.original.errPlus ?? null, row.original.errMinus ?? null, VOUNITS[row.original.unit] ?? row.original.unit, row.limit, row.timeSystem ?? null,
        row.normalized?.value ?? null, VOUNITS[block.unit] ?? block.unit, row.normalized?.errPlus ?? null, row.normalized?.errMinus ?? null, row.normalized?.frame ?? null, methodOf(product, row),
        row.compat?.relation ?? null, row.compat?.verdict ?? null, row.compat?.sigma ?? null, ref?.label ?? null, ref?.url ?? null, ref?.bibcode ?? null, record.published ?? null, record.updated ?? null]);
    }
  }
  return out;
}

function csvCell(value) {
  if (value === null || value === undefined) return '';
  if (typeof value === 'boolean') return value ? 'True' : 'False';
  const text = String(value);
  return /[",\r\n]|^\s|\s$/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}
function xml(value) { return String(value ?? '').replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;').replaceAll('"', '&quot;'); }

export function exportEcsv(rows, meta) {
  const lines = ['# %ECSV 1.0', '# ---', '# datatype:'];
  for (const [name, datatype, ucd, unit, description] of EXPORT_COLUMNS) lines.push(`# - ${JSON.stringify({ name, datatype, ...(unit ? { unit } : {}), description, meta: { ucd } })}`);
  lines.push(`# meta: ${JSON.stringify(meta)}`, '# schema: astropy-2.0', "# delimiter: ','", EXPORT_COLUMNS.map((column) => column[0]).join(','));
  for (const row of rows) lines.push(row.map(csvCell).join(','));
  return `${lines.join('\n')}\n`;
}
export function exportVotable(rows, meta) {
  const types = { string: 'char', float64: 'double', bool: 'boolean' };
  const fields = EXPORT_COLUMNS.map(([name, datatype, ucd, unit, description]) => `<FIELD name="${name}" datatype="${types[datatype]}"${datatype === 'string' ? ' arraysize="*"' : ''} ucd="${xml(ucd)}"${unit ? ` unit="${xml(unit)}"` : ''}><DESCRIPTION>${xml(description)}</DESCRIPTION></FIELD>`);
  const params = Object.entries(meta).filter(([, value]) => typeof value !== 'object').map(([name, value]) => `<PARAM name="${xml(name)}" datatype="char" arraysize="*" value="${xml(value)}"/>`);
  const body = rows.map((row) => `<TR>${row.map((value, index) => value === null || value === undefined ? '<TD/>' : EXPORT_COLUMNS[index][1] === 'bool' ? `<TD>${value ? 'true' : 'false'}</TD>` : `<TD>${xml(value)}</TD>`).join('')}</TR>`);
  return `${['<?xml version="1.0" encoding="UTF-8"?>', '<VOTABLE version="1.5" xmlns="http://www.ivoa.net/xml/VOTable/v1.3">', `<DESCRIPTION>${xml(meta.claim_boundary)}</DESCRIPTION>`, '<RESOURCE type="results">', `<TABLE name="worldsindex_compare" nrows="${rows.length}">`, ...params, ...fields, '<DATA><TABLEDATA>', ...body, '</TABLEDATA></DATA>', '</TABLE>', '</RESOURCE>', '</VOTABLE>'].join('\n')}\n`;
}
export function citationList(product, rows) {
  const refs = new Map(), sources = new Set();
  const at = (name) => EXPORT_COLUMNS.findIndex((column) => column[0] === name);
  for (const row of rows) {
    if (row[at('source_id')]) sources.add(row[at('source_id')]);
    const label = row[at('reference')], url = row[at('reference_url')], bibcode = row[at('bibcode')];
    if (label || url) refs.set(`${label}|${url}`, { label, url, bibcode });
  }
  const lines = [`Citations for the ${rows.length} exported WorldsIndex compare rows of ${product.name} (${product.objectId}).`, '', 'References cited by the exported rows:'];
  for (const ref of [...refs.values()].sort((a, b) => String(a.label).localeCompare(String(b.label)))) lines.push(`- ${ref.label || ref.bibcode}${ref.bibcode ? ` [${ref.bibcode}]` : ''}${ref.url ? ` ${ref.url}` : ''}`);
  if (!refs.size) lines.push('- (no row states a reference)');
  lines.push('', 'Sources (rights and acknowledgement terms of each source travel with the release: registry.json.gz):');
  for (const source of [...sources].sort()) lines.push(`- ${sourceName(source)} (${source})${ACKNOWLEDGEMENTS[source] ? `: ${ACKNOWLEDGEMENTS[source]}` : ''}`);
  lines.push('', 'WorldsIndex: https://jackmcguireastro.github.io/worldsindex/ (selections are versioned choices of source rows, not measurements; nothing is averaged).');
  return `${lines.join('\n')}\n`;
}
export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
export function exportMeta(product, view, context = {}) {
  return {
    name: 'worldsindex_compare', object_id: product.objectId, object_name: product.name, schema_version: EXPORT_SCHEMA_VERSION,
    release_generated_at: context.manifest?.generatedAt ?? '', atlas_generated_at: context.manifest?.atlasGeneratedAt ?? '', atlas_sha256: context.manifest?.atlasSha256 ?? '',
    conflict_policy: context.manifest?.conflictSets?.policyVersion ?? '', selection_rule: context.manifest?.conflictSets?.selectionRule ?? '',
    filters: JSON.stringify({ ...view.filters, showCandidates: view.showCandidates }),
    claim_boundary: 'Exactly the rows shown on the Compare tab when exported: one row per source assertion with its original value, plus the selection rows (a selection, not a measurement). Nothing is averaged; normalised values are in the canonical unit whatever the display toggle.',
  };
}
export async function buildExport(product, view, context = {}) {
  const rows = exportRows(product, view);
  const meta = exportMeta(product, view, context);
  const ecsv = exportEcsv(rows, meta), votable = exportVotable(rows, meta), citations = citationList(product, rows);
  const stem = `worldsindex-compare-${product.objectId}`;
  const manifest = {
    schemaVersion: EXPORT_SCHEMA_VERSION, objectId: product.objectId, name: product.name, rowCount: rows.length, sourceAssertionRows: rows.filter((row) => row[3] === 'source assertion').length,
    selectionRows: rows.filter((row) => row[3] === SELECTION_LABEL).length, filters: view.filters, showCandidates: view.showCandidates, displayUnits: view.units,
    release: { generatedAt: meta.release_generated_at, atlasGeneratedAt: meta.atlas_generated_at, atlasSha256: meta.atlas_sha256, conflictPolicy: meta.conflict_policy, selectionRule: meta.selection_rule },
    columns: EXPORT_COLUMNS.map(([name, datatype, ucd, unit]) => ({ name, datatype, ucd, unit })),
    files: { ecsv: { name: `${stem}.ecsv`, bytes: new TextEncoder().encode(ecsv).length, sha256: await sha256Hex(ecsv) }, votable: { name: `${stem}.vot`, bytes: new TextEncoder().encode(votable).length, sha256: await sha256Hex(votable) }, citations: { name: `${stem}-citations.txt`, sha256: await sha256Hex(citations) } },
    claimBoundary: meta.claim_boundary,
  };
  return { rows, ecsv, votable, citations, manifest: `${JSON.stringify(manifest, null, 2)}\n`, stem };
}

/** Tab-separated text of one property's visible rows (copy) and the same as CSV (download). */
export function subsectionText(product, block, view, separator = '\t') {
  const header = ['row', 'source', 'source_record_id', 'field', 'original', 'normalized', 'uncertainty_or_limit', 'epoch_frame', 'method', 'compatibility', 'state', 'reference'];
  const lines = [header.join(separator)];
  const cell = (value) => (separator === ',' ? csvCell(value) : String(value ?? '').replace(/[\t\n]/g, ' '));
  for (const selection of block.selections || []) if (selection.basis !== 'not-available') lines.push(['selection', SELECTION_LABEL, selection.chosenSourceRecordIds.join(' '), '', '', `${selection.value} ${selection.unit}`, '', '', selection.basis, '', selection.reviewState, selection.reason].map(cell).join(separator));
  block.rows.forEach((row, index) => {
    if (!isVisible(product, block, row, view)) return;
    const record = recordOf(product, row), ref = referenceOf(product, record);
    lines.push([index + 1, record.sourceId, record.sourceRecordId, row.field, originalText(row), normalizedText(block, row, view.units), limitText(row, block.code, view.units).text, epochText(block, row), methodOf(product, row), compatText(row, block), rowState(product, row).join('; '), ref ? `${ref.label}${ref.url ? ` ${ref.url}` : ''}` : ''].map(cell).join(separator));
  });
  return `${lines.join('\n')}\n`;
}

// ------------------------------------------------------------------ rendering
function propertySection(product, block, view) {
  const visible = block.rows.map((row, index) => ({ row, index })).filter(({ row }) => isVisible(product, block, row, view));
  const hiddenCandidates = !view.showCandidates && !block.set?.conflict ? block.rows.filter((row) => recordOf(product, row).candidate).length : 0;
  const selections = (block.selections || []).filter((item) => item.basis !== 'not-available');
  const id = `compare-${block.code}`;
  return `<section class="compare-property" aria-labelledby="${id}" data-compare-property="${esc(block.code)}"${block.set?.conflict ? ' data-conflict="true"' : ''}>`
    + `<h3 id="${id}">${esc(block.label)} <span class="muted">· ${visible.length} of ${block.rows.length} rows shown</span></h3>${setSummary(block)}`
    + (hiddenCandidates ? `<p class="fineprint">${hiddenCandidates} candidate solution${hiddenCandidates === 1 ? ' is' : 's are'} hidden; use “Show candidate solutions”.</p>` : '')
    + `<div class="table-wrap" tabindex="0" role="region" aria-label="${esc(block.label)} rows"><table class="compare-table"><caption class="visually-hidden">${esc(block.label)}: one row per source assertion${selections.length ? ', after the selection row' : ''}</caption>`
    + '<thead><tr><th scope="col">Source and reference</th><th scope="col">Original value</th><th scope="col">Normalised value</th><th scope="col">Uncertainty or limit</th><th scope="col">Epoch and frame</th><th scope="col">Method or instrument</th><th scope="col">Compatibility</th><th scope="col">State</th></tr></thead><tbody>'
    + selections.map((selection) => selectionRowHtml(block, selection, view.units)).join('')
    + visible.map(({ row, index }) => rowHtml(product, block, row, index, view.units)).join('')
    + `</tbody></table></div><div class="subsection-actions"><button type="button" class="text-link" data-copy="${esc(block.code)}">Copy ${esc(block.label.toLowerCase())} rows</button><button type="button" class="text-link" data-download="${esc(block.code)}">Download ${esc(block.label.toLowerCase())} rows (CSV)</button></div>`
    + evolutionHtml(product, block, view.units) + '</section>';
}

function statusSection(product) {
  const status = product.status;
  if (!status?.assertions?.length && !product.flags?.length) return '';
  const rows = (status?.assertions || []).map((item) => `<tr><td>${esc(sourceName(item.sourceId))}<br><code>${esc(item.sourceRecordId)}</code></td><td>${esc(item.sourceStatus)}</td><td>${esc(item.normalized)}</td><td>${esc(item.rule)}</td></tr>`).join('');
  const flags = (product.flags || []).map((flag) => `<tr><td><strong>${esc(flag.flag.replaceAll('_', ' '))}</strong></td><td>${esc(flag.detail)}</td><td>${esc(flag.rule)}</td><td>${flag.rows.map((row) => `<code>${esc(row.sourceRecordId)}</code>`).join(' ')}</td></tr>`).join('');
  return `<section class="compare-property" aria-labelledby="compare-status"><h3 id="compare-status">Status claims and reconciliation flags</h3>`
    + `<p class="table-summary">${status?.conflict ? `<strong class="conflict-flag"><span aria-hidden="true">⚠</span> The sources state different statuses (${esc(RELATION_WORDS[status.relation] || status.relation)}).</strong>` : 'The sources that state a status agree.'} Flags follow the published Exo-MerCat rules (period mismatch over 10 %, fallback merge, duplicate and misnamed entries, status reconciliation).</p>`
    + (rows ? `<div class="table-wrap" tabindex="0" role="region" aria-label="Status claims by source"><table><caption class="visually-hidden">Status claims by source</caption><thead><tr><th scope="col">Source</th><th scope="col">Source status</th><th scope="col">Normalised</th><th scope="col">Rule</th></tr></thead><tbody>${rows}</tbody></table></div>` : '')
    + (flags ? `<div class="table-wrap" tabindex="0" role="region" aria-label="Reconciliation flags"><table><caption class="visually-hidden">Reconciliation flags</caption><thead><tr><th scope="col">Flag</th><th scope="col">Detail</th><th scope="col">Rule</th><th scope="col">Rows</th></tr></thead><tbody>${flags}</tbody></table></div>` : '<p class="fineprint">No reconciliation flag is raised for this object.</p>')
    + '</section>';
}

export function compareHtml(product, view) {
  const options = filterOptions(product);
  const option = (value, label, current) => `<option value="${esc(value)}"${String(current) === String(value) ? ' selected' : ''}>${esc(label)}</option>`;
  const unitGroup = (key, legend, choices) => `<fieldset class="unit-toggle"><legend>${esc(legend)}</legend>${choices.map(([value, label]) => `<label><input type="radio" name="compare-unit-${key}" value="${esc(value)}" data-unit="${esc(key)}"${view.units[key] === value ? ' checked' : ''}> ${esc(label)}</label>`).join('')}</fieldset>`;
  const totals = product.properties.reduce((sum, block) => sum + block.rows.length, 0);
  const shown = visibleRows(product, view).length;
  const conflicts = product.properties.filter((block) => block.set?.conflict).length;
  return `<h2 id="compare-tab-title">Compare every source assertion</h2>`
    + `<p class="plain-language-summary">One row per source assertion: each source's own value and unit, the value normalised to one unit, its uncertainty or limit, the time system of every epoch, and how it relates to the selected row. Rows are compared only after unit, definition, frame, epoch and identity are assessed; two values are consistent within k = ${esc(product.properties.find((block) => block.set)?.set.sigmaThreshold ?? 3)} combined sigma. The selection row follows rule ${esc(product.properties.flatMap((block) => block.selections || [])[0]?.policyVersion || 'nasa-pscomppars-like.v1')} and is a selection, not a measurement. Nothing is averaged.</p>`
    + `<p class="table-summary" data-compare-status aria-live="polite">${shown} of ${totals} source rows shown across ${product.properties.length} propert${product.properties.length === 1 ? 'y' : 'ies'}; ${conflicts} propert${conflicts === 1 ? 'y is' : 'ies are'} in conflict.</p>`
    + `<form class="compare-controls" data-compare-controls><div class="filter-grid">`
    + `<label>Method or instrument<select data-filter="method">${option('all', 'All methods', view.filters.method || 'all')}${options.methods.map((m) => option(m, m, view.filters.method)).join('')}</select></label>`
    + `<label>Source<select data-filter="source">${option('all', 'All sources', view.filters.source || 'all')}${options.sources.map((s) => option(s, sourceName(s), view.filters.source)).join('')}</select></label>`
    + `<label>State<select data-filter="state">${[['all', 'All rows'], ['default parameter set', 'Default parameter sets'], ['archive composite', 'Archive composite'], ['candidate solution', 'Candidate solutions'], ['limit', 'Limits'], ['range', 'Ranges'], ['inconsistent', 'Inconsistent with the selected row'], ['not-comparable', 'Not comparable']].map(([v, l]) => option(v, l, view.filters.state || 'all')).join('')}</select></label>`
    + `<label>Published from (year)<input type="number" inputmode="numeric" data-filter="from" min="1980" max="2100" placeholder="${esc(options.minYear ?? '')}" value="${esc(view.filters.from ?? '')}"></label>`
    + `<label>Published to (year)<input type="number" inputmode="numeric" data-filter="to" min="1980" max="2100" placeholder="${esc(options.maxYear ?? '')}" value="${esc(view.filters.to ?? '')}"></label>`
    + `</div><div class="unit-toggles">${unitGroup('radius', 'Radius', [['R_earth', 'R⊕'], ['R_jup', 'R♃']])}${unitGroup('mass', 'Mass', [['M_earth', 'M⊕'], ['M_jup', 'M♃']])}${unitGroup('period', 'Period', [['day', 'days'], ['year', 'years']])}${unitGroup('axis', 'Semi-major axis', [['au', 'au'], ['R_sun', 'R☉']])}</div>`
    + `<label class="check"><input type="checkbox" data-toggle="candidates"${view.showCandidates ? ' checked' : ''}> Show candidate solutions (always shown for a property in conflict)</label>`
    + `<p class="fineprint">Unit choices change only the normalised and uncertainty columns; the original value column always shows the source's own value and unit.</p></form>`
    + `<div class="compare-export"><button type="button" data-export="ecsv">Export exactly these rows (ECSV)</button><button type="button" data-export="votable">Export exactly these rows (VOTable)</button><button type="button" data-export="manifest">Export manifest (JSON)</button><button type="button" data-export="citations">Citation list (text)</button><p class="fineprint" data-export-status aria-live="polite"></p></div>`
    + product.properties.map((block) => propertySection(product, block, view)).join('')
    + statusSection(product);
}

function download(name, text, type) {
  const blob = new Blob([text], { type }), url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = name; link.hidden = true; document.body.append(link); link.click();
  setTimeout(() => { URL.revokeObjectURL(url); link.remove(); }, 1000);
}

/** Renders the tab into `container` and wires its controls; returns the live view state. */
export function mountCompare(container, product, context = {}) {
  const view = { filters: { method: 'all', source: 'all', state: 'all', from: '', to: '' }, units: { ...DEFAULT_UNITS }, showCandidates: false };
  const render = (focusSelector = null) => {
    container.innerHTML = compareHtml(product, view);
    container.dataset.compareRows = String(visibleRows(product, view).length);
    if (focusSelector) container.querySelector(focusSelector)?.focus();
  };
  render();
  container.addEventListener('change', (event) => {
    const target = event.target;
    if (target.matches('[data-filter]')) { view.filters[target.dataset.filter] = target.value; render(`[data-filter="${target.dataset.filter}"]`); }
    else if (target.matches('[data-unit]')) { view.units[target.dataset.unit] = target.value; render(`[data-unit="${target.dataset.unit}"][value="${target.value}"]`); }
    else if (target.matches('[data-toggle="candidates"]')) { view.showCandidates = target.checked; render('[data-toggle="candidates"]'); }
  });
  container.addEventListener('submit', (event) => event.preventDefault());
  container.addEventListener('click', async (event) => {
    const button = event.target.closest('button');
    if (!button || !container.contains(button)) return;
    const status = container.querySelector('[data-export-status]');
    if (button.dataset.export) {
      const built = await buildExport(product, view, context);
      const kind = button.dataset.export;
      if (kind === 'ecsv') download(`${built.stem}.ecsv`, built.ecsv, 'text/plain');
      if (kind === 'votable') download(`${built.stem}.vot`, built.votable, 'application/x-votable+xml');
      if (kind === 'manifest') download(`${built.stem}-manifest.json`, built.manifest, 'application/json');
      if (kind === 'citations') download(`${built.stem}-citations.txt`, built.citations, 'text/plain');
      if (status) status.textContent = `${built.rows.length} rows prepared (${kind}); the manifest names the release, the filters and the SHA-256 of each file.`;
      return;
    }
    const code = button.dataset.copy || button.dataset.download;
    const block = code ? product.properties.find((item) => item.code === code) : null;
    if (!block) return;
    if (button.dataset.download) { download(`worldsindex-${product.objectId}-${code}.csv`, subsectionText(product, block, view, ','), 'text/csv'); if (status) status.textContent = `${block.label} rows downloaded as CSV.`; return; }
    try { await navigator.clipboard.writeText(subsectionText(product, block, view)); if (status) status.textContent = `${block.label} rows copied as tab-separated text.`; } catch { if (status) status.textContent = 'The browser did not allow copying; use the CSV download instead.'; }
  });
  return view;
}

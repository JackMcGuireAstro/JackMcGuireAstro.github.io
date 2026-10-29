// Calculated quantities, population plots and ephemeris staleness on the planet page
// (Framework v2, Track 3b). Everything here is read from release files built by
// scripts/worldsindex_derived.mjs; the browser only formats and places a marker.
const base = new URL('../data/', import.meta.url);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const shards = new Map();
let summaryPromise = null;

async function gunzipJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Data unavailable (${response.status})`);
  return JSON.parse(await new Response(response.body.pipeThrough(new DecompressionStream('gzip'))).text());
}
async function bucketOf(objectId) {
  const hash = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(objectId));
  return new Uint8Array(hash)[0].toString(16).padStart(2, '0');
}
/** The derived block of one object (null when the release has none for it). */
export async function loadDerived(objectId) {
  const bucket = await bucketOf(objectId);
  if (!shards.has(bucket)) shards.set(bucket, gunzipJson(new URL(`derived/${bucket}.json.gz`, base)).catch(() => ({ objects: {} })));
  return (await shards.get(bucket)).objects?.[objectId] ?? null;
}
export function loadSummary() {
  summaryPromise ??= fetch(new URL('derived-summary.json', base)).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  return summaryPromise;
}
/** Quadratic limb-darkening coefficients for a band label, or null (Track 3b light-curve model). */
export function limbDarkeningFor(block, bandLabel) {
  const row = block?.quantities?.find((q) => q.code === 'limb_darkening' && q.state === 'calculated');
  if (!row) return null;
  const label = String(bandLabel ?? '').toLowerCase();
  const band = /tess/.test(label) ? 'TESS' : /kepler|k2/.test(label) ? 'Kepler' : /\b(i|ip|sdss_i|cousins i)\b|i-band|i'/.test(label) ? 'I' : /\b(r|rp|sdss_r)\b|r-band|r'/.test(label) ? 'R' : /\bv\b|v-band/.test(label) ? 'V' : 'TESS';
  const pair = row.coefficients?.[band];
  return pair ? { band, u1: pair[0], u2: pair[1], formulaId: row.formulaId } : null;
}

const fmt = (value) => {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return '—';
  const n = Number(value), a = Math.abs(n);
  if (a !== 0 && (a >= 1e5 || a < 1e-3)) return n.toExponential(3);
  return String(Number(n.toPrecision(4)));
};
const UNIT_LABEL = { L_sun: 'L☉', S_earth: 'S⊕', K: 'K', 'g/cm3': 'g cm⁻³', mas: 'mas', R_earth: 'R⊕', M_earth: 'M⊕', au: 'au', fraction: '', dimensionless: '', yr: 'yr', 'm/s': 'm s⁻¹', uas: 'µas', ppm: 'ppm', hour: 'h', class: '' };
const INPUT_NAMES = { orbital_period: 'period', planet_radius: 'planet radius', planet_mass: 'planet mass', semimajor_axis: 'semi-major axis', eccentricity: 'eccentricity', inclination: 'inclination',
  impact_parameter: 'impact parameter', radius_ratio: 'Rp/R★', scaled_semimajor_axis: 'a/R★', stellar_teff: 'stellar Teff', stellar_radius: 'stellar radius', stellar_mass: 'stellar mass', distance: 'distance', j_mag: 'J magnitude', k_mag: 'K magnitude' };

function valueCell(q, formula) {
  const unit = UNIT_LABEL[formula?.unit] ?? formula?.unit ?? '';
  if (q.code === 'habitable_zone') {
    const e = q.edges;
    return `conservative ${fmt(e.conservativeInner)}–${fmt(e.conservativeOuter)} au; optimistic ${fmt(e.optimisticInner)}–${fmt(e.optimisticOuter)} au${q.semimajorAxisIn ? `<br><small>orbit: ${esc(q.semimajorAxisIn)}</small>` : ''}`;
  }
  if (q.code === 'habitable_zone_orbit_fraction') return `conservative ${fmt(q.conservative * 100)} %, optimistic ${fmt(q.optimistic * 100)} %`;
  if (q.code === 'limb_darkening') return Object.entries(q.coefficients ?? {}).map(([band, [u1, u2]]) => `${esc(band)}: u₁ ${fmt(u1)}, u₂ ${fmt(u2)}`).join('<br>');
  if (q.code === 'planet_class') return `${esc(q.value)} <small>(by ${esc(q.basis)})</small>`;
  const uncertainty = q.errPlus !== undefined && (q.errPlus || q.errMinus) ? ` <span class="value-uncertainty">+${fmt(q.errPlus)} / −${fmt(q.errMinus)}</span>` : '';
  const extra = q.semimajorAxisOverRocheLimit ? `<br><small>a / a_Roche = ${fmt(q.semimajorAxisOverRocheLimit)}</small>` : '';
  return `${fmt(q.value)} ${esc(unit)}${uncertainty}${extra}`;
}
function inputsCell(q, formula, block) {
  const names = formula?.inputs ?? [];
  const items = names.filter((n) => block.inputs?.[n]).map((n) => {
    const input = block.inputs[n];
    const origin = input.derivedFrom ? `from ${input.derivedFrom.map((d) => INPUT_NAMES[d] ?? d).join(' and ')}` : `${esc(input.source ?? '')}${input.reference ? ` · ${esc(input.reference)}` : ''}${input.sourceRecordIds?.length ? ` · <code>${esc(input.sourceRecordIds[0])}</code>` : ''}`;
    return `<li>${esc(INPUT_NAMES[n] ?? n)} = ${fmt(input.value)} ${esc(input.unit ?? '')}${input.limit ? ` (${esc(input.limit.toLowerCase().replace('_', ' '))})` : ''} — ${origin}</li>`;
  });
  const notes = [q.massBasis ? `Mass: ${esc(q.massBasis)}.` : '', q.uncertaintyOmits?.length ? `No reported error for ${esc(q.uncertaintyOmits.map((n) => INPUT_NAMES[n] ?? n).join(', '))}; the uncertainty leaves it out.` : '',
    q.assumptions ? `Assumed: ${esc(Object.entries(q.assumptions).map(([k, v]) => `${k} ${v}`).join(', '))}.` : ''].filter(Boolean).join(' ');
  return `<details><summary>Inputs</summary><ul>${items.join('')}</ul>${notes ? `<p class="fineprint">${notes}</p>` : ''}</details>`;
}
function formulaCell(formula) {
  if (!formula) return '';
  const citation = formula.citation?.url ? `<a href="${esc(formula.citation.url)}" rel="noopener">${esc(formula.citation.text)}</a>` : esc(formula.citation?.text ?? '');
  return `<details><summary>Formula</summary><p><code>${esc(formula.formula)}</code></p><p class="fineprint">${citation} · <code>${esc(formula.id)}</code></p></details>`;
}

async function populationFigure(container, objectId, summary) {
  const plots = Object.entries(summary?.plots ?? {}).filter(([, p]) => p.kind === 'scatter');
  if (!plots.length) return;
  const figure = container.querySelector('#derived-population');
  figure.innerHTML = `<div class="curve-toolbar"><label>Population plot<select id="derived-plot">${plots.map(([id, p]) => `<option value="${esc(id)}">${esc(p.title)}</option>`).join('')}</select></label>
    <a href="${new URL('populations.json', base)}" download>Download the plot tables (JSON)</a></div><div id="derived-plot-svg" class="population-plot"></div><p id="derived-plot-summary" class="fineprint"></p>`;
  const block = await loadDerived(objectId);
  const draw = async () => {
    const id = figure.querySelector('#derived-plot').value, spec = summary.plots[id];
    const holder = figure.querySelector('#derived-plot-svg');
    try {
      const text = await fetch(new URL(spec.path, base)).then((r) => (r.ok ? r.text() : Promise.reject(new Error(r.status))));
      holder.innerHTML = text;
    } catch (error) { holder.textContent = `The plot could not be loaded (${error.message}).`; return; }
    const svg = holder.querySelector('svg');
    const point = (axis, value, lo, hi) => { if (!(value > 0)) return null; const v = Math.log10(value), a = Math.log10(axis.min), b = Math.log10(axis.max); return v < a || v > b ? null : lo + ((v - a) / (b - a)) * (hi - lo); };
    const xv = block?.inputs?.[{ periodDays: 'orbital_period', massEarth: 'planet_mass', radiusEarth: 'planet_radius' }[spec.x.key]]?.value;
    const yv = block?.inputs?.[{ periodDays: 'orbital_period', massEarth: 'planet_mass', radiusEarth: 'planet_radius' }[spec.y.key]]?.value;
    const f = spec.frame, x = point(spec.x, xv, f.left, f.width - f.right), y = point(spec.y, yv, f.height - f.bottom, f.top);
    let note = spec.summary;
    if (svg && x !== null && y !== null && !(spec.y.key === 'radiusEarth' && spec.x.key === 'massEarth' && block?.massIsMinimum)) {
      svg.insertAdjacentHTML('beforeend', `<g class="population-marker"><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="7" fill="none" stroke="#d62f2f" stroke-width="2.5"/><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2" fill="#d62f2f"/></g>`);
      note += ` The red ring marks this planet (${fmt(xv)}, ${fmt(yv)}).`;
    } else note += ' This planet is not on this plot (a value is missing, a limit, a minimum mass, or outside the axes).';
    figure.querySelector('#derived-plot-summary').textContent = note;
  };
  figure.querySelector('#derived-plot').addEventListener('change', draw);
  await draw();
}

/** Mount the calculated-quantities section into `container` (the System & orbit tab). */
export async function mountDerived(container, object) {
  let section = container.querySelector('#derived-section');
  if (!section) { section = document.createElement('section'); section.id = 'derived-section'; section.className = 'derived-quantities'; section.setAttribute('aria-labelledby', 'derived-title'); container.append(section); }
  if (section.dataset.objectId === object.objectId) return;
  section.dataset.objectId = object.objectId;
  section.innerHTML = '<div class="loading-shell" role="status"><span class="loading-pulse" aria-hidden="true"></span><span>Opening the calculated quantities…</span></div>';
  const [block, summary] = await Promise.all([loadDerived(object.objectId), loadSummary()]);
  if (section.dataset.objectId !== object.objectId) return;
  if (!block || !summary) {
    section.innerHTML = '<h2 id="derived-title">Calculated quantities</h2><p class="plain-language-summary">This release calculates no quantities for this object: it has no selected planet values (candidates, false positives and objects without a NASA parameter set are not calculated).</p>';
    return;
  }
  const formulas = new Map((summary.formulas ?? []).map((f) => [f.code, f]));
  const rows = block.quantities.map((q) => {
    const formula = formulas.get(q.code);
    const label = esc(formula?.label ?? q.code);
    if (q.state !== 'calculated') return `<tr data-state="not-computed"><th scope="row">${label}</th><td colspan="2"><span class="consensus-badge" data-consensus="non-comparable">not computed</span> ${esc(q.reason?.text ?? '')}</td><td>${formulaCell(formula)}</td></tr>`;
    const archive = q.archive ? `<br><small>Archive value: ${fmt(q.archive.value)} ${esc(UNIT_LABEL[q.archive.unit] ?? q.archive.unit ?? '')}</small>` : '';
    const tag = q.label === 'estimate' ? 'estimate' : q.label === 'estimated' ? 'estimated' : 'calculated';
    return `<tr data-state="calculated" data-code="${esc(q.code)}"><th scope="row">${label} <span class="consensus-badge" data-consensus="calculated">${tag}</span></th><td>${valueCell(q, formula)}${archive}</td><td>${inputsCell(q, formula, block)}</td><td>${formulaCell(formula)}</td></tr>`;
  });
  const staleness = block.ephemerisStaleness;
  const stalenessHtml = staleness ? `<div class="derived-staleness"><h3>Transit-time uncertainty</h3><p><strong>Priority: ${esc(staleness.priority)}</strong>${staleness.rank ? ` · rank ${staleness.rank.toLocaleString()} of the release's transiting planets (1 = least certain)` : ''}.
    Predicted 1σ mid-transit uncertainty: ${fmt(staleness.sigma1MonthMin)} min at +1 month, ${fmt(staleness.sigma6MonthMin)} min at +6 months, ${fmt(staleness.sigma12MonthMin)} min at +12 months (threshold ${fmt(staleness.thresholdMin)} min).</p>
    <details><summary>Rule</summary><p class="fineprint">${esc(summary.stalenessRule ?? 'See staleness.json for the rule and the full ranking.')}</p><p class="fineprint"><a href="${new URL('staleness.json', base)}" download>Download the full ranking (JSON)</a></p></details></div>` : '';
  const csv = ['quantity,state,value,err_plus,err_minus,unit,formula_id,reason', ...block.quantities.map((q) => [q.code, q.state, q.value ?? '', q.errPlus ?? '', q.errMinus ?? '', formulas.get(q.code)?.unit ?? '', q.formulaId, q.reason?.text ?? ''].map((v) => `"${String(v).replaceAll('"', '""')}"`).join(','))].join('\n');
  section.innerHTML = `<div class="section-heading"><div><p class="eyebrow">Calculated · not measured</p><h2 id="derived-title">Calculated quantities</h2></div></div>
    <p class="plain-language-summary">WorldsIndex calculates these from the values this page selects and one stated stellar parameter set. They are not measurements and not a published solution; each names its formula, inputs and sources. A missing input or a limit gives “not computed” with the reason — nothing is filled with a default.</p>
    <div class="table-wrap"><table class="derived-table"><caption class="sr-only">Calculated quantities for ${esc(object.name)}</caption><thead><tr><th scope="col">Quantity</th><th scope="col">Value</th><th scope="col">Inputs</th><th scope="col">Formula</th></tr></thead><tbody>${rows.join('')}</tbody></table></div>
    <div class="curve-toolbar"><a id="derived-csv" download="${esc(object.objectId)}-calculated.csv" href="data:text/csv;charset=utf-8,${encodeURIComponent(csv)}">Download these values (CSV)</a></div>
    <details class="source-row"><summary>Rules and uncertainty</summary><p class="fineprint">${esc(summary.stellarRule)}</p><p class="fineprint">${esc(summary.samePublicationRule ?? '')}</p><p class="fineprint">${esc(summary.propagation)}</p><p class="fineprint">${esc(summary.claimBoundary)}</p></details>
    ${stalenessHtml}<figure id="derived-population" class="derived-population"></figure>`;
  await populationFigure(section, object.objectId, summary);
}

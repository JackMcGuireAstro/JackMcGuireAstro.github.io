// Live TESS / Kepler / K2 light curves from MAST, fetched in the visitor's browser.
// The MAST search API (/api/v0/invoke) lets web pages read it directly; the light-curve
// FITS files do not, so they come through the site's pass-through relay (address in
// /live-config.json). Nothing is stored by WorldsIndex.
//
// Lookup: the host star's TIC / KIC / EPIC number (from the catalog rows) is matched
// exactly against MAST's `target_name`; only when no identifier is known, or the
// identifier finds nothing, does it fall back to a 5″ cone around the catalog position.

const MAST = 'https://mast.stsci.edu/api/v0/invoke';
const DOWNLOAD = 'https://mast.stsci.edu/api/v0.1/Download/file?uri=';
const OBSERVATION_COLUMNS = 'obsid,obs_collection,obs_id,target_name,sequence_number,t_min,t_max,provenance_name,project,t_exptime';
const CONE_DEG = 0.0014; // ≈5″

// lightkurve's "default" QUALITY bitmasks: cadences with these bits set are unusable
// (attitude tweak, safe mode, coarse/Earth point, desaturation, manual exclude; for Kepler
// and K2 also the thruster-firing/no-fine-point bits). "strict" keeps only QUALITY = 0.
export const QUALITY_MASKS = { TESS: 175, Kepler: 1130799, K2: 1130799 };

export class LiveDataError extends Error {}
const fail = (message) => new LiveDataError(message);

async function mast(request) {
  const body = 'request=' + encodeURIComponent(JSON.stringify({ format: 'json', pagesize: 2000, ...request }));
  let r;
  try {
    r = await fetch(MAST, { method: 'POST', credentials: 'omit', headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body });
  } catch {
    throw fail('MAST’s search service could not be reached. Check your connection, or try again in a few minutes');
  }
  if (!r.ok) throw fail(`MAST’s search service answered with an error (HTTP ${r.status}). Try again in a few minutes`);
  let json;
  try { json = await r.json(); } catch { throw fail('MAST’s search service sent an answer that is not readable'); }
  if (json.status && json.status !== 'COMPLETE') throw fail(`MAST’s search did not finish (${json.status}). Try again in a few minutes`);
  return json.data || [];
}

export async function relayAddress() {
  try {
    const r = await fetch(new URL('../../live-config.json', import.meta.url), { cache: 'no-cache' });
    const config = r.ok ? await r.json() : {};
    return typeof config.relay === 'string' && /^https:\/\//.test(config.relay) ? config.relay.replace(/\/+$/, '') : '';
  } catch { return ''; }
}

// ------------------------------------------------------------------ identifiers
const digits = (value, pattern) => {
  const m = pattern.exec(String(value ?? '').trim());
  return m ? String(Number(m[1])) : null;
};
// The host star's TESS Input Catalog, Kepler Input Catalog and K2 EPIC numbers, from the
// object's own name fields and its catalog rows (TOI `tid`, KOI `kepid`, PS/K2 `tic_id`,
// K2 `epic_hostname`/`epic_candname`).
export function hostIdentifiers(object, records = []) {
  const tic = new Set(), kic = new Set(), epic = new Set();
  const add = (set, value) => { if (value && value !== '0') set.add(value); };
  for (const name of [object?.hostName, object?.name]) {
    add(tic, digits(name, /^TIC[\s-]*(\d{1,12})\b/i));
    add(kic, digits(name, /^KIC[\s-]*(\d{1,9})\b/i));
    add(epic, digits(name, /^EPIC[\s-]*(\d{9})\b/i));
  }
  for (const record of records) {
    const v = record?.values || {};
    add(tic, digits(v.tic_id, /^(?:TIC\s*)?(\d{1,12})$/i));
    if (record.sourceId === 'nasa-toi') add(tic, digits(v.tid, /^(\d{1,12})$/));
    if (record.sourceId === 'nasa-koi') add(kic, digits(v.kepid, /^(\d{1,9})$/));
    add(epic, digits(v.epic_hostname, /^EPIC\s*(\d{9})$/i));
    add(epic, digits(v.epic_candname, /^EPIC\s*(\d{9})\.\d+$/i));
  }
  return { tic: [...tic], kic: [...kic], epic: [...epic] };
}
// A planet's TIC number from the NASA Exoplanet Archive (PSCompPars), through the relay's
// read-only TAP route. Used only when the catalog rows carry no identifier.
export async function lookupTicByName(planetName, relay, fetcher = fetch) {
  if (!relay || !planetName) return null;
  const query = `select tic_id from pscomppars where pl_name = '${String(planetName).replace(/'/g, "''")}'`;
  const url = `https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=${encodeURIComponent(query)}&format=json`;
  const r = await fetcher(`${relay}/?url=${encodeURIComponent(url)}`, { credentials: 'omit' });
  if (!r.ok) return null;
  const rows = await r.json();
  return Array.isArray(rows) ? digits(rows[0]?.tic_id, /^(?:TIC\s*)?(\d{1,12})$/i) : null;
}
export const identifierLabel = (ids) => [
  ...ids.tic.map((n) => `TIC ${n}`), ...ids.kic.map((n) => `KIC ${n}`), ...ids.epic.map((n) => `EPIC ${n}`),
].join(', ');

// MAST `target_name` spellings: TESS uses the bare TIC number (high-level science products
// may zero-pad it), Kepler kplr + 9 digits, K2 ktwo + 9 digits.
export function mastTargetNames(ids) {
  const names = [];
  for (const n of ids.tic || []) names.push(n, n.padStart(16, '0'), `TIC ${n}`);
  for (const n of ids.kic || []) names.push(`kplr${n.padStart(9, '0')}`);
  for (const n of ids.epic || []) names.push(`ktwo${n.padStart(9, '0')}`);
  return [...new Set(names)];
}

// ------------------------------------------------------------------ products
// Kepler long-cadence file timestamps (yyyydddhhmmss) mark the end of each quarter.
const KEPLER_QUARTER_ENDS = ['2009131105131', '2009166043257', '2009259160929', '2009350155506', '2010078095331', '2010174085026',
  '2010265121752', '2010355172524', '2011073133259', '2011177032512', '2011271113734', '2012004120508', '2012088054726',
  '2012179063303', '2012277125453', '2013011073258', '2013098041711', '2013131215648'];
export function keplerTimestampDate(stamp) {
  const m = /^(\d{4})(\d{3})(\d{2})(\d{2})(\d{2})$/.exec(String(stamp));
  return m ? Date.UTC(+m[1], 0, +m[2], +m[3], +m[4], +m[5]) : null;
}
export function keplerQuarter(stamp) {
  const when = keplerTimestampDate(stamp);
  if (when === null) return null;
  const q = KEPLER_QUARTER_ENDS.findIndex((end) => keplerTimestampDate(end) >= when - 86400000);
  return q < 0 ? null : q;
}
const GROUPS = [
  ['tess-2min', 'TESS 2-minute (SPOC)'], ['tess-ffi', 'TESS full-frame images (TESS-SPOC)'],
  ['kepler-long', 'Kepler 30-minute'], ['kepler-short', 'Kepler 1-minute'], ['k2-long', 'K2 30-minute'], ['k2-short', 'K2 1-minute'],
];
export const PRODUCT_GROUPS = Object.fromEntries(GROUPS);
const ffiCadence = (sector) => (sector <= 26 ? '30-min' : sector <= 55 ? '10-min' : '200-s');
const megabytes = (bytes) => (Number(bytes) > 0 ? ` · ${(Number(bytes) / 1048576).toFixed(Number(bytes) < 1048576 ? 2 : 1)} MB` : '');

// One MAST product row → a light-curve file the viewer can read, or null. Only SPOC-processed
// TESS light curves, Kepler and K2 mission light curves; the URI must also pass the relay.
export function classifyProduct(product, observation = {}) {
  const uri = String(product?.dataURI || '');
  if (!/^mast:(?:TESS|Kepler|K2|HLSP\/tess-spoc)\/[A-Za-z0-9_\-./]+\.fits$/.test(uri) || uri.includes('..')) return null;
  const file = uri.split('/').pop();
  let m, kind, mission, sequence, target, cadence, label;
  if ((m = /^tess\d{13}-s(\d{4})-(\d{16})-\d{4}-s_lc\.fits$/.exec(file)) && uri.startsWith('mast:TESS/')) {
    kind = 'tess-2min'; mission = 'TESS'; sequence = Number(m[1]); target = `TIC ${Number(m[2])}`; cadence = '2-min';
    label = `TESS sector ${sequence} · 2-min`;
  } else if ((m = /^hlsp_tess-spoc_tess_phot_(\d{16})-s(\d{4})_tess_v\d+_lc\.fits$/.exec(file)) && uri.startsWith('mast:HLSP/tess-spoc/')) {
    kind = 'tess-ffi'; mission = 'TESS'; sequence = Number(m[2]); target = `TIC ${Number(m[1])}`; cadence = ffiCadence(sequence);
    label = `TESS sector ${sequence} · ${cadence} full-frame`;
  } else if ((m = /^kplr(\d{9})-(\d{13})_(llc|slc)\.fits$/.exec(file)) && uri.startsWith('mast:Kepler/')) {
    mission = 'Kepler'; kind = m[3] === 'slc' ? 'kepler-short' : 'kepler-long'; target = `KIC ${Number(m[1])}`;
    sequence = keplerQuarter(m[2]); cadence = m[3] === 'slc' ? '1-min' : '30-min';
    const when = keplerTimestampDate(m[2]);
    label = `Kepler quarter ${sequence ?? '?'}${m[3] === 'slc' && when ? ` (month ending ${new Date(when).toISOString().slice(0, 10)})` : ''} · ${cadence}`;
  } else if ((m = /^ktwo(\d{9})-c(\d{2,3})_(llc|slc)\.fits$/.exec(file)) && uri.startsWith('mast:K2/')) {
    mission = 'K2'; kind = m[3] === 'slc' ? 'k2-short' : 'k2-long'; target = `EPIC ${Number(m[1])}`;
    sequence = Number(m[2]); cadence = m[3] === 'slc' ? '1-min' : '30-min';
    label = `K2 campaign ${sequence} · ${cadence}`;
  } else return null;
  return {
    uri, file, kind, mission, sequence, target, cadence, group: PRODUCT_GROUPS[kind],
    pipeline: kind === 'tess-ffi' ? 'TESS-SPOC' : kind === 'tess-2min' ? 'SPOC' : `${mission} pipeline`,
    label: label + megabytes(product.size), shortLabel: label, sizeBytes: Number(product.size) || null,
    tMin: Number(observation.t_min) || 0, obsid: String(product.parent_obsid ?? product.obsID ?? ''),
  };
}
export function sortProducts(products) {
  const order = Object.fromEntries(GROUPS.map(([kind], i) => [kind, i]));
  return products.slice().sort((a, b) => order[a.kind] - order[b.kind] || (b.sequence ?? -1) - (a.sequence ?? -1) || a.file.localeCompare(b.file));
}
function acceptObservation(o) {
  const collection = String(o.obs_collection || '');
  return ['TESS', 'Kepler', 'K2'].includes(collection) || (collection === 'HLSP' && /^tess-spoc$/i.test(String(o.provenance_name || '')));
}

// Light-curve files for a host star: by identifier first, then (only if that finds nothing)
// by a 5″ cone around the catalog position. Returns {products, searchedBy, names, targets}.
export async function findLightcurves({ ids = { tic: [], kic: [], epic: [] }, ra, dec } = {}, { invoke = mast } = {}) {
  const names = mastTargetNames(ids);
  const filters = [{ paramName: 'dataproduct_type', values: ['timeseries'] }, { paramName: 'obs_collection', values: ['TESS', 'Kepler', 'K2', 'HLSP'] }];
  let observations = [], searchedBy = null;
  if (names.length) {
    searchedBy = 'identifier';
    observations = (await invoke({ service: 'Mast.Caom.Filtered', params: { columns: OBSERVATION_COLUMNS, filters: [{ paramName: 'target_name', values: names }, ...filters] } })).filter(acceptObservation);
  }
  if (!observations.length && Number.isFinite(ra) && Number.isFinite(dec)) {
    searchedBy = 'position';
    observations = (await invoke({ service: 'Mast.Caom.Filtered.Position', params: { columns: OBSERVATION_COLUMNS, filters, position: `${ra}, ${dec}, ${CONE_DEG}` } })).filter(acceptObservation);
  }
  if (!observations.length) return { products: [], searchedBy, names, targets: [] };
  const byObs = new Map(observations.map((o) => [String(o.obsid), o]));
  const ids50 = [...byObs.keys()];
  const rows = [];
  for (let i = 0; i < ids50.length && i < 400; i += 50) {
    rows.push(...await invoke({ service: 'Mast.Caom.Products', params: { obsid: ids50.slice(i, i + 50).join(',') } }));
  }
  const seen = new Set(), products = [];
  for (const row of rows) {
    const product = classifyProduct(row, byObs.get(String(row.parent_obsid)) || byObs.get(String(row.obsID)) || {});
    if (!product || seen.has(product.uri)) continue;
    seen.add(product.uri); products.push(product);
  }
  const sorted = sortProducts(products);
  return { products: sorted, searchedBy, names, targets: [...new Set(sorted.map((p) => p.target))] };
}

// ------------------------------------------------------------------ minimal FITS reader
const BLOCK = 2880;
function headerCards(bytes, offset) {
  const cards = {}; let pos = offset;
  const decoder = new TextDecoder('ascii');
  for (;;) {
    if (pos + BLOCK > bytes.length) throw fail('the FITS header ended early (the file is incomplete)');
    const block = decoder.decode(bytes.subarray(pos, pos + BLOCK)); pos += BLOCK;
    let end = false;
    for (let i = 0; i < BLOCK; i += 80) {
      const card = block.slice(i, i + 80), key = card.slice(0, 8).trim();
      if (key === 'END') { end = true; break; }
      if (card[8] !== '=') continue;
      const raw = card.slice(10);
      let value;
      if (raw.trimStart().startsWith("'")) value = raw.match(/'((?:[^']|'')*)'/)?.[1].replace(/''/g, "'").trimEnd() ?? '';
      else {
        value = raw.split('/')[0].trim();
        if (value === 'T' || value === 'F') value = value === 'T';
        else if (value !== '' && Number.isFinite(Number(value.replace(/D/i, 'E')))) value = Number(value.replace(/D/i, 'E'));
      }
      if (!(key in cards)) cards[key] = value;
    }
    if (end) return { cards, dataStart: pos };
  }
}
function dataSize(cards) {
  const bitpix = Math.abs(Number(cards.BITPIX || 0)), naxis = Number(cards.NAXIS || 0);
  if (!naxis) return 0;
  let n = 1; for (let i = 1; i <= naxis; i++) n *= Number(cards['NAXIS' + i] || 0);
  n = (Number(cards.GCOUNT || 1) * (n + Number(cards.PCOUNT || 0)) * bitpix) / 8;
  return Math.ceil(n / BLOCK) * BLOCK;
}
const SIZES = { L: 1, X: 1, B: 1, I: 2, J: 4, K: 8, A: 1, E: 4, D: 8, C: 8, M: 16, P: 8, Q: 16 };
const NUMERIC = new Set(['L', 'B', 'I', 'J', 'K', 'E', 'D']);

// Read named columns (first element of each cell) of the first binary-table extension.
// TSCALn/TZEROn are applied and TNULLn integers become NaN. Missing columns come back null.
export function readFitsTable(buffer, wanted) {
  const bytes = new Uint8Array(buffer), view = new DataView(buffer);
  const primary = headerCards(bytes, 0);
  let offset = primary.dataStart + dataSize(primary.cards), ext = null;
  for (let hdu = 1; hdu < 8 && offset < bytes.length; hdu++) {
    const next = headerCards(bytes, offset);
    if (next.cards.XTENSION === 'BINTABLE') { ext = next; break; }
    offset = next.dataStart + dataSize(next.cards);
  }
  if (!ext) throw fail('the file has no binary table, so it is not a light-curve file');
  const h = ext.cards;
  const rows = Number(h.NAXIS2), width = Number(h.NAXIS1), fields = Number(h.TFIELDS);
  if (!(rows >= 0) || !(width > 0) || !(fields > 0)) throw fail('the light-curve table header is incomplete');
  if (ext.dataStart + rows * width > bytes.length) throw fail('the file is truncated (fewer table rows than its header declares)');
  const columns = {}; let at = 0;
  for (let i = 1; i <= fields; i++) {
    const form = String(h['TFORM' + i] ?? '').trim().match(/^(\d*)([LXBIJKAEDCMPQ])/);
    if (!form) throw fail(`column ${i} uses an unsupported format (${h['TFORM' + i]})`);
    const repeat = form[1] === '' ? 1 : Number(form[1]), type = form[2];
    columns[String(h['TTYPE' + i] ?? `COL${i}`).trim()] = { offset: at, type, repeat, scale: Number(h['TSCAL' + i] ?? 1), zero: Number(h['TZERO' + i] ?? 0), nul: h['TNULL' + i] };
    at += type === 'X' ? Math.ceil(repeat / 8) : repeat * SIZES[type];
  }
  if (at !== width) throw fail('the table’s column widths do not add up to its row width');
  const out = { header: h, primaryHeader: primary.cards, rows };
  for (const name of wanted) {
    const col = columns[name];
    if (!col || !NUMERIC.has(col.type) || col.repeat < 1) { out[name] = null; continue; }
    const values = new Float64Array(rows), integer = 'BIJK'.includes(col.type), nul = col.nul === undefined ? null : Number(col.nul);
    for (let r = 0; r < rows; r++) {
      const p = ext.dataStart + r * width + col.offset;
      let v = col.type === 'D' ? view.getFloat64(p, false) : col.type === 'E' ? view.getFloat32(p, false)
        : col.type === 'J' ? view.getInt32(p, false) : col.type === 'I' ? view.getInt16(p, false)
        : col.type === 'K' ? Number(view.getBigInt64(p, false)) : col.type === 'L' ? (bytes[p] === 84 ? 1 : 0) : view.getUint8(p);
      if (integer && nul !== null && v === nul) v = NaN;
      else if (col.scale !== 1 || col.zero !== 0) v = v * col.scale + col.zero;
      values[r] = v;
    }
    out[name] = values;
  }
  return out;
}

function missionOf(product, primary) {
  if (product?.mission) return product.mission;
  const scope = `${primary.TELESCOP ?? ''} ${primary.MISSION ?? ''}`.toUpperCase();
  return scope.includes('TESS') ? 'TESS' : scope.includes('K2') ? 'K2' : scope.includes('KEPLER') ? 'Kepler' : '';
}
export function qualitySelectionText(mission, quality, column) {
  return quality === 'strict'
    ? `Rows with a finite TIME and ${column} and QUALITY = 0 (strict).`
    : `Rows with a finite TIME and ${column} whose QUALITY has none of the bits in ${QUALITY_MASKS[mission] ?? 'any'} (lightkurve’s default ${mission || 'mission'} mask: attitude tweaks, safe mode, coarse or Earth pointing, momentum dumps, manual exclusions${mission === 'TESS' ? '' : ', thruster firings'}).`;
}

// Light-curve FITS → WorldsIndex points [BJD_TDB, flux, error, mission, stream].
// PDCSAP_FLUX when present (systematics-corrected), else SAP_FLUX. `quality`: 'default' drops
// cadences with lightkurve's default QUALITY bits, 'strict' keeps only QUALITY = 0.
export function pointsFromLightcurve(buffer, product = {}, { quality = 'default' } = {}) {
  const table = readFitsTable(buffer, ['TIME', 'PDCSAP_FLUX', 'PDCSAP_FLUX_ERR', 'SAP_FLUX', 'SAP_FLUX_ERR', 'QUALITY']);
  const h = table.header, primary = table.primaryHeader;
  const ref = Number(h.BJDREFI ?? primary.BJDREFI ?? 0) + Number(h.BJDREFF ?? primary.BJDREFF ?? 0);
  const usePdc = table.PDCSAP_FLUX && table.PDCSAP_FLUX.some(Number.isFinite);
  const flux = usePdc ? table.PDCSAP_FLUX : table.SAP_FLUX, err = usePdc ? table.PDCSAP_FLUX_ERR : table.SAP_FLUX_ERR;
  if (!table.TIME || !flux) throw fail('the file has no TIME or flux column, so it is not a light-curve file');
  if (!(ref > 2400000)) throw fail('the file does not say which barycentric time its TIME column counts from (BJDREFI)');
  const mission = missionOf(product, primary);
  const mask = quality === 'strict' ? 0xffffffff : QUALITY_MASKS[mission] ?? 0xffffffff;
  const stream = product.shortLabel || product.label || product.file || 'MAST light curve';
  const points = []; let droppedQuality = 0, droppedMissing = 0;
  for (let i = 0; i < table.rows; i++) {
    const t = table.TIME[i], f = flux[i], e = err ? err[i] : NaN, q = table.QUALITY ? table.QUALITY[i] : 0;
    if (!Number.isFinite(t) || !Number.isFinite(f)) { droppedMissing += 1; continue; }
    if (((q >>> 0) & mask) !== 0) { droppedQuality += 1; continue; }
    points.push([t + ref, f, Number.isFinite(e) ? e : 0, mission || 'MAST', stream]);
  }
  const sequence = primary.SECTOR ?? primary.QUARTER ?? primary.CAMPAIGN ?? null;
  const target = primary.TICID ? `TIC ${primary.TICID}` : primary.KEPLERID ? `${mission === 'K2' ? 'EPIC' : 'KIC'} ${primary.KEPLERID}` : String(primary.OBJECT || h.OBJECT || '');
  return {
    points, rawRowCount: table.rows, droppedQuality, droppedMissing, mission, sequence, target,
    valueColumn: usePdc ? 'PDCSAP_FLUX' : 'SAP_FLUX', timeSystem: h.TIMESYS || primary.TIMESYS || 'TDB',
    cadenceMinutes: Number(h.TIMEDEL) > 0 ? Number(h.TIMEDEL) * 1440 : null, object: primary.OBJECT || h.OBJECT || '',
  };
}

// HTTP status from the relay or MAST → a sentence a visitor can act on.
export function downloadErrorMessage(status, product = {}) {
  if (status === 400) return product.kind === 'tess-ffi'
    ? 'the site’s relay does not yet pass TESS-SPOC full-frame light curves (a relay update is pending). Choose a 2-minute sector, or try again after the update'
    : 'the site’s relay does not pass this kind of file';
  if (status === 403) return 'the relay only serves pages on jackmcguireastro.github.io';
  if (status === 404) return 'MAST no longer has this file at that address';
  if (status === 413) return 'the file is larger than the relay’s 40 MB limit';
  if (status === 429) return 'MAST is limiting requests right now. Wait a minute and try again';
  if (status >= 500) return `MAST or the relay did not answer properly (HTTP ${status}). Try again in a few minutes`;
  return `the download failed (HTTP ${status})`;
}

async function sha256Hex(buffer) {
  try {
    const digest = await crypto.subtle.digest('SHA-256', buffer);
    return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
  } catch { return 'not recorded (this browser cannot compute it)'; }
}

export async function loadLightcurve(product, relay, { quality = 'default', fetcher = fetch } = {}) {
  if (!relay) throw fail('the site’s live-data relay is not configured, so MAST files cannot be fetched');
  const target = DOWNLOAD + product.uri;
  let r;
  try { r = await fetcher(`${relay}/?url=${encodeURIComponent(target)}`, { credentials: 'omit' }); }
  catch { throw fail('the relay could not be reached. Check your connection, or try again in a few minutes'); }
  if (!r.ok) throw fail(downloadErrorMessage(r.status, product));
  const buffer = await r.arrayBuffer();
  let parsed;
  try { parsed = pointsFromLightcurve(buffer, product, { quality }); }
  catch (error) { throw fail(`the file arrived but could not be read: ${error.message}`); }
  if (!parsed.points.length) throw fail(`the file has no usable measurements after removing ${parsed.droppedMissing.toLocaleString()} empty and ${parsed.droppedQuality.toLocaleString()} quality-flagged rows`);
  return {
    key: product.uri, label: product.shortLabel || product.label, source: `${product.mission} ${product.pipeline || ''} via MAST (live)`.replace(/\s+/g, ' '),
    sourceUrl: target, points: parsed.points, pointCount: parsed.points.length, rawRowCount: parsed.rawRowCount,
    droppedQuality: parsed.droppedQuality, droppedMissing: parsed.droppedMissing, sequence: parsed.sequence, target: parsed.target,
    retrievedAt: new Date().toISOString(), rawSha256: await sha256Hex(buffer), valueUnit: 'e-/s', valueColumn: parsed.valueColumn,
    qualitySelection: qualitySelectionText(parsed.mission, quality, parsed.valueColumn),
    uncertaintyNote: 'Fetched live from MAST in your browser; not part of the WorldsIndex release.',
    targetScope: 'host-system', live: true,
  };
}

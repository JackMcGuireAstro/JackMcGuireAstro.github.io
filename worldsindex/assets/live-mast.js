// Live TESS / Kepler / K2 light curves from MAST, fetched in the visitor's browser.
// The MAST search API allows web pages to read it directly; the light-curve FITS
// files themselves do not, so they come through the site's pass-through relay
// (address in /live-config.json). Nothing is stored by WorldsIndex.

const MAST = 'https://mast.stsci.edu/api/v0/invoke';
const DOWNLOAD = 'https://mast.stsci.edu/api/v0.1/Download/file?uri=';

async function mast(request) {
  const body = 'request=' + encodeURIComponent(JSON.stringify({format: 'json', pagesize: 500, ...request}));
  const r = await fetch(MAST, {method: 'POST', credentials: 'omit', headers: {'Content-Type': 'application/x-www-form-urlencoded'}, body});
  if (!r.ok) throw Error(`MAST search failed (${r.status})`);
  const json = await r.json();
  if (json.status && json.status !== 'COMPLETE') throw Error(`MAST search ${json.status}`);
  return json.data || [];
}

export async function relayAddress() {
  try {
    const r = await fetch(new URL('../../live-config.json', import.meta.url), {cache: 'no-cache'});
    const config = r.ok ? await r.json() : {};
    return typeof config.relay === 'string' && /^https:\/\//.test(config.relay) ? config.relay.replace(/\/+$/, '') : '';
  } catch { return ''; }
}

// Light-curve products of TESS SPOC, Kepler and K2 within ~5″ of the position.
export async function findLightcurves(ra, dec) {
  if (!Number.isFinite(ra) || !Number.isFinite(dec)) return [];
  const observations = await mast({service: 'Mast.Caom.Filtered.Position', params: {
    columns: 'obsid,obs_collection,obs_id,target_name,sequence_number,t_min,t_max',
    filters: [{paramName: 'obs_collection', values: ['TESS', 'Kepler', 'K2']},
              {paramName: 'dataproduct_type', values: ['timeseries']}],
    position: `${ra}, ${dec}, 0.0014`}});
  if (!observations.length) return [];
  const products = await mast({service: 'Mast.Caom.Products', params: {obsid: observations.slice(0, 60).map(o => o.obsid).join(',')}});
  const byObs = new Map(observations.map(o => [String(o.obsid), o]));
  return products
    .filter(p => /^mast:(TESS|Kepler|K2)\/[A-Za-z0-9_\-./]+_(lc|llc|slc)\.fits$/.test(p.dataURI || ''))
    .map(p => {
      const o = byObs.get(String(p.parent_obsid)) || byObs.get(String(p.obsID)) || {};
      const mission = o.obs_collection || (p.dataURI.split(':')[1] || '').split('/')[0];
      const unit = mission === 'TESS' ? 'sector' : mission === 'K2' ? 'campaign' : 'quarter';
      const cadence = /_slc\.fits$/.test(p.dataURI) ? '1 min' : /_llc\.fits$/.test(p.dataURI) ? '30 min' : '2 min';
      return {uri: p.dataURI, mission, label: `${mission} ${unit} ${o.sequence_number ?? '?'} · ${cadence}`, sequence: Number(o.sequence_number) || 0, tMin: Number(o.t_min) || 0};
    })
    .filter((p, i, all) => all.findIndex(q => q.uri === p.uri) === i)
    .sort((a, b) => b.tMin - a.tMin);
}

// ------------------------------------------------------------ minimal FITS reader
function headerCards(bytes, offset) {
  const cards = {}; let pos = offset;
  for (;;) {
    if (pos + 2880 > bytes.length) throw Error('FITS header ended early');
    const block = new TextDecoder('ascii').decode(bytes.subarray(pos, pos + 2880)); pos += 2880;
    let end = false;
    for (let i = 0; i < 2880; i += 80) {
      const card = block.slice(i, i + 80), key = card.slice(0, 8).trim();
      if (key === 'END') { end = true; break; }
      if (card[8] !== '=') continue;
      let value = card.slice(10).split('/')[0].trim();
      if (value.startsWith("'")) value = card.slice(10).match(/'((?:[^']|'')*)'/)?.[1].replace(/''/g, "'").trim() ?? '';
      else if (value === 'T' || value === 'F') value = value === 'T';
      else if (value !== '' && Number.isFinite(Number(value))) value = Number(value);
      cards[key] = value;
    }
    if (end) return {cards, dataStart: pos};
  }
}
function dataSize(cards) {
  const bitpix = Math.abs(Number(cards.BITPIX || 0)), naxis = Number(cards.NAXIS || 0);
  if (!naxis) return 0;
  let n = 1; for (let i = 1; i <= naxis; i++) n *= Number(cards['NAXIS' + i] || 0);
  n = (n + Number(cards.PCOUNT || 0)) * bitpix / 8;
  return Math.ceil(n / 2880) * 2880;
}
const TYPES = {L: [1], B: [1], I: [2], J: [4], K: [8], E: [4], D: [8], A: [1]};
export function readFitsTable(buffer, wanted) {
  const bytes = new Uint8Array(buffer), view = new DataView(buffer);
  const primary = headerCards(bytes, 0);
  const ext = headerCards(bytes, primary.dataStart + dataSize(primary.cards));
  const h = ext.cards;
  if (h.XTENSION !== 'BINTABLE') throw Error('First extension is not a binary table');
  const rows = Number(h.NAXIS2), width = Number(h.NAXIS1), fields = Number(h.TFIELDS);
  const columns = {}; let offset = 0;
  for (let i = 1; i <= fields; i++) {
    const form = String(h['TFORM' + i]).match(/^(\d*)([LXBIJKAEDCM])/);
    if (!form) throw Error('Unsupported column format ' + h['TFORM' + i]);
    const repeat = form[1] === '' ? 1 : Number(form[1]), size = (TYPES[form[2]] || [1])[0];
    columns[String(h['TTYPE' + i]).trim()] = {offset, type: form[2], repeat};
    offset += form[2] === 'X' ? Math.ceil(repeat / 8) : repeat * size;
  }
  if (offset !== width) throw Error('FITS column widths do not add up to the row width');
  const out = {header: h, rows};
  for (const name of wanted) {
    const col = columns[name];
    if (!col) { out[name] = null; continue; }
    const values = new Float64Array(rows);
    for (let r = 0; r < rows; r++) {
      const at = ext.dataStart + r * width + col.offset;
      values[r] = col.type === 'D' ? view.getFloat64(at, false) : col.type === 'E' ? view.getFloat32(at, false)
        : col.type === 'J' ? view.getInt32(at, false) : col.type === 'I' ? view.getInt16(at, false)
        : col.type === 'K' ? Number(view.getBigInt64(at, false)) : view.getUint8(at);
    }
    out[name] = values;
  }
  return out;
}

// Light-curve FITS → WorldsIndex points [BJD_TDB, flux, error, mission, stream] (quality 0 only).
export function pointsFromLightcurve(buffer, product) {
  const table = readFitsTable(buffer, ['TIME', 'PDCSAP_FLUX', 'PDCSAP_FLUX_ERR', 'SAP_FLUX', 'SAP_FLUX_ERR', 'QUALITY']);
  const h = table.header, ref = Number(h.BJDREFI || 0) + Number(h.BJDREFF || 0);
  const usePdc = table.PDCSAP_FLUX && table.PDCSAP_FLUX.some(Number.isFinite);
  const flux = usePdc ? table.PDCSAP_FLUX : table.SAP_FLUX, err = usePdc ? table.PDCSAP_FLUX_ERR : table.SAP_FLUX_ERR;
  if (!table.TIME || !flux) throw Error('The file has no TIME or flux column');
  const points = [];
  for (let i = 0; i < table.rows; i++) {
    const t = table.TIME[i], f = flux[i], e = err ? err[i] : NaN, q = table.QUALITY ? table.QUALITY[i] : 0;
    if (q !== 0 || !Number.isFinite(t) || !Number.isFinite(f)) continue;
    points.push([t + ref, f, Number.isFinite(e) ? e : 0, product.mission, product.label]);
  }
  return {points, rawRowCount: table.rows, valueColumn: usePdc ? 'PDCSAP_FLUX' : 'SAP_FLUX', timeSystem: h.TIMESYS || 'TDB', object: h.OBJECT || ''};
}

export async function loadLightcurve(product, relay) {
  if (!relay) throw Error('the live-data relay is not configured yet');
  const target = DOWNLOAD + product.uri;
  const r = await fetch(`${relay}/?url=${encodeURIComponent(target)}`, {credentials: 'omit'});
  if (!r.ok) throw Error(`MAST file unavailable (${r.status})`);
  const parsed = pointsFromLightcurve(await r.arrayBuffer(), product);
  return {
    source: `${product.mission} via MAST (live)`, sourceUrl: target, points: parsed.points,
    pointCount: parsed.points.length, rawRowCount: parsed.rawRowCount, retrievedAt: new Date().toISOString(),
    rawSha256: 'not recorded (fetched live, not stored)', valueUnit: 'e-/s', valueColumn: parsed.valueColumn,
    qualitySelection: `QUALITY == 0 rows of ${parsed.valueColumn}.`,
    uncertaintyNote: 'Fetched live from MAST in your browser; not part of the WorldsIndex release.',
    targetScope: 'host-system',
  };
}

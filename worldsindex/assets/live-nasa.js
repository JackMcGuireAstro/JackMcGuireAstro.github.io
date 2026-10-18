// Helpers for the NASA Exoplanet Archive, ExoFOP-TESS and NASA ADS: names the archives
// key on, the spectrum-file address, the IPAC table reader and readable references. The
// live panel itself (every source, "Check all sources now", the release comparison) is
// worldsindex/assets/live-sources.js. Nothing here is stored by WorldsIndex.
const ARCHIVE = 'https://exoplanetarchive.ipac.caltech.edu';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));

export function planetNames(object) {
  const name = String(object.name || '').trim();
  const joined = name.replace(/\s+([a-z])$/, '$1');
  return [...new Set([name, joined].filter(Boolean))];
}
// The archive serves spectrum files only from a short-lived workspace; the relay turns
// this request (the spectra table's spec_path) into the file itself.
export function spectrumFileUrl(path) {
  const p = String(path || '').trim();
  if (!/^\d{2}\/\d{2}\/\d{2}\/\d{2}\/[A-Za-z0-9_.+\-]+\.tbl$/.test(p)) return '';
  return `${ARCHIVE}/cgi-bin/atmospheres/nph-firefly?atmospheres&spec_path=${p}`;
}
export const spectraPage = name => `${ARCHIVE}/cgi-bin/atmospheres/nph-firefly?atmospheres&planet=${encodeURIComponent(`'${name}'`)}`;
export function exofopTarget(object) {
  const tic = /^TIC\s*(\d{1,12})$/i.exec(String(object.hostName || '').trim());
  if (tic) return {param: 'id', value: tic[1], label: `TIC ${tic[1]}`};
  const toi = /^TOI-(\d{1,6}(?:\.\d{2})?)$/i.exec(String(object.name || '').trim());
  if (toi) return {param: 'toi', value: toi[1], label: `TOI-${toi[1]}`};
  return null;
}
export function adsQuery(object) {
  const names = planetNames(object);
  return names.length ? `full:(${names.map(n => `"${n.replace(/"/g, '')}"`).join(' OR ')})` : '';
}

// IPAC table text → {columns, rows}; columns are located by the pipes in the header.
export function parseIpacTable(text) {
  const lines = String(text || '').split(/\r?\n/);
  const headers = lines.filter(l => l.startsWith('|'));
  if (!headers.length) return {columns: [], rows: []};
  const bars = [...headers[0].matchAll(/\|/g)].map(m => m.index);
  const columns = bars.slice(0, -1).map((start, i) => headers[0].slice(start + 1, bars[i + 1]).trim());
  const rows = lines.filter(l => l.trim() && !l.startsWith('|') && !l.startsWith('\\')).map(line => {
    const row = {}, tokens = line.trim().split(/\s+/);
    // numeric spectrum files have one token per column; otherwise use the pipe positions
    if (tokens.length === columns.length) columns.forEach((c, i) => { row[c] = tokens[i]; });
    else columns.forEach((c, i) => { row[c] = line.slice(bars[i] + 1, bars[i + 1] + 1).trim(); });
    return row;
  });
  return {columns, rows};
}
const VALUE_COLUMNS = ['PL_TRANDEP', 'ESPECLIPDEP', 'PL_ECLDEP', 'PL_RATRORSQ', 'PL_RATROR', 'FLAMBDA', 'FNU', 'PL_FLUXRATIO'];
export function spectrumPoints(table) {
  const upper = new Map(table.columns.map(c => [c.toUpperCase(), c]));
  const wave = [...upper.keys()].find(c => /^(CENTRALWAVELNG|WAVELENGTH|WAVELNG|WAVE)$/.test(c));
  const value = VALUE_COLUMNS.find(c => upper.has(c));
  if (!wave || !value) return {points: [], value: null};
  const err = upper.get(value + 'ERR1'), W = upper.get(wave), V = upper.get(value);
  const points = table.rows.map(r => ({x: Number(r[W]), y: Number(r[V]), e: err ? Math.abs(Number(r[err])) : NaN}))
    .filter(p => Number.isFinite(p.x) && Number.isFinite(p.y));
  return {points, value};
}
// The archive gives references as HTML (<a refstr=… href=…>Name et al. 2017</a>);
// show the name, linked only to ADS or DOI addresses.
export function referenceHtml(value) {
  const text = String(value ?? '');
  const label = text.replace(/<[^>]*>/g, '').trim();
  const href = /href=["']?([^"'\s>]+)/i.exec(text)?.[1] || '';
  return /^https:\/\/(ui\.adsabs\.harvard\.edu|doi\.org)\//.test(href)
    ? `<a href="${esc(href)}" target="_blank" rel="noopener">${esc(label)}</a>` : esc(label);
}

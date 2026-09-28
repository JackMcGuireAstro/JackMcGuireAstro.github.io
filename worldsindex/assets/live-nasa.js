// Live NASA Exoplanet Archive, ExoFOP-TESS and NASA ADS data for an object, fetched
// in the visitor's browser when asked. These archives do not let other websites read
// them directly, so requests go through the site's pass-through relay (address in
// /live-config.json); ADS needs a key that lives only inside the relay. Nothing is
// stored by WorldsIndex, and nothing here is part of the WorldsIndex release.
import {relayAddress} from './live-mast.js?v=20260927';

const TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=';
const ARCHIVE = 'https://exoplanetarchive.ipac.caltech.edu';
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const sql = s => String(s ?? '').replace(/'/g, "''");
const via = (relay, url) => `${relay}/?url=${encodeURIComponent(url)}`;

async function relayJson(relay, url) {
  const r = await fetch(via(relay, url), {credentials: 'omit'});
  if (r.status === 503) { const e = Error('needs an access key added to the relay'); e.key = true; throw e; }
  if (!r.ok) throw Error(`HTTP ${r.status}`);
  return r.json();
}
const tap = (relay, query) => relayJson(relay, `${TAP}${encodeURIComponent(query)}&format=json`);

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
function spectrumSvg(points, label) {
  if (!points.length) return '<p class="muted">No plottable points in this file.</p>';
  const w = 720, h = 260, L = 64, R = 14, T = 14, B = 40;
  const xs = points.map(p => p.x), lo = Math.min(...points.map(p => p.y - (Number.isFinite(p.e) ? p.e : 0))), hi = Math.max(...points.map(p => p.y + (Number.isFinite(p.e) ? p.e : 0)));
  const x0 = Math.min(...xs), x1 = Math.max(...xs) === x0 ? x0 + 1 : Math.max(...xs), y0 = lo, y1 = hi === lo ? lo + 1 : hi;
  const X = x => L + (x - x0) / (x1 - x0) * (w - L - R), Y = y => h - B - (y - y0) / (y1 - y0) * (h - T - B);
  const marks = points.map(p => (Number.isFinite(p.e) ? `<line x1="${X(p.x).toFixed(1)}" x2="${X(p.x).toFixed(1)}" y1="${Y(p.y - p.e).toFixed(1)}" y2="${Y(p.y + p.e).toFixed(1)}" stroke="#69cbed66"/>` : '') +
    `<circle cx="${X(p.x).toFixed(1)}" cy="${Y(p.y).toFixed(1)}" r="2.6" fill="#69cbed"><title>${p.x} µm · ${p.y}${Number.isFinite(p.e) ? ' ± ' + p.e : ''}</title></circle>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}" role="img" aria-label="${esc(label)}" style="width:100%;max-width:760px;background:#091322;border-radius:8px">` +
    `<line x1="${L}" y1="${h - B}" x2="${w - R}" y2="${h - B}" stroke="#35506a"/><line x1="${L}" y1="${T}" x2="${L}" y2="${h - B}" stroke="#35506a"/>` +
    `<text x="${L}" y="${h - 12}" fill="#b6c9d9" font-size="12">${x0.toFixed(2)} µm</text><text x="${w - R}" y="${h - 12}" fill="#b6c9d9" font-size="12" text-anchor="end">${x1.toFixed(2)} µm</text>` +
    `<text x="${L - 6}" y="${T + 10}" fill="#b6c9d9" font-size="11" text-anchor="end">${esc(y1.toPrecision(3))}</text><text x="${L - 6}" y="${h - B}" fill="#b6c9d9" font-size="11" text-anchor="end">${esc(y0.toPrecision(3))}</text>${marks}</svg>`;
}
function table(rows, columns, labels = {}) {
  if (!rows.length) return '';
  const cols = columns.filter(c => rows.some(r => r[c] !== null && r[c] !== undefined && r[c] !== ''));
  return `<div class="table-wrap"><table><thead><tr>${cols.map(c => `<th>${esc(labels[c] || c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${esc(r[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
}

export function mountLiveExtras(container, object) {
  if (!container || !object) return;
  const box = document.createElement('section');
  box.className = 'live-extras';
  box.innerHTML = `<h3>Live from the archives</h3><p class="muted">Fetch this object’s current atmosphere spectra, host-star records${object.methodCode === 'gravitational-microlensing' ? ', microlensing solutions' : ''} and papers straight from the NASA Exoplanet Archive${exofopTarget(object) ? ', ExoFOP-TESS' : ''} and NASA ADS. Nothing is stored by WorldsIndex, and these values are not part of the WorldsIndex release.</p><button type="button" class="button" data-live-extras>Fetch live archive data</button><div data-live-extras-out role="status" aria-live="polite"></div>`;
  container.append(box);
  const out = box.querySelector('[data-live-extras-out]'), button = box.querySelector('[data-live-extras]');
  let spectraRows = [];
  button.onclick = async () => {
    button.disabled = true; out.innerHTML = '<p>Contacting the archives…</p>';
    const relay = await relayAddress();
    if (!relay) { out.innerHTML = '<p>The site’s live-data relay is not configured yet.</p>'; button.disabled = false; return; }
    const name = sql(object.name), host = sql(object.hostName), fop = exofopTarget(object), q = adsQuery(object);
    const jobs = await Promise.allSettled([
      tap(relay, `select pl_name,spec_type,instrument,facility,minwavelng,maxwavelng,num_datapoints,bibcode,authors,note,spec_path from spectra where pl_name = '${name}'`),
      tap(relay, `select hostname,st_refname,st_spectype,st_teff,st_rad,st_mass,st_met,st_age,st_lum,sy_dist from stellarhosts where hostname = '${host}'`),
      object.methodCode === 'gravitational-microlensing' ? tap(relay, `select * from ml where pl_name = '${name}'`) : Promise.resolve(null),
      fop ? relayJson(relay, `https://exofop.ipac.caltech.edu/tess/target.php?${fop.param}=${fop.value}&json`) : Promise.resolve(null),
      q ? relayJson(relay, `https://api.adsabs.harvard.edu/v1/search/query?q=${encodeURIComponent(q)}&fl=bibcode,title,author,pubdate,doctype&rows=25&sort=${encodeURIComponent('date desc')}`) : Promise.resolve(null),
    ]);
    const [spec, hosts, ml, exofop, ads] = jobs;
    let html = '';
    spectraRows = spec.status === 'fulfilled' ? spec.value || [] : [];
    html += `<h4>Atmospheric spectra (NASA Exoplanet Archive)</h4>` + (spec.status === 'rejected' ? `<p>Unavailable right now (${esc(spec.reason.message)}).</p>` : spectraRows.length
      ? table(spectraRows.map((r, i) => ({...r, range: `${r.minwavelng ?? '?'}–${r.maxwavelng ?? '?'} µm`, plot: i})), ['spec_type', 'instrument', 'facility', 'range', 'num_datapoints', 'authors', 'bibcode', 'note'], {spec_type: 'Type', num_datapoints: 'Points', range: 'Wavelengths'})
        + spectraRows.map((r, i) => `<button type="button" class="text-link" data-live-spectrum="${i}">Plot ${esc(r.spec_type || 'spectrum')} · ${esc(r.instrument || '')} (${esc(r.bibcode || '')})</button>`).join(' ') + '<div data-live-spectrum-plot></div>'
      : '<p class="muted">The archive lists no atmospheric spectrum for this planet name.</p>');
    const hostRows = hosts.status === 'fulfilled' ? hosts.value || [] : [];
    html += `<h4>Host star records (NASA Exoplanet Archive)</h4>` + (hosts.status === 'rejected' ? `<p>Unavailable right now (${esc(hosts.reason.message)}).</p>` : hostRows.length
      ? table(hostRows, ['st_refname', 'st_spectype', 'st_teff', 'st_rad', 'st_mass', 'st_met', 'st_age', 'st_lum', 'sy_dist'], {st_refname: 'Reference', st_spectype: 'Spectral type', st_teff: 'Teff (K)', st_rad: 'Radius (R☉)', st_mass: 'Mass (M☉)', st_met: '[Fe/H]', st_age: 'Age (Gyr)', st_lum: 'log L (L☉)', sy_dist: 'Distance (pc)'}) + '<p class="fineprint">One row per published solution; values are not combined.</p>'
      : '<p class="muted">No host-star records under this host name.</p>');
    if (ml.status === 'fulfilled' && ml.value) {
      const rows = ml.value;
      html += `<h4>Microlensing solutions (NASA Exoplanet Archive)</h4>` + (rows.length ? table(rows.slice(0, 12), Object.keys(rows[0]).filter(k => !/^(rowupdate|pl_pubdate|releasedate|htmlparam)/.test(k)).slice(0, 18)) : '<p class="muted">No microlensing rows for this name.</p>');
    }
    if (fop) {
      html += `<h4>ExoFOP-TESS (${esc(fop.label)})</h4>`;
      if (exofop.status === 'rejected') html += `<p>Unavailable right now (${esc(exofop.reason.message)}).</p>`;
      else if (exofop.value) {
        const v = exofop.value, basic = v.basic_info || {};
        const counts = Object.entries(v).filter(([, val]) => Array.isArray(val)).map(([key, val]) => `${key.replace(/_/g, ' ')}: ${val.length}`);
        html += `<p>${esc(basic.star_names || '')}</p>${counts.length ? `<p class="muted">${esc(counts.join(' · '))}</p>` : ''}<p><a href="https://exofop.ipac.caltech.edu/tess/target.php?${fop.param}=${esc(fop.value)}" target="_blank" rel="noopener">Open the full ExoFOP page ↗</a></p>`;
      }
    }
    html += '<h4>Papers (NASA ADS)</h4>';
    if (ads.status === 'rejected') html += `<p>${ads.reason.key ? 'Needs a free NASA ADS key added to the relay.' : `Unavailable right now (${esc(ads.reason.message)}).`}</p>`;
    else {
      const docs = ads.value?.response?.docs || [];
      html += docs.length ? `<ol class="live-papers">${docs.map(d => `<li><a href="https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(d.bibcode)}/abstract" target="_blank" rel="noopener">${esc((d.title || [d.bibcode])[0])}</a> <small class="muted">${esc([(d.author || []).slice(0, 3).join('; ') + ((d.author || []).length > 3 ? ' et al.' : ''), d.pubdate, d.doctype].filter(Boolean).join(' · '))}</small></li>`).join('')}</ol><p class="fineprint">ADS full-text search for ${esc(q)}; a match means the name appears in the paper.</p>` : '<p class="muted">No papers found in ADS for this name.</p>';
    }
    html += `<p class="fineprint">Fetched ${esc(new Date().toISOString())} through the site’s relay; values are shown as the archives return them.</p>`;
    out.innerHTML = html; button.disabled = false; button.textContent = 'Fetch again';
  };
  out.addEventListener('click', async event => {
    const trigger = event.target.closest('[data-live-spectrum]');
    if (!trigger) return;
    const row = spectraRows[Number(trigger.dataset.liveSpectrum)], plot = out.querySelector('[data-live-spectrum-plot]');
    const url = spectrumFileUrl(row?.spec_path);
    if (!url) { plot.innerHTML = '<p>No data file is listed for this spectrum.</p>'; return; }
    plot.innerHTML = '<p>Downloading the spectrum…</p>';
    try {
      const relay = await relayAddress();
      const r = await fetch(via(relay, url), {credentials: 'omit'});
      if (!r.ok) throw Error(`HTTP ${r.status}`);
      const {points, value} = spectrumPoints(parseIpacTable(await r.text()));
      plot.innerHTML = `<p><strong>${esc(row.spec_type || 'Spectrum')}</strong> · ${esc(row.instrument || '')} · ${esc(row.authors || row.bibcode || '')} · ${points.length} points${value ? ` of ${esc(value)}` : ''} · <a href="${esc(spectraPage(object.name))}" target="_blank" rel="noopener">archive spectra page ↗</a></p>${spectrumSvg(points, `${row.spec_type || 'Spectrum'} of ${object.name}`)}`;
    } catch (error) { plot.innerHTML = `<p>Could not load the spectrum: ${esc(error.message)}.</p>`; }
  });
}

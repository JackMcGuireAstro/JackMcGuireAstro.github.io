// "Live from the archives" for one object: every public source this page can reach, run
// on request in the visitor's browser, each answer compared with what this release holds.
//
// Sources that let web pages read them (SIMBAD TAP) are fetched directly; the rest go
// through the site's pass-through relay (address in /live-config.json): the NASA Exoplanet
// Archive tables, ExoFOP-TESS, VizieR (Gaia DR3, SWEET-Cat) and NASA ADS (key inside the
// relay). Nothing is stored by WorldsIndex; nothing here is part of the release.
//
// "Check all sources now" runs the sources a few at a time (pool); a second press passes
// fresh = true, which only bypasses this browser's HTTP cache (cache: "reload"). The relay
// address is unchanged, so its one-hour edge cache still stands between visitors and the
// providers. The last summary per object is kept in this browser's localStorage so a later
// visit can say what changed since the reader last looked (per-viewer convenience only).
import {relayAddress, hostIdentifiers} from './live-mast.js?v=20261017';
import {exofopTarget, adsQuery, planetNames, spectrumFileUrl, spectraPage, parseIpacTable, spectrumPoints, referenceHtml} from './live-nasa.js?v=20261017';

const TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=';
const VIZIER = 'https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync';
const SIMBAD = 'https://simbad.cds.unistra.fr/simbad/sim-tap/sync';
const CONCURRENCY = 4;
const TIMEOUT_MS = 25000;
export const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
const sql = s => String(s ?? '').replace(/'/g, "''");
const via = (relay, url) => `${relay}/?url=${encodeURIComponent(url)}`;
const tapUrl = (base, query, format = 'csv') => `${base}?REQUEST=doQuery&LANG=ADQL&FORMAT=${format}&QUERY=${encodeURIComponent(query)}`;

// ------------------------------------------------------------------ fetching
class NeedsRelay extends Error { constructor() { super('Needs the live-data relay'); this.noRelay = true; } }
class NeedsKey extends Error { constructor(name) { super(`Needs a free ${name} key added to the relay`); this.needsKey = name; } }
function timed(url, options = {}, fetcher = fetch) {
  const controller = typeof AbortController === 'function' ? new AbortController() : null;
  const timer = controller ? setTimeout(() => controller.abort(), TIMEOUT_MS) : null;
  const init = {credentials: 'omit', referrerPolicy: 'strict-origin', ...options};
  if (init.fresh) init.cache = 'reload';
  delete init.fresh;
  if (controller) init.signal = controller.signal;
  return fetcher(url, init).finally(() => { if (timer) clearTimeout(timer); });
}
export function makeContext(relay, fresh = false, fetcher = fetch) {
  const base = fresh ? {fresh: true} : {};
  const text = async (url, options = {}) => {
    const r = await timed(url, {...base, ...options}, fetcher);
    if (r.status === 503) throw new NeedsKey(/adsabs/.test(url) ? 'NASA ADS' : 'provider');
    if (r.status === 404) return '';
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    return r.text();
  };
  const json = async (url, options = {}) => {
    const body = await text(url, options);
    return body ? JSON.parse(body) : null;
  };
  return {relay, fresh: Boolean(fresh),
    json, text,
    relayJson: (url) => { if (!relay) throw new NeedsRelay(); return json(via(relay, url)); },
    relayText: (url) => { if (!relay) throw new NeedsRelay(); return text(via(relay, url)); },
    tap: (query) => { if (!relay) throw new NeedsRelay(); return json(via(relay, `${TAP}${encodeURIComponent(query)}&format=json`)); }};
}

// ------------------------------------------------------------------ readers
function splitCsvLine(line) {
  const out = []; let cur = '', quoted = false;
  for (let i = 0; i < line.length; i += 1) {
    const c = line[i];
    if (quoted) { if (c === '"' && line[i + 1] === '"') { cur += '"'; i += 1; } else if (c === '"') quoted = false; else cur += c; }
    else if (c === '"') quoted = true;
    else if (c === ',') { out.push(cur); cur = ''; }
    else cur += c;
  }
  out.push(cur);
  return out;
}
export function parseCsv(text) {
  const lines = String(text || '').replace(/^﻿/, '').split(/\r?\n/).filter(l => l.trim() && !/^#(?![A-Za-z])/.test(l));
  if (!lines.length) return {columns: [], rows: []};
  if (/^\s*</.test(lines[0])) return {columns: [], rows: [], markup: true};
  const columns = splitCsvLine(lines[0].replace(/^#/, '')).map(c => c.trim());
  return {columns, rows: lines.slice(1).map(line => { const cells = splitCsvLine(line), row = {}; columns.forEach((c, i) => { row[c] = (cells[i] ?? '').trim(); }); return row; })};
}
export function tapRows(json) {
  if (Array.isArray(json)) return json;
  if (json && Array.isArray(json.data) && Array.isArray(json.metadata)) {
    const names = json.metadata.map(m => m.name);
    return json.data.map(values => Object.fromEntries(names.map((n, i) => [n, values[i]])));
  }
  return [];
}
const latestOf = (rows, keys) => rows.reduce((best, r) => { for (const k of keys) { const v = r?.[k]; if (v && String(v) > String(best || '')) best = String(v); } return best; }, null);

// ------------------------------------------------------------------ identifiers
// Everything the object states that a source can be keyed on: names, host, TIC/KIC/EPIC,
// TOI and KOI designations, a Gaia DR3 source id (from NASA-stated aliases when present).
export function identifiers(object, records = [], aliases = []) {
  const ids = hostIdentifiers(object, records);
  const names = planetNames(object), host = String(object.hostName || '').trim();
  const toi = new Set(), koi = new Set(), gaia = new Set();
  const consider = (value) => {
    const v = String(value ?? '').trim();
    let m;
    if ((m = /^TOI-?(\d{1,6}(?:\.\d{2})?)$/i.exec(v))) toi.add(m[1]);
    if ((m = /^(?:KOI-?|K0*)(\d{1,6}\.\d{2})$/i.exec(v))) koi.add(m[1]);
    if ((m = /^Gaia\s*DR3\s*(\d{10,20})$/i.exec(v))) gaia.add(m[1]);
  };
  [object.name, object.hostName, ...(object.aliases || [])].forEach(consider);
  for (const record of records) {
    const v = record?.values || {};
    [v.toi, v.kepoi_name, v.gaia_dr3_id, v.gaia_id].forEach(consider);
    if (record.sourceId === 'nasa-toi' && v.toi) toi.add(String(v.toi));
  }
  for (const alias of aliases) consider(alias?.value);
  return {names, host, tic: ids.tic, kic: ids.kic, epic: ids.epic, toi: [...toi], koi: [...koi], gaia: [...gaia],
    position: Number.isFinite(object.raDeg) && Number.isFinite(object.decDeg) ? {ra: object.raDeg, dec: object.decDeg} : null};
}

// ------------------------------------------------------------------ release baseline
// What this release holds per source for the object: record counts and the latest
// updated/published date, from the detail shard's records.
export function releaseBaseline(object, records = [], releasedAt = null) {
  const bySource = {};
  for (const record of records) {
    const cur = bySource[record.sourceId] || {count: 0, latest: null};
    cur.count += 1;
    const stamp = record.updated || record.published || record.values?.rowupdate || record.values?.updated || null;
    if (stamp && (!cur.latest || String(stamp) > cur.latest)) cur.latest = String(stamp).slice(0, 10);
    bySource[record.sourceId] = cur;
  }
  return {bySource, sourceIds: object.sourceIds || [], sourceRecordCount: Number(object.sourceRecordCount || 0), releasedAt: releasedAt ? String(releasedAt).slice(0, 10) : null};
}
export function compareWithRelease(source, result, base) {
  if (!result || (result.status !== 'ok' && result.status !== 'none')) return {state: 'unknown', text: ''};
  const count = Number(result.count || 0);
  const retainedBy = (source.family || []).map(id => base.bySource[id]).filter(Boolean);
  if (!retainedBy.length) {
    if (!(source.family || []).some(id => base.sourceIds.includes(id))) return count > 0 ? {state: 'live-only', text: `${count.toLocaleString()} upstream; this source is live-only, not in the release`} : {state: 'none', text: 'live-only source, nothing here'};
    return count > 0 ? {state: 'new', text: `${count.toLocaleString()} upstream, no rows of this source retained for this object`} : {state: 'none', text: 'none here, none retained'};
  }
  const retained = retainedBy.reduce((a, b) => a + b.count, 0), latestRetained = retainedBy.map(b => b.latest).filter(Boolean).sort().pop() || null;
  const parts = [];
  const live = result.latest ? String(result.latest).slice(0, 10) : null;
  if (live && latestRetained && live > latestRetained) parts.push(`updated upstream ${live}, release holds up to ${latestRetained}`);
  else if (live && !latestRetained && base.releasedAt && live > base.releasedAt) parts.push(`updated upstream ${live}, after this release was built (${base.releasedAt})`);
  if (count > retained) parts.push(`${count.toLocaleString()} upstream vs ${retained.toLocaleString()} retained (+${(count - retained).toLocaleString()})`);
  if (parts.length) return {state: 'new', text: parts.join('; ')};
  if (count === retained) return {state: 'same', text: count ? `matches the release (${count.toLocaleString()} retained)` : 'none here, none retained'};
  return {state: 'fewer', text: `release holds more (${retained.toLocaleString()} retained, ${count.toLocaleString()} answered now)`};
}

// ------------------------------------------------------------------ the sources
// Each: key, label, kind, family (release source ids compared with), unit [one, many],
// applies(ids) → true | reason, run(ids, ctx, object) → {status, count, latest, rows, how, link, note}.
export const SOURCES = [];
const source = (spec) => { SOURCES.push(spec); return spec; };
const okOrNone = (r) => { r.status = r.count > 0 ? 'ok' : 'none'; return r; };
const needs = (what) => (ids) => ids[what] && (Array.isArray(ids[what]) ? ids[what].length : true) ? true : `No ${what === 'position' ? 'coordinates' : what.toUpperCase() + ' identifier'} in the record`;
const archiveLink = (table, column, value) => `https://exoplanetarchive.ipac.caltech.edu/cgi-bin/TblView/nph-tblView?app=ExoTbls&config=${table}&constraint=${encodeURIComponent(`${column}='${value}'`)}`;

source({key: 'nasa-ps', label: 'NASA Planetary Systems', kind: 'catalog', family: ['nasa-ps'], unit: ['published row', 'published rows'], relay: true,
  applies: (ids) => ids.names.length ? true : 'No planet name',
  run: async (ids, ctx) => {
    const rows = tapRows(await ctx.tap(`select pl_name,pl_refname,default_flag,rowupdate,pl_pubdate from ps where pl_name = '${sql(ids.names[0])}'`)) || [];
    return okOrNone({how: `pl_name = ${ids.names[0]}`, count: rows.length, rows, latest: latestOf(rows, ['rowupdate', 'pl_pubdate']), link: archiveLink('PS', 'pl_name', ids.names[0])});
  }});
source({key: 'nasa-pscomppars', label: 'NASA PSCompPars composite', kind: 'catalog', family: ['nasa-pscomppars'], unit: ['composite row', 'composite rows'], relay: true,
  applies: (ids) => ids.names.length ? true : 'No planet name',
  run: async (ids, ctx) => {
    const rows = tapRows(await ctx.tap(`select pl_name,hostname,tic_id,gaia_dr3_id,sy_pnum,disc_refname,rowupdate from pscomppars where pl_name = '${sql(ids.names[0])}'`)) || [];
    return okOrNone({how: `pl_name = ${ids.names[0]}`, count: rows.length, rows, latest: latestOf(rows, ['rowupdate']), link: archiveLink('PSCompPars', 'pl_name', ids.names[0])});
  }});
source({key: 'nasa-toi', label: 'NASA TESS Objects of Interest', kind: 'catalog', family: ['nasa-toi'], unit: ['TOI row', 'TOI rows'], relay: true,
  applies: (ids) => ids.toi.length || ids.tic.length ? true : 'No TOI or TIC identifier in the record',
  run: async (ids, ctx) => {
    const where = ids.tic.length ? `tid = ${Number(ids.tic[0])}` : `toi = ${Number(ids.toi[0])}`;
    const rows = tapRows(await ctx.tap(`select toi,tid,tfopwg_disp,rowupdate,toi_created from toi where ${where}`)) || [];
    return okOrNone({how: where.replace('tid', 'TIC'), count: rows.length, rows, latest: latestOf(rows, ['rowupdate']), link: archiveLink('TOI', ids.tic.length ? 'tid' : 'toi', ids.tic[0] || ids.toi[0])});
  }});
source({key: 'nasa-koi', label: 'NASA Kepler Objects of Interest (cumulative)', kind: 'catalog', family: ['nasa-koi'], unit: ['KOI row', 'KOI rows'], relay: true,
  applies: (ids) => ids.koi.length || ids.kic.length ? true : 'No KOI or KIC identifier in the record',
  run: async (ids, ctx) => {
    const where = ids.kic.length ? `kepid = ${Number(ids.kic[0])}` : `kepoi_name = 'K${String(ids.koi[0]).padStart(8, '0')}'`;
    const rows = tapRows(await ctx.tap(`select kepoi_name,kepler_name,koi_disposition,koi_pdisposition,koi_score from cumulative where ${where}`)) || [];
    return okOrNone({how: where.replace('kepid', 'KIC'), count: rows.length, rows, link: archiveLink('cumulative', ids.kic.length ? 'kepid' : 'kepoi_name', ids.kic[0] || `K${String(ids.koi[0]).padStart(8, '0')}`)});
  }});
source({key: 'nasa-k2', label: 'NASA K2 planets and candidates', kind: 'catalog', family: ['nasa-k2'], unit: ['K2 row', 'K2 rows'], relay: true,
  applies: (ids) => ids.epic.length || /^(K2-|EPIC )/i.test(ids.names[0] || '') ? true : 'No EPIC or K2 identifier in the record',
  run: async (ids, ctx) => {
    const where = ids.epic.length ? `epic_hostname = 'EPIC ${Number(ids.epic[0])}'` : `pl_name = '${sql(ids.names[0])}'`;
    const rows = tapRows(await ctx.tap(`select pl_name,epic_hostname,disposition,rowupdate,pl_pubdate from k2pandc where ${where}`)) || [];
    return okOrNone({how: where, count: rows.length, rows, latest: latestOf(rows, ['rowupdate', 'pl_pubdate']), link: 'https://exoplanetarchive.ipac.caltech.edu/cgi-bin/TblView/nph-tblView?app=ExoTbls&config=k2pandc'});
  }});
source({key: 'nasa-stellarhosts', label: 'NASA stellar hosts', kind: 'context', family: ['nasa-stellar-hosts'], unit: ['host-star row', 'host-star rows'], relay: true,
  applies: (ids) => ids.host ? true : 'No host name',
  run: async (ids, ctx) => {
    const rows = tapRows(await ctx.tap(`select hostname,st_refname,st_spectype,st_teff,st_rad,st_mass,st_met,st_age,st_lum,sy_dist from stellarhosts where hostname = '${sql(ids.host)}'`)) || [];
    return okOrNone({how: `hostname = ${ids.host}`, count: rows.length, rows, link: archiveLink('stellarhosts', 'hostname', ids.host)});
  }});
source({key: 'nasa-spectra', label: 'NASA atmospheric spectra', kind: 'context', family: ['nasa-atmospheric-spectroscopy'], unit: ['spectrum', 'spectra'], relay: true,
  applies: (ids) => ids.names.length ? true : 'No planet name',
  run: async (ids, ctx) => {
    const rows = tapRows(await ctx.tap(`select pl_name,spec_type,instrument,facility,minwavelng,maxwavelng,num_datapoints,bibcode,authors,note,spec_path from spectra where pl_name = '${sql(ids.names[0])}'`)) || [];
    return okOrNone({how: `pl_name = ${ids.names[0]}`, count: rows.length, rows, link: spectraPage(ids.names[0])});
  }});
source({key: 'nasa-ml', label: 'NASA microlensing solutions', kind: 'context', family: ['nasa-microlensing'], unit: ['solution', 'solutions'], relay: true,
  applies: (ids, object) => object.methodCode === 'gravitational-microlensing' ? true : 'Not a microlensing object',
  run: async (ids, ctx) => {
    const rows = tapRows(await ctx.tap(`select * from ml where pl_name = '${sql(ids.names[0])}'`)) || [];
    return okOrNone({how: `pl_name = ${ids.names[0]}`, count: rows.length, rows, link: 'https://exoplanetarchive.ipac.caltech.edu/cgi-bin/TblView/nph-tblView?app=ExoTbls&config=ML'});
  }});
source({key: 'exofop', label: 'ExoFOP-TESS', kind: 'context', family: ['exofop'], unit: ['table with entries', 'tables with entries'], relay: true,
  applies: (ids, object) => exofopTarget(object) || ids.tic.length ? true : 'No TIC or TOI identifier',
  run: async (ids, ctx, object) => {
    const fop = exofopTarget(object) || {param: 'id', value: ids.tic[0], label: `TIC ${ids.tic[0]}`};
    const v = await ctx.relayJson(`https://exofop.ipac.caltech.edu/tess/target.php?${fop.param}=${fop.value}&json`);
    const tables = Object.entries(v || {}).filter(([, val]) => Array.isArray(val) && val.length).map(([key, val]) => `${key.replace(/_/g, ' ')}: ${val.length}`);
    return okOrNone({how: `${fop.label}${tables.length ? ' · ' + tables.join(', ') : ''}`, count: tables.length, items: tables, star: v?.basic_info?.star_names || '', link: `https://exofop.ipac.caltech.edu/tess/target.php?${fop.param}=${fop.value}`});
  }});
source({key: 'gaia-dr3', label: 'Gaia DR3 (VizieR)', kind: 'context', family: ['gaia'], unit: ['source', 'sources'], relay: true,
  applies: (ids) => ids.gaia.length || ids.position || ids.names.length ? true : 'No Gaia id or coordinates',
  run: async (ids, ctx) => {
    let gaiaId = ids.gaia[0] || null, how = '';
    if (!gaiaId && ids.names.length) {
      const rows = tapRows(await ctx.tap(`select gaia_dr3_id from pscomppars where pl_name = '${sql(ids.names[0])}'`).catch(() => null)) || [];
      const m = /(\d{10,20})/.exec(String(rows[0]?.gaia_dr3_id || ''));
      if (m) { gaiaId = m[1]; how = 'source id from PSCompPars'; }
    } else if (gaiaId) how = 'source id stated by the record';
    const query = gaiaId
      ? `SELECT TOP 1 Source, RA_ICRS, DE_ICRS, Gmag, Plx, PM FROM "I/355/gaiadr3" WHERE Source=${gaiaId}`
      : `SELECT TOP 5 Source, RA_ICRS, DE_ICRS, Gmag, Plx, PM FROM "I/355/gaiadr3" WHERE 1=CONTAINS(POINT('ICRS', RA_ICRS, DE_ICRS), CIRCLE('ICRS', ${ids.position.ra}, ${ids.position.dec}, ${(5 / 3600).toFixed(6)}))`;
    if (!gaiaId) how = 'within 5″ of the catalog position (not an identification)';
    const table = parseCsv(await ctx.relayText(tapUrl(VIZIER, query)));
    if (table.markup) throw new Error('VizieR answered in a format this page cannot read');
    const rows = table.rows.map(r => ({name: `Gaia DR3 ${r.Source}`, g: r.Gmag, plx: r.Plx, pm: r.PM}));
    return okOrNone({how, count: rows.length, rows, link: gaiaId ? `https://vizier.cds.unistra.fr/viz-bin/VizieR-5?-source=I/355/gaiadr3&Source=${gaiaId}` : 'https://vizier.cds.unistra.fr/viz-bin/VizieR-3?-source=I/355/gaiadr3'});
  }});
source({key: 'simbad', label: 'SIMBAD', kind: 'context', family: ['simbad'], unit: ['object', 'objects'],
  applies: (ids) => ids.host || ids.names.length || ids.position ? true : 'No name or coordinates',
  run: async (ids, ctx) => {
    const byName = async (name) => tapRows(await ctx.json(tapUrl(SIMBAD, `SELECT TOP 5 main_id, otype, ra, dec FROM basic JOIN ident ON ident.oidref = basic.oid WHERE ident.id = '${sql(name)}'`, 'json')));
    for (const name of [ids.names[0], ids.host].filter(Boolean)) {
      const rows = await byName(name);
      if (rows.length) return okOrNone({how: `by name ${name}`, count: rows.length, rows: rows.map(r => ({name: r.main_id, type: r.otype})), link: `https://simbad.cds.unistra.fr/simbad/sim-id?Ident=${encodeURIComponent(name)}`});
    }
    if (!ids.position) return {status: 'none', count: 0, how: 'by name', note: 'SIMBAD knows neither the planet nor the host name'};
    const r = 5, rows = tapRows(await ctx.json(tapUrl(SIMBAD, `SELECT TOP 5 main_id, otype, ra, dec FROM basic WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', ${ids.position.ra}, ${ids.position.dec}, ${(r / 3600).toFixed(6)}))=1`, 'json')));
    return okOrNone({how: `names unknown to SIMBAD; within ${r}″ of the catalog position`, count: rows.length, rows: rows.map(x => ({name: x.main_id, type: x.otype})), link: `https://simbad.cds.unistra.fr/simbad/sim-coo?Coord=${encodeURIComponent(ids.position.ra + ' ' + ids.position.dec)}&Radius=${r}&Radius.unit=arcsec`});
  }});
source({key: 'sweetcat', label: 'SWEET-Cat 2.0 (VizieR)', kind: 'context', family: ['vizier', 'vizier-contract-catalogs'], unit: ['host row', 'host rows'], relay: true,
  applies: (ids) => ids.host ? true : 'No host name',
  run: async (ids, ctx) => {
    const table = parseCsv(await ctx.relayText(tapUrl(VIZIER, `SELECT TOP 5 * FROM "J/A+A/656/A53/sweetcat" WHERE Name='${sql(ids.host)}'`)));
    if (table.markup) throw new Error('VizieR answered in a format this page cannot read');
    return okOrNone({how: `Name = ${ids.host}`, count: table.rows.length, rows: table.rows, link: `https://vizier.cds.unistra.fr/viz-bin/VizieR-4?-source=J/A%2bA/656/A53/sweetcat&Name=${encodeURIComponent(ids.host)}`});
  }});
source({key: 'ads', label: 'Papers (NASA ADS)', kind: 'papers', family: ['ads'], unit: ['paper', 'papers'], relay: true,
  applies: (ids, object) => adsQuery(object) ? true : 'No searchable name',
  run: async (ids, ctx, object) => {
    const q = adsQuery(object);
    const json = await ctx.relayJson(`https://api.adsabs.harvard.edu/v1/search/query?q=${encodeURIComponent(q)}&fl=bibcode,title,author,pubdate,doctype&rows=25&sort=${encodeURIComponent('date desc')}`);
    const docs = json?.response?.docs || [], total = Number(json?.response?.numFound);
    return okOrNone({how: `full-text search for ${q}`, count: Number.isFinite(total) ? total : docs.length, rows: docs, latest: latestOf(docs, ['pubdate']), query: q});
  }});
source({key: 'exoplanet-eu', label: 'Exoplanet.eu', kind: 'link', family: ['exoplanet-eu'],
  applies: (ids) => ids.names.length ? true : 'No planet name',
  link: (ids) => `https://exoplanet.eu/catalog/?f=${encodeURIComponent(`name like "${ids.names[0]}"`)}`,
  note: 'The encyclopaedia’s per-object API is a form post behind a CSRF token, so the page links to its catalogue filtered on this name.'});
source({key: 'mast', label: 'MAST light curves', kind: 'link', family: ['mast', 'mast-products'],
  applies: (ids) => ids.tic.length || ids.kic.length || ids.epic.length || ids.position ? true : 'No identifier or coordinates',
  link: () => '?',
  note: 'TESS, Kepler and K2 light-curve files are found and loaded live in the Light curves tab (MAST search direct, files through the relay).'});

// ------------------------------------------------------------------ running
export function problemResult(error) {
  if (error?.noRelay) return {status: 'no-relay', count: 0, note: 'Needs the live-data relay'};
  if (error?.needsKey) return {status: 'needs-key', count: 0, note: `Needs a free ${error.needsKey} key added to the relay`};
  const reason = error?.name === 'AbortError' ? 'timed out' : error?.name === 'SyntaxError' ? 'the archive’s answer could not be read'
    : error?.name === 'TypeError' && /fetch|network/i.test(error.message || '') ? 'the archive could not be reached' : error?.message || String(error);
  return {status: 'unavailable', count: 0, note: `Unavailable right now (${reason})`};
}
export async function runSource(spec, ids, ctx, object) {
  const started = Date.now();
  const finish = (r) => ({...r, key: spec.key, source: spec.label, kind: spec.kind, unit: spec.unit || ['row', 'rows'], fetchedAt: new Date().toISOString(), ms: Date.now() - started});
  const applies = spec.applies ? spec.applies(ids, object) : true;
  if (applies !== true) return finish({status: 'not-applicable', count: 0, note: applies});
  if (spec.kind === 'link') return finish({status: 'link', count: 0, link: spec.link(ids, object), note: spec.note});
  if (spec.relay && !ctx.relay) return finish(problemResult(new NeedsRelay()));
  try { return finish((await spec.run(ids, ctx, object)) || {status: 'none', count: 0}); }
  catch (error) { return finish(problemResult(error)); }
}
export async function pool(jobs, limit, onEach) {
  let next = 0; const results = new Array(jobs.length);
  const worker = async () => {
    while (next < jobs.length) {
      const index = next; next += 1;
      try { results[index] = await jobs[index](); } catch (error) { results[index] = problemResult(error); }
      if (onEach) onEach(index, results[index]);
    }
  };
  await Promise.all(Array.from({length: Math.min(limit, jobs.length)}, worker));
  return results;
}
// Every source for one object. options: fresh, onRow(result, index), limit, relay (else
// read from live-config.json), fetcher (tests), aliases (NASA-stated names from the shard).
export async function load(object, records = [], options = {}) {
  const relay = options.relay !== undefined ? options.relay : await relayAddress();
  const ctx = makeContext(relay, options.fresh, options.fetcher || fetch);
  const ids = identifiers(object, records, options.aliases || []), base = releaseBaseline(object, records, options.releasedAt);
  const results = await pool(SOURCES.map(spec => () => runSource(spec, ids, ctx, object)), options.limit || CONCURRENCY, (index, result) => {
    result.comparison = compareWithRelease(SOURCES[index], result, base);
    if (options.onRow) options.onRow(result, index);
  });
  return {fetchedAt: new Date().toISOString(), relay, fresh: Boolean(options.fresh), results, ids, baseline: base, object};
}

// ------------------------------------------------------------------ memory (this browser only)
const SEEN_PREFIX = 'worldsindex-live-seen:';
function storage() { try { return globalThis.localStorage || null; } catch { return null; } }
export function readSeen(objectId) { try { const raw = storage()?.getItem(SEEN_PREFIX + objectId); return raw ? JSON.parse(raw) : null; } catch { return null; } }
export function summarize(result) {
  return {at: result.fetchedAt, rows: Object.fromEntries(result.results.map(r => [r.key, {status: r.status, count: Number(r.count || 0), latest: r.latest || null}]))};
}
export function writeSeen(objectId, result) { try { storage()?.setItem(SEEN_PREFIX + objectId, JSON.stringify(summarize(result))); } catch { /* no memory in this browser */ } }
export function changeSinceSeen(result, seen) {
  const was = seen?.rows?.[result.key];
  if (!was) return '';
  const when = String(seen.at || '').slice(0, 10);
  const settled = (s) => s === 'ok' || s === 'none';
  if (was.status !== result.status && settled(was.status) && settled(result.status)) return `changed since you last looked (${when}): ${result.status === 'ok' ? `now ${Number(result.count).toLocaleString()}` : 'now none'}`;
  if (result.status !== 'ok') return '';
  if (Number(result.count) !== was.count) return `changed since you last looked (${when}): was ${was.count.toLocaleString()}, now ${Number(result.count).toLocaleString()}`;
  if (result.latest && was.latest && String(result.latest) > String(was.latest)) return `updated since you last looked (${when}): now ${String(result.latest).slice(0, 10)}`;
  return '';
}

// ------------------------------------------------------------------ rendering
const clock = iso => iso ? iso.slice(11, 19) + ' UTC' : '';
const plural = (unit, n) => Array.isArray(unit) ? (n === 1 ? unit[0] : unit[1]) : unit;
function releaseText(spec, base) {
  const fam = (spec.family || []).map(id => base.bySource[id]).filter(Boolean);
  if (fam.length) { const n = fam.reduce((a, b) => a + b.count, 0), latest = fam.map(b => b.latest).filter(Boolean).sort().pop(); return `release holds ${n.toLocaleString()}${latest ? ` (latest ${latest})` : ''}`; }
  if ((spec.family || []).some(id => base.sourceIds.includes(id))) return 'release lists the source, no rows packaged for this object';
  return 'live-only: not in the release';
}
export function summaryText(result) {
  const n = Number(result.count || 0);
  switch (result.status) {
    case 'ok': return `${n.toLocaleString()} ${plural(result.unit, n)}`;
    case 'none': return result.note || 'none found';
    case 'link': return `Link only — ${result.note}`;
    case 'proprietary': return `Proprietary at the provider: ${result.note}`;
    default: return result.note || result.status;
  }
}
export function rowHtml(spec, result, base, seen) {
  let body;
  if (!result) body = `<span class="live-state">not checked yet</span> <small class="live-meta">${esc(releaseText(spec, base))}</small>`;
  else if (result.status === 'pending') body = '<span class="live-state">checking…</span>';
  else {
    const cmp = result.comparison || {state: 'unknown', text: ''}, changed = changeSinceSeen(result, seen);
    const link = result.link === '?' ? '<button type="button" class="text-link" data-live-goto="lightcurves">open the Light curves tab</button>' : result.link ? `<a href="${esc(result.link)}" target="_blank" rel="noopener">open at source ↗</a>` : '';
    body = `<span class="live-state">${esc(summaryText(result))}</span>${result.how ? ` <small>(${esc(result.how)})</small>` : ''} ${link}` +
      (cmp.state === 'new' ? ` <mark class="live-new">New since the release: ${esc(cmp.text)}</mark>` : cmp.text ? ` <small class="live-same">${esc(cmp.text)}</small>` : '') +
      (changed ? ` <mark class="live-changed">${esc(changed)}</mark>` : '') +
      (result.rows?.length && (result.key === 'simbad' || result.key === 'gaia-dr3') ? `<br><small>${esc(result.rows.slice(0, 3).map(r => [r.name, r.type || (r.g ? `G = ${r.g}` : '')].filter(Boolean).join(' · ')).join('; '))}</small>` : '') +
      (result.status !== 'link' ? `<br><small class="live-meta">fetched <time datetime="${esc(result.fetchedAt)}">${esc(clock(result.fetchedAt))}</time> · ${esc(releaseText(spec, base))}</small>` : '');
  }
  return `<strong>${esc(spec.label)}</strong> — ${body}`;
}
function table(rows, columns, labels = {}, render = {}) {
  if (!rows.length) return '';
  const cols = columns.filter(c => rows.some(r => r[c] !== null && r[c] !== undefined && r[c] !== ''));
  return `<div class="table-wrap"><table><thead><tr>${cols.map(c => `<th>${esc(labels[c] || c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${cols.map(c => `<td>${render[c] ? render[c](r[c]) : esc(r[c] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`;
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
// The detail tables under the rows, drawn once everything has answered.
export function extrasHtml(result) {
  const by = Object.fromEntries(result.results.map(r => [r.key, r])), object = result.object;
  let html = '';
  const spec = by['nasa-spectra'];
  if (spec?.status === 'ok') {
    const rows = spec.rows;
    html += '<h4>Atmospheric spectra (NASA Exoplanet Archive)</h4>' + table(rows.map((r, i) => ({...r, range: `${r.minwavelng ?? '?'}–${r.maxwavelng ?? '?'} µm`, plot: i})), ['spec_type', 'instrument', 'facility', 'range', 'num_datapoints', 'authors', 'bibcode', 'note'], {spec_type: 'Type', instrument: 'Instrument', facility: 'Facility', num_datapoints: 'Points', range: 'Wavelengths', authors: 'Authors', bibcode: 'Bibcode', note: 'Note'}, {bibcode: b => b ? `<a href="https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(b)}/abstract" target="_blank" rel="noopener">${esc(b)}</a>` : ''}) +
      rows.map((r, i) => `<button type="button" class="text-link" data-live-spectrum="${i}">Plot ${esc(r.spec_type || 'spectrum')} · ${esc(r.instrument || '')} (${esc(r.bibcode || '')})</button>`).join(' ') + '<div data-live-spectrum-plot></div>';
  }
  const hosts = by['nasa-stellarhosts'];
  if (hosts?.status === 'ok') html += '<h4>Host star records (NASA Exoplanet Archive)</h4>' + table(hosts.rows, ['st_refname', 'st_spectype', 'st_teff', 'st_rad', 'st_mass', 'st_met', 'st_age', 'st_lum', 'sy_dist'], {st_refname: 'Reference', st_spectype: 'Spectral type', st_teff: 'Teff (K)', st_rad: 'Radius (R☉)', st_mass: 'Mass (M☉)', st_met: '[Fe/H]', st_age: 'Age (Gyr)', st_lum: 'log L (L☉)', sy_dist: 'Distance (pc)'}, {st_refname: referenceHtml}) + '<p class="fineprint">One row per published solution; values are not combined.</p>';
  const ps = by['nasa-ps'];
  if (ps?.status === 'ok') html += '<h4>Planetary Systems rows (NASA Exoplanet Archive)</h4>' + table(ps.rows, ['pl_refname', 'default_flag', 'rowupdate', 'pl_pubdate'], {pl_refname: 'Reference', default_flag: 'Default', rowupdate: 'Row updated', pl_pubdate: 'Published'}, {pl_refname: referenceHtml});
  const ml = by['nasa-ml'];
  if (ml?.status === 'ok') html += '<h4>Microlensing solutions (NASA Exoplanet Archive)</h4>' + table(ml.rows.slice(0, 12), Object.keys(ml.rows[0]).filter(k => !/^(rowupdate|pl_pubdate|releasedate|htmlparam)/.test(k)).slice(0, 18));
  const fop = by.exofop;
  if (fop?.status === 'ok' || fop?.status === 'none') html += `<h4>ExoFOP-TESS</h4><p>${esc(fop.star || '')}${fop.items?.length ? ` <span class="muted">${esc(fop.items.join(' · '))}</span>` : ''} <a href="${esc(fop.link)}" target="_blank" rel="noopener">Open the full ExoFOP page ↗</a></p>`;
  const ads = by.ads;
  if (ads?.status === 'ok') html += `<h4>Papers (NASA ADS)</h4><ol class="live-papers">${ads.rows.map(d => `<li><a href="https://ui.adsabs.harvard.edu/abs/${encodeURIComponent(d.bibcode)}/abstract" target="_blank" rel="noopener">${esc((d.title || [d.bibcode])[0])}</a> <small class="muted">${esc([(d.author || []).slice(0, 3).join('; ') + ((d.author || []).length > 3 ? ' et al.' : ''), d.pubdate, d.doctype].filter(Boolean).join(' · '))}</small></li>`).join('')}</ol><p class="fineprint">ADS full-text search for ${esc(ads.query)}; a match means the name appears in the paper.</p>`;
  if (!result.relay) html += '<p class="muted">The NASA Exoplanet Archive tables, ExoFOP, VizieR and ADS need the site’s live-data relay, which is not configured yet; SIMBAD was asked directly.</p>';
  html += `<p class="fineprint">Fetched ${esc(result.fetchedAt)}${result.fresh ? ' (this browser’s cache bypassed; the relay may still answer from its one-hour cache)' : ''}; values are shown as the archives return them. “New since the release” compares row counts and update dates with the records packaged for ${esc(object.name)} in this release, not with the archive’s own history.</p>`;
  return html;
}

// Mount in the Evidence tab. options.aliases: NASA-stated names from the detail shard;
// options.releasedAt: when this release's atlas was generated (manifest).
export function mountLiveSources(container, object, records = [], options = {}) {
  if (!container || !object) return;
  const base = releaseBaseline(object, records, options.releasedAt), seen = readSeen(object.objectId);
  const box = document.createElement('section');
  box.className = 'live-extras';
  box.innerHTML = `<h3>Live from the archives</h3><p class="muted">Ask every public source this page can reach for ${esc(object.name)} now: the NASA Exoplanet Archive tables (Planetary Systems, PSCompPars, TOI, KOI, K2, stellar hosts, spectra), ExoFOP-TESS, Gaia DR3 and SWEET-Cat at VizieR, SIMBAD and NASA ADS, with links for what no web page can read. Each row says when it was fetched, what came back, and whether that is <strong>new since the release</strong> compared with the rows packaged in this dossier. Nothing is stored by WorldsIndex; these values are not part of the release.${seen?.at ? ` This browser last checked this object on ${esc(String(seen.at).slice(0, 10))}; rows that differ are marked.` : ''}</p>` +
    `<div class="curve-toolbar"><button type="button" class="button" data-live-extras data-live-check>Check all sources now</button><span class="live-progress" data-live-progress role="status" aria-live="polite"></span></div>` +
    `<ol class="live-rows" data-live-rows>${SOURCES.map(spec => `<li data-live-row="${esc(spec.key)}" data-live-status="idle">${rowHtml(spec, null, base, seen)}</li>`).join('')}</ol><div data-live-extras-out></div>`;
  container.append(box);
  const out = box.querySelector('[data-live-extras-out]'), button = box.querySelector('[data-live-check]'), progress = box.querySelector('[data-live-progress]');
  const row = key => box.querySelector(`[data-live-row="${key}"]`);
  let last = null;
  button.onclick = async () => {
    const fresh = button.dataset.liveFresh === '1';
    button.disabled = true; out.innerHTML = ''; let done = 0, news = 0;
    for (const spec of SOURCES) { const li = row(spec.key); li.dataset.liveStatus = 'pending'; li.innerHTML = rowHtml(spec, {status: 'pending'}, base, seen); }
    progress.textContent = `Checking ${SOURCES.length} sources…`;
    try {
      last = await load(object, records, {fresh, aliases: options.aliases || [], releasedAt: options.releasedAt, onRow: (result) => {
        const spec = SOURCES.find(s => s.key === result.key), li = row(result.key);
        done += 1; if (result.comparison?.state === 'new') news += 1;
        li.dataset.liveStatus = result.status; li.innerHTML = rowHtml(spec, result, base, seen);
        progress.textContent = `Checked ${done} of ${SOURCES.length} sources${news ? ` · ${news} with something new since the release` : ''}`;
      }});
      out.innerHTML = extrasHtml(last);
      progress.textContent = `Checked all ${SOURCES.length} sources at ${clock(last.fetchedAt)}${news ? ` · ${news} with something new since the release` : ' · nothing newer than the release was found'}`;
      writeSeen(object.objectId, last);
    } catch (error) { out.innerHTML = `<p>Live data could not be fetched: ${esc(error.message)}.</p>`; progress.textContent = 'The check did not finish.'; }
    button.disabled = false; button.textContent = 'Check again (bypass this browser’s cache)'; button.dataset.liveFresh = '1';
  };
  box.addEventListener('click', async event => {
    const go = event.target.closest('[data-live-goto]');
    if (go) { document.getElementById('object-tab-' + go.dataset.liveGoto)?.click(); return; }
    const trigger = event.target.closest('[data-live-spectrum]');
    if (!trigger || !last) return;
    const rows = last.results.find(r => r.key === 'nasa-spectra')?.rows || [], rowData = rows[Number(trigger.dataset.liveSpectrum)], plot = out.querySelector('[data-live-spectrum-plot]');
    const url = spectrumFileUrl(rowData?.spec_path);
    if (!url) { plot.innerHTML = '<p>No data file is listed for this spectrum.</p>'; return; }
    plot.innerHTML = '<p>Downloading the spectrum…</p>';
    try {
      const relay = await relayAddress();
      if (!relay) throw new NeedsRelay();
      const r = await fetch(via(relay, url), {credentials: 'omit'});
      if (!r.ok) throw Error(`HTTP ${r.status}`);
      const {points, value} = spectrumPoints(parseIpacTable(await r.text()));
      plot.innerHTML = `<p><strong>${esc(rowData.spec_type || 'Spectrum')}</strong> · ${esc(rowData.instrument || '')} · ${esc(rowData.authors || rowData.bibcode || '')} · ${points.length} points${value ? ` of ${esc(value)}` : ''} · <a href="${esc(spectraPage(object.name))}" target="_blank" rel="noopener">archive spectra page ↗</a></p>${spectrumSvg(points, `${rowData.spec_type || 'Spectrum'} of ${object.name}`)}`;
    } catch (error) { plot.innerHTML = `<p>Could not load the spectrum: ${esc(error.message)}.</p>`; }
  });
}

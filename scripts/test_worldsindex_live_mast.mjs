// Tests for worldsindex/assets/live-mast.js (no network): the FITS table reader on a
// hand-built file and on synthetic TESS/Kepler light curves written by astropy
// (tests/fixtures/worldsindex-lightcurves, made by scripts/make_worldsindex_lc_fixtures.py),
// identifier lookup, MAST product classification, and plain-word download errors.
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  readFitsTable, pointsFromLightcurve, hostIdentifiers, mastTargetNames, classifyProduct, sortProducts, keplerQuarter,
  findLightcurves, loadLightcurve, downloadErrorMessage, lookupTicByName, QUALITY_MASKS, identifierLabel,
} from '../worldsindex/assets/live-mast.js';

function card(key, value) {
  const v = typeof value === 'string' ? `'${value.padEnd(8)}'` : typeof value === 'boolean' ? (value ? 'T' : 'F') : String(value);
  return (key.padEnd(8) + '= ' + v.padStart(typeof value === 'string' ? 0 : 20)).padEnd(80);
}
function header(cards) {
  let text = cards.map(([k, v]) => card(k, v)).join('') + 'END'.padEnd(80);
  while (text.length % 2880) text += ' ';
  return Buffer.from(text, 'ascii');
}
const rows = [
  [1000.0, 5000.5, 2.5, 0], [1000.1, 5010.0, 2.5, 0], [1000.2, NaN, 2.5, 0],
  [1000.3, 4990.0, 2.5, 128], [1000.4, 5005.0, 2.5, 0],
];
const width = 8 + 8 + 4 + 4 + 4; // TIME D, LABEL 8A, PDCSAP_FLUX E, PDCSAP_FLUX_ERR E, QUALITY J
const primary = header([['SIMPLE', true], ['BITPIX', 8], ['NAXIS', 0], ['EXTEND', true], ['OBJECT', 'TIC 1']]);
const ext = header([['XTENSION', 'BINTABLE'], ['BITPIX', 8], ['NAXIS', 2], ['NAXIS1', width], ['NAXIS2', rows.length],
  ['PCOUNT', 0], ['GCOUNT', 1], ['TFIELDS', 5], ['TTYPE1', 'TIME'], ['TFORM1', 'D'], ['TTYPE2', 'LABEL'], ['TFORM2', '8A'],
  ['TTYPE3', 'PDCSAP_FLUX'], ['TFORM3', 'E'], ['TTYPE4', 'PDCSAP_FLUX_ERR'], ['TFORM4', 'E'], ['TTYPE5', 'QUALITY'], ['TFORM5', 'J'],
  ['BJDREFI', 2457000], ['BJDREFF', 0.0], ['TIMESYS', 'TDB']]);
let data = Buffer.alloc(Math.ceil(width * rows.length / 2880) * 2880);
rows.forEach(([t, f, e, q], i) => {
  const at = i * width;
  data.writeDoubleBE(t, at); data.write('abcdefgh', at + 8, 'ascii');
  data.writeFloatBE(f, at + 16); data.writeFloatBE(e, at + 20); data.writeInt32BE(q, at + 24);
});
const file = Buffer.concat([primary, ext, data]);
const buffer = file.buffer.slice(file.byteOffset, file.byteOffset + file.byteLength);

const table = readFitsTable(buffer, ['TIME', 'PDCSAP_FLUX', 'QUALITY', 'MISSING']);
assert.equal(table.rows, 5);
assert.equal(table.TIME[1], 1000.1);
assert.ok(Math.abs(table.PDCSAP_FLUX[0] - 5000.5) < 1e-3);
assert.equal(table.QUALITY[3], 128);
assert.equal(table.MISSING, null);
assert.equal(table.header.BJDREFI, 2457000);

const parsed = pointsFromLightcurve(buffer, {mission: 'TESS', label: 'TESS sector 1 · 2 min'});
assert.equal(parsed.rawRowCount, 5);
assert.equal(parsed.points.length, 3, 'NaN flux and non-zero QUALITY rows are dropped');
assert.equal(parsed.points[0][0], 2458000.0, 'TIME is converted to BJD_TDB with BJDREFI/BJDREFF');
assert.equal(parsed.points[0][3], 'TESS');
assert.equal(parsed.valueColumn, 'PDCSAP_FLUX');

assert.throws(() => readFitsTable(new ArrayBuffer(100), ['TIME']), /header ended early/);
assert.throws(() => readFitsTable(buffer.slice(0, 2880 * 2 + 40), ['TIME']), /truncated/, 'a cut-off download is reported as truncated');

// ---- synthetic light curves written by astropy
const fixtures = new URL('../tests/fixtures/worldsindex-lightcurves/', import.meta.url);
const expected = JSON.parse(readFileSync(new URL('expected.json', fixtures), 'utf8'));
const bytesOf = (name) => { const b = readFileSync(new URL(name, fixtures)); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength); };
const tess = bytesOf(expected.tess.file), kepler = bytesOf(expected.kepler.file);

const tessTable = readFitsTable(tess, ['TIME', 'PDCSAP_FLUX', 'SAP_FLUX', 'QUALITY', 'X_UNSIGNED', 'NOT_THERE']);
assert.equal(tessTable.rows, expected.tess.rows);
assert.equal(tessTable.TIME[0], expected.tess.time0);
assert.equal(tessTable.PDCSAP_FLUX[0], expected.tess.pdcsap0, 'float32 flux matches astropy exactly');
assert.equal(tessTable.SAP_FLUX[0], expected.tess.sap0);
assert.equal(tessTable.QUALITY[200], expected.tess.quality200);
assert.equal(tessTable.X_UNSIGNED[539], expected.tess.unsigned539, 'TZERO is applied (unsigned 16-bit column)');
assert.equal(tessTable.NOT_THERE, null);
assert.equal(tessTable.primaryHeader.SECTOR, expected.tess.sector, 'the APERTURE image after the table does not confuse the reader');
assert.equal(tessTable.header.TUNIT1, 'BJD - 2457000, days', 'quoted header strings may contain commas');

const tessDefault = pointsFromLightcurve(tess, {}); // mission read from TELESCOP
assert.equal(tessDefault.mission, 'TESS');
assert.equal(tessDefault.points.length, expected.tess.defaultKept, 'default mask keeps stray-light (4096) cadences');
assert.equal(tessDefault.droppedMissing, expected.tess.missing);
assert.equal(tessDefault.droppedQuality, expected.tess.rows - expected.tess.missing - expected.tess.defaultKept);
assert.equal(tessDefault.points[0][0], expected.tess.bjdref + expected.tess.time0);
assert.equal(tessDefault.sequence, 14);
assert.equal(tessDefault.target, 'TIC 25155310');
assert.equal(tessDefault.valueColumn, 'PDCSAP_FLUX');
assert.ok(Math.abs(tessDefault.cadenceMinutes - 2) < 1e-9);
const tessStrict = pointsFromLightcurve(tess, { mission: 'TESS', shortLabel: 'TESS sector 14 · 2-min' }, { quality: 'strict' });
assert.equal(tessStrict.points.length, expected.tess.strictKept, 'strict keeps only QUALITY = 0');
assert.equal(tessStrict.points[0][4], 'TESS sector 14 · 2-min', 'stream label without the file size');

const kep = pointsFromLightcurve(kepler, {});
assert.equal(kep.mission, 'Kepler');
assert.equal(kep.valueColumn, 'SAP_FLUX', 'all-NaN PDCSAP falls back to SAP');
assert.equal(kep.points.length, expected.kepler.defaultKept);
assert.equal(kep.points[0][0], expected.kepler.bjdref + expected.kepler.time0, 'Kepler times count from BJD 2454833');
assert.equal(kep.points[0][1], expected.kepler.sap0);
assert.equal(kep.sequence, 4);
assert.equal(kep.target, 'KIC 11446443');
assert.equal(QUALITY_MASKS.TESS, 175);

// ---- identifiers
const ids = hostIdentifiers({ name: 'TOI-1150.01', hostName: 'TIC 16740101' }, [
  { sourceId: 'nasa-toi', values: { tid: 16740101 } },
  { sourceId: 'nasa-koi', values: { kepid: 11446443 } },
  { sourceId: 'nasa-k2', values: { epic_hostname: 'EPIC 210848071', epic_candname: 'EPIC 210848071.01', tic_id: 'TIC 26123781' } },
  { sourceId: 'nasa-pscomppars', values: { tic_id: 'TIC 16740101' } },
  { sourceId: 'nasa-ps', values: { tic_id: null } },
]);
assert.deepEqual(ids, { tic: ['16740101', '26123781'], kic: ['11446443'], epic: ['210848071'] });
assert.equal(identifierLabel(ids), 'TIC 16740101, TIC 26123781, KIC 11446443, EPIC 210848071');
assert.deepEqual(hostIdentifiers({ name: 'WASP-12 b', hostName: 'WASP-12' }, []), { tic: [], kic: [], epic: [] });
assert.deepEqual(hostIdentifiers({ name: 'K00001.01', hostName: 'KIC 011446443' }, []).kic, ['11446443'], 'leading zeros dropped');
assert.deepEqual(mastTargetNames({ tic: ['25155310'], kic: ['11446443'], epic: ['201367065'] }),
  ['25155310', '0000000025155310', 'TIC 25155310', 'kplr011446443', 'ktwo201367065']);

// ---- product classification
const P = (dataURI, extra = {}) => classifyProduct({ dataURI, size: 2097152, parent_obsid: '1', ...extra });
const spoc = P('mast:TESS/product/tess2019198215352-s0014-0000000025155310-0150-s_lc.fits');
assert.equal(spoc.kind, 'tess-2min'); assert.equal(spoc.sequence, 14); assert.equal(spoc.target, 'TIC 25155310');
assert.equal(spoc.label, 'TESS sector 14 · 2-min · 2.0 MB'); assert.equal(spoc.shortLabel, 'TESS sector 14 · 2-min');
const ffi = P('mast:HLSP/tess-spoc/s0040/target/0000/0000/2515/5310/hlsp_tess-spoc_tess_phot_0000000025155310-s0040_tess_v1_lc.fits');
assert.equal(ffi.kind, 'tess-ffi'); assert.equal(ffi.cadence, '10-min'); assert.equal(ffi.pipeline, 'TESS-SPOC');
assert.equal(P('mast:HLSP/tess-spoc/s0010/x/hlsp_tess-spoc_tess_phot_0000000025155310-s0010_tess_v1_lc.fits').cadence, '30-min');
assert.equal(P('mast:HLSP/tess-spoc/s0060/x/hlsp_tess-spoc_tess_phot_0000000025155310-s0060_tess_v1_lc.fits').cadence, '200-s');
const kq4 = P('mast:Kepler/url/missions/kepler/lightcurves/0114/011446443/kplr011446443-2010078095331_llc.fits');
assert.equal(kq4.kind, 'kepler-long'); assert.equal(kq4.sequence, 4); assert.equal(kq4.target, 'KIC 11446443');
const ksc = P('mast:Kepler/url/missions/kepler/lightcurves/0114/011446443/kplr011446443-2010009091648_slc.fits');
assert.equal(ksc.kind, 'kepler-short'); assert.equal(ksc.sequence, 4); assert.match(ksc.label, /month ending 2010-01-09/);
const k2 = P('mast:K2/url/missions/k2/lightcurves/c5/211800000/89000/ktwo211889233-c05_llc.fits');
assert.equal(k2.kind, 'k2-long'); assert.equal(k2.sequence, 5); assert.equal(k2.target, 'EPIC 211889233');
for (const rejected of [
  'mast:TESS/product/tess2019198215352-s0014-0000000025155310-0150-s_tp.fits',        // target pixels
  'mast:TESS/product/tess2020238165205-s0029-0000000025155310-0193-a_fast-lc.fits',   // 20-second file
  'mast:TESS/product/tess2019198215352-s0014-0000000025155310-0150-s_dvt.fits',       // DV time series
  'mast:HLSP/qlp/s0001/x/hlsp_qlp_tess_ffi_s0001-0000000025155310_tess_v01_llc.fits',  // another HLSP
  'mast:TESS/product/../tess2019198215352-s0014-0000000025155310-0150-s_lc.fits',
  'mast:HST/product/x_drz.fits', '',
]) assert.equal(P(rejected), null, rejected);
assert.equal(keplerQuarter('2009131105131'), 0); assert.equal(keplerQuarter('2013131215648'), 17); assert.equal(keplerQuarter('2009350160919'), 3);
assert.equal(keplerQuarter('2014001000000'), null);
assert.deepEqual(sortProducts([kq4, k2, ffi, spoc]).map((p) => p.kind), ['tess-2min', 'tess-ffi', 'kepler-long', 'k2-long']);

// ---- search: by identifier first, by position only when that finds nothing
function fakeMast({ byName = [], byPosition = [], products = [] }) {
  const calls = [];
  const invoke = async (request) => {
    calls.push(request);
    if (request.service === 'Mast.Caom.Filtered') return byName;
    if (request.service === 'Mast.Caom.Filtered.Position') return byPosition;
    if (request.service === 'Mast.Caom.Products') return products.filter((p) => request.params.obsid.split(',').includes(String(p.parent_obsid)));
    throw Error('unexpected service ' + request.service);
  };
  return { invoke, calls };
}
const observations = [
  { obsid: 11, obs_collection: 'TESS', target_name: '25155310', sequence_number: 14 },
  { obsid: 12, obs_collection: 'HLSP', provenance_name: 'TESS-SPOC', target_name: '25155310', sequence_number: 40 },
  { obsid: 13, obs_collection: 'HLSP', provenance_name: 'QLP', target_name: '25155310', sequence_number: 1 },
];
const productRows = [
  { parent_obsid: 11, dataURI: spoc.uri, size: 2e6 }, { parent_obsid: 11, dataURI: spoc.uri.replace('_lc', '_tp'), size: 4e7 },
  { parent_obsid: 12, dataURI: ffi.uri, size: 3e5 }, { parent_obsid: 13, dataURI: 'mast:HLSP/qlp/a/b_llc.fits' },
  { parent_obsid: 11, dataURI: spoc.uri, size: 2e6 }, // duplicate row
];
let fake = fakeMast({ byName: observations, products: productRows });
let found = await findLightcurves({ ids: { tic: ['25155310'], kic: [], epic: [] }, ra: 1, dec: 2 }, { invoke: fake.invoke });
assert.equal(found.searchedBy, 'identifier');
assert.deepEqual(found.products.map((p) => p.kind), ['tess-2min', 'tess-ffi']);
assert.deepEqual(found.targets, ['TIC 25155310']);
assert.deepEqual(fake.calls[0].params.filters[0], { paramName: 'target_name', values: ['25155310', '0000000025155310', 'TIC 25155310'] });
assert.ok(!fake.calls.some((c) => c.service === 'Mast.Caom.Filtered.Position'), 'no cone search when the identifier finds files');
assert.equal(fake.calls.at(-1).params.obsid, '11,12', 'only accepted observations are expanded into products');

fake = fakeMast({ byName: [], byPosition: observations.slice(0, 1), products: productRows });
found = await findLightcurves({ ids: { tic: ['999'], kic: [], epic: [] }, ra: 10, dec: -20 }, { invoke: fake.invoke });
assert.equal(found.searchedBy, 'position');
assert.equal(fake.calls[1].params.position, '10, -20, 0.0014');
assert.equal(found.products.length, 1);
fake = fakeMast({});
found = await findLightcurves({ ids: { tic: [], kic: [], epic: [] } }, { invoke: fake.invoke });
assert.deepEqual([found.products.length, fake.calls.length], [0, 0], 'nothing to search without identifiers or coordinates');

// ---- downloads through the relay, with errors a visitor can act on
const ok = (body) => async () => new Response(body, { status: 200 });
const status = (code) => async () => new Response('no', { status: code });
const product = { ...spoc, mission: 'TESS' };
const loaded = await loadLightcurve(product, 'https://relay.example', { fetcher: ok(tess) });
assert.equal(loaded.pointCount, expected.tess.defaultKept);
assert.equal(loaded.valueUnit, 'e-/s');
assert.match(loaded.rawSha256, /^[0-9a-f]{64}$/);
assert.match(loaded.qualitySelection, /none of the bits in 175/);
assert.match(loaded.sourceUrl, /^https:\/\/mast\.stsci\.edu\/api\/v0\.1\/Download\/file\?uri=mast:TESS\//);
let seenUrl = '';
await loadLightcurve(product, 'https://relay.example', { fetcher: async (url) => { seenUrl = url; return new Response(tess); } });
assert.equal(seenUrl, 'https://relay.example/?url=' + encodeURIComponent('https://mast.stsci.edu/api/v0.1/Download/file?uri=' + spoc.uri));
await assert.rejects(loadLightcurve({ ...ffi }, 'https://relay.example', { fetcher: status(400) }), /relay does not yet pass TESS-SPOC full-frame/);
await assert.rejects(loadLightcurve(product, 'https://relay.example', { fetcher: status(404) }), /no longer has this file/);
await assert.rejects(loadLightcurve(product, 'https://relay.example', { fetcher: status(502) }), /did not answer properly \(HTTP 502\)/);
await assert.rejects(loadLightcurve(product, 'https://relay.example', { fetcher: async () => { throw new TypeError('Failed to fetch'); } }), /relay could not be reached/);
await assert.rejects(loadLightcurve(product, 'https://relay.example', { fetcher: ok('<html>not fits</html>') }), /could not be read: the FITS header ended early/);
await assert.rejects(loadLightcurve(product, '', { fetcher: ok(tess) }), /relay is not configured/);
assert.match(downloadErrorMessage(413), /40 MB/);

// ---- TIC from the NASA Exoplanet Archive when the catalog rows carry none
let tapUrl = '';
const tic = await lookupTicByName("HD 189733 b", 'https://relay.example', async (url) => { tapUrl = url; return new Response(JSON.stringify([{ tic_id: 'TIC 256364928' }])); });
assert.equal(tic, '256364928');
assert.match(decodeURIComponent(decodeURIComponent(tapUrl)), /select tic_id from pscomppars where pl_name = 'HD 189733 b'&format=json$/);
assert.equal(await lookupTicByName("x' or 1=1", 'https://relay.example', async (url) => { tapUrl = url; return new Response('[]'); }), null);
assert.match(decodeURIComponent(decodeURIComponent(tapUrl)), /pl_name = 'x'' or 1=1'/, 'quotes are escaped');
console.log('WorldsIndex live MAST: all checks passed');

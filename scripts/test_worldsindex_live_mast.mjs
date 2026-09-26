// Tests for worldsindex/assets/live-mast.js: the FITS table reader on a synthetic
// TESS-style light-curve file (no network).
import assert from 'node:assert/strict';
import { readFitsTable, pointsFromLightcurve } from '../worldsindex/assets/live-mast.js';

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
console.log('WorldsIndex live MAST: all checks passed');

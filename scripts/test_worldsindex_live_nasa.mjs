// Tests for worldsindex/assets/live-nasa.js helpers (no network).
import assert from 'node:assert/strict';
import { planetNames, spectrumFileUrl, exofopTarget, adsQuery, parseIpacTable, spectrumPoints } from '../worldsindex/assets/live-nasa.js';

assert.deepEqual(planetNames({name: 'WASP-18 b'}), ['WASP-18 b', 'WASP-18b']);
assert.deepEqual(planetNames({name: 'TOI-1000.01'}), ['TOI-1000.01']);
assert.equal(adsQuery({name: 'WASP-18 b'}), 'full:("WASP-18 b" OR "WASP-18b")');
assert.deepEqual(exofopTarget({name: 'TOI-1000.01', hostName: 'TIC 50365310'}), {param: 'id', value: '50365310', label: 'TIC 50365310'});
assert.deepEqual(exofopTarget({name: 'TOI-1000.01', hostName: 'HD 1'}), {param: 'toi', value: '1000.01', label: 'TOI-1000.01'});
assert.equal(exofopTarget({name: 'WASP-18 b', hostName: 'WASP-18'}), null);
assert.equal(spectrumFileUrl('/data/ExoData/0113/x.tbl'), 'https://exoplanetarchive.ipac.caltech.edu/data/ExoData/0113/x.tbl');
assert.equal(spectrumFileUrl('0113/x.tbl'), 'https://exoplanetarchive.ipac.caltech.edu/data/ExoData/0113/x.tbl');
assert.equal(spectrumFileUrl(''), '');

const ipac = `\\fixlen = T
\\PL_NAME = 'WASP-18 b'
|CENTRALWAVELNG|BANDWIDTH|PL_TRANDEP|PL_TRANDEPERR1|PL_TRANDEPERR2|
|        double|   double|    double|        double|        double|
|       microns|  microns|         %|             %|             %|
       1.1500     0.0500     0.9312          0.0120         -0.0120 
       1.2500     0.0500     0.9401          0.0110         -0.0110 
       1.3500     0.0500       null          0.0100         -0.0100 
`;
const t = parseIpacTable(ipac);
assert.deepEqual(t.columns, ['CENTRALWAVELNG', 'BANDWIDTH', 'PL_TRANDEP', 'PL_TRANDEPERR1', 'PL_TRANDEPERR2']);
assert.equal(t.rows.length, 3);
assert.equal(t.rows[1].PL_TRANDEP, '0.9401');
const s = spectrumPoints(t);
assert.equal(s.value, 'PL_TRANDEP');
assert.equal(s.points.length, 2, 'null values are left out');
assert.deepEqual(s.points[0], {x: 1.15, y: 0.9312, e: 0.012});
assert.deepEqual(spectrumPoints(parseIpacTable('no table here')), {points: [], value: null});
console.log('WorldsIndex live NASA: all checks passed');

// Tests for worldsindex/assets/live-nasa.js helpers (no network).
import assert from 'node:assert/strict';
import { referenceHtml, planetNames, spectrumFileUrl, exofopTarget, adsQuery, parseIpacTable, spectrumPoints } from '../worldsindex/assets/live-nasa.js';

assert.deepEqual(planetNames({name: 'WASP-18 b'}), ['WASP-18 b', 'WASP-18b']);
assert.deepEqual(planetNames({name: 'TOI-1000.01'}), ['TOI-1000.01']);
assert.equal(adsQuery({name: 'WASP-18 b'}), 'full:("WASP-18 b" OR "WASP-18b")');
assert.deepEqual(exofopTarget({name: 'TOI-1000.01', hostName: 'TIC 50365310'}), {param: 'id', value: '50365310', label: 'TIC 50365310'});
assert.deepEqual(exofopTarget({name: 'TOI-1000.01', hostName: 'HD 1'}), {param: 'toi', value: '1000.01', label: 'TOI-1000.01'});
assert.equal(exofopTarget({name: 'WASP-18 b', hostName: 'WASP-18'}), null);
assert.equal(spectrumFileUrl('80/70/32/30/WASP_39_b_3.11466_3868_2.tbl'), 'https://exoplanetarchive.ipac.caltech.edu/cgi-bin/atmospheres/nph-firefly?atmospheres&spec_path=80/70/32/30/WASP_39_b_3.11466_3868_2.tbl');
assert.equal(spectrumFileUrl('../etc/x.tbl'), '');
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
// real archive layouts (2026-09-27): eclipse files, and transmission files with text columns
const eclipse = `\\PL_NAME='WASP-39 b'
|CENTRALWAVELNG|BANDWIDTH|ESPECLIPDEP|ESPECLIPDEPERR1|ESPECLIPDEPERR2|ESPECLIPDEPLIM|
            3.6      null       0.088           0.015          -0.015              0 
`;
assert.deepEqual(spectrumPoints(parseIpacTable(eclipse)), {points: [{x: 3.6, y: 0.088, e: 0.015}], value: 'ESPECLIPDEP'});
const trans = `|CENTRALWAVELNG|BANDWIDTH|PL_TRANDEP|PL_TRANDEPERR1|PL_TRANDEPERR2|PL_TRANDEPLIM|PL_TRANDEP_AUTHORS|                         PL_TRANDEP_URL|
        0.33000   0.08000    2.08196        0.06690       -0.06584             0         Calculated /docs/atmospheres/atmospheres_calc.html
`;
assert.deepEqual(spectrumPoints(parseIpacTable(trans)).points, [{x: 0.33, y: 2.08196, e: 0.0669}]);
assert.equal(referenceHtml('<a refstr=BONOMO_ET_AL__2017 href=https://ui.adsabs.harvard.edu/abs/2017A&A...602A.107B/abstract target=ref>Bonomo et al. 2017</a>'),
  '<a href="https://ui.adsabs.harvard.edu/abs/2017A&amp;A...602A.107B/abstract" target="_blank" rel="noopener">Bonomo et al. 2017</a>');
assert.equal(referenceHtml('<a href=javascript:alert(1)>x</a>'), 'x', 'only ADS or DOI links are kept');
assert.equal(referenceHtml('Plain <b>text</b>'), 'Plain text');
console.log('WorldsIndex live NASA: all checks passed');

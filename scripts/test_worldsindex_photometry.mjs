import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { phase, overlap, transitFlux, eccentricAnomaly, relativeFlux, finite, publishedTransitParameters, median, phaseDays, foldPoints, binFolded, flattenPoints, inTransit, foldedDepthPpt } from '../worldsindex/assets/photometry.js';
import { pointsFromLightcurve } from '../worldsindex/assets/live-mast.js';
assert.equal(phase(100,2,100),0); assert.equal(phase(101,2,100),-.5); assert.equal(phase(99.5,2,100),-.25);
assert.equal(phase(1,0,0),null); assert.equal(finite(null),null); assert.equal(finite(''),null);
assert.ok(Math.abs(overlap(0,.1)-.01)<1e-12); assert.equal(overlap(2,.1),0);
const p={period:2,epoch:100,ratio:.1,scaledAxis:10,impact:0};
assert.ok(Math.abs(transitFlux(100,p)-.99)<1e-12);assert.equal(transitFlux(101,p),1);
assert.equal(transitFlux(100,{...p,scaledAxis:1}),null);
for(const e of [0,.2,.7,.95])for(const m of [0,.1,1,3,6]){const E=eccentricAnomaly(m,e);assert.ok(Math.abs(E-e*Math.sin(E)-m)<1e-10);}
const f=relativeFlux([[1,10,.01,'r','1'],[2,10,.01,'r','1']]);assert.equal(f[0][1],1);assert.ok(Math.abs(f[0][2]-.00921034037)<1e-10);
console.log('Photometry geometry, phase, uncertainty conversion, and Kepler motion passed.');

const missing=publishedTransitParameters({values:{pl_orbper:6.099615}});
assert.deepEqual(missing,{period:6.099615,epoch:null,ratio:null,axis:null,impact:null});
assert.equal(publishedTransitParameters({values:{pl_orbper:3,pl_orbperlim:1}}).period,null);
assert.equal(publishedTransitParameters({values:{pl_imppar:0}}).impact,0);

assert.deepEqual(relativeFlux([[1,100,2,'K','1'],[2,200,4,'K','1']],'e-/s'),[[1,2/3,2/150,'K','1'],[2,4/3,4/150,'K','1']]);

// ---- folding on an ephemeris
const close = (a, b, tol, msg) => assert.ok(Math.abs(a - b) <= tol, `${msg ?? ''} ${a} vs ${b}`);
assert.equal(median([3, 1, 2]), 2); assert.equal(median([4, 1, 3, 2]), 2.5); assert.equal(median([NaN]), null);
close(phaseDays(100.25, 2, 100), 0.25, 1e-12); close(phaseDays(101.75, 2, 100), -0.25, 1e-12);
close(phaseDays(2460000.5 + 1000 * 3.5, 3.5, 2460000.5), 0, 1e-9, 'a thousand orbits later is still mid-transit');
assert.equal(phaseDays(1, 0, 0), null);
const folded = foldPoints([[100.3, 1, .1], [99.9, 2, .1], [102.05, 3, .1]], 2, 100);
assert.deepEqual(folded.map((p) => p[1]), [2, 3, 1], 'folded points are sorted by phase');
close(folded[0][0], -0.1, 1e-12); close(folded[1][0], 0.05, 1e-12);
assert.deepEqual(foldPoints([[1, 1, 1]], 0, 0), []);
const bins = binFolded([[-0.09, 1], [-0.08, 3], [0.01, 5], [0.2, 9]], 0.1, 0.1);
assert.deepEqual(bins.map((b) => [Number(b.x.toFixed(3)), b.y, b.n]), [[-0.05, 2, 2], [0.05, 5, 1]], 'points outside the window are ignored');
assert.ok(inTransit(100.02, 2, 100, 0.05)); assert.ok(!inTransit(100.03, 2, 100, 0.05)); assert.ok(inTransit(100.03, 2, 100, 0.05, 1.6));
assert.ok(!inTransit(100, 2, 100, null), 'no duration, no window');

// A slow linear trend is divided out; masked (in-transit) points do not bend the trend.
const ramp = Array.from({ length: 500 }, (_, i) => { const t = i * 0.01, dip = Math.abs(t - 2.5) < 0.05 ? 0.99 : 1; return [t, (1 + 0.02 * t) * dip, 0.001]; });
const flat = flattenPoints(ramp, 0.5, (t) => Math.abs(t - 2.5) < 0.08);
for (const p of flat) if (Math.abs(p[0] - 2.5) > 0.08 && p[0] > 0.3 && p[0] < 4.7) close(p[1], 1, 1e-3, 'trend removed at ' + p[0]);
close(flat.find((p) => Math.abs(p[0] - 2.5) < 1e-9)[1], 0.99, 1.5e-3, 'the transit keeps its depth');
assert.deepEqual(flattenPoints([[1, 1, 0]], 1), [[1, 1, 0]]);

// The synthetic TESS file (astropy fixture): a 10 ppt box transit, P = 0.3 d, on a slow trend.
const expectedFixture = JSON.parse(readFileSync(new URL('../tests/fixtures/worldsindex-lightcurves/expected.json', import.meta.url), 'utf8')).tess;
const raw = readFileSync(new URL('../tests/fixtures/worldsindex-lightcurves/' + expectedFixture.file, import.meta.url));
const lc = pointsFromLightcurve(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength), { mission: 'TESS', shortLabel: 'S14' });
const { periodDays, t0Bjd, durationHours, depthPpt } = expectedFixture.transit, dur = durationHours / 24;
const normalised = relativeFlux(lc.points, 'e-/s');
const detrended = flattenPoints(normalised, 0.15, (t) => inTransit(t, periodDays, t0Bjd, dur, 1.6));
const fold = foldPoints(detrended, periodDays, t0Bjd);
close(foldedDepthPpt(fold, dur), depthPpt, 1.2, 'recovered depth (ppt)');
const binned = binFolded(fold, dur / 4, 3 * dur);
close(Math.min(...binned.map((b) => b.y)), 1 - depthPpt / 1000, 0.002, 'deepest bin');
close(binned.find((b) => Math.abs(b.x) > 2 * dur).y, 1, 0.002, 'out-of-transit bins sit at 1');
assert.equal(foldedDepthPpt(fold, null), null);
console.log('Folding, binning, trend removal and depth recovery passed.');

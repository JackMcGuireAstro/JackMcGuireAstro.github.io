import assert from 'node:assert/strict';
import { phase, overlap, transitFlux, eccentricAnomaly, relativeFlux, finite } from '../worldsindex/assets/photometry.js';
assert.equal(phase(100,2,100),0); assert.equal(phase(101,2,100),-.5); assert.equal(phase(99.5,2,100),-.25);
assert.equal(phase(1,0,0),null); assert.equal(finite(null),null); assert.equal(finite(''),null);
assert.ok(Math.abs(overlap(0,.1)-.01)<1e-12); assert.equal(overlap(2,.1),0);
const p={period:2,epoch:100,ratio:.1,scaledAxis:10,impact:0};
assert.ok(Math.abs(transitFlux(100,p)-.99)<1e-12);assert.equal(transitFlux(101,p),1);
assert.equal(transitFlux(100,{...p,scaledAxis:1}),null);
for(const e of [0,.2,.7,.95])for(const m of [0,.1,1,3,6]){const E=eccentricAnomaly(m,e);assert.ok(Math.abs(E-e*Math.sin(E)-m)<1e-10);}
const f=relativeFlux([[1,10,.01,'r','1'],[2,10,.01,'r','1']]);assert.equal(f[0][1],1);assert.ok(Math.abs(f[0][2]-.00921034037)<1e-10);
console.log('Photometry geometry, phase, uncertainty conversion, and Kepler motion passed.');

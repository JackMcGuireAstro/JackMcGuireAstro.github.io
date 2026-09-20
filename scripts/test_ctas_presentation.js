const assert = require('node:assert/strict');
const p = require('../ctas/presentation.js');
assert.match(p.thumbnail({name:'zero',ra_deg:0,dec_deg:0}), /hips2fits/);
for (const coords of [[null,0],['',0],[360,0],[10,91],[NaN,0]]) assert.doesNotMatch(p.thumbnail({ra_deg:coords[0],dec_deg:coords[1]}), /<img/);
assert.match(p.thumbnail({name:'<script>',ra_deg:12,dec_deg:-4}), /&lt;script&gt;/);
const rows=[{event_id:'alert',follow_up_counts:{classifications:900}},{event_id:'phot',follow_up_counts:{observations:10}},{event_id:'spec',follow_up_counts:{spectra:2,publications:1}}];
assert.deepEqual(p.examples(rows,'total').map(x=>x.event_id),['phot','spec']);
assert.deepEqual(p.examples(rows,'spectra').map(x=>x.event_id),['spec','phot']);
assert.equal(rows[0].event_id,'alert');
console.log('Archival coordinate handling, escaped labels, and evidence ranking passed.');

assert.equal(p.classInfo({classification:'high-importance'}).physical,'');
assert.equal(p.classInfo({classification:'SN Ia'}).physical,'SN Ia');
assert.equal(p.classInfo({classification:'high-importance'}).alert,'high-importance');
assert.deepEqual(p.outcomes({follow_up:{observations:[{detection:true},{detection:false,limiting_flux:1,photometry_method:'forced'},{detection:true,superseded:true},{flux:-1,photometry_method:'forced'}]}}),{active:3,detections:1,limits:1,forced:2});
assert.doesNotMatch(p.outcomeText({classification:'high-importance',follow_up_counts:{observations:10,classifications:1}}),/Reported high-importance/);

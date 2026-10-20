// Tests for worldsindex/assets/live-sources.js (no network): identifiers, the release
// baseline and comparison, the registry, the pool cap, load() with a stubbed fetch, the
// no-relay and fresh paths, row text and the per-browser memory.
import assert from 'node:assert/strict';
import {
  SOURCES, identifiers, releaseBaseline, compareWithRelease, parseCsv, tapRows, pool, runSource, load, makeContext,
  rowHtml, summaryText, changeSinceSeen, summarize, extrasHtml, problemResult,
} from '../worldsindex/assets/live-sources.js';

const object = {objectId: 'object-wasp-39-b', name: 'WASP-39 b', hostName: 'WASP-39', raDeg: 217.3266, decDeg: -3.4445, methodCode: 'primary-transit-photometry',
  sourceIds: ['exoplanet-eu', 'nasa-ps', 'nasa-pscomppars', 'open-exoplanet-catalogue'], sourceRecordCount: 16, identityState: 'CANONICAL'};
const records = [
  {sourceId: 'nasa-ps', updated: '2019-03-18', values: {pl_orbper: 4.05}}, {sourceId: 'nasa-ps', updated: '2026-01-29', values: {pl_orbper: 4.055}},
  {sourceId: 'nasa-pscomppars', values: {}}, {sourceId: 'exoplanet-eu', values: {updated: '2026-02-01'}},
];
const toi = {objectId: 'candidate-toi-1000-01', name: 'TOI-1000.01', hostName: 'TIC 50365310', raDeg: 1, decDeg: 2, sourceIds: ['nasa-toi'], sourceRecordCount: 1};

// identifiers from names, host, records and NASA-stated aliases
let ids = identifiers(object, records, [{value: 'Gaia DR3 3643098875811934208', kind: 'HOST'}]);
assert.deepEqual(ids.names, ['WASP-39 b', 'WASP-39b']);
assert.equal(ids.host, 'WASP-39');
assert.deepEqual(ids.gaia, ['3643098875811934208']);
assert.deepEqual(ids.position, {ra: 217.3266, dec: -3.4445});
ids = identifiers(toi, [{sourceId: 'nasa-toi', values: {toi: '1000.01', tid: '50365310'}}]);
assert.deepEqual(ids.tic, ['50365310']);
assert.deepEqual(ids.toi, ['1000.01']);
ids = identifiers({name: 'KOI-72.01', hostName: 'KIC 11446443', sourceIds: []}, [{sourceId: 'nasa-koi', values: {kepid: '11446443', kepoi_name: 'K00072.01'}}]);
assert.deepEqual(ids.koi, ['72.01']);
assert.deepEqual(ids.kic, ['11446443']);

// release baseline: counts and latest dates per source from the packaged records
const base = releaseBaseline(object, records);
assert.deepEqual(base.bySource['nasa-ps'], {count: 2, latest: '2026-01-29'});
assert.deepEqual(base.bySource['exoplanet-eu'], {count: 1, latest: '2026-02-01'});
const byKey = (k) => SOURCES.find((s) => s.key === k);
assert.equal(compareWithRelease(byKey('nasa-ps'), {status: 'ok', count: 2, latest: '2026-01-29'}, base).state, 'same');
let cmp = compareWithRelease(byKey('nasa-ps'), {status: 'ok', count: 3, latest: '2026-10-01'}, base);
assert.equal(cmp.state, 'new');
assert.match(cmp.text, /updated upstream 2026-10-01, release holds up to 2026-01-29/);
assert.match(cmp.text, /3 upstream vs 2 retained \(\+1\)/);
assert.equal(compareWithRelease(byKey('nasa-ps'), {status: 'ok', count: 1, latest: '2019-01-01'}, base).state, 'fewer');
assert.equal(compareWithRelease(byKey('nasa-toi'), {status: 'ok', count: 1}, base).state, 'live-only', 'a source outside the object’s release membership');
assert.equal(compareWithRelease(byKey('simbad'), {status: 'none', count: 0}, base).state, 'none');
assert.equal(compareWithRelease(byKey('nasa-ps'), {status: 'unavailable', count: 0}, base).state, 'unknown');
cmp = compareWithRelease(byKey('nasa-toi'), {status: 'ok', count: 2}, releaseBaseline(toi, []));
assert.equal(cmp.state, 'new', 'the release lists the source for this object but packaged no rows');

// registry and readers
for (const k of ['nasa-ps', 'nasa-pscomppars', 'nasa-toi', 'nasa-koi', 'nasa-k2', 'nasa-stellarhosts', 'nasa-spectra', 'nasa-ml', 'exofop', 'gaia-dr3', 'simbad', 'sweetcat', 'ads', 'exoplanet-eu', 'mast']) assert.ok(byKey(k), 'source ' + k);
assert.equal(new Set(SOURCES.map((s) => s.key)).size, SOURCES.length);
assert.deepEqual(parseCsv('Source,Gmag\n"123",12.5\n').rows, [{Source: '123', Gmag: '12.5'}]);
assert.ok(parseCsv('<VOTABLE/>').markup);
assert.deepEqual(tapRows({metadata: [{name: 'main_id'}], data: [['WASP-39']]}), [{main_id: 'WASP-39'}]);

// the pool: at most `limit` in flight, results in job order, each reported as it finishes
{
  let running = 0, peak = 0; const order = [];
  const job = (i, ms) => () => new Promise((resolve) => { running += 1; peak = Math.max(peak, running); setTimeout(() => { running -= 1; resolve(i); }, ms); });
  const results = await pool([job(0, 30), job(1, 5), job(2, 5), job(3, 5)], 2, (index) => order.push(index));
  assert.deepEqual(results, [0, 1, 2, 3]);
  assert.equal(peak, 2);
  assert.equal(order[0], 1);
}
assert.equal(problemResult({name: 'AbortError'}).note, 'Unavailable right now (timed out)');
assert.equal(problemResult({noRelay: true}).status, 'no-relay');

// load() with a stand-in network: every source answers, comparisons are attached, fresh → cache: "reload"
{
  const calls = [];
  const reply = (body, type = 'application/json') => new Response(typeof body === 'string' ? body : JSON.stringify(body), {status: 200, headers: {'Content-Type': type}});
  const fetcher = async (url, init) => {
    calls.push({url: String(url), init});
    const u = String(url), target = decodeURIComponent(decodeURIComponent(u.split('?url=')[1] || ''));
    if (u.includes('simbad')) return reply({metadata: [{name: 'main_id'}, {name: 'otype'}, {name: 'ra'}, {name: 'dec'}], data: [['WASP-39', 'PM*', 217.3, -3.4]]});
    if (target.includes('from ps ')) return reply([{pl_name: 'WASP-39 b', pl_refname: 'A', default_flag: 1, rowupdate: '2026-09-30', pl_pubdate: '2026-09'}, {pl_name: 'WASP-39 b', pl_refname: 'B', default_flag: 0, rowupdate: '2019-03-18', pl_pubdate: '2019-03'}, {pl_name: 'WASP-39 b', pl_refname: 'C', default_flag: 0, rowupdate: '2016-01-01'}]);
    if (target.includes('select gaia_dr3_id')) return reply([{gaia_dr3_id: 'Gaia DR3 3643098875811934208'}]);
    if (target.includes('from pscomppars')) return reply([{pl_name: 'WASP-39 b', gaia_dr3_id: 'Gaia DR3 3643098875811934208', tic_id: 'TIC 181949561', rowupdate: '2026-09-30'}]);
    if (target.includes('from stellarhosts')) return reply([{hostname: 'WASP-39', st_refname: '<a href=https://ui.adsabs.harvard.edu/abs/2011A/abstract>Faedi 2011</a>', st_teff: 5400}]);
    if (target.includes('from spectra')) return reply([{pl_name: 'WASP-39 b', spec_type: 'transmission', instrument: 'NIRISS', spec_path: '80/70/32/30/WASP_39_b_3.11466_3868_2.tbl'}]);
    if (target.includes('gaiadr3')) return reply('Source,RA_ICRS,DE_ICRS,Gmag,Plx,PM\n3643098875811934208,217.3266,-3.4445,11.9,4.6,30\n', 'text/csv');
    if (target.includes('sweetcat') || target.includes('691/A53/catalog')) return reply('Name,Teff,logg\nWASP-39,5485,4.5\n', 'text/csv');
    if (target.includes('adsabs')) return new Response('key', {status: 503});
    return new Response('', {status: 404});
  };
  const rows = [];
  const result = await load(object, records, {relay: 'https://relay.test', fresh: true, fetcher, releasedAt: '2026-10-03T13:53:58.029Z', onRow: (r) => rows.push(r.key)});
  assert.equal(result.results.length, SOURCES.length);
  assert.equal(rows.length, SOURCES.length);
  const by = Object.fromEntries(result.results.map((r) => [r.key, r]));
  assert.equal(by['nasa-ps'].count, 3); assert.equal(by['nasa-ps'].comparison.state, 'new'); assert.match(by['nasa-ps'].comparison.text, /updated upstream 2026-09-30/);
  assert.equal(by['nasa-pscomppars'].status, 'ok'); assert.equal(by['nasa-pscomppars'].comparison.state, 'same', 'the upstream composite row predates this release');
  assert.equal(compareWithRelease(byKey('nasa-pscomppars'), {status: 'ok', count: 1, latest: '2026-10-05'}, releaseBaseline(object, records, '2026-10-03T13:53:58Z')).state, 'new', 'a row updated after the release was built is new even when the packaged row carries no date');
  assert.equal(by['nasa-toi'].status, 'not-applicable'); assert.match(by['nasa-toi'].note, /TOI or TIC/);
  assert.equal(by['nasa-koi'].status, 'not-applicable'); assert.equal(by['nasa-k2'].status, 'not-applicable'); assert.equal(by['nasa-ml'].status, 'not-applicable');
  assert.equal(by['nasa-stellarhosts'].count, 1); assert.equal(by['nasa-spectra'].count, 1);
  assert.equal(by['gaia-dr3'].status, 'ok'); assert.equal(by['gaia-dr3'].how, 'source id from PSCompPars'); assert.equal(by['gaia-dr3'].rows[0].name, 'Gaia DR3 3643098875811934208');
  assert.equal(by.simbad.count, 1); assert.equal(by.simbad.how, 'by name WASP-39 b');
  assert.equal(by.sweetcat.count, 1);
  assert.equal(by.ads.status, 'needs-key'); assert.match(by.ads.note, /NASA ADS key/);
  assert.equal(by.exofop.status, 'not-applicable');
  assert.equal(by['exoplanet-eu'].status, 'link'); assert.match(by['exoplanet-eu'].link, /^https:\/\/exoplanet\.eu\/catalog\//);
  assert.equal(by.mast.status, 'link');
  const provider = calls.filter((c) => !c.url.endsWith('live-config.json'));
  assert.ok(provider.every((c) => c.init.cache === 'reload'), 'fresh bypasses only the browser cache');
  assert.ok(provider.every((c) => !/fresh=/.test(c.url)), 'the relay address never changes');
  assert.ok(provider.every((c) => c.init.credentials === 'omit'));
  assert.ok(provider.some((c) => c.url.startsWith('https://simbad.cds.unistra.fr/')), 'SIMBAD is asked directly');
  assert.ok(provider.filter((c) => !c.url.startsWith('https://simbad')).every((c) => c.url.startsWith('https://relay.test/?url=')), 'everything else goes through the relay');
  const html = extrasHtml(result);
  assert.match(html, /Atmospheric spectra/); assert.match(html, /data-live-spectrum="0"/); assert.match(html, /Host star records/); assert.match(html, /Faedi 2011/);
  assert.match(html, /Planetary Systems rows/);
  assert.deepEqual(summarize(result).rows['nasa-ps'], {status: 'ok', count: 3, latest: '2026-09-30'});
  // without a relay: relay sources say so, SIMBAD still answers
  const offline = await load(object, records, {relay: '', fetcher});
  const off = Object.fromEntries(offline.results.map((r) => [r.key, r]));
  assert.equal(off['nasa-ps'].status, 'no-relay'); assert.match(off['nasa-ps'].note, /relay/);
  assert.equal(off.simbad.status, 'ok');
}
// rows and memory
let html = rowHtml(byKey('nasa-ps'), null, base, null);
assert.match(html, /not checked yet/); assert.match(html, /release holds 2 \(latest 2026-01-29\)/);
html = rowHtml(byKey('simbad'), null, base, null);
assert.match(html, /live-only: not in the release/);
html = rowHtml(byKey('nasa-ps'), {key: 'nasa-ps', status: 'ok', count: 1, unit: ['published row', 'published rows'], fetchedAt: '2026-10-17T12:00:00.000Z', comparison: {state: 'fewer', text: 'release holds more (2 retained, 1 answered now)'}, link: 'https://x.example/'}, base, null);
assert.match(html, /1 published row</); assert.match(html, /fetched <time datetime="2026-10-17T12:00:00.000Z">12:00:00 UTC/); assert.match(html, /live-same/);
html = rowHtml(byKey('nasa-ps'), {key: 'nasa-ps', status: 'ok', count: 3, unit: ['published row', 'published rows'], fetchedAt: '2026-10-17T12:00:00.000Z', comparison: {state: 'new', text: '3 upstream vs 2 retained (+1)'}}, base, {at: '2026-10-01T00:00:00Z', rows: {'nasa-ps': {status: 'ok', count: 2, latest: null}}});
assert.match(html, /<mark class="live-new">New since the release: 3 upstream vs 2 retained \(\+1\)<\/mark>/);
assert.match(html, /<mark class="live-changed">changed since you last looked \(2026-10-01\): was 2, now 3<\/mark>/);
assert.equal(changeSinceSeen({key: 'a', status: 'ok', count: 2}, {at: '2026-10-01', rows: {a: {status: 'ok', count: 2, latest: null}}}), '');
assert.match(changeSinceSeen({key: 'a', status: 'ok', count: 2, latest: '2026-10-09'}, {at: '2026-10-01', rows: {a: {status: 'ok', count: 2, latest: '2026-10-05'}}}), /updated since you last looked/);
assert.equal(summaryText({status: 'link', note: 'x'}), 'Link only — x');
assert.equal(summaryText({status: 'ok', count: 2, unit: ['spectrum', 'spectra']}), '2 spectra');
const ctx = makeContext('', false, async () => new Response('{}'));
assert.equal((await runSource(byKey('nasa-ps'), identifiers(object, records), ctx, object)).status, 'no-relay');
console.log('WorldsIndex live sources: all checks passed');

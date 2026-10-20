#!/usr/bin/env node
// Unit tests for the WorldsIndex Track 1 part D front-end helpers (worldsindex/assets/sky.js,
// worldsindex/assets/status.js) and the static builder's source-contributions acceptance
// (scripts/worldsindex_contributions.mjs). Pure functions only; the browser behaviour is in
// scripts/test_worldsindex_sky_browser.py. Run: node --test scripts/test_worldsindex_front.mjs
import assert from 'node:assert/strict';
import test from 'node:test';

import * as sky from '../worldsindex/assets/sky.js';
import * as status from '../worldsindex/assets/status.js';
import { acceptSourceContributions, CONTRIBUTIONS_SCHEMA_VERSIONS, describeSourceContributions } from './worldsindex_contributions.mjs';

const hd = { objectId: 'object-hd-209458-b', name: 'HD 209458 b', hostName: 'HD 209458', raDeg: 330.7950219, decDeg: 18.8842419, normalizedStatus: 'CONFIRMED', methodCode: 'primary-transit-photometry', discoveryYear: 1999,
  population: { orbitalPeriodDays: { value: 3.52474859, unit: 'days', sourceId: 'nasa-pscomppars', sourceRecordId: 'source-pscomppars-000001-abc', limitType: 'NONE' }, massEarth: { value: 219, unit: 'M⊕', sourceId: 'nasa-ps', sourceRecordId: 'source-ps-000002-def', limitType: 'LOWER_LIMIT' } } };
const records = [
  { sourceId: 'nasa-ps', sourceRecordId: 'source-ps-000002-def', recordType: 'published solution', reference: { label: 'Bonomo et al. 2017', href: null }, values: { pl_orbper: 3.52474859, pl_rade: 15.1, pl_bmasse: null, pl_orbeccen: '' } },
  { sourceId: 'nasa-stellar-hosts', sourceRecordId: 'nasa-stellar-hosts:stellarhosts:000010-abcdef', recordType: 'stellar host solution', link: 'EXACT_HOST_IDENTIFIER', via: { value: 'HIP 108859', sourceId: 'nasa-pscomppars', table: 'pscomppars', column: 'hip_name', link: 'SAME_RECORD' }, values: { st_teff: 6065, 'st_met': '0.00' } },
];
const manifest = { atlasSha256: 'a'.repeat(64), generatedAt: '2026-10-20T05:00:00.000Z', atlasGeneratedAt: '2026-10-17T13:23:33.206Z' };

test('coordinates: only finite in-range RA/Dec count; the nine coordinate-less objects get null', () => {
  assert.deepEqual(sky.coordinatesOf(hd), { ra: 330.7950219, dec: 18.8842419 });
  assert.equal(sky.coordinatesOf({ raDeg: null, decDeg: null }), null);
  assert.equal(sky.coordinatesOf({ raDeg: 360, decDeg: 0 }), null);
  assert.equal(sky.coordinatesOf({ raDeg: 10, decDeg: -91 }), null);
  assert.equal(sky.coordinatesOf(null), null);
  assert.equal(sky.sexagesimal(330.7950219, 18.8842419), '22:03:10.81 +18:53:03.3');
  assert.equal(sky.sexagesimal(0, -0.5), '00:00:00.00 −00:30:00.0');
});

test('nearby objects: within 5′ by great-circle separation, nearest first, the object itself excluded, same-host flagged', () => {
  const center = { ra: 346.6263919, dec: -5.0434618 };
  const objects = [
    { objectId: 'b', name: 'TRAPPIST-1 b', hostName: 'TRAPPIST-1', raDeg: 346.6263919, decDeg: -5.0434618 },
    { objectId: 'c', name: 'TRAPPIST-1 c', hostName: 'TRAPPIST-1', raDeg: 346.6263919, decDeg: -5.0434618 },
    { objectId: 'near', name: 'Other b', hostName: 'Other', raDeg: 346.6263919 + 0.04 / Math.cos(-5.0434618 * Math.PI / 180), decDeg: -5.0434618 },
    { objectId: 'far', name: 'Far b', hostName: 'Far', raDeg: 346.8, decDeg: -5.0434618 },
    { objectId: 'pole', name: 'Pole b', hostName: 'Pole', raDeg: 100, decDeg: 89.99 },
    { objectId: 'none', name: 'No coords', hostName: 'X', raDeg: null, decDeg: null },
  ];
  const found = sky.nearbyObjects(center, objects, 5, { excludeId: 'b', hostName: 'TRAPPIST-1' });
  assert.deepEqual(found.map((item) => [item.object.objectId, item.sameHost, Number(item.separationArcmin.toFixed(2))]), [['c', true, 0], ['near', false, 2.4]]);
  assert.equal(sky.separationArcmin({ ra: 0, dec: 0 }, { ra: 1, dec: 0 }).toFixed(3), '60.000');
  assert.equal(Math.round(sky.separationArcmin({ ra: 0, dec: 89 }, { ra: 180, dec: 89 })), 120, 'across the pole, not along the RA circle');
});

test('finder chart: the declared image sources in order, PS1 only north of Dec −30, URLs carry the field, scale bars fit a quarter of the field', () => {
  const north = sky.finderSources(10, 20, 2, () => Promise.reject(new Error('no network')));
  assert.deepEqual(north.map((source) => source.id), ['ls-dr10', 'ls-dr9', 'ps1', 'dss2']);
  const south = sky.finderSources(10, -45, 10);
  assert.deepEqual(south.map((source) => source.id), ['ls-dr10', 'ls-dr9', 'dss2']);
  assert.equal(sky.legacyCutoutUrl(10, 20, 2, 'ls-dr10'), 'https://www.legacysurvey.org/viewer/jpeg-cutout?ra=10.000000&dec=20.000000&layer=ls-dr10&pixscale=0.2344&width=512&height=512');
  assert.match(sky.hips2fitsUrl(10, 20, 10, 'CDS/P/DSS2/color'), /hips2fits\?hips=CDS%2FP%2FDSS2%2Fcolor&width=512&height=512&fov=0\.166667&projection=TAN&coordsys=icrs&ra=10\.000000&dec=20\.000000&format=jpg$/);
  const listing = 'projcell,subcell,ra,dec,filter,mjd,type,filename,shortname\n1,2,10,20,g,5,stack,rings.v3.skycell/1/2/g.fits,g\n1,2,10,20,i,5,stack,rings.v3.skycell/1/2/i.fits,i\n1,2,10,20,y,5,stack,rings.v3.skycell/1/2/y.fits,y\n';
  assert.match(sky.ps1CutoutUrl(10, 20, 2, listing), /fitscut\.cgi\?red=rings\.v3\.skycell\/1\/2\/y\.fits&green=rings\.v3\.skycell\/1\/2\/i\.fits&blue=rings\.v3\.skycell\/1\/2\/g\.fits&format=jpeg&size=480&/);
  assert.equal(sky.ps1CutoutUrl(10, 20, 2, 'projcell\n'), null);
  assert.deepEqual([sky.scaleBar(2), sky.scaleBar(10)], [{ arcsec: 30, label: '30″' }, { arcsec: 120, label: '2′' }]);
  const svg = sky.finderOverlaySvg(2);
  assert.match(svg, /<text [^>]*>N<\/text>/);
  assert.match(svg, /<text [^>]*>E<\/text>/);
  assert.match(svg, />30″</);
  const html = sky.finderPanelHtml(hd, sky.coordinatesOf(hd));
  assert.equal((html.match(/data-finder-field="(\d+)"/g) || []).length, 2);
  assert.match(html, /epoch J2000/);
  const printed = sky.printDocument(hd, sky.coordinatesOf(hd), [{ fov: 2, url: 'https://example.test/a.jpg', label: 'Legacy Surveys DR9' }], manifest.atlasSha256);
  assert.match(printed, /<title>Finder chart: HD 209458 b<\/title>/);
  assert.match(printed, /Stable object id object-hd-209458-b/);
  assert.match(printed, /release aaaaaaaaaaaaaaaa…/);
});

test('SAMP: XML-RPC envelopes escape values, and the private key is read from the register response', () => {
  const envelope = sky.sampEnvelope('samp.hub.notifyAll', ['key-1', { 'samp.mtype': 'coord.pointAt.sky', 'samp.params': { ra: '1.5', dec: '-2 <x>' } }]);
  assert.match(envelope, /^<\?xml version="1\.0"\?><methodCall><methodName>samp\.hub\.notifyAll<\/methodName>/);
  assert.match(envelope, /<member><name>samp\.mtype<\/name><value><string>coord\.pointAt\.sky<\/string><\/value><\/member>/);
  assert.match(envelope, /<string>-2 &lt;x&gt;<\/string>/);
  assert.equal(sky.SAMP_HUB_URL, 'http://localhost:21012/');
  assert.match(sky.SAMP_NOTE, /Chrome, Firefox and Edge/);
  assert.match(sky.SAMP_NOTE, /not work in Safari/);
});

test("the object's VOTable: one row per release selection and per non-empty native value, as the sources state them", () => {
  const rows = sky.objectVotableRows(hd, records);
  assert.deepEqual(rows.map((row) => [row.record_type, row.column, row.value, row.unit, row.limit_type]), [
    ['release selection', 'orbitalPeriodDays', '3.52474859', 'days', 'NONE'], ['release selection', 'massEarth', '219', 'M⊕', 'LOWER_LIMIT'],
    ['published solution', 'pl_orbper', '3.52474859', '', ''], ['published solution', 'pl_rade', '15.1', '', ''],
    ['stellar host solution', 'st_teff', '6065', '', ''], ['stellar host solution', 'st_met', '0.00', '', ''],
  ]);
  assert.equal(rows[2].reference, 'Bonomo et al. 2017');
  const votable = sky.objectVotable(hd, records, manifest);
  assert.match(votable, /<VOTABLE version="1\.4"/);
  assert.match(votable, /<TABLE name="worldsindex_object-hd-209458-b" nrows="6">/);
  assert.equal((votable.match(/<FIELD /g) || []).length, 11);
  assert.equal((votable.match(/<TR>/g) || []).length, 6);
  assert.match(votable, /atlas SHA-256 aaaaaaaa/);
  assert.match(votable, /Not a merged or averaged table/);
  assert.equal(sky.objectVotable({ objectId: 'x', name: 'X' }, []).match(/nrows="(\d+)"/)[1], '0');
  const dataUrl = sky.votableDataUrl(votable);
  assert.match(dataUrl, /^data:application\/x-votable\+xml;base64,/);
  assert.equal(Buffer.from(dataUrl.split(',')[1], 'base64').toString('utf8'), votable);
});

test('report a problem: a prefilled issue on the website repository with the data-problem label, the stable id, the projection, the checksum and the panel', () => {
  const url = new URL(sky.reportProblemUrl(hd, { projectionId: 'sky-4beebdb335a3be47a24e', manifest, panel: 'Measurements', pageUrl: 'https://jackmcguireastro.github.io/worldsindex/?object=object-hd-209458-b&section=object' }));
  assert.equal(url.origin + url.pathname, 'https://github.com/JackMcGuireAstro/JackMcGuireAstro.github.io/issues/new');
  assert.equal(url.searchParams.get('title'), 'HD 209458 b (object-hd-209458-b)');
  assert.equal(url.searchParams.get('labels'), 'data-problem');
  const body = url.searchParams.get('body');
  for (const line of ['Stable object id: object-hd-209458-b', 'Atlas projection id: sky-4beebdb335a3be47a24e', `Release checksum (atlas SHA-256): ${'a'.repeat(64)}`, 'Panel: Measurements', 'Dossier: https://jackmcguireastro.github.io/worldsindex/?object=object-hd-209458-b&section=object']) assert.ok(body.includes(line), line);
  assert.ok(sky.reportProblemUrl({ objectId: 'x', name: 'X' }).includes('Release+checksum+%28atlas+SHA-256%29%3A+unknown') || sky.reportProblemUrl({ objectId: 'x', name: 'X' }).includes(encodeURIComponent('Release checksum (atlas SHA-256): unknown')));
});

test('status page helpers: denominators in words, attempt labels, quarantine rows with the provider text, identity rows, cloud-run marker', () => {
  const coverage = { generatedAt: '2026-10-20T04:58:37.833Z', objects: 31326, denominators: { sources: { declared: 42, applicable: 42, executed: 15, executedUnknown: 15, notExecuted: 12, dataBearing: 15 } },
    sources: { vizier: { sourceId: 'vizier', class: 'FROZEN_CONTEXT', state: 'NO_FROZEN_SNAPSHOT', eligible: 27297, attached: 0, missingAtProvider: 0, blocked: 27297, blockedReason: 'no frozen snapshot yet; last retrieval attempt 2026-10-20T03:58Z FAILED TIMEOUT', attachedRows: 0,
      denominators: { declared: true, applicable: true, executed: true, dataBearing: false, objects: { declared: 31326, applicable: 27297, executed: 0, dataBearing: 0 }, notComputable: null },
      lastAttempt: { at: '2026-10-20T03:58:00.000Z', outcome: 'FAILED', category: 'TIMEOUT', detail: 'J/A+A/656/A53: The operation was aborted due to timeout (no active snapshot; first retrieval)' } },
      mast: { sourceId: 'mast', class: 'LIVE_PER_TARGET', state: 'LIVE_QUERY', eligible: 32880, attached: 0, missingAtProvider: 0, blocked: 32880, attachedRows: 0, denominators: { declared: true, applicable: true, executed: null, dataBearing: false, objects: { applicable: 32880, executed: null, dataBearing: 0 }, notComputable: 'executed: live per-target source' } } } };
  assert.match(status.denominatorSummary(coverage), /^42 declared sources; 42 applicable[^;]*; 15 executed \(a bounded query, snapshot lookup or retrieval attempt happened for this release\), 15 not computable[^,]*, 12 not executed; 15 data-bearing/);
  assert.match(status.denominatorSummary({}), /no denominators block/);
  assert.deepEqual(status.attemptLabel(coverage.sources.vizier.lastAttempt), { text: 'FAILED TIMEOUT: J/A+A/656/A53: The operation was aborted due to timeout (no active snapshot; first retrieval)', at: '2026-10-20T03:58:00.000Z', state: 'bad' });
  assert.deepEqual(status.attemptLabel(null), { text: 'no attempt recorded for this release', state: 'none' });
  assert.equal(status.attemptLabel({ outcome: 'SKIPPED_SOURCE_NOT_AVAILABLE', detail: 'monitor state QUERY_TIMEOUT' }).text, 'SKIPPED SOURCE NOT AVAILABLE: monitor state QUERY_TIMEOUT');
  const rows = status.sourceRows(coverage, { sources: { entries: [{ id: 'vizier', name: 'VizieR / CDS' }] } });
  assert.deepEqual(rows.map((row) => [row.id, row.name, row.klass, row.executed, row.dataBearing, row.objectsExecuted, row.attempt.state]), [['mast', 'mast', 'live per target (browser)', 'not computable', 'no', null, 'none'], ['vizier', 'VizieR / CDS', 'frozen context snapshot', 'yes', 'no', 0, 'bad']]);
  const quarantined = { state: 'QUARANTINED', completedAt: '2026-10-19T01:00:00Z', lastGoodCompletedAt: '2026-10-18T23:00:00Z', summary: { checked: 17, available: 16, failed: 1, changed: 0 },
    latestAttempt: { runId: 'sync-x', observations: [{ sourceId: 'nasa-ps', required: true, state: 'QUERY_FAILED', httpStatus: 500, error: 'Failed to connect to cx_Oracle', errorCategory: 'PROVIDER_ERROR' }, { sourceId: 'simbad', required: false, state: 'QUERY_TIMEOUT' }, { sourceId: 'gaia', required: true, state: 'AVAILABLE' }] } };
  assert.deepEqual(status.quarantineRows(quarantined).map((row) => [row.sourceId, row.state, row.httpStatus, row.error]), [['nasa-ps', 'QUERY_FAILED', 500, 'Failed to connect to cx_Oracle']]);
  const summary = status.monitorSummary(quarantined);
  assert.equal(summary.state, 'bad');
  assert.match(summary.text, /QUARANTINED: required check nasa-ps \(QUERY_FAILED, HTTP 500: Failed to connect to cx_Oracle\)\. Every catalog measurement shown on the site is from the last validated release/);
  assert.equal(status.monitorSummary({ state: 'VALIDATED_UNCHANGED', summary: { checked: 17, available: 17, failed: 0 } }).state, 'ok');
  assert.match(status.monitorSummary(null).text, /No source-monitor\.json/);
  assert.deepEqual(status.identityRows({ coverage: { identityCounts: { UNREVIEWED: 24878, CANONICAL: 6354, AMBIGUOUS: 94 } } }).map((row) => [row.state, row.count]), [['AMBIGUOUS', 94], ['CANONICAL', 6354], ['UNREVIEWED', 24878]]);
  assert.match(status.cloudRunSummary({ workflow: 'worldsindex-cloud', run_id: '123', generated_at: '2026-10-20T05:00:00Z', state_asset: 'wi-state-20261020T0400Z.tar.gz', token_status: 'ok', token_expires_at: '2026-11-15' }), /^Published by worldsindex-cloud run 123 at 2026-10-20T05:00:00Z; working state restored from wi-state-20261020T0400Z\.tar\.gz; state key ok \(expires 2026-11-15\)\.$/);
  assert.match(status.cloudRunSummary(null), /No cloud-run marker/);
});

test('source contributions: v1 and v2 are accepted for the matching projection, anything else is refused whole with a reason, and the log line counts v2 rows', () => {
  assert.deepEqual(CONTRIBUTIONS_SCHEMA_VERSIONS, ['worldsindex-source-contributions.v1', 'worldsindex-source-contributions.v2']);
  const v2 = { schemaName: 'worldsindex-source-contributions', schemaVersion: 'worldsindex-source-contributions.v2', generatedAt: '2026-10-19T22:41:00.000Z', atlasProjectionId: 'sky-4beebdb335a3be47a24e', boundary: 'context only',
    sources: { 'nasa-stellar-hosts': { sourceId: 'nasa-stellar-hosts', state: 'FROZEN_SNAPSHOT', snapshotId: 'nasa-stellar-hosts-af998a188295e158-v1', rows: 47929, attachedRows: 2, objects: 1, links: { EXACT_HOST_NAME: 1, EXACT_HOST_IDENTIFIER: 1 }, viaCrossIdentifier: 1, note: '' },
      'nasa-atmospheric-spectroscopy': { sourceId: 'nasa-atmospheric-spectroscopy', state: 'FROZEN_SNAPSHOT', snapshotId: 'nasa-atmospheric-spectroscopy-x-v1', rows: 10, attachedRows: 1, objects: 1, note: '' },
      vizier: { sourceId: 'vizier', state: 'NO_FROZEN_SNAPSHOT', snapshotId: null, rows: 0, attachedRows: 0, objects: 0, note: 'no snapshot' } },
    objects: { 'object-hd-209458-b': { aliases: [{ value: 'HIP 108859', kind: 'HOST', sourceId: 'nasa-pscomppars', table: 'pscomppars', column: 'hip_name', sourceRecordRef: 'pscomppars:1', link: 'SAME_RECORD' }],
      records: [records[1], { ...records[1], sourceRecordId: 'nasa-stellar-hosts:stellarhosts:000011-abcdef', link: 'EXACT_HOST_NAME', via: undefined }, { sourceId: 'nasa-atmospheric-spectroscopy', sourceRecordId: 'nasa-atmospheric-spectroscopy:spectra:000001-a', recordType: 'atmospheric spectrum', link: 'EXACT_PLANET_NAME', values: { instrument: 'HST/WFC3' } }] } } };
  const accepted = acceptSourceContributions(v2, 'sky-4beebdb335a3be47a24e');
  assert.equal(accepted.contributions, v2);
  assert.equal(accepted.reason, null);
  assert.equal(describeSourceContributions(v2), 'worldsindex-source-contributions.v2 (1 objects with context, 3 rows, 1 through a stated cross-identifier, 1 spectrum rows; frozen sources: nasa-stellar-hosts, nasa-atmospheric-spectroscopy)');
  const v1 = { ...v2, schemaVersion: 'worldsindex-source-contributions.v1' };
  assert.equal(acceptSourceContributions(v1, 'sky-4beebdb335a3be47a24e').contributions, v1);
  assert.deepEqual(acceptSourceContributions({ ...v2, schemaVersion: 'worldsindex-source-contributions.v3' }, 'sky-4beebdb335a3be47a24e'), { contributions: null, reason: 'schema worldsindex-source-contributions.v3 is not one of worldsindex-source-contributions.v1, worldsindex-source-contributions.v2' });
  assert.deepEqual(acceptSourceContributions(v2, 'sky-other'), { contributions: null, reason: 'built for atlas projection sky-4beebdb335a3be47a24e, this release is sky-other' });
  assert.deepEqual(acceptSourceContributions({ schemaVersion: 'worldsindex-source-contributions.v2', atlasProjectionId: 'p' }, 'p'), { contributions: null, reason: 'objects or sources block missing' });
  assert.deepEqual(acceptSourceContributions(null, 'p'), { contributions: null, reason: null });
});

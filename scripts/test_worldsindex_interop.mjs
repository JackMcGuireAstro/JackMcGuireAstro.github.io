#!/usr/bin/env node
// Unit tests for the WorldsIndex interoperability modules (no built release needed):
// the column registry, the ECSV/VOTable writers, HEALPix indexing, the release diff,
// the feeds and the PROV-JSON document. The Astropy/pyarrow round trip of a built
// release is scripts/test_worldsindex_interop.py.
import assert from 'node:assert/strict';
import test from 'node:test';

import * as interop from './worldsindex_interop.mjs';
import { atomFeed, diffReleases, feedItemsFor, jsonFeed, parseEcsv, renderChangelogMarkdown, FEED_ITEM_CAP } from './worldsindex_changelog.mjs';
import { provenanceDocument } from './worldsindex_provenance.mjs';

test('every registry column has a snake_case name, a supported datatype, a UCD and a description; VOUnits strings are the agreed renderings', () => {
  for (const [table, specs] of Object.entries(interop.COLUMN_REGISTRY)) {
    const names = specs.map((spec) => spec.name);
    assert.equal(new Set(names).size, names.length, `${table}: duplicate column`);
    interop.assertRegistered(table, names);
    assert.ok(specs.some((spec) => spec.name.endsWith('_id') || spec.name === 'reference_key'), `${table}: an identifier column`);
  }
  assert.deepEqual(interop.VOUNIT, { day: 'd', hour: 'h', kelvin: 'K', degree: 'deg', au: 'AU', solRad: 'solRad', earthRadius: '6378100m', jupiterRadius: '71492000m', earthMass: '5.9721679e+24kg', jupiterMass: '1.8981246e+27kg' });
  assert.throws(() => interop.assertRegistered('objects', ['object_id']), /registered but not emitted/);
  assert.throws(() => interop.assertRegistered('references', [...interop.COLUMN_REGISTRY.references.map((s) => s.name), 'surprise']), /unregistered: surprise/);
  assert.throws(() => interop.assertRegistered('nope', []), /no column registry/);
});

test('HEALPix order-8 nested indices agree with astropy_healpix reference values and are null without coordinates', () => {
  const cases = [[0, 0, 311296], [0, 90, 65535], [0, -90, 524288], [359.999, 45, 218965], [180, -41.8, 393216], [90, 41.81, 393215], [22.0, -5.5, 281850], [266.4, -28.9, 461282]];
  for (const [ra, dec, expected] of cases) assert.equal(interop.healpixNested(ra, dec), expected, `${ra} ${dec}`);
  assert.equal(interop.healpixNested(null, 10), null);
  assert.equal(interop.healpixNested(10, NaN), null);
  assert.equal(interop.healpixNested(-10, 10), interop.healpixNested(350, 10), 'negative RA wraps');
});

const refSpecs = interop.COLUMN_REGISTRY.references;
const refRows = [
  { reference_key: 'k1', source_id: 'nasa-ps', label: 'Smith, J. "et al." 2020, A&A, 1', url: 'https://x.example/1', record_count: 3, object_count: 2, example_object_ids: 'a b' },
  { reference_key: 'k2', source_id: 'exoplanet-eu', label: ' leading space, comma', url: null, record_count: 1, object_count: 1, example_object_ids: 'c' },
];
const refNames = refSpecs.map((spec) => spec.name);
const refCells = refRows.map((row) => refNames.map((name) => row[name]));

test('ECSV 1.0: header per column with unit, UCD and description, comma delimiter, RFC 4180 quoting, empty cells for absent values; the file parses back', () => {
  const { specs, rows } = interop.materialize('references', refCells, refNames);
  const text = interop.ecsv('references', specs, rows, { extra: 'yes' });
  const lines = text.split('\n');
  assert.equal(lines[0], '# %ECSV 1.0');
  assert.equal(lines[1], '# ---');
  assert.equal(lines[2], '# datatype:');
  assert.match(text, /# - \{"name":"reference_key","datatype":"string","description":"[^"]+","meta":\{"ucd":"meta.id;meta.main"\}\}/);
  assert.match(text, /# meta: \{.*"extra":"yes".*\}/);
  assert.match(text, /# delimiter: ','/);
  const header = lines.findIndex((line) => line === refNames.join(','));
  assert.ok(header > 0);
  assert.equal(lines[header + 1], 'k1,nasa-ps,"Smith, J. ""et al."" 2020, A&A, 1",https://x.example/1,3,2,a b');
  assert.equal(lines[header + 2], 'k2,exoplanet-eu," leading space, comma",,1,1,c');
  const parsed = parseEcsv(text);
  assert.equal(parsed.length, 2);
  assert.equal(parsed[0].label, 'Smith, J. "et al." 2020, A&A, 1');
  assert.equal(parsed[1].url, '');
  // A unit column carries its VOUnits string; the ECSV header names it.
  const objectsText = interop.ecsv('objects', interop.COLUMN_REGISTRY.objects, [], {});
  assert.match(objectsText, /"name":"ra_deg","datatype":"float64","unit":"deg"/);
  assert.match(objectsText, /"name":"radius_earth","datatype":"float64","unit":"6378100m"/);
});

test('VOTable 1.5: namespace v1.3, COOSYS ICRS referenced by the coordinate FIELDs, UCD and unit per FIELD, empty TD for absent values, booleans as true/false', () => {
  const { specs, rows } = interop.materialize('transit_ephemerides', [['o1', 'Planet b', 'Host', 10.5, -20.25, interop.healpixNested(10.5, -20.25), 'CONFIRMED', 3.5, null, 2459000.5, 0.001, null, 1.2, 10.1, 'V', 'BJD_TDB', 'nasa-ps', 'ps', 'source-ps-1', null, null, null]], interop.COLUMN_REGISTRY.transit_ephemerides.map((s) => s.name));
  const xml = interop.votable('transit_ephemerides', specs, rows, { atlas_projection_id: 'sky-test' });
  assert.match(xml, /<VOTABLE version="1.5" xmlns="http:\/\/www.ivoa.net\/xml\/VOTable\/v1.3"/);
  assert.match(xml, /<COOSYS ID="ICRS" system="ICRS"\/>/);
  assert.match(xml, /<FIELD name="ra_deg" datatype="double" ucd="pos.eq.ra;meta.main" unit="deg" ref="ICRS">/);
  assert.match(xml, /<FIELD name="object_id" datatype="char" arraysize="\*" ucd="meta.id;meta.main">/);
  assert.match(xml, /<PARAM name="atlas_projection_id" datatype="char" arraysize="\*" value="sky-test"\/>/);
  assert.match(xml, /<TABLE name="transit_ephemerides" nrows="1">/);
  assert.match(xml, /<TR><TD>o1<\/TD><TD>Planet b<\/TD><TD>Host<\/TD><TD>10.5<\/TD><TD>-20.25<\/TD><TD>\d+<\/TD><TD>CONFIRMED<\/TD><TD>3.5<\/TD><TD\/><TD>2459000.5<\/TD>/);
  assert.match(xml, /<TD\/><TD\/><TD\/><\/TR>/, 'trailing absent reference, url and matched_via');
  const covXml = interop.votable('source_coverage', interop.COLUMN_REGISTRY.source_coverage, [interop.COLUMN_REGISTRY.source_coverage.map((spec) => (spec.datatype === 'bool' ? true : spec.datatype === 'string' ? 'x' : 1))]);
  assert.match(covXml, /<TD>true<\/TD>/);
  assert.doesNotMatch(covXml, /COOSYS/, 'a table without coordinates has no COOSYS');
  assert.equal(interop.xmlEscape('a<b&"c"'), 'a&lt;b&amp;&quot;c&quot;');
});

test('cells are coerced to their datatype and absent values are null; rows sort by HEALPix then the tie-break', () => {
  const spec = { name: 'x', datatype: 'int64' };
  assert.equal(interop.coerce(spec, '12'), 12);
  assert.equal(interop.coerce(spec, ''), null);
  assert.equal(interop.coerce(spec, undefined), null);
  assert.throws(() => interop.coerce(spec, 1.5), /not an integer/);
  assert.throws(() => interop.coerce({ name: 'b', datatype: 'bool' }, 'true'), /not a boolean/);
  assert.equal(interop.coerce({ name: 's', datatype: 'string' }, 7), '7');
  assert.equal(interop.coerce({ name: 'f', datatype: 'float64' }, NaN), null);
  const specs = [{ name: 'healpix_8_nested', datatype: 'int64' }, { name: 'object_id', datatype: 'string' }];
  const sorted = interop.sortByHealpix(specs, [[5, 'b'], [null, 'z'], [5, 'a'], [1, 'q']], ['object_id']);
  assert.deepEqual(sorted, [[1, 'q'], [5, 'a'], [5, 'b'], [null, 'z']]);
});

test('the objects, measurements, identity and references tables are built from the catalog and detail records with limits and provenance kept', () => {
  const detection = { objectId: 'object-x-b', name: 'X b', hostName: 'X', systemId: 'system-x', raDeg: 10, decDeg: 10, normalizedStatus: 'CONFIRMED', methodCode: 'radial-velocity', identityState: 'CANONICAL', primarySourceId: 'nasa-ps', sourceIds: ['nasa-ps', 'exoplanet-eu'], sourceObjectIds: ['object-x-b'], sourceRecordCount: 2, discoveryYear: 2001, discoveryFacility: 'Keck',
    population: { massEarth: { value: 300, unit: 'M_earth', propertyCode: 'planet_mass_best', sourceId: 'nasa-ps', sourceRecordId: 'source-ps-1', limitType: 'LOWER_LIMIT' } } };
  const catalog = [{ ...detection, methodCodes: ['radial-velocity'], aliases: ['HIP 1', 'HD 2'] }];
  const detailsById = new Map([['object-x-b', { records: [
    { sourceId: 'nasa-ps', sourceRecordId: 'source-ps-1', recordType: 'published solution', name: 'X b', hostName: 'X', reference: { label: 'Doe 2001', href: 'https://ui.adsabs.harvard.edu/abs/2001' }, updated: '2020-01-01', published: '2001-05', isSourceDefault: true,
      values: { pl_orbper: 3.5, pl_orbpererr1: 0.01, pl_orbpererr2: -0.02, pl_orbperlim: 0, pl_bmasse: 300, pl_bmasseerr1: null, pl_bmasseerr2: null, pl_bmasselim: 1, pl_bmassprov: 'Msini', pl_rade: '', pl_orbeccen: 0.1, pl_orbeccenlim: -1 } },
    { sourceId: 'nasa-pscomppars', sourceRecordId: 'source-pscomppars-1', recordType: 'source composite', name: 'X b', hostName: 'X', references: { pl_orbper: { label: 'Roe 2010', href: null } }, values: { pl_orbper: 3.49, pl_orbpererr1: 0.001, pl_orbpererr2: -0.001, pl_orbperlim: 0 } },
    { sourceId: 'exoplanet-eu', sourceRecordId: 'exoplanet-eu-x-b-1', recordType: 'catalog row', name: 'X b', values: { mass_sini: 0.95, mass_sini_error_max: 0.05, mass_sini_error_min: 0.04, radius: '', orbital_period: 3.5, publication: 'Doe 2001', updated: '2021-02-03' } },
    { sourceId: 'nasa-atmospheric-spectroscopy', sourceRecordId: 'spec-1', recordType: 'spectrum row', values: {} },
  ] }]]);
  const detectionsById = new Map([['object-x-b', detection]]);
  const objects = interop.objectsTable(catalog, detectionsById, new Map([['object-x-b', { records: 4, spectra: 1 }]]));
  const o = Object.fromEntries(objects.specs.map((spec, index) => [spec.name, objects.rows[0][index]]));
  assert.equal(o.mass_earth, 300); assert.equal(o.mass_earth_limit, 'LOWER_LIMIT'); assert.equal(o.mass_earth_source_record_id, 'source-ps-1');
  assert.equal(o.radius_earth, null); assert.equal(o.radius_earth_limit, null);
  assert.deepEqual([o.aliases, o.source_ids, o.detail_record_count, o.spectrum_record_count, o.healpix_8_nested], ['HIP 1 | HD 2', 'nasa-ps exoplanet-eu', 4, 1, interop.healpixNested(10, 10)]);
  const m = interop.measurementsTable(detailsById, detectionsById);
  const rows = m.rows.map((row) => Object.fromEntries(m.specs.map((spec, index) => [spec.name, row[index]])));
  const find = (source, field) => rows.find((row) => row.source_id === source && row.source_field === field);
  assert.equal(rows.length, 6, 'one row per retained numeric value (3 PS, 1 composite, 2 eu); empty pl_rade and radius are not rows; the spectrum source has no field map');
  assert.deepEqual([find('nasa-ps', 'pl_orbper').value, find('nasa-ps', 'pl_orbper').error_upper, find('nasa-ps', 'pl_orbper').error_lower, find('nasa-ps', 'pl_orbper').unit, find('nasa-ps', 'pl_orbper').limit_type, find('nasa-ps', 'pl_orbper').reference, find('nasa-ps', 'pl_orbper').reference_url, find('nasa-ps', 'pl_orbper').is_source_default, find('nasa-ps', 'pl_orbper').published], [3.5, 0.01, 0.02, 'd', 'NONE', 'Doe 2001', 'https://ui.adsabs.harvard.edu/abs/2001', true, '2001-05']);
  assert.deepEqual([find('nasa-ps', 'pl_bmasse').limit_type, find('nasa-ps', 'pl_bmasse').mass_definition, find('nasa-ps', 'pl_bmasse').error_upper, find('nasa-ps', 'pl_bmasse').unit], ['LOWER_LIMIT', 'MINIMUM_MASS_M_SIN_I', null, '5.9721679e+24kg']);
  assert.equal(find('nasa-ps', 'pl_orbeccen').limit_type, 'UPPER_LIMIT', 'NASA lim -1 is an upper limit');
  assert.deepEqual([find('nasa-pscomppars', 'pl_orbper').reference, find('nasa-pscomppars', 'pl_orbper').reference_url, find('nasa-pscomppars', 'pl_orbper').is_source_default], ['Roe 2010', null, null], 'composite references are per value');
  assert.deepEqual([find('exoplanet-eu', 'mass_sini').unit, find('exoplanet-eu', 'mass_sini').mass_definition, find('exoplanet-eu', 'mass_sini').error_upper, find('exoplanet-eu', 'mass_sini').error_lower, find('exoplanet-eu', 'mass_sini').reference, find('exoplanet-eu', 'mass_sini').updated, find('exoplanet-eu', 'mass_sini').limit_type], ['1.8981246e+27kg', 'MINIMUM_MASS_M_SIN_I', 0.05, 0.04, 'Doe 2001', '2021-02-03', null]);
  const identity = interop.identityStatesTable(catalog, 'worldsindex-exoplanet-eu-identity.v3');
  assert.deepEqual(identity.rows[0].slice(6), ['CANONICAL', 'system-x', 'nasa-ps', 'nasa-ps exoplanet-eu', 'object-x-b', 2, 'worldsindex-exoplanet-eu-identity.v3']);
  const references = interop.referencesTable(detailsById);
  const labels = references.rows.map((row) => [row[1], row[2], row[3], row[4], row[5]]);
  assert.deepEqual(labels, [['exoplanet-eu', 'Doe 2001', null, 1, 1], ['nasa-ps', 'Doe 2001', 'https://ui.adsabs.harvard.edu/abs/2001', 1, 1], ['nasa-pscomppars', 'Roe 2010', null, 1, 1]]);
  assert.throws(() => interop.transitEphemeridesTable({ columns: ['objectId'], rows: [] }), /columns changed/);
});

const object = (id, status, identity = 'CANONICAL') => ({ objectId: id, name: id.toUpperCase(), hostName: 'H', normalizedStatus: status, identityState: identity, primarySourceId: 'nasa-ps' });
const current = {
  manifest: { generatedAt: '2026-10-19T12:00:00.000Z', atlasGeneratedAt: '2026-10-19T11:00:00.000Z', objectCount: 4, detailRecordCount: 10 },
  catalogIndex: { projectionId: 'sky-b', objects: [object('a', 'CONFIRMED'), object('b', 'FALSE_POSITIVE'), object('c', 'CANDIDATE', 'AMBIGUOUS'), object('e', 'CANDIDATE')] },
  sourceCoverage: { sources: { exofop: { state: 'FROZEN_SNAPSHOT', snapshotId: 'exofop-1', attached: 5, blocked: 0, lastAttempt: { at: '2026-10-19T10:00:00Z', outcome: 'RETRIEVED' } }, vizier: { state: 'NO_FROZEN_SNAPSHOT', snapshotId: null, attached: 0, blocked: 9, lastAttempt: { at: '2026-10-19T10:00:00Z', outcome: 'REFUSED' } } } },
  sourceMonitor: { state: 'QUARANTINED', observations: [{ sourceId: 'nasa-ps', state: 'QUERY_FAILED', httpStatus: 503, error: 'maintenance' }, { sourceId: 'exoplanet-eu', state: 'AVAILABLE' }] },
  detailCounts: new Map([['a', { records: 5, spectra: 1 }], ['b', { records: 2, spectra: 0 }], ['c', { records: 2, spectra: 0 }], ['e', { records: 1, spectra: 0 }]]),
};
const previous = {
  ref: 'origin/worldsindex-data', commit: 'abc123', manifest: { generatedAt: '2026-10-18T12:00:00.000Z', objectCount: 4, detailRecordCount: 8 },
  catalogIndex: { projectionId: 'sky-a', objects: [object('a', 'CANDIDATE'), object('b', 'CANDIDATE'), object('c', 'CANDIDATE', 'UNREVIEWED'), object('d', 'CONFIRMED')] },
  sourceCoverage: { sources: { exofop: { state: 'NO_FROZEN_SNAPSHOT', snapshotId: null, attached: 0, blocked: 5, lastAttempt: { at: '2026-10-18T10:00:00Z', outcome: 'FAILED' } }, vizier: { state: 'NO_FROZEN_SNAPSHOT', snapshotId: null, attached: 0, blocked: 9, lastAttempt: { at: '2026-10-18T10:00:00Z', outcome: 'REFUSED' } } } },
  sourceMonitor: { state: 'VALIDATED_UNCHANGED', observations: [{ sourceId: 'nasa-ps', state: 'AVAILABLE' }, { sourceId: 'exoplanet-eu', state: 'AVAILABLE' }] },
  feed: { items: [{ id: 'urn:worldsindex:release:20261018T120000000Z:new:d', url: 'u', title: 'old', content_text: 't', date_published: '2026-10-18T12:00:00.000Z', tags: ['new'] }] },
  objectRows: [{ object_id: 'a', detail_record_count: '3', spectrum_record_count: '0' }, { object_id: 'b', detail_record_count: '2', spectrum_record_count: '0' }, { object_id: 'c', detail_record_count: '2', spectrum_record_count: '0' }],
};

test('the release diff names added, removed, promoted, demoted and re-identified objects, rows and spectra added, and source and monitor changes; it is deterministic and clock-free', () => {
  const changelog = diffReleases(current, previous);
  assert.equal(changelog.comparable, true);
  assert.deepEqual(changelog.summary, { added: 1, removed: 1, promotions: 1, demotions: 1, otherStatusChanges: 0, identityChanges: 1, objectsWithRowsAdded: 2, rowsAdded: 3, objectsWithSpectraAdded: 1, spectraAdded: 1, sourceChanges: 2, monitorChanges: 2 });
  assert.deepEqual(changelog.objects.added.entries.map((e) => e.objectId), ['e']);
  assert.deepEqual(changelog.objects.removed.entries.map((e) => e.objectId), ['d']);
  assert.deepEqual(changelog.objects.promotions.entries[0], { objectId: 'a', name: 'A', hostName: 'H', status: 'CONFIRMED', identityState: 'CANONICAL', primarySourceId: 'nasa-ps', from: 'CANDIDATE', to: 'CONFIRMED' });
  assert.deepEqual([changelog.objects.demotions.entries[0].objectId, changelog.objects.demotions.entries[0].to], ['b', 'FALSE_POSITIVE']);
  assert.deepEqual(changelog.objects.identityChanges.entries.map((e) => [e.objectId, e.from, e.to]), [['c', 'UNREVIEWED', 'AMBIGUOUS']]);
  assert.deepEqual(changelog.objects.rowsAdded.entries.map((e) => [e.objectId, e.from, e.to]), [['a', 3, 5], ['e', 0, 1]]);
  assert.deepEqual(changelog.sources.changes.entries.map((e) => [e.sourceId, e.kind]), [['exofop', 'snapshot-retrieved'], ['vizier', 'attempt-refused-or-failed']]);
  assert.match(changelog.sources.changes.entries[0].detail, /snapshot none -> exofop-1/);
  assert.deepEqual(changelog.sources.monitor.entries.map((e) => [e.sourceId, e.kind]), [[null, 'monitor-state'], ['nasa-ps', 'degraded']]);
  assert.equal(changelog.generatedAt, '2026-10-19T12:00:00.000Z');
  assert.deepEqual(diffReleases(current, previous), changelog, 'deterministic');
  const markdown = renderChangelogMarkdown(changelog);
  assert.match(markdown, /^# WorldsIndex release 2026-10-19T12:00:00.000Z/);
  assert.match(markdown, /1 object added; 1 object removed; 1 promotion to confirmed; 1 demotion to false positive or refuted/);
  assert.match(markdown, /## Promoted to confirmed \(1\)\n\n- A \(a\): CANDIDATE -> CONFIRMED/);
  assert.match(markdown, /## Source-state changes \(2\)/);
  // No base: nothing is listed, and it says why.
  const first = diffReleases(current, null);
  assert.equal(first.comparable, false);
  assert.deepEqual(first.summary.added, 0);
  assert.match(first.note, /No previous release/);
  assert.match(renderChangelogMarkdown(first), /Nothing is listed as changed/);
  // A previous release without an objects export: rows added are not computable, never guessed.
  const withoutExport = diffReleases(current, { ...previous, objectRows: null });
  assert.equal(withoutExport.objects.rowsAdded, null);
  assert.equal(withoutExport.summary.rowsAdded, null);
  assert.match(renderChangelogMarkdown(withoutExport), /native rows added: not computable/);
});

test('feeds: one summary item per release with changes plus one item per changed planet, the previous feed carried forward and capped; Atom mirrors JSON Feed', () => {
  const changelog = diffReleases(current, previous);
  const items = feedItemsFor(changelog);
  assert.deepEqual(items.map((item) => item.tags[0]), ['release', 'new', 'confirmed', 'demoted', 'updated', 'updated']);
  assert.equal(items[1].id, 'urn:worldsindex:release:20261019T120000000Z:new:e');
  assert.equal(items[1].url, 'https://jackmcguireastro.github.io/worldsindex/?object=e');
  assert.ok(items.every((item) => item.date_published === '2026-10-19T12:00:00.000Z'));
  const feed = jsonFeed(changelog, previous.feed);
  assert.equal(feed.version, 'https://jsonfeed.org/version/1.1');
  assert.equal(feed.items.length, 7);
  assert.equal(feed.items[6].title, 'old', 'previous items follow the new ones');
  assert.deepEqual(jsonFeed(changelog, previous.feed), feed, 'deterministic');
  const crowded = jsonFeed(changelog, { items: Array.from({ length: FEED_ITEM_CAP + 50 }, (_, i) => ({ id: `urn:worldsindex:release:old:${i}`, url: 'u', title: 't', content_text: 'c', date_published: '2026-01-01T00:00:00.000Z' })) });
  assert.equal(crowded.items.length, FEED_ITEM_CAP);
  assert.equal(jsonFeed(diffReleases(current, null), previous.feed).items.length, 1, 'an incomparable release adds nothing and keeps history');
  const atom = atomFeed(feed);
  assert.equal((atom.match(/<entry>/g) || []).length, 7);
  assert.match(atom, /<feed xmlns="http:\/\/www.w3.org\/2005\/Atom">/);
  assert.match(atom, /<category term="confirmed"\/>/);
  assert.match(atom, /<updated>2026-10-19T12:00:00.000Z<\/updated>/);
});

test('PROV-JSON names the release, artifacts, atlas, sources with contract and parser versions, identity rules and pins, the state asset and the activities', () => {
  const document = provenanceDocument({
    generatedAt: '2026-10-19T12:00:00.000Z', manifest: { schemaVersion: 'worldsindex-static-release.v1', objectCount: 4, detailRecordCount: 10 },
    artifacts: { 'manifest-like.json': { bytes: 1, sha256: 'a'.repeat(64) }, 'source-monitor.json': { bytes: 2, sha256: 'b'.repeat(64) } },
    atlas: { projectionId: 'sky-b', generatedAt: '2026-10-19T11:00:00.000Z', schemaVersion: '1.5.0', sourceSnapshots: [{ sourceId: 'nasa-ps+nasa-pscomppars', snapshotId: 'nasa-1', artifactSha256: 'c'.repeat(64) }] },
    registry: { sources: { version: 'v11', entries: [{ id: 'nasa-ps', name: 'NASA PS', authority: 'NASA', accessMode: 'HYBRID', state: 'INGESTED', adapterVersion: 'contract.v1', parserVersion: 'parser.v2', release: 'nasa-1', documentationUrl: 'https://d', rights: 'r', lastVerifiedAt: '2026-08-29T21:28:00Z' }, { id: 'vizier', name: 'VizieR', authority: 'CDS', accessMode: 'PER_TARGET_QUERY', state: 'LIVE_QUERY', adapterVersion: 'contract.v1', parserVersion: 'p', release: null, documentationUrl: 'https://v', rights: 'r', lastVerifiedAt: 'x' }] }, methods: { version: 'm1' } },
    sourceCoverage: current.sourceCoverage.sources ? { sources: { 'nasa-ps': { class: 'ATLAS_INGESTED', snapshotId: 'nasa-1', retrievedAt: null, attached: 4, attachedRows: 0 }, vizier: { class: 'FROZEN_CONTEXT', snapshotId: null, retrievedAt: null, attached: 0, attachedRows: 0, lastAttempt: { at: '2026-10-19T10:00:00Z', outcome: 'REFUSED', category: 'HTTP', detail: '403' } } } } : null,
    sourceMonitor: { runId: 'sync-1', startedAt: '2026-10-19T10:00:00Z', completedAt: '2026-10-19T10:01:00Z', state: 'VALIDATED_UNCHANGED', schemaVersion: 'worldsindex-public-sync-status.v2', observations: [{ sourceId: 'nasa-ps', state: 'AVAILABLE', checkedAt: '2026-10-19T10:00:30Z', artifact: { parserVersion: 'mon.v2' } }] },
    identity: { rulesVersion: 'identity.v3', contract: { reviewedAt: 'r', reviewedBy: 'policy', snapshotId: 'eu-1' }, pins: [{ fromRules: 'identity.v2', toRules: 'identity.v3', snapshotId: 'eu-1', transitionsSha256: 'd'.repeat(64), reviewedAt: 'r', reviewedBy: 'Jack' }] },
    builder: { commit: 'builder-sha', version: '0.5.0', name: 'ExoNexus' }, site: { commit: 'site-sha', repository: 'o/r' },
    stateAsset: { state_asset: 'state-2026-10-19.tar.zst', state_saved_at: 's', state_restored_at: 't', workflow: 'worldsindex-cloud.yml', run_url: 'https://run' },
    releaseAssets: [{ name: 'worldsindex-objects.parquet', bytes: 10, sha256: 'e'.repeat(64), downloadUrl: 'https://dl', format: 'VOParquet 1.0' }],
  });
  assert.equal(document.schemaVersion, 'worldsindex-provenance.v1');
  assert.equal(document.prefix.prov, 'http://www.w3.org/ns/prov#');
  const release = document.entity['wi:release/sky-b/2026-10-19T12:00:00.000Z'];
  assert.deepEqual([release['prov:type'], release['wi:objectCount'], release['prov:generatedAtTime']], ['wi:Release', 4, { $: '2026-10-19T12:00:00.000Z', type: 'xsd:dateTime' }]);
  assert.equal(document.entity['wi:artifact/source-monitor.json']['wi:sha256'], 'b'.repeat(64));
  const source = document.entity['wi:source/nasa-ps'];
  assert.deepEqual([source['wi:contractVersion'], source['wi:parserVersion'], source['wi:snapshotId'], source['wi:snapshotSha256'], source['wi:monitorState'], source['wi:monitorParserVersion']], ['contract.v1', 'parser.v2', 'nasa-1', 'c'.repeat(64), 'AVAILABLE', 'mon.v2']);
  assert.deepEqual([document.entity['wi:source/vizier']['wi:lastAttemptOutcome'], document.entity['wi:source/vizier']['wi:lastAttemptDetail']], ['REFUSED', '403']);
  assert.equal(document.entity['wi:identity-pin/identity.v3']['wi:transitionsSha256'], 'd'.repeat(64));
  assert.equal(document.entity['wi:state-asset/state-2026-10-19.tar.zst']['wi:workflow'], 'worldsindex-cloud.yml');
  assert.equal(document.entity['wi:release-asset/worldsindex-objects.parquet']['wi:url'], 'https://dl');
  assert.deepEqual(Object.keys(document.activity).sort(), ['wi:activity/atlas-generation/sky-b', 'wi:activity/source-monitor/sync-1', 'wi:activity/static-build/2026-10-19T12:00:00.000Z']);
  assert.equal(document.activity['wi:activity/static-build/2026-10-19T12:00:00.000Z']['wi:siteCommit'], 'site-sha');
  assert.equal(document.agent['wi:agent/exonexus-builder']['wi:commit'], 'builder-sha');
  assert.ok(Object.values(document.wasGeneratedBy).some((r) => r['prov:entity'] === 'wi:release/sky-b/2026-10-19T12:00:00.000Z'));
  assert.ok(Object.values(document.used).some((r) => r['prov:entity'] === 'wi:identity-rules/identity.v3'));
  assert.deepEqual(provenanceDocument(JSON.parse(JSON.stringify({ generatedAt: '2026-10-19T12:00:00.000Z', manifest: { schemaVersion: 'v', objectCount: 1, detailRecordCount: 1 }, artifacts: {}, atlas: { projectionId: 'p', generatedAt: 'g', schemaVersion: 's', sourceSnapshots: [] }, registry: { sources: { version: 'v', entries: [] }, methods: { version: 'm' } }, sourceCoverage: null, sourceMonitor: null, identity: { rulesVersion: 'r', contract: null, pins: [] }, builder: { commit: null, version: null, name: null }, site: { commit: null, repository: null }, stateAsset: null, releaseAssets: [] }))).activity['wi:activity/static-build/2026-10-19T12:00:00.000Z'], { 'prov:type': 'wi:StaticBuild', 'prov:label': 'WorldsIndex static release build (scripts/build_worldsindex_static.mjs)', 'prov:endedAtTime': { $: '2026-10-19T12:00:00.000Z', type: 'xsd:dateTime' } }, 'absent inputs leave no keys, never placeholders');
});

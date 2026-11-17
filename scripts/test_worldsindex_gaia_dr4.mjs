#!/usr/bin/env node
// Gaia DR4 on WorldsIndex (2026-11-17): the release accepts the builder's gaia-dr4.json only under
// its schema and only for objects the atlas holds; the object panel, the "New in Gaia DR4" list and
// the status line show Gaia values under Gaia names, call candidates candidates, list ambiguity
// without resolving it, and show nothing before the release. The fixture is the file the ExoNexus
// builder wrote from SYNTHETIC stand-in tables. The release test runs when a built release carries
// worldsindex/data/gaia-dr4.json.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { acceptGaiaDr4, gaiaDr4ManifestBlock, GAIA_DR4_SCHEMA_VERSION } from './worldsindex_gaia_dr4.mjs';
import { discoveriesHtml, hasRows, objectPanelHtml, statusHtml } from '../worldsindex/assets/gaia-dr4.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const fixture = () => JSON.parse(readFileSync(join(ROOT, 'tests/fixtures/worldsindex/gaia-dr4.synthetic.json'), 'utf8'));
const ATLAS = new Set(['object-barnard-b', 'object-hd-209458-b', 'object-pair-a-b', 'object-pair-b-b']);
const textOf = (html) => html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ');

test('the release accepts the file only under its schema and only for objects the atlas holds', () => {
  assert.equal(acceptGaiaDr4(null, ATLAS).file, null);
  assert.match(acceptGaiaDr4({ ...fixture(), schemaVersion: 'other' }, ATLAS).reason, /unknown schema/);
  assert.match(acceptGaiaDr4({ ...fixture(), state: 'CONFIRMED_PLANETS' }, ATLAS).reason, /unknown state/);
  const { file, dropped } = acceptGaiaDr4(fixture(), new Set(['object-barnard-b', 'object-pair-a-b']));
  assert.equal(dropped, 2);
  assert.deepEqual(Object.keys(file.objects).sort(), ['object-barnard-b', 'object-pair-a-b']);
  assert.deepEqual(file.sources['1000000000000000001'].match.hosts.map((host) => host.objectIds), [['object-pair-a-b'], []], 'host links keep every host, object links only the atlas objects');
  assert.equal(file.counts.objectsWithDr4, 2);
  const block = gaiaDr4ManifestBlock(file, dropped);
  assert.deepEqual([block.path, block.schemaVersion, block.state, block.droppedObjectIds], ['gaia-dr4.json', GAIA_DR4_SCHEMA_VERSION, 'AVAILABLE', 2]);
  assert.equal(gaiaDr4ManifestBlock(null, 0), null);
});

test('the object panel: Gaia names, the dr3_neighbourhood link, RUWE, candidate wording, calculated conversions', () => {
  const html = objectPanelHtml(fixture(), 'object-barnard-b');
  const words = textOf(html);
  assert.match(html, /<h3 id="gaia-dr4-title">Gaia DR4<\/h3>/);
  assert.match(words, /Gaia DR4 4472832130942575873/);
  assert.match(words, /dr3_neighbourhood table as a counterpart of the host's Gaia DR3 source \(probable match by Gaia's rule: yes/);
  assert.match(words, /elevated \(above 1\.4\)/);
  assert.match(words, /A companion is one possible cause, not a conclusion/);
  for (const column of ['solution_type', 'period', 'eccentricity', 'significance', 'gof', 'combination_method', 'm2_lower', 'exoplanet_candidate_hrd_proba']) assert.match(html, new RegExp(`<code>${column}</code>`), column);
  assert.match(words, /Gaia exoplanet candidate candidate, not a confirmed planet/);
  assert.match(words, /m2 in Jupiter masses calculated/);
  assert.match(words, /Calculated: unit conversion/);
  assert.match(words, /m2 \(M☉\)/); assert.match(html, /not given/, 'ORBITAL_M1 gives no m2: shown as not given, never a default');
  assert.match(html, /href="https:\/\/gea\.esac\.esa\.int\/tap-server\/tap\/sync\?[^"]*" rel="noopener" target="_blank">Gaia archive row/);
  const claims = [...words.matchAll(/confirmed (planet|exoplanet)/gi)].map((match) => words.slice(Math.max(0, match.index - 6), match.index));
  assert.ok(claims.length && claims.every((before) => before.endsWith('not a ')), 'the panel never calls anything a confirmed planet');
});

test('ambiguity is listed, never resolved; objects without DR4 and pre-release files show nothing', () => {
  const file = fixture();
  const pair = textOf(objectPanelHtml(file, 'object-pair-a-b'));
  assert.match(pair, /Ambiguous: this DR4 source also matches Pair B\. WorldsIndex does not choose between them\./);
  assert.match(textOf(objectPanelHtml(file, 'object-pair-b-b')), /also matches Pair A/);
  assert.match(pair, /probable match by Gaia's rule: yes/);
  assert.equal(objectPanelHtml(file, 'object-not-in-dr4'), '');
  assert.equal(objectPanelHtml({ ...file, state: 'NOT_YET_RELEASED' }, 'object-barnard-b'), '');
  assert.equal(objectPanelHtml({ ...file, state: 'RETRIEVING' }, 'object-barnard-b'), '', 'a retrieval in progress shows nothing yet');
  assert.equal(objectPanelHtml(null, 'object-barnard-b'), '');
});

test('Gaia text is escaped', () => {
  const file = fixture();
  file.sources['4472832130942575873'].candidate.fields.note = '<img src=x onerror=alert(1)>';
  const html = objectPanelHtml(file, 'object-barnard-b');
  assert.ok(!html.includes('<img'), 'markup from a Gaia column is never rendered');
  assert.match(html, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test('"New in Gaia DR4": candidates near known hosts and without, the candidate warning, nothing when empty', () => {
  const file = fixture();
  const html = discoveriesHtml(file);
  const words = textOf(html);
  assert.match(words, /New in Gaia DR4/);
  assert.match(words, /A candidate is not a confirmed planet\./);
  assert.match(words, /Near a known planet host \(2\)/); assert.match(words, /No known planet host \(2\)/);
  assert.match(html, /data-compare-open="object-barnard-b">Barnard&#39;s star ↗/);
  assert.match(words, /Exoplanet candidate/); assert.match(words, /Transit candidate/);
  assert.equal(discoveriesHtml({ ...file, discoveries: [] }), '');
  assert.equal(discoveriesHtml({ ...file, state: 'NOT_YET_RELEASED' }), '');
  const capped = textOf(discoveriesHtml(file, { limit: 1 }));
  assert.match(capped, /Showing 1 of 2; every row is in gaia-dr4\.json/);
});

test('the status line: the expected date before the release, the tables and counts after', () => {
  const before = { state: 'NOT_YET_RELEASED', stateDetail: 'Gaia DR4 is expected on 2026-12-02; the Gaia archive re-opens with it at 12:00 CET (11:00 UTC).', counts: {}, tables: [] };
  assert.equal(hasRows(before), false);
  assert.match(textOf(statusHtml(before)), /expected on 2026-12-02; the Gaia archive re-opens with it at 12:00 CET/);
  assert.doesNotMatch(statusHtml(before), /fineprint/);
  const block = gaiaDr4ManifestBlock(acceptGaiaDr4(fixture(), ATLAS).file, 0);
  assert.equal(hasRows(block), true);
  const after = textOf(statusHtml(block));
  assert.match(after, /4 objects carry a Gaia DR4 panel; 2 exoplanet and 2 transit candidates are listed\./);
  assert.match(after, /nss two body orbit|nss_two_body_orbit: confirmed/);
  assert.equal(statusHtml(null), '');
});

test('the built release (when present) carries a coherent Gaia DR4 file', { skip: !existsSync(join(ROOT, 'worldsindex/data/gaia-dr4.json')) }, () => {
  const manifest = JSON.parse(readFileSync(join(ROOT, 'worldsindex/data/manifest.json'), 'utf8'));
  const data = JSON.parse(readFileSync(join(ROOT, 'worldsindex/data/gaia-dr4.json'), 'utf8'));
  assert.equal(manifest.gaiaDr4.state, data.state);
  for (const objectId of Object.keys(data.objects)) assert.ok(objectPanelHtml(data, objectId) || data.state === 'RETRIEVING' || data.state === 'NOT_YET_RELEASED', objectId);
});

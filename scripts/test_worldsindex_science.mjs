import assert from 'node:assert/strict';

import {
  JUPITER_MASS_IN_EARTH_MASSES,
  JUPITER_RADIUS_IN_EARTH_RADII,
  convertUncertaintyPair,
  makeExportMetadata,
} from '../worldsindex/assets/science.js';

const mass = convertUncertaintyPair({ plus: 0.1, minus: -0.1 }, 'M♃', 'M⊕');
assert.equal(mass.comparable, true);
assert.equal(mass.converted, true);
assert.ok(Math.abs(mass.plus - 0.1 * JUPITER_MASS_IN_EARTH_MASSES) < 1e-12);
assert.ok(Math.abs(mass.minus + 0.1 * JUPITER_MASS_IN_EARTH_MASSES) < 1e-12);

const radius = convertUncertaintyPair({ plus: 0.02, minus: -0.01 }, 'R♃', 'R⊕');
assert.equal(radius.comparable, true);
assert.ok(Math.abs(radius.plus - 0.02 * JUPITER_RADIUS_IN_EARTH_RADII) < 1e-12);
assert.ok(Math.abs(radius.minus + 0.01 * JUPITER_RADIUS_IN_EARTH_RADII) < 1e-12);

const incompatible = convertUncertaintyPair({ plus: 1, minus: -1 }, 'km', 'au');
assert.equal(incompatible.comparable, false);
assert.equal(incompatible.unit, 'km');

const complete = makeExportMetadata({
  manifest: { generatedAt: 'public-release-time', atlasGeneratedAt: 'manifest-atlas-time', atlasSha256: 'abc123' },
  atlas: { generatedAt: 'atlas-snapshot-time' },
  scope: 'complete_atlas',
  filters: { status: 'all', method: 'all', source: 'all', yearStart: '', yearEnd: '' },
});
assert.equal(complete.publicReleaseGeneratedAt, 'public-release-time');
assert.equal(complete.atlasGeneratedAt, 'atlas-snapshot-time');
assert.equal(complete.filterStatus, 'all');
assert.equal(complete.filterYearStart, '');

const plot = makeExportMetadata({
  manifest: { generatedAt: 'public-release-time', atlasSha256: 'abc123' },
  atlas: { generatedAt: 'atlas-snapshot-time' },
  scope: 'visible_population_plot',
  filters: { status: 'CANDIDATE', method: 'TRANSIT', source: 'nasa-toi', yearStart: 2018, yearEnd: 2026 },
  plot: { x: 'orbitalPeriodDays', y: 'radiusEarth', xLog: true, yLog: false },
});
assert.equal(plot.filterStatus, 'CANDIDATE');
assert.equal(plot.filterSource, 'nasa-toi');
assert.equal(plot.plotXLog, true);
assert.equal(plot.plotYLog, false);

console.log('WorldsIndex scientific UI logic passed: unit-safe uncertainties and reproducible export metadata.');

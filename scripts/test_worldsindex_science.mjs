import assert from 'node:assert/strict';

import {
  JUPITER_MASS_IN_EARTH_MASSES,
  JUPITER_RADIUS_IN_EARTH_RADII,
  classifyPropertyAgreement,
  convertMeasurementToUnit,
  convertUncertaintyPair,
  makeExportMetadata,
  makePreferredValueRationale,
} from '../worldsindex/assets/science.js';

assert.ok(Math.abs(JUPITER_MASS_IN_EARTH_MASSES - 317.8284065946748) < 1e-12);
assert.ok(Math.abs(JUPITER_RADIUS_IN_EARTH_RADII - 11.20898073093868) < 1e-12);

const convertedMass = convertMeasurementToUnit(
  { value: 1, plus: 0.1, minus: -0.2, unit: 'M♃' },
  'M⊕',
);
assert.equal(convertedMass.comparable, true);
assert.equal(convertedMass.converted, true);
assert.ok(Math.abs(convertedMass.value - JUPITER_MASS_IN_EARTH_MASSES) < 1e-12);
assert.ok(Math.abs(convertedMass.plus - 0.1 * JUPITER_MASS_IN_EARTH_MASSES) < 1e-12);
assert.ok(Math.abs(convertedMass.minus + 0.2 * JUPITER_MASS_IN_EARTH_MASSES) < 1e-12);

const reverseRadius = convertMeasurementToUnit(
  { value: JUPITER_RADIUS_IN_EARTH_RADII, plus: 0.2, minus: -0.1, unit: 'R⊕' },
  'R♃',
);
assert.equal(reverseRadius.comparable, true);
assert.equal(reverseRadius.converted, true);
assert.ok(Math.abs(reverseRadius.value - 1) < 1e-12);

const incompatibleMeasurement = convertMeasurementToUnit(
  { value: 1, plus: 0.1, minus: -0.1, unit: 'M⊕' },
  'R⊕',
);
assert.equal(incompatibleMeasurement.comparable, false);
assert.equal(incompatibleMeasurement.value, 1);

for (const invalid of [' ', false, [], Number.NaN, Number.POSITIVE_INFINITY]) {
  assert.equal(
    convertMeasurementToUnit({ value: invalid, unit: 'M⊕' }, 'M⊕'),
    null,
  );
}

const overflow = convertMeasurementToUnit(
  { value: Number.MAX_VALUE, plus: 1, minus: -1, unit: 'M♃' },
  'M⊕',
);
assert.equal(overflow.comparable, false);
assert.equal(overflow.reasonCode, 'NONFINITE_CONVERSION');

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

const overlapping = classifyPropertyAgreement([
  { value: 10, plus: 2, minus: -1, unit: 'M⊕' },
  { value: 11.5, plus: 0.5, minus: -1, unit: 'M⊕' },
], 'M⊕');
assert.equal(overlapping.state, 'agreeing');
assert.equal(overlapping.intervalCount, 2);

const touchingIntervals = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1, unit: 'R⊕' },
  { value: 12, plus: 1, minus: -1, unit: 'R⊕' },
], 'R⊕');
assert.equal(touchingIntervals.state, 'agreeing');

const disjoint = classifyPropertyAgreement([
  { value: 10, plus: 0.2, minus: -0.2, unit: 'R⊕' },
  { value: 12, plus: 0.2, minus: -0.2, unit: 'R⊕' },
], 'R⊕');
assert.equal(disjoint.state, 'discrepant');

const mixed = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1, unit: 'M⊕' },
  { value: 10.5, plus: 1, minus: -1, unit: 'M⊕' },
  { value: 20, plus: 1, minus: -1, unit: 'M⊕' },
], 'M⊕');
assert.equal(mixed.state, 'mixed');

const crossUnitAgreement = classifyPropertyAgreement([
  { value: 1, plus: 0.01, minus: -0.01, unit: 'M♃' },
  {
    value: JUPITER_MASS_IN_EARTH_MASSES,
    plus: 0.01 * JUPITER_MASS_IN_EARTH_MASSES,
    minus: -0.01 * JUPITER_MASS_IN_EARTH_MASSES,
    unit: 'M⊕',
  },
], 'M⊕');
assert.equal(crossUnitAgreement.state, 'agreeing');
assert.equal(crossUnitAgreement.intervals[0].converted, true);

const missingUncertainty = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1, unit: 'R⊕' },
  { value: 10.2, plus: null, minus: null, unit: 'R⊕' },
], 'R⊕');
assert.equal(missingUncertainty.state, 'insufficient');

const retainedLimit = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1, unit: 'M⊕' },
  { value: 12, plus: null, minus: null, unit: 'M⊕', limitType: 'UPPER_LIMIT' },
], 'M⊕');
assert.equal(retainedLimit.state, 'non-comparable');

const incompatibleUnits = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1, unit: 'M⊕' },
  { value: 10, plus: 1, minus: -1, unit: 'R⊕' },
], 'M⊕');
assert.equal(incompatibleUnits.state, 'non-comparable');

const missingUnits = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1 },
  { value: 10.2, plus: 1, minus: -1 },
]);
assert.equal(missingUnits.state, 'non-comparable');

const incompatibleDefinitions = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1, unit: 'M⊕', comparisonKind: 'mass' },
  { value: 10, plus: 1, minus: -1, unit: 'M⊕', comparisonKind: 'minimum-mass' },
], 'M⊕');
assert.equal(incompatibleDefinitions.state, 'non-comparable');

const incompatibleUncertaintySemantics = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1, unit: 'M⊕', uncertaintySemantics: 'one-sigma' },
  { value: 10, plus: 1, minus: -1, unit: 'M⊕', uncertaintySemantics: 'ninety-five-percent' },
], 'M⊕');
assert.equal(incompatibleUncertaintySemantics.state, 'non-comparable');

const duplicateRecords = classifyPropertyAgreement([
  { value: 10, plus: 1, minus: -1, unit: 'M⊕', sourceRecordId: 'same-row' },
  { value: 10.2, plus: 1, minus: -1, unit: 'M⊕', sourceRecordId: 'same-row' },
], 'M⊕');
assert.equal(duplicateRecords.state, 'non-comparable');

const defaultRationale = makePreferredValueRationale({
  sourceLabel: 'NASA Planetary Systems',
  isSourceDefault: true,
  referenceLabel: 'Example et al. 2026',
  limitType: 'NONE',
});
assert.match(defaultRationale, /source-designated default/);
assert.match(defaultRationale, /reference Example et al\. 2026/);
assert.match(defaultRationale, /not an average or consensus/);

const limitRationale = makePreferredValueRationale({
  sourceId: 'example-source',
  isSourceDefault: false,
  referenceLabel: null,
  limitType: 'LOWER_LIMIT',
});
assert.match(limitRationale, /not source-designated default/);
assert.match(limitRationale, /lower limit, not a central measurement/);
assert.match(limitRationale, /no packaged reference/);

assert.match(makePreferredValueRationale({ sourceLabel: 'NASA', isSourceDefault: true, referenceLabel: 'ref', limitType: 1 }), /lower limit, not a central measurement/);
assert.match(makePreferredValueRationale({ sourceLabel: 'NASA', isSourceDefault: true, referenceLabel: 'ref', limitType: -1 }), /upper limit, not a central measurement/);

const unexplainedRationale = makePreferredValueRationale();
assert.match(unexplainedRationale, /^Unexplained display selection:/);

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
  filters: { status: 'CANDIDATE', method: 'TRANSIT', methodBasis: 'claims', identity: 'resolved', source: 'nasa-toi', yearStart: 2018, yearEnd: 2026 },
  plot: { x: 'orbitalPeriodDays', y: 'radiusEarth', xLog: true, yLog: false },
});
assert.equal(plot.filterStatus, 'CANDIDATE');
assert.equal(plot.filterSource, 'nasa-toi');
assert.equal(plot.filterMethodBasis, 'claims');
assert.equal(plot.filterIdentity, 'resolved');
assert.equal(plot.plotXLog, true);
assert.equal(plot.plotYLog, false);

console.log('WorldsIndex scientific UI logic passed: unit-safe values, conservative agreement states, selection rationale, and reproducible export metadata.');

// IAU 2015 Resolution B3 nominal conversion ratios, matching the ExoNexus atlas builder.
export const JUPITER_RADIUS_IN_EARTH_RADII = 7.1492e7 / 6.3781e6;
export const JUPITER_MASS_IN_EARTH_MASSES = 1.2668653e17 / 3.986004e14;

const UNIT_DEFINITIONS = new Map([
  ['M⊕', { dimension: 'mass', earthScale: 1 }],
  ['M_earth', { dimension: 'mass', earthScale: 1 }],
  ['M♃', { dimension: 'mass', earthScale: JUPITER_MASS_IN_EARTH_MASSES }],
  ['M_jupiter', { dimension: 'mass', earthScale: JUPITER_MASS_IN_EARTH_MASSES }],
  ['R⊕', { dimension: 'radius', earthScale: 1 }],
  ['R_earth', { dimension: 'radius', earthScale: 1 }],
  ['R♃', { dimension: 'radius', earthScale: JUPITER_RADIUS_IN_EARTH_RADII }],
  ['R_jupiter', { dimension: 'radius', earthScale: JUPITER_RADIUS_IN_EARTH_RADII }],
]);

function finiteOrNull(value) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null;
  if (typeof value !== 'string') return null;
  const trimmed = value.trim();
  if (!trimmed || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(trimmed)) return null;
  const number = Number(trimmed);
  return Number.isFinite(number) ? number : null;
}

function conversionForUnits(sourceUnit, targetUnit) {
  const sourceSupplied = typeof sourceUnit === 'string' ? sourceUnit.trim() !== '' : sourceUnit !== null && sourceUnit !== undefined;
  const targetSupplied = typeof targetUnit === 'string' ? targetUnit.trim() !== '' : targetUnit !== null && targetUnit !== undefined;
  if (!sourceSupplied || !targetSupplied) return { comparable: false, factor: null };
  if (sourceUnit === targetUnit) {
    return { comparable: true, factor: 1 };
  }
  const source = UNIT_DEFINITIONS.get(sourceUnit);
  const target = UNIT_DEFINITIONS.get(targetUnit);
  if (!source || !target || source.dimension !== target.dimension) {
    return { comparable: false, factor: null };
  }
  return { comparable: true, factor: source.earthScale / target.earthScale };
}

function limitKind(value) {
  if (value === 0) return 'measurement';
  // NASA Exoplanet Archive limit flags: +1 means a lower limit (>) and
  // -1 means an upper limit (<). Keep this direction explicit here because
  // reversing it changes the scientific claim.
  if (value === 1) return 'lower';
  if (value === -1) return 'upper';
  const normalized = String(value ?? 'NONE').trim().toUpperCase().replaceAll('-', '_').replaceAll(' ', '_');
  if (['', 'NONE', 'MEASUREMENT', 'NOT_A_LIMIT'].includes(normalized)) return 'measurement';
  if (['UPPER', 'UPPER_LIMIT'].includes(normalized)) return 'upper';
  if (['LOWER', 'LOWER_LIMIT'].includes(normalized)) return 'lower';
  return 'unknown';
}

// Converts a central value and its signed or unsigned uncertainty pair together.
// Only exact-unit matches and the explicit Earth/Jupiter mass and radius pairs
// above are comparable; unsupported conversions return the source-native values.
export function convertMeasurementToUnit(measurement, targetUnit) {
  const sourceUnit = measurement?.unit;
  const value = finiteOrNull(measurement?.value);
  const plus = finiteOrNull(measurement?.plus);
  const minus = finiteOrNull(measurement?.minus);
  if (value === null && plus === null && minus === null) return null;

  const conversion = conversionForUnits(sourceUnit, targetUnit);
  if (!conversion.comparable) {
    return {
      value,
      plus,
      minus,
      unit: sourceUnit,
      sourceUnit,
      targetUnit,
      converted: false,
      comparable: false,
    };
  }

  const scale = (number) => (number === null ? null : number * conversion.factor);
  const scaledValue = scale(value);
  const scaledPlus = scale(plus);
  const scaledMinus = scale(minus);
  if ([scaledValue, scaledPlus, scaledMinus].some((number) => number !== null && !Number.isFinite(number))) {
    return {
      value,
      plus,
      minus,
      unit: sourceUnit,
      sourceUnit,
      targetUnit,
      converted: false,
      comparable: false,
      reasonCode: 'NONFINITE_CONVERSION',
    };
  }
  return {
    value: scaledValue,
    plus: scaledPlus,
    minus: scaledMinus,
    unit: targetUnit,
    sourceUnit,
    targetUnit,
    converted: conversion.factor !== 1,
    comparable: true,
  };
}

export function convertUncertaintyPair(pair, sourceUnit, targetUnit) {
  if (!sourceUnit && !targetUnit) {
    const plus = finiteOrNull(pair?.plus);
    const minus = finiteOrNull(pair?.minus);
    if (plus === null && minus === null) return null;
    return { plus, minus, unit: targetUnit, sourceUnit, converted: false, comparable: true };
  }
  const converted = convertMeasurementToUnit({ plus: pair?.plus, minus: pair?.minus, unit: sourceUnit }, targetUnit);
  if (!converted) return null;
  const { plus, minus, unit, converted: didConvert, comparable } = converted;
  return { plus, minus, unit, sourceUnit, converted: didConvert, comparable };
}

// Compares reported uncertainty intervals without combining or averaging them.
// Limits and unsupported unit conversions are deliberately non-comparable;
// missing central values or two-sided uncertainties are insufficient evidence.
export function classifyPropertyAgreement(measurements, targetUnit = null) {
  const rows = Array.isArray(measurements) ? measurements : [];
  if (rows.length < 2) {
    return {
      state: 'insufficient',
      reason: 'At least two reported measurements are required.',
      measurementCount: rows.length,
      intervalCount: 0,
      targetUnit,
      intervals: [],
    };
  }

  if (rows.some((row) => finiteOrNull(row?.value) === null)) {
    return {
      state: 'insufficient',
      reason: 'At least one retained alternative has no finite central value.',
      measurementCount: rows.length,
      intervalCount: 0,
      targetUnit,
      intervals: [],
    };
  }

  const commonUnit = targetUnit ?? rows[0]?.unit;
  const comparisonKinds = rows.map((row) => row?.comparisonKind).filter((value) => value !== null && value !== undefined && value !== '');
  if (comparisonKinds.length > 0
    && (comparisonKinds.length !== rows.length || new Set(comparisonKinds).size !== 1)) {
    return {
      state: 'non-comparable',
      reason: 'The retained alternatives use incompatible property definitions.',
      measurementCount: rows.length,
      intervalCount: 0,
      targetUnit: commonUnit,
      intervals: [],
    };
  }
  const uncertaintySemantics = rows.map((row) => row?.uncertaintySemantics).filter((value) => value !== null && value !== undefined && value !== '');
  if (uncertaintySemantics.length > 0
    && (uncertaintySemantics.length !== rows.length || new Set(uncertaintySemantics).size !== 1)) {
    return {
      state: 'non-comparable',
      reason: 'The reported uncertainty intervals use incompatible or incomplete semantics.',
      measurementCount: rows.length,
      intervalCount: 0,
      targetUnit: commonUnit,
      intervals: [],
    };
  }
  const recordIds = rows.map((row) => row?.sourceRecordId).filter((value) => value !== null && value !== undefined && value !== '');
  if (new Set(recordIds).size !== recordIds.length) {
    return {
      state: 'non-comparable',
      reason: 'Duplicate source-record identifiers must be resolved before comparison.',
      measurementCount: rows.length,
      intervalCount: 0,
      targetUnit: commonUnit,
      intervals: [],
    };
  }
  if (rows.some((row) => limitKind(row?.limitType) !== 'measurement')) {
    return {
      state: 'non-comparable',
      reason: 'Limits are retained separately from central measurements.',
      measurementCount: rows.length,
      intervalCount: 0,
      targetUnit: commonUnit,
      intervals: [],
    };
  }

  const converted = rows.map((row) => convertMeasurementToUnit(row, commonUnit));
  if (converted.some((row) => !row?.comparable)) {
    return {
      state: 'non-comparable',
      reason: 'At least one unit cannot be converted into the requested common unit.',
      measurementCount: rows.length,
      intervalCount: 0,
      targetUnit: commonUnit,
      intervals: [],
    };
  }

  if (converted.some((row) => row.plus === null || row.minus === null)) {
    return {
      state: 'insufficient',
      reason: 'Every compared measurement needs reported upper and lower uncertainty.',
      measurementCount: rows.length,
      intervalCount: 0,
      targetUnit: commonUnit,
      intervals: [],
    };
  }

  const intervals = converted.map((row, index) => ({
    index,
    value: row.value,
    lower: row.value - Math.abs(row.minus),
    upper: row.value + Math.abs(row.plus),
    unit: row.unit,
    sourceUnit: row.sourceUnit,
    converted: row.converted,
  }));
  let overlappingPairs = 0;
  let pairCount = 0;
  for (let left = 0; left < intervals.length; left += 1) {
    for (let right = left + 1; right < intervals.length; right += 1) {
      pairCount += 1;
      if (Math.max(intervals[left].lower, intervals[right].lower)
        <= Math.min(intervals[left].upper, intervals[right].upper)) {
        overlappingPairs += 1;
      }
    }
  }

  let state = 'mixed';
  let reason = `${overlappingPairs} of ${pairCount} reported uncertainty-interval pairs overlap.`;
  if (overlappingPairs === pairCount) {
    state = 'agreeing';
    reason = 'All reported uncertainty-interval pairs overlap.';
  } else if (overlappingPairs === 0) {
    state = 'discrepant';
    reason = 'No reported uncertainty-interval pairs overlap.';
  }
  return {
    state,
    reason,
    measurementCount: rows.length,
    intervalCount: intervals.length,
    pairCount,
    overlappingPairCount: overlappingPairs,
    targetUnit: commonUnit,
    intervals,
  };
}

export function makePreferredValueRationale(selection = {}) {
  const {
    sourceLabel,
    sourceId,
    isSourceDefault = null,
    referenceLabel,
    limitType = 'NONE',
  } = selection;
  const missingInputs = [];
  if (!sourceLabel && !sourceId) missingInputs.push('source');
  if (!Object.hasOwn(selection, 'isSourceDefault')) missingInputs.push('source-default state');
  if (!Object.hasOwn(selection, 'referenceLabel')) missingInputs.push('reference state');
  if (!Object.hasOwn(selection, 'limitType')) missingInputs.push('limit state');
  if (missingInputs.length) {
    return `Unexplained display selection: missing explicit ${missingInputs.join(', ')}; not an average or consensus.`;
  }
  const source = sourceLabel || sourceId || 'source not supplied';
  const defaultState = isSourceDefault === true
    ? 'source-designated default'
    : isSourceDefault === false
      ? 'not source-designated default'
      : 'source-default state not supplied';
  const reference = referenceLabel ? `reference ${referenceLabel}` : 'no packaged reference';
  const kind = limitKind(limitType);
  const limit = kind === 'upper'
    ? 'upper limit, not a central measurement'
    : kind === 'lower'
      ? 'lower limit, not a central measurement'
      : kind === 'measurement'
        ? 'reported measurement'
        : `unrecognized limit semantics: ${String(limitType)}`;
  return `Display selection from ${source}; ${defaultState}; ${reference}; ${limit}; not an average or consensus.`;
}

export function makeExportMetadata({ manifest, atlas, scope, filters, plot = null }) {
  return {
    publicReleaseGeneratedAt: manifest.generatedAt,
    atlasGeneratedAt: atlas.generatedAt ?? manifest.atlasGeneratedAt,
    atlasSha256: manifest.atlasSha256,
    exportScope: scope,
    filterStatus: filters.status,
    filterMethod: filters.method,
    filterMethodBasis: filters.methodBasis ?? '',
    filterIdentity: filters.identity ?? '',
    filterSource: filters.source,
    filterYearStart: filters.yearStart,
    filterYearEnd: filters.yearEnd,
    plotX: plot?.x ?? '',
    plotY: plot?.y ?? '',
    plotXLog: plot?.xLog ?? '',
    plotYLog: plot?.yLog ?? '',
  };
}

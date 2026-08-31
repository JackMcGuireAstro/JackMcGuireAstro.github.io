// IAU 2015 Resolution B3 nominal conversion ratios, matching the ExoNexus atlas builder.
export const JUPITER_RADIUS_IN_EARTH_RADII = 7.1492e7 / 6.3781e6;
export const JUPITER_MASS_IN_EARTH_MASSES = 1.2668653e17 / 3.986004e14;

function finiteOrNull(value) {
  if (value === null || value === undefined || value === '') return null;
  const number = Number(value);
  return Number.isFinite(number) ? number : null;
}

export function convertUncertaintyPair(pair, sourceUnit, targetUnit) {
  const plus = finiteOrNull(pair?.plus);
  const minus = finiteOrNull(pair?.minus);
  if (plus === null && minus === null) return null;

  let factor = 1;
  let comparable = sourceUnit === targetUnit || (!sourceUnit && !targetUnit);
  if (sourceUnit === 'M♃' && targetUnit === 'M⊕') {
    factor = JUPITER_MASS_IN_EARTH_MASSES;
    comparable = true;
  } else if (sourceUnit === 'R♃' && targetUnit === 'R⊕') {
    factor = JUPITER_RADIUS_IN_EARTH_RADII;
    comparable = true;
  }

  if (!comparable) {
    return { plus, minus, unit: sourceUnit, sourceUnit, converted: false, comparable: false };
  }
  return {
    plus: plus === null ? null : plus * factor,
    minus: minus === null ? null : minus * factor,
    unit: targetUnit,
    sourceUnit,
    converted: factor !== 1,
    comparable: true,
  };
}

export function makeExportMetadata({ manifest, atlas, scope, filters, plot = null }) {
  return {
    publicReleaseGeneratedAt: manifest.generatedAt,
    atlasGeneratedAt: atlas.generatedAt ?? manifest.atlasGeneratedAt,
    atlasSha256: manifest.atlasSha256,
    exportScope: scope,
    filterStatus: filters.status,
    filterMethod: filters.method,
    filterSource: filters.source,
    filterYearStart: filters.yearStart,
    filterYearEnd: filters.yearEnd,
    plotX: plot?.x ?? '',
    plotY: plot?.y ?? '',
    plotXLog: plot?.xLog ?? '',
    plotYLog: plot?.yLog ?? '',
  };
}

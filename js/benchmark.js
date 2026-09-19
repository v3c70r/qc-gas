// ── Same-day regional benchmark for a single station ──
// Pure functions, no DOM, no network. The benchmark is always computed from
// the FULL station snapshot (`currentStations`) so it is intentionally
// independent of the active radius / region / brand / search filters.

function round2(v) {
  return Math.round(v * 100) / 100;
}

/**
 * Median of a sorted numeric array. For an even number of values this uses
 * the arithmetic mean of the two middle values, which is the conventional
 * definition and keeps the result deterministic.
 */
export function medianOf(sorted) {
  if (!sorted || !sorted.length) return null;
  const mid = Math.floor(sorted.length / 2);
  if (sorted.length % 2 === 1) return sorted[mid];
  return (sorted[mid - 1] + sorted[mid]) / 2;
}

/**
 * Build the same-day benchmark for one region + fuel price key.
 *
 * @param {object} currentStations GeoJSON FeatureCollection ({ features: [] })
 * @param {string} region region name from feature.properties.region
 * @param {string} priceKey e.g. 'regular_price'
 * @param {number|null} [stationPrice] current station price in ¢/L. When
 *   provided and the sample is sufficient, the station-relative fields are
 *   populated (gapToMin, gapToMedian, saving50L, cheaperThanPercent).
 * @returns {{
 *   count: number,
 *   min: number|null,
 *   max: number|null,
 *   median: number|null,
 *   cheaperThanPercent: number|null,
 *   gapToMin: number|null,
 *   gapToMedian: number|null,
 *   saving50L: number|null,
 *   sufficient: boolean
 * }}
 */
export function computeRegionBenchmark(currentStations, region, priceKey, stationPrice = null) {
  const empty = {
    count: 0,
    min: null,
    max: null,
    median: null,
    cheaperThanPercent: null,
    gapToMin: null,
    gapToMedian: null,
    saving50L: null,
    sufficient: false
  };

  if (!currentStations || !Array.isArray(currentStations.features) || !region || !priceKey) {
    return empty;
  }

  const prices = [];
  for (const feature of currentStations.features) {
    const props = feature && feature.properties;
    if (!props) continue;
    if (props.region !== region) continue;
    const price = props[priceKey];
    if (price != null && Number.isFinite(price)) prices.push(price);
  }

  const count = prices.length;
  if (!count) return empty;

  const sorted = prices.slice().sort((a, b) => a - b);
  const min = sorted[0];
  const max = sorted[sorted.length - 1];
  const median = medianOf(sorted);
  const sufficient = count >= 10;

  let cheaperThanPercent = null;
  let gapToMin = null;
  let gapToMedian = null;
  let saving50L = null;

  if (sufficient && stationPrice != null && Number.isFinite(stationPrice)) {
    // Strictly cheaper comparison makes equal-price ties deterministic: a
    // station is only "cheaper than" stations priced strictly below it.
    let cheaperCount = 0;
    for (const price of sorted) {
      if (price < stationPrice) cheaperCount += 1;
    }
    cheaperThanPercent = Math.round((cheaperCount / count) * 100);
    gapToMin = round2(stationPrice - min);
    gapToMedian = round2(stationPrice - median);
    // 50 L × gap in ¢/L → dollars. Positive = cheaper than the median.
    saving50L = round2((median - stationPrice) * 50 / 100);
  }

  return {
    count,
    min,
    max,
    median,
    cheaperThanPercent,
    gapToMin,
    gapToMedian,
    saving50L,
    sufficient
  };
}

// Test/console hook (mirrors window.__qcGasSearch / window.__qcGasMap).
if (typeof window !== 'undefined') {
  window.__qcGasBenchmark = { computeRegionBenchmark, medianOf };
}

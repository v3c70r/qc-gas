// Fuel price window (slider bounds) + map colour ramp, derived from the loaded
// snapshot instead of hardcoded regular-price constants (issue #118).
//
// The range slider used to sit at 150–240¢ and the map ramp topped out at 200¢
// while both were really describing the *regular* distribution. Diesel (median
// 289.9¢) therefore rendered as an empty map with a single "most expensive"
// red, and Super lost every station above 240¢.

// Green → yellow → red, unchanged palette (cheap → expensive).
const COLOR_CHEAP = '#16a34a';
const COLOR_MEDIUM = '#eab308';
const COLOR_EXPENSIVE = '#dc2626';

// Legacy fixed window, used when a fuel has no usable price at all so the UI
// never shows NaN / Infinity / an inverted range.
export const FALLBACK_PRICE_BOUNDS = Object.freeze({ min: 150, max: 240 });
const FALLBACK_STOPS = Object.freeze([165, 180, 200]);

// Where the ramp turns yellow / red inside the window. 0.35 / 0.75 keeps the
// colour changes where the bulk of the stations sit, so the map actually says
// "relatively cheap" / "relatively expensive" for every fuel.
const STOP_RATIOS = [0, 0.35, 0.75];

// Accepts either a fuel id ('diesel') or a property key ('diesel_price').
export function priceKeyFor(fuel) {
  if (!fuel) return null;
  return fuel.endsWith('_price') ? fuel : `${fuel}_price`;
}

/**
 * min/max of `fuelKey` over the snapshot, rounded outward to 5¢.
 * Returns null when no station carries a finite price for that fuel.
 */
export function computeFuelBounds(stations, fuelKey) {
  const key = priceKeyFor(fuelKey);
  if (!key) return null;
  const features = Array.isArray(stations?.features)
    ? stations.features
    : Array.isArray(stations) ? stations : [];

  let min = Infinity;
  let max = -Infinity;
  for (const feature of features) {
    const value = feature?.properties?.[key];
    if (typeof value !== 'number' || !Number.isFinite(value)) continue;
    if (value < min) min = value;
    if (value > max) max = value;
  }
  if (!Number.isFinite(min) || !Number.isFinite(max)) return null;

  const bounds = { min: Math.floor(min / 5) * 5, max: Math.ceil(max / 5) * 5 };
  // A single flat price would collapse the slider to one value; keep it usable.
  if (bounds.max <= bounds.min) bounds.max = bounds.min + 5;
  return bounds;
}

// Bounds for the slider: the computed ones, or the legacy window as a fallback.
export function effectivePriceBounds(bounds) {
  return bounds && Number.isFinite(bounds.min) && Number.isFinite(bounds.max) && bounds.max > bounds.min
    ? bounds
    : FALLBACK_PRICE_BOUNDS;
}

/** The three ramp stop values for a window (legacy 165/180/200 without one). */
export function priceStops(bounds) {
  const span = bounds ? bounds.max - bounds.min : 0;
  if (!Number.isFinite(span) || span <= 0) return [...FALLBACK_STOPS];
  return STOP_RATIOS.map((ratio) => Math.round((bounds.min + ratio * span) * 100) / 100);
}

/**
 * Mapbox `interpolate` expression colouring a station by `fuelKey`.
 * `bounds` missing or degenerate → the historical 165/180/200 ramp.
 */
export function priceColorExpression(fuelKey, bounds) {
  const key = priceKeyFor(fuelKey);
  const [cheap, medium, expensive] = priceStops(bounds);
  return [
    'interpolate',
    ['linear'],
    ['get', key],
    cheap, COLOR_CHEAP,
    medium, COLOR_MEDIUM,
    expensive, COLOR_EXPENSIVE
  ];
}

export const PRICE_COLORS = Object.freeze([COLOR_CHEAP, COLOR_MEDIUM, COLOR_EXPENSIVE]);

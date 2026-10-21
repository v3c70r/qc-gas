// Google/Apple-Maps-like basemap for the Québec gas-price map (issue #109).
//
// The Mapbox `light-v11` style the app used to ship has 50 layers and only 22
// road layers: no landuse blocks, almost no road hierarchy, 3 POI layers. That
// makes the map impossible to place by its looks. `streets-v12` has 134 layers
// (93 road layers, landuse, POIs, haloed labels) and is the closest official
// style to Google/Apple Maps, so we start from it and re-tint it with a small
// palette instead of authoring a whole style.
//
// Everything here is pure data + one apply function so it can be unit tested
// without a WebGL context.
//
// See docs/map-style.md for the token → layer mapping.

/** Official Mapbox style, not a private/custom style URL. */
export const BASEMAP_STYLE = 'mapbox://styles/mapbox/streets-v12';

/**
 * Palette tokens (Google/Apple Maps reference values).
 * `land`   warm off-white ground (Google)      `water`   light blue water
 * `park`   park green                          `wood`    woodland, slightly deeper
 * `landuse` neutral non-park landuse           `building` light warm grey blocks
 * `roadMinor` white minor roads                `roadCase` road casing light grey
 * `motorway` light yellow motorways            `motorwayCase` darker motorway casing
 * `label`  dark grey place labels              `labelHalo` white label halo
 */
export const BASEMAP_PALETTE = Object.freeze({
  land: '#f2efe9',
  water: '#aadaff',
  park: '#c8e8c9',
  wood: '#cfe6c4',
  landuse: '#eceae4',
  building: '#e8e5e1',
  roadMinor: '#ffffff',
  roadCase: '#d9d6d0',
  motorway: '#ffe082',
  motorwayCase: '#e0b843',
  label: '#3c4043',
  labelHalo: '#ffffff'
});

/** POI labels: Mapbox streets only shows them from z14, the app lives at z10–12. */
export const POI_MIN_ZOOM = 12;
export const POI_MAX_ZOOM = 24;

// ── Performance baseline (issue #124, completing #109 acceptance item 5) ──
//
// `streets-v12` is a lot heavier than the `light-v11` it replaced (134 vs 50
// layers), so "did the basemap get slower?" must be answerable with a number,
// not a feeling. These are real measurements produced by
// `npm run measure:map-load -- --runs 5` (scripts/measure_map_load.mjs); the
// prose lives in docs/map-style.md → 「性能基线」.
//
// Two probe columns are recorded because they answer different questions:
//  - `app`          the app's own map, i.e. what a visitor experiences
//                   (`window.__qcBasemapStyleLoadMs`, set in js/map.js);
//  - `streets-v12` / `light-v11`  an off-screen probe map created inside the
//                   already-loaded page, so both styles are measured under
//                   identical conditions (the only honest way to compare them).
const freezeSeries = (samples) => {
  const sorted = [...samples].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  return Object.freeze({
    samples: Object.freeze([...samples]),
    median,
    range: Object.freeze([sorted[0], sorted[sorted.length - 1]])
  });
};

export const STYLE_LOAD_BASELINE = Object.freeze({
  measuredAt: '2026-10-20',
  environment:
    'headless chromium 156 (Playwright), viewport 1280x800, Montréal z10, ' +
    'vite dev server, 5 runs, URL-restricted token so vector tiles 403',
  command: 'npm run measure:map-load -- --runs 5',
  series: Object.freeze({
    // style.load of the running app (streets-v12 + palette).
    app: freezeSeries([152, 147, 156, 164, 153]),
    // Probe maps, `style.load` only; compare these two with each other.
    'streets-v12': freezeSeries([514, 512, 512, 506, 519]),
    'light-v11': freezeSeries([498, 468, 502, 505, 483])
  })
});

/**
 * Budget enforced by the Playwright guard in tests/map-style.spec.js: a `style.load`
 * slower than this fails the suite with the measured value printed.
 *
 * 8 s is ~50× the recorded app median (153 ms) on purpose: the guard exists to
 * catch "someone made the basemap 10× heavier", not to police CI jitter (a busy
 * runner plus a cold dev server easily costs a second, and a guard that flakes
 * gets muted — which protects nothing). Bump it only together with a fresh
 * `npm run measure:map-load` run recorded in `STYLE_LOAD_BASELINE` + the docs.
 */
export const STYLE_LOAD_BUDGET_MS = 8000;

// ── Layer groups (all ids from `GET /styles/v1/mapbox/streets-v12`) ──
// Any id missing from a future style is skipped by `applyBasemapPalette`.

// Minor/major roads rendered as plain white surface (Google look).
const ROAD_SURFACE_LAYERS = [
  'road-street', 'road-street-low', 'road-secondary-tertiary', 'road-minor',
  'road-minor-link', 'road-major-link', 'road-primary',
  'tunnel-street', 'tunnel-street-low', 'tunnel-secondary-tertiary', 'tunnel-minor',
  'tunnel-minor-link', 'tunnel-major-link', 'tunnel-primary',
  'bridge-street', 'bridge-street-low', 'bridge-secondary-tertiary', 'bridge-minor',
  'bridge-minor-link', 'bridge-major-link', 'bridge-major-link-2', 'bridge-primary'
];

// Motorways/trunks keep the yellow "highway" convention.
const MOTORWAY_LAYERS = [
  'road-motorway-trunk',
  'tunnel-motorway-trunk',
  'bridge-motorway-trunk',
  'bridge-motorway-trunk-2'
];

// Casing (`*-case`) draws the thin outline under the road fill, so the road
// network stays legible on the warm background at any zoom.
const ROAD_CASE_LAYERS = [
  'tunnel-minor-case', 'tunnel-street-case', 'tunnel-minor-link-case',
  'tunnel-secondary-tertiary-case', 'tunnel-primary-case', 'tunnel-major-link-case',
  'road-pedestrian-case', 'road-minor-case', 'road-street-case', 'road-minor-link-case',
  'road-secondary-tertiary-case', 'road-primary-case', 'road-major-link-case',
  'bridge-pedestrian-case', 'bridge-minor-case', 'bridge-street-case',
  'bridge-minor-link-case', 'bridge-secondary-tertiary-case', 'bridge-primary-case',
  'bridge-major-link-case', 'bridge-major-link-2-case'
];

// Label layers get one text colour + a white halo, like Google/Apple labels.
const LABEL_LAYERS = [
  'road-label', 'poi-label', 'country-label', 'state-label',
  'settlement-major-label', 'settlement-minor-label', 'settlement-subdivision-label',
  'water-line-label', 'water-point-label', 'natural-line-label', 'natural-point-label',
  'waterway-label', 'transit-label', 'airport-label', 'continent-label'
];

// `landuse`/`landcover` are `match` expressions in streets-v12: replacing them
// wholesale would paint the whole province green, so only the natural classes
// are re-tinted and everything else falls back to the neutral landuse token.
const LANDUSE_COLOR = [
  'match', ['get', 'class'],
  ['park', 'national_park', 'pitch', 'golf_course'], BASEMAP_PALETTE.park,
  ['wood', 'scrub'], BASEMAP_PALETTE.wood,
  BASEMAP_PALETTE.landuse
];

const LANDCOVER_COLOR = [
  'match', ['get', 'class'],
  ['wood', 'scrub'], BASEMAP_PALETTE.wood,
  ['grass', 'grassland'], BASEMAP_PALETTE.park,
  BASEMAP_PALETTE.landuse
];

const override = (id, prop, value) => ({ id, prop, value });

/**
 * Flat list of `setPaintProperty` operations. `line-width`, `line-opacity` and
 * `fill-opacity` are deliberately untouched: they carry the zoom expressions
 * that make roads appear/disappear at the right zoom.
 */
export const PAINT_OVERRIDES = Object.freeze([
  override('land', 'background-color', BASEMAP_PALETTE.land),

  override('water', 'fill-color', BASEMAP_PALETTE.water),
  override('water-shadow', 'fill-color', BASEMAP_PALETTE.water),
  override('waterway', 'line-color', BASEMAP_PALETTE.water),
  override('waterway-shadow', 'line-color', BASEMAP_PALETTE.water),
  override('ferry', 'line-color', BASEMAP_PALETTE.water),
  override('ferry-auto', 'line-color', BASEMAP_PALETTE.water),

  override('national-park', 'fill-color', BASEMAP_PALETTE.park),
  override('landuse', 'fill-color', LANDUSE_COLOR),
  override('landcover', 'fill-color', LANDCOVER_COLOR),
  override('golf-hole-line', 'line-color', BASEMAP_PALETTE.park),

  override('building', 'fill-color', BASEMAP_PALETTE.building),
  override('building-underground', 'fill-color', BASEMAP_PALETTE.building),

  ...ROAD_CASE_LAYERS.map(id => override(id, 'line-color', BASEMAP_PALETTE.roadCase)),
  ...ROAD_SURFACE_LAYERS.map(id => override(id, 'line-color', BASEMAP_PALETTE.roadMinor)),
  ...MOTORWAY_LAYERS.map(id => override(id, 'line-color', BASEMAP_PALETTE.motorway)),
  override('road-motorway-trunk-case', 'line-color', BASEMAP_PALETTE.motorwayCase),
  override('tunnel-motorway-trunk-case', 'line-color', BASEMAP_PALETTE.motorwayCase),
  override('bridge-motorway-trunk-case', 'line-color', BASEMAP_PALETTE.motorwayCase),
  override('bridge-motorway-trunk-2-case', 'line-color', BASEMAP_PALETTE.motorwayCase),

  ...LABEL_LAYERS.flatMap(id => [
    override(id, 'text-color', BASEMAP_PALETTE.label),
    override(id, 'text-halo-color', BASEMAP_PALETTE.labelHalo),
    override(id, 'text-halo-width', 1.5)
  ])
]).map(Object.freeze);

const LABEL_LAYERS_SET = new Set(LABEL_LAYERS);

/**
 * Apply the Google/Apple-like palette on top of the streets-v12 basemap.
 *
 * Every operation is guarded by `map.getLayer(id)` so a Mapbox style update
 * (renamed/removed layer) degrades to "that detail keeps its Mapbox colour"
 * instead of throwing. Safe to call on every `style.load`, which also covers
 * future `setStyle()` calls.
 *
 * @param {object} map Mapbox GL map (a duck-typed fake is enough for tests)
 * @returns {{applied: number, skipped: number, missing: string[]}}
 */
export function applyBasemapPalette(map) {
  const result = { applied: 0, skipped: 0, missing: [] };

  for (const { id, prop, value } of PAINT_OVERRIDES) {
    if (!map.getLayer(id)) {
      result.skipped += 1;
      // One entry per missing layer, not per missing paint property.
      if (!result.missing.includes(id)) result.missing.push(id);
      continue;
    }
    map.setPaintProperty(id, prop, value);
    result.applied += 1;
  }

  if (map.getLayer('poi-label')) {
    map.setLayerZoomRange('poi-label', POI_MIN_ZOOM, POI_MAX_ZOOM);
  }

  return result;
}

/** Layer ids this module expects to exist (useful for diagnostics/tests). */
export function expectedLayerIds() {
  return [...new Set(PAINT_OVERRIDES.map(o => o.id))];
}

export function isLabelLayer(id) {
  return LABEL_LAYERS_SET.has(id);
}

// ── WCAG contrast helper (used by the palette tests) ──

function channelToLinear(channel) {
  const c = channel / 255;
  return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

/** WCAG 2.1 relative luminance of a `#rrggbb` colour. */
export function relativeLuminance(hex) {
  const value = String(hex).replace('#', '');
  if (!/^[0-9a-f]{6}$/i.test(value)) throw new Error(`Invalid colour: ${hex}`);
  const r = channelToLinear(parseInt(value.slice(0, 2), 16));
  const g = channelToLinear(parseInt(value.slice(2, 4), 16));
  const b = channelToLinear(parseInt(value.slice(4, 6), 16));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** WCAG 2.1 contrast ratio between two `#rrggbb` colours (1 → 21). */
export function contrastRatio(hexA, hexB) {
  const a = relativeLuminance(hexA);
  const b = relativeLuminance(hexB);
  const [light, dark] = a >= b ? [a, b] : [b, a];
  return (light + 0.05) / (dark + 0.05);
}

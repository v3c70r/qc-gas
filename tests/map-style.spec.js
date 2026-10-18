// Issue #109 — Google/Apple-like basemap (streets-v12 + palette overrides).
//
// Two groups of assertions:
//  - pure unit tests on `js/map-style.js` (runs in node, no browser);
//  - one Playwright integration spec against the real Mapbox-rendered page
//    (`window.__qcMap`), plus the human-comparison screenshot.
import { test, expect } from '@playwright/test';
import { derivedDataPath } from './fixtures/data-snapshots.js';

import {
  BASEMAP_STYLE,
  BASEMAP_PALETTE,
  PAINT_OVERRIDES,
  POI_MIN_ZOOM,
  applyBasemapPalette,
  contrastRatio
} from '../js/map-style.js';

const BASE_URL = process.env.BASE_URL || 'http://localhost:5173';

// The local Mapbox token is URL-restricted to the production domain, so vector
// tiles requested from localhost come back 403. That is a *token* restriction,
// not an app problem: the style JSON and every `getStyle()`/`getPaintProperty()`
// assertion below work regardless. Only the screenshot needs real tiles, so it
// replays the production Referer through Playwright's route handler (test-only).
const PRODUCTION_REFERER = 'https://qgu.io/qc-gas/';

// ── Unit: palette shape ──

test.describe('basemap palette (unit)', () => {
  test('uses the official streets-v12 style, not light-v11', () => {
    expect(BASEMAP_STYLE).toBe('mapbox://styles/mapbox/streets-v12');
  });

  test('every palette value is a valid #rrggbb colour', () => {
    const entries = Object.entries(BASEMAP_PALETTE);
    expect(entries.length).toBeGreaterThanOrEqual(12);
    for (const [token, value] of entries) {
      expect(value, `palette.${token}`).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  test('label text stays WCAG AA readable over land and over roads', () => {
    expect(contrastRatio(BASEMAP_PALETTE.label, BASEMAP_PALETTE.land)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(BASEMAP_PALETTE.label, BASEMAP_PALETTE.roadMinor)).toBeGreaterThanOrEqual(4.5);
  });

  test('POI labels start at zoom 12', () => {
    expect(POI_MIN_ZOOM).toBe(12);
  });

  test('water, land and road overrides target the documented properties', () => {
    const find = (id, prop) => PAINT_OVERRIDES.find(o => o.id === id && o.prop === prop);
    expect(find('land', 'background-color')?.value).toBe(BASEMAP_PALETTE.land);
    expect(find('water', 'fill-color')?.value).toBe(BASEMAP_PALETTE.water);
    expect(find('waterway', 'line-color')?.value).toBe(BASEMAP_PALETTE.water);
    expect(find('road-street', 'line-color')?.value).toBe(BASEMAP_PALETTE.roadMinor);
    expect(find('road-motorway-trunk', 'line-color')?.value).toBe(BASEMAP_PALETTE.motorway);
    expect(find('road-motorway-trunk-case', 'line-color')?.value).toBe(BASEMAP_PALETTE.motorwayCase);
    expect(find('road-street-case', 'line-color')?.value).toBe(BASEMAP_PALETTE.roadCase);
    expect(find('poi-label', 'text-color')?.value).toBe(BASEMAP_PALETTE.label);
    expect(find('poi-label', 'text-halo-color')?.value).toBe(BASEMAP_PALETTE.labelHalo);
  });

  test('overrides never touch line-width / line-opacity / fill-opacity', () => {
    const forbidden = ['line-width', 'line-opacity', 'fill-opacity', 'circle-radius'];
    for (const o of PAINT_OVERRIDES) {
      expect(forbidden, `${o.id}.${o.prop}`).not.toContain(o.prop);
    }
  });

  test('every override id is unique per (layer, prop)', () => {
    const keys = PAINT_OVERRIDES.map(o => `${o.id}|${o.prop}`);
    expect(new Set(keys).size).toBe(keys.length);
  });
});

// ── Unit: applyBasemapPalette against a fake map ──

function fakeMap(layerIds) {
  const layers = new Set(layerIds);
  const calls = [];
  const zoomRanges = [];
  return {
    calls,
    zoomRanges,
    getLayer: (id) => (layers.has(id) ? { id } : undefined),
    setPaintProperty: (id, prop, value) => { calls.push({ id, prop, value }); },
    setLayerZoomRange: (id, min, max) => { zoomRanges.push({ id, min, max }); }
  };
}

test.describe('applyBasemapPalette (unit)', () => {
  test('applies the expected (id, prop, value) set to known layers', () => {
    const map = fakeMap(['land', 'water', 'road-street', 'poi-label']);
    applyBasemapPalette(map);

    expect(map.calls).toContainEqual({ id: 'land', prop: 'background-color', value: BASEMAP_PALETTE.land });
    expect(map.calls).toContainEqual({ id: 'water', prop: 'fill-color', value: BASEMAP_PALETTE.water });
    expect(map.calls).toContainEqual({ id: 'road-street', prop: 'line-color', value: BASEMAP_PALETTE.roadMinor });
    expect(map.calls).toContainEqual({ id: 'poi-label', prop: 'text-color', value: BASEMAP_PALETTE.label });
    // Only layers that exist in this fake map may be touched.
    for (const call of map.calls) expect(['land', 'water', 'road-street', 'poi-label']).toContain(call.id);
  });

  test('silently skips layers missing from the style and never throws', () => {
    const map = fakeMap([]);
    expect(() => applyBasemapPalette(map)).not.toThrow();
    expect(map.calls).toHaveLength(0);
    expect(map.zoomRanges).toHaveLength(0);
  });

  test('raises POI labels to z12 (Mapbox default is z14)', () => {
    const map = fakeMap(['poi-label']);
    applyBasemapPalette(map);
    expect(map.zoomRanges).toContainEqual({ id: 'poi-label', min: POI_MIN_ZOOM, max: 24 });
  });

  test('does not call setLayerZoomRange when poi-label is absent', () => {
    const map = fakeMap(['water']);
    applyBasemapPalette(map);
    expect(map.zoomRanges).toHaveLength(0);
  });

  test('reports how many overrides were applied', () => {
    const map = fakeMap(['land', 'water']);
    const result = applyBasemapPalette(map);
    expect(result.applied).toBe(2);
    expect(result.skipped).toBeGreaterThan(0);
  });
});

// ── Integration: real Mapbox render ──

test.describe('basemap integration (real Mapbox)', () => {
  test.beforeEach(async ({ page }) => {
    // The app fetches data/stations.json (derived, not committed): bootstrap it
    // the same way tests/app.spec.js does.
    derivedDataPath('stations.json');
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    // `style.load` has fired and the streets style is parsed. `map.loaded()` is
    // not usable here: streets keeps sources dirty (tiles 403 on localhost).
    await page.waitForFunction(
      () => window.__qcMap && window.__qcMap.isStyleLoaded && window.__qcMap.isStyleLoaded(),
      null,
      { timeout: 20000 }
    );
  });

  test('the basemap is streets (road source-layer present), not light', async ({ page }) => {
    const info = await page.evaluate(() => {
      const style = window.__qcMap.getStyle();
      return {
        name: style.name,
        hasRoad: style.layers.some(l => l['source-layer'] === 'road'),
        hasRoadStreet: Boolean(window.__qcMap.getLayer('road-street')),
        layerCount: style.layers.length
      };
    });
    expect(info.hasRoad).toBe(true);
    expect(info.hasRoadStreet).toBe(true);
    expect(info.name).toBe('Mapbox Streets');
    // streets-v12 ships 134 layers; light-v11 only 50.
    expect(info.layerCount).toBeGreaterThan(100);
  });

  test('palette overrides are live on the map', async ({ page }) => {
    const paint = await page.evaluate(() => ({
      land: window.__qcMap.getPaintProperty('land', 'background-color'),
      water: window.__qcMap.getPaintProperty('water', 'fill-color'),
      roadStreet: window.__qcMap.getPaintProperty('road-street', 'line-color'),
      park: window.__qcMap.getPaintProperty('national-park', 'fill-color'),
      poiText: window.__qcMap.getPaintProperty('poi-label', 'text-color')
    }));
    expect(paint.land).toBe(BASEMAP_PALETTE.land);
    expect(paint.water).toBe(BASEMAP_PALETTE.water);
    expect(paint.roadStreet).toBe(BASEMAP_PALETTE.roadMinor);
    expect(paint.park).toBe(BASEMAP_PALETTE.park);
    expect(paint.poiText).toBe(BASEMAP_PALETTE.label);
  });

  test('POI labels become visible at z12', async ({ page }) => {
    const minzoom = await page.evaluate(() => window.__qcMap.getLayer('poi-label').minzoom);
    expect(minzoom).toBe(POI_MIN_ZOOM);
  });

  test('application layers keep rendering above the basemap', async ({ page }) => {
    await page.waitForFunction(
      () => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0,
      null,
      { timeout: 15000 }
    );
    const order = await page.evaluate(() => {
      const style = window.__qcMap.getStyle();
      const ids = style.layers.map(l => l.id);
      return {
        poi: ids.indexOf('poi-label'),
        points: ids.indexOf('unclustered-points'),
        labels: ids.indexOf('station-price-labels'),
        cluster: ids.indexOf('clusters'),
        total: ids.length
      };
    });
    expect(order.points).toBeGreaterThan(order.poi);
    expect(order.cluster).toBeGreaterThan(order.poi);
    expect(order.labels).toBeGreaterThan(order.points);

    // Brand price colours are untouched by the basemap change.
    const radius = await page.evaluate(() =>
      window.__qcMap.getPaintProperty('unclustered-points', 'circle-radius')
    );
    expect(JSON.stringify(radius)).toContain('"zoom"');
  });

  // Screenshot for human comparison only — no pixel assertion, same policy as
  // the other screenshot specs in tests/app.spec.js.
  test('basemap screenshot - Montréal z12 (Google/Apple comparison)', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });

    // Replay the production Referer so the URL-restricted token serves tiles
    // (see PRODUCTION_REFERER above). Without this the basemap is blank grey.
    await page.route('https://api.mapbox.com/**', async (route) => {
      const headers = { ...route.request().headers(), referer: PRODUCTION_REFERER };
      delete headers.origin;
      await route.fulfill({ response: await route.fetch({ headers }) });
    });
    await page.reload();
    await page.waitForFunction(
      () => window.__qcMap && window.__qcMap.isStyleLoaded && window.__qcMap.isStyleLoaded(),
      null,
      { timeout: 20000 }
    );
    await page.waitForFunction(
      () => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0,
      null,
      { timeout: 15000 }
    );

    await page.evaluate(() => {
      window.__qcMap.jumpTo({ center: [-73.5673, 45.5017], zoom: 12, pitch: 0, bearing: 0 });
    });
    await page.waitForFunction(
      () => window.__qcMap.areTilesLoaded && window.__qcMap.areTilesLoaded(),
      null,
      { timeout: 20000 }
    );
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'tests/screenshots/basemap-google-like.png', fullPage: false });
  });
});

// Issue #109 — Google/Apple-like basemap (streets-v12 + palette overrides).
//
// Four groups of assertions (issue #125 split them apart on purpose):
//  - pure unit tests on `js/map-style.js` (node, no browser);
//  - style-data integration tests against a committed streets-v12 snapshot
//    (node, no tiles, no network) — the strict assertions;
//  - live-map integration tests (`window.__qcMap`), style JSON only, never tiles;
//  - one best-effort human-comparison screenshot that skips itself when real
//    tiles are unavailable instead of flaking the round.
import { test, expect } from '@playwright/test';
import { derivedDataPath } from './fixtures/data-snapshots.js';
import { STREETS_V12_STYLE, styleLayer, styleLayerIds } from './fixtures/basemap-style.js';

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

// ── Integration: style data (offline, no tiles) ──
//
// These assertions used to live behind a real Mapbox render, so a slow or
// rate-limited tile CDN could fail them (issue #125). They only ever read
// *style data* — layer ids, `source-layer`, zoom range, the palette applied on
// top — which is exactly what the committed streets-v12 snapshot holds. So they
// now run in node: deterministic, no network, no WebGL, no token.
test.describe('basemap style data (offline)', () => {
  test('is the streets style (road source-layer present), not light', () => {
    expect(STREETS_V12_STYLE.name).toBe('Mapbox Streets');
    expect(STREETS_V12_STYLE.layers.some(l => l['source-layer'] === 'road')).toBe(true);
    expect(styleLayer('road-street')?.['source-layer']).toBe('road');
    // streets-v12 ships 134 layers; light-v11 only 50.
    expect(STREETS_V12_STYLE.layers.length).toBeGreaterThan(100);
  });

  test('every palette override targets a layer that exists in streets-v12', () => {
    const map = fakeMap(styleLayerIds());
    const result = applyBasemapPalette(map);
    // A layer renamed/removed upstream would be skipped silently at runtime;
    // this asserts the real style still has all of them.
    expect(result.missing).toEqual([]);
    expect(result.applied).toBe(PAINT_OVERRIDES.length);
    expect(result.skipped).toBe(0);
  });

  test('the applied palette matches BASEMAP_PALETTE on the real layers', () => {
    const map = fakeMap(styleLayerIds());
    applyBasemapPalette(map);
    const applied = (id, prop) => map.calls.find(c => c.id === id && c.prop === prop)?.value;

    expect(applied('land', 'background-color')).toBe(BASEMAP_PALETTE.land);
    expect(applied('water', 'fill-color')).toBe(BASEMAP_PALETTE.water);
    expect(applied('road-street', 'line-color')).toBe(BASEMAP_PALETTE.roadMinor);
    expect(applied('national-park', 'fill-color')).toBe(BASEMAP_PALETTE.park);
    expect(applied('poi-label', 'text-color')).toBe(BASEMAP_PALETTE.label);
  });

  test('POI labels are raised from the streets default to z12', () => {
    // streets ships POI labels at z6 (icon) / z14 (text); the app shows the
    // label layer from z12, because the map lives at z10–12.
    expect(styleLayer('poi-label').minzoom).toBeLessThanOrEqual(POI_MIN_ZOOM);
    const map = fakeMap(styleLayerIds());
    applyBasemapPalette(map);
    expect(map.zoomRanges).toContainEqual({ id: 'poi-label', min: POI_MIN_ZOOM, max: 24 });
  });
});

// ── Integration: live map (needs the Mapbox style, never the tiles) ──
//
// Same properties, but read off the *running* map: this catches app wiring
// regressions the snapshot cannot (palette never applied on `style.load`,
// style swapped back to light-v11, layer order broken).
//
// Only *tiles* are blocked on localhost (the token is URL-restricted); the
// style JSON itself is small and always 200, so none of these assertions wait
// on tiles. If Mapbox is unreachable altogether there is no map at all — an
// environment problem, not an app regression, and the offline group above still
// asserts every property — so those cases skip with a reason instead of failing
// the round (issue #125 acceptance #2).
const STYLE_TIMEOUT_MS = 20000;

async function waitForStyle(page, timeout = STYLE_TIMEOUT_MS) {
  try {
    await page.waitForFunction(
      () => window.__qcMap && window.__qcMap.isStyleLoaded && window.__qcMap.isStyleLoaded(),
      null,
      { timeout }
    );
    return true;
  } catch {
    return false;
  }
}

async function skipUnlessStyleLoaded(page) {
  if (await waitForStyle(page)) return;
  const reason = 'Mapbox style never loaded (mapbox-gl or style JSON unreachable)';
  test.info().annotations.push({ type: 'basemap-live-skipped', description: reason });
  test.skip(true, `${reason}; palette properties stay covered by 'basemap style data (offline)'`);
}

test.describe('basemap integration (live map)', () => {
  // Two bounded waits (style ≤ 20 s, station data ≤ 20 s) must both fit.
  test.describe.configure({ timeout: 45000 });

  test.beforeEach(async ({ page }) => {
    // The app fetches data/stations.json (derived, not committed): bootstrap it
    // the same way tests/app.spec.js does.
    derivedDataPath('stations.json');
    // `domcontentloaded` + explicit state waits, never `networkidle`: the map
    // keeps retrying 403'd tiles forever, so "idle" only ever resolves on the
    // 15 s timeout and eats the whole test budget (issue #125 flake).
    await page.goto(BASE_URL, { waitUntil: 'domcontentloaded' });
  });

  test('the basemap is streets (road source-layer present), not light', async ({ page }) => {
    await skipUnlessStyleLoaded(page);
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
    expect(info.layerCount).toBeGreaterThan(100);
  });

  test('palette overrides are live on the map', async ({ page }) => {
    await skipUnlessStyleLoaded(page);
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
    await skipUnlessStyleLoaded(page);
    const minzoom = await page.evaluate(() => window.__qcMap.getLayer('poi-label').minzoom);
    expect(minzoom).toBe(POI_MIN_ZOOM);
  });

  test('application layers keep rendering above the basemap', async ({ page }) => {
    await skipUnlessStyleLoaded(page);
    // Local data, no network: if the station layers never appear that is an app
    // regression, so this wait fails the test instead of skipping.
    await page.waitForFunction(
      () => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0,
      null,
      { timeout: 20000 }
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
});

// ── Screenshot: best-effort, never fails the round (issue #125) ──
//
// Human-comparison artefact only, no pixel assertion — same policy as the other
// screenshot specs in tests/app.spec.js. The local token is URL-restricted to
// the production domain, so tiles are proxied with the production Referer
// (test-only). Real tiles are still *network*: when they are slow, blocked or
// rate-limited the test skips itself with a recorded reason instead of turning
// the whole round `needs_human`.

/** Vector tiles and glyph atlases; the style JSON and sprites are not. */
const isTileRequest = url => url.includes('/v4/') || url.includes('/fonts/');

function proxyMapboxThroughProductionReferer(net) {
  return async (route) => {
    const request = route.request();
    const tile = isTileRequest(request.url());
    if (tile) net.tiles += 1;

    const headers = { ...request.headers(), referer: PRODUCTION_REFERER };
    delete headers.origin;

    try {
      const response = await route.fetch({ headers, timeout: 15000 });
      if (tile) {
        if (response.ok()) net.tilesOk += 1;
        else net.tilesFailed += 1;
      }
      await route.fulfill({ response });
    } catch (err) {
      // A rejected route handler surfaces *outside* any test ("error was not a
      // part of any test") and fails the run. Swallow it, abort the request and
      // let the tile-availability check below decide to skip.
      if (tile) net.tilesFailed += 1;
      net.lastError = err?.message ? String(err.message).split('\n')[0] : String(err);
      await route.abort().catch(() => {});
    }
  };
}

/** Wait for real source data (state, not a fixed sleep); bounded so it cannot hang. */
async function waitForFirstSourceEvent(page, timeoutMs = 15000) {
  try {
    return await page.evaluate((timeout) => new Promise((resolve) => {
      const map = window.__qcMap;
      if (!map) return resolve({ ok: false, reason: 'map instance missing' });
      if (map.areTilesLoaded && map.areTilesLoaded()) return resolve({ ok: true, event: 'already-loaded' });

      let settled = false;
      const finish = (payload) => {
        if (settled) return;
        settled = true;
        clearTimeout(timer);
        map.off('sourcedata', onSource);
        map.off('idle', onIdle);
        resolve(payload);
      };
      const onSource = (e) => { if (e && e.isSourceLoaded) finish({ ok: true, event: 'sourcedata' }); };
      const onIdle = () => finish({ ok: true, event: 'idle' });
      const timer = setTimeout(
        () => finish({ ok: false, reason: `no sourcedata/idle within ${timeout}ms` }),
        timeout
      );
      map.on('sourcedata', onSource);
      map.on('idle', onIdle);
    }), timeoutMs);
  } catch (err) {
    return { ok: false, reason: `page evaluate failed: ${err.message}` };
  }
}

async function waitForTilesLoaded(page, timeoutMs = 20000) {
  try {
    await page.waitForFunction(
      () => window.__qcMap && window.__qcMap.areTilesLoaded && window.__qcMap.areTilesLoaded(),
      null,
      { timeout: timeoutMs }
    );
    return true;
  } catch {
    return false;
  }
}

test.describe('basemap screenshot (best-effort)', () => {
  test('Montréal z12 (Google/Apple comparison)', async ({ page }) => {
    test.setTimeout(120000);
    await page.setViewportSize({ width: 1280, height: 800 });

    const net = { tiles: 0, tilesOk: 0, tilesFailed: 0, lastError: null };
    const skipScreenshot = (why) => {
      const detail = `${why} — tiles: ${net.tiles}, ok: ${net.tilesOk}, failed: ${net.tilesFailed}` +
        (net.lastError ? `, last error: ${net.lastError}` : '');
      test.info().annotations.push({ type: 'basemap-screenshot-skipped', description: detail });
      test.skip(true, `basemap screenshot skipped: ${detail}`);
    };

    try {
      // Register the proxy *before* the first load so the initial style/tile
      // requests already carry the production Referer.
      await page.route('https://api.mapbox.com/**', proxyMapboxThroughProductionReferer(net));
      await page.goto(BASE_URL);

      if (!(await waitForStyle(page))) skipScreenshot('Mapbox style never loaded');

      const sourceEvent = await waitForFirstSourceEvent(page);
      if (!sourceEvent.ok) skipScreenshot(sourceEvent.reason);

      try {
        await page.waitForFunction(
          () => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0,
          null,
          { timeout: 15000 }
        );
      } catch {
        skipScreenshot('station features never rendered');
      }

      await page.evaluate(() => {
        window.__qcMap.jumpTo({ center: [-73.5673, 45.5017], zoom: 12, pitch: 0, bearing: 0 });
      });
      const tilesLoaded = await waitForTilesLoaded(page);

      // Blank-grey screenshot is worse than none: require that tiles actually
      // arrived through the proxy and that most of them succeeded.
      if (!tilesLoaded) skipScreenshot('tiles never finished loading');
      if (net.tilesOk === 0) skipScreenshot('no tile served (token whitelist / network)');
      if (net.tilesFailed > net.tilesOk) skipScreenshot('most tiles failed');

      await page.screenshot({ path: 'tests/screenshots/basemap-google-like.png', fullPage: false });
    } finally {
      // Requests are still in flight when the map settles; leaving them queued
      // is what produced "error was not a part of any test".
      await page.unrouteAll({ behavior: 'ignoreErrors' }).catch(() => {});
    }
  });
});

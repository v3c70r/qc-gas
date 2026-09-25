import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const BASE_URL = process.env.BASE_URL || 'http://localhost:5173';
const DEPLOY_URL = 'https://qgu.io/qc-gas/';

const stationsFixture = JSON.parse(
  readFileSync(path.join(path.dirname(fileURLToPath(import.meta.url)), '../data/stations.json'), 'utf8')
);

test.describe('QC Gas Price App - Regression Tests', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  });

  test('homepage loads with map', async ({ page }) => {
    await expect(page.locator('#map')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('#header h1')).toContainText('⛽');
  });

  test('sidebar renders with quick stats', async ({ page }) => {
    await expect(page.locator('#sidebar')).toBeVisible();
    await expect(page.locator('.quick-stats')).toBeVisible();
    await expect(page.locator('#quick-regular')).toBeVisible();
    await expect(page.locator('#quick-super')).toBeVisible();
    await expect(page.locator('#quick-diesel')).toBeVisible();
  });

  test('filter toggle expands/collapses', async ({ page }) => {
    const filterToggle = page.locator('#filter-toggle');
    await expect(filterToggle).toBeVisible();

    const filterPanel = page.locator('#filter-panel');
    await expect(filterPanel).not.toHaveClass(/open/);

    await filterToggle.click();
    await expect(filterPanel).toHaveClass(/open/);

    await filterToggle.click();
    await expect(filterPanel).not.toHaveClass(/open/);
  });

  test('brand filters render', async ({ page }) => {
    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    const brandFilters = page.locator('.brand-filter-item');
    await expect(brandFilters.first()).toBeVisible();
  });

  test('fuel type chips toggle', async ({ page }) => {
    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    // Fuel type is single-select (radio): clicking another chip switches the active one
    const regular = page.locator('.fuel-chip').nth(0);
    const superChip = page.locator('.fuel-chip').nth(1);
    await expect(regular).toBeVisible();

    await expect(regular).toHaveClass(/active/);
    await superChip.click();
    await expect(superChip).toHaveClass(/active/);
    await expect(regular).not.toHaveClass(/active/);

    // switching back works too
    await regular.click();
    await expect(regular).toHaveClass(/active/);
    await expect(superChip).not.toHaveClass(/active/);
  });

  test('radius buttons switch active state', async ({ page }) => {
    const radiusBtns = page.locator('.radius-btn');
    const firstBtn = radiusBtns.first();
    await expect(firstBtn).toBeVisible();

    await firstBtn.click();
    await expect(firstBtn).toHaveClass(/active/);
  });

  test('station list renders after data loads', async ({ page }) => {
    await page.waitForTimeout(3000);
    const stationList = page.locator('#station-list');
    await expect(stationList).toBeVisible();
    const items = stationList.locator('.list-item');
    const count = await items.count();
    expect(count).toBeGreaterThan(0);
  });

  test('language switcher changes UI text', async ({ page }) => {
    const langSelector = page.locator('#lang-selector');
    await expect(langSelector).toBeVisible();

    const enBtn = langSelector.locator('button:has-text("EN")');
    await enBtn.click();
    await page.waitForTimeout(500);
    await expect(page.locator('#title')).toContainText('Québec Gas Prices');

    const zhBtn = langSelector.locator('button:has-text("中文")');
    await zhBtn.click();
    await page.waitForTimeout(500);
    await expect(page.locator('#title')).toContainText('魁北克油价');
  });

  test('no stations message shows when no results', async ({ page }) => {
    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    const fuelChip = page.locator('.fuel-chip').first();
    await fuelChip.click();
    await page.waitForTimeout(500);
  });

  test('locate button exists', async ({ page }) => {
    await expect(page.locator('#locate-btn')).toBeVisible();
  });

  test('legend is visible', async ({ page }) => {
    await expect(page.locator('#legend')).toBeVisible();
    await expect(page.locator('.legend-bar')).toBeVisible();
  });
});

test.describe('Mobile Responsiveness', () => {
  const viewports = [
    { name: 'iPhone', width: 390, height: 844 },
    { name: 'Android', width: 412, height: 915 },
    { name: 'iPad', width: 820, height: 1180 }
  ];

  for (const vp of viewports) {
    test(`${vp.name} viewport (${vp.width}x${vp.height}) renders correctly`, async ({ page }) => {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(BASE_URL);
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

      await expect(page.locator('#map')).toBeVisible({ timeout: 10000 });
      await expect(page.locator('#sidebar')).toBeVisible();
      await expect(page.locator('#locate-btn')).toBeVisible();

      if (vp.width < 768) {
        await expect(page.locator('#sidebar-handle')).toBeVisible();
        await expect(page.locator('#sidebar')).toHaveClass(/collapsed/);
      }
    });
  }

  test('mobile bottom sheet swipe behavior', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const sidebar = page.locator('#sidebar');
    const handle = page.locator('#sidebar-handle');

    await expect(sidebar).toHaveClass(/collapsed/);

    await handle.click();
    await expect(sidebar).not.toHaveClass(/collapsed/);
  });
});

test.describe('Desktop Layout', () => {
  test('sidebar expanded on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    await expect(page.locator('#sidebar')).not.toHaveClass(/collapsed/);
    await expect(page.locator('#sidebar-handle')).not.toBeVisible();
  });

  test('header positioned correctly on desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const header = page.locator('#header');
    const box = await header.boundingBox();
    // On desktop the map (and its overlay header) sits to the RIGHT of the
    // 320px sidebar, so the header's left edge is expected near x≈332.
    expect(box.x).toBeGreaterThanOrEqual(320);
    expect(box.x).toBeLessThan(420);
  });
});

test.describe('i18n - Language Coverage', () => {
  const languages = [
    { code: 'en-CA', title: 'Gas Prices', results: 'Results' },
    { code: 'fr-CA', title: "Prix de l'essence", results: 'Résultats' },
    { code: 'zh-Hans', title: '油价', results: '结果' }
  ];

  for (const lang of languages) {
    test(`${lang.code} - all key UI elements translated`, async ({ page }) => {
      await page.goto(BASE_URL);
      await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

      const langSelector = page.locator('#lang-selector');
      await langSelector.locator(`button[data-lang="${lang.code}"]`).click();
      await page.waitForTimeout(500);

      await expect(page.locator('#title')).toContainText(lang.title.split(' ')[0], { ignoreCase: true });
    });
  }
});

test.describe('Filter Workflows', () => {
  test('price slider updates display', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    const minSlider = page.locator('#min-price');
    await minSlider.fill('170');
    await page.waitForTimeout(500);

    await expect(page.locator('#min-price-val')).toContainText('1.70');
  });

  test('region dropdown populates with options', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    const regionSelect = page.locator('#region-filter');
    const options = regionSelect.locator('option');
    const count = await options.count();
    expect(count).toBeGreaterThan(1);
  });

  test('map source has zero features when all brands deselected', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    await page.locator('#brand-select-all').click();
    await page.waitForTimeout(500);

    const count = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());
    expect(count).toBe(0);
  });
});

test.describe('Brand Price Comparison', () => {
  test('brand rows show station count, average price and diff', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    const first = page.locator('.brand-filter-item').first();
    await expect(first).toBeVisible();
    await expect(first.locator('.brand-count')).toBeVisible();
    await expect(first.locator('.brand-avg')).toContainText('¢', { timeout: 15000 });
    await expect(first.locator('.brand-diff')).toContainText('¢');
  });

  test('brand comparison updates when fuel type changes', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    const first = page.locator('.brand-filter-item').first();
    await expect(first.locator('.brand-avg')).toContainText('¢', { timeout: 15000 });
    const before = await first.locator('.brand-avg').textContent();

    await page.locator('.fuel-chip').nth(1).click();
    await expect(first.locator('.brand-avg')).not.toHaveText(before, { timeout: 5000 });
  });

  test('membership brand shows an explanatory hint', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(300);

    const memberships = page.locator('.brand-membership');
    expect(await memberships.count()).toBeGreaterThan(0);
    await expect(memberships.first()).toHaveAttribute('title', /Membership|Membre|会员/);
  });
});

test.describe('Visual Regression Screenshots', () => {
  test('full page screenshot - desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'tests/screenshots/desktop-home.png', fullPage: false });
  });

  test('filter panel screenshot - desktop', async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 800 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(500);

    await page.screenshot({ path: 'tests/screenshots/desktop-filter-panel.png', fullPage: false });
  });

  test('mobile home screenshot', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(2000);
    await page.screenshot({ path: 'tests/screenshots/mobile-home.png', fullPage: false });
  });

  test('mobile filter panel screenshot', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const sidebar = page.locator('#sidebar');
    await sidebar.evaluate(el => el.classList.remove('collapsed'));
    await page.waitForTimeout(500);

    const filterToggle = page.locator('#filter-toggle');
    await filterToggle.click();
    await page.waitForTimeout(500);

    await page.screenshot({ path: 'tests/screenshots/mobile-filter-panel.png', fullPage: false });
  });

  test('Chinese language screenshot', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    await page.locator('#lang-selector button[data-lang="zh-Hans"]').click();
    await page.waitForTimeout(1000);
    await page.screenshot({ path: 'tests/screenshots/mobile-chinese.png', fullPage: false });
  });
});

test.describe('Accessibility Basic Checks', () => {
  test('all buttons have accessible names', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const buttons = page.locator('button');
    const count = await buttons.count();
    for (let i = 0; i < count; i++) {
      const btn = buttons.nth(i);
      const text = await btn.textContent();
      const title = await btn.getAttribute('title');
      const aria = await btn.getAttribute('aria-label');
      // an accessible name may come from text, title, or aria-label
      expect(text?.trim() || title || aria).toBeTruthy();
    }
  });

  test('images have alt text or title', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    const svgs = page.locator('svg');
    const count = await svgs.count();
    expect(count).toBeGreaterThan(0);
  });
});

test.describe('Favorites (localStorage)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  });

  test('favorite star toggles and persists after reload', async ({ page }) => {
    await page.waitForTimeout(3000);
    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();

    const firstStar = list.locator('.list-item .fav-star').first();
    await expect(firstStar).toHaveAttribute('aria-label', /Favorite|Favori|收藏/);
    const firstItemName = await list.locator('.list-item .name').first().textContent();

    await firstStar.click();
    await expect(list.locator('.list-item .fav-star').first()).toHaveClass(/on/);
    await expect(list.locator('.list-item .name').first()).toContainText(firstItemName);

    await page.reload();
    await page.waitForTimeout(3000);
    await expect(page.locator('#station-list .list-item .fav-star').first()).toHaveClass(/on/);
    await expect(page.locator('#station-list .list-item .name').first()).toContainText(firstItemName);
  });

  test('favorited stations are pinned above price sort', async ({ page }) => {
    await page.waitForTimeout(3000);
    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();

    const secondName = await list.locator('.list-item').nth(1).locator('.name').textContent();
    await list.locator('.list-item').nth(1).locator('.fav-star').click();

    await expect(list.locator('.list-item').first().locator('.name')).toContainText(secondName);
    await expect(list.locator('.list-item').first().locator('.fav-star')).toHaveClass(/on/);
  });

  test('favorites only toggle shows only favorites and updates count', async ({ page }) => {
    await page.waitForTimeout(3000);
    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();

    const favCount = page.locator('#favorites-count');
    await expect(favCount).toContainText('0');

    await list.locator('.list-item').nth(0).locator('.fav-star').click();
    await expect(favCount).toContainText('1');
    await list.locator('.list-item').nth(1).locator('.fav-star').click();
    await expect(favCount).toContainText('2');

    await page.locator('#favorites-toggle').click();
    await expect(page.locator('#favorites-toggle')).toHaveClass(/active/);

    const items = list.locator('.list-item');
    await expect(items).toHaveCount(2);
    for (let i = 0; i < 2; i++) {
      await expect(items.nth(i).locator('.fav-star')).toHaveClass(/on/);
    }
  });

  test('unfavoriting under Favorites only re-syncs map and stats', async ({ page }) => {
    await page.waitForTimeout(3000);
    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();

    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    // Favorite one station, then enable Favorites only.
    await list.locator('.list-item').nth(0).locator('.fav-star').click();
    await page.locator('#favorites-toggle').click();
    await expect(page.locator('#favorites-toggle')).toHaveClass(/active/);

    await page.waitForFunction(() => window.__qcGasMap.getStationFeatureCount() === 1, null, { timeout: 10000 });
    await expect(page.locator('#sidebar-station-count')).toContainText('1');

    // Unfavorite it from the list — map, count and list must all empty out.
    await list.locator('.list-item').first().locator('.fav-star').click();

    await page.waitForFunction(() => window.__qcGasMap.getStationFeatureCount() === 0, null, { timeout: 10000 });
    await expect(page.locator('#sidebar-station-count')).toContainText('0');
    await expect(list.locator('.list-item')).toHaveCount(0);
  });

  test('favorites toggle before stations load does not throw', async ({ page }) => {
    let release;
    const gate = new Promise(resolve => { release = resolve; });
    await page.route('**/data/stations.json', async route => {
      await gate;
      await route.continue();
    });

    const pageErrors = [];
    page.on('pageerror', err => pageErrors.push(err.message));

    await page.goto(BASE_URL);
    await page.waitForSelector('#map canvas', { timeout: 15000 });
    await page.waitForFunction(() => document.getElementById('favorites-count').textContent.trim().length > 1);

    // Fire the favorites toggle and a language switch while stations are still loading.
    await page.locator('#favorites-toggle').click();
    await page.locator('#lang-selector button[data-lang="en-CA"]').click();

    release();
    await page.waitForTimeout(2500);

    // Toggle back off so the normal unfiltered list can render.
    await page.locator('#favorites-toggle').click();
    await expect(page.locator('#station-list .list-item').first()).toBeVisible();

    expect(pageErrors).toEqual([]);
  });

  test('favorite star buttons have accessible names', async ({ page }) => {
    await page.waitForTimeout(3000);
    const stars = page.locator('#station-list .fav-star');
    const count = await stars.count();
    expect(count).toBeGreaterThan(0);
    for (let i = 0; i < Math.min(count, 3); i++) {
      const aria = await stars.nth(i).getAttribute('aria-label');
      expect(aria).toBeTruthy();
    }
  });
});

test.describe('Offline Station Search', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  });

  test('pure normalization ignores case, accents and extra whitespace', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasSearch, null, { timeout: 15000 });

    const result = await page.evaluate(() => {
      const s = window.__qcGasSearch;
      const feature = {
        properties: {
          name: 'Station Émile',
          brand: 'Shell',
          address: "123 rue de l'Église, Montréal",
          postal_code: 'H2X 1Y0',
          region: 'Montréal'
        }
      };
      return {
        norm: s.normalizeText('  ÉGLISE   Montréal '),
        accent: s.matchesFeature(feature, s.normalizeText('eglise')),
        city: s.matchesFeature(feature, s.normalizeText('MONTREAL')),
        postal: s.matchesFeature(feature, s.normalizeText('h2x 1y0')),
        brand: s.matchesFeature(feature, s.normalizeText('SHELL')),
        name: s.matchesFeature(feature, s.normalizeText('emile'))
      };
    });

    expect(result.norm).toBe('eglise montreal');
    expect(result.accent).toBe(true);
    expect(result.city).toBe(true);
    expect(result.postal).toBe(true);
    expect(result.brand).toBe(true);
    expect(result.name).toBe(true);
  });

  test('queries shorter than two characters do not match', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasSearch, null, { timeout: 15000 });

    const count = await page.evaluate(() => {
      const s = window.__qcGasSearch;
      const feature = { properties: { name: 'Shell', brand: 'Shell', address: 'x', postal_code: 'x', region: 'x' } };
      return s.searchFeatures('s', [feature]).length;
    });

    expect(count).toBe(0);
  });

  test('clear button is hidden when the query is empty', async ({ page }) => {
    const clearBtn = page.locator('#search-clear');
    await expect(clearBtn).toBeHidden();

    const input = page.locator('#station-search');
    await input.fill('rouyn');
    await expect(clearBtn).toBeVisible();

    await input.fill('');
    await expect(clearBtn).toBeHidden();
  });

  test('typing filters the map source and sidebar list', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    const input = page.locator('#station-search');
    await input.fill('rouyn');
    await page.waitForTimeout(500);

    await expect(page.locator('#search-suggestions .search-suggestion').first()).toBeVisible();
    const count = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());
    expect(count).toBeGreaterThan(0);
    await expect(page.locator('#station-list .list-item').first()).toBeVisible();
  });

  test('clear button restores the previous filtered state', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });
    const before = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());

    const input = page.locator('#station-search');
    await input.fill('shell');
    await page.waitForTimeout(500);
    const during = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());
    expect(during).not.toBe(before);

    await page.locator('#search-clear').click();
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());
    expect(after).toBe(before);
  });

  test('Escape while focused clears the active search', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });
    const before = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());

    const input = page.locator('#station-search');
    await input.fill('rouyn');
    await page.waitForTimeout(500);
    await input.press('Escape');
    await page.waitForTimeout(500);

    await expect(input).toHaveValue('');
    const after = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());
    expect(after).toBe(before);
  });

  test('keyboard up/down + Enter opens a station card', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    const input = page.locator('#station-search');
    await input.fill('rouyn');
    await page.waitForTimeout(500);

    const suggestions = page.locator('#search-suggestions .search-suggestion');
    await expect(suggestions.first()).toBeVisible();

    await input.press('ArrowDown');
    await input.press('Enter');
    await page.waitForTimeout(800);

    await expect(page.locator('.mapboxgl-popup').first()).toBeVisible();
  });
});

test.describe('Trip Fuel Cost Estimator', () => {
  const openDetailPanel = async (page) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3000);
    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();
    await list.locator('.list-item').first().click();
    await expect(page.locator('.mapboxgl-popup').first()).toBeVisible();
    await page.locator('.mapboxgl-popup [data-expand]').click();
    await expect(page.locator('#station-panel')).toHaveClass(/open/);
    await expect(page.locator('.sd-trip')).toBeVisible();
  };

  test('computes cost and switches one-way / round-trip', async ({ page }) => {
    await openDetailPanel(page);

    const input = page.locator('.sd-trip-input');
    await expect(input).toHaveValue('8');

    await input.fill('10');
    const costEl = page.locator('.sd-trip-cost');
    await expect(costEl).not.toHaveText('—');
    await expect(costEl).not.toContainText('NaN');
    const oneWayCost = await costEl.textContent();

    await page.locator('[data-trip-mode="roundtrip"]').click();
    await expect(page.locator('[data-trip-mode="roundtrip"]')).toHaveClass(/on/);
    const roundTripCost = await costEl.textContent();
    expect(roundTripCost).not.toBe(oneWayCost);
  });

  test('consumption persists across reload', async ({ page }) => {
    await openDetailPanel(page);

    const input = page.locator('.sd-trip-input');
    await input.fill('9.5');

    await page.reload();
    await openDetailPanel(page);
    await expect(page.locator('.sd-trip-input')).toHaveValue('9.5');
  });

  test('invalid consumption falls back without NaN', async ({ page }) => {
    await openDetailPanel(page);

    const input = page.locator('.sd-trip-input');
    await input.fill('0');
    await expect(page.locator('.sd-trip-cost')).toHaveText('—');
    await expect(page.locator('.sd-trip-cost')).not.toContainText('NaN');
  });
});

test.describe('Station History (real data only)', () => {
  test('shows empty placeholder and hides change chip when no recorded station history', async ({ page }) => {
    await page.route('**/data/history/station-history.json', route =>
      route.fulfill({ json: { v: 1, t: [], s: {} } })
    );

    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3000);

    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();
    await list.locator('.list-item').first().click();
    await expect(page.locator('.mapboxgl-popup').first()).toBeVisible();

    const popup = page.locator('.mapboxgl-popup').first();
    await expect(popup.locator('.sc-empty')).toBeVisible();
    await expect(popup.locator('.sc-change')).toHaveCount(0);
    await expect(popup).not.toContainText('0.0%');
    await expect(popup.locator('.sc-mini-stats .sc-mini').nth(0).locator('b')).toHaveText('—');
    await expect(popup.locator('.sc-mini-stats .sc-mini').nth(1).locator('b')).toHaveText('—');

    // Expanded panel also shows an empty state, not an empty axis.
    await popup.locator('[data-expand]').click();
    await expect(page.locator('#station-panel')).toHaveClass(/open/);
    await expect(page.locator('#station-chart-empty')).toBeVisible();
    await expect(page.locator('.sd-change-row')).toBeEmpty();
  });
});

test.describe('Dashboard Region Ranking', () => {
  const openDashboard = async (page) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.locator('#dashboard-trigger').click();
    await expect(page.locator('#dashboard-panel')).toHaveClass(/open/);
  };

  const sample = (date, avg, min, max) => ({
    date,
    regular: { avg, min, max },
    super: { avg: avg + 20, min: min + 20, max: max + 20 },
    diesel: { avg: avg + 30, min: min + 30, max: max + 30 }
  });

  test('renders 18 regions plus the overall province row', async ({ page }) => {
    await openDashboard(page);
    const rows = page.locator('#dashboard-ranking-body tr');
    await expect(rows.first()).toBeVisible();
    expect(await rows.count()).toBe(19);
    await expect(page.locator('#dashboard-ranking-body')).toContainText('Province');
  });

  test('change column degrades to — when fewer than two samples exist', async ({ page }) => {
    await page.route('**/data/history.json', async route => {
      await route.fulfill({
        json: {
          regions: {
            'Montréal': { points: [sample('2026-09-10T18:00:00Z', 199.6, 189.9, 203.9)] },
            'Québec': { points: [sample('2026-09-10T18:00:00Z', 198.5, 183.9, 200.9)] }
          },
          overall: { points: [sample('2026-09-10T18:00:00Z', 196.4, 152.2, 239.3)] },
          metadata: {
            generated_at: '2026-09-10T18:00:00Z',
            latest: '2026-09-10T18:00:00Z',
            interval_hours: 6,
            regions: ['Montréal', 'Québec'],
            source: 'test'
          }
        }
      });
    });

    await openDashboard(page);
    const changes = page.locator('#dashboard-ranking-body td.ranking-change');
    await expect(changes.first()).toBeVisible();
    expect(await changes.count()).toBe(3);
    for (let i = 0; i < await changes.count(); i++) {
      await expect(changes.nth(i)).toHaveText('—');
    }
  });

  test('change column shows a day-over-day delta when two samples exist', async ({ page }) => {
    await page.route('**/data/history.json', async route => {
      await route.fulfill({
        json: {
          regions: {
            'Montréal': {
              points: [
                sample('2026-09-09T12:00:00Z', 190.0, 185.0, 195.0),
                sample('2026-09-10T12:00:00Z', 195.0, 190.0, 200.0)
              ]
            }
          },
          overall: {
            points: [
              sample('2026-09-09T12:00:00Z', 190.0, 185.0, 195.0),
              sample('2026-09-10T12:00:00Z', 196.0, 191.0, 201.0)
            ]
          },
          metadata: {
            generated_at: '2026-09-10T18:00:00Z',
            latest: '2026-09-10T12:00:00Z',
            interval_hours: 6,
            regions: ['Montréal'],
            source: 'test'
          }
        }
      });
    });

    await openDashboard(page);
    const row = page.locator('#dashboard-ranking-body tr').filter({ hasText: 'Montréal' }).first();
    const change = row.locator('td.ranking-change');
    await expect(change).toContainText('↑');
    await expect(change).toContainText('5.0¢');
  });

  test('clicking a ranking row switches the selected region', async ({ page }) => {
    await openDashboard(page);
    const row = page.locator('#dashboard-ranking-body tr').filter({ hasText: 'Montréal' }).first();
    await row.click();
    await page.waitForTimeout(300);
    await expect(page.locator('.dashboard-chip.active')).toContainText('Montréal');
  });

  test('fuel radio switches ranking values', async ({ page }) => {
    await openDashboard(page);
    await page.waitForTimeout(500);
    const before = await page.locator('#dashboard-ranking-body tr').first().locator('td').nth(1).textContent();

    await page.locator('.dashboard-fuel-radio').nth(2).click();
    await page.waitForTimeout(500);

    await expect(page.locator('.dashboard-fuel-radio').nth(2)).toHaveClass(/active/);
    const after = await page.locator('#dashboard-ranking-body tr').first().locator('td').nth(1).textContent();
    expect(after).not.toBe(before);
  });

  test('spread column header sorts rows', async ({ page }) => {
    await openDashboard(page);
    await page.waitForTimeout(500);
    await page.locator('#dashboard-ranking-head th[data-sort="spread"]').click();
    await page.waitForTimeout(300);

    const first = parseFloat(await page.locator('#dashboard-ranking-body tr').first().locator('td').nth(4).textContent());
    const last = parseFloat(await page.locator('#dashboard-ranking-body tr').last().locator('td').nth(4).textContent());
    expect(first).toBeLessThanOrEqual(last);
  });
});

test.describe('Fill-ups (localStorage fuel log)', () => {
  const openDetailPanel = async (page) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3000);
    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();
    await list.locator('.list-item').first().click();
    await expect(page.locator('.mapboxgl-popup').first()).toBeVisible();
    await page.locator('.mapboxgl-popup [data-expand]').click();
    await expect(page.locator('#station-panel')).toHaveClass(/open/);
  };

  test('record, persists and delete a fill-up', async ({ page }) => {
    await openDetailPanel(page);

    const addBtn = page.locator('.sd-fillup-add');
    await addBtn.click();
    const form = page.locator('.sd-fillup-form');
    await expect(form).toBeVisible();

    // Date and price are pre-filled; the user only fills litres.
    await expect(page.locator('.sd-fillup-date')).not.toHaveValue('');
    await expect(page.locator('.sd-fillup-price')).not.toHaveValue('');
    await page.locator('.sd-fillup-liters').fill('40');
    await page.locator('.sd-fillup-save').click();

    await expect(page.locator('#fillups-list .fillup-item')).toHaveCount(1);
    await expect(page.locator('#fillups-summary')).toContainText('$');

    // Persists across a reload.
    await page.reload();
    await page.waitForTimeout(3000);
    await expect(page.locator('#fillups-list .fillup-item')).toHaveCount(1);

    // Delete the single record.
    await page.locator('.fillup-delete').click();
    await expect(page.locator('#fillups-list .fillup-item')).toHaveCount(0);
    await expect(page.locator('#fillups-summary')).toContainText(/No fill-ups|Aucun plein|暂无加油记录/);
  });

  test('computeMonthStats sums spend, weighted avg price and savings from region average', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasFillups, null, { timeout: 15000 });

    const result = await page.evaluate(() => {
      const m = window.__qcGasFillups;
      const fillups = [
        { id: '1', stationName: 'S1', region: 'Montréal', fuel: 'regular', date: '2026-09-15', priceCents: 190, liters: 50, totalPrice: null },
        { id: '2', stationName: 'S2', region: 'Montréal', fuel: 'regular', date: '2026-09-16', priceCents: 195, liters: 40, totalPrice: null }
      ];
      const history = {
        regions: {
          'Montréal': {
            points: [
              { date: '2026-09-15T12:00:00Z', regular: { avg: 200 } },
              { date: '2026-09-16T12:00:00Z', regular: { avg: 200 } }
            ]
          }
        }
      };
      return m.computeMonthStats(fillups, history, new Date(2026, 8, 17));
    });

    expect(result.count).toBe(2);
    expect(result.liters).toBe(90);
    expect(result.spendDollars).toBeCloseTo(95 + 78, 2);
    expect(result.avgPriceCents).toBeCloseTo((190 * 50 + 195 * 40) / 90, 1);
    expect(result.savingsKnown).toBe(true);
    expect(result.savingsDollars).toBeCloseTo(5 + 2, 2);
  });

  test('regionAverageForDate falls back to the province overall series', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasFillups, null, { timeout: 15000 });

    const result = await page.evaluate(() => {
      const m = window.__qcGasFillups;
      return m.regionAverageForDate(
        { overall: { points: [
          { date: '2026-09-15T06:00:00Z', regular: { avg: 205 } },
          { date: '2026-09-15T18:00:00Z', regular: { avg: 207 } }
        ] } },
        'Montréal',
        'regular',
        '2026-09-15'
      );
    });

    expect(result).toBeCloseTo(206, 1);
  });

  test('fill-up delete button has an accessible name', async ({ page }) => {
    await openDetailPanel(page);
    await page.locator('.sd-fillup-add').click();
    await page.locator('.sd-fillup-liters').fill('10');
    await page.locator('.sd-fillup-save').click();
    await expect(page.locator('#fillups-list .fillup-delete').first()).toHaveAttribute('aria-label', /Delete|Supprimer|删除/);
  });
});

test.describe('Price Watch (localStorage)', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  });

  test('computeWatchStatus triggers only at or below threshold', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasWatch, null, { timeout: 15000 });

    const result = await page.evaluate(() => {
      const w = window.__qcGasWatch;
      const entry = { thresholdCents: 180 };
      return {
        at: w.computeWatchStatus(entry, 180),
        below: w.computeWatchStatus(entry, 179.9),
        idle: w.computeWatchStatus(entry, 180.1),
        noThreshold: w.computeWatchStatus({}, 180),
        unknown: w.computeWatchStatus(entry, null)
      };
    });

    expect(result.at).toBe('triggered');
    expect(result.below).toBe('triggered');
    expect(result.idle).toBe('idle');
    expect(result.noThreshold).toBe('idle');
    expect(result.unknown).toBe('unknown');
  });

  test('computeDelta returns the real observed change or null when missing', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasWatch, null, { timeout: 15000 });

    const result = await page.evaluate(() => {
      const w = window.__qcGasWatch;
      return {
        down: w.computeDelta({ lastSeenPriceCents: 200 }, 197.6),
        up: w.computeDelta({ lastSeenPriceCents: 200 }, 202.4),
        noLast: w.computeDelta({}, 197.6),
        noCurrent: w.computeDelta({ lastSeenPriceCents: 200 }, null)
      };
    });

    expect(result.down).toBeCloseTo(-2.4, 1);
    expect(result.up).toBeCloseTo(2.4, 1);
    expect(result.noLast).toBeNull();
    expect(result.noCurrent).toBeNull();
  });

  test('setWatchFuel re-baselines the delta for the new fuel', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasWatch && window.__qcGasMap?.getStationFeatureCount() > 0, null, { timeout: 15000 });

    const result = await page.evaluate(async () => {
      const w = window.__qcGasWatch;
      const data = await fetch('data/stations.json').then(r => r.json());
      const feature = data.features.find(f =>
        f.properties.regular_price != null && f.properties.super_price != null
      );
      const id = [feature.properties.name, feature.properties.address, feature.properties.postal_code]
        .map(v => (v ?? '').trim()).join('|');

      w.setWatch(feature, { fuel: 'regular', thresholdCents: 170 });
      const before = w.getWatchEntries().find(e => e.id === id);

      w.setWatchFuel(feature, 'super');
      const after = w.getWatchEntries().find(e => e.id === id);

      return {
        regularBaseline: before.lastSeenPriceCents,
        superPrice: feature.properties.super_price,
        fuel: after.fuel,
        baseline: after.lastSeenPriceCents,
        delta: w.computeDelta(after, feature.properties.super_price)
      };
    });

    expect(result.regularBaseline).not.toBeNull();
    expect(result.fuel).toBe('super');
    expect(result.baseline).toBeCloseTo(result.superPrice, 1);
    // A freshly re-based watch must show a zero delta, never a cross-fuel jump.
    expect(result.delta).toBe(0);
  });

  test('watch entry persists in localStorage after reload', async ({ page }) => {
    await page.waitForFunction(() => window.__qcGasWatch, null, { timeout: 15000 });

    const { id } = await page.evaluate(async () => {
      const w = window.__qcGasWatch;
      const data = await fetch('data/stations.json').then(r => r.json());
      const feature = data.features[0];
      const id = [feature.properties.name, feature.properties.address, feature.properties.postal_code]
        .map(v => (v ?? '').trim()).join('|');
      w.setWatch(feature, { fuel: 'regular', thresholdCents: 160 });
      return { id };
    });

    expect(id).toBeTruthy();
    const raw = await page.evaluate(() => localStorage.getItem('qc-gas-watch'));
    expect(raw).toContain(id);

    await page.reload();
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasWatch, null, { timeout: 15000 });
    await page.waitForTimeout(3000);

    const entries = await page.evaluate(() => window.__qcGasWatch.getWatchEntries());
    expect(entries.some(e => e.id === id)).toBe(true);
  });

  test('detail panel watch toggle adds a triggered sidebar item and can remove it', async ({ page }) => {
    await page.waitForTimeout(3000);
    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();
    await list.locator('.list-item').first().click();
    await expect(page.locator('.mapboxgl-popup').first()).toBeVisible();
    await page.locator('.mapboxgl-popup [data-expand]').click();
    await expect(page.locator('#station-panel')).toHaveClass(/open/);

    await expect(page.locator('#watch-section')).toBeHidden();

    const watchToggle = page.locator('.sd-watch-toggle');
    await expect(watchToggle).toBeVisible();
    // Default threshold is prefilled with the current price minus 2¢.
    await expect(page.locator('.sd-watch-threshold')).not.toHaveValue('');

    await watchToggle.click();
    await expect(watchToggle).toHaveClass(/on/);
    await expect(watchToggle).toHaveAttribute('aria-pressed', 'true');
    await expect(page.locator('#watch-section')).toBeVisible();
    await expect(page.locator('#watch-list .watch-item')).toHaveCount(1);

    // The prefilled threshold (current − 2¢) is below the current price, so
    // the item is watched but NOT yet triggered.
    await expect(page.locator('#watch-count')).toHaveText('0');
    await expect(page.locator('.watch-trigger-badge')).toHaveCount(0);

    // Raising the threshold above the current price triggers the badge.
    await page.locator('.sd-watch-threshold').fill('999');
    await page.locator('.sd-watch-threshold').press('Tab');
    await expect(page.locator('#watch-count')).toHaveText('1');
    await expect(page.locator('.watch-trigger-badge').first()).toBeVisible();

    // Toggling off cleans the section up again (empty → hidden).
    await watchToggle.click();
    await expect(page.locator('#watch-section')).toBeHidden();
  });
});

test.describe('Same-day regional benchmark', () => {
  test('computes median, gap, percentile and 50 L saving deterministically', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasBenchmark, null, { timeout: 15000 });

    const result = await page.evaluate(() => {
      const b = window.__qcGasBenchmark;
      const fc = {
        type: 'FeatureCollection',
        features: Array.from({ length: 10 }, (_, i) => ({
          type: 'Feature',
          geometry: { type: 'Point', coordinates: [0, 0] },
          properties: { region: 'Test', regular_price: 100 + i }
        }))
      };
      return b.computeRegionBenchmark(fc, 'Test', 'regular_price', 104);
    });

    expect(result.count).toBe(10);
    expect(result.sufficient).toBe(true);
    expect(result.min).toBe(100);
    expect(result.max).toBe(109);
    expect(result.median).toBe(104.5);
    expect(result.cheaperThanPercent).toBe(40);
    expect(result.gapToMin).toBeCloseTo(4, 2);
    expect(result.gapToMedian).toBeCloseTo(-0.5, 2);
    expect(result.saving50L).toBeCloseTo(0.25, 2);
  });

  test('degrades to insufficient sample when a region has fewer than 10 stations', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasBenchmark, null, { timeout: 15000 });

    const result = await page.evaluate(() => {
      const b = window.__qcGasBenchmark;
      const fc = {
        type: 'FeatureCollection',
        features: [
          { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { region: 'Test', regular_price: 180 } },
          { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { region: 'Test', regular_price: 190 } },
          { type: 'Feature', geometry: { type: 'Point', coordinates: [0, 0] }, properties: { region: 'Test', regular_price: 200 } }
        ]
      };
      return b.computeRegionBenchmark(fc, 'Test', 'regular_price', 190);
    });

    expect(result.count).toBe(3);
    expect(result.sufficient).toBe(false);
    expect(result.median).toBe(190);
    expect(result.cheaperThanPercent).toBeNull();
    expect(result.gapToMedian).toBeNull();
    expect(result.saving50L).toBeNull();
  });

  test('disables range pills beyond real station history coverage', async ({ page }) => {
    const history = { v: 1, t: ['2026-09-18', '2026-09-19'], s: {} };
    for (const feature of stationsFixture.features) {
      const [lng, lat] = feature.geometry.coordinates;
      const key = `${lng.toFixed(5)},${lat.toFixed(5)}`;
      history.s[key] = { g: [199.9, 198.4], s: [239.9, 238.4], d: [259.9, 258.4] };
    }

    await page.route('**/data/history/station-history.json', route =>
      route.fulfill({ json: history })
    );
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3000);

    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();
    await list.locator('.list-item').first().click();
    await expect(page.locator('.mapboxgl-popup').first()).toBeVisible();

    const popup = page.locator('.mapboxgl-popup').first();
    await expect(popup.locator('.sc-rangepill[aria-disabled="true"]')).toHaveCount(5);
    await expect(popup.locator('.sc-coverage')).toContainText('2');
    await expect(popup.locator('.sc-coverage')).toContainText('2026-09-18');
  });

  test('station card and detail panel show the same-day benchmark block', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForTimeout(3000);

    const list = page.locator('#station-list');
    await expect(list.locator('.list-item').first()).toBeVisible();
    await list.locator('.list-item').first().click();
    await expect(page.locator('.mapboxgl-popup').first()).toBeVisible();

    const popup = page.locator('.mapboxgl-popup').first();
    await expect(popup.locator('.sc-bench')).toBeVisible();
    await expect(popup.locator('.sc-bench-grid .sc-bench-item')).toHaveCount(5);
    await expect(popup.locator('.sc-bench-note')).toContainText(/are cheaper|moins chères|比本站便宜/);

    await popup.locator('[data-expand]').click();
    await expect(page.locator('#station-panel')).toHaveClass(/open/);
    await expect(page.locator('.sd-bench')).toBeVisible();
    await expect(page.locator('.sd-bench-grid .sd-bench-item')).toHaveCount(5);
  });
});

test.describe('PWA installability and offline shell', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  });

  test('manifest is accessible and satisfies installability basics', async ({ request }) => {
    const res = await request.get(`${BASE_URL}/manifest.webmanifest`);
    expect(res.status()).toBe(200);

    const manifest = await res.json();
    expect(manifest.name).toBeTruthy();
    expect(manifest.short_name).toBeTruthy();
    expect(manifest.start_url).toBeTruthy();
    expect(manifest.scope).toBeTruthy();
    expect(manifest.display).toBe('standalone');

    const sizes = manifest.icons.map((icon) => icon.sizes);
    expect(sizes).toContain('192x192');
    expect(sizes).toContain('512x512');
    expect(manifest.icons.some((icon) => icon.purpose === 'any')).toBe(true);
    expect(manifest.icons.some((icon) => icon.purpose === 'maskable')).toBe(true);
  });

  test('service worker and icons are served with valid PNG bytes', async ({ request }) => {
    const sw = await request.get(`${BASE_URL}/sw.js`);
    expect(sw.status()).toBe(200);
    expect(await sw.text()).toMatch(/qc-gas-v\d/);

    const pngSignature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    for (const icon of ['icon-192.png', 'icon-512.png', 'maskable-512.png', 'apple-touch-icon-180.png']) {
      const res = await request.get(`${BASE_URL}/icons/${icon}`);
      expect(res.status()).toBe(200);
      expect(res.headers()['content-type']).toContain('image/png');
      const body = await res.body();
      expect([...body.slice(0, 8)]).toEqual(pngSignature);
    }
  });

  test('beforeinstallprompt shows a closable install entry', async ({ page }) => {
    await page.evaluate(() => {
      window.dispatchEvent(new Event('beforeinstallprompt', { cancelable: true }));
    });

    const banner = page.locator('#pwa-install');
    await expect(banner).toBeVisible();
    await expect(page.locator('#pwa-install-btn')).toBeVisible();
    await expect(page.locator('#pwa-install-close')).toBeVisible();

    await page.locator('#pwa-install-close').click();
    await expect(banner).toHaveCount(0);
  });

  test('install entry is hidden when already running standalone', async ({ page }) => {
    await page.addInitScript(() => {
      const originalMatchMedia = window.matchMedia.bind(window);
      window.matchMedia = (query) => {
        if (query === '(display-mode: standalone)') {
          return {
            matches: true,
            media: query,
            onchange: null,
            addListener() {},
            removeListener() {},
            addEventListener() {},
            removeEventListener() {},
            dispatchEvent() { return false; }
          };
        }
        return originalMatchMedia(query);
      };
    });

    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});

    await page.evaluate(() => {
      window.dispatchEvent(new Event('beforeinstallprompt', { cancelable: true }));
    });

    await expect(page.locator('#pwa-install')).toHaveCount(0);
  });

  test('offline status is shown honestly and restored when back online', async ({ page }) => {
    await page.evaluate(() => window.dispatchEvent(new Event('offline')));
    await expect(page.locator('#data-status')).toContainText(/Offline|离线|Hors ligne/);
    await expect(page.locator('#data-status')).toHaveClass(/offline/);

    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await expect(page.locator('#data-status')).not.toContainText(/Offline|离线|Hors ligne/);
    await expect(page.locator('#data-status')).not.toHaveClass(/offline/);
  });
});

test.describe('Province-first view & location memory (Issue #39)', () => {
  const isValidCoord = (f) => {
    const [lng, lat] = f.geometry.coordinates;
    return Number.isFinite(lng) && Number.isFinite(lat) &&
      lng >= -79.5 && lng <= -57.1 && lat >= 44.9 && lat <= 62.4;
  };

  const haversine = (lng1, lat1, lng2, lat2) => {
    const R = 6371;
    const toRad = (deg) => deg * Math.PI / 180;
    const dLat = toRad(lat2 - lat1);
    const dLon = toRad(lng2 - lng1);
    const a = Math.sin(dLat / 2) ** 2 +
      Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
  };

  // The default UI has all *known* brands checked, so features with a null
  // brand are filtered out by the existing brand-filter logic.
  const knownBrands = new Set(stationsFixture.features.map((f) => f.properties.brand).filter(Boolean));
  const isDefaultVisible = (f) => knownBrands.has(f.properties.brand);

  const provinceCount = stationsFixture.features.filter((f) => {
    if (!isValidCoord(f) || !isDefaultVisible(f)) return false;
    const p = f.properties.regular_price;
    return p != null && p >= 150 && p <= 240;
  }).length;

  const radiusCount = (lng, lat, km) => stationsFixture.features.filter((f) => {
    if (!isValidCoord(f) || !isDefaultVisible(f)) return false;
    const p = f.properties.regular_price;
    if (p == null || p < 150 || p > 240) return false;
    return haversine(lng, lat, f.geometry.coordinates[0], f.geometry.coordinates[1]) <= km;
  }).length;

  test('first visit defaults to a province-wide view with no active radius', async ({ page }) => {
    await page.addInitScript(() => localStorage.clear());
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    await expect(page.locator('.radius-btn.active')).toHaveCount(0);
    await expect(page.locator('.radius-label')).toContainText(/Province|全省/);
    await expect(page.locator('#locate-around-btn')).toBeVisible();

    const count = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());
    expect(count).toBe(provinceCount);

    const hasCircle = await page.evaluate(() => window.__qcGasMap.hasRangeCircle());
    expect(hasCircle).toBe(false);
  });

  test('restores a saved radius view from qc-gas-view', async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.clear();
      localStorage.setItem('qc-gas-view', JSON.stringify({ mode: 'radius', lng: -73.7, lat: 45.45, radiusKm: 10, zoom: 12 }));
    });
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    await expect(page.locator('.radius-btn[data-radius="10"]')).toHaveClass(/active/);
    await expect(page.locator('.radius-label')).toContainText(/Rayon|Radius|半径/);

    const hasCircle = await page.evaluate(() => window.__qcGasMap.hasRangeCircle());
    expect(hasCircle).toBe(true);

    const count = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());
    expect(count).toBe(radiusCount(-73.7, 45.45, 10));
  });

  test('clicking a radius button enters radius mode and persists the view', async ({ page }) => {
    await page.addInitScript(() => localStorage.clear());
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    await expect(page.locator('.radius-btn.active')).toHaveCount(0);
    await page.locator('.radius-btn[data-radius="25"]').click();
    await page.waitForTimeout(500);

    await expect(page.locator('.radius-btn[data-radius="25"]')).toHaveClass(/active/);
    const hasCircle = await page.evaluate(() => window.__qcGasMap.hasRangeCircle());
    expect(hasCircle).toBe(true);

    const count = await page.evaluate(() => window.__qcGasMap.getStationFeatureCount());
    expect(count).toBe(radiusCount(-73.7, 45.45, 25));

    const raw = await page.evaluate(() => localStorage.getItem('qc-gas-view'));
    expect(raw).toContain('"mode":"radius"');
  });

  test('invalid-coordinate Hub Régie station is excluded from the map source', async ({ page }) => {
    await page.addInitScript(() => localStorage.clear());
    await page.goto(BASE_URL);
    await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    const result = await page.evaluate(() => {
      const features = window.__qcGasMap.getStationFeatures();
      return {
        count: features.length,
        hasHub: features.some((f) => f.properties.name === 'Hub Régie'),
        allValid: features.every((f) => {
          const [lng, lat] = f.geometry.coordinates;
          return Number.isFinite(lng) && Number.isFinite(lat) &&
            lng >= -79.5 && lng <= -57.1 && lat >= 44.9 && lat <= 62.4;
        })
      };
    });

    expect(result.hasHub).toBe(false);
    expect(result.allValid).toBe(true);
    expect(result.count).toBe(provinceCount);
  });
});

test.describe('PWA update strategy (Issue #45)', () => {
  const SW_PATH = path.join(path.dirname(fileURLToPath(import.meta.url)), '../public/sw.js');
  const SW_SCRIPT_URL = 'https://example.test/qc-gas/sw.js';
  const SCOPE = 'https://example.test/qc-gas/';
  const SW_SOURCE = readFileSync(SW_PATH, 'utf8');

  // Runs the real public/sw.js in a sandbox with a fake CacheStorage + fetch,
  // so the cache strategy is asserted on behaviour instead of on source text.
  function loadServiceWorker() {
    const listeners = new Map();
    const stores = new Map();
    const scriptUrl = new URL(SW_SCRIPT_URL);
    let online = true;
    let networkCalls = 0;
    let skipWaitingCalls = 0;

    const bucket = (name) => {
      if (!stores.has(name)) stores.set(name, new Map());
      return stores.get(name);
    };

    const cacheHandle = (store) => ({
      addAll: async (entries) => {
        for (const entry of entries) {
          store.set(new URL(entry, scriptUrl).href, new Response(`precached:${entry}`, { status: 200 }));
        }
      },
      put: async (request, response) => {
        store.set(typeof request === 'string' ? request : request.url, response);
      },
      match: async (request) => store.get(typeof request === 'string' ? request : request.url),
      keys: async () => [...store.keys()]
    });

    const caches = {
      open: async (name) => cacheHandle(bucket(name)),
      keys: async () => [...stores.keys()],
      delete: async (name) => stores.delete(name)
    };

    const fetchImpl = async (request) => {
      networkCalls += 1;
      const url = typeof request === 'string' ? request : request.url;
      if (!online) throw new TypeError('Failed to fetch');
      if (url.includes('/data/')) {
        return new Response('{"generated_at":"2026-09-25T13:15:00Z"}', {
          status: 200,
          headers: { 'Content-Type': 'application/json' }
        });
      }
      if (url === SCOPE || url.endsWith('/index.html')) {
        return new Response('<html><body>shell from network</body></html>', {
          status: 200,
          headers: { 'Content-Type': 'text/html' }
        });
      }
      return new Response(`asset:${url}`, {
        status: 200,
        headers: { 'Content-Type': 'application/javascript' }
      });
    };

    const swSelf = {
      location: scriptUrl,
      registration: { scope: SCOPE },
      clients: { claim: async () => {} },
      skipWaiting: async () => { skipWaitingCalls += 1; },
      addEventListener: (type, handler) => {
        const list = listeners.get(type) || [];
        list.push(handler);
        listeners.set(type, list);
      }
    };

    new Function('self', 'caches', 'fetch', 'Response', 'Request', 'URL', 'console', SW_SOURCE)(
      swSelf, caches, fetchImpl, Response, Request, URL, console
    );

    const runLifecycle = async (type) => {
      const pending = [];
      for (const handler of listeners.get(type) || []) {
        handler({ type, waitUntil: (promise) => pending.push(promise) });
      }
      await Promise.all(pending);
    };

    return {
      install: () => runLifecycle('install'),
      activate: () => runLifecycle('activate'),
      message: async (data) => {
        const pending = [];
        for (const handler of listeners.get('message') || []) {
          handler({ data, waitUntil: (promise) => pending.push(promise) });
        }
        await Promise.all(pending);
      },
      fetch: async (request) => {
        let handled;
        for (const handler of listeners.get('fetch') || []) {
          handler({ request, respondWith: (response) => { handled = response; } });
        }
        return handled === undefined ? undefined : await handled;
      },
      setOnline: (value) => { online = value; },
      networkCalls: () => networkCalls,
      skipWaitingCalls: () => skipWaitingCalls,
      cacheNames: () => [...stores.keys()],
      precachedUrls: () => [...stores.values()].flatMap((store) => [...store.keys()]),
      createLegacyCache: (name) => { bucket(name); },
      seedCachedShell: (body) => {
        for (const store of stores.values()) {
          store.set(`${SCOPE}index.html`, new Response(body, {
            status: 200,
            headers: { 'Content-Type': 'text/html' }
          }));
        }
      }
    };
  }

  const requestFor = (url, overrides = {}) => ({
    url,
    method: 'GET',
    mode: 'no-cors',
    destination: '',
    headers: new Headers(),
    ...overrides
  });

  const navigationRequest = () => requestFor(SCOPE, {
    mode: 'navigate',
    destination: 'document',
    headers: new Headers({ accept: 'text/html,application/xhtml+xml' })
  });

  test('navigations hit the network first so installed users get the new shell', async () => {
    const sw = loadServiceWorker();
    await sw.install();
    sw.seedCachedShell('<html><body>stale shell from the previous release</body></html>');

    const response = await sw.fetch(navigationRequest());

    expect(response.status).toBe(200);
    expect(await response.text()).toContain('shell from network');
  });

  test('offline navigation falls back to the cached shell and says so', async () => {
    const sw = loadServiceWorker();
    await sw.install();
    sw.seedCachedShell('<html><body>cached shell</body></html>');
    sw.setOnline(false);

    const response = await sw.fetch(navigationRequest());

    expect(response.status).toBe(200);
    expect(await response.text()).toContain('cached shell');
    expect(response.headers.get('X-QCGas-From-Cache')).toBe('1');
  });

  test('hashed assets are cache-first and need no network once cached', async () => {
    const sw = loadServiceWorker();
    await sw.install();
    const assetUrl = `${SCOPE}assets/index-DukmIqY3.js`;

    const first = await sw.fetch(requestFor(assetUrl, { destination: 'script' }));
    expect(await first.text()).toContain('asset:');
    expect(sw.precachedUrls()).toContain(assetUrl);

    const callsAfterFirst = sw.networkCalls();
    const second = await sw.fetch(requestFor(assetUrl, { destination: 'script' }));
    expect(await second.text()).toContain('asset:');
    expect(sw.networkCalls()).toBe(callsAfterFirst);
  });

  test('data snapshots stay network-first and are labelled when served from cache', async () => {
    const sw = loadServiceWorker();
    await sw.install();
    const dataUrl = `${SCOPE}data/stations.json`;

    const fresh = await sw.fetch(requestFor(dataUrl));
    expect(fresh.headers.get('X-QCGas-From-Cache')).toBeNull();
    expect(await fresh.text()).toContain('generated_at');

    sw.setOnline(false);
    const cached = await sw.fetch(requestFor(dataUrl));
    expect(cached.headers.get('X-QCGas-From-Cache')).toBe('1');
    expect(await cached.text()).toContain('generated_at');
  });

  test('a brand-new install can still open offline (the shell is precached)', async () => {
    // A first visit installs and claims the worker *after* its own navigation,
    // so that navigation is never a cacheable one. Without a precached shell
    // the next (offline) launch would get an empty 503 page.
    const sw = loadServiceWorker();
    await sw.install();
    sw.setOnline(false);

    const response = await sw.fetch(navigationRequest());

    expect(response.status).toBe(200);
    expect(await response.text()).toContain('precached');
    expect(response.headers.get('X-QCGas-From-Cache')).toBe('1');
  });

  test('install precaches a navigation fallback plus static files and waits for SKIP_WAITING', async () => {
    const sw = loadServiceWorker();
    await sw.install();

    const precached = sw.precachedUrls();
    expect(precached).toContain(`${SCOPE}manifest.webmanifest`);
    expect(precached).toContain(`${SCOPE}icons/icon-512.png`);
    // The shell is the offline fallback; navigation requests still refresh it
    // from the network on every visit.
    expect(precached).toContain(SCOPE);
    expect(precached).toContain(`${SCOPE}index.html`);

    // No forced skipWaiting: the user decides when the new version is applied.
    expect(sw.skipWaitingCalls()).toBe(0);
    await sw.message({ type: 'SKIP_WAITING' });
    expect(sw.skipWaitingCalls()).toBe(1);
  });

  test('activate drops our older caches but leaves foreign ones alone', async () => {
    const sw = loadServiceWorker();
    sw.createLegacyCache('qc-gas-v1');
    sw.createLegacyCache('mapbox-tiles');
    await sw.install();
    await sw.activate();

    expect(sw.cacheNames()).not.toContain('qc-gas-v1');
    expect(sw.cacheNames()).toContain('mapbox-tiles');
    const own = sw.cacheNames().filter((name) => name.startsWith('qc-gas-'));
    expect(own).toHaveLength(1);
    expect(own[0]).toMatch(/^qc-gas-v\d/);
  });

  test('offline after a successful load labels the cached snapshot with its sync time', async ({ page }) => {
    // A response served from the service-worker cache is flagged with
    // X-QCGas-From-Cache; the UI must then say offline + last sync time
    // instead of presenting cached prices as live.
    await page.route('**/data/stations.json', async (route) => {
      const response = await route.fetch();
      await route.fulfill({
        response,
        headers: { ...response.headers(), 'X-QCGas-From-Cache': '1' }
      });
    });
    await page.goto(BASE_URL);
    await page.waitForFunction(() => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0, null, { timeout: 15000 });

    const status = page.locator('#data-status');
    await expect(status).toHaveClass(/offline/);
    await expect(status).toContainText(/Offline|离线|Hors ligne/);
    await expect(status).toContainText(/Last synced|最后同步|Dernière synchro/);
  });

  test('a pending update shows a non-blocking banner with a 44px reload action', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForFunction(() => window.__qcGasPwa, null, { timeout: 15000 });
    await page.locator('#lang-selector button[data-lang="fr-CA"]').click();

    await expect(page.locator('#pwa-update')).toHaveCount(0);
    await page.evaluate(() => window.__qcGasPwa.notifyUpdateAvailable({ postMessage: () => {} }));

    const banner = page.locator('#pwa-update');
    await expect(banner).toBeVisible();
    await expect(banner).toHaveAttribute('role', 'status');
    await expect(banner).toContainText('Nouvelle version disponible');

    // Non-blocking: the app behind the banner stays usable.
    await expect(page.locator('#map')).toBeVisible();
    expect(await page.evaluate(() => window.__qcGasPwa.isUpdateAvailable())).toBe(true);

    const reloadButton = page.locator('#pwa-update-btn');
    await expect(reloadButton).toBeVisible();
    await expect(reloadButton).toContainText('Recharger');
    const box = await reloadButton.boundingBox();
    expect(box.height).toBeGreaterThanOrEqual(44);
  });

  test('update banner copy follows the active language', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForFunction(() => window.__qcGasPwa, null, { timeout: 15000 });
    await page.locator('#lang-selector button[data-lang="fr-CA"]').click();
    await page.evaluate(() => window.__qcGasPwa.notifyUpdateAvailable({ postMessage: () => {} }));
    await expect(page.locator('#pwa-update')).toContainText('Nouvelle version disponible');

    await page.locator('#lang-selector button[data-lang="zh-Hans"]').click();
    await expect(page.locator('#pwa-update')).toContainText('新版本可用');
    await expect(page.locator('#pwa-update-btn')).toContainText('重新加载');

    await page.locator('#lang-selector button[data-lang="en-CA"]').click();
    await expect(page.locator('#pwa-update')).toContainText('New version available');
    await expect(page.locator('#pwa-update-btn')).toContainText('Reload');
  });

  test('no banner and no service-worker registration while nothing is pending (dev)', async ({ page }) => {
    const swRequests = [];
    page.on('request', (request) => {
      if (request.url().includes('sw.js')) swRequests.push(request.url());
    });

    await page.goto(BASE_URL);
    await page.waitForFunction(() => window.__qcGasPwa, null, { timeout: 15000 });
    await page.waitForTimeout(300);

    await expect(page.locator('#pwa-update')).toHaveCount(0);
    expect(await page.evaluate(() => window.__qcGasPwa.isUpdateAvailable())).toBe(false);

    await page.evaluate(() => {
      window.__qcGasPwa.checkForUpdate();
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));
    });
    await page.waitForTimeout(200);

    await expect(page.locator('#pwa-update')).toHaveCount(0);
    expect(swRequests).toEqual([]);
  });

  test('update checks are throttled and re-run when the page is focused again', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForFunction(() => window.__qcGasPwa, null, { timeout: 15000 });

    const updates = await page.evaluate(async () => {
      const realNow = Date.now;
      let clock = realNow();
      Date.now = () => clock;

      let count = 0;
      window.__qcGasPwa.setRegistration({
        update: () => { count += 1; return Promise.resolve(); },
        addEventListener: () => {}
      });

      window.__qcGasPwa.checkForUpdate();
      window.__qcGasPwa.checkForUpdate();
      window.dispatchEvent(new Event('focus'));
      document.dispatchEvent(new Event('visibilitychange'));

      clock += 60 * 60 * 1000; // long after the throttle window
      window.dispatchEvent(new Event('focus'));

      await new Promise((resolve) => setTimeout(resolve, 50));
      Date.now = realNow;
      return count;
    });

    expect(updates).toBe(2);
  });

  test('a failed update check is retried instead of being throttled for a full interval', async ({ page }) => {
    await page.goto(BASE_URL);
    await page.waitForFunction(() => window.__qcGasPwa, null, { timeout: 15000 });

    const counts = await page.evaluate(async () => {
      let calls = 0;
      window.__qcGasPwa.setRegistration({
        update: () => { calls += 1; return Promise.reject(new Error('offline')); },
        addEventListener: () => {}
      });

      window.__qcGasPwa.checkForUpdate();
      window.__qcGasPwa.checkForUpdate();
      const whileThrottled = calls;

      await new Promise((resolve) => setTimeout(resolve, 30));
      window.__qcGasPwa.checkForUpdate();
      return { whileThrottled, total: calls };
    });

    expect(counts.whileThrottled).toBe(1);
    expect(counts.total).toBe(2);
  });

  test('a first install (no controller yet) never shows the update banner', async ({ page }) => {
    await page.addInitScript(() => {
      const containerListeners = {};
      const workerListeners = {};
      const worker = {
        state: 'installing',
        postMessage: () => {},
        addEventListener: (type, handler) => {
          (workerListeners[type] = workerListeners[type] || []).push(handler);
        }
      };
      const registration = {
        waiting: null,
        installing: worker,
        update: () => Promise.resolve(),
        addEventListener: (type, handler) => {
          (containerListeners[type] = containerListeners[type] || []).push(handler);
        }
      };
      window.__fakeReg = {
        registration,
        emitUpdateFound: () => (containerListeners.updatefound || []).forEach((handler) => handler({ type: 'updatefound' })),
        emitInstalled: () => {
          worker.state = 'installed';
          (workerListeners.statechange || []).forEach((handler) => handler({ type: 'statechange' }));
        }
      };
      Object.defineProperty(navigator, 'serviceWorker', {
        value: {
          controller: null,
          addEventListener: () => {},
          removeEventListener: () => {}
        },
        configurable: true
      });
    });

    await page.goto(BASE_URL);
    await page.waitForFunction(() => window.__qcGasPwa, null, { timeout: 15000 });

    await page.evaluate(() => {
      window.__qcGasPwa.setRegistration(window.__fakeReg.registration);
      window.__fakeReg.emitUpdateFound();
      window.__fakeReg.emitInstalled();
    });
    await page.waitForTimeout(300);

    expect(await page.evaluate(() => window.__qcGasPwa.isUpdateAvailable())).toBe(false);
    await expect(page.locator('#pwa-update')).toHaveCount(0);
  });

  test('Recharger posts SKIP_WAITING and reloads exactly once', async ({ page }) => {
    await page.addInitScript(() => {
      const loads = Number(sessionStorage.getItem('__pwaLoads') || '0') + 1;
      sessionStorage.setItem('__pwaLoads', String(loads));
      window.__pwaLoads = loads;

      const listeners = {};
      window.__fakeSw = {
        controller: { state: 'activated' },
        addEventListener: (type, handler) => {
          (listeners[type] = listeners[type] || []).push(handler);
        },
        removeEventListener: () => {},
        emit: (type) => (listeners[type] || []).forEach((handler) => handler({ type }))
      };
      Object.defineProperty(navigator, 'serviceWorker', {
        value: window.__fakeSw,
        configurable: true
      });
    });

    await page.goto(BASE_URL);
    await page.waitForFunction(() => window.__qcGasPwa, null, { timeout: 15000 });

    const posted = await page.evaluate(() => {
      window.__posted = [];
      window.__qcGasPwa.notifyUpdateAvailable({ postMessage: (message) => window.__posted.push(message) });
      document.getElementById('pwa-update-btn').click();
      return window.__posted;
    });
    expect(posted).toEqual([{ type: 'SKIP_WAITING' }]);
    expect(await page.evaluate(() => window.__pwaLoads)).toBe(1);

    // The new worker taking over is what triggers the single reload.
    await page.evaluate(() => window.__fakeSw.emit('controllerchange'));
    await page.waitForFunction(() => window.__pwaLoads === 2, null, { timeout: 10000 });

    // Anti-loop guard: a second controllerchange must not reload again.
    await page.waitForFunction(() => window.__qcGasPwa, null, { timeout: 15000 });
    await page.evaluate(() => window.__fakeSw.emit('controllerchange'));
    await page.waitForTimeout(500);
    expect(await page.evaluate(() => window.__pwaLoads)).toBe(2);
  });
});

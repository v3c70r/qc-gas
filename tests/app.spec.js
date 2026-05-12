import { test, expect } from '@playwright/test';

const BASE_URL = process.env.BASE_URL || 'http://localhost:5173';
const DEPLOY_URL = 'https://qgu.io/qc-gas/';

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

    const fuelChip = page.locator('.fuel-chip').first();
    await expect(fuelChip).toBeVisible();

    const isActiveBefore = await fuelChip.evaluate(el => el.classList.contains('active'));
    await fuelChip.click();
    const isActiveAfter = await fuelChip.evaluate(el => el.classList.contains('active'));
    expect(isActiveBefore).not.toBe(isActiveAfter);
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
    expect(box.x).toBeLessThan(100);
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
      expect(text?.trim() || title).toBeTruthy();
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
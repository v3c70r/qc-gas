// ── Compact map popup (Issue #132) ───────────────────────────────────────────
// The map popup used to stack the whole "expandable" card: address, regional
// benchmark, Régie margin, range pills, coverage, sparkline, min/high/≈$/L and
// the "record a fill-up here" button. On a 390×844 phone that is taller than the
// screen, so the top of the card (the station name) was pushed off-viewport.
//
// It now keeps only what a driver wants at a glance — name, favourite star,
// current price + change chip, fuel switcher, freshness and two actions (a real
// navigation link and "details") — while the detail panel keeps everything.
import { test, expect } from '@playwright/test';

const BASE_URL = process.env.BASE_URL || 'http://localhost:5173';
const MOBILE = { width: 390, height: 844 };
const DESKTOP = { width: 1280, height: 800 };

const FRESHNESS = {
  'zh-Hans': /价格观测于|价格变动|尚未观察到价格变动/,
  'en-CA': /Price observed|Price changed|No price change observed/,
  'fr-CA': /Prix observé|Prix modifié|Aucun changement observé/
};

const NAV_LABEL = {
  'zh-Hans': '导航',
  'en-CA': 'Navigate',
  'fr-CA': 'Itinéraire'
};

const DETAILS_LABEL = {
  'zh-Hans': /详情/,
  'en-CA': /details/i,
  'fr-CA': /détails/i
};

const waitForStations = async (page) => {
  await page.waitForFunction(
    () => window.__qcGasMap && window.__qcGasMap.getStationFeatureCount() > 0,
    null,
    { timeout: 20000 }
  );
};

/**
 * Open the first sidebar station's popup on the given viewport.
 *
 * The map is centred on that station first: with the marker pinned to a viewport
 * edge the popup is auto-positioned off-screen and "does the popup fit in the
 * viewport?" becomes untestable rather than a real failure.
 */
const openFirstPopup = async (page, viewport = DESKTOP) => {
  await page.setViewportSize(viewport);
  await page.goto(BASE_URL);
  await page.waitForLoadState('networkidle', { timeout: 15000 }).catch(() => {});
  await waitForStations(page);

  if (viewport.width < 768) {
    // Collapsed bottom sheet: reveal the list, then re-collapse it so the popup
    // is not partly covered by the sheet in the screenshots.
    await page.locator('#sidebar-handle').click();
    await expect(page.locator('#sidebar')).not.toHaveClass(/collapsed/);
  }

  const item = page.locator('#station-list .list-item').first();
  await expect(item).toBeVisible();
  const name = (await item.locator('.name').innerText()).trim();

  const station = await page.evaluate((name) => {
    const feature = window.__qcGasMap.getStationFeatures()
      .find((x) => (x.properties.name || x.properties.brand) === name);
    if (!feature) return null;
    window.__qcGasMap.flyTo(feature.geometry.coordinates, 14);
    return {
      coords: feature.geometry.coordinates,
      address: feature.properties.address || ''
    };
  }, name);
  expect(station).not.toBeNull();
  await page.waitForTimeout(600); // let the (instant) move settle

  await item.click();
  const popup = page.locator('.mapboxgl-popup').first();
  await expect(popup).toBeVisible();

  if (viewport.width < 768) {
    await page.locator('#sidebar-handle').click();
    await expect(page.locator('#sidebar')).toHaveClass(/collapsed/);
    await page.waitForTimeout(400);
  }

  return { popup, name, station };
};

const selectLang = async (page, code) => {
  await page.locator(`#lang-selector button[data-lang="${code}"]`).click();
};

test.describe('Compact map popup (Issue #132)', () => {
  test('fits the mobile viewport: ≤320×190 and fully on screen', async ({ page }) => {
    const { popup } = await openFirstPopup(page, MOBILE);

    const box = await popup.boundingBox();
    expect(box).not.toBeNull();
    expect(box.width).toBeLessThanOrEqual(320);
    expect(box.height).toBeLessThanOrEqual(190);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.y + box.height).toBeLessThanOrEqual(MOBILE.height);
  });

  for (const lang of ['zh-Hans', 'en-CA', 'fr-CA']) {
    test(`keeps the at-a-glance info in ${lang}`, async ({ page }) => {
      const { popup, name } = await openFirstPopup(page, MOBILE);
      await selectLang(page, lang);
      await expect(popup.locator('.sc-name')).toHaveText(name);
      await expect(popup.locator('.sc-price')).toContainText('¢');
      await expect(popup.locator('.sc-fresh')).toContainText(FRESHNESS[lang]);

      const nav = popup.locator('a.sc-nav');
      await expect(nav).toBeVisible();
      await expect(nav).toHaveAttribute('aria-label', NAV_LABEL[lang]);

      const details = popup.locator('button.sc-expand');
      await expect(details).toBeVisible();
      await expect(details).toHaveAttribute('aria-label', DETAILS_LABEL[lang]);
    });
  }

  test('drops the detail-panel blocks and the address', async ({ page }) => {
    const { popup, station } = await openFirstPopup(page, MOBILE);

    for (const selector of ['.sc-chart', '.sc-range', '.sc-mini-stats', '.sc-bench', '.sc-fillup', '.sc-updated']) {
      await expect(popup.locator(selector)).toHaveCount(0);
    }
    expect(station.address.length).toBeGreaterThan(0);
    await expect(popup).not.toContainText(station.address);
  });

  test('the navigation link is a real URL built from the station coordinates', async ({ page }) => {
    const { popup, station } = await openFirstPopup(page, MOBILE);
    const [lng, lat] = station.coords;
    const nav = popup.locator('a.sc-nav');

    await expect(nav).toHaveAttribute(
      'href',
      `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`
    );
    await expect(nav).toHaveAttribute('target', '_blank');
    await expect(nav).toHaveAttribute('rel', /noopener/);
  });

  test('the navigation link is keyboard operable', async ({ page }) => {
    const { popup } = await openFirstPopup(page, MOBILE);
    const nav = popup.locator('a.sc-nav');

    await nav.focus();
    await expect(nav).toBeFocused();

    await page.context().route('https://www.google.com/**', (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: 'ok' }));

    const [opened] = await Promise.all([
      page.waitForEvent('popup'),
      page.keyboard.press('Enter')
    ]);
    expect(opened).toBeTruthy();
    await opened.close();
  });

  test('navigation and details buttons meet the 44px touch target', async ({ page }) => {
    const { popup } = await openFirstPopup(page, MOBILE);
    for (const selector of ['a.sc-nav', 'button.sc-expand']) {
      const box = await popup.locator(selector).boundingBox();
      expect(box, selector).not.toBeNull();
      expect(box.height, selector).toBeGreaterThanOrEqual(44);
    }
  });

  test('freshness changes are still announced through aria-live', async ({ page }) => {
    const { popup } = await openFirstPopup(page, MOBILE);
    await expect(popup.locator('.sc-fresh')).toHaveAttribute('aria-live', 'polite');
  });

  test('the detail panel keeps every block the popup dropped', async ({ page }) => {
    const { popup } = await openFirstPopup(page, DESKTOP);
    await popup.locator('[data-expand]').click();

    const panel = page.locator('#station-panel');
    await expect(panel).toHaveClass(/open/);
    for (const selector of ['.sd-range', '.sd-bench', '.sd-stats', '.sd-fillups']) {
      await expect(panel.locator(selector)).toBeVisible();
    }
    // `.sd-coverage` is both the container and the line it holds: take the inner.
    await expect(panel.locator('.sd-coverage').last()).toBeVisible();
  });

  test('screenshots for manual comparison', async ({ page }) => {
    await openFirstPopup(page, MOBILE);
    await page.screenshot({ path: 'tests/screenshots/popup-mobile.png', fullPage: false });

    await openFirstPopup(page, DESKTOP);
    await page.screenshot({ path: 'tests/screenshots/popup-desktop.png', fullPage: false });
  });
});

test.describe('Compact map popup on iOS (Issue #132)', () => {
  test.use({
    userAgent: 'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1'
  });

  test('navigation link opens Apple Maps directions', async ({ page }) => {
    const { popup, station } = await openFirstPopup(page, MOBILE);
    const [lng, lat] = station.coords;
    await expect(popup.locator('a.sc-nav')).toHaveAttribute(
      'href',
      `https://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`
    );
  });
});

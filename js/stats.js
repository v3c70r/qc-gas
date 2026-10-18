import { map, currentStations, isRadiusMode, getReferencePoint, getEffectiveReferencePoint, rangeRadius, setRadiusMode, fitRegionBounds } from './map.js';
import { tf, t, getLanguage, onLanguageChange } from './i18n.js';
import { brandColor, brandAbbr } from './constants.js';
import { isFavorite, toggleFavorite, getFavoriteCount, subscribe, STAR_ICON } from './favorites.js';
import { searchFeatures, getSearchQuery, isSearchActive, refreshSearchSuggestions } from './search.js';

// The sidebar renders stations in pages so a big city (Montréal has ~500
// stations within 25 km) stays browsable without dumping everything at once.
const PAGE_SIZE = 30;
const LIST_STORAGE_KEY = 'qc-gas-list';

let favoritesOnly = false;
const storedListPreferences = loadListPreferences();
// 'price' (default) = favorites pinned first then price ascending;
// 'distance' = strict nearest-first. Persisted so the choice survives reloads.
let sortMode = storedListPreferences.sort;
// 'stations' (default: the province-wide top 30) or 'regions' (one summary row
// per region, issue #87). Opt-in only: the default view must not change.
let listView = storedListPreferences.view;
// Set when the user asks for the distance sort without a reference point, so
// the explanation stays visible until the location becomes available.
let distanceHintVisible = false;
let renderedStations = [];
let renderedCount = 0;
let cheapestVisiblePrice = Infinity;

function loadListPreferences() {
  const prefs = { sort: 'price', view: 'stations' };
  try {
    const parsed = JSON.parse(localStorage.getItem(LIST_STORAGE_KEY) || 'null');
    if (parsed && parsed.sort === 'distance') prefs.sort = 'distance';
    // Unknown / missing values (e.g. a pre-#87 backup, whose sanitizer drops
    // this field) fall back to the default view instead of failing.
    if (parsed && parsed.view === 'regions') prefs.view = 'regions';
  } catch {
    // Storage may be unavailable (private browsing); keep the defaults.
  }
  return prefs;
}

function persistListPreferences() {
  try {
    // Only non-default values are written, so the stored shape stays exactly
    // what pre-#87 builds produced (and read) — `view` appears only once the
    // user opts into the region summary.
    const prefs = { sort: sortMode };
    if (listView === 'regions') prefs.view = listView;
    localStorage.setItem(LIST_STORAGE_KEY, JSON.stringify(prefs));
  } catch {
    // Storage may be unavailable (private browsing); the choice still applies in-session.
  }
}

// Re-read the list preferences from localStorage. Used by the JSON restore flow
// (issue #57) so an import is reflected in the list without a page reload.
export function reloadListPreferences() {
  const prefs = loadListPreferences();
  sortMode = prefs.sort;
  listView = prefs.view;
  distanceHintVisible = false;
  renderSortControls();
  updateStationList();
  return sortMode;
}

function isDistanceSortAvailable() {
  return getReferencePoint() !== null;
}

export function haversineDistance(lng1, lat1, lng2, lat2) {
  const R = 6371;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lng2 - lng1);
  const a = Math.sin(dLat/2) * Math.sin(dLat/2) +
            Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) *
            Math.sin(dLon/2) * Math.sin(dLon/2);
  const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
  return R * c;
}
function toRad(deg) { return deg * Math.PI / 180; }

function getActiveFuel() {
  return document.querySelector('.fuel-filter:checked')?.value || 'regular';
}

function getActiveFuelPriceKey() {
  return getActiveFuel() + '_price';
}

function getFilterCriteria() {
  return {
    radiusKm: rangeRadius.value,
    radiusMode: isRadiusMode(),
    selectedRegion: document.getElementById('region-filter').value,
    priceMin: parseFloat(document.getElementById('min-price').value),
    priceMax: parseFloat(document.getElementById('max-price').value),
    priceKey: getActiveFuelPriceKey()
  };
}

// Shared by the map/list filter and the brand comparison so radius, region and
// price-range behaviour cannot drift between the two.
// When a search is active, location filters (radius and region) are lifted so
// users can search an address / postal code / city anywhere in the province.
// Brand, price-range and favorites filters still apply to keep results useful.
function matchesNonBrandFilters(feat, criteria, searchMode = false) {
  const props = feat.properties;
  const coords = feat.geometry.coordinates;
  if (!searchMode) {
    if (criteria.radiusMode) {
      const reference = getEffectiveReferencePoint();
      const distance = haversineDistance(reference[0], reference[1], coords[0], coords[1]);
      if (distance > criteria.radiusKm) return false;
    }
    if (criteria.selectedRegion && props.region !== criteria.selectedRegion) return false;
  }

  const fuelPrice = props[criteria.priceKey];
  if (fuelPrice === null) return false;
  if (fuelPrice < criteria.priceMin || fuelPrice > criteria.priceMax) return false;
  return true;
}

function filterStations() {
  // Stations load asynchronously; guard every early call path (language
  // change, favorites toggle, etc.) until the FeatureCollection is ready.
  if (!currentStations || !Array.isArray(currentStations.features)) return [];

  const criteria = getFilterCriteria();
  const selectedBrands = new Set();
  document.querySelectorAll('.brand-filter:checked').forEach(cb => selectedBrands.add(cb.value));

  const searching = isSearchActive();
  const baseFeatures = searching
    ? searchFeatures(getSearchQuery(), currentStations.features)
    : currentStations.features;

  return baseFeatures.filter(feat => {
    if (!matchesNonBrandFilters(feat, criteria, searching)) return false;
    if (selectedBrands.size === 0 || !selectedBrands.has(feat.properties.brand)) return false;
    return true;
  });
}

function filterStationsAsFeatureCollection() {
  return { type: 'FeatureCollection', features: filterStations() };
}

// Brand comparison ignores the brand checkboxes (they are the dimension being
// compared), but respects the other active filters (radius / region when not
// searching, and price range / active fuel always) so averages move with them.
function filterStationsForBrandComparison() {
  if (!currentStations || !Array.isArray(currentStations.features)) return [];
  const criteria = getFilterCriteria();
  const searching = isSearchActive();
  const baseFeatures = searching
    ? searchFeatures(getSearchQuery(), currentStations.features)
    : currentStations.features;
  return baseFeatures.filter(feat => matchesNonBrandFilters(feat, criteria, searching));
}

// The comparison baseline is the true province-wide average for the active
// fuel: full dataset, no radius / region / price-range / brand filtering.
function computeProvinceAverage(priceKey) {
  if (!currentStations || !Array.isArray(currentStations.features)) return null;
  let total = 0;
  let count = 0;
  currentStations.features.forEach(feat => {
    const price = feat.properties?.[priceKey];
    if (price !== null && price !== undefined) {
      total += price;
      count += 1;
    }
  });
  return count > 0 ? total / count : null;
}

function computeBrandComparison() {
  const priceKey = getActiveFuelPriceKey();
  const features = filterStationsForBrandComparison();
  const brands = {};

  features.forEach(feat => {
    const props = feat.properties;
    const brand = props.brand;
    const price = props[priceKey];
    if (!brand || price === null) return;

    if (!brands[brand]) brands[brand] = { sum: 0, count: 0 };
    brands[brand].sum += price;
    brands[brand].count += 1;
  });

  return {
    provinceAvg: computeProvinceAverage(priceKey),
    brands
  };
}

function updateBrandComparison() {
  const { provinceAvg, brands } = computeBrandComparison();

  document.querySelectorAll('.brand-filter-item').forEach(item => {
    const input = item.querySelector('.brand-filter');
    if (!input) return;
    const stats = brands[input.value];
    const countEl = item.querySelector('.brand-count');
    const avgEl = item.querySelector('.brand-avg');
    const diffEl = item.querySelector('.brand-diff');

    if (countEl) countEl.textContent = stats ? String(stats.count) : '0';

    if (avgEl) {
      avgEl.textContent = stats ? (stats.sum / stats.count).toFixed(1) + '¢' : '—';
    }

    if (diffEl) {
      if (stats && provinceAvg !== null) {
        const diff = (stats.sum / stats.count) - provinceAvg;
        diffEl.textContent = (diff > 0 ? '+' : '') + diff.toFixed(1) + '¢';
        diffEl.classList.toggle('diff-pos', diff > 0);
        diffEl.classList.toggle('diff-neg', diff <= 0);
      } else {
        diffEl.textContent = '—';
        diffEl.classList.remove('diff-pos', 'diff-neg');
      }
    }
  });
}

function getVisibleStations() {
  let filtered = filterStations();
  if (favoritesOnly) {
    filtered = filtered.filter(f => isFavorite(f));
  }
  return filtered;
}

function updateMapStations(filteredStations) {
  if (!map || !map.getSource) return;
  const source = map.getSource('stations');
  if (!source || typeof source.setData !== 'function') return;
  source.setData({ type: 'FeatureCollection', features: filteredStations });
}

function updateStats() {
  const filtered = getVisibleStations();
  updateMapStations(filtered);
  const stats = { regular: [], super: [], diesel: [] };

  filtered.forEach(feat => {
    const props = feat.properties;
    if (props.regular_price !== null) stats.regular.push(props.regular_price);
    if (props.super_price !== null) stats.super.push(props.super_price);
    if (props.diesel_price !== null) stats.diesel.push(props.diesel_price);
  });

  const countEl = document.getElementById('sidebar-station-count');
  countEl.textContent = tf('stations_count', { n: filtered.length });
  countEl.dataset.count = filtered.length;
  updateQuickStat('quick-regular', stats.regular);
  updateQuickStat('quick-super', stats.super);
  updateQuickStat('quick-diesel', stats.diesel);
  updateBrandComparison();
  updateLowestPriceHighlight(filtered);
  updateStationList(filtered);
  refreshSearchSuggestions();
  return filtered;
}

function updateQuickStat(elementId, prices) {
  const el = document.getElementById(elementId);
  if (prices.length === 0) {
    el.textContent = '—';
    el.classList.remove('lowest');
    return;
  }
  const min = Math.min(...prices);
  el.textContent = min.toFixed(1) + '¢';
  el.classList.add('lowest');
}

let pulseAnimationId = null;

function updateLowestPriceHighlight(filteredStations) {
  if (map.getLayer('lowest-price')) map.removeLayer('lowest-price');
  if (map.getLayer('lowest-price-border')) map.removeLayer('lowest-price-border');
  if (map.getSource('lowest-price')) map.removeSource('lowest-price');
  if (pulseAnimationId) { cancelAnimationFrame(pulseAnimationId); pulseAnimationId = null; }
  if (filteredStations.length === 0) return;

  const activeFuel = document.querySelector('.fuel-filter:checked')?.value || 'regular';
  const priceKey = activeFuel + '_price';

  let lowest = null;
  let lowestPrice = Infinity;
  filteredStations.forEach(feat => {
    const price = feat.properties[priceKey];
    if (price !== null && price < lowestPrice) { lowestPrice = price; lowest = feat; }
  });
  if (!lowest) return;

  map.addSource('lowest-price', { type: 'geojson', data: lowest });
  map.addLayer({ id: 'lowest-price', type: 'circle', source: 'lowest-price', paint: { 'circle-color': '#28a745', 'circle-radius': 10, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 } });
  map.addLayer({ id: 'lowest-price-border', type: 'circle', source: 'lowest-price', paint: { 'circle-color': '#28a745', 'circle-radius': 15, 'circle-opacity': 0.5, 'circle-stroke-color': '#ffffff', 'circle-stroke-width': 2 } });
  pulseLowestPrice();
}

function pulseLowestPrice() {
  let radius = 15, opacity = 0.5, growing = true;
  function animate() {
    if (growing) { radius += 1; opacity -= 0.02; if (radius >= 30 || opacity <= 0) growing = false; }
    else { radius -= 1; opacity += 0.02; if (radius <= 15 || opacity >= 0.5) growing = true; }
    map.setPaintProperty('lowest-price-border', 'circle-radius', radius);
    map.setPaintProperty('lowest-price-border', 'circle-opacity', opacity);
    pulseAnimationId = requestAnimationFrame(animate);
  }
  animate();
}

function stationDistance(reference, feat) {
  return haversineDistance(reference[0], reference[1], feat.geometry.coordinates[0], feat.geometry.coordinates[1]);
}

// 'price' keeps the historical semantics (favorites pinned, then price
// ascending). 'distance' is a strict nearest-first order: favorites are not
// pinned, so the closest station always wins.
function sortStationList(features, priceKey) {
  const sorted = [...features];
  const reference = getReferencePoint();
  if (sortMode === 'distance' && reference) {
    sorted.sort((a, b) => stationDistance(reference, a) - stationDistance(reference, b));
    return sorted;
  }
  sorted.sort((a, b) => {
    const aFav = isFavorite(a) ? 0 : 1;
    const bFav = isFavorite(b) ? 0 : 1;
    if (aFav !== bFav) return aFav - bFav;
    return (a.properties[priceKey] ?? Infinity) - (b.properties[priceKey] ?? Infinity);
  });
  return sorted;
}

function updateListCount(shown, total, key = 'shownCount') {
  const el = document.getElementById('stations-shown-count');
  if (!el) return;
  el.dataset.shown = String(shown);
  el.dataset.total = String(total);
  // Remembered so a language switch re-renders the same wording (stations vs
  // regions) instead of silently reverting to the station count.
  el.dataset.countKey = key;
  el.textContent = tf(key, { shown, total, n: shown });
}

function updateShowMoreButton() {
  const btn = document.getElementById('show-more-stations');
  if (!btn) return;
  const remaining = renderedStations.length - renderedCount;
  if (remaining <= 0) {
    btn.hidden = true;
    return;
  }
  btn.hidden = false;
  btn.textContent = tf('showMoreStations', { n: Math.min(PAGE_SIZE, remaining) });
}

// The distance sort is only meaningful with a reference point; without one the
// control refuses and explains itself instead of printing a column of “— km”.
function renderSortControls() {
  const container = document.getElementById('list-sort');
  if (!container) return;
  const available = isDistanceSortAvailable();
  if (available) distanceHintVisible = false;

  container.setAttribute('aria-label', t('listSortLabel'));
  container.querySelectorAll('.sort-btn').forEach(btn => {
    const active = btn.dataset.sort === sortMode;
    btn.classList.toggle('active', active);
    btn.setAttribute('aria-pressed', String(active));
    if (btn.dataset.sort !== 'distance') return;
    // aria-disabled is deliberately not used: the click must still be able to
    // surface the explanation below.
    btn.dataset.unavailable = String(!available);
    btn.classList.toggle('unavailable', !available);
    if (available) btn.removeAttribute('title');
    else btn.title = t('distanceSortNeedsLocation');
  });

  const hint = document.getElementById('sort-distance-hint');
  if (!hint) return;
  // The per-region summary has no station ordering at all, so the "enable
  // location to sort by distance" explanation would be misleading there.
  const show = listView !== 'regions' && !available && (distanceHintVisible || sortMode === 'distance');
  hint.hidden = !show;
  if (show) hint.textContent = t('distanceSortNeedsLocation');
}

function createStationRow(feat, priceKey) {
  const props = feat.properties;
  const reference = getReferencePoint();
  const distance = reference
    ? haversineDistance(reference[0], reference[1], feat.geometry.coordinates[0], feat.geometry.coordinates[1])
    : null;
  const distanceText = distance != null ? `${distance.toFixed(1)} km` : '—';
  const color = brandColor(props.brand);
  const abbr = brandAbbr(props.brand);
  const stationPrice = props[priceKey];
  const isBest = stationPrice === cheapestVisiblePrice;
  const isFav = isFavorite(feat);

  const priceText = stationPrice ? stationPrice.toFixed(1) + '¢' : '—';

  const item = document.createElement('div');
  item.className = 'list-item' + (isBest ? ' best' : '');
  // The row opens the station card, so it must announce itself as a button and
  // be reachable with Tab (WCAG 2.1 A: 2.1.1 / 4.1.2). It stays a div: the
  // favourite star inside is a real <button>, and nesting buttons is invalid.
  item.setAttribute('role', 'button');
  item.setAttribute('tabindex', '0');
  item.setAttribute('aria-label', tf('openStationAria', {
    name: props.name || props.brand,
    price: priceText
  }));
  item.innerHTML = `
    <div class="brand-icon" style="background:${color}">${abbr}</div>
    <div class="info">
      <div class="name">${props.name || props.brand}</div>
      <div class="details">${props.address}</div>
    </div>
    <button class="fav-star ${isFav ? 'on' : ''}" data-fav aria-label="${isFav ? t('unfavorite') : t('favorite')}" aria-pressed="${isFav}">${STAR_ICON}</button>
    <div class="price-block">
      <div class="price">${priceText}</div>
      <div class="distance">${distanceText}</div>
    </div>`;

  item.querySelector('.fav-star').addEventListener('click', (e) => {
    e.stopPropagation();
    toggleFavorite(feat);
  });

  // One activation path for pointer and keyboard, so the two can never drift.
  const activateRow = () => {
    map.flyTo({ center: feat.geometry.coordinates, zoom: 15, duration: 800 });
    updatePopup(feat);
  };

  item.addEventListener('click', activateRow);
  item.addEventListener('keydown', (e) => {
    // Key events bubbling up from the star belong to the star, not the row.
    if (e.target !== item) return;
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    // Space would otherwise scroll the sidebar while the row is focused.
    e.preventDefault();
    activateRow();
  });
  return item;
}

// Appends the next page of the already-sorted list. Called by "Voir plus"
// only: every filter / sort / language change re-renders from page one.
function appendStationPage() {
  const list = document.getElementById('station-list');
  if (!list) return;
  const priceKey = getActiveFuelPriceKey();
  const nextPage = renderedStations.slice(renderedCount, renderedCount + PAGE_SIZE);
  nextPage.forEach(feat => list.appendChild(createStationRow(feat, priceKey)));
  renderedCount += nextPage.length;
  updateListCount(renderedCount, renderedStations.length);
  updateShowMoreButton();
}

// Stations that survive every filter (brand / fuel / price range / favorites),
// with the location scope made explicit instead of read from the UI. One shared
// path so the per-region summary row count and the sidebar count obtained after
// drilling into that region can never disagree (issues #60, #87).
function stationsMatchingScope({ region = '', radiusMode = false } = {}) {
  if (!currentStations || !Array.isArray(currentStations.features)) return [];
  const criteria = { ...getFilterCriteria(), radiusMode, selectedRegion: region };
  const selectedBrands = new Set();
  document.querySelectorAll('.brand-filter:checked').forEach(cb => selectedBrands.add(cb.value));

  return currentStations.features.filter(feat => {
    if (!matchesNonBrandFilters(feat, criteria)) return false;
    if (selectedBrands.size === 0 || !selectedBrands.has(feat.properties.brand)) return false;
    if (favoritesOnly && !isFavorite(feat)) return false;
    return true;
  });
}

// Stations of a region that survive every filter except the radius: this is the
// count the "see the whole region" escape hatch reveals.
function countRegionStationsWithoutRadius(region) {
  return stationsMatchingScope({ region }).length;
}

// Zero results normally mean "nothing matches your filters". But region (a
// scope) and radius ("around me") are orthogonal: selecting a far region and
// then locating yourself intersects to nothing. That is not a dead end, so we
// offer a one-tap escape that keeps the region and the other filters (#60).
function renderEmptyState() {
  const list = document.getElementById('station-list');
  const criteria = getFilterCriteria();
  const region = criteria.selectedRegion;
  const regionCount = region && criteria.radiusMode && !isSearchActive()
    ? countRegionStationsWithoutRadius(region)
    : 0;
  const actionable = regionCount > 0;

  const wrap = document.createElement('div');
  wrap.className = 'no-stations';
  wrap.setAttribute('no-stations', '');
  wrap.setAttribute('role', 'status');
  wrap.setAttribute('aria-live', 'polite');
  wrap.dataset.emptyKind = actionable ? 'regionRadius' : 'plain';
  wrap.dataset.count = String(regionCount);

  const text = document.createElement('div');
  text.className = 'no-stations-text';
  text.setAttribute('data-no-stations-text', '');
  text.textContent = actionable ? t('noStationsRadiusRegion') : t('noStations');
  wrap.appendChild(text);

  if (actionable) {
    const action = document.createElement('button');
    action.type = 'button';
    action.className = 'no-stations-action';
    action.textContent = tf('viewWholeRegion', { n: regionCount });
    action.setAttribute('aria-label', tf('viewWholeRegionAria', { n: regionCount }));
    action.addEventListener('click', () => {
      // Same escape as selecting the region directly: drop the radius and refit
      // the map to the region, otherwise the revealed stations stay off-screen.
      setRadiusMode(false);
      fitRegionBounds(region);
      updateStats();
    });
    wrap.appendChild(action);
  }

  list.appendChild(wrap);
}

// ── "Par région" summary view (issue #87) ──
// The province-wide default list is a top 30 by price, so on a real snapshot
// most regions have no row at all. This view answers the one actionable
// question the sidebar could not: "what does *my* region cost right now?".

function getSelectedRegion() {
  return document.getElementById('region-filter')?.value || '';
}

// The summary is only meaningful as a province-wide scope. With a radius, a
// concrete region, an active search (which lifts the location filters) or the
// favorites-only listing, "the region minimum" would silently mean something
// other than the numbers the user sees after drilling in.
function isRegionViewAvailable() {
  if (getSelectedRegion()) return false;
  if (isRadiusMode()) return false;
  if (isSearchActive()) return false;
  if (favoritesOnly) return false;
  return true;
}

// Keeps the switch's state, visibility and ARIA in sync with the current view.
// Unavailable contexts fall back to the station listing (never leave the list
// showing a scope-less region summary).
function renderViewControls() {
  const available = isRegionViewAvailable();
  if (!available && listView === 'regions') {
    listView = 'stations';
    persistListPreferences();
  }

  const container = document.getElementById('list-view');
  if (container) {
    container.hidden = !available;
    container.setAttribute('aria-label', t('listViewLabel'));
    container.querySelectorAll('.view-btn').forEach(btn => {
      const active = btn.dataset.view === listView;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-checked', String(active));
      // Roving tabindex: a radiogroup is a single tab stop, so Tab lands on the
      // checked radio and the arrows move inside the group (WCAG 2.1 A: 4.1.2).
      btn.tabIndex = active ? 0 : -1;
    });
  }

  // Sorting a per-region summary by "distance" would be meaningless.
  const sortSwitch = document.getElementById('list-sort');
  if (sortSwitch) sortSwitch.hidden = listView === 'regions';
}

// Min / median / count per region, computed from the stations that survive the
// brand, fuel and price filters — radius and region scopes ignored.
function computeRegionSummaries(priceKey) {
  const byRegion = new Map();
  stationsMatchingScope({}).forEach(feat => {
    const props = feat.properties;
    const price = props[priceKey];
    if (!props.region || price === null || price === undefined) return;
    if (!byRegion.has(props.region)) byRegion.set(props.region, []);
    byRegion.get(props.region).push(price);
  });

  const summaries = [];
  byRegion.forEach((prices, region) => {
    const sorted = [...prices].sort((a, b) => a - b);
    const middle = Math.floor(sorted.length / 2);
    const median = sorted.length % 2 === 0
      ? (sorted[middle - 1] + sorted[middle]) / 2
      : sorted[middle];
    summaries.push({ region, count: sorted.length, min: sorted[0], median });
  });

  summaries.sort((a, b) => a.min - b.min || a.region.localeCompare(b.region));
  return summaries;
}

// Drilling into a region reuses the #60 semantics exactly: a region is a scope,
// so the radius is released, the map is fitted to it and the list switches back
// to the station rows of that region.
function enterRegion(region) {
  const select = document.getElementById('region-filter');
  if (!select) return;
  select.value = region;
  if (isRadiusMode()) setRadiusMode(false);
  fitRegionBounds(region);
  listView = 'stations';
  persistListPreferences();
  updateStats();
  // The summary rows were just replaced by station rows, so focus would fall
  // back to <body> and the keyboard user would restart at the top of the page.
  // Land on the first station of the region they just entered (WCAG 2.4.3).
  document.querySelector('#station-list .list-item')?.focus();
}

function createRegionRow(summary) {
  const priceText = summary.min.toFixed(1) + '¢';
  const item = document.createElement('div');
  item.className = 'region-row';
  item.dataset.region = summary.region;
  item.dataset.count = String(summary.count);
  item.dataset.minPrice = String(summary.min);
  item.dataset.medianPrice = String(summary.median);
  // The row is an activation target (selects the region), so it needs the same
  // name / role / keyboard contract as a station row (WCAG 2.1 A: 4.1.2).
  item.setAttribute('role', 'button');
  item.setAttribute('tabindex', '0');
  item.setAttribute('aria-label', tf('openRegionAria', {
    region: summary.region,
    n: summary.count,
    price: priceText
  }));
  item.innerHTML = `
    <div class="info">
      <div class="region-name">${summary.region}</div>
      <div class="region-meta">${tf('regionRowMeta', { n: summary.count, m: summary.median.toFixed(1) })}</div>
    </div>
    <div class="price-block">
      <div class="region-price">${priceText}</div>
    </div>`;

  const activate = () => enterRegion(summary.region);
  item.addEventListener('click', activate);
  item.addEventListener('keydown', (e) => {
    if (e.target !== item) return;
    if (e.key !== 'Enter' && e.key !== ' ' && e.key !== 'Spacebar') return;
    // Space would otherwise scroll the sidebar while the row is focused.
    e.preventDefault();
    activate();
  });
  return item;
}

function renderRegionView(priceKey) {
  const list = document.getElementById('station-list');
  const summaries = computeRegionSummaries(priceKey);
  if (summaries.length === 0) {
    renderEmptyState();
    updateListCount(0, 0);
    updateShowMoreButton();
    return;
  }
  summaries.forEach(summary => list.appendChild(createRegionRow(summary)));
  // The header count describes rendered rows, and these rows are regions, not
  // stations — reusing the station wording would read "18" as 18 stations.
  updateListCount(summaries.length, summaries.length, 'shownRegions');
  updateShowMoreButton();
}

function updateStationList(filteredStations = null) {
  if (filteredStations === null) filteredStations = getVisibleStations();
  const list = document.getElementById('station-list');
  list.innerHTML = '';
  renderedStations = [];
  renderedCount = 0;
  cheapestVisiblePrice = Infinity;

  renderViewControls();

  const priceKey = getActiveFuelPriceKey();
  if (listView === 'regions') {
    renderRegionView(priceKey);
    renderSortControls();
    return;
  }

  if (filteredStations.length === 0) {
    renderEmptyState();
    updateListCount(0, 0);
    updateShowMoreButton();
    renderSortControls();
    return;
  }

  renderedStations = sortStationList(filteredStations, priceKey);
  // "Best price" must reflect every filtered station, not just the rendered
  // page (the cheapest Montréal station is often far beyond the first 30).
  cheapestVisiblePrice = renderedStations.reduce(
    (min, f) => Math.min(min, f.properties[priceKey] ?? Infinity),
    Infinity
  );

  appendStationPage();
  renderSortControls();
}

// Wired once from app.js: sort switch + "Voir plus" pagination.
export function initStationListControls() {
  const container = document.getElementById('list-sort');
  if (container) {
    container.addEventListener('click', (e) => {
      const btn = e.target.closest('.sort-btn');
      if (!btn) return;
      const mode = btn.dataset.sort;
      if (mode !== 'price' && mode !== 'distance') return;
      if (mode === 'distance' && !isDistanceSortAvailable()) {
        // No reference point: keep the price order and explain why.
        distanceHintVisible = true;
        updateStationList();
        return;
      }
      if (mode === sortMode) {
        renderSortControls();
        return;
      }
      sortMode = mode;
      distanceHintVisible = false;
      persistListPreferences();
      // Re-render from page one in the new order.
      updateStationList();
    });
  }

  const showMore = document.getElementById('show-more-stations');
  if (showMore) showMore.addEventListener('click', () => appendStationPage());

  const viewSwitch = document.getElementById('list-view');
  if (viewSwitch) {
    viewSwitch.addEventListener('click', (e) => {
      const btn = e.target.closest('.view-btn');
      if (!btn) return;
      const view = btn.dataset.view;
      if (view !== 'stations' && view !== 'regions') return;
      if (view === listView) {
        renderViewControls();
        return;
      }
      // Never enter the summary in a context where it would be misleading.
      if (view === 'regions' && !isRegionViewAvailable()) {
        renderViewControls();
        return;
      }
      listView = view;
      persistListPreferences();
      updateStationList();
    });

    // Arrow keys move the selection inside the radiogroup (roving tabindex is
    // set by renderViewControls); Home/End jump to the ends.
    viewSwitch.addEventListener('keydown', (e) => {
      const btn = e.target.closest('.view-btn');
      if (!btn) return;
      const buttons = [...viewSwitch.querySelectorAll('.view-btn')];
      const index = buttons.indexOf(btn);
      if (index < 0) return;
      let next;
      if (e.key === 'Home') next = 0;
      else if (e.key === 'End') next = buttons.length - 1;
      else if (e.key === 'ArrowLeft' || e.key === 'ArrowUp') next = (index - 1 + buttons.length) % buttons.length;
      else if (e.key === 'ArrowRight' || e.key === 'ArrowDown') next = (index + 1) % buttons.length;
      else return;
      e.preventDefault();
      // Same activation path as a click, so availability is honoured too.
      buttons[next].focus();
      buttons[next].click();
    });
  }
}

export function showPopup(feature) {
  updatePopup(feature);
}

function updatePopup(feature) {
  // Delegate to the Apple-stock style station card (popup + expandable panel)
  const ts = currentStations?.metadata?.generated_at;
  let updatedText = '';
  if (ts) {
    const d = new Date(ts);
    const timeStr = d.toLocaleString(getLanguage(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    updatedText = tf('dataUpdated', { time: timeStr });
  }
  import('./history.js').then(h => h.loadHistoryData().catch(() => null))
    .then(() => import('./station-card.js'))
    .then(mod => mod.showStationCard(feature, map, updatedText));
}

export function openStationDetail(feature) {
  const ts = currentStations?.metadata?.generated_at;
  let updatedText = '';
  if (ts) {
    const d = new Date(ts);
    const timeStr = d.toLocaleString(getLanguage(), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
    updatedText = tf('dataUpdated', { time: timeStr });
  }
  import('./history.js').then(h => h.loadHistoryData().catch(() => null))
    .then(() => import('./station-card.js'))
    .then(mod => mod.openStationDetail(feature, map, updatedText));
}

export function closeStationDetail() {
  import('./station-card.js').then(mod => mod.closeStationDetail());
}

function updateFavoritesUI() {
  const countEl = document.getElementById('favorites-count');
  if (countEl) {
    countEl.textContent = tf('favoritesCount', { n: getFavoriteCount() });
  }
  const toggle = document.getElementById('favorites-toggle');
  if (toggle) {
    toggle.classList.toggle('active', favoritesOnly);
    toggle.setAttribute('aria-pressed', String(favoritesOnly));
  }
}

export function initFavorites() {
  const toggle = document.getElementById('favorites-toggle');
  if (toggle) {
    toggle.addEventListener('click', () => {
      favoritesOnly = !favoritesOnly;
      updateFavoritesUI();
      updateStats();
    });
  }

  subscribe(() => {
    updateFavoritesUI();
    // When "Favorites only" is active, un-favoriting a station must also
    // re-sync the map source, lowest-price highlight, quick stats and count,
    // not just the list.
    if (favoritesOnly) updateStats();
    else updateStationList();
  });

  onLanguageChange(() => {
    updateFavoritesUI();
    // Re-render the list so star aria-labels pick up the new language.
    updateStationList();
  });
  updateFavoritesUI();
}

export { filterStations, filterStationsAsFeatureCollection, updateMapStations, updateStats, updateLowestPriceHighlight, updateStationList, getVisibleStations };

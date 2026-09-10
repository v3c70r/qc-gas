import { map, MONTREAL_CENTER, currentStations } from './map.js';
import { tf, translations, getLanguage, t, onLanguageChange } from './i18n.js';
import { brandColor, brandAbbr } from './constants.js';
import { isFavorite, toggleFavorite, getFavoriteCount, subscribe, STAR_ICON } from './favorites.js';

let favoritesOnly = false;

function haversineDistance(lng1, lat1, lng2, lat2) {
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

function filterStations() {
  const radiusBtn = document.querySelector('.radius-btn.active');
  const radiusKm = radiusBtn ? parseFloat(radiusBtn.dataset.radius) : 25;

  const selectedBrands = new Set();
  document.querySelectorAll('.brand-filter:checked').forEach(cb => selectedBrands.add(cb.value));

  const selectedFuelTypes = {};
  document.querySelectorAll('.fuel-filter').forEach(cb => { selectedFuelTypes[cb.value] = cb.checked; });
  // Determine active fuel type (radio: exactly one is always checked)
  const activeFuel = document.querySelector('.fuel-filter:checked')?.value || 'regular';

  const priceMin = parseFloat(document.getElementById('min-price').value);
  const priceMax = parseFloat(document.getElementById('max-price').value);
  const selectedRegion = document.getElementById('region-filter').value;

  return currentStations.features.filter(feat => {
    const props = feat.properties;
    const coords = feat.geometry.coordinates;
    const distance = haversineDistance(MONTREAL_CENTER[0], MONTREAL_CENTER[1], coords[0], coords[1]);
    if (distance > radiusKm) return false;
    if (selectedBrands.size === 0 || !selectedBrands.has(props.brand)) return false;
    if (selectedRegion && props.region !== selectedRegion) return false;

    // Must have the selected fuel type with a price in range
    const priceKey = activeFuel + '_price';
    const fuelPrice = props[priceKey];
    if (fuelPrice === null) return false;
    if (fuelPrice < priceMin || fuelPrice > priceMax) return false;
    return true;
  });
}

function filterStationsAsFeatureCollection() {
  return { type: 'FeatureCollection', features: filterStations() };
}

function updateMapStations(filteredStations) {
  if (!map || !map.getSource) return;
  const source = map.getSource('stations');
  if (!source || typeof source.setData !== 'function') return;
  source.setData({ type: 'FeatureCollection', features: filteredStations });
}

function updateStats() {
  let filtered = filterStations();
  if (favoritesOnly) {
    filtered = filtered.filter(f => isFavorite(f));
  }
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
  updateLowestPriceHighlight(filtered);
  updateStationList(filtered);
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

function updateStationList(filteredStations = null) {
  if (filteredStations === null) {
    filteredStations = filterStations();
    if (favoritesOnly) filteredStations = filteredStations.filter(f => isFavorite(f));
  }
  const list = document.getElementById('station-list');
  list.innerHTML = '';

  if (filteredStations.length === 0) {
    list.innerHTML = `<div style="padding:20px;text-align:center;color:#94a3b8;font-size:13px;" no-stations>${tf('noStations')}</div>`;
    return;
  }

  // Determine active fuel type for price display
  const activeFuel = document.querySelector('.fuel-filter:checked')?.value || 'regular';
  const priceKey = activeFuel + '_price';
  const dict = translations[getLanguage()];
  const fuelLabel = (dict?.[activeFuel] || activeFuel).toLowerCase();

  // Favorites are pinned above the price sort (acceptance: favorites first).
  filteredStations.sort((a, b) => {
    const aFav = isFavorite(a) ? 0 : 1;
    const bFav = isFavorite(b) ? 0 : 1;
    if (aFav !== bFav) return aFav - bFav;
    return (a.properties[priceKey] || Infinity) - (b.properties[priceKey] || Infinity);
  });
  const cheapestPrice = filteredStations.reduce((min, f) => Math.min(min, f.properties[priceKey] || Infinity), Infinity);

  filteredStations.slice(0, 30).forEach(feat => {
    const props = feat.properties;
    const distance = haversineDistance(MONTREAL_CENTER[0], MONTREAL_CENTER[1], feat.geometry.coordinates[0], feat.geometry.coordinates[1]);
    const color = brandColor(props.brand);
    const abbr = brandAbbr(props.brand);
    const stationPrice = props[priceKey];
    const isBest = stationPrice === cheapestPrice;
    const isFav = isFavorite(feat);

    const item = document.createElement('div');
    item.className = 'list-item' + (isBest ? ' best' : '');
    item.innerHTML = `
      <div class="brand-icon" style="background:${color}">${abbr}</div>
      <div class="info">
        <div class="name">${props.name || props.brand}</div>
        <div class="details">${props.address}</div>
      </div>
      <button class="fav-star ${isFav ? 'on' : ''}" data-fav aria-label="${isFav ? t('unfavorite') : t('favorite')}" aria-pressed="${isFav}">${STAR_ICON}</button>
      <div class="price-block">
        <div class="price">${stationPrice ? stationPrice.toFixed(1) + '¢' : '—'}</div>
        <div class="distance">${distance.toFixed(1)} km</div>
      </div>`;

    item.querySelector('.fav-star').addEventListener('click', (e) => {
      e.stopPropagation();
      toggleFavorite(feat);
    });

    item.addEventListener('click', () => {
      map.flyTo({ center: feat.geometry.coordinates, zoom: 15, duration: 800 });
      updatePopup(feat);
    });
    list.appendChild(item);
  });
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
    updateStationList();
  });

  onLanguageChange(updateFavoritesUI);
  updateFavoritesUI();
}

export { filterStations, filterStationsAsFeatureCollection, updateMapStations, updateStats, updateLowestPriceHighlight, updateStationList };

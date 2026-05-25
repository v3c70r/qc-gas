import { map, MONTREAL_CENTER, currentStations } from './map.js';
import { tf, translations, getLanguage } from './i18n.js';
import { brandColor, brandAbbr } from './constants.js';
import { t } from './i18n.js';

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
    if (selectedBrands.size > 0 && !selectedBrands.has(props.brand)) return false;
    if (selectedRegion && props.region !== selectedRegion) return false;

    // Must have the selected fuel type with a price in range
    const priceKey = activeFuel + '_price';
    const fuelPrice = props[priceKey];
    if (fuelPrice === null) return false;
    if (fuelPrice < priceMin || fuelPrice > priceMax) return false;
    return true;
  });
}

function updateStats() {
  const filtered = filterStations();
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
  if (map.getSource('lowest-price')) map.removeSource('lowest-price');
  if (map.getLayer('lowest-price')) map.removeLayer('lowest-price');
  if (map.getLayer('lowest-price-border')) map.removeLayer('lowest-price-border');
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
  if (filteredStations === null) filteredStations = filterStations();
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

  filteredStations.sort((a, b) => (a.properties[priceKey] || Infinity) - (b.properties[priceKey] || Infinity));
  const cheapestPrice = filteredStations[0]?.properties[priceKey] || Infinity;

  filteredStations.slice(0, 30).forEach(feat => {
    const props = feat.properties;
    const distance = haversineDistance(MONTREAL_CENTER[0], MONTREAL_CENTER[1], feat.geometry.coordinates[0], feat.geometry.coordinates[1]);
    const color = brandColor(props.brand);
    const abbr = brandAbbr(props.brand);
    const stationPrice = props[priceKey];
    const isBest = stationPrice === cheapestPrice;

    const item = document.createElement('div');
    item.className = 'list-item' + (isBest ? ' best' : '');
    item.innerHTML = `
      <div class="brand-icon" style="background:${color}">${abbr}</div>
      <div class="info">
        <div class="name">${props.name || props.brand}</div>
        <div class="details">${props.address}</div>
      </div>
      <div class="price-block">
        <div class="price">${stationPrice ? stationPrice.toFixed(1) + '¢' : '—'}</div>
        <div class="distance">${distance.toFixed(1)} km</div>
      </div>`;

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
  const props = feature.properties;
  const priceInfo = [];
  const lang = getLanguage();
  const dict = translations[lang];
  const stationLabel = dict?.station || 'Station';
  const [lng, lat] = feature.geometry.coordinates;

  if (props.regular_price) priceInfo.push(`${dict?.regular || 'Régulier'}: ${props.regular_price.toFixed(1)}¢`);
  if (props.super_price) priceInfo.push(`${dict?.super || 'Super'}: ${props.super_price.toFixed(1)}¢`);
  if (props.diesel_price) priceInfo.push(`${dict?.diesel || 'Diesel'}: ${props.diesel_price.toFixed(1)}¢`);

  // Navigation URL: Apple Maps on iOS, Google Maps elsewhere
  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const navUrl = isIOS
    ? `https://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`
    : `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;

  const popupContent = `<div style="padding:8px;min-width:220px;">
    <h3 style="margin:0 0 4px 0;font-size:14px;font-weight:600;">${props.name || stationLabel}</h3>
    <p style="margin:2px 0;font-size:12px;color:#64748b;">${props.brand}</p>
    <p style="margin:2px 0 8px 0;font-size:12px;color:#334155;">${props.address}</p>
    <div style="margin-bottom:8px;padding:8px;background:#f8fafc;border-radius:6px;font-size:11px;">${priceInfo.join('<br>')}</div>
    <a href="${navUrl}" target="_blank" rel="noopener"
       style="display:flex;align-items:center;justify-content:center;gap:6px;width:100%;padding:8px 0;background:#1a73e8;color:#fff;border-radius:6px;font-size:13px;font-weight:600;text-decoration:none;text-align:center;transition:background .15s;"
       onmouseover="this.style.background='#1557b0'" onmouseout="this.style.background='#1a73e8'">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2L2 22h20L12 2z"/><line x1="12" y1="22" x2="12" y2="10"/></svg>
      ${t('navigate')}
    </a>
  </div>`;

  new mapboxgl.Popup({ closeButton: true, closeOnClick: true, anchor: 'bottom', maxWidth: '280px' })
    .setLngLat(feature.geometry.coordinates)
    .setHTML(popupContent)
    .addTo(map);
}

export { filterStations, updateStats, updateLowestPriceHighlight, updateStationList };
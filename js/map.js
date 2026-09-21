// Mapbox initialization and map configuration
import { t, onLanguageChange } from './i18n.js';
import { brandColor, brandAbbr, isMembershipBrand } from './constants.js';
import { updateStats } from './stats.js';
import { setDataSnapshot, isResponseFromCache } from './pwa.js';

const mapboxToken = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN;

if (!mapboxToken) {
  document.body.innerHTML = `<div style="padding:40px;text-align:center;font-family:sans-serif;"><h1>Configuration Error</h1><p>${t('noToken')}</p></div>`;
}

// ── Async Mapbox GL loader ──
function waitForMapboxGL(timeoutMs = 15000) {
  if (window.mapboxgl) return Promise.resolve();
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      if (window.mapboxgl) return resolve();
      if (Date.now() - start > timeoutMs) {
        return reject(new Error('Mapbox GL failed to load after ' + timeoutMs + 'ms'));
      }
      // Fallback: inject if script tag missing
      if (!document.querySelector('script[src*="mapbox-gl"]')) {
        const s = document.createElement('script');
        s.src = 'https://api.mapbox.com/mapbox-gl-js/v3.6.0/mapbox-gl.js';
        s.onload = () => check();
        s.onerror = () => reject(new Error('Mapbox GL script failed to load'));
        document.head.appendChild(s);
        return;
      }
      setTimeout(check, 100);
    };
    check();
  });
}

// Montréal West Island fallback reference. It is NOT used for radius filtering
// unless the user explicitly enters radius mode without another reference point.
const MONTREAL_CENTER = [-73.7, 45.45];

// Valid Québec bounding box used for first-visit province view + invalid
// coordinate protection. The official snapshot contains a (0,0) "Hub Régie"
// record that must never enter the map/list/stats/benchmarks.
export const QUEBEC_BOUNDS = [[-79.5, 44.9], [-57.1, 62.4]];
const VIEW_STORAGE_KEY = 'qc-gas-view';
const VALID_RADII = [5, 10, 25, 50];

let map;
let currentStations = [];
// Explicit user reference point ([lng, lat]) or null when the user has not
// located / clicked the map. `MONTREAL_CENTER` is only the legacy fallback.
let referencePoint = null;
let radiusMode = false;
let referenceSource = 'stored'; // 'geolocation' | 'map-click' | 'stored'

// Must match the default `.radius-btn.active` in index.html (25 km)
export const rangeRadius = { value: 25 }; // km, shared mutable reference
let pulseAnimationId = null;

export function isValidQuebecCoordinate(lng, lat) {
  const [sw, ne] = QUEBEC_BOUNDS;
  return Number.isFinite(lng) && Number.isFinite(lat) &&
    lng >= sw[0] && lng <= ne[0] &&
    lat >= sw[1] && lat <= ne[1];
}

export function isValidStation(feature) {
  const coords = feature?.geometry?.coordinates;
  return Array.isArray(coords) && isValidQuebecCoordinate(coords[0], coords[1]);
}

export function getReferencePoint() {
  return referencePoint ? [...referencePoint] : null;
}

export function getEffectiveReferencePoint() {
  return getReferencePoint() || [...MONTREAL_CENTER];
}

export function isRadiusMode() {
  return radiusMode;
}

function loadViewState() {
  try {
    const raw = localStorage.getItem(VIEW_STORAGE_KEY);
    if (!raw) return null;
    const saved = JSON.parse(raw);
    const lng = Number(saved?.lng);
    const lat = Number(saved?.lat);
    if (!isValidQuebecCoordinate(lng, lat)) return null;
    const radiusKm = VALID_RADII.includes(Number(saved?.radiusKm)) ? Number(saved.radiusKm) : 25;
    return {
      mode: saved.mode === 'radius' ? 'radius' : 'province',
      lng,
      lat,
      radiusKm,
      zoom: Number.isFinite(Number(saved?.zoom)) ? Number(saved.zoom) : null
    };
  } catch {
    return null;
  }
}

function persistView() {
  try {
    const reference = getEffectiveReferencePoint();
    const center = map?.getCenter ? map.getCenter() : null;
    localStorage.setItem(VIEW_STORAGE_KEY, JSON.stringify({
      mode: radiusMode ? 'radius' : 'province',
      lng: reference[0],
      lat: reference[1],
      radiusKm: rangeRadius.value,
      zoom: map?.getZoom ? map.getZoom() : null,
      center: center ? [center.lng, center.lat] : null
    }));
  } catch {
    // Storage may be unavailable (private browsing); view still works in-session.
  }
}

export function setRadiusMode(mode) {
  radiusMode = Boolean(mode);
  syncRadiusUI();
  addRangeCircle();
  persistView();
}

export function setReferencePoint(lng, lat, { zoom = 13, fly = true, source = 'map-click' } = {}) {
  if (!isValidQuebecCoordinate(lng, lat)) return;
  referencePoint = [lng, lat];
  MONTREAL_CENTER[0] = lng;
  MONTREAL_CENTER[1] = lat;
  radiusMode = true;
  referenceSource = source;
  syncRadiusUI();
  if (fly && map) map.flyTo({ center: [lng, lat], zoom, duration: 1200 });
  addRangeCircle();
  persistView();
  updateStats();
}

export function setRadiusKm(km) {
  const value = Number(km);
  if (!VALID_RADII.includes(value)) return;
  rangeRadius.value = value;
  radiusMode = true;
  syncRadiusUI();
  addRangeCircle();
  persistView();
  updateStats();
}

function syncRadiusUI() {
  document.querySelectorAll('.radius-btn').forEach((btn) => {
    const active = radiusMode && Number(btn.dataset.radius) === rangeRadius.value;
    btn.classList.toggle('active', active);
  });

  const label = document.querySelector('.radius-label');
  if (label) label.textContent = radiusMode ? t('radius') : t('provinceView');

  const source = document.getElementById('radius-source');
  if (source) {
    if (radiusMode) {
      const key = referenceSource === 'geolocation' ? 'referenceGeolocation'
        : referenceSource === 'map-click' ? 'referenceMapClick' : 'referenceStored';
      source.textContent = t(key);
      source.hidden = false;
    } else {
      source.hidden = true;
    }
  }
}

onLanguageChange(syncRadiusUI);

// ── Fuel selection helpers ──
function getActiveFuelPriceKey() {
  const activeFuel = document.querySelector('.fuel-filter:checked')?.value || 'regular';
  return activeFuel + '_price';
}

// Price → color gradient shared by the unclustered dots and price labels.
function priceColorExpression(priceKey) {
  return [
    'interpolate',
    ['linear'],
    ['get', priceKey],
    165, '#16a34a',  // Green - cheap
    180, '#eab308',  // Yellow - medium
    200, '#dc2626'   // Red - expensive
  ];
}

// Initialize map with modern light style
export async function initMap() {
  try {
    await waitForMapboxGL();
  } catch (err) {
    console.error(err);
    document.getElementById('map').innerHTML = `<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#94a3b8;font-family:sans-serif;font-size:14px;text-align:center;padding:20px;">${t('mapError')}</div>`;
    return;
  }
  mapboxgl.accessToken = mapboxToken;

  const savedView = loadViewState();
  if (savedView && savedView.mode === 'radius') {
    referencePoint = [savedView.lng, savedView.lat];
    MONTREAL_CENTER[0] = savedView.lng;
    MONTREAL_CENTER[1] = savedView.lat;
    radiusMode = true;
    rangeRadius.value = savedView.radiusKm;
    referenceSource = 'stored';
  }

  syncRadiusUI();

  // Keep the pre-load view quick and familiar (Montréal zoom 10, as before).
  // Province mode is fitted to valid station bounds right after stations load,
  // so first-time visitors still land on the province-wide view without
  // delaying the initial Mapbox load.
  const initialCenter = radiusMode ? getEffectiveReferencePoint() : [...MONTREAL_CENTER];
  const initialZoom = radiusMode ? (savedView?.zoom ?? 12) : 10;

  map = new mapboxgl.Map({
    container: 'map',
    style: 'mapbox://styles/mapbox/light-v11',
    center: initialCenter,
    zoom: initialZoom,
    attributionControl: true,
    pitch: 0,
    bearing: 0
  });

  map.on('load', () => {
    loadStations();
  });

  // Add scale control
  map.addControl(new mapboxgl.ScaleControl({
    unit: 'metric',
    maxWidth: 100
  }));

  // Add navigation control
  map.addControl(new mapboxgl.NavigationControl({
    showCompass: true,
    showZoom: true
  }));

  // Click on map to set the radius reference point.
  map.on('click', (e) => {
    const features = map.queryRenderedFeatures(e.point);
    if (features.length > 0) return;
    setReferencePoint(e.lngLat.lng, e.lngLat.lat, { zoom: map.getZoom(), fly: false, source: 'map-click' });
  });
}

// Station count for i18n reactivity
let stationCount = 0;

// Keep brand-item tooltips in sync with the active language (the markup itself
// is built once in loadStations, so only attribute text needs refreshing).
function refreshBrandItemLabels() {
  document.querySelectorAll('.brand-filter-item').forEach(item => {
    const membership = item.querySelector('.brand-membership');
    if (membership) {
      membership.title = t('membershipRequired');
      membership.setAttribute('aria-label', t('membershipRequired'));
    }
    const count = item.querySelector('.brand-count');
    if (count) count.title = t('stations');
    const avg = item.querySelector('.brand-avg');
    if (avg) avg.title = t('brandAvg');
    const diff = item.querySelector('.brand-diff');
    if (diff) diff.title = t('brandVsAvg');
  });
}
onLanguageChange(() => refreshBrandItemLabels());

// Load stations data
export async function loadStations() {
  document.getElementById('loading').classList.add('active');
  
  try {
    const response = await fetch('data/stations.json');
    const data = await response.json();

    // Exclude official records with invalid coordinates (currently the (0,0)
    // "Hub Régie" record) from every downstream consumer: map, list, stats,
    // regional benchmarks and province fitBounds.
    const rawFeatures = Array.isArray(data.features) ? data.features : [];
    const validFeatures = rawFeatures.filter(isValidStation);
    currentStations = { ...data, features: validFeatures };
    stationCount = validFeatures.length;

    console.log('Loaded', validFeatures.length, 'stations (excluded', rawFeatures.length - validFeatures.length, 'invalid coordinates)');

    // Initialize brand filters — sorted by popularity (most stations first)
    const brandCount = {};
    validFeatures.forEach(f => {
      const b = f.properties.brand;
      if (b) brandCount[b] = (brandCount[b] || 0) + 1;
    });
    const brands = Object.entries(brandCount)
      .sort((a, b) => b[1] - a[1]); // descending by count
    const brandContainer = document.getElementById('brand-filters');
    const moreBrands = document.getElementById('more-brands');

    // ── Select / deselect all toggle ──
    const toggleRow = document.createElement('div');
    toggleRow.className = 'brand-toggle-row';
    toggleRow.innerHTML = `
      <button class="brand-toggle-btn" id="brand-select-all" data-i18n="selectAllBrands">${t('selectAllBrands')}</button>
      <span class="brand-toggle-count">${brands.length} ${t('brand').toLowerCase()}</span>
    `;
    brandContainer.parentNode.insertBefore(toggleRow, brandContainer);

    function syncToggleState() {
      const allChecked = document.querySelectorAll('.brand-filter:checked').length === brands.length;
      const noneChecked = document.querySelectorAll('.brand-filter:checked').length === 0;
      const btn = document.getElementById('brand-select-all');
      if (allChecked) {
        btn.textContent = t('deselectAllBrands');
        btn.classList.add('deselect');
      } else {
        btn.textContent = t('selectAllBrands');
        btn.classList.remove('deselect');
      }
    }

    document.getElementById('brand-select-all').addEventListener('click', () => {
      const allChecked = document.querySelectorAll('.brand-filter:checked').length === brands.length;
      window.__brandBatchUpdate = true;
      document.querySelectorAll('.brand-filter').forEach(cb => { cb.checked = !allChecked; });
      window.__brandBatchUpdate = false;
      document.querySelectorAll('.brand-filter-item').forEach(item => {
        const cb = item.querySelector('input');
        item.classList.toggle('active', cb && cb.checked);
      });
      syncToggleState();
      updateStats();
    });

    // Build brand filter items with count badges
    function createBrandItem(brand, count, compact) {
      const color = brandColor(brand);
      const abbr = brandAbbr(brand);
      const item = document.createElement('label');
      item.className = 'brand-filter-item active';
      if (compact) item.style.margin = '4px';
      const iconSize = compact ? '18px' : '20px';
      const fontSize = compact ? '7px' : '8px';
      const membership = isMembershipBrand(brand)
        ? `<span class="brand-membership" title="${t('membershipRequired')}" aria-label="${t('membershipRequired')}">🔒</span>`
        : '';
      item.innerHTML = `<input type="checkbox" class="brand-filter" value="${brand}" checked>
        <span class="brand-icon" style="width:${iconSize};height:${iconSize};border-radius:4px;background:${color};display:inline-flex;align-items:center;justify-content:center;font-size:${fontSize};font-weight:700;color:#fff;flex-shrink:0;">${abbr}</span>
        <span class="brand-name">${brand}</span>
        ${membership}
        <span class="brand-metrics">
          <span class="brand-count" title="${t('stations')}">${count}</span>
          <span class="brand-avg" title="${t('brandAvg')}">—</span>
          <span class="brand-diff" title="${t('brandVsAvg')}">—</span>
        </span>`;
      item.addEventListener('click', (e) => {
        // Let the label toggle the checkbox naturally, then sync
        setTimeout(syncToggleState, 0);
      });
      return item;
    }

    brands.slice(0, 14).forEach(([brand, count]) => {
      brandContainer.appendChild(createBrandItem(brand, count, false));
    });

    if (brands.length > 14) {
      brands.slice(14).forEach(([brand, count]) => {
        moreBrands.appendChild(createBrandItem(brand, count, true));
      });
    }

    // Initial sync — all brands start checked, so button should say "Deselect all"
    syncToggleState();
    
    // Initialize region filter
    const regionContainer = document.getElementById('region-filter');
    const regions = [...new Set(validFeatures.map(f => f.properties.region))].sort();
    regions.forEach(region => {
      const option = document.createElement('option');
      option.value = region;
      option.textContent = region;
      regionContainer.appendChild(option);
    });

    // Add layers
    addStationLayers();
    
    // Add range circle (only when radius mode is active)
    addRangeCircle();

    // First visit (no saved radius view) shows the whole province, never a
    // silent Montréal zoom.
    if (!radiusMode) fitQuebecBounds();
    
    // Update UI (pwa.js renders #data-status, including honest offline label)
    setDataSnapshot({
      generatedAt: data.metadata?.generated_at || null,
      fromCache: isResponseFromCache(response),
      stationCount
    });
    updateStats();

    // Let dependent modules (e.g. the price-watch list) evaluate once the
    // real snapshot is available.
    window.dispatchEvent(new CustomEvent('stations:loaded', { detail: currentStations }));
    
  } catch (error) {
    console.error('Error loading stations:', error);
    document.getElementById('data-status').textContent = 'Erreur de chargement des données';
  } finally {
    document.getElementById('loading').classList.remove('active');
  }
}

// Add station layers
function addStationLayers() {
  // Remove existing source
  if (map.getSource('stations')) {
    map.removeSource('stations');
  }
  
  // Add data source with clustering
  map.addSource('stations', {
    type: 'geojson',
    data: currentStations,
    cluster: true,
    clusterRadius: 50,
    clusterMaxZoom: 14
  });

  // Cluster circles (zoom < 10)
  if (!map.getLayer('clusters')) {
    map.addLayer({
      id: 'clusters',
      type: 'circle',
      source: 'stations',
      filter: ['has', 'point_count'],
      paint: {
        'circle-color': [
          'step',
          ['get', 'point_count'],
          '#6366f1', 10,   // Indigo for small clusters
          '#8b5cf6', 30,   // Violet for medium
          '#a78bfa'       // Light purple for large
        ],
        'circle-radius': [
          'step',
          ['get', 'point_count'],
          18, 10,
          28, 30,
          36
        ],
        'circle-stroke-color': '#ffffff',
        'circle-stroke-width': 2
      }
    });

    // Cluster count labels
    map.addLayer({
      id: 'cluster-count',
      type: 'symbol',
      source: 'stations',
      filter: ['has', 'point_count'],
      layout: {
        'text-field': ['get', 'point_count_abbreviated'],
        'text-size': 11,
        'text-font': ['DIN Pro Bold', 'Arial Unicode MS Bold']
      },
      paint: {
        'text-color': '#ffffff'
      }
    });
  }

  // Individual stations (zoom >= 10) - color by price
    if (!map.getLayer('unclustered-points')) {
      map.addLayer({
        id: 'unclustered-points',
        type: 'circle',
        source: 'stations',
        filter: ['!', ['has', 'point_count']],
        paint: {
          'circle-color': priceColorExpression(getActiveFuelPriceKey()),
          'circle-radius': [
            'interpolate',
            ['linear'],
            ['zoom'],
            10, 6,
            15, 8,
            20, 10
          ],
          'circle-stroke-color': '#ffffff',
          'circle-stroke-width': 1.5,
          'circle-opacity': 0.9
        }
      });
    }

  // Price labels above unclustered points (Airbnb/Zillow-style)
  addPriceLabelLayer();

  // Cluster click event
  map.on('click', 'clusters', (e) => {
    const features = map.queryRenderedFeatures(e.point, {
      layers: ['clusters']
    });
    if (features.length === 0) return;
    
    const clusterId = features[0].properties.cluster_id;
    map.getSource('stations').getClusterExpansionZoom(clusterId, (err, zoom) => {
      if (err) return;
      map.easeTo({
        center: features[0].geometry.coordinates,
        zoom: zoom,
        duration: 500
      });
    });
  });

  // Individual point click event
  map.on('click', 'unclustered-points', (e) => {
    if (e.features && e.features.length > 0) {
      showPopup(e.features[0]);
    }
  });

  // Mouse hover effects
  map.on('mouseenter', 'clusters', () => {
    map.getCanvas().style.cursor = 'pointer';
  });
  map.on('mouseleave', 'clusters', () => {
    map.getCanvas().style.cursor = '';
  });
  map.on('mouseenter', 'unclustered-points', () => {
    map.getCanvas().style.cursor = 'pointer';
  });
  map.on('mouseleave', 'unclustered-points', () => {
    map.getCanvas().style.cursor = '';
  });
}

// Add / rebuild the price label symbol layer for the currently selected fuel.
function addPriceLabelLayer() {
  if (!map.getSource('stations')) return;
  if (map.getLayer('station-price-labels')) {
    map.removeLayer('station-price-labels');
  }

  const priceKey = getActiveFuelPriceKey();
  map.addLayer({
    id: 'station-price-labels',
    type: 'symbol',
    source: 'stations',
    // Clusters have point_count; only label individual stations. Also hide null
    // prices so the canvas never renders the literal "null".
    filter: [
      'all',
      ['!', ['has', 'point_count']],
      ['!=', ['get', priceKey], null]
    ],
    minzoom: 11,
    layout: {
      'text-field': [
        'number-format',
        ['get', priceKey],
        { 'min-fraction-digits': 1, 'max-fraction-digits': 1 }
      ],
      'text-font': ['DIN Pro Bold', 'Arial Unicode MS Bold'],
      'text-size': [
        'interpolate',
        ['linear'],
        ['zoom'],
        11, 11,
        15, 13
      ],
      'text-anchor': 'bottom',
      'text-offset': [0, -0.3],
      'text-allow-overlap': false
    },
    paint: {
      'text-color': '#1f2937',
      'text-halo-color': '#ffffff',
      'text-halo-width': 1.5
    }
  });
}

// Exposed for filters.js so the fuel radio can swap the displayed price in place.
export function updateFuelPriceLayer() {
  if (!map || !map.getSource || !map.getSource('stations')) return;
  addPriceLabelLayer();

  // Keep the dot color in sync with the selected fuel too, so a diesel label
  // is not shown on a green dot colored by the regular price.
  if (map.getLayer('unclustered-points')) {
    map.setPaintProperty(
      'unclustered-points',
      'circle-color',
      priceColorExpression(getActiveFuelPriceKey())
    );
  }
}

// Fit the map to valid station bounds (or the Québec bbox as a fallback).
function fitQuebecBounds() {
  if (!map || !map.fitBounds) return;

  let bounds = QUEBEC_BOUNDS;
  if (currentStations && Array.isArray(currentStations.features) && currentStations.features.length) {
    let minLng = Infinity;
    let minLat = Infinity;
    let maxLng = -Infinity;
    let maxLat = -Infinity;
    currentStations.features.forEach((f) => {
      const [lng, lat] = f.geometry?.coordinates || [];
      if (!isValidQuebecCoordinate(lng, lat)) return;
      if (lng < minLng) minLng = lng;
      if (lat < minLat) minLat = lat;
      if (lng > maxLng) maxLng = lng;
      if (lat > maxLat) maxLat = lat;
    });
    if (Number.isFinite(minLng) && Number.isFinite(minLat) && Number.isFinite(maxLng) && Number.isFinite(maxLat)) {
      bounds = [[minLng, minLat], [maxLng, maxLat]];
    }
  }

  map.fitBounds(bounds, { padding: { top: 80, bottom: 80, left: 80, right: 80 }, duration: 0 });
}

// Add / remove the range circle. The circle only exists in radius mode.
function addRangeCircle() {
  if (map.getSource('range-circle')) {
    map.removeSource('range-circle');
  }
  if (map.getLayer('range-circle')) {
    map.removeLayer('range-circle');
  }
  if (map.getLayer('range-circle-border')) {
    map.removeLayer('range-circle-border');
  }

  if (!radiusMode) return;

  // Calculate circle coordinates around the current reference point.
  const circle = createCircle(getEffectiveReferencePoint(), rangeRadius.value);
  
  map.addSource('range-circle', {
    type: 'geojson',
    data: circle
  });

  // Circle fill
  map.addLayer({
    id: 'range-circle',
    type: 'fill',
    source: 'range-circle',
    paint: {
      'fill-color': '#1a73e8',
      'fill-opacity': 0.15
    }
  });

  // Circle border
  map.addLayer({
    id: 'range-circle-border',
    type: 'line',
    source: 'range-circle',
    paint: {
      'line-color': '#1a73e8',
      'line-width': 2,
      'line-dasharray': [5, 5]
    }
  });
}

// Create circle GeoJSON
function createCircle(center, radiusKm) {
  const coordinates = [];
  const numPoints = 64;
  
  for (let i = 0; i < numPoints; i++) {
    const angle = (i / numPoints) * 2 * Math.PI;
    const point = destination(center, radiusKm, angle);
    coordinates.push(point);
  }
  
  // Close the ring
  coordinates.push(coordinates[0]);
  
  return {
    type: 'Feature',
    geometry: {
      type: 'Polygon',
      coordinates: [coordinates]
    }
  };
}

// Calculate destination point given origin, distance and bearing
function destination(origin, distanceKm, bearing) {
  const R = 6371; // Earth radius in km
  const lat1 = toRad(origin[1]);
  const lon1 = toRad(origin[0]);
  const bearingRad = toRad(bearing);
  const distanceRad = distanceKm / R;
  
  const lat2 = Math.asin(
    Math.sin(lat1) * Math.cos(distanceRad) +
    Math.cos(lat1) * Math.sin(distanceRad) * Math.cos(bearingRad)
  );
  
  const lon2 = lon1 + Math.atan2(
    Math.sin(bearingRad) * Math.sin(distanceRad) * Math.cos(lat1),
    Math.cos(distanceRad) - Math.sin(lat1) * Math.sin(lat2)
  );
  
  return [toDeg(lon2), toDeg(lat2)];
}

function toRad(deg) { return deg * Math.PI / 180; }
function toDeg(rad) { return rad * 180 / Math.PI; }

// Show popup (called from unclustered-points click)
function showPopup(feature) {
  // Delegate to shared popup in stats.js
  import('./stats.js').then(mod => mod.showPopup(feature));
}

// Test hook: expose the current number of features in the station source.
window.__qcGasMap = {
  getStationFeatureCount: () => {
    const source = map && map.getSource ? map.getSource('stations') : null;
    if (!source) return -1;
    return source._data?.features?.length ?? -1;
  },
  getStationFeatures: () => {
    const source = map && map.getSource ? map.getSource('stations') : null;
    return source?._data?.features ?? [];
  },
  hasRangeCircle: () => Boolean(map && map.getSource && map.getSource('range-circle')),
  isValidQuebecCoordinate,
  isRadiusMode,
  getReferencePoint,
  getEffectiveReferencePoint
};

export { map, currentStations, MONTREAL_CENTER, addRangeCircle };


// Mapbox initialization and map configuration
import { t, tf, onLanguageChange } from './i18n.js';
import { brandColor, brandAbbr } from './constants.js';
import { updateStats } from './stats.js';

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

// Montréal West Island center
const MONTREAL_CENTER = [-73.7, 45.45];

let map;
let currentStations = [];
export const rangeRadius = { value: 5 }; // km, shared mutable reference
let pulseAnimationId = null;

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

  map = new mapboxgl.Map({
    container: 'map',
    style: 'mapbox://styles/mapbox/light-v11',
    center: MONTREAL_CENTER,
    zoom: 10,
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

  // Click on map to set center
  map.on('click', (e) => {
    const features = map.queryRenderedFeatures(e.point);
    if (features.length > 0) return;
    
    MONTREAL_CENTER[0] = e.lngLat.lng;
    MONTREAL_CENTER[1] = e.lngLat.lat;
    addRangeCircle();
  });
}

// Station count for i18n reactivity
let stationCount = 0;

function updateDataStatus() {
  const el = document.getElementById('data-status');
  if (stationCount > 0) {
    el.textContent = tf('dataLoaded', { n: stationCount });
  } else {
    el.textContent = t('dataLoading');
  }
  el.dataset.count = stationCount;
}

// Listen for language changes to keep data-status in sync
onLanguageChange(() => updateDataStatus());

// Load stations data
export async function loadStations() {
  document.getElementById('loading').classList.add('active');
  
  try {
    const response = await fetch('data/stations.json');
    const data = await response.json();
    currentStations = data;
    stationCount = data.metadata.total_stations;
    
    console.log('Loaded', data.features.length, 'stations');
    
    // Initialize brand filters — sorted by popularity (most stations first)
    const brandCount = {};
    data.features.forEach(f => {
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
      document.querySelectorAll('.brand-filter').forEach(cb => { cb.checked = !allChecked; });
      document.querySelectorAll('.brand-filter-item').forEach(item => {
        const cb = item.querySelector('input');
        item.classList.toggle('active', cb.checked);
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
      const countSize = compact ? '10px' : '11px';
      item.innerHTML = `<input type="checkbox" class="brand-filter" value="${brand}" checked>
        <span style="width:${iconSize};height:${iconSize};border-radius:4px;background:${color};display:inline-flex;align-items:center;justify-content:center;font-size:${fontSize};font-weight:700;color:#fff;flex-shrink:0;">${abbr}</span>
        <span>${brand}</span>
        <span class="brand-count" style="margin-left:auto;font-size:${countSize};color:#94a3b8;">${count}</span>`;
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
    
    // Initialize region filter
    const regionContainer = document.getElementById('region-filter');
    const regions = [...new Set(data.features.map(f => f.properties.region))].sort();
    regions.forEach(region => {
      const option = document.createElement('option');
      option.value = region;
      option.textContent = region;
      regionContainer.appendChild(option);
    });

    // Add layers
    addStationLayers();
    
    // Add range circle
    addRangeCircle();
    
    // Update UI
    updateDataStatus();
    updateStats();
    
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
          'circle-color': [
            'interpolate',
            ['linear'],
            ['get', 'regular_price'],
            165, '#16a34a',  // Green - cheap
            180, '#eab308',  // Yellow - medium
            200, '#dc2626'   // Red - expensive
          ],
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

// Add range circle
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

  // Calculate circle coordinates
  const circle = createCircle(MONTREAL_CENTER, rangeRadius.value);
  
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

export { map, currentStations, MONTREAL_CENTER, addRangeCircle };


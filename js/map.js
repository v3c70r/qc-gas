// Mapbox initialization and map configuration
const mapboxToken = import.meta.env.VITE_MAPBOX_ACCESS_TOKEN;

if (!mapboxToken) {
  console.error('MAPBOX_TOKEN is not set. Please add VITE_MAPBOX_ACCESS_TOKEN to your .env file.');
  document.body.innerHTML = '<div style="padding:40px;text-align:center;font-family:sans-serif;"><h1>Configuration Error</h1><p>Please set <code>VITE_MAPBOX_ACCESS_TOKEN</code> in your <code>.env</code> file.</p></div>';
}

mapboxgl.accessToken = mapboxToken;

// Montréal West Island center
const MONTREAL_CENTER = [-73.7, 45.45];

let map;
let currentStations = [];
let rangeRadius = 5; // km
let pulseAnimationId = null;

// Initialize map with modern light style
export function initMap() {
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
    unit: 'metric'
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

// Load stations data
export async function loadStations() {
  document.getElementById('loading').classList.add('active');
  
  try {
    const response = await fetch('data/stations.json');
    const data = await response.json();
    currentStations = data;
    
    console.log('Loaded', data.features.length, 'stations');
    
    // Initialize brand filters with logos
    const brands = [...new Set(data.features.map(f => f.properties.brand).filter(Boolean))].sort();
    const brandContainer = document.getElementById('brand-filters');
    const brandLogos = {
      'AMI': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#0066CC"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">A</text></svg>',
      'Aucun': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#888"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">?</text></svg>',
      'Axco': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF6600"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">AX</text></svg>',
      'Beausoir': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#1E90FF"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">B</text></svg>',
      'Belzile': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF4500"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">B</text></svg>',
      'Bélisle': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#228B22"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">BL</text></svg>',
      'Canadian Tire': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF6600"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">CT</text></svg>',
      'Costco': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#0065AD"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">C</text></svg>',
      'Couche-Tard': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#0055A4"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">CT</text></svg>',
      'Crevier': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#4169E1"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">C</text></svg>',
      'Eko': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#32CD32"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">E</text></svg>',
      'Esso': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#E31C1C"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">E</text></svg>',
      'Francis': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF8C00"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">F</text></svg>',
      'Gaz-O-Bar': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF6347"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">GB</text></svg>',
      'Harnois': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF6600"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">H</text></svg>',
      'Irving': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#E31837"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">I</text></svg>',
      'Le Relais': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#228B22"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">LR</text></svg>',
      'Little Tree': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#2E8B57"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">LT</text></svg>',
      'MacEwen': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#006400"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">M</text></svg>',
      'Miraco': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#4169E1"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">M</text></svg>',
      'Mobil': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#0066CC"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">M</text></svg>',
      'Nutrinor Énergies': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF8C00"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">N</text></svg>',
      'Paddock': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#8B4513"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">P</text></svg>',
      'Paquet': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF6347"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">P</text></svg>',
      'Pepco': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF4500"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">P</text></svg>',
      'Petro-Canada': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#D00"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">P</text></svg>',
      'Petroplus': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#4169E1"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">P</text></svg>',
      'Pétro-Québec': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#0066CC"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">PQ</text></svg>',
      'Pétro-T': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF6600"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">PT</text></svg>',
      'Pétroles Maurice': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF8C00"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">PM</text></svg>',
      'Quickie': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF6347"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">Q</text></svg>',
      'Sergaz': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF4500"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">S</text></svg>',
      'Shell': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#ED1118"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">S</text></svg>',
      'Sonic': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF4500"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">S</text></svg>',
      'Stinson': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#4169E1"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">S</text></svg>',
      'Super Gaz': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#FF6347"/><text x="12" y="15" font-size="4" text-anchor="middle" fill="white">SG</text></svg>',
      'Ultramar': '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#1C75BC"/><text x="12" y="15" font-size="5" text-anchor="middle" fill="white">U</text></svg>',
    };
    
    brands.slice(0, 12).forEach(brand => {
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;align-items:center;gap:4px;font-size:12px;padding:2px 0';
      const logo = brandLogos[brand] || '<svg viewBox="0 0 24 24" width="18" height="18"><circle cx="12" cy="12" r="10" fill="#666"/><text x="12" y="15" font-size="6" text-anchor="middle" fill="white">?</text></svg>';
      label.innerHTML = `
        <input type="checkbox" class="brand-filter" value="${brand}" checked>
        ${logo}
        <span>${brand}</span>
      `;
      brandContainer.appendChild(label);
    });
    
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
    document.getElementById('data-status').textContent = 
      `Données: ${data.metadata.total_stations} stations | ${new Date().toLocaleString('fr-CA')}`;
    
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
  const circle = createCircle(MONTREAL_CENTER, rangeRadius);
  
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

// Show popup
function showPopup(feature) {
  const props = feature.properties;
  const priceInfo = [];
  
  if (props.regular_price) priceInfo.push(`Régulier: ${props.regular_price.toFixed(1)}¢`);
  if (props.super_price) priceInfo.push(`Super: ${props.super_price.toFixed(1)}¢`);
  if (props.diesel_price) priceInfo.push(`Diesel: ${props.diesel_price.toFixed(1)}¢`);
  
  const popupContent = `
    <div style="padding: 8px; min-width: 200px;">
      <h3 style="margin: 0 0 8px 0; font-size: 14px;">${props.name || 'Station'}</h3>
      <p style="margin: 4px 0; font-size: 12px; color: #666;">${props.brand}</p>
      <p style="margin: 4px 0; font-size: 12px;">${props.address}</p>
      <div style="margin-top: 8px; padding: 8px; background: #f8f9fa; border-radius: 4px; font-size: 11px;">
        ${priceInfo.join('<br>')}
      </div>
    </div>
  `;
  
  const popup = new mapboxgl.Popup({
    closeButton: true,
    closeOnClick: true,
    anchor: 'bottom'
  })
    .setLngLat(feature.geometry.coordinates)
    .setHTML(popupContent)
    .addTo(map);
}

export { map, currentStations, MONTREAL_CENTER, rangeRadius };


// Statistics and range calculations
import { map, MONTREAL_CENTER, currentStations } from './map.js';

// Haversine distance calculation
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

// Filter stations based on criteria
function filterStations() {
  const radiusBtn = document.querySelector('.range-btn.active');
  const radiusKm = radiusBtn ? parseFloat(radiusBtn.dataset.radius) : 5;
  
  const selectedBrands = new Set();
  document.querySelectorAll('.brand-filter:checked').forEach(cb => {
    selectedBrands.add(cb.value);
  });
  
  const selectedFuelTypes = {};
  document.querySelectorAll('.fuel-filter').forEach(cb => {
    selectedFuelTypes[cb.value] = cb.checked;
  });
  
  const priceMin = parseFloat(document.getElementById('min-price').value);
  const priceMax = parseFloat(document.getElementById('max-price').value);
  const selectedRegion = document.getElementById('region-filter').value;
  
  return currentStations.features.filter(feat => {
    const props = feat.properties;
    const coords = feat.geometry.coordinates;
    
    const distance = haversineDistance(
      MONTREAL_CENTER[0], MONTREAL_CENTER[1],
      coords[0], coords[1]
    );
    if (distance > radiusKm) return false;
    
    if (selectedBrands.size > 0 && !selectedBrands.has(props.brand)) return false;
    
    if (props.regular_price !== null) {
      if (props.regular_price < priceMin || props.regular_price > priceMax) return false;
    }
    
    if (selectedRegion && props.region !== selectedRegion) return false;
    
    const hasSelectedFuel = 
      (selectedFuelTypes.regular && props.regular_price !== null) ||
      (selectedFuelTypes.super && props.super_price !== null) ||
      (selectedFuelTypes.diesel && props.diesel_price !== null);
    
    return hasSelectedFuel;
  });
}

// Update statistics
function updateStats() {
  const filtered = filterStations();
  const stats = { regular: [], super: [], diesel: [] };
  
  filtered.forEach(feat => {
    const props = feat.properties;
    if (props.regular_price !== null) stats.regular.push(props.regular_price);
    if (props.super_price !== null) stats.super.push(props.super_price);
    if (props.diesel_price !== null) stats.diesel.push(props.diesel_price);
  });
  
  document.getElementById('station-count').textContent = filtered.length;
  updateStatElement('regular-stats', stats.regular);
  updateStatElement('super-stats', stats.super);
  updateStatElement('diesel-stats', stats.diesel);
  updateLowestPriceHighlight(filtered);
  updateStationList(filtered);
  
  return filtered;
}

function updateStatElement(elementId, prices) {
  const el = document.getElementById(elementId);
  if (prices.length === 0) {
    el.innerHTML = '-';
    return;
  }
  
  const min = Math.min(...prices);
  const max = Math.max(...prices);
  const avg = prices.reduce((a, b) => a + b, 0) / prices.length;
  
  el.innerHTML = `
    <span class="lowest">${min.toFixed(1)}¢</span>
    <span style="color: #999;">-</span>
    <span>${max.toFixed(1)}¢</span>
    <span style="color: #999;">(avg ${avg.toFixed(1)}¢)</span>
  `;
}

// Update lowest price highlight
let pulseAnimationId = null;

function updateLowestPriceHighlight(filteredStations) {
  if (map.getSource('lowest-price')) map.removeSource('lowest-price');
  if (map.getLayer('lowest-price')) map.removeLayer('lowest-price');
  if (map.getLayer('lowest-price-border')) map.removeLayer('lowest-price-border');
  
  if (pulseAnimationId) {
    cancelAnimationFrame(pulseAnimationId);
    pulseAnimationId = null;
  }
  
  if (filteredStations.length === 0) return;
  
  let lowest = null;
  let lowestPrice = Infinity;
  
  filteredStations.forEach(feat => {
    const price = feat.properties.regular_price;
    if (price !== null && price < lowestPrice) {
      lowestPrice = price;
      lowest = feat;
    }
  });
  
  if (!lowest) return;
  
  map.addSource('lowest-price', {
    type: 'geojson',
    data: lowest
  });
  
  map.addLayer({
    id: 'lowest-price',
    type: 'circle',
    source: 'lowest-price',
    paint: {
      'circle-color': '#28a745',
      'circle-radius': 10,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2
    }
  });
  
  map.addLayer({
    id: 'lowest-price-border',
    type: 'circle',
    source: 'lowest-price',
    paint: {
      'circle-color': '#28a745',
      'circle-radius': 15,
      'circle-opacity': 0.5,
      'circle-stroke-color': '#ffffff',
      'circle-stroke-width': 2
    }
  });
  
  pulseLowestPrice();
}

function pulseLowestPrice() {
  let radius = 15;
  let opacity = 0.5;
  let growing = true;
  
  function animate() {
    if (growing) {
      radius += 1;
      opacity -= 0.02;
      if (radius >= 30 || opacity <= 0) growing = false;
    } else {
      radius -= 1;
      opacity += 0.02;
      if (radius <= 15 || opacity >= 0.5) growing = true;
    }
    
    map.setPaintProperty('lowest-price-border', 'circle-radius', radius);
    map.setPaintProperty('lowest-price-border', 'circle-opacity', opacity);
    
    pulseAnimationId = requestAnimationFrame(animate);
  }
  
  animate();
}

// Update station list
function updateStationList(filteredStations = null) {
  if (filteredStations === null) filteredStations = filterStations();
  
  const list = document.getElementById('station-list');
  list.innerHTML = '';
  
  if (filteredStations.length === 0) {
    list.innerHTML = '<div style="padding: 16px; text-align: center; color: #666;">Aucune station trouvée</div>';
    return;
  }
  
  filteredStations.sort((a, b) => {
    const priceA = a.properties.regular_price || Infinity;
    const priceB = b.properties.regular_price || Infinity;
    return priceA - priceB;
  });
  
  filteredStations.slice(0, 20).forEach(feat => {
    const props = feat.properties;
    const distance = haversineDistance(
      MONTREAL_CENTER[0], MONTREAL_CENTER[1],
      feat.geometry.coordinates[0], feat.geometry.coordinates[1]
    );
    
    const item = document.createElement('div');
    item.className = 'list-item';
    
    const prices = filteredStations.map(f => f.properties.regular_price).filter(p => p !== null);
    if (props.regular_price === Math.min(...prices)) {
      item.classList.add('highlight');
    }
    
    item.innerHTML = `
      <div class="name">${props.name}</div>
      <div class="details">${props.brand} • ${props.address}</div>
      <div class="details" style="margin-top: 4px;">
        <span class="price">${props.regular_price ? props.regular_price.toFixed(1) + '¢' : '-'}</span>
        <span style="color: #999; margin-left: 8px;">${distance.toFixed(1)} km</span>
      </div>
    `;
    
    item.addEventListener('click', () => {
      map.flyTo({
        center: feat.geometry.coordinates,
        zoom: 15,
        essential: true
      });
    });
    
    list.appendChild(item);
  });
}

export { filterStations, updateStats, updateLowestPriceHighlight, updateStationList };

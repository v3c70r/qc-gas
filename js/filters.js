// Filter controls logic
import { updateStats, updateStationList } from './stats.js';
import { map, MONTREAL_CENTER } from './map.js';

// Initialize filters
function initFilters() {
  document.addEventListener('change', (e) => {
    if (e.target.classList.contains('brand-filter')) {
      updateStats();
    }
    if (e.target.classList.contains('fuel-filter')) {
      updateStats();
    }
  });
  
  document.getElementById('region-filter').addEventListener('change', () => {
    updateStats();
  });
  
  const minSlider = document.getElementById('min-price');
  const maxSlider = document.getElementById('max-price');
  const minLabel = document.getElementById('price-min-label');
  const maxLabel = document.getElementById('price-max-label');
  
  function updatePriceLabels() {
    minLabel.textContent = (parseFloat(minSlider.value) / 100).toFixed(2);
    maxLabel.textContent = (parseFloat(maxSlider.value) / 100).toFixed(2);
    updateStats();
  }
  
  minSlider.addEventListener('input', updatePriceLabels);
  maxSlider.addEventListener('input', updatePriceLabels);
  
  const rangeButtons = document.querySelectorAll('.range-btn');
  rangeButtons.forEach(btn => {
    btn.addEventListener('click', () => {
      rangeButtons.forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      updateStats();
    });
  });
}

// Geolocation
function initGeolocation() {
  const btn = document.getElementById('current-location');
  
  btn.addEventListener('click', () => {
    if (!navigator.geolocation) {
      alert('Geolocation n\'est pas supporté par votre navigateur');
      return;
    }
    
    btn.disabled = true;
    btn.textContent = '⏳ Localisation...';
    
    navigator.geolocation.getCurrentPosition(
      (position) => {
        const { latitude, longitude } = position.coords;
        MONTREAL_CENTER[0] = longitude;
        MONTREAL_CENTER[1] = latitude;
        map.setCenter([longitude, latitude]);
        map.setZoom(12);
        updateStats();
        updateStationList();
        btn.disabled = false;
        btn.textContent = '📍 Ma position';
      },
      (error) => {
        console.error('Geolocation error:', error);
        btn.disabled = false;
        btn.textContent = '📍 Ma position';
        
        let message = 'Impossible de déterminer votre position';
        if (error.code === error.PERMISSION_DENIED) {
          message = 'Accès à la localisation refusé. Veuillez l\'activer dans les paramètres du navigateur.';
        } else if (error.code === error.POSITION_UNAVAILABLE) {
          message = 'Position non available. Veuillez réessayer.';
        } else if (error.code === error.TIMEOUT) {
          message = 'Délai d\'attente dépassé. Veuillez réessayer.';
        }
        alert(message);
      },
      {
        enableHighAccuracy: true,
        timeout: 10000,
        maximumAge: 300000
      }
    );
  });
}

// Reset view
function initResetView() {
  document.getElementById('reset-view').addEventListener('click', () => {
    map.flyTo({
      center: [-73.7, 45.45],
      zoom: 10,
      essential: true
    });
    updateStats();
  });
}

// Toggle sidebar
function initSidebarToggle() {
  const toggleBtn = document.getElementById('toggle-sidebar');
  const sidebar = document.getElementById('sidebar');
  
  toggleBtn.addEventListener('click', () => {
    sidebar.classList.toggle('collapsed');
    sidebar.classList.toggle('expanded');
  });
}

export { initFilters, initGeolocation, initResetView, initSidebarToggle };

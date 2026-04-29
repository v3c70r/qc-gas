// Application entry point
import { initMap, loadStations, MONTREAL_CENTER } from './map.js';
import { initFilters, initGeolocation, initResetView, initSidebarToggle } from './filters.js';

function initApp() {
  console.log('Initializing Gas Price Quebec App...');
  
  // Desktop: sidebar expanded by default
  if (window.innerWidth >= 768) {
    document.getElementById('sidebar').classList.add('expanded');
    document.getElementById('sidebar').classList.remove('collapsed');
  } else {
    document.getElementById('sidebar').classList.add('collapsed');
  }
  
  initMap();
  loadStations();
  initFilters();
  initGeolocation();
  initResetView();
  initSidebarToggle();
  console.log('App initialized!');
}

document.addEventListener('DOMContentLoaded', () => {
  initApp();
});

window.MONTREAL_CENTER = MONTREAL_CENTER;

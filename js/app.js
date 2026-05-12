import { initMap, loadStations, MONTREAL_CENTER } from './map.js';
import { initFilters, initGeolocation, initSidebarToggle } from './filters.js';

function initApp() {
  console.log('Initializing Gas Price Quebec App...');
  initMap();
  initFilters();
  initGeolocation();
  initSidebarToggle();

  if (window.innerWidth >= 768) {
    document.getElementById('sidebar').classList.remove('collapsed');
  } else {
    document.getElementById('sidebar').classList.add('collapsed');
  }
  console.log('App initialized!');
}

document.addEventListener('DOMContentLoaded', initApp);
window.MONTREAL_CENTER = MONTREAL_CENTER;
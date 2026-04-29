// Application entry point
import { initMap, MONTREAL_CENTER } from './map.js';
import { initFilters, initGeolocation, initResetView, initSidebarToggle } from './filters.js';

function initApp() {
  console.log('Initializing Gas Price Quebec App...');
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

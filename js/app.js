import { initMap, loadStations, MONTREAL_CENTER } from './map.js';
import { initFilters, initGeolocation, initSidebarToggle } from './filters.js';
import { getStoredLanguage, createLanguageSelector, applyTranslations } from './i18n.js';

function initApp() {
  console.log('Initializing Gas Price Quebec App...');

  const lang = getStoredLanguage();
  createLanguageSelector();
  applyTranslations(lang);

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
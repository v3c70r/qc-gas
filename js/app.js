import { initMap, loadStations, MONTREAL_CENTER } from './map.js';
import { initFilters, initGeolocation, initSidebarToggle } from './filters.js';
import { getStoredLanguage, createLanguageSelector, applyTranslations } from './i18n.js';

function initKeyboardShortcuts() {
  document.addEventListener('keydown', (e) => {
    // Ignore when typing in inputs
    const tag = e.target.tagName;
    if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || e.target.isContentEditable) return;

    switch (e.key.toLowerCase()) {
      case 'f':
        e.preventDefault();
        document.getElementById('filter-toggle').click();
        break;
      case 'l':
        e.preventDefault();
        document.getElementById('locate-btn').click();
        break;
      case 'escape': {
        const sidebar = document.getElementById('sidebar');
        const isMobile = window.innerWidth < 768;
        if (isMobile && !sidebar.classList.contains('collapsed')) {
          sidebar.classList.add('collapsed');
        }
        break;
      }
    }
  });
}

function initApp() {
  console.log('Initializing Gas Price Quebec App...');

  const lang = getStoredLanguage();
  createLanguageSelector();
  applyTranslations(lang);

  initMap();
  initFilters();
  initGeolocation();
  initSidebarToggle();
  initKeyboardShortcuts();

  if (window.innerWidth >= 768) {
    document.getElementById('sidebar').classList.remove('collapsed');
  } else {
    document.getElementById('sidebar').classList.add('collapsed');
  }
  console.log('App initialized!');
}

document.addEventListener('DOMContentLoaded', initApp);
window.MONTREAL_CENTER = MONTREAL_CENTER;
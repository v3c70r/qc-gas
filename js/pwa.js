// PWA lifecycle helpers: service-worker registration, install prompt UI,
// iOS "Add to Home Screen" guidance, and honest offline status.
import { t, tf, getLanguage, onLanguageChange } from './i18n.js';

const FROM_CACHE_HEADER = 'X-QCGas-From-Cache';

let offline = typeof navigator !== 'undefined' && !navigator.onLine;
let fromCache = false;
let generatedAt = null;
let stationCount = 0;
let installPrompt = null;
let bannerVisible = false;

function isStandalone() {
  return window.matchMedia('(display-mode: standalone)').matches ||
    (window.navigator.standalone === true);
}

function isIOS() {
  return /iphone|ipad|ipod/i.test(navigator.userAgent);
}

function canRegisterServiceWorker() {
  return import.meta.env.PROD && window.isSecureContext && 'serviceWorker' in navigator;
}

function formatSyncTime(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(getLanguage(), {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
}

// ── #data-status rendering ────────────────────────────────────────────────

function renderStatus() {
  const el = document.getElementById('data-status');
  if (!el) return;

  if (offline || fromCache) {
    el.classList.add('offline');
    const sync = formatSyncTime(generatedAt);
    el.textContent = sync
      ? `${t('offlineStatus')} · ${t('lastSync')} ${sync}`
      : t('offlineStatus');
  } else {
    el.classList.remove('offline');
    el.textContent = stationCount > 0
      ? tf('dataLoaded', { n: stationCount })
      : t('dataLoading');
  }
  el.dataset.count = String(stationCount);
}

onLanguageChange(() => renderStatus());

// Called by map.js when a station snapshot is loaded (fresh or from cache).
export function setDataSnapshot({ generatedAt: iso, fromCache: cached, stationCount: count }) {
  generatedAt = iso || null;
  fromCache = Boolean(cached);
  stationCount = Number(count) || 0;
  renderStatus();
}

export function setOffline(value) {
  offline = Boolean(value);
  renderStatus();
}

// Used by map.js to decide whether a response was served from the SW cache.
export function isResponseFromCache(response) {
  return response && response.headers.get(FROM_CACHE_HEADER) === '1';
}

// ── Install entry ─────────────────────────────────────────────────────────

function createInstallBanner(ios) {
  if (bannerVisible) return;
  bannerVisible = true;

  const wrap = document.createElement('div');
  wrap.id = 'pwa-install';
  wrap.className = 'pwa-install';

  const text = document.createElement('span');
  text.className = 'pwa-install-text';
  text.dataset.i18n = ios ? 'iosInstallGuide' : 'installApp';
  text.textContent = t(ios ? 'iosInstallGuide' : 'installApp');

  const actions = document.createElement('div');
  actions.className = 'pwa-install-actions';

  let installBtn = null;
  if (!ios) {
    installBtn = document.createElement('button');
    installBtn.id = 'pwa-install-btn';
    installBtn.className = 'pwa-install-btn';
    installBtn.dataset.i18n = 'installApp';
    installBtn.textContent = t('installApp');
    installBtn.addEventListener('click', () => {
      if (!installPrompt) return;
      installPrompt.prompt();
      if (installPrompt.userChoice) {
        installPrompt.userChoice.then((choice) => {
          if (choice.outcome === 'accepted') {
            bannerVisible = false;
            wrap.remove();
          }
        });
      }
    });
    actions.appendChild(installBtn);
  }

  const close = document.createElement('button');
  close.id = 'pwa-install-close';
  close.className = 'pwa-install-close';
  close.setAttribute('aria-label', t('installAppDismiss'));
  close.textContent = '✕';
  close.addEventListener('click', () => {
    bannerVisible = false;
    wrap.remove();
  });
  actions.appendChild(close);

  wrap.appendChild(text);
  wrap.appendChild(actions);
  document.body.appendChild(wrap);

  onLanguageChange(() => {
    text.textContent = t(ios ? 'iosInstallGuide' : 'installApp');
    if (installBtn) installBtn.textContent = t('installApp');
    close.setAttribute('aria-label', t('installAppDismiss'));
  });
}

function initInstallUI() {
  if (isStandalone()) return;

  window.addEventListener('beforeinstallprompt', (event) => {
    event.preventDefault();
    installPrompt = event;
    createInstallBanner(false);
  });

  window.addEventListener('appinstalled', () => {
    bannerVisible = false;
    document.getElementById('pwa-install')?.remove();
  });

  // iOS Safari does not fire beforeinstallprompt; show honest guidance instead.
  if (isIOS() && !isStandalone()) {
    createInstallBanner(true);
  }
}

// ── Offline / online listeners ────────────────────────────────────────────

function initOfflineUI() {
  window.addEventListener('online', () => setOffline(false));
  window.addEventListener('offline', () => setOffline(true));
}

function registerServiceWorker() {
  if (!canRegisterServiceWorker()) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js').catch((error) => {
      console.warn('Service worker registration failed', error);
    });
  });
}

export function initPWA() {
  registerServiceWorker();
  initOfflineUI();
  initInstallUI();
  renderStatus();
}

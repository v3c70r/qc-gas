// PWA lifecycle helpers: service-worker registration, update notice, install
// prompt UI, iOS "Add to Home Screen" guidance, and honest offline status.
import { t, tf, getLanguage, onLanguageChange } from './i18n.js';

const FROM_CACHE_HEADER = 'X-QCGas-From-Cache';
// A returning user should learn about a new release quickly, but focus storms
// must not hammer the network with SW update checks.
const UPDATE_CHECK_INTERVAL_MS = 60 * 60 * 1000;

let offline = typeof navigator !== 'undefined' && !navigator.onLine;
let fromCache = false;
let generatedAt = null;
let stationCount = 0;
let installPrompt = null;
let bannerVisible = false;
let serviceWorkerRegistration = null;
let waitingWorker = null;
let updateBannerVisible = false;
let applyingUpdate = false;
let reloadedForUpdate = false;
let lastUpdateCheck = 0;

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

// ── Update flow (issue #45) ───────────────────────────────────────────────
// The service worker is network-first for navigations, so a reload is enough to
// pick up a new release. This part only makes that decision visible and
// explicit for the user instead of silently keeping a stale shell.

function isUpdateAvailable() {
  return Boolean(waitingWorker);
}

function setRegistration(registration) {
  if (!registration || typeof registration.addEventListener !== 'function') return;
  serviceWorkerRegistration = registration;

  registration.addEventListener('updatefound', () => handleUpdateFound(registration));

  // A worker that was already waiting (e.g. installed while the page was in the
  // background) also counts as an available update.
  if (registration.waiting && navigator.serviceWorker.controller) {
    showUpdateBanner(registration.waiting);
  }
}

function handleUpdateFound(registration) {
  const worker = registration.installing;
  if (!worker) return;

  worker.addEventListener('statechange', () => {
    // "installed" while the page is already controlled means a new version is
    // ready to take over; a first install has no controller and needs no banner.
    if (worker.state === 'installed' && navigator.serviceWorker.controller) {
      showUpdateBanner(worker);
    }
  });
}

function showUpdateBanner(worker) {
  waitingWorker = worker || null;
  if (updateBannerVisible) return;
  updateBannerVisible = true;

  const wrap = document.createElement('div');
  wrap.id = 'pwa-update';
  wrap.className = 'pwa-update';
  wrap.setAttribute('role', 'status');
  wrap.setAttribute('aria-live', 'polite');

  const text = document.createElement('span');
  text.className = 'pwa-update-text';
  text.dataset.i18n = 'updateAvailable';
  text.textContent = t('updateAvailable');

  const actions = document.createElement('div');
  actions.className = 'pwa-update-actions';

  const reloadBtn = document.createElement('button');
  reloadBtn.id = 'pwa-update-btn';
  reloadBtn.className = 'pwa-update-btn';
  reloadBtn.dataset.i18n = 'updateReload';
  reloadBtn.textContent = t('updateReload');
  reloadBtn.addEventListener('click', () => applyUpdate());

  const close = document.createElement('button');
  close.id = 'pwa-update-close';
  close.className = 'pwa-update-close';
  close.setAttribute('aria-label', t('updateDismiss'));
  close.textContent = '✕';
  close.addEventListener('click', () => {
    updateBannerVisible = false;
    wrap.remove();
  });

  actions.appendChild(reloadBtn);
  actions.appendChild(close);
  wrap.appendChild(text);
  wrap.appendChild(actions);
  document.body.appendChild(wrap);

  onLanguageChange(() => {
    text.textContent = t('updateAvailable');
    reloadBtn.textContent = t('updateReload');
    close.setAttribute('aria-label', t('updateDismiss'));
  });
}

// Asks the waiting worker to activate; the reload itself happens on
// `controllerchange` so it is guaranteed to run on the new version.
function applyUpdate() {
  if (!waitingWorker || applyingUpdate) return false;
  applyingUpdate = true;
  waitingWorker.postMessage({ type: 'SKIP_WAITING' });
  return true;
}

function checkForUpdate() {
  const registration = serviceWorkerRegistration;
  if (!registration || typeof registration.update !== 'function') return;

  const now = Date.now();
  if (now - lastUpdateCheck < UPDATE_CHECK_INTERVAL_MS) return;
  lastUpdateCheck = now;

  const result = registration.update();
  if (result && typeof result.catch === 'function') {
    // A transient failure (offline, flaky network) must not lock the next
    // check out for a whole interval.
    result.catch(() => { lastUpdateCheck = 0; });
  }
}

function initUpdateFlow() {
  const container = typeof navigator !== 'undefined' ? navigator.serviceWorker : null;
  if (!container || typeof container.addEventListener !== 'function') return;

  container.addEventListener('controllerchange', () => {
    // Reload only after the user accepted the update, and only once, so a
    // claimed client can never end up in a reload loop.
    if (!applyingUpdate || reloadedForUpdate) return;
    reloadedForUpdate = true;
    window.location.reload();
  });

  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) checkForUpdate();
  });
  window.addEventListener('focus', checkForUpdate);
}

// ── Offline / online listeners ────────────────────────────────────────────

function initOfflineUI() {
  window.addEventListener('online', () => setOffline(false));
  window.addEventListener('offline', () => setOffline(true));
}

function registerServiceWorker() {
  if (!canRegisterServiceWorker()) return;
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('./sw.js')
      .then((registration) => {
        setRegistration(registration);
        checkForUpdate();
      })
      .catch((error) => {
        console.warn('Service worker registration failed', error);
      });
  });
}

export function initPWA() {
  initUpdateFlow();
  registerServiceWorker();
  initOfflineUI();
  initInstallUI();
  renderStatus();
}

// Test/console hooks (mirrors window.__qcGasMap / window.__qcGasSearch).
// The update flow is otherwise unreachable in dev where no service worker is
// registered (import.meta.env.PROD is false).
if (typeof window !== 'undefined') {
  window.__qcGasPwa = {
    notifyUpdateAvailable: (worker) => showUpdateBanner(worker),
    applyUpdate,
    checkForUpdate,
    setRegistration,
    isUpdateAvailable
  };
}

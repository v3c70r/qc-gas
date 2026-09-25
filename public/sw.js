/* Service worker for Essence Québec PWA.
 *
 * Cache strategy (issue #45):
 *  - Navigation requests (HTML) → network-first, falling back to the cached
 *    shell when offline. Installed users therefore always receive the newest
 *    index.html (and with it the newest hashed asset names) after a deploy.
 *  - Immutable assets (`/assets/`, `/icons/`, css/js/images) → cache-first;
 *    fetched once and kept in the runtime cache.
 *  - Other same-origin GETs → stale-while-revalidate.
 *  - data/*.json (price snapshots) → network-first; on success the response
 *    is cached and returned, on failure the last cached snapshot is served
 *    with an `X-QCGas-From-Cache` header so the UI can label it honestly.
 *  - `'./'` and `'./index.html'` are deliberately NOT precached: they change
 *    on every release, so navigation requests refresh them instead.
 *  - The cache name carries a version — bump CACHE_VERSION whenever this file
 *    changes; `activate` removes our older caches (and only ours).
 *  - install does not call skipWaiting(): the page shows an update banner and
 *    only sends SKIP_WAITING when the user chooses to reload.
 */
const CACHE_VERSION = 'qc-gas-v2-2026-09-25';
const CACHE_NAME = CACHE_VERSION;
// Only caches created by this worker are cleaned up; other origin caches
// (e.g. Mapbox GL JS tile storage) are left untouched.
const CACHE_PREFIX = 'qc-gas-';

// sw.js lives at the deployed app root, so this is the base URL
// (https://qgu.io/qc-gas/ in production).
const APP_BASE = new URL('./', self.location).href;
// Canonical cache key for the navigation fallback (the current shell).
const INDEX_URL = new URL('index.html', APP_BASE).href;

// Only truly static files: the manifest and the icons. Nothing here changes
// between releases in a way that would break a stale copy.
const APP_SHELL = [
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/maskable-512.png',
  './icons/apple-touch-icon-180.png'
];

const DATA_PATH = '/data/';
const IMMUTABLE_PATH = /\/(assets|icons)\//;
const IMMUTABLE_EXT = /\.(css|js|mjs|png|svg|webp|ico|woff2?|ttf)$/;

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key.startsWith(CACHE_PREFIX) && key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

// The page asks for the waiting worker to take over once the user accepts the
// update (see js/pwa.js); until then the old worker keeps serving.
self.addEventListener('message', (event) => {
  if (event.data && event.data.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.includes(DATA_PATH)) {
    event.respondWith(dataNetworkFirst(request));
    return;
  }

  if (isNavigationRequest(request)) {
    event.respondWith(navigationNetworkFirst(request));
    return;
  }

  if (isImmutableAsset(url)) {
    event.respondWith(cacheFirst(request));
    return;
  }

  event.respondWith(staleWhileRevalidate(request));
});

function isNavigationRequest(request) {
  if (request.mode === 'navigate' || request.destination === 'document') return true;
  const accept = request.headers.get('Accept') || '';
  return accept.includes('text/html');
}

function isImmutableAsset(url) {
  return IMMUTABLE_PATH.test(url.pathname) || IMMUTABLE_EXT.test(url.pathname);
}

// Network-first: a deployed shell must win over whatever is cached.
async function navigationNetworkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (!response || !response.ok) throw new Error('Navigation response was not ok');
    await cache.put(INDEX_URL, response.clone());
    return response;
  } catch (error) {
    const cached = (await cache.match(INDEX_URL)) || (await cache.match(request));
    if (cached) return withFromCacheHeader(cached, 'text/html');
    return new Response('', { status: 503, statusText: 'Offline' });
  }
}

async function cacheFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) return cached;

  const response = await fetch(request);
  if (response && response.ok) {
    cache.put(request, response.clone());
  }
  return response;
}

async function staleWhileRevalidate(request) {
  const cache = await caches.open(CACHE_NAME);
  const cached = await cache.match(request);
  const network = fetch(request).then((response) => {
    if (response && response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  });

  if (cached) {
    network.catch(() => {});
    return cached;
  }
  return network;
}

async function dataNetworkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (!response || !response.ok) throw new Error('Network response was not ok');
    cache.put(request, response.clone());
    return response;
  } catch (error) {
    const cached = await cache.match(request);
    if (cached) {
      return withFromCacheHeader(cached, 'application/json');
    }
    return new Response('', { status: 503, statusText: 'Offline' });
  }
}

// Cached content is served with an explicit marker so the UI never presents it
// as live data.
function withFromCacheHeader(cached, fallbackContentType) {
  return new Response(cached.body, {
    status: cached.status,
    statusText: cached.statusText,
    headers: {
      'Content-Type': cached.headers.get('Content-Type') || fallbackContentType,
      'X-QCGas-From-Cache': '1'
    }
  });
}

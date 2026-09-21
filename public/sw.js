/* Service worker for Essence Québec PWA.
 *
 * Cache strategy:
 *  - Application shell (index.html, css, js modules, icons, manifest):
 *    cache-first, so the app opens instantly on repeat visits.
 *  - data/*.json (price snapshots): network-first; on success the response
 *    is cached and returned, on failure the last cached snapshot is served
 *    with an `X-QCGas-From-Cache` header so the UI can label it honestly.
 *  - activate: old cache versions are removed; skipWaiting/clientsClaim let
 *    updates take effect without waiting for all tabs to close.
 */
const CACHE_NAME = 'qc-gas-v1';

const APP_SHELL = [
  './',
  './index.html',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
  './icons/maskable-512.png',
  './icons/apple-touch-icon-180.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => cache.addAll(APP_SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys
          .filter((key) => key !== CACHE_NAME)
          .map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  const request = event.request;
  if (request.method !== 'GET') return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.includes('/data/')) {
    event.respondWith(networkFirst(request));
    return;
  }

  event.respondWith(cacheFirst(request));
});

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

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (!response || !response.ok) throw new Error('Network response was not ok');
    cache.put(request, response.clone());
    return response;
  } catch (error) {
    const cached = await cache.match(request);
    if (cached) {
      return withFromCacheHeader(cached);
    }
    return new Response('', { status: 503, statusText: 'Offline' });
  }
}

function withFromCacheHeader(cached) {
  return new Response(cached.body, {
    status: cached.status,
    statusText: cached.statusText,
    headers: {
      'Content-Type': cached.headers.get('Content-Type') || 'application/json',
      'X-QCGas-From-Cache': '1'
    }
  });
}

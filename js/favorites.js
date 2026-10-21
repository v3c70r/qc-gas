// ── Favorites (localStorage) ──
// Lightweight store for user-favorited stations. Persists locally only; no
// network requests and no account required.
//
// Storage stays a plain `string[]` (so js/databackup.js — which only requires
// non-empty strings — and BACKUP_VERSION are untouched). As of issue #89 a new
// favorite is written as its coordinate key (`coord:<lng>,<lat>`, 5 decimals —
// the same convention as history.js) because the official data occasionally
// renames or re-addresses a station, which silently broke text-key favorites.
// The legacy text key (`name|address|postal_code`) still matches as a fallback,
// and a one-shot, idempotent migration rewrites resolvable text keys.

import { currentStations } from './map.js';

const STORAGE_KEY = 'qc-gas-favorites';
const COORD_PREFIX = 'coord:';

const STAR_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`;

let favorites = load();
const listeners = new Set();
// Last snapshot announced by map.js through `stations:loaded`; kept so the
// migration can run again (backup import) without re-reading map.js.
let snapshotStations = null;

function load() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = JSON.parse(raw || '[]');
    return Array.isArray(parsed) ? parsed.filter(id => typeof id === 'string') : [];
  } catch {
    return [];
  }
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify([...favorites]));
  } catch {
    // Storage may be unavailable (private browsing); favorites simply won't persist.
  }
}

function notify() {
  listeners.forEach(fn => fn());
}

// ── Keys ──

// Stations have no id in the source data, so derive a stable key from the
// fields that identify a physical location. Kept as-is: js/watch.js,
// js/station-card.js, js/stats.js and js/fillups.js depend on this text form.
export function stationId(feature) {
  const p = feature?.properties || {};
  const key = [p.name, p.address, p.postal_code].map(v => (v ?? '').trim()).join('|');
  if (key.replace(/\|/g, '')) return key;
  const c = feature?.geometry?.coordinates;
  return c ? `coord:${c[0]},${c[1]}` : '';
}

// Coordinate key: immutable when only the label changes. Empty when the
// geometry is missing or not a valid lng/lat pair.
function coordinateKey(feature) {
  const c = feature?.geometry?.coordinates;
  if (!Array.isArray(c) || c.length < 2) return '';
  const lng = Number(c[0]);
  const lat = Number(c[1]);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return '';
  return `${COORD_PREFIX}${lng.toFixed(5)},${lat.toFixed(5)}`;
}

// What gets written for a new favorite: the coordinate key when we have one,
// otherwise the text key (same fallback the store always had).
function favoriteId(feature) {
  return coordinateKey(feature) || stationId(feature);
}

// ── Public API ──

export function isFavorite(feature) {
  const coordKey = coordinateKey(feature);
  const textId = stationId(feature);
  // Double-key match: a migrated coordinate entry AND a legacy text entry are
  // both honoured, so an upgrade never loses an existing favorite.
  if (coordKey && favorites.includes(coordKey)) return true;
  return !!textId && favorites.includes(textId);
}

export function toggleFavorite(feature) {
  const coordKey = coordinateKey(feature);
  const textId = stationId(feature);
  const id = coordKey || textId;
  if (!id) return false;
  // Remove every stored form on unfavorite so a legacy text entry can never
  // become undeletable.
  const stored = new Set([id, coordKey, textId].filter(Boolean));
  if (isFavorite(feature)) {
    favorites = favorites.filter(x => !stored.has(x));
  } else {
    favorites = [...favorites, id];
  }
  persist();
  notify();
  return isFavorite(feature);
}

export function getFavoriteCount() {
  return favorites.length;
}

/**
 * One-shot, idempotent migration: rewrite every resolvable legacy text key to
 * the coordinate key of the station that still carries that text identity.
 * Unresolvable keys are kept verbatim (their station may come back), and
 * nothing is persisted unless the list really changed. Returns true when the
 * store was rewritten.
 */
export function resolveFavoriteIds(stations) {
  const features = Array.isArray(stations?.features) ? stations.features : null;
  if (!features) return false;

  const coordByTextId = new Map();
  for (const feature of features) {
    const textId = stationId(feature);
    const coordKey = coordinateKey(feature);
    if (textId && coordKey && !coordByTextId.has(textId)) coordByTextId.set(textId, coordKey);
  }

  const next = [];
  const seen = new Set();
  let changed = false;
  for (const id of favorites) {
    // `coord:` ids are already canonical; anything else may be a legacy key.
    const resolved = id.startsWith(COORD_PREFIX) ? id : (coordByTextId.get(id) || id);
    // Collapse duplicates (both forms of the same station) so a favorite
    // never counts twice.
    if (seen.has(resolved)) {
      changed = true;
      continue;
    }
    seen.add(resolved);
    if (resolved !== id) changed = true;
    next.push(resolved);
  }
  if (!changed) return false;

  favorites = next;
  persist();
  notify();
  return true;
}

function migrateFromKnownSnapshot() {
  if (snapshotStations) resolveFavoriteIds(snapshotStations);
}

// Re-read the store from localStorage. Used by the JSON restore flow (issue
// #57) so an import is reflected in the UI without a page reload.
export function reloadFavorites() {
  favorites = load();
  migrateFromKnownSnapshot();
  notify();
  return favorites.slice();
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// The snapshot arrives asynchronously; migrate exactly once per snapshot. This
// mirrors the price-watch listener added by #65.
if (typeof window !== 'undefined') {
  window.addEventListener('stations:loaded', (event) => {
    if (event?.detail) snapshotStations = event.detail;
    migrateFromKnownSnapshot();
  });
  // The migration needs the station snapshot. It normally arrives through the
  // `stations:loaded` event above, but this module can also be evaluated after
  // the snapshot was applied (HMR). Read map.js's live binding once on a
  // microtask: favorites.js and map.js sit in a static import cycle
  // (map → stats → favorites → map), so the binding is still in its TDZ while
  // this module body runs — a microtask only fires once the graph is evaluated.
  queueMicrotask(() => {
    if (snapshotStations) return;
    const stations = currentStations;
    if (stations && Array.isArray(stations.features) && stations.features.length) {
      snapshotStations = stations;
      migrateFromKnownSnapshot();
    }
  });
}

export { STAR_ICON };

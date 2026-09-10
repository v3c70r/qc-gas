// ── Favorites (localStorage) ──
// Lightweight store for user-favorited stations. Persists locally only; no
// network requests and no account required.

const STORAGE_KEY = 'qc-gas-favorites';

const STAR_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><polygon points="12 2 15.09 8.26 22 9.27 17 14.14 18.18 21.02 12 17.77 5.82 21.02 7 14.14 2 9.27 8.91 8.26 12 2"/></svg>`;

let favorites = load();
const listeners = new Set();

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

// Stations have no id in the source data, so derive a stable key from the
// fields that identify a physical location.
export function stationId(feature) {
  const p = feature?.properties || {};
  const key = [p.name, p.address, p.postal_code].map(v => (v ?? '').trim()).join('|');
  if (key.replace(/\|/g, '')) return key;
  const c = feature?.geometry?.coordinates;
  return c ? `coord:${c[0]},${c[1]}` : '';
}

export function isFavorite(feature) {
  const id = stationId(feature);
  return !!id && favorites.includes(id);
}

export function toggleFavorite(feature) {
  const id = stationId(feature);
  if (!id) return false;
  if (favorites.includes(id)) {
    favorites = favorites.filter(x => x !== id);
  } else {
    favorites = [...favorites, id];
  }
  persist();
  notify();
  return favorites.includes(id);
}

export function getFavoriteCount() {
  return favorites.length;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

export { STAR_ICON };

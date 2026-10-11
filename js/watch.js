// ── Price watch (价格提醒) ──
// Local-only watch list: user picks a station and a price threshold, and the
// app compares the real current snapshot against BOTH the threshold and the
// price observed on the previous visit. No backend, no account, no service
// worker, no push permission and no new network request.
//
// Honesty constraints:
//   • Only real observed prices are used (the current stations snapshot).
//   • The sidebar is explicitly labelled "since last viewed <date>" — it never
//     implies real-time monitoring.
//   • Stations that disappear from the snapshot are kept and labelled
//     "missing" (never silently deleted); missing fuel prices degrade to
//     "price unavailable" instead of a fabricated number.

import { stationId } from './favorites.js';
import { t, tf, translations, getLanguage, onLanguageChange } from './i18n.js';
import { map, currentStations } from './map.js';

export const STORAGE_KEY = 'qc-gas-watch';
const FUEL_KEYS = ['regular', 'super', 'diesel'];

let watchEntries = loadWatch();
const listeners = new Set();

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function round1(v) {
  return Math.round(v * 10) / 10;
}

function normalizeThreshold(value) {
  if (value === null || value === undefined || value === '') return null;
  const n = Number(value);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

function normalizeEntry(data) {
  if (!data || typeof data !== 'object') return null;
  const id = String(data.id || '');
  if (!id) return null;
  return {
    id,
    name: String(data.name || ''),
    brand: String(data.brand || ''),
    address: String(data.address || ''),
    region: String(data.region || ''),
    fuel: FUEL_KEYS.includes(data.fuel) ? data.fuel : 'regular',
    thresholdCents: normalizeThreshold(data.thresholdCents),
    lastSeenPriceCents: (data.lastSeenPriceCents != null && Number.isFinite(Number(data.lastSeenPriceCents)))
      ? Number(data.lastSeenPriceCents)
      : null,
    lastSeenAt: String(data.lastSeenAt || ''),
    lng: (data.lng != null && Number.isFinite(Number(data.lng))) ? Number(data.lng) : null,
    lat: (data.lat != null && Number.isFinite(Number(data.lat))) ? Number(data.lat) : null
  };
}

export function loadWatch() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = JSON.parse(raw || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normalizeEntry).filter(Boolean);
  } catch {
    return [];
  }
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(watchEntries));
  } catch {
    // Storage may be unavailable (private browsing); watch list still works in-session.
  }
}

function notify() {
  listeners.forEach(fn => fn());
}

// ── Pure helpers (unit-testable) ──

/**
 * Status for one watch entry against the currently observed price.
 * 'triggered' only when a threshold is set AND current <= threshold.
 * 'unknown' when the current price is unavailable; otherwise 'idle'.
 */
export function computeWatchStatus(entry, currentPriceCents) {
  if (currentPriceCents == null || !Number.isFinite(Number(currentPriceCents))) return 'unknown';
  const threshold = entry?.thresholdCents;
  if (threshold == null || !Number.isFinite(Number(threshold))) return 'idle';
  return Number(currentPriceCents) <= Number(threshold) ? 'triggered' : 'idle';
}

/**
 * Real observed change since the previous visit, in ¢/L (one decimal).
 * Returns null when either price is missing so the UI shows "—" and never
 * invents a number.
 */
export function computeDelta(entry, currentPriceCents) {
  if (currentPriceCents == null || !Number.isFinite(Number(currentPriceCents))) return null;
  const last = entry?.lastSeenPriceCents;
  if (last == null || !Number.isFinite(Number(last))) return null;
  return round1(Number(currentPriceCents) - Number(last));
}

/** Find the live station Feature for a stored watch id. */
export function findFeature(id, stations = currentStations) {
  if (!id || !stations || !Array.isArray(stations.features)) return null;
  return stations.features.find(f => stationId(f) === id) || null;
}

// 5-decimal coordinate key, matching the convention already used by
// js/history.js (`${lng.toFixed(5)},${lat.toFixed(5)}`). Module-private on
// purpose: unifying this helper across modules is out of scope for #65.
function coordKey(lng, lat) {
  const x = Number(lng);
  const y = Number(lat);
  if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
  return `${x.toFixed(5)},${y.toFixed(5)}`;
}

function featureCoordKey(feature) {
  const c = feature?.geometry?.coordinates;
  if (!c || c.length < 2) return null;
  return coordKey(c[0], c[1]);
}

/**
 * Resolve the live Feature for a watch entry: first by the stored text id
 * (backward compatible with ids written before coordinates were kept), then by
 * the entry's own `lng`/`lat` (already persisted by setWatch, so no migration).
 * Returns null when the station is truly absent from the snapshot.
 */
function resolveWatchFeature(entry, stations = currentStations) {
  if (!entry || !stations || !Array.isArray(stations.features)) return null;
  const byId = findFeature(entry.id, stations);
  if (byId) return byId;
  const key = coordKey(entry.lng, entry.lat);
  if (!key) return null;
  return stations.features.find(f => featureCoordKey(f) === key) || null;
}

function currentPrice(feature, fuel) {
  const price = feature?.properties?.[fuel + '_price'];
  return price != null && Number.isFinite(Number(price)) ? Number(price) : null;
}

// ── Store mutations ──

export function getWatchEntries() {
  return watchEntries.slice();
}

// Re-read the watch list from localStorage. Used by the JSON restore flow
// (issue #57) so an import is reflected in the UI without a page reload.
export function reloadWatch() {
  watchEntries = loadWatch();
  notify();
  return watchEntries.slice();
}

export function getWatchEntry(feature) {
  const id = stationId(feature);
  if (!id) return undefined;
  return watchEntries.find(e => e.id === id);
}

export function isWatching(feature) {
  return !!getWatchEntry(feature);
}

export function setWatch(feature, opts = {}) {
  const id = stationId(feature);
  if (!id) return null;
  const props = feature?.properties || {};
  const fuel = FUEL_KEYS.includes(opts.fuel) ? opts.fuel : 'regular';
  const price = currentPrice(feature, fuel);
  const existing = watchEntries.find(e => e.id === id);
  const snapshotAt = currentStations?.metadata?.generated_at || new Date().toISOString();

  const entry = {
    id,
    name: props.name || props.brand || '',
    brand: props.brand || '',
    address: props.address || '',
    region: props.region || '',
    fuel,
    thresholdCents: normalizeThreshold(opts.thresholdCents),
    // A brand-new watch starts from the price observed right now; editing an
    // existing watch preserves the previous-visit baseline.
    lastSeenPriceCents: existing?.lastSeenPriceCents != null ? existing.lastSeenPriceCents : price,
    lastSeenAt: existing?.lastSeenAt || snapshotAt,
    lng: feature?.geometry?.coordinates?.[0] ?? null,
    lat: feature?.geometry?.coordinates?.[1] ?? null
  };

  watchEntries = watchEntries.filter(e => e.id !== id);
  watchEntries.push(entry);
  persist();
  notify();
  return entry;
}

export function removeWatch(feature) {
  const id = stationId(feature);
  if (!id) return false;
  return removeWatchById(id);
}

export function removeWatchById(id) {
  const before = watchEntries.length;
  watchEntries = watchEntries.filter(e => e.id !== id);
  if (watchEntries.length === before) return false;
  persist();
  notify();
  return true;
}

export function setWatchThreshold(feature, thresholdCents) {
  const id = stationId(feature);
  const entry = watchEntries.find(e => e.id === id);
  if (!entry) return null;
  entry.thresholdCents = normalizeThreshold(thresholdCents);
  persist();
  notify();
  return entry;
}

export function setWatchFuel(feature, fuel) {
  if (!FUEL_KEYS.includes(fuel)) return null;
  const id = stationId(feature);
  const entry = watchEntries.find(e => e.id === id);
  if (!entry) return null;
  entry.fuel = fuel;
  // A fuel switch must never compare prices across fuels: re-baseline the
  // "since last viewed" delta from the new fuel's real observed price.
  entry.lastSeenPriceCents = currentPrice(feature, fuel);
  entry.lastSeenAt = currentStations?.metadata?.generated_at || new Date().toISOString();
  persist();
  notify();
  return entry;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ── Snapshot evaluation ──

function refreshEntryFromFeature(entry, feature) {
  const props = feature?.properties || {};
  entry.name = props.name || props.brand || entry.name;
  entry.brand = props.brand || entry.brand;
  entry.address = props.address || entry.address;
  entry.region = props.region || entry.region;
  entry.lng = feature?.geometry?.coordinates?.[0] ?? entry.lng;
  entry.lat = feature?.geometry?.coordinates?.[1] ?? entry.lat;
  // The station may have been renamed/re-addressed since the watch was saved
  // (the fallback matched it by coordinates). Adopt the new text key so the
  // detail panel's stationId-based lookups keep hitting the same entry instead
  // of reporting "not watching" and spawning a duplicate on re-add. Existing
  // localized names here are already the official ones, so nothing is invented.
  const nextId = stationId(feature);
  if (nextId && nextId !== entry.id) entry.id = nextId;
}

/**
 * Evaluate every watch entry against the real current snapshot once after
 * stations load. A station that is not found under either its text id or its
 * coordinates is KEPT and reported as 'missing' (never silently deleted), so a
 * temporary upstream gap cannot destroy a user's watch. For available prices
 * this records the new `lastSeen*` baseline AFTER computing the delta/status
 * from the previous visit so the sidebar shows the true "since last viewed"
 * change.
 */
export function evaluateWatch(stations = currentStations) {
  if (!stations || !Array.isArray(stations.features)) return [];

  const snapshotAt = stations.metadata?.generated_at || new Date().toISOString();
  const results = [];
  const kept = [];

  for (const entry of watchEntries) {
    const feature = resolveWatchFeature(entry, stations);
    if (!feature) {
      // Station truly gone from the official snapshot: keep the entry (and its
      // threshold/baseline) and surface it instead of auto-cleaning it.
      results.push({
        entry,
        feature: null,
        price: null,
        status: 'missing',
        delta: null,
        lastSeenAt: entry.lastSeenAt,
        lastSeenPriceCents: entry.lastSeenPriceCents
      });
      kept.push(entry);
      continue;
    }

    const price = currentPrice(feature, entry.fuel);
    const status = computeWatchStatus(entry, price);
    const delta = computeDelta(entry, price);
    // Snapshot the previous-visit values before updating the baseline.
    results.push({
      entry,
      feature,
      price,
      status,
      delta,
      lastSeenAt: entry.lastSeenAt,
      lastSeenPriceCents: entry.lastSeenPriceCents
    });

    if (price != null) {
      entry.lastSeenPriceCents = price;
      entry.lastSeenAt = snapshotAt;
    }
    refreshEntryFromFeature(entry, feature);
    // A rename can move this entry onto the id of an already-kept entry (e.g.
    // two snapshots of the same physical station); keep exactly one watch per
    // station so the sidebar never shows duplicate rows.
    const dup = kept.findIndex(e => e.id === entry.id);
    if (dup >= 0) kept.splice(dup, 1);
    kept.push(entry);
  }

  watchEntries = kept;
  persist();
  renderResults(results);
  return results;
}

// ── Sidebar rendering ──

function formatWatchDate(value) {
  if (!value) return '';
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString(getLanguage(), { year: 'numeric', month: 'short', day: 'numeric' });
}

function deltaText(delta) {
  if (delta == null) return '—';
  const sign = delta > 0 ? '+' : delta < 0 ? '−' : '';
  return `${sign}${Math.abs(delta).toFixed(1)}¢`;
}

function watchItemHTML(result) {
  const { entry, price, status, delta, lastSeenAt } = result;
  const dict = translations[getLanguage()] || {};
  const fuelText = dict[entry.fuel] || entry.fuel;
  const missing = status === 'missing';
  const triggered = status === 'triggered';
  const name = entry.name || entry.brand || entry.id;
  const lastSeenText = formatWatchDate(lastSeenAt);
  const sinceText = lastSeenText ? tf('watchSinceLastSeen', { date: lastSeenText }) : '';

  const metaText = missing
    ? t('watchStationMissing')
    : `${escapeHtml(fuelText)} · ${price != null ? price.toFixed(1) + '¢' : escapeHtml(t('watchPriceUnavailable'))}` +
      (entry.thresholdCents != null ? ' · ' + escapeHtml(`≤ ${entry.thresholdCents.toFixed(1)}¢`) : '');

  // A missing station has no live feature to locate, so the row is a plain
  // (non-interactive) element; the remove button stays the only control.
  const openInner = `
        <span class="watch-item-name">${escapeHtml(name)}</span>
        <span class="watch-item-meta">${metaText}</span>
        ${sinceText ? `<span class="watch-item-delta">${escapeHtml(sinceText)}${missing ? '' : ` <b>${deltaText(delta)}</b>`}</span>` : ''}`;

  return `
    <div class="watch-item${missing ? ' watch-item-missing' : ''}">
      ${missing
        ? `<div class="watch-item-main">${openInner}</div>`
        : `<button class="watch-item-main" data-watch-open="${escapeHtml(entry.id)}" aria-label="${escapeHtml(tf('watchLocate', { name }))}">${openInner}</button>`}
      <div class="watch-item-side">
        ${triggered ? `<span class="watch-trigger-badge">${t('watchTriggered')}</span>` : ''}
        <button class="watch-remove" data-watch-remove="${escapeHtml(entry.id)}" aria-label="${escapeHtml(tf('watchRemoveAria', { name }))}" aria-pressed="true" title="${escapeHtml(tf('watchRemoveAria', { name }))}">🔔</button>
      </div>
    </div>`;
}

function buildResults(stations = currentStations) {
  if (!stations || !Array.isArray(stations.features)) return [];
  const results = [];
  for (const entry of watchEntries) {
    const feature = resolveWatchFeature(entry, stations);
    const price = feature ? currentPrice(feature, entry.fuel) : null;
    results.push({
      entry,
      feature: feature || null,
      price,
      status: feature ? computeWatchStatus(entry, price) : 'missing',
      delta: feature ? computeDelta(entry, price) : null,
      lastSeenAt: entry.lastSeenAt,
      lastSeenPriceCents: entry.lastSeenPriceCents
    });
  }
  return results;
}

function renderResults(results) {
  const section = document.getElementById('watch-section');
  const listEl = document.getElementById('watch-list');
  const countEl = document.getElementById('watch-count');
  if (!section || !listEl || !countEl) return;

  const triggered = results.filter(r => r.status === 'triggered').length;
  section.hidden = results.length === 0;
  countEl.textContent = String(triggered);
  listEl.innerHTML = results.map(watchItemHTML).join('');
}

function renderWatchSidebar() {
  renderResults(buildResults(currentStations));
}

async function openWatchStation(feature) {
  if (!feature) return;
  const coords = feature.geometry?.coordinates;
  if (map?.flyTo && coords) {
    map.flyTo({ center: coords, zoom: 15, duration: 800 });
  }
  const { showPopup } = await import('./stats.js');
  showPopup(feature);
}

function wireSidebarEvents() {
  const listEl = document.getElementById('watch-list');
  if (!listEl) return;

  listEl.addEventListener('click', (e) => {
    const openBtn = e.target.closest('[data-watch-open]');
    if (openBtn) {
      const id = openBtn.dataset.watchOpen;
      const entry = watchEntries.find(x => x.id === id);
      // Same resolution as evaluation: a renamed station is still locatable.
      const feature = entry
        ? resolveWatchFeature(entry, currentStations)
        : findFeature(id, currentStations);
      if (feature) openWatchStation(feature);
      return;
    }

    const removeBtn = e.target.closest('[data-watch-remove]');
    if (removeBtn) {
      removeWatchById(removeBtn.dataset.watchRemove);
    }
  });
}

export function initWatch() {
  wireSidebarEvents();

  const toggle = document.getElementById('watch-toggle');
  const section = document.getElementById('watch-section');
  if (toggle && section) {
    toggle.addEventListener('click', () => {
      const open = section.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
    });
  }

  subscribe(renderWatchSidebar);
  onLanguageChange(renderWatchSidebar);

  // Stations load asynchronously; evaluate exactly once when the snapshot is
  // ready, then re-render from the in-memory watch list for later events.
  window.addEventListener('stations:loaded', () => evaluateWatch());

  renderWatchSidebar();
}

// Test/console hooks (mirrors window.__qcGasFillups / window.__qcGasSearch).
if (typeof window !== 'undefined') {
  window.__qcGasWatch = {
    STORAGE_KEY,
    getWatchEntries,
    computeWatchStatus,
    computeDelta,
    normalizeThreshold,
    findFeature,
    resolveWatchFeature,
    setWatch,
    removeWatch,
    removeWatchById,
    setWatchThreshold,
    setWatchFuel,
    isWatching,
    getWatchEntry,
    evaluateWatch
  };
}

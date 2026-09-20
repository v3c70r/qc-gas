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
//   • Stations that disappear from the snapshot are auto-cleaned; missing fuel
//     prices degrade to "price unavailable" instead of a fabricated number.

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

function currentPrice(feature, fuel) {
  const price = feature?.properties?.[fuel + '_price'];
  return price != null && Number.isFinite(Number(price)) ? Number(price) : null;
}

// ── Store mutations ──

export function getWatchEntries() {
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
}

/**
 * Evaluate every watch entry against the real current snapshot once after
 * stations load. Missing stations are auto-cleaned. For available prices this
 * records the new `lastSeen*` baseline AFTER computing the delta/status from
 * the previous visit so the sidebar shows the true "since last viewed" change.
 */
export function evaluateWatch(stations = currentStations) {
  if (!stations || !Array.isArray(stations.features)) return [];

  const snapshotAt = stations.metadata?.generated_at || new Date().toISOString();
  const results = [];
  const kept = [];

  for (const entry of watchEntries) {
    const feature = findFeature(entry.id, stations);
    if (!feature) continue; // station disappeared → auto-clean

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
  const priceText = price != null ? `${price.toFixed(1)}¢` : t('watchPriceUnavailable');
  const thresholdText = entry.thresholdCents != null ? `≤ ${entry.thresholdCents.toFixed(1)}¢` : t('watchTrackOnly');
  const lastSeenText = formatWatchDate(lastSeenAt);
  const sinceText = lastSeenText ? tf('watchSinceLastSeen', { date: lastSeenText }) : '';
  const triggered = status === 'triggered';
  const name = entry.name || entry.brand || entry.id;

  return `
    <div class="watch-item">
      <button class="watch-item-main" data-watch-open="${escapeHtml(entry.id)}" aria-label="${escapeHtml(tf('watchLocate', { name }))}">
        <span class="watch-item-name">${escapeHtml(name)}</span>
        <span class="watch-item-meta">${escapeHtml(fuelText)} · ${priceText}${entry.thresholdCents != null ? ' · ' + escapeHtml(thresholdText) : ''}</span>
        ${sinceText ? `<span class="watch-item-delta">${escapeHtml(sinceText)} <b>${deltaText(delta)}</b></span>` : ''}
      </button>
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
    const feature = findFeature(entry.id, stations);
    if (!feature) continue;
    const price = currentPrice(feature, entry.fuel);
    results.push({
      entry,
      feature,
      price,
      status: computeWatchStatus(entry, price),
      delta: computeDelta(entry, price),
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
      const feature = findFeature(openBtn.dataset.watchOpen, currentStations);
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
    setWatch,
    removeWatch,
    removeWatchById,
    setWatchThreshold,
    isWatching,
    getWatchEntry,
    evaluateWatch
  };
}

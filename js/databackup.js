// ── Personal data backup (「Mes données」) ──
// Export / import every personal dataset as a versioned JSON file so the user
// can carry their data between browsers/devices and restore it after a cache
// wipe or Safari's 7-day script-storage eviction (ITP 2.3). Local-only: the
// file is generated in the browser and saved by the user — no server, no
// account, no new network request.
//
// Covered keys: favorites, fill-ups, price watch, trip preferences and list
// preferences. Device-specific view state (`qc-gas-view`) is deliberately
// excluded: restoring a radius/view on another device would be surprising.
//
// Design constraints:
//   • A malformed or unknown file must NEVER touch existing data.
//   • Merge dedupes by id (favorites: stationId, fill-ups: id, watch: station
//     id) and never silently drops a watch baseline.
//   • Replace is destructive and therefore confirmed a second time in the UI.

import { t, tf, onLanguageChange } from './i18n.js';

export const BACKUP_VERSION = 1;
export const BACKUP_APP = 'essence-quebec';

const FAVORITES_KEY = 'qc-gas-favorites';
const FILLUPS_KEY = 'qc-gas-fillups';
const WATCH_KEY = 'qc-gas-watch';
const TRIP_KEY = 'qc-gas-trip';
const LIST_KEY = 'qc-gas-list';

export const STORAGE_KEYS = {
  favorites: FAVORITES_KEY,
  fillups: FILLUPS_KEY,
  watch: WATCH_KEY,
  tripPrefs: TRIP_KEY,
  listPrefs: LIST_KEY
};

const DEFAULT_TRIP_PREFS = { consumption: 8, roundTrip: false };
const DEFAULT_LIST_PREFS = { sort: 'price' };

// ── Validation helpers (pure) ──

function isValidFavoriteId(value) {
  return typeof value === 'string' && value.trim() !== '';
}

function isFillupRecord(value) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  if (typeof value.id !== 'string' || value.id === '') return false;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(value.date || ''))) return false;
  const price = Number(value.priceCents);
  return Number.isFinite(price) && price > 0;
}

function isWatchRecord(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value) &&
    typeof value.id === 'string' && value.id !== '';
}

function sanitizeTripPrefs(raw) {
  const consumption = Number(raw?.consumption);
  return {
    consumption: Number.isFinite(consumption) && consumption > 0 ? consumption : DEFAULT_TRIP_PREFS.consumption,
    roundTrip: raw?.roundTrip === true
  };
}

function sanitizeListPrefs(raw) {
  return { sort: raw?.sort === 'distance' ? 'distance' : 'price' };
}

function readJSON(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    if (raw == null) return fallback;
    const parsed = JSON.parse(raw);
    return parsed == null ? fallback : parsed;
  } catch {
    return fallback;
  }
}

// ── Current local data + backup envelope ──

/** Snapshot of every personal dataset, sanitized and ready to export. */
export function readLocalData() {
  const favoritesRaw = readJSON(FAVORITES_KEY, []);
  const fillupsRaw = readJSON(FILLUPS_KEY, []);
  const watchRaw = readJSON(WATCH_KEY, []);
  return {
    favorites: Array.isArray(favoritesRaw) ? favoritesRaw.filter(isValidFavoriteId) : [],
    fillups: Array.isArray(fillupsRaw) ? fillupsRaw.filter(isFillupRecord) : [],
    watch: Array.isArray(watchRaw) ? watchRaw.filter(isWatchRecord) : [],
    tripPrefs: sanitizeTripPrefs(readJSON(TRIP_KEY, null)),
    listPrefs: sanitizeListPrefs(readJSON(LIST_KEY, null))
  };
}

/** Versioned, self-describing backup envelope. */
export function buildBackup(now = new Date()) {
  const date = now instanceof Date ? now : new Date(now);
  const stamp = Number.isNaN(date.getTime()) ? new Date() : date;
  return {
    v: BACKUP_VERSION,
    app: BACKUP_APP,
    exported_at: stamp.toISOString(),
    data: readLocalData()
  };
}

function failValidate(key) {
  return {
    ok: false,
    errors: [key],
    counts: { favorites: 0, fillups: 0, watch: 0, invalid: 0 },
    data: null
  };
}

function validateDataset(raw, isValid) {
  if (raw == null) return { valid: [], invalid: 0 };
  if (!Array.isArray(raw)) return { valid: [], invalid: 1 };
  const valid = [];
  let invalid = 0;
  for (const item of raw) {
    if (isValid(item)) valid.push(item);
    else invalid += 1;
  }
  return { valid, invalid };
}

/**
 * Validate a parsed object OR a raw string. Returns { ok, errors[], counts,
 * data }: `errors` holds i18n keys, `counts.invalid` the number of rejected
 * records, and `data` the sanitized incoming datasets (only when ok).
 * A structural failure (bad JSON, unknown version/app, missing data) blocks the
 * whole import — callers must not write anything in that case.
 */
export function validateBackup(input) {
  let parsed = input;
  if (typeof input === 'string') {
    const text = input.trim();
    if (!text) return failValidate('dataErrJson');
    try {
      parsed = JSON.parse(text);
    } catch {
      return failValidate('dataErrJson');
    }
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return failValidate('dataErrShape');
  if (parsed.app !== BACKUP_APP) return failValidate('dataErrApp');
  if (Number(parsed.v) !== BACKUP_VERSION) return failValidate('dataErrVersion');

  const data = parsed.data;
  if (!data || typeof data !== 'object' || Array.isArray(data)) return failValidate('dataErrShape');
  const known = ['favorites', 'fillups', 'watch', 'tripPrefs', 'listPrefs'];
  if (!known.some(key => key in data)) return failValidate('dataErrShape');

  const favorites = validateDataset(data.favorites, isValidFavoriteId);
  const fillups = validateDataset(data.fillups, isFillupRecord);
  const watch = validateDataset(data.watch, isWatchRecord);
  let invalid = favorites.invalid + fillups.invalid + watch.invalid;

  let tripPrefs = { ...DEFAULT_TRIP_PREFS };
  if ('tripPrefs' in data) {
    if (data.tripPrefs && typeof data.tripPrefs === 'object' && !Array.isArray(data.tripPrefs)) {
      tripPrefs = sanitizeTripPrefs(data.tripPrefs);
      const raw = data.tripPrefs.consumption;
      if (raw != null && !(Number.isFinite(Number(raw)) && Number(raw) > 0)) invalid += 1;
    } else {
      invalid += 1;
    }
  }

  let listPrefs = { ...DEFAULT_LIST_PREFS };
  if ('listPrefs' in data) {
    if (data.listPrefs && typeof data.listPrefs === 'object' && !Array.isArray(data.listPrefs)) {
      listPrefs = sanitizeListPrefs(data.listPrefs);
    } else {
      invalid += 1;
    }
  }

  return {
    ok: true,
    errors: [],
    counts: {
      favorites: favorites.valid.length,
      fillups: fillups.valid.length,
      watch: watch.valid.length,
      invalid
    },
    data: {
      favorites: favorites.valid,
      fillups: fillups.valid,
      watch: watch.valid,
      tripPrefs,
      listPrefs
    }
  };
}

// ── Merge (pure) ──

/**
 * Merge two watch entries that share a station id.
 * The baseline (`lastSeenPriceCents`) comes from the entry observed most
 * recently (`lastSeenAt`); when no timestamps are comparable we keep the larger
 * observed price. An explicit local threshold always wins, so a restore never
 * silently removes a threshold the user set on this device.
 */
export function mergeWatchEntry(existing, incoming) {
  const merged = { ...existing };
  if (merged.thresholdCents == null && incoming?.thresholdCents != null) {
    merged.thresholdCents = incoming.thresholdCents;
  }

  const existingAt = Date.parse(existing?.lastSeenAt || '');
  const incomingAt = Date.parse(incoming?.lastSeenAt || '');
  let newer = null;
  if (Number.isFinite(existingAt) && Number.isFinite(incomingAt)) {
    newer = incomingAt > existingAt ? incoming : (existingAt > incomingAt ? existing : null);
  } else if (Number.isFinite(incomingAt)) {
    newer = incoming;
  } else if (Number.isFinite(existingAt)) {
    newer = existing;
  }
  if (!newer) {
    const a = existing?.lastSeenPriceCents;
    const b = incoming?.lastSeenPriceCents;
    if (a == null) newer = incoming;
    else if (b == null) newer = existing;
    else newer = Number(b) > Number(a) ? incoming : existing;
  }

  if (newer?.lastSeenPriceCents != null) merged.lastSeenPriceCents = newer.lastSeenPriceCents;
  if (newer?.lastSeenAt) merged.lastSeenAt = newer.lastSeenAt;

  for (const key of ['name', 'brand', 'address', 'region', 'fuel', 'lng', 'lat']) {
    if ((merged[key] == null || merged[key] === '') && incoming?.[key] != null) merged[key] = incoming[key];
  }
  return merged;
}

/**
 * Apply already-validated incoming datasets to the current data.
 * mode 'merge' dedupes by id (duplicates are counted as skipped);
 * mode 'replace' swaps each dataset wholesale (skipped is always 0).
 * Returns { data, imported, skipped } — pure, never touches localStorage.
 */
export function applyImport(incoming, mode, current) {
  const cur = current && typeof current === 'object' ? current : readLocalData();
  const inc = incoming && typeof incoming === 'object' ? incoming : {};
  const favorites = Array.isArray(inc.favorites) ? inc.favorites.filter(isValidFavoriteId) : [];
  const fillups = Array.isArray(inc.fillups) ? inc.fillups.filter(isFillupRecord) : [];
  const watch = Array.isArray(inc.watch) ? inc.watch.filter(isWatchRecord) : [];
  const tripPrefs = inc.tripPrefs ? sanitizeTripPrefs(inc.tripPrefs) : (cur.tripPrefs || { ...DEFAULT_TRIP_PREFS });
  const listPrefs = inc.listPrefs ? sanitizeListPrefs(inc.listPrefs) : (cur.listPrefs || { ...DEFAULT_LIST_PREFS });

  if (mode === 'replace') {
    return {
      data: { favorites, fillups, watch, tripPrefs, listPrefs },
      imported: favorites.length + fillups.length + watch.length,
      skipped: 0
    };
  }

  let imported = 0;
  let skipped = 0;

  const mergedFavorites = Array.isArray(cur.favorites) ? cur.favorites.slice() : [];
  const favoriteSeen = new Set(mergedFavorites);
  for (const id of favorites) {
    if (favoriteSeen.has(id)) { skipped += 1; continue; }
    favoriteSeen.add(id);
    mergedFavorites.push(id);
    imported += 1;
  }

  const fillupMap = new Map((Array.isArray(cur.fillups) ? cur.fillups : []).map(f => [f.id, f]));
  for (const entry of fillups) {
    if (fillupMap.has(entry.id)) { skipped += 1; continue; }
    fillupMap.set(entry.id, entry);
    imported += 1;
  }

  const watchMap = new Map((Array.isArray(cur.watch) ? cur.watch : []).map(e => [e.id, e]));
  for (const entry of watch) {
    const existing = watchMap.get(entry.id);
    if (!existing) {
      watchMap.set(entry.id, entry);
      imported += 1;
      continue;
    }
    skipped += 1;
    watchMap.set(entry.id, mergeWatchEntry(existing, entry));
  }

  return {
    data: {
      favorites: mergedFavorites,
      fillups: [...fillupMap.values()],
      watch: [...watchMap.values()],
      tripPrefs,
      listPrefs
    },
    imported,
    skipped
  };
}

// ── Storage write + rehydration ──

/** Write every dataset back to its localStorage key. Returns false on failure. */
export function writeLocalData(data) {
  try {
    localStorage.setItem(FAVORITES_KEY, JSON.stringify(data?.favorites || []));
    localStorage.setItem(FILLUPS_KEY, JSON.stringify(data?.fillups || []));
    localStorage.setItem(WATCH_KEY, JSON.stringify(data?.watch || []));
    localStorage.setItem(TRIP_KEY, JSON.stringify(data?.tripPrefs || DEFAULT_TRIP_PREFS));
    localStorage.setItem(LIST_KEY, JSON.stringify(data?.listPrefs || DEFAULT_LIST_PREFS));
    return true;
  } catch {
    return false;
  }
}

// The stores keep an in-memory copy loaded at module init, so a successful
// import must ask them to re-read localStorage (otherwise the restored data
// would only appear after a manual reload). Dynamic imports keep this module
// free of heavy static dependencies.
function rehydrateStores() {
  const tasks = [
    import('./favorites.js').then(m => m.reloadFavorites()),
    import('./fillups.js').then(m => m.reloadFillups()),
    import('./watch.js').then(m => m.reloadWatch()),
    import('./stats.js').then(m => m.reloadListPreferences())
  ];
  import('./station-card.js').then(m => m.reloadTripPrefs?.()).catch(() => {});
  Promise.all(tasks).catch(() => {});
}

// ── Import / export ──

/**
 * Import a raw JSON string. Validates FIRST: on any structural failure nothing
 * is written and a localized error key is returned.
 */
export function importFromText(text, mode = 'merge') {
  const validation = validateBackup(text);
  if (!validation.ok) {
    return {
      ok: false,
      errors: validation.errors,
      imported: 0,
      skipped: 0,
      invalid: validation.counts.invalid
    };
  }

  const applied = applyImport(validation.data, mode === 'replace' ? 'replace' : 'merge', readLocalData());
  if (!writeLocalData(applied.data)) {
    return { ok: false, errors: ['dataErrWrite'], imported: 0, skipped: 0, invalid: validation.counts.invalid };
  }
  rehydrateStores();
  return {
    ok: true,
    errors: [],
    imported: applied.imported,
    skipped: applied.skipped,
    invalid: validation.counts.invalid
  };
}

/** Import a File chosen from the user's device. */
export async function importFromFile(file, mode = 'merge') {
  if (!file || typeof file.text !== 'function') {
    return { ok: false, errors: ['dataErrShape'], imported: 0, skipped: 0, invalid: 0 };
  }
  let text;
  try {
    text = await file.text();
  } catch {
    return { ok: false, errors: ['dataErrJson'], imported: 0, skipped: 0, invalid: 0 };
  }
  return importFromText(text, mode);
}

function localDateKey(date) {
  const d = date instanceof Date ? date : new Date(date);
  const safe = Number.isNaN(d.getTime()) ? new Date() : d;
  return `${safe.getFullYear()}-${String(safe.getMonth() + 1).padStart(2, '0')}-${String(safe.getDate()).padStart(2, '0')}`;
}

/** Trigger a JSON download named with today's date. Offline-safe. */
export function exportToFile(now = new Date()) {
  const backup = buildBackup(now);
  const json = JSON.stringify(backup, null, 2);
  const filename = `essence-quebec-sauvegarde-${localDateKey(now)}.json`;

  const blob = new Blob([json], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return { filename, json };
}

// ── UI binding ──

let pendingFile = null;
// Last rendered status, kept so a language switch can re-translate it.
// { result } for an import outcome, { key, kind } for a static message.
let lastStatus = null;

function getMode() {
  return document.querySelector('input[name="databackup-mode"]:checked')?.value === 'replace'
    ? 'replace'
    : 'merge';
}

function setStatus(text, kind) {
  const el = document.getElementById('databackup-status');
  if (!el) return;
  el.textContent = text;
  el.dataset.kind = kind || '';
}

function renderResult(result) {
  lastStatus = { result };
  if (!result.ok) {
    setStatus(result.errors.map(key => t(key)).join(' · '), 'error');
    return;
  }
  setStatus(tf('dataResult', {
    imported: result.imported,
    skipped: result.skipped,
    invalid: result.invalid
  }), 'ok');
}

function renderStatusMessage() {
  if (!lastStatus) return;
  if (lastStatus.result) {
    renderResult(lastStatus.result);
    return;
  }
  setStatus(t(lastStatus.key), lastStatus.kind || '');
}

function showConfirm(visible) {
  const block = document.getElementById('databackup-confirm');
  if (block) block.hidden = !visible;
}

function resetFileInput() {
  const input = document.getElementById('databackup-file');
  if (input) input.value = '';
}

async function applyFile(file, mode) {
  const result = await importFromFile(file, mode);
  pendingFile = null;
  showConfirm(false);
  resetFileInput();
  renderResult(result);
  return result;
}

export function initDataBackup() {
  const toggle = document.getElementById('databackup-toggle');
  const section = document.getElementById('databackup-section');
  if (toggle && section) {
    toggle.addEventListener('click', () => {
      const open = section.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
    });
  }

  const exportBtn = document.getElementById('databackup-export');
  exportBtn?.addEventListener('click', () => {
    exportToFile();
    lastStatus = { key: 'dataExported', kind: 'ok' };
    renderStatusMessage();
  });

  const importBtn = document.getElementById('databackup-import');
  const fileInput = document.getElementById('databackup-file');
  importBtn?.addEventListener('click', () => fileInput?.click());

  fileInput?.addEventListener('change', () => {
    const file = fileInput.files && fileInput.files[0];
    if (!file) return;
    pendingFile = file;
    if (getMode() === 'replace') {
      // Destructive: require an explicit second confirmation.
      showConfirm(true);
    } else {
      applyFile(file, 'merge');
    }
  });

  document.getElementById('databackup-confirm-yes')?.addEventListener('click', () => {
    if (pendingFile) applyFile(pendingFile, 'replace');
  });

  document.getElementById('databackup-confirm-no')?.addEventListener('click', () => {
    pendingFile = null;
    showConfirm(false);
    resetFileInput();
  });

  document.querySelectorAll('input[name="databackup-mode"]').forEach(radio => {
    radio.addEventListener('change', () => {
      pendingFile = null;
      showConfirm(false);
      resetFileInput();
    });
  });

  const modes = document.querySelector('.databackup-modes');
  const labelModes = () => {
    if (modes) modes.setAttribute('aria-label', t('dataImport'));
  };
  labelModes();

  onLanguageChange(() => {
    labelModes();
    renderStatusMessage();
  });
}

// Test/console hooks (mirrors window.__qcGasFillups / window.__qcGasWatch).
if (typeof window !== 'undefined') {
  window.__qcGasBackup = {
    BACKUP_VERSION,
    BACKUP_APP,
    STORAGE_KEYS,
    buildBackup,
    validateBackup,
    applyImport,
    mergeWatchEntry,
    readLocalData,
    writeLocalData,
    importFromText,
    importFromFile,
    exportToFile
  };
}

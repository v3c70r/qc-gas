// ── Fill-ups (加油日志) ──
// Local-only fuel log: localStorage persistence + pure statistics. No backend,
// no account, no new network requests. The summary compares each logged fill-up
// against the REAL region-level average for that day (data/history.json), and
// degrades gracefully to simple totals when history is unavailable.
// Issue #62: the summary is scoped to a user-chosen period (month/year/all,
// persisted), and the current period can be exported as a UTF-8 CSV.

import { t, tf, onLanguageChange, translations, getLanguage } from './i18n.js';
import { loadHistoryData } from './history.js';

export const STORAGE_KEY = 'qc-gas-fillups';
// Period preference for the summary/export (issue #62). Travels with the JSON
// backup (js/databackup.js) so a restore keeps the user's chosen period.
export const PREFS_KEY = 'qc-gas-fillups-prefs';
export const RANGES = ['month', 'year', 'all'];
const RANGE_LABEL_KEYS = { month: 'fillupRangeMonth', year: 'fillupRangeYear', all: 'fillupRangeAll' };
const FUEL_KEYS = ['regular', 'super', 'diesel'];

let fillups = loadFillups();
let range = loadRangePrefs().range;
const listeners = new Set();

function loadRangePrefs() {
  try {
    const parsed = JSON.parse(localStorage.getItem(PREFS_KEY) || 'null');
    if (parsed && RANGES.includes(parsed.range)) return { range: parsed.range };
  } catch {
    // Storage may be unavailable (private browsing); keep the default.
  }
  return { range: 'month' };
}

function persistRangePrefs() {
  try {
    localStorage.setItem(PREFS_KEY, JSON.stringify({ range }));
  } catch {
    // Storage may be unavailable (private browsing); the choice still applies in-session.
  }
}

/** Currently selected period ('month' | 'year' | 'all'). */
export function getRange() {
  return range;
}

/** Switch the period, persist it and re-render. Returns the effective range. */
export function setRange(next) {
  if (!RANGES.includes(next)) return range;
  if (range !== next) {
    range = next;
    persistRangePrefs();
  }
  renderRangeControls();
  renderFillupsPanel();
  return range;
}

// Re-read the period preference from localStorage (JSON restore, issue #57).
export function reloadFillupPrefs() {
  range = loadRangePrefs().range;
  renderRangeControls();
  renderFillupsPanel();
  return range;
}

function genId() {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
  return 'f-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function normalizeFillup(data) {
  if (!data || typeof data !== 'object') return null;
  const priceCents = Number(data.priceCents);
  if (!Number.isFinite(priceCents) || priceCents <= 0) return null;

  const date = String(data.date || '');
  if (!/^\d{4}-\d{2}-\d{2}$/.test(date)) return null;

  const rawLiters = data.liters;
  const liters = (rawLiters === null || rawLiters === undefined || rawLiters === '') ? null : Number(rawLiters);
  const rawTotal = data.totalPrice;
  const totalPrice = (rawTotal === null || rawTotal === undefined || rawTotal === '') ? null : Number(rawTotal);

  if (!(liters != null && Number.isFinite(liters) && liters > 0) &&
      !(totalPrice != null && Number.isFinite(totalPrice) && totalPrice > 0)) return null;

  return {
    id: String(data.id || genId()),
    stationId: String(data.stationId || ''),
    stationName: String(data.stationName || ''),
    brand: String(data.brand || ''),
    region: String(data.region || ''),
    fuel: FUEL_KEYS.includes(data.fuel) ? data.fuel : 'regular',
    date,
    priceCents,
    liters: (liters != null && Number.isFinite(liters)) ? liters : null,
    totalPrice: (totalPrice != null && Number.isFinite(totalPrice)) ? totalPrice : null,
    createdAt: String(data.createdAt || new Date().toISOString())
  };
}

export function loadFillups() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const parsed = JSON.parse(raw || '[]');
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normalizeFillup).filter(Boolean);
  } catch {
    return [];
  }
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(fillups));
  } catch {
    // Storage may be unavailable (private browsing); the log still works in-session.
  }
}

function notify() {
  listeners.forEach(fn => fn());
}

// Re-read the log from localStorage. Used by the JSON restore flow (issue
// #57) so an import is reflected in the UI without a page reload.
export function reloadFillups() {
  fillups = loadFillups();
  notify();
  return getFillups();
}

export function getFillups() {
  return fillups.slice().sort((a, b) => {
    const byDate = String(b.date || '').localeCompare(String(a.date || ''));
    if (byDate !== 0) return byDate;
    return String(b.createdAt || '').localeCompare(String(a.createdAt || ''));
  });
}

export function addFillup(data) {
  const entry = normalizeFillup(data);
  if (!entry) return null;
  fillups = [entry, ...fillups];
  persist();
  notify();
  return entry;
}

export function deleteFillup(id) {
  const before = fillups.length;
  fillups = fillups.filter(f => f.id !== id);
  if (fillups.length === before) return false;
  persist();
  notify();
  return true;
}

export function subscribe(fn) {
  listeners.add(fn);
  return () => listeners.delete(fn);
}

// ── Pure helpers (unit-testable) ──

/** Calendar-day key for a date-only or ISO timestamp string. */
export function dayKey(value) {
  const s = String(value ?? '');
  const m = s.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/** Local 'YYYY-MM-DD' for a Date (matches <input type="date"> values). */
export function localDateKey(date) {
  const d = date instanceof Date ? date : new Date(date);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

/**
 * Mean region-level average price (¢/L) for a region + fuel on a given
 * calendar day, using REAL history data. Falls back to the province-wide
 * `overall` series when the region is unknown. Returns null when missing.
 */
export function regionAverageForDate(historyData, region, fuel, dateStr) {
  if (!historyData || !dateStr) return null;
  const src = (historyData.regions && historyData.regions[region]) || historyData.overall;
  const points = (src && (src.points || src.days)) || [];
  if (!points.length) return null;

  const target = dayKey(dateStr);
  if (!target) return null;

  const vals = [];
  for (const p of points) {
    if (dayKey(p && p.date) !== target) continue;
    const v = p && p[fuel] ? p[fuel].avg : null;
    if (v != null && Number.isFinite(Number(v))) vals.push(Number(v));
  }
  if (!vals.length) return null;
  return vals.reduce((a, b) => a + b, 0) / vals.length;
}

/**
 * Current-period summary for the fill-up log.
 * Returns { range, count, liters, spendDollars, avgPriceCents, savingsDollars,
 *           savingsKnown }.
 * `avgPriceCents` is litres-weighted; `savingsDollars` sums
 * (region average − paid price) × litres for each matched fill-up and is null
 * when no fill-up could be matched to real history.
 */
export function computeStats(fillupsList, historyData, options = {}) {
  const selectedRange = RANGES.includes(options?.range) ? options.range : 'month';
  const now = options?.now instanceof Date ? options.now : new Date(options?.now ?? Date.now());
  const inPeriod = (fillupsList || []).filter(f => inRange(f.date, selectedRange, now));

  let liters = 0;
  let spend = 0;
  let weightedPriceSum = 0;
  let weightedLiters = 0;
  let simplePriceSum = 0;
  let simplePriceCount = 0;
  let savings = 0;
  let savingsKnown = false;

  for (const f of inPeriod) {
    const price = Number(f.priceCents);
    const lit = (f.liters != null && Number.isFinite(Number(f.liters))) ? Number(f.liters) : null;

    if (lit != null && lit > 0) {
      liters += lit;
      weightedPriceSum += price * lit;
      weightedLiters += lit;
    }
    if (Number.isFinite(price) && price > 0) {
      simplePriceSum += price;
      simplePriceCount += 1;
    }

    const total = (f.totalPrice != null && Number(f.totalPrice) > 0)
      ? Number(f.totalPrice)
      : (lit != null && lit > 0 ? price * lit / 100 : 0);
    spend += total;

    const regionAvg = regionAverageForDate(historyData, f.region, f.fuel, f.date);
    if (regionAvg != null && lit != null && lit > 0) {
      savings += (regionAvg - price) * lit / 100;
      savingsKnown = true;
    }
  }

  const avgPriceCents = weightedLiters > 0
    ? weightedPriceSum / weightedLiters
    : (simplePriceCount > 0 ? simplePriceSum / simplePriceCount : null);

  return {
    range: selectedRange,
    count: inPeriod.length,
    liters,
    spendDollars: spend,
    avgPriceCents,
    savingsDollars: savings,
    savingsKnown
  };
}

/**
 * Backwards-compatible month wrapper (issue #29 call sites and tests).
 */
export function computeMonthStats(fillupsList, historyData, now = new Date()) {
  return computeStats(fillupsList, historyData, { range: 'month', now });
}

/** True when `dateStr` (YYYY-MM-DD) falls inside the selected period. */
function inRange(dateStr, selectedRange, now) {
  if (selectedRange === 'all') return true;
  const d = String(dateStr || '');
  if (!/^\d{4}-\d{2}-\d{2}/.test(d)) return false;
  const ref = now instanceof Date && !Number.isNaN(now.getTime()) ? now : new Date();
  if (selectedRange === 'year') return d.slice(0, 4) === String(ref.getFullYear());
  return d.slice(0, 7) === `${ref.getFullYear()}-${String(ref.getMonth() + 1).padStart(2, '0')}`;
}

// ── CSV export (RFC 4180, UTF-8 BOM for Excel) ──

function csvCell(value) {
  const s = value == null ? '' : String(value);
  return /[",;\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

function csvNumber(value, digits) {
  const n = Number(value);
  return Number.isFinite(n) ? n.toFixed(digits) : '';
}

/**
 * CSV for every fill-up of the selected period, oldest first. Columns:
 * date, station, brand, region, fuel, price, litres, total, region average
 * that day (when known), savings. Quoted per RFC 4180 and prefixed with a
 * UTF-8 BOM so Excel shows accented station names correctly.
 */
export function buildCsv(fillupsList, historyData, selectedRange = 'month', now = new Date()) {
  const rows = (fillupsList || [])
    .filter(f => inRange(f.date, RANGES.includes(selectedRange) ? selectedRange : 'month', now))
    .slice()
    .sort((a, b) => String(a.date || '').localeCompare(String(b.date || '')));

  const header = ['fillupCsvDate', 'fillupCsvStation', 'fillupCsvBrand', 'fillupCsvRegion', 'fillupCsvFuel',
    'fillupCsvPrice', 'fillupCsvLiters', 'fillupCsvTotal', 'fillupCsvRegionAvg', 'fillupCsvSavings']
    .map(key => csvCell(t(key)));

  const lines = [header.join(',')];
  for (const f of rows) {
    const price = Number(f.priceCents);
    const lit = (f.liters != null && Number.isFinite(Number(f.liters))) ? Number(f.liters) : null;
    const total = (f.totalPrice != null && Number(f.totalPrice) > 0)
      ? Number(f.totalPrice)
      : (lit != null && lit > 0 ? price * lit / 100 : null);
    const regionAvg = regionAverageForDate(historyData, f.region, f.fuel, f.date);
    const savings = (regionAvg != null && lit != null && lit > 0) ? (regionAvg - price) * lit / 100 : null;

    lines.push([
      f.date,
      f.stationName,
      f.brand,
      f.region,
      fuelLabel(f.fuel),
      csvNumber(price, 1),
      lit != null ? csvNumber(lit, 2) : '',
      csvNumber(total, 2),
      regionAvg != null ? csvNumber(regionAvg, 1) : '',
      savings != null ? csvNumber(savings, 2) : ''
    ].map(csvCell).join(','));
  }

  return '\uFEFF' + lines.join('\r\n') + '\r\n';
}

/** Trigger a CSV download for the current period. Offline-safe, no network. */
export async function exportFillupsCsv(now = new Date()) {
  const historyData = await loadHistoryData().catch(() => null);
  const csv = buildCsv(getFillups(), historyData, range, now);
  const filename = `mes-pleins-${localDateKey(now)}.csv`;

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  anchor.rel = 'noopener';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
  return { filename, csv };
}

// ── Sidebar summary rendering ──

function formatDollars(amount) {
  if (amount == null || !Number.isFinite(amount)) return '—';
  const sign = amount < 0 ? '-' : '';
  return `${sign}$${Math.abs(amount).toFixed(2)}`;
}

function fuelLabel(fuel) {
  const dict = translations[getLanguage()];
  return dict?.[fuel] || fuel;
}

function fillupItemHTML(f) {
  const spend = (f.totalPrice != null && Number(f.totalPrice) > 0)
    ? Number(f.totalPrice)
    : (f.liters != null ? f.liters * f.priceCents / 100 : 0);
  const litersText = f.liters != null ? `${Number(f.liters).toFixed(2)} L` : '';
  return `
    <div class="fillup-item">
      <div class="fillup-item-main">
        <div class="fillup-item-date">${escapeHtml(f.date)}</div>
        <div class="fillup-item-name">${escapeHtml(f.stationName)}</div>
        <div class="fillup-item-meta">${escapeHtml(fuelLabel(f.fuel))}${litersText ? ' · ' + litersText : ''} · ${Number(f.priceCents).toFixed(1)}¢/L</div>
      </div>
      <div class="fillup-item-side">
        <div class="fillup-item-spend">${formatDollars(spend)}</div>
        <button class="fillup-delete" data-fillup-delete="${escapeHtml(f.id)}" aria-label="${t('fillupDelete')}" title="${t('fillupDelete')}">✕</button>
      </div>
    </div>`;
}

function rangeSpendLabel(selectedRange) {
  return tf('fillupSpendLabel', { range: t(RANGE_LABEL_KEYS[selectedRange] || 'fillupRangeMonth') });
}

function renderRangeControls() {
  const group = document.getElementById('fillups-range');
  if (!group) return;
  group.setAttribute('aria-label', t('fillupRange'));
  group.querySelectorAll('[data-fillup-range]').forEach(btn => {
    const active = btn.dataset.fillupRange === range;
    btn.setAttribute('aria-checked', active ? 'true' : 'false');
    btn.classList.toggle('active', active);
    btn.tabIndex = 0;
  });
}

async function renderFillupsPanel() {
  const summaryEl = document.getElementById('fillups-summary');
  const listEl = document.getElementById('fillups-list');
  if (!summaryEl || !listEl) return;

  renderRangeControls();

  const historyData = await loadHistoryData().catch(() => null);
  const items = getFillups();
  const stats = computeStats(items, historyData, { range });

  if (stats.count === 0) {
    if (items.length > 0 && range !== 'all') {
      // The log is not empty, the *period* is: offer the way out instead of the
      // contradictory "no fill-ups recorded".
      summaryEl.innerHTML = `
        <div class="fillups-empty">${t('fillupRangeEmpty')}</div>
        <button type="button" class="fillups-viewall" id="fillups-viewall">${escapeHtml(tf('fillupViewAll', { count: items.length }))}</button>`;
      summaryEl.querySelector('#fillups-viewall')?.addEventListener('click', () => setRange('all'));
    } else {
      summaryEl.innerHTML = `<div class="fillups-empty">${t('fillupNoRecords')}</div>`;
    }
  } else {
    summaryEl.innerHTML = `
      <div class="fillups-stat">
        <span class="fillups-stat-label">${rangeSpendLabel(range)}</span>
        <b class="fillups-stat-value">${formatDollars(stats.spendDollars)}</b>
      </div>
      <div class="fillups-stat">
        <span class="fillups-stat-label">${t('fillupAvgPrice')}</span>
        <b class="fillups-stat-value">${stats.avgPriceCents != null ? stats.avgPriceCents.toFixed(1) + '¢/L' : '—'}</b>
      </div>
      <div class="fillups-stat ${stats.savingsKnown && stats.savingsDollars < 0 ? 'neg' : ''}">
        <span class="fillups-stat-label">${t('fillupSavings')}</span>
        <b class="fillups-stat-value">${stats.savingsKnown ? formatDollars(stats.savingsDollars) : '—'}</b>
      </div>
      <div class="fillups-subline">${escapeHtml(tf('fillupCountLiters', { count: stats.count, liters: stats.liters.toFixed(1) }))}</div>`;
  }

  listEl.innerHTML = items.map(fillupItemHTML).join('');
  listEl.querySelectorAll('[data-fillup-delete]').forEach(btn => {
    btn.addEventListener('click', () => deleteFillup(btn.dataset.fillupDelete));
  });
}

export function initFillups() {
  const toggle = document.getElementById('fillups-toggle');
  const section = document.getElementById('fillups-section');
  if (toggle && section) {
    toggle.addEventListener('click', () => {
      const open = section.classList.toggle('open');
      toggle.setAttribute('aria-expanded', String(open));
    });
  }

  const group = document.getElementById('fillups-range');
  if (group) {
    group.addEventListener('click', (event) => {
      const btn = event.target.closest('[data-fillup-range]');
      if (btn) setRange(btn.dataset.fillupRange);
    });
    // Arrow keys move/select within the radiogroup; Enter/Space work natively.
    group.addEventListener('keydown', (event) => {
      const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
      if (!step) return;
      event.preventDefault();
      const idx = RANGES.indexOf(range);
      const next = RANGES[(idx + step + RANGES.length) % RANGES.length];
      setRange(next);
      group.querySelector(`[data-fillup-range="${next}"]`)?.focus();
    });
  }

  document.getElementById('fillups-export-csv')?.addEventListener('click', () => {
    exportFillupsCsv();
  });

  renderRangeControls();
  renderFillupsPanel();
  subscribe(() => renderFillupsPanel());
  onLanguageChange(() => renderFillupsPanel());
}

// Test/console hooks (mirrors window.__qcGasSearch / window.__qcGasMap).
if (typeof window !== 'undefined') {
  window.__qcGasFillups = {
    STORAGE_KEY,
    PREFS_KEY,
    RANGES,
    getFillups,
    addFillup,
    deleteFillup,
    getRange,
    setRange,
    computeStats,
    computeMonthStats,
    buildCsv,
    exportFillupsCsv,
    regionAverageForDate,
    localDateKey,
    dayKey
  };
}

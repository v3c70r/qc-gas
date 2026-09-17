// ── Fill-ups (加油日志) ──
// Local-only fuel log: localStorage persistence + pure statistics. No backend,
// no account, no new network requests. The summary compares each logged fill-up
// against the REAL region-level average for that day (data/history.json), and
// degrades gracefully to simple totals when history is unavailable.

import { t, onLanguageChange, translations, getLanguage } from './i18n.js';
import { loadHistoryData } from './history.js';

export const STORAGE_KEY = 'qc-gas-fillups';
const FUEL_KEYS = ['regular', 'super', 'diesel'];

let fillups = loadFillups();
const listeners = new Set();

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
 * Current-month summary for the fill-up log.
 * Returns { count, liters, spendDollars, avgPriceCents, savingsDollars,
 *           savingsKnown }.
 * `avgPriceCents` is litres-weighted; `savingsDollars` sums
 * (region average − paid price) × litres for each matched fill-up and is null
 * when no fill-up could be matched to real history.
 */
export function computeMonthStats(fillupsList, historyData, now = new Date()) {
  const monthKey = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}`;
  const month = (fillupsList || []).filter(f => String(f.date || '').slice(0, 7) === monthKey);

  let liters = 0;
  let spend = 0;
  let weightedPriceSum = 0;
  let weightedLiters = 0;
  let simplePriceSum = 0;
  let simplePriceCount = 0;
  let savings = 0;
  let savingsKnown = false;

  for (const f of month) {
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
    count: month.length,
    liters,
    spendDollars: spend,
    avgPriceCents,
    savingsDollars: savings,
    savingsKnown
  };
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

async function renderFillupsPanel() {
  const summaryEl = document.getElementById('fillups-summary');
  const listEl = document.getElementById('fillups-list');
  if (!summaryEl || !listEl) return;

  const historyData = await loadHistoryData().catch(() => null);
  const stats = computeMonthStats(getFillups(), historyData);

  if (stats.count === 0) {
    summaryEl.innerHTML = `<div class="fillups-empty">${t('fillupNoRecords')}</div>`;
  } else {
    summaryEl.innerHTML = `
      <div class="fillups-stat">
        <span class="fillups-stat-label">${t('fillupMonthSpend')}</span>
        <b class="fillups-stat-value">${formatDollars(stats.spendDollars)}</b>
      </div>
      <div class="fillups-stat">
        <span class="fillups-stat-label">${t('fillupAvgPrice')}</span>
        <b class="fillups-stat-value">${stats.avgPriceCents != null ? stats.avgPriceCents.toFixed(1) + '¢/L' : '—'}</b>
      </div>
      <div class="fillups-stat ${stats.savingsKnown && stats.savingsDollars < 0 ? 'neg' : ''}">
        <span class="fillups-stat-label">${t('fillupSavings')}</span>
        <b class="fillups-stat-value">${stats.savingsKnown ? formatDollars(stats.savingsDollars) : '—'}</b>
      </div>`;
  }

  const items = getFillups();
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

  renderFillupsPanel();
  subscribe(() => renderFillupsPanel());
  onLanguageChange(() => renderFillupsPanel());
}

// Test/console hooks (mirrors window.__qcGasSearch / window.__qcGasMap).
if (typeof window !== 'undefined') {
  window.__qcGasFillups = {
    STORAGE_KEY,
    getFillups,
    addFillup,
    deleteFillup,
    computeMonthStats,
    regionAverageForDate,
    localDateKey,
    dayKey
  };
}

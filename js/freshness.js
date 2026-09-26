// Data freshness (issue #47).
//
// Two responsibilities:
//  1. `formatRelativeTime()` renders the age of the snapshot currently on
//     screen, so an open tab (or an installed PWA left running) never presents
//     a stale snapshot as if it were live.
//  2. `refreshNow()` / `maybeRefresh()` pull `data/stations.json` again while
//     the page is visible and online, and hand a **newer** snapshot to the app
//     so it can be applied in place.
//
// The header (#data-status) has exactly one writer — pwa.js — because it also
// owns the honest offline label. This module only notifies its subscribers when
// the relative label must be recomputed (new snapshot applied, minute tick).
//
// Without Intl.RelativeTimeFormat the module falls back to hand-written
// plurals translated through the shared i18n dictionary.

import { translations, getLanguage } from './i18n.js';

// Relative path so the app works both at the domain root and under a sub-path.
export const DATA_URL = 'data/stations.json';
// Background check cadence while the tab stays visible.
export const CHECK_INTERVAL_MS = 10 * 60 * 1000;
// Never hit the network more often than this, whatever the trigger was.
export const MIN_REFRESH_GAP_MS = 5 * 60 * 1000;
// The label is relative, so it must be recomputed every minute.
export const LABEL_TICK_MS = 60 * 1000;
// A snapshot younger than this is simply "just now".
const JUST_NOW_THRESHOLD_S = 45;

// ── Relative time formatting (pure) ───────────────────────────────────────

function dictFor(lang) {
  if (translations[lang]) return translations[lang];
  const base = String(lang || '').slice(0, 2).toLowerCase();
  const code = Object.keys(translations).find((key) => key.slice(0, 2).toLowerCase() === base);
  return translations[code] || translations['en-CA'];
}

function fill(template, n) {
  return String(template).replace('{n}', String(n));
}

// Intl gives us proper localized wording; only the compact "short" style is
// used so the header stays a single glanceable line.
function intlRelativeTime(secondsAgo, lang) {
  if (typeof Intl === 'undefined' || typeof Intl.RelativeTimeFormat !== 'function') return null;

  let value;
  let unit;
  if (secondsAgo < 3600) {
    value = -Math.round(secondsAgo / 60);
    unit = 'minute';
  } else if (secondsAgo < 86400) {
    value = -Math.round(secondsAgo / 3600);
    unit = 'hour';
  } else {
    value = -Math.round(secondsAgo / 86400);
    unit = 'day';
  }

  try {
    return new Intl.RelativeTimeFormat(lang, { numeric: 'always', style: 'short' }).format(value, unit);
  } catch {
    return null;
  }
}

// Fallback for engines without Intl.RelativeTimeFormat (hand-written plurals).
function manualRelativeTime(secondsAgo, dict) {
  if (secondsAgo < 3600) return fill(dict.relMinutes, Math.round(secondsAgo / 60));
  if (secondsAgo < 86400) {
    const hours = Math.round(secondsAgo / 3600);
    return fill(hours === 1 ? dict.relHour : dict.relHours, hours);
  }
  const days = Math.round(secondsAgo / 86400);
  return fill(days === 1 ? dict.relDay : dict.relDays, days);
}

/**
 * Human-readable age of a timestamp ("il y a 12 min", "just now", "刚刚").
 *
 * @param {number} thenMs timestamp to describe (ms since epoch)
 * @param {number} nowMs current time (ms since epoch)
 * @param {string} [lang] BCP-47 tag; defaults to the active UI language
 * @returns {string} empty string when the inputs are not usable numbers
 */
export function formatRelativeTime(thenMs, nowMs, lang = getLanguage()) {
  const then = Number(thenMs);
  const now = Number(nowMs);
  if (!Number.isFinite(then) || !Number.isFinite(now)) return '';

  const secondsAgo = (now - then) / 1000;
  const dict = dictFor(lang);
  if (secondsAgo < JUST_NOW_THRESHOLD_S) return dict.justNow;

  const text = intlRelativeTime(secondsAgo, lang) ?? manualRelativeTime(secondsAgo, dict);
  // Intl inserts narrow no-break spaces in some locales; normalize them so the
  // label behaves like plain text when copied, tested or ellipsized.
  return text.replace(/[\u202f\u00a0]/g, ' ');
}

// ── Snapshot state ────────────────────────────────────────────────────────

// Injected by map.js: applies a freshly fetched snapshot in place.
let applySnapshot = null;
// The snapshot currently displayed; a fetch only wins when it is newer.
let hasSnapshot = false;
let lastAppliedAt = null; // generated_at (ms) of the displayed snapshot
let lastCheckAt = 0;
let refreshInFlight = false;
let started = false;
let checkTimer = null;
let labelTimer = null;
const subscribers = new Set();

export function subscribeFreshness(fn) {
  subscribers.add(fn);
  return () => subscribers.delete(fn);
}

function notify() {
  subscribers.forEach((fn) => {
    try {
      fn();
    } catch (error) {
      console.error('Freshness subscriber failed', error);
    }
  });
}

/** Recompute time-dependent labels (used by the minute ticker and tests). */
export function tick() {
  notify();
}

/** generated_at (ms) of the snapshot currently on screen, or null. */
export function getLastAppliedAt() {
  return lastAppliedAt;
}

/** Timestamp (ms) of the last refresh attempt (success or failure). */
export function getLastCheckAt() {
  return lastCheckAt;
}

/** Test seam: backdate (or clear) the throttle timestamp. */
export function setLastCheckAt(ms) {
  lastCheckAt = Number(ms) || 0;
}

/** Called by map.js whenever a snapshot (initial or refreshed) is displayed. */
export function setAppliedSnapshot(generatedAtIso) {
  const ms = Date.parse(generatedAtIso ?? '');
  lastAppliedAt = Number.isFinite(ms) ? ms : null;
  hasSnapshot = true;
  notify();
}

export function isHidden() {
  return typeof document !== 'undefined' && document.hidden === true;
}

export function isOffline() {
  return typeof navigator !== 'undefined' && navigator.onLine === false;
}

/**
 * Fetch the snapshot and apply it when it is strictly newer than the one on
 * screen. Returns true only when new data was applied. Failures are silent by
 * design: the current data and its honest age label stay untouched.
 */
export async function refreshNow() {
  if (typeof applySnapshot !== 'function' || !hasSnapshot) return false;
  if (isHidden() || isOffline()) return false;
  if (refreshInFlight) return false;

  refreshInFlight = true;
  lastCheckAt = Date.now();
  try {
    const response = await fetch(DATA_URL, { cache: 'no-store' });
    if (!response || !response.ok) return false;

    const data = await response.json();
    // An empty/truncated payload must never wipe a good snapshot.
    if (!Array.isArray(data?.features) || data.features.length === 0) return false;
    const next = Date.parse(data?.metadata?.generated_at ?? '');
    if (!Number.isFinite(next)) return false;
    if (lastAppliedAt !== null && next <= lastAppliedAt) return false;

    applySnapshot(data, response);
    return true;
  } catch {
    // Offline, flaky network or malformed payload: keep showing what we have.
    return false;
  } finally {
    refreshInFlight = false;
  }
}

/**
 * Throttled variant used by the environment triggers (visibility, online,
 * interval): at most one request every MIN_REFRESH_GAP_MS.
 */
export function maybeRefresh() {
  if (isHidden() || isOffline()) return Promise.resolve(false);
  if (Date.now() - lastCheckAt < MIN_REFRESH_GAP_MS) return Promise.resolve(false);
  return refreshNow();
}

/**
 * Wire the automatic triggers. `applyStations(data, response)` is provided by
 * map.js, which owns every other consumer of the snapshot.
 */
export function startFreshness({ applyStations } = {}) {
  if (typeof applyStations === 'function') applySnapshot = applyStations;
  if (started || typeof document === 'undefined') return;
  started = true;
  lastCheckAt = Date.now();

  document.addEventListener('visibilitychange', () => {
    // Coming back from the background is the moment a stale price matters most.
    if (document.hidden) return;
    maybeRefresh();
  });

  window.addEventListener('online', () => {
    maybeRefresh();
  });

  checkTimer = setInterval(() => {
    maybeRefresh();
  }, CHECK_INTERVAL_MS);

  labelTimer = setInterval(() => {
    if (isHidden()) return;
    notify();
  }, LABEL_TICK_MS);
}

// Test/console hooks (mirrors window.__qcGasMap / window.__qcGasPwa).
if (typeof window !== 'undefined') {
  window.__qcGasFreshness = {
    formatRelativeTime,
    refreshNow,
    maybeRefresh,
    getLastAppliedAt,
    getLastCheckAt,
    setLastCheckAt,
    tick,
    DATA_URL
  };
}

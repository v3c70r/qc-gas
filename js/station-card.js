// ── Station data card — stock-exchange style price history ──
// Inspired by Apple Stocks: big quote, change chip, sparkline, range switcher.
// Popup = compact card; side panel = expanded card (Chart.js area chart).

import { t, tf, translations, getLanguage, onLanguageChange } from './i18n.js';
import { getStationHistory, getStationHistoryCoverage, loadStationHistoryData } from './history.js';
import { computeRegionBenchmark } from './benchmark.js';
import { loadChartJS } from './chartjs.js';
import { isFavorite, toggleFavorite, stationId, STAR_ICON } from './favorites.js';
import { addFillup, localDateKey } from './fillups.js';
import { MONTREAL_CENTER, currentStations } from './map.js';
import { haversineDistance } from './stats.js';
import { isWatching, getWatchEntry, setWatch, removeWatch, setWatchThreshold, setWatchFuel } from './watch.js';

const FUEL_KEYS = ['regular', 'super', 'diesel'];
const RANGES = [
  { key: 'rangeWeek', days: 7 },
  { key: 'rangeMonth', days: 30 },
  { key: 'range3Months', days: 90 },
  { key: 'range6Months', days: 180 },
  { key: 'rangeYear', days: 365 }
];

let activePopup = null;        // mapboxgl Popup instance
let currentFeature = null;     // feature displayed in popup
let popupFuel = 'regular';
let popupRange = 7;
let popupUpdated = '';

// ── Trip cost estimator preferences (localStorage, no backend) ──
const TRIP_STORAGE_KEY = 'qc-gas-trip';
const DEFAULT_CONSUMPTION = 8; // L/100 km

let tripPrefs = loadTripPrefs();

function loadTripPrefs() {
  try {
    const parsed = JSON.parse(localStorage.getItem(TRIP_STORAGE_KEY) || 'null');
    const consumption = Number(parsed?.consumption);
    return {
      consumption: Number.isFinite(consumption) && consumption > 0 ? consumption : DEFAULT_CONSUMPTION,
      roundTrip: parsed?.roundTrip === true
    };
  } catch {
    return { consumption: DEFAULT_CONSUMPTION, roundTrip: false };
  }
}

function saveTripPrefs() {
  try {
    localStorage.setItem(TRIP_STORAGE_KEY, JSON.stringify(tripPrefs));
  } catch {
    // Storage may be unavailable; the estimator still works for this session.
  }
}

// Keep open popup/detail star labels in sync when the language changes.
onLanguageChange(async () => {
  await loadStationHistoryData().catch(() => {});
  if (activePopup && currentFeature) {
    activePopup.setHTML(cardHTML(currentFeature));
  }
  if (detailEl && detailFeature && detailEl.classList.contains('open')) {
    // Re-render the whole panel so range pills, stats and chart labels
    // pick up the newly selected language.
    renderDetail().catch(() => {});
  } else {
    const favBtn = detailEl?.querySelector('.sd-fav');
    if (favBtn && detailFeature) {
      const fav = isFavorite(detailFeature);
      favBtn.setAttribute('aria-label', fav ? t('unfavorite') : t('favorite'));
    }
  }
});

// ── Small helpers ──
function fuelLabel(fuel) {
  const dict = translations[getLanguage()];
  return dict?.[fuel] || fuel;
}
function fmt(v) { return v.toFixed(1); }
function fmtMoney(v) { return Math.abs(v).toFixed(2); }
function sign(v) { return v > 0 ? '+' : v < 0 ? '−' : ''; }
function centsToDollar(cents) { return (cents / 100).toFixed(2); }

// Direction: gas price rising = red (cost up), falling = green (cheap)
function trendColor(series) {
  if (!series || series.length < 2) return '#64748b';
  const first = series[0].price, last = series[series.length - 1].price;
  if (last > first) return '#dc2626';
  if (last < first) return '#16a34a';
  return '#64748b';
}

function changeInfo(series) {
  if (!series || series.length < 2) return { diff: 0, pct: 0, up: false, flat: true };
  const first = series[0].price, last = series[series.length - 1].price;
  const diff = last - first;
  const pct = first !== 0 ? (diff / first) * 100 : 0;
  return { diff, pct, up: diff > 0, flat: diff === 0 };
}

// ── Smooth SVG sparkline (catmull-rom → bezier) ──
function sparklinePath(series, w, h) {
  if (!series || series.length < 2) return '';
  const prices = series.map(s => s.price);
  const min = Math.min(...prices), max = Math.max(...prices);
  const span = max - min || 1;
  const pad = 4;
  const stepX = (w - pad * 2) / (series.length - 1);
  const pts = prices.map((p, i) => [
    pad + i * stepX,
    h - pad - ((p - min) / span) * (h - pad * 2)
  ]);
  // Catmull-Rom smoothing
  let d = `M ${pts[0][0].toFixed(1)} ${pts[0][1].toFixed(1)}`;
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[Math.max(0, i - 1)], p1 = pts[i], p2 = pts[i + 1], p3 = pts[Math.min(pts.length - 1, i + 2)];
    const cp1x = p1[0] + (p2[0] - p0[0]) / 6, cp1y = p1[1] + (p2[1] - p0[1]) / 6;
    const cp2x = p2[0] - (p3[0] - p1[0]) / 6, cp2y = p2[1] - (p3[1] - p1[1]) / 6;
    d += ` C ${cp1x.toFixed(1)} ${cp1y.toFixed(1)}, ${cp2x.toFixed(1)} ${cp2y.toFixed(1)}, ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return d;
}

function sparklineSVG(series, color) {
  const w = 260, h = 72;
  if (series.length === 1) {
    return `
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="72" preserveAspectRatio="none" aria-hidden="true">
      <circle cx="${w / 2}" cy="${h / 2}" r="3.5" fill="${color}"/>
    </svg>`;
  }
  const path = sparklinePath(series, w, h);
  const area = `${path} L ${w - 4} ${h - 1} L 4 ${h - 1} Z`;
  const gid = 'sl_' + Math.random().toString(36).slice(2, 8);
  return `
    <svg viewBox="0 0 ${w} ${h}" width="100%" height="72" preserveAspectRatio="none" aria-hidden="true">
      <defs>
        <linearGradient id="${gid}" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${color}" stop-opacity="0.22"/>
          <stop offset="100%" stop-color="${color}" stop-opacity="0"/>
        </linearGradient>
      </defs>
      <path d="${area}" fill="url(#${gid})"/>
      <path d="${path}" fill="none" stroke="${color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
}

// ── Same-day regional benchmark (full snapshot, filter-independent) ──
function benchmarkFor(feature, fuel) {
  const props = feature && feature.properties;
  const region = props && props.region;
  const priceKey = fuel + '_price';
  const price = props && props[priceKey];
  if (!region || price == null || !currentStations) return null;
  return computeRegionBenchmark(currentStations, region, priceKey, price);
}

function benchmarkBlockHTML(bench, prefix) {
  if (!bench) return '';
  const head = `<div class="${prefix}-bench-head">${t('benchTitle')}</div>`;
  if (!bench.sufficient || bench.median == null) {
    return `${head}<div class="${prefix}-bench-insufficient">${t('benchInsufficient')} (${bench.count})</div>`;
  }

  const vs = bench.gapToMedian;
  const vsCls = vs < 0 ? 'bench-pos' : vs > 0 ? 'bench-neg' : 'bench-zero';
  const saving = bench.saving50L;
  const savingCls = saving > 0 ? 'bench-pos' : saving < 0 ? 'bench-neg' : 'bench-zero';
  const gapMin = bench.gapToMin;
  const gapMinCls = gapMin === 0 ? 'bench-pos' : '';
  const gapMinText = gapMin > 0 ? `+${fmt(gapMin)}` : '0.0';
  const savingText = `${saving < 0 ? '−' : ''}$${fmtMoney(saving)}`;
  const percentilePhrase = escapeHtml(tf('benchCheaperThan', { p: bench.cheaperThanPercent }));

  return `${head}
    <div class="${prefix}-bench-grid">
      <div class="${prefix}-bench-item"><span>${t('benchMedian')}</span><b>${fmt(bench.median)}¢</b></div>
      <div class="${prefix}-bench-item ${vsCls}"><span>${t('benchVsMedian')}</span><b>${sign(vs)}${fmt(Math.abs(vs))}¢</b></div>
      <div class="${prefix}-bench-item" title="${percentilePhrase}"><span>${t('benchPercentile')}</span><b>${bench.cheaperThanPercent}%</b></div>
      <div class="${prefix}-bench-item ${savingCls}"><span>${t('benchSaving50L')}</span><b>${savingText}</b></div>
      <div class="${prefix}-bench-item ${gapMinCls}"><span>${t('benchGapMin')}</span><b>${gapMinText}¢</b></div>
    </div>
    <div class="${prefix}-bench-note">${percentilePhrase}</div>`;
}

function rangePillsHTML(prefix, activeDays, coverageDays) {
  return RANGES.map(r => {
    const disabled = coverageDays > 0 && r.days > coverageDays;
    const active = !disabled && r.days === activeDays;
    const cls = `${prefix}-rangepill${active ? ' on' : ''}${disabled ? ' is-disabled' : ''}`;
    const attrs = disabled ? ' aria-disabled="true" disabled' : '';
    return `<button class="${cls}" data-days="${r.days}"${attrs}>${t(r.key)}</button>`;
  }).join('');
}

function coverageHTML(prefix, coverage) {
  if (!coverage || coverage.days <= 0 || !coverage.firstDate) return '';
  return `<div class="${prefix}-coverage">${escapeHtml(tf('dataCoverage', { n: coverage.days, date: coverage.firstDate }))}</div>`;
}

// ── Build compact card HTML for the Mapbox popup ──
function cardHTML(feature) {
  const props = feature.properties;
  if (props[popupFuel + '_price'] == null) {
    // fall back to first available fuel
    for (const f of FUEL_KEYS) {
      if (props[f + '_price'] != null) { popupFuel = f; break; }
    }
  }

  const coverage = getStationHistoryCoverage(feature, popupFuel);
  if (coverage.days > 0) popupRange = Math.min(popupRange, coverage.days);
  const series = getStationHistory(feature, popupFuel, popupRange) || [];
  const color = trendColor(series);
  const hasTrend = series.length >= 2;
  const { diff, pct, up, flat } = changeInfo(series);
  const priceVal = props[popupFuel + '_price'];
  const available = FUEL_KEYS.filter(f => props[f + '_price'] != null);
  const bench = benchmarkFor(feature, popupFuel);

  const fuelPills = available.map(f => `
    <button class="sc-pill ${f === popupFuel ? 'on' : ''}" data-fuel="${f}">${fuelLabel(f)}</button>`).join('');

  const rangePills = rangePillsHTML('sc', popupRange, coverage.days);

  // mini stats
  const prices = series.map(s => s.price);
  const hi = prices.length ? Math.max(...prices) : null;
  const lo = prices.length ? Math.min(...prices) : null;
  const fewSamples = series.length > 0 && series.length <= 2;
  const highLabel = fewSamples ? tf('highRecent', { n: coverage.days }) : t('high');
  const lowLabel = fewSamples ? tf('lowRecent', { n: coverage.days }) : t('low');

  const arrow = flat ? '' : (up ? '▲' : '▼');
  const sign = flat ? '' : (up ? '+' : '−');
  const badgeCls = flat ? 'flat' : up ? 'up' : 'down';
  const delta = flat ? '0,0' : `${sign}${fmt(Math.abs(diff))},${sign}${fmt(Math.abs(pct))}`;
  const changeHTML = hasTrend ? `
      <div class="sc-change ${badgeCls}">
        <span class="sc-arrow">${arrow}</span>
        <span class="sc-delta">${delta.split(',')[0]}</span>
        <span class="sc-pct">(${delta.split(',')[1] || '0.0'}%)</span>
      </div>` : '';

  const fav = isFavorite(feature);
  return `
  <div class="sc-card" data-sc-card>
    <div class="sc-top">
      <div class="sc-title">
        <div class="sc-name">${escapeHtml(props.name || props.brand || '')}</div>
        <div class="sc-sub">${escapeHtml(props.brand || '')}${props.address ? ' · ' + escapeHtml(props.address) : ''}</div>
      </div>
      <button class="sc-fav ${fav ? 'on' : ''}" data-sc-fav aria-label="${fav ? t('unfavorite') : t('favorite')}" aria-pressed="${fav}">${STAR_ICON}</button>
    </div>
    ${available.length > 1 ? `<div class="sc-pills">${fuelPills}</div>` : ''}
    <div class="sc-quote">
      <div class="sc-price">${priceVal != null ? fmt(priceVal) : '—'}<span class="sc-unit">¢</span></div>
      ${changeHTML}
    </div>
    ${bench ? `<div class="sc-bench">${benchmarkBlockHTML(bench, 'sc')}</div>` : ''}
    <div class="sc-range">${rangePills}</div>
    ${coverageHTML('sc', coverage)}
    ${series.length ? `<div class="sc-chart">${sparklineSVG(series, color)}</div>` : `<div class="sc-empty">${t('noHistory')}</div>`}
    ${priceVal != null ? `
    <div class="sc-mini-stats">
      <div class="sc-mini"><span>${highLabel}</span><b>${hi != null ? fmt(hi) : '—'}</b></div>
      <div class="sc-mini"><span>${lowLabel}</span><b>${lo != null ? fmt(lo) : '—'}</b></div>
      <div class="sc-mini"><span>≈ $/L</span><b>${centsToDollar(priceVal)}</b></div>
    </div>` : ''}
    <button class="sc-expand" data-expand>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
      ${t('expand')}
    </button>
    <button class="sc-fillup" data-fillup-add>⛽ ${t('fillupRecordHere')}</button>
    ${popupUpdated ? `<div class="sc-updated">${popupUpdated}</div>` : ''}
  </div>`;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ── Open the popup card for a station ──
export async function showStationCard(feature, map, updatedText = '') {
  const props = feature.properties;
  popupUpdated = updatedText || '';
  // Respect the fuel chosen in the sidebar filter
  const checkedFuel = document.querySelector('.fuel-filter:checked');
  popupFuel = checkedFuel && props[checkedFuel.value + '_price'] != null ? checkedFuel.value
    : (props.regular_price != null ? 'regular' : FUEL_KEYS.find(f => props[f + '_price'] != null) || 'regular');
  popupRange = 7;
  currentFeature = feature;

  await loadStationHistoryData().catch(() => {});
  const coverage = getStationHistoryCoverage(feature, popupFuel);
  if (coverage.days > 0) popupRange = Math.min(popupRange, coverage.days);

  if (activePopup) { activePopup.remove(); }

  const popup = new mapboxgl.Popup({
    closeButton: true, closeOnClick: true, anchor: 'bottom', maxWidth: '320px', offset: 14, className: 'sc-popup'
  })
    .setLngLat(feature.geometry.coordinates)
    .setHTML(cardHTML(feature))
    .addTo(map);

  activePopup = popup;

  // Rebuild card when range/fuel changes inside popup
  popup.on('close', () => { activePopup = null; });
  wireCardEvents(feature, map);
}

// ── Delegate clicks inside the popup (fuel pills, range pills, expand) ──
function wireCardEvents(feature, map) {
  if (window.__scWired) return;
  window.__scWired = true;

  document.addEventListener('click', (e) => {
    if (!activePopup) return;

    const favBtn = e.target.closest('[data-sc-fav]');
    if (favBtn && activePopup && currentFeature) {
      toggleFavorite(currentFeature);
      activePopup.setHTML(cardHTML(currentFeature));
      return;
    }
    const fuelBtn = e.target.closest('[data-fuel]');
    if (fuelBtn && activePopup && currentFeature) {
      popupFuel = fuelBtn.dataset.fuel;
      activePopup.setHTML(cardHTML(currentFeature));
      return;
    }
    const rangeBtn = e.target.closest('[data-days]');
    if (rangeBtn && activePopup && currentFeature) {
      popupRange = parseInt(rangeBtn.dataset.days, 10);
      activePopup.setHTML(cardHTML(currentFeature));
      return;
    }
    const expand = e.target.closest('[data-expand]');
    if (expand && activePopup && currentFeature) {
      if (activePopup) activePopup.remove();
      activePopup = null;
      openStationDetail(currentFeature, map);
    }
    const fillup = e.target.closest('[data-fillup-add]');
    if (fillup && activePopup && currentFeature) {
      if (activePopup) activePopup.remove();
      activePopup = null;
      openStationDetail(currentFeature, map).then(() => openFillupForm());
    }
  });
}

// ═══════════════════════════════════════════════
//  Expanded side panel (Apple-stock style, Chart.js)
// ═══════════════════════════════════════════════
let detailEl = null;
let detailChart = null;
let detailFeature = null;
let detailFuel = 'regular';
let detailRange = 90;
let detailMap = null;
let detailUpdated = '';

// ── Pull-down-to-dismiss for mobile bottom sheets ──
function initSheetDrag(sheet, handle, onDismiss) {
  if (!sheet || !handle) return;
  let startY = 0;
  let dragged = false;

  handle.addEventListener('touchstart', (e) => {
    if (!sheet.classList.contains('open')) return;
    startY = e.touches[0].clientY;
    dragged = false;
    sheet.classList.add('dragging');
  }, { passive: true });

  handle.addEventListener('touchmove', (e) => {
    if (!sheet.classList.contains('open')) return;
    const delta = e.touches[0].clientY - startY;
    if (Math.abs(delta) > 8) dragged = true;
    // Only downward drags are meaningful for a dismiss gesture.
    sheet.style.transform = `translateY(${Math.max(0, delta)}px)`;
  }, { passive: true });

  const finish = () => {
    if (!sheet.classList.contains('open')) {
      sheet.classList.remove('dragging');
      sheet.style.transform = '';
      return;
    }
    const match = sheet.style.transform.match(/translateY\(([0-9.-]+)px\)/);
    const offset = match ? parseFloat(match[1]) : 0;
    sheet.classList.remove('dragging');
    sheet.style.transform = '';
    const threshold = Math.max(90, sheet.offsetHeight * 0.25);
    if (dragged && offset > threshold) onDismiss();
  };

  handle.addEventListener('touchend', finish);
  handle.addEventListener('touchcancel', () => {
    sheet.classList.remove('dragging');
    sheet.style.transform = '';
  });
}

function buildDetailPanel() {
  if (detailEl) return detailEl;
  detailEl = document.createElement('div');
  detailEl.id = 'station-panel';
  detailEl.innerHTML = `
    <div class="sd-handle" aria-hidden="true"><div class="handle-bar"></div></div>
    <div class="sd-head">
      <div class="sd-head-info">
        <div class="sd-name"></div>
        <div class="sd-sub"></div>
      </div>
      <div class="sd-head-actions">
        <button class="sd-fav" aria-label="${t('favorite')}" aria-pressed="false">${STAR_ICON}</button>
        <button class="sd-close" aria-label="${t('close')}">✕</button>
      </div>
    </div>
    <div class="sd-body">
      <div class="sd-pills"></div>
      <div class="sd-quote">
        <div class="sd-bigprice"></div>
        <div class="sd-change-row"></div>
      </div>
      <div class="sd-watch">
        <div class="sd-watch-row">
          <button class="sd-watch-toggle" aria-pressed="false">
            <span class="sd-watch-icon" aria-hidden="true">🔔</span>
            <span class="sd-watch-toggle-text"></span>
          </button>
          <label class="sd-watch-field">
            <span class="sd-watch-label"></span>
            <input type="number" class="sd-watch-threshold" inputmode="decimal" min="0" step="0.1" placeholder="—">
          </label>
        </div>
        <div class="sd-watch-hint"></div>
      </div>
      <div class="sd-bench"></div>
      <div class="sd-range"></div>
      <div class="sd-coverage"></div>
      <div class="sd-chart">
        <canvas id="station-chart"></canvas>
        <div class="sd-empty" id="station-chart-empty" hidden>${t('noHistory')}</div>
      </div>
      <div class="sd-stats"></div>
      <div class="sd-trip">
        <div class="sd-trip-head"></div>
        <div class="sd-trip-row">
          <label class="sd-trip-field">
            <span class="sd-trip-label"></span>
            <input class="sd-trip-input" type="number" inputmode="decimal" min="0" step="0.1" value="8">
          </label>
          <div class="sd-trip-toggle" role="group">
            <button class="sd-trip-seg on" type="button" data-trip-mode="oneway"></button>
            <button class="sd-trip-seg" type="button" data-trip-mode="roundtrip"></button>
          </div>
        </div>
        <div class="sd-trip-result">
          <span class="sd-trip-result-label"></span>
          <b class="sd-trip-cost">—</b>
        </div>
        <div class="sd-trip-distance">
          <span class="sd-trip-distance-label"></span>
          <b class="sd-trip-distance-value">—</b>
        </div>
      </div>
      <div class="sd-fillups">
        <div class="sd-fillups-head">
          <span class="sd-fillups-title">⛽ ${t('fillups')}</span>
        </div>
        <button class="sd-fillup-add" data-fillup-add>${t('fillupRecordHere')}</button>
        <div class="sd-fillup-form" id="sd-fillup-form" hidden>
          <div class="sd-fillup-grid">
            <label class="sd-fillup-field">
              <span class="sd-fillup-label">${t('fillupDate')}</span>
              <input type="date" class="sd-fillup-date">
            </label>
            <label class="sd-fillup-field">
              <span class="sd-fillup-label">${t('fillupFuel')}</span>
              <select class="sd-fillup-fuel"></select>
            </label>
            <label class="sd-fillup-field">
              <span class="sd-fillup-label">${t('fillupPrice')}</span>
              <input type="number" class="sd-fillup-price" inputmode="decimal" min="0" step="0.1" placeholder="¢/L">
            </label>
            <label class="sd-fillup-field">
              <span class="sd-fillup-label">${t('fillupLiters')}</span>
              <input type="number" class="sd-fillup-liters" inputmode="decimal" min="0" step="0.01" placeholder="L">
            </label>
            <label class="sd-fillup-field">
              <span class="sd-fillup-label">${t('fillupTotal')}</span>
              <input type="number" class="sd-fillup-total" inputmode="decimal" min="0" step="0.01" placeholder="$">
            </label>
          </div>
          <div class="sd-fillup-actions">
            <button class="sd-fillup-save">${t('fillupSave')}</button>
            <button class="sd-fillup-cancel">${t('fillupCancel')}</button>
          </div>
        </div>
      </div>
      <div class="sd-actions">
        <button class="sd-nav"></button>
        <div class="sd-updated"></div>
      </div>
    </div>`;
  document.getElementById('map-container').appendChild(detailEl);

  detailEl.querySelector('.sd-close').addEventListener('click', closeStationDetail);

  // On mobile the panel is a bottom sheet: allow swipe-down to dismiss.
  initSheetDrag(detailEl, detailEl.querySelector('.sd-handle'), closeStationDetail);

  detailEl.querySelector('.sd-fav').addEventListener('click', () => {
    if (detailFeature) {
      toggleFavorite(detailFeature);
      renderDetail();
    }
  });

  // Price watch: bell toggles the watch; threshold input persists live (debounced).
  let watchThresholdTimer = null;
  detailEl.querySelector('.sd-watch-toggle').addEventListener('click', () => {
    if (!detailFeature) return;
    if (isWatching(detailFeature)) {
      removeWatch(detailFeature);
    } else {
      const input = detailEl.querySelector('.sd-watch-threshold');
      const raw = input?.value?.trim();
      const threshold = raw === '' ? null : Number(raw);
      setWatch(detailFeature, { fuel: detailFuel, thresholdCents: threshold });
    }
    renderDetail();
  });

  const watchThresholdInput = detailEl.querySelector('.sd-watch-threshold');
  const persistThreshold = () => {
    if (!detailFeature || !isWatching(detailFeature)) return;
    const raw = watchThresholdInput.value.trim();
    const threshold = raw === '' ? null : Number(raw);
    setWatchThreshold(detailFeature, threshold);
  };
  watchThresholdInput.addEventListener('input', () => {
    if (!detailFeature || !isWatching(detailFeature)) return;
    clearTimeout(watchThresholdTimer);
    watchThresholdTimer = setTimeout(persistThreshold, 300);
  });
  watchThresholdInput.addEventListener('change', () => {
    clearTimeout(watchThresholdTimer);
    persistThreshold();
  });

  // Delegated fuel / range switching inside panel
  detailEl.addEventListener('click', (e) => {
    const f = e.target.closest('[data-fuel]');
    if (f) {
      detailFuel = f.dataset.fuel;
      renderDetail();
      return;
    }
    const r = e.target.closest('[data-days]');
    if (r) {
      detailRange = parseInt(r.dataset.days, 10);
      renderDetail();
    }
  });

  // Trip cost estimator: consumption input + one-way / round-trip toggle
  const tripInput = detailEl.querySelector('.sd-trip-input');
  tripInput.addEventListener('input', () => {
    const raw = tripInput.value.trim();
    const val = Number(raw);
    if (raw !== '' && Number.isFinite(val) && val > 0) {
      tripPrefs.consumption = val;
      saveTripPrefs();
    }
    updateTripEstimatorResult();
  });

  detailEl.querySelector('.sd-trip-toggle').addEventListener('click', (e) => {
    const btn = e.target.closest('[data-trip-mode]');
    if (!btn) return;
    tripPrefs.roundTrip = btn.dataset.tripMode === 'roundtrip';
    saveTripPrefs();
    updateTripEstimator();
  });

  // Fill-up logging (localStorage, no backend)
  detailEl.querySelector('.sd-fillup-add').addEventListener('click', () => openFillupForm());
  detailEl.querySelector('.sd-fillup-cancel').addEventListener('click', () => {
    detailEl.querySelector('.sd-fillup-form').hidden = true;
  });
  const fillupFuelSel = detailEl.querySelector('.sd-fillup-fuel');
  fillupFuelSel.addEventListener('change', () => {
    if (!detailFeature) return;
    const price = detailFeature.properties[fillupFuelSel.value + '_price'];
    if (price != null) detailEl.querySelector('.sd-fillup-price').value = price;
  });
  detailEl.querySelector('.sd-fillup-save').addEventListener('click', () => saveFillup());

  const mapEl = document.getElementById('map-container');
  mapEl.classList.add('panel-open');
  return detailEl;
}

// ── Fill-up logging ──
function openFillupForm() {
  if (!detailFeature || !detailEl) return;
  const props = detailFeature.properties;
  const form = detailEl.querySelector('.sd-fillup-form');
  if (!form) return;

  const available = FUEL_KEYS.filter(f => props[f + '_price'] != null);
  const fuelSel = detailEl.querySelector('.sd-fillup-fuel');
  fuelSel.innerHTML = available.map(f => `<option value="${f}">${fuelLabel(f)}</option>`).join('');
  const fuel = available.includes(detailFuel) ? detailFuel : (available[0] || 'regular');
  fuelSel.value = fuel;

  detailEl.querySelector('.sd-fillup-date').value = localDateKey(new Date()) || '';
  detailEl.querySelector('.sd-fillup-price').value = props[fuel + '_price'] != null ? props[fuel + '_price'] : '';
  detailEl.querySelector('.sd-fillup-liters').value = '';
  detailEl.querySelector('.sd-fillup-total').value = '';
  form.hidden = false;
}

function saveFillup() {
  if (!detailFeature || !detailEl) return;
  const props = detailFeature.properties;
  const form = detailEl.querySelector('.sd-fillup-form');
  const dateInput = detailEl.querySelector('.sd-fillup-date');
  const fuelSel = detailEl.querySelector('.sd-fillup-fuel');
  const priceInput = detailEl.querySelector('.sd-fillup-price');
  const litersInput = detailEl.querySelector('.sd-fillup-liters');
  const totalInput = detailEl.querySelector('.sd-fillup-total');

  const date = dateInput.value;
  const fuel = fuelSel.value;
  const priceCents = Number(priceInput.value);
  const litersRaw = litersInput.value.trim();
  const totalRaw = totalInput.value.trim();
  const liters = litersRaw === '' ? null : Number(litersRaw);
  const totalPrice = totalRaw === '' ? null : Number(totalRaw);

  if (!date) { dateInput.focus(); return; }
  if (!Number.isFinite(priceCents) || priceCents <= 0) { priceInput.focus(); return; }
  if ((liters == null || !Number.isFinite(liters) || liters <= 0) &&
      (totalPrice == null || !Number.isFinite(totalPrice) || totalPrice <= 0)) {
    litersInput.focus();
    return;
  }

  const entry = {
    stationId: stationId(detailFeature),
    stationName: props.name || props.brand || '',
    brand: props.brand || '',
    region: props.region || '',
    fuel,
    date,
    priceCents,
    liters: (liters != null && Number.isFinite(liters) && liters > 0) ? liters : null,
    totalPrice: (totalPrice != null && Number.isFinite(totalPrice) && totalPrice > 0) ? totalPrice : null
  };

  if (addFillup(entry)) form.hidden = true;
}

function updateFillupLabels() {
  if (!detailEl) return;
  const labels = detailEl.querySelectorAll('.sd-fillup-label');
  const keys = ['fillupDate', 'fillupFuel', 'fillupPrice', 'fillupLiters', 'fillupTotal'];
  labels.forEach((label, i) => { if (keys[i]) label.textContent = t(keys[i]); });
  const add = detailEl.querySelector('.sd-fillup-add');
  if (add) add.textContent = t('fillupRecordHere');
  const title = detailEl.querySelector('.sd-fillups-title');
  if (title) title.innerHTML = `⛽ ${t('fillups')}`;
  const save = detailEl.querySelector('.sd-fillup-save');
  if (save) save.textContent = t('fillupSave');
  const cancel = detailEl.querySelector('.sd-fillup-cancel');
  if (cancel) cancel.textContent = t('fillupCancel');
}

function updateWatchRow() {
  if (!detailEl || !detailFeature) return;
  const entry = getWatchEntry(detailFeature);
  const watching = !!entry;
  const btn = detailEl.querySelector('.sd-watch-toggle');
  const input = detailEl.querySelector('.sd-watch-threshold');
  const label = detailEl.querySelector('.sd-watch-label');
  const hint = detailEl.querySelector('.sd-watch-hint');
  const text = detailEl.querySelector('.sd-watch-toggle-text');

  if (label) label.textContent = t('watchThresholdLabel');
  if (hint) hint.textContent = t('watchThresholdPlaceholder');
  if (btn) {
    btn.classList.toggle('on', watching);
    btn.setAttribute('aria-pressed', String(watching));
    const btnLabel = watching ? t('watchRemove') : t('watchSet');
    btn.setAttribute('aria-label', btnLabel);
    if (text) text.textContent = btnLabel;
  }
  if (input) {
    input.setAttribute('aria-label', t('watchThresholdLabel'));
    if (watching) {
      input.value = entry.thresholdCents != null ? String(entry.thresholdCents) : '';
    } else {
      const current = detailFeature.properties?.[detailFuel + '_price'];
      input.value = current != null ? String(Math.round((current - 2) * 10) / 10) : '';
    }
  }
}

export function closeStationDetail() {
  if (detailEl) {
    detailEl.classList.remove('open');
    document.getElementById('map-container').classList.remove('panel-open');
    if (detailChart) { detailChart.destroy(); detailChart = null; }
  }
}

export async function openStationDetail(feature, map, updatedText = '') {
  // Close trends dashboard if open (mutual exclusivity)
  const dp = document.getElementById('dashboard-panel');
  const mapC = document.getElementById('map-container');
  if (dp && dp.classList.contains('open')) { dp.classList.remove('open'); mapC.classList.remove('dashboard-open'); }

  detailFeature = feature;
  detailMap = map;
  detailUpdated = updatedText || '';
  // Default to sidebar fuel choice when possible
  const checked = document.querySelector('.fuel-filter:checked');
  const props = feature.properties;
  detailFuel = checked && props[checked.value + '_price'] != null ? checked.value
    : (props.regular_price != null ? 'regular' : FUEL_KEYS.find(f => props[f + '_price'] != null) || 'regular');
  detailRange = 90;

  const panel = buildDetailPanel();
  panel.classList.add('open');
  document.getElementById('map-container').classList.add('panel-open');
  await loadStationHistoryData().catch(() => {});
  const coverage = getStationHistoryCoverage(feature, detailFuel);
  if (coverage.days > 0) detailRange = Math.min(detailRange, coverage.days);
  renderDetail();
}

async function renderDetail() {
  if (!detailFeature) return;
  await loadStationHistoryData().catch(() => {});
  const props = detailFeature.properties;
  const available = FUEL_KEYS.filter(f => props[f + '_price'] != null);

  // Header
  detailEl.querySelector('.sd-name').textContent = props.name || props.brand || '';
  const region = props.region ? ' · ' + props.region : '';
  detailEl.querySelector('.sd-sub').textContent = `${props.brand || ''}${region}`;

  // Favorite star
  const fav = isFavorite(detailFeature);
  const favBtn = detailEl.querySelector('.sd-fav');
  if (favBtn) {
    favBtn.classList.toggle('on', fav);
    favBtn.setAttribute('aria-pressed', String(fav));
    favBtn.setAttribute('aria-label', fav ? t('unfavorite') : t('favorite'));
  }

  // Fuel pills
  detailEl.querySelector('.sd-pills').innerHTML = available.map(f => `
    <button class="sd-fuelpill ${f === detailFuel ? 'on' : ''}" data-fuel="${f}">${fuelLabel(f)}</button>`).join('');

  // Keep a watched station's threshold attached to the fuel being viewed.
  const watchEntry = getWatchEntry(detailFeature);
  if (watchEntry && watchEntry.fuel !== detailFuel) {
    setWatchFuel(detailFeature, detailFuel);
  }
  updateWatchRow();

  const coverage = getStationHistoryCoverage(detailFeature, detailFuel);
  if (coverage.days > 0) detailRange = Math.min(detailRange, coverage.days);
  const series = getStationHistory(detailFeature, detailFuel, detailRange) || [];
  const priceVal = props[detailFuel + '_price'];
  const color = trendColor(series);
  const hasTrend = series.length >= 2;
  const { diff, pct, up, flat } = changeInfo(series);
  const bench = benchmarkFor(detailFeature, detailFuel);

  // Big quote
  detailEl.querySelector('.sd-bigprice').innerHTML = priceVal != null
    ? `${fmt(priceVal)}<span class="sd-unit">¢/L</span>` : '—';

  const arrow = flat ? '' : up ? '▲' : '▼';
  const sign = flat ? '' : up ? '+' : '−';
  detailEl.querySelector('.sd-change-row').className = 'sd-change-row ' + (hasTrend ? (flat ? 'flat' : up ? 'up' : 'down') : '');
  detailEl.querySelector('.sd-change-row').innerHTML = hasTrend && priceVal != null
    ? `<span class="sd-arrow">${arrow}</span> <b>${sign}${fmt(Math.abs(diff))}</b> <span class="sd-pct">${sign}${fmt(Math.abs(pct))}%</span> <span class="sd-hint">(${t('sinceStart')})</span>`
    : '';

  // Range pills + data coverage line
  detailEl.querySelector('.sd-range').innerHTML = rangePillsHTML('sd', detailRange, coverage.days);
  detailEl.querySelector('.sd-coverage').innerHTML = coverageHTML('sd', coverage);

  // Same-day regional benchmark block
  const benchEl = detailEl.querySelector('.sd-bench');
  if (bench) {
    benchEl.hidden = false;
    benchEl.innerHTML = benchmarkBlockHTML(bench, 'sd');
  } else {
    benchEl.hidden = true;
    benchEl.innerHTML = '';
  }

  // Stats
  const prices = series.map(s => s.price);
  const lo = prices.length ? Math.min(...prices) : null;
  const hi = prices.length ? Math.max(...prices) : null;
  const avg = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : null;
  const lastChange = series.length >= 2 ? series[series.length - 1].price - series[series.length - 2].price : null;
  const lastCls = lastChange == null ? '' : lastChange > 0 ? 'up' : lastChange < 0 ? 'down' : '';
  const fewSamples = series.length > 0 && series.length <= 2;
  const highLabel = fewSamples ? tf('highRecent', { n: coverage.days }) : t('high');
  const lowLabel = fewSamples ? tf('lowRecent', { n: coverage.days }) : t('low');
  detailEl.querySelector('.sd-stats').innerHTML = `
    <div class="sd-stat"><span>${lowLabel}</span><b>${lo != null ? fmt(lo) : '—'}</b></div>
    <div class="sd-stat"><span>${highLabel}</span><b>${hi != null ? fmt(hi) : '—'}</b></div>
    <div class="sd-stat"><span>${t('avg')}</span><b>${avg != null ? fmt(avg) : '—'}</b></div>
    <div class="sd-stat ${lastCls}"><span>${t('lastPrice')}</span><b>${lastChange == null ? '—' : `${lastChange > 0 ? '+' : lastChange < 0 ? '−' : ''}${fmt(Math.abs(lastChange))}`}</b></div>`;

  updateTripEstimator();
  updateFillupLabels();

  // Navigate + updated
  const isIOS = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const [lng, lat] = detailFeature.geometry.coordinates;
  const navUrl = isIOS
    ? `https://maps.apple.com/?daddr=${lat},${lng}&dirflg=d`
    : `https://www.google.com/maps/dir/?api=1&destination=${lat},${lng}`;
  detailEl.querySelector('.sd-nav').innerHTML = `🧭 ${t('navigate')}`;
  detailEl.querySelector('.sd-nav').onclick = () => window.open(navUrl, '_blank', 'noopener');

  const upd = detailUpdated;
  detailEl.querySelector('.sd-updated').textContent = upd;

  // Chart
  await loadChartJS();
  if (!detailChart) {
    detailChart = new Chart(document.getElementById('station-chart'), {
      type: 'line',
      data: { labels: [], datasets: [{ data: [], borderColor: color, backgroundColor: color, fill: true, tension: 0.35, pointRadius: 0, borderWidth: 2 }] },
      options: detailChartOptions()
    });
  }
  updateDetailChart(series, color);
}

// ── Trip cost estimator (L/100 km × distance × station price) ──
function tripEstimate() {
  if (!detailFeature) return { distanceKm: null, cost: null };

  const raw = detailEl?.querySelector('.sd-trip-input')?.value ?? String(tripPrefs.consumption);
  const consumption = Number(raw);
  const validConsumption = raw.trim() !== '' && Number.isFinite(consumption) && consumption > 0;

  const [lng, lat] = detailFeature.geometry.coordinates;
  const oneWayKm = haversineDistance(MONTREAL_CENTER[0], MONTREAL_CENTER[1], lng, lat);
  const distanceKm = oneWayKm * (tripPrefs.roundTrip ? 2 : 1);
  const priceCents = detailFeature.properties[detailFuel + '_price'];

  // cost = (distanceKm / 100) * consumption * (priceCents / 100)  → CAD
  const cost = validConsumption && priceCents != null
    ? (distanceKm / 100) * consumption * (priceCents / 100)
    : null;

  return { distanceKm, cost };
}

function updateTripEstimatorResult() {
  if (!detailEl) return;
  const { distanceKm, cost } = tripEstimate();
  const costEl = detailEl.querySelector('.sd-trip-cost');
  const distEl = detailEl.querySelector('.sd-trip-distance-value');
  if (costEl) costEl.textContent = cost != null ? `$${cost.toFixed(2)}` : '—';
  if (distEl) distEl.textContent = distanceKm != null ? `${distanceKm.toFixed(1)} km` : '—';
}

function updateTripEstimator() {
  if (!detailEl) return;
  detailEl.querySelector('.sd-trip-head').textContent = t('tripEstimator');
  detailEl.querySelector('.sd-trip-label').textContent = t('fuelConsumption');
  detailEl.querySelector('.sd-trip-result-label').textContent = t('tripCost');
  detailEl.querySelector('.sd-trip-distance-label').textContent = t('tripDistance');

  const input = detailEl.querySelector('.sd-trip-input');
  input.value = String(tripPrefs.consumption);
  input.setAttribute('aria-label', t('fuelConsumption'));

  detailEl.querySelectorAll('.sd-trip-seg').forEach(btn => {
    const on = (btn.dataset.tripMode === 'roundtrip') === tripPrefs.roundTrip;
    btn.classList.toggle('on', on);
    btn.textContent = btn.dataset.tripMode === 'roundtrip' ? t('roundTrip') : t('oneWay');
  });

  updateTripEstimatorResult();
}

// ── small helper to format metadata time (kept local, no import cycle) ──
export function formatUpdated(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const lang = getLanguage();
  return d.toLocaleString(lang, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function detailChartOptions() {
  return {
    responsive: true,
    maintainAspectRatio: false,
    interaction: { intersect: false, mode: 'index' },
    plugins: {
      legend: { display: false },
      tooltip: {
        callbacks: {
          label: (c) => `${c.parsed.y != null ? fmt(c.parsed.y) : '—'}¢`
        }
      }
    },
    scales: {
      x: {
        grid: { display: false },
        ticks: { maxTicksLimit: 6, font: { size: 10, family: 'ui-monospace, SFMono-Regular, Menlo, monospace' } }
      },
      y: {
        position: 'right',
        grid: { color: '#f1f5f9' },
        ticks: { font: { size: 10 }, callback: v => v.toFixed(1) + '¢' }
      }
    }
  };
}

function updateDetailChart(series, color) {
  if (!detailChart) return;
  const canvas = detailEl?.querySelector('#station-chart');
  const empty = detailEl?.querySelector('#station-chart-empty');

  if (!series || !series.length) {
    // No real recorded history: show the empty-state copy, not an empty axis.
    if (canvas) canvas.style.display = 'none';
    if (empty) empty.hidden = false;
    detailChart.data.labels = [];
    detailChart.data.datasets[0].data = [];
    detailChart.update();
    return;
  }

  if (canvas) canvas.style.display = '';
  if (empty) empty.hidden = true;

  const daily = detailRange > 7;
  const labels = series.map(s => {
    const d = new Date(s.date);
    return daily
      ? d.toLocaleDateString(getLanguage(), { month: 'short', day: 'numeric' })
      : d.toLocaleString(getLanguage(), { month: 'short', day: 'numeric', hour: '2-digit' });
  });
  detailChart.data.labels = labels;
  detailChart.data.datasets[0].data = series.map(s => s.price);
  detailChart.data.datasets[0].borderColor = color;
  detailChart.data.datasets[0].backgroundColor = color + '22';
  // A short/single-point series should still be visible as points.
  detailChart.data.datasets[0].pointRadius = series.length < 7 ? 3 : 0;
  // dynamic y range padding
  const prices = series.map(s => s.price);
  const min = Math.min(...prices), max = Math.max(...prices), span = max - min || 1;
  detailChart.options.scales.y.min = min - span * 0.15;
  detailChart.options.scales.y.max = max + span * 0.15;
  detailChart.update();
}

// Close panel when map container click / Escape handled elsewhere; expose helpers
export function isStationPanelOpen() {
  return !!(detailEl && detailEl.classList.contains('open'));
}

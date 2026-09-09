// ── Station data card — stock-exchange style price history ──
// Inspired by Apple Stocks: big quote, change chip, sparkline, range switcher.
// Popup = compact card; side panel = expanded card (Chart.js area chart).

import { t, tf, translations, getLanguage } from './i18n.js';
import { getStationHistory } from './history.js';
import { loadChartJS } from './chartjs.js';

const FUEL_KEYS = ['regular', 'super', 'diesel'];
const RANGES = [
  { label: '1W', days: 7 },
  { label: '1M', days: 30 },
  { label: '3M', days: 90 },
  { label: '6M', days: 180 },
  { label: '1A', days: 365 }
];

let activePopup = null;        // mapboxgl Popup instance
let currentFeature = null;     // feature displayed in popup
let popupFuel = 'regular';
let popupRange = 7;
let popupUpdated = '';

// ── Small helpers ──
function fuelLabel(fuel) {
  const dict = translations[getLanguage()];
  return dict?.[fuel] || fuel;
}
function fmt(v) { return v.toFixed(1); }
function centsToDollar(cents) { return (cents / 100).toFixed(3); }

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

// ── Build compact card HTML for the Mapbox popup ──
function cardHTML(feature) {
  const props = feature.properties;
  const fuel = popupFuel;
  const price = props[fuel + '_price'];
  if (price == null) {
    // fall back to first available fuel
    for (const f of FUEL_KEYS) {
      if (props[f + '_price'] != null) { popupFuel = f; break; }
    }
  }

  const series = getStationHistory(feature, popupFuel, popupRange) || [];
  const color = trendColor(series);
  const { diff, pct, up, flat } = changeInfo(series);
  const priceVal = props[popupFuel + '_price'];
  const available = FUEL_KEYS.filter(f => props[f + '_price'] != null);

  const fuelPills = available.map(f => `
    <button class="sc-pill ${f === popupFuel ? 'on' : ''}" data-fuel="${f}">${fuelLabel(f)}</button>`).join('');

  const rangePills = RANGES.map(r => `
    <button class="sc-rangepill ${r.days === popupRange ? 'on' : ''}" data-days="${r.days}">${r.label}</button>`).join('');

  // mini stats
  const prices = series.map(s => s.price);
  const hi = prices.length ? Math.max(...prices) : null;
  const lo = prices.length ? Math.min(...prices) : null;

  const arrow = flat ? '' : (up ? '▲' : '▼');
  const sign = flat ? '' : (up ? '+' : '−');
  const badgeCls = flat ? 'flat' : up ? 'up' : 'down';
  const delta = flat ? '0,0' : `${sign}${fmt(Math.abs(diff))},${sign}${fmt(Math.abs(pct))}`;

  return `
  <div class="sc-card" data-sc-card>
    <div class="sc-top">
      <div class="sc-name">${escapeHtml(props.name || props.brand || '')}</div>
      <div class="sc-sub">${escapeHtml(props.brand || '')}${props.address ? ' · ' + escapeHtml(props.address) : ''}</div>
    </div>
    ${available.length > 1 ? `<div class="sc-pills">${fuelPills}</div>` : ''}
    <div class="sc-quote">
      <div class="sc-price">${priceVal != null ? fmt(priceVal) : '—'}<span class="sc-unit">¢</span></div>
      <div class="sc-change ${badgeCls}">
        <span class="sc-arrow">${arrow}</span>
        <span class="sc-delta">${delta.split(',')[0]}</span>
        <span class="sc-pct">(${delta.split(',')[1] || '0.0'}%)</span>
      </div>
    </div>
    <div class="sc-range">${rangePills}</div>
    ${prices.length ? `<div class="sc-chart">${sparklineSVG(series, color)}</div>` : `<div class="sc-empty">${t('noHistory')}</div>`}
    ${prices.length ? `
    <div class="sc-mini-stats">
      <div class="sc-mini"><span>${t('high')}</span><b>${fmt(hi)}</b></div>
      <div class="sc-mini"><span>${t('low')}</span><b>${fmt(lo)}</b></div>
      <div class="sc-mini"><span>≈ $/L</span><b>${centsToDollar(priceVal)}</b></div>
    </div>` : ''}
    <button class="sc-expand" data-expand>
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
      ${t('expand')}
    </button>
    ${popupUpdated ? `<div class="sc-updated">${popupUpdated}</div>` : ''}
  </div>`;
}

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ── Open the popup card for a station ──
export function showStationCard(feature, map, updatedText = '') {
  const props = feature.properties;
  popupUpdated = updatedText || '';
  // Respect the fuel chosen in the sidebar filter
  const checkedFuel = document.querySelector('.fuel-filter:checked');
  popupFuel = checkedFuel && props[checkedFuel.value + '_price'] != null ? checkedFuel.value
    : (props.regular_price != null ? 'regular' : FUEL_KEYS.find(f => props[f + '_price'] != null) || 'regular');
  popupRange = 7;
  currentFeature = feature;

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

function buildDetailPanel() {
  if (detailEl) return detailEl;
  detailEl = document.createElement('div');
  detailEl.id = 'station-panel';
  detailEl.innerHTML = `
    <div class="sd-head">
      <div>
        <div class="sd-name"></div>
        <div class="sd-sub"></div>
      </div>
      <button class="sd-close" aria-label="${t('close')}">✕</button>
    </div>
    <div class="sd-body">
      <div class="sd-pills"></div>
      <div class="sd-quote">
        <div class="sd-bigprice"></div>
        <div class="sd-change-row"></div>
      </div>
      <div class="sd-range"></div>
      <div class="sd-chart"><canvas id="station-chart"></canvas></div>
      <div class="sd-stats"></div>
      <div class="sd-actions">
        <button class="sd-nav"></button>
        <div class="sd-updated"></div>
      </div>
    </div>`;
  document.getElementById('map-container').appendChild(detailEl);

  detailEl.querySelector('.sd-close').addEventListener('click', closeStationDetail);

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

  const mapEl = document.getElementById('map-container');
  mapEl.classList.add('panel-open');
  return detailEl;
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
  renderDetail();
}

async function renderDetail() {
  if (!detailFeature) return;
  const props = detailFeature.properties;
  const available = FUEL_KEYS.filter(f => props[f + '_price'] != null);

  // Header
  detailEl.querySelector('.sd-name').textContent = props.name || props.brand || '';
  const region = props.region ? ' · ' + props.region : '';
  detailEl.querySelector('.sd-sub').textContent = `${props.brand || ''}${region}`;

  // Fuel pills
  detailEl.querySelector('.sd-pills').innerHTML = available.map(f => `
    <button class="sd-fuelpill ${f === detailFuel ? 'on' : ''}" data-fuel="${f}">${fuelLabel(f)}</button>`).join('');

  const series = getStationHistory(detailFeature, detailFuel, detailRange) || [];
  const priceVal = props[detailFuel + '_price'];
  const color = trendColor(series);
  const { diff, pct, up, flat } = changeInfo(series);

  // Big quote
  detailEl.querySelector('.sd-bigprice').innerHTML = priceVal != null
    ? `${fmt(priceVal)}<span class="sd-unit">¢/L</span>` : '—';

  const arrow = flat ? '' : up ? '▲' : '▼';
  const sign = flat ? '' : up ? '+' : '−';
  detailEl.querySelector('.sd-change-row').className = 'sd-change-row ' + (flat ? 'flat' : up ? 'up' : 'down');
  detailEl.querySelector('.sd-change-row').innerHTML = priceVal != null
    ? `<span class="sd-arrow">${arrow}</span> <b>${sign}${fmt(Math.abs(diff))}</b> <span class="sd-pct">${sign}${fmt(Math.abs(pct))}%</span> <span class="sd-hint">(${t('sinceStart')})</span>`
    : '';

  // Range pills
  detailEl.querySelector('.sd-range').innerHTML = RANGES.map(r => `
    <button class="sd-rangepill ${r.days === detailRange ? 'on' : ''}" data-days="${r.days}">${r.label}</button>`).join('');

  // Stats
  const prices = series.map(s => s.price);
  const lo = prices.length ? Math.min(...prices) : null;
  const hi = prices.length ? Math.max(...prices) : null;
  const avg = prices.length ? prices.reduce((a, b) => a + b, 0) / prices.length : null;
  const lastChange = series.length >= 2 ? series[series.length - 1].price - series[series.length - 2].price : 0;
  const lastCls = lastChange > 0 ? 'up' : lastChange < 0 ? 'down' : '';
  detailEl.querySelector('.sd-stats').innerHTML = `
    <div class="sd-stat"><span>${t('low')}</span><b>${lo != null ? fmt(lo) : '—'}</b></div>
    <div class="sd-stat"><span>${t('high')}</span><b>${hi != null ? fmt(hi) : '—'}</b></div>
    <div class="sd-stat"><span>${t('avg')}</span><b>${avg != null ? fmt(avg) : '—'}</b></div>
    <div class="sd-stat ${lastCls}"><span>${t('lastPrice')}</span><b>${lastChange > 0 ? '+' : lastChange < 0 ? '−' : ''}${fmt(Math.abs(lastChange))}</b></div>`;

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

// ── small helper to format metadata time (kept local, no import cycle) ──
export function formatUpdated(ts) {
  if (!ts) return '';
  const d = new Date(ts);
  const lang = getLanguage();
  return d.toLocaleString(lang, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function detailChartOptions() {
  const dict = translations[getLanguage()];
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
        ticks: { font: { size: 10 }, callback: v => v + '¢' }
      }
    }
  };
}

function updateDetailChart(series, color) {
  if (!detailChart || !series || !series.length) return;
  const labels = series.map(s => {
    const d = new Date(s.date + 'T12:00:00');
    return d.toLocaleDateString(getLanguage(), { month: 'short', day: 'numeric' });
  });
  detailChart.data.labels = labels;
  detailChart.data.datasets[0].data = series.map(s => s.price);
  detailChart.data.datasets[0].borderColor = color;
  detailChart.data.datasets[0].backgroundColor = color + '22';
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

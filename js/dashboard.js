// ── Temporal Dashboard Module ──
// Slide-over panel with Chart.js time-series for fuel price trends

import { t, tf, translations, getLanguage, onLanguageChange } from './i18n.js';

let chart = null;
let historyData = null;
let panelEl = null;
let panelOpen = false;
let chartJsLoaded = false;

// Current filter state
let filterRegion = 'overall';
let filterDays = 30;
let filterFuel = 'regular';

const fuelColors = { regular: '#16a34a', super: '#eab308', diesel: '#dc2626' };
const regionColors = ['#1a73e8', '#8b5cf6', '#ec4899', '#f59e0b', '#10b981', '#6366f1'];

// ── Load Chart.js dynamically on first use ──
function loadChartJS() {
  if (chartJsLoaded) return Promise.resolve();
  if (window.Chart) { chartJsLoaded = true; return Promise.resolve(); }
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js';
    script.onload = () => { chartJsLoaded = true; resolve(); };
    script.onerror = () => reject(new Error('Chart.js failed to load'));
    document.head.appendChild(script);
  });
}

// ── Fetch historical data ──
async function loadHistory() {
  if (historyData) return historyData;
  const resp = await fetch('data/history.json');
  historyData = await resp.json();
  return historyData;
}

// ── Build dashboard DOM ──
function createPanel() {
  if (panelEl) return panelEl;

  panelEl = document.createElement('div');
  panelEl.id = 'dashboard-panel';
  panelEl.innerHTML = `
    <div class="dashboard-header">
      <span class="dashboard-title">📊 ${t('trends')}</span>
      <button class="dashboard-close" id="dashboard-close" aria-label="${t('close')}">✕</button>
    </div>
    <div class="dashboard-body">
      <div class="dashboard-filters">
        <div class="dashboard-filter-row">
          <label class="dashboard-filter-label">${t('region')}</label>
          <div class="dashboard-chips" id="dashboard-region-chips"></div>
        </div>
        <div class="dashboard-filter-row">
          <label class="dashboard-filter-label">${t('fuel')}</label>
          <div class="dashboard-fuel-radios" id="dashboard-fuel-radios">
            <label class="dashboard-fuel-radio active"><input type="radio" name="dashboard-fuel" value="regular" checked><span>${t('regular')}</span></label>
            <label class="dashboard-fuel-radio"><input type="radio" name="dashboard-fuel" value="super"><span>${t('super')}</span></label>
            <label class="dashboard-fuel-radio"><input type="radio" name="dashboard-fuel" value="diesel"><span>${t('diesel')}</span></label>
          </div>
        </div>
        <div class="dashboard-filter-row">
          <label class="dashboard-filter-label"></label>
          <div class="dashboard-time-btns">
            <button class="dashboard-time-btn active" data-days="7">7J</button>
            <button class="dashboard-time-btn" data-days="30">30J</button>
            <button class="dashboard-time-btn" data-days="60">60J</button>
            <button class="dashboard-time-btn" data-days="90">90J</button>
          </div>
        </div>
      </div>
      <div class="dashboard-chart-wrap">
        <canvas id="dashboard-chart"></canvas>
      </div>
      <div class="dashboard-stats" id="dashboard-stats"></div>
    </div>
  `;

  // Events
  panelEl.querySelector('#dashboard-close').addEventListener('click', closePanel);
  panelEl.querySelectorAll('.dashboard-time-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      panelEl.querySelectorAll('.dashboard-time-btn').forEach(b => b.classList.remove('active'));
      btn.classList.add('active');
      filterDays = parseInt(btn.dataset.days);
      renderChart();
    });
  });
  panelEl.querySelectorAll('input[name="dashboard-fuel"]').forEach(rb => {
    rb.addEventListener('change', () => {
      filterFuel = rb.value;
      panelEl.querySelectorAll('.dashboard-fuel-radio').forEach(l => l.classList.remove('active'));
      rb.closest('.dashboard-fuel-radio').classList.add('active');
      renderChart();
    });
  });

  document.getElementById('map-container').appendChild(panelEl);
  return panelEl;
}

// ── Build region chips ──
function buildRegionChips(regions) {
  const container = document.getElementById('dashboard-region-chips');
  if (!container) return;
  container.innerHTML = '';

  const allChip = document.createElement('button');
  allChip.className = 'dashboard-chip active';
  allChip.textContent = t('allRegions');
  allChip.dataset.region = 'overall';
  allChip.addEventListener('click', () => {
    container.querySelectorAll('.dashboard-chip').forEach(c => c.classList.remove('active'));
    allChip.classList.add('active');
    filterRegion = 'overall';
    renderChart();
  });
  container.appendChild(allChip);

  regions.forEach((region, i) => {
    const chip = document.createElement('button');
    chip.className = 'dashboard-chip';
    chip.textContent = region;
    chip.dataset.region = region;
    chip.style.borderLeft = `3px solid ${regionColors[i % regionColors.length]}`;
    chip.addEventListener('click', () => {
      container.querySelectorAll('.dashboard-chip').forEach(c => c.classList.remove('active'));
      chip.classList.add('active');
      filterRegion = region;
      renderChart();
    });
    container.appendChild(chip);
  });
}

// ── Render chart ──
async function renderChart() {
  await loadChartJS();
  const history = await loadHistory();
  if (!history) return;

  const regionData = history.regions[filterRegion] || history.overall;
  const days = regionData.days.slice(-filterDays);
  const labels = days.map(d => {
    const date = new Date(d.date + 'T12:00:00');
    return date.toLocaleDateString(getLanguage(), { month: 'short', day: 'numeric' });
  });
  const avgPrices = days.map(d => d[filterFuel]?.avg ?? null);
  const minPrices = days.map(d => d[filterFuel]?.min ?? null);
  const maxPrices = days.map(d => d[filterFuel]?.max ?? null);

  const ctx = document.getElementById('dashboard-chart')?.getContext('2d');
  if (!ctx) return;

  if (chart) chart.destroy();

  const dict = translations[getLanguage()];
  const fuelLabel = dict?.[filterFuel] || filterFuel;
  const regionLabel = filterRegion === 'overall' ? t('allRegions') : filterRegion;

  chart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [
        {
          label: `${fuelLabel} — ${regionLabel}`,
          data: avgPrices,
          borderColor: fuelColors[filterFuel],
          backgroundColor: fuelColors[filterFuel] + '20',
          fill: true,
          tension: 0.3,
          pointRadius: filterDays <= 30 ? 3 : 0,
          pointHoverRadius: 5,
          borderWidth: 2,
        }
      ]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: { intersect: false, mode: 'index' },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.dataset.label}: ${ctx.parsed.y.toFixed(1)}¢`
          }
        }
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { maxTicksLimit: filterDays <= 7 ? 7 : filterDays <= 30 ? 12 : 15, font: { size: 11 } }
        },
        y: {
          grid: { color: '#f1f5f9' },
          ticks: { callback: v => v.toFixed(0) + '¢', font: { size: 11 } },
          min: Math.floor(Math.min(...avgPrices.filter(Boolean)) - 5),
          max: Math.ceil(Math.max(...avgPrices.filter(Boolean)) + 5)
        }
      }
    }
  });

  updateStats(avgPrices, days);
}

// ── Summary statistics ──
function updateStats(avgPrices, days) {
  const el = document.getElementById('dashboard-stats');
  if (!el) return;

  const valid = avgPrices.filter(p => p !== null);
  if (valid.length === 0) { el.innerHTML = ''; return; }

  const current = valid[valid.length - 1];
  const avg = valid.reduce((a, b) => a + b, 0) / valid.length;

  // Calculate changes
  const days7 = valid.slice(-Math.min(7, valid.length));
  const days30 = valid.slice(-Math.min(30, valid.length));
  const change7 = days7.length >= 2 ? days7[days7.length - 1] - days7[0] : 0;
  const change30 = days30.length >= 2 ? days30[days30.length - 1] - days30[0] : 0;

  const arrow = (v) => v > 0 ? '↑' : v < 0 ? '↓' : '→';
  const cls = (v) => v > 0 ? 'up' : v < 0 ? 'down' : '';

  el.innerHTML = `
    <div class="dashboard-stat-item">
      <span class="dashboard-stat-label">${t('minPrice')}</span>
      <span class="dashboard-stat-value">${current.toFixed(1)}¢</span>
    </div>
    <div class="dashboard-stat-item">
      <span class="dashboard-stat-label">${t('avg')}</span>
      <span class="dashboard-stat-value">${avg.toFixed(1)}¢</span>
    </div>
    <div class="dashboard-stat-item ${cls(change7)}">
      <span class="dashboard-stat-label">7J</span>
      <span class="dashboard-stat-value">${arrow(change7)} ${Math.abs(change7).toFixed(1)}¢</span>
    </div>
    <div class="dashboard-stat-item ${cls(change30)}">
      <span class="dashboard-stat-label">30J</span>
      <span class="dashboard-stat-value">${arrow(change30)} ${Math.abs(change30).toFixed(1)}¢</span>
    </div>
  `;
}

// ── Open/close panel ──
export async function openPanel() {
  const panel = createPanel();
  await loadHistory();
  const history = historyData;
  if (history && history.metadata) {
    buildRegionChips(history.metadata.regions);
  }
  panel.classList.add('open');
  panelOpen = true;
  document.getElementById('map-container').classList.add('dashboard-open');
  renderChart();
}

export function closePanel() {
  if (panelEl) panelEl.classList.remove('open');
  panelOpen = false;
  document.getElementById('map-container').classList.remove('dashboard-open');
}

export function togglePanel() {
  panelOpen ? closePanel() : openPanel();
}

// ── Re-translate dashboard labels on language change ──
onLanguageChange(() => {
  if (panelEl) {
    const title = panelEl.querySelector('.dashboard-title');
    if (title) title.innerHTML = `📊 ${t('trends')}`;
    // Update fuel radio labels
    const dict = translations[getLanguage()];
    panelEl.querySelectorAll('.dashboard-fuel-radio span').forEach(span => {
      const input = span.parentElement.querySelector('input');
      if (input && dict[input.value]) span.textContent = dict[input.value];
    });
    // Re-render chart with new locale
    if (panelOpen && chart) renderChart();
  }
});

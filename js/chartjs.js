// ── Shared Chart.js loader (loaded once, cached) ──
let chartJsLoaded = false;
let chartJsPromise = null;

export function loadChartJS() {
  if (window.Chart) { chartJsLoaded = true; return Promise.resolve(window.Chart); }
  if (chartJsPromise) return chartJsPromise;
  chartJsPromise = new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js';
    script.onload = () => { chartJsLoaded = true; resolve(window.Chart); };
    script.onerror = () => { chartJsPromise = null; reject(new Error('Chart.js failed to load')); };
    document.head.appendChild(script);
  });
  return chartJsPromise;
}

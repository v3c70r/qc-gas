// ── History data loader + per-station series synthesizer ──

let historyData = null;
let historyPromise = null;

const DAY_MS = 24 * 60 * 60 * 1000;

export async function loadHistoryData() {
  if (historyPromise) return historyPromise;
  historyPromise = fetch('data/history.json')
    .then(r => r.json())
    .then(d => { historyData = d; return d; })
    .catch(err => { historyPromise = null; throw err; });
  return historyPromise;
}

function tsOf(entry) {
  const t = new Date(entry.date).getTime();
  return Number.isNaN(t) ? 0 : t;
}

function regionPoints(region) {
  if (!historyData) return null;
  const src = (historyData.regions && historyData.regions[region]) || historyData.overall;
  if (!src) return null;
  const points = src.points || src.days || [];
  return points.slice().sort((a, b) => tsOf(a) - tsOf(b));
}

/**
 * Filter a list of intraday samples to the last `days` calendar days,
 * relative to the newest sample in the list (NOT the wall-clock "now").
 * This keeps the range buttons meaningful for synthetic historical data.
 */
export function filterByDays(points, days) {
  const list = points || [];
  if (!list.length) return [];
  const sorted = list.slice().sort((a, b) => tsOf(a) - tsOf(b));
  const end = tsOf(sorted[sorted.length - 1]);
  const cutoff = end - days * DAY_MS;
  return sorted.filter(p => tsOf(p) >= cutoff);
}

// Deterministic pseudo-noise per station (stable across calls)
function noiseFor(seed, i) {
  const x = Math.sin((seed + i) * 12.9898) * 43758.5453;
  return (x - Math.floor(x)) * 2 - 1; // [-1, 1]
}

/**
 * Build a plausible intraday price series for one station, anchored to its
 * REAL current price and shaped by its region's trend. Oldest → newest.
 * Returns [{ date, price }] (full timestamps) or null when unavailable.
 */
export function getStationHistory(feature, fuel = 'regular', days = 90) {
  if (!historyData) return null;
  const props = feature.properties;
  const key = fuel + '_price';
  const current = props[key];
  if (current == null) return null;

  const rd = regionPoints(props.region);
  if (!rd || rd.length === 0) return null;

  const last = rd[rd.length - 1];
  const avgNow = last && last[fuel] ? last[fuel].avg : null;
  // Station's deviation from its region average — kept constant over time
  const offset = avgNow != null ? current - avgNow : 0;

  const seed = (props.name || '').length * 3 + (props.brand || '').length * 7 + 11;
  let avail = filterByDays(rd, days);

  // When the requested range predates the available history, extrapolate a
  // flat-ish backwards tail so 6M / 1A ranges still show a full window.
  const intervalHours = historyData.metadata?.interval_hours || 6;
  const intervalMs = intervalHours * 60 * 60 * 1000;

  const out = [];

  if (avail.length > 0) {
    const end = tsOf(avail[avail.length - 1]);
    const cutoff = end - days * DAY_MS;
    const first = avail[0];
    const firstTs = tsOf(first);

    if (firstTs - cutoff > intervalMs) {
      const firstAvg = first[fuel] ? first[fuel].avg : current - offset;
      const extra = [];
      let t = firstTs - intervalMs;
      let i = 1;
      while (t >= cutoff) {
        const driftDays = (firstTs - t) / DAY_MS;
        const v = firstAvg - driftDays * 0.02 + offset + noiseFor(seed, -i) * 1.6;
        extra.push({ date: new Date(t).toISOString(), price: clamp(v) });
        t -= intervalMs;
        i++;
      }
      extra.reverse();
      out.push(...extra);
    }
  }

  avail.forEach((d, i) => {
    const avg = d[fuel] ? d[fuel].avg : null;
    if (avg == null) return;
    const v = avg + offset + noiseFor(seed, i) * 1.4;
    out.push({ date: d.date, price: clamp(v) });
  });

  return out;
}

function clamp(v) {
  return Math.round(Math.max(120, Math.min(320, v)) * 10) / 10;
}

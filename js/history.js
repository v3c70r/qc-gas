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

  // Keep intraday granularity for short windows; for anything longer than a
  // week, downsample to one point per calendar day (mean price).
  return days > 7 ? aggregateDaily(out) : out;
}

function clamp(v) {
  return Math.round(Math.max(120, Math.min(320, v)) * 10) / 10;
}

function round1(v) {
  return Math.round(v * 10) / 10;
}

/**
 * Calendar-day key for a history point. Prefers the date portion of an ISO
 * string so intraday samples stay grouped on their source day (avoids
 * timezone-boundary splits); falls back to local Date components for older
 * date-only formats.
 */
function dayKey(entry) {
  const s = String(entry?.date ?? entry);
  const m = String(s).match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (m) return `${m[1]}-${m[2]}-${m[3]}`;
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return null;
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function groupByDay(points) {
  const groups = new Map();
  for (const p of points || []) {
    const key = dayKey(p);
    if (!key) continue;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(p);
  }
  return groups;
}

/**
 * Downsample a station series ({ date, price }) to one point per calendar
 * day, using the arithmetic mean of that day's samples.
 */
export function aggregateDaily(points) {
  const groups = groupByDay(points || []);
  const out = [];
  for (const [key, samples] of groups) {
    const [y, m, d] = key.split('-').map(Number);
    const valid = samples.filter(s => s && Number.isFinite(s.price));
    if (!valid.length) continue;
    const price = valid.reduce((sum, s) => sum + s.price, 0) / valid.length;
    out.push({ date: new Date(y, m - 1, d, 12).toISOString(), price: round1(price) });
  }
  return out.sort((a, b) => tsOf(a) - tsOf(b));
}

/**
 * Downsample region trend points ({ date, regular/super/diesel: {avg,min,max} })
 * to one point per calendar day. The daily `avg` is the mean of intraday
 * averages; `min`/`max` keep the day's low/high extremes.
 */
export function aggregateRegionDaily(points) {
  const groups = groupByDay(points || []);
  const out = [];
  for (const [key, samples] of groups) {
    const [y, m, d] = key.split('-').map(Number);
    const entry = { date: new Date(y, m - 1, d, 12).toISOString() };
    for (const fuel of ['regular', 'super', 'diesel']) {
      const vals = samples.map(s => s?.[fuel]).filter(v => v && Number.isFinite(v.avg));
      if (!vals.length) continue;
      const avg = vals.reduce((sum, v) => sum + v.avg, 0) / vals.length;
      const min = Math.min(...vals.map(v => Number.isFinite(v.min) ? v.min : v.avg));
      const max = Math.max(...vals.map(v => Number.isFinite(v.max) ? v.max : v.avg));
      entry[fuel] = { avg: round1(avg), min: round1(min), max: round1(max) };
    }
    out.push(entry);
  }
  return out.sort((a, b) => tsOf(a) - tsOf(b));
}

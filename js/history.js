// ── History data loaders + region/station series helpers ──

let historyData = null;
let historyPromise = null;

let stationHistoryData = null;
let stationHistoryPromise = null;

const DAY_MS = 24 * 60 * 60 * 1000;
const FUEL_SHORT = { regular: 'g', super: 's', diesel: 'd' };

export async function loadHistoryData() {
  if (historyPromise) return historyPromise;
  historyPromise = fetch('data/history.json')
    .then(r => r.json())
    .then(d => { historyData = d; return d; })
    .catch(err => { historyPromise = null; throw err; });
  return historyPromise;
}

export async function loadStationHistoryData() {
  if (stationHistoryPromise) return stationHistoryPromise;
  stationHistoryPromise = fetch('data/history/station-history.json')
    .then(r => r.json())
    .then(d => { stationHistoryData = d; return d; })
    .catch(err => { stationHistoryPromise = null; throw err; });
  return stationHistoryPromise;
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
 * This keeps the range buttons meaningful for historical data.
 */
export function filterByDays(points, days) {
  const list = points || [];
  if (!list.length) return [];
  const sorted = list.slice().sort((a, b) => tsOf(a) - tsOf(b));
  const end = tsOf(sorted[sorted.length - 1]);
  const cutoff = end - days * DAY_MS;
  return sorted.filter(p => tsOf(p) >= cutoff);
}

/**
 * Stable station key derived from the feature coordinates. The recorder stores
 * no station id, so the frontend matches recorded station history back to the
 * currently rendered feature using the same 5-decimal coordinate key.
 */
function stationKey(feature) {
  const coords = feature?.geometry?.coordinates;
  if (!coords || coords.length < 2) return null;
  const lng = Number(coords[0]);
  const lat = Number(coords[1]);
  if (!Number.isFinite(lng) || !Number.isFinite(lat)) return null;
  return `${lng.toFixed(5)},${lat.toFixed(5)}`;
}

/**
 * Return the REAL recorded price history for one station, oldest → newest.
 * Returns [{ date, price }] when recorded data exists for the requested
 * window, or null when the station has no recorded history yet. No synthetic
 * data, no offsets, and no backwards extrapolation are applied.
 */
export function getStationHistory(feature, fuel = 'regular', days = 90) {
  if (!stationHistoryData || !feature) return null;
  const short = FUEL_SHORT[fuel];
  if (!short) return null;

  const key = stationKey(feature);
  const site = key ? stationHistoryData.s && stationHistoryData.s[key] : null;
  const values = site && site[short];
  const dates = stationHistoryData.t || [];
  if (!values || !dates.length) return null;

  const points = [];
  for (let i = 0; i < dates.length; i++) {
    const v = values[i];
    if (v == null || !Number.isFinite(v)) continue;
    points.push({ date: `${dates[i]}T12:00:00Z`, price: v });
  }
  if (!points.length) return null;

  const sliced = filterByDays(points, days);
  if (!sliced.length) return null;

  // The station store is recorded once per day. For windows longer than a
  // week we still run it through the daily aggregator so both region and
  // station series share the same day-key/mean behaviour.
  return days > 7 ? aggregateDaily(sliced) : sliced;
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

/**
 * Build one row of the region ranking table from the latest recorded sample.
 * The change is a day-over-day delta computed on the daily-aggregated series,
 * so it answers "vs yesterday" instead of comparing two 6h buckets on the
 * same day. Returns null values when data is missing so the UI can degrade.
 */
function regionRank(history, region, fuel) {
  const src = region === 'overall'
    ? history?.overall
    : (history?.regions && history.regions[region]);
  if (!src) return null;

  const points = (src.points || src.days || []).slice().sort((a, b) => tsOf(a) - tsOf(b));
  const valid = points.filter(p => p?.[fuel] && Number.isFinite(p[fuel].avg));
  const latest = valid[valid.length - 1];

  const avg = latest ? round1(latest[fuel].avg) : null;
  const min = latest && Number.isFinite(latest[fuel].min) ? round1(latest[fuel].min) : avg;
  const max = latest && Number.isFinite(latest[fuel].max) ? round1(latest[fuel].max) : avg;
  const spread = min != null && max != null ? round1(max - min) : null;

  const daily = aggregateRegionDaily(points);
  const dailyValid = daily.filter(d => d?.[fuel] && Number.isFinite(d[fuel].avg));
  let change = null;
  if (dailyValid.length >= 2) {
    change = round1(dailyValid[dailyValid.length - 1][fuel].avg - dailyValid[dailyValid.length - 2][fuel].avg);
  }

  return { region, avg, min, max, spread, change };
}

/**
 * Ranking rows for every known region plus the province-wide `overall` row.
 * Uses `history.metadata.regions` for ordering and tolerates older data
 * shapes that only expose `history.regions`.
 */
export function buildRegionRanking(history, fuel = 'regular') {
  const regions = history?.metadata?.regions || Object.keys(history?.regions || {});
  const rows = [];
  for (const region of regions) {
    const row = regionRank(history, region, fuel);
    if (row) rows.push(row);
  }
  const overall = regionRank(history, 'overall', fuel);
  if (overall) rows.push(overall);
  return rows;
}

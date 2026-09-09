// ── History data loader + per-station series synthesizer ──

let historyData = null;
let historyPromise = null;

export async function loadHistoryData() {
  if (historyPromise) return historyPromise;
  historyPromise = fetch('data/history.json')
    .then(r => r.json())
    .then(d => { historyData = d; return d; })
    .catch(err => { historyPromise = null; throw err; });
  return historyPromise;
}

function regionDays(region) {
  if (!historyData) return null;
  const src = (historyData.regions && historyData.regions[region]) || historyData.overall;
  return src ? src.days : null;
}

// Deterministic pseudo-noise per station (stable across calls)
function noiseFor(seed, i) {
  const x = Math.sin((seed + i) * 12.9898) * 43758.5453;
  return (x - Math.floor(x)) * 2 - 1; // [-1, 1]
}

/**
 * Build a plausible daily price series for one station, anchored to its
 * REAL current price and shaped by its region's trend. Oldest → newest.
 * Returns [{ date, price }] or null when unavailable.
 */
export function getStationHistory(feature, fuel = 'regular', days = 90) {
  if (!historyData) return null;
  const props = feature.properties;
  const key = fuel + '_price';
  const current = props[key];
  if (current == null) return null;

  const rd = regionDays(props.region);
  if (!rd || rd.length === 0) return null;

  const last = rd[rd.length - 1];
  const avgNow = last && last[fuel] ? last[fuel].avg : null;
  // Station's deviation from its region average — kept constant over time
  const offset = avgNow != null ? current - avgNow : 0;

  const seed = (props.name || '').length * 3 + (props.brand || '').length * 7 + 11;
  const out = [];

  const avail = rd.slice(-Math.min(days, rd.length));
  // If we need older points than regional data provides, extrapolate a flat-ish tail
  const missing = days - avail.length;

  if (missing > 0) {
    const oldestAvg = avail.length && avail[0][fuel] ? avail[0][fuel].avg : current - offset;
    for (let i = missing; i >= 1; i--) {
      const d = new Date(avail[0].date + 'T12:00:00');
      d.setDate(d.getDate() - i);
      const drift = (missing - i) * 0.04; // slight backward drift
      const v = oldestAvg - drift + offset + noiseFor(seed, -i) * 1.6;
      out.push({ date: d.toISOString().slice(0, 10), price: clamp(v) });
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

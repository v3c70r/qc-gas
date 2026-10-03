// ── Régie de l'énergie weekly retail margin ──
// Loads the parsed weekly Bulletin (data/regie-margin.json) and exposes the
// official, before-tax retail margin for a region + fuel. This is what turns a
// regional price difference into an explanation ("why is my region pricier")
// instead of just another number.
//
// Failure is silent on purpose: when the file is missing or malformed, every
// helper returns null and the UI simply omits the margin (never 0 / NaN).

import { normalizeText } from './search.js';

export const REGIE_DATA_URL = 'data/regie-margin.json';

let regieData = null;
let regiePromise = null;

function isUsable(doc) {
  return !!(
    doc &&
    doc.week &&
    doc.fuels &&
    typeof doc.fuels === 'object'
  );
}

export async function loadRegieData() {
  if (regiePromise) return regiePromise;
  regiePromise = fetch(REGIE_DATA_URL, { cache: 'no-store' })
    .then((response) => {
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      return response.json();
    })
    .then((doc) => {
      regieData = isUsable(doc) ? doc : null;
      return regieData;
    })
    .catch(() => {
      regieData = null;
      return null;
    });
  return regiePromise;
}

export function hasRegieData() {
  return !!regieData;
}

export function getRegieWeek() {
  return regieData?.week || null;
}

function findRegionEntry(fuel, region) {
  const regions = regieData?.fuels?.[fuel]?.regions;
  if (!regions || !region) return null;
  const target = normalizeText(region);
  if (!target) return null;
  for (const [name, entry] of Object.entries(regions)) {
    if (normalizeText(name) === target) return entry;
  }
  return null;
}

/**
 * Official Régie retail margin for a region + fuel, or null when unavailable.
 *
 * Region names are matched accent- and punctuation-insensitively (the Bulletin
 * writes "Gaspésie–Iles-de-la-Madeleine" with an en dash and no accent on
 * "Iles", while we store "Gaspésie-Îles-de-la-Madeleine"). The synthetic
 * "overall" row maps to the Bulletin's weighted Québec average. Regions absent
 * from the Bulletin ("Municipalités hors MRC \ CMM") return null.
 *
 * @returns {null | {prev_mean:number|null, mean:number|null, delta:number|null,
 *   margin:number|null, sub?:object, week:{start,end,published_at}}}
 */
export function getRegionMargin(region, fuel = 'regular') {
  if (!regieData || !region) return null;
  const entry = region === 'overall'
    ? regieData.fuels?.[fuel]?.quebec
    : findRegionEntry(fuel, region);
  if (!entry || entry.margin == null || !Number.isFinite(entry.margin)) return null;
  return { ...entry, week: regieData.week };
}

// Test/console hook (mirrors window.__qcGasBenchmark / window.__qcGasSearch).
if (typeof window !== 'undefined') {
  window.__qcGasRegie = {
    getRegionMargin,
    getRegieWeek,
    hasRegieData,
    loadRegieData,
  };
}

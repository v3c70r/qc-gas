#!/usr/bin/env node
// Generates data/history.json with intraday synthetic price samples.
//
// Each day contains SAMPLES_PER_DAY samples spaced 6h apart (4-6h interval),
// so charts can show morning/noon/evening movement within the same day.
// Data is deterministic: run this script any time to regenerate the same file.

import { writeFileSync, mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT = resolve(__dirname, '..', 'data', 'history.json');

const REGIONS = ['Montréal', 'Laval', 'Capitale-Nationale', 'Montérégie', 'Outaouais'];
const FUELS = ['regular', 'super', 'diesel'];

const DAYS = 90;
const SAMPLES_PER_DAY = 4;
const HOURS = [0, 6, 12, 18]; // local Quebec hours → 6h interval
const INTERVAL_HOURS = 6;
const TZ_OFFSET = '-04:00';

// 2026-03-09 → 2026-06-06 (Quebec DST is stable in this window)
const START_MS = new Date('2026-03-09T00:00:00-04:00').getTime();
const DAY_MS = 24 * 60 * 60 * 1000;

const FUEL_BASE = { regular: 170, super: 188, diesel: 180 };
const REGION_OFFSET = {
  Montréal: 0,
  Laval: 1.2,
  'Capitale-Nationale': 2.4,
  Montérégie: -1.1,
  Outaouais: -2.0
};

// ── Deterministic PRNG (mulberry32) ──
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hash(str) {
  let h = 2166136261;
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function round1(v) {
  return Math.round(v * 10) / 10;
}

function isoAt(dayIndex, hour) {
  const ms = START_MS + dayIndex * DAY_MS + hour * 60 * 60 * 1000;
  const d = new Date(ms);
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  const hh = String(hour).padStart(2, '0');
  return `${y}-${m}-${day}T${hh}:00:00${TZ_OFFSET}`;
}

function generateRegionPoints(region) {
  const rand = mulberry32(hash(region));
  const walk = {};
  for (const f of FUELS) walk[f] = FUEL_BASE[f] + REGION_OFFSET[region];

  const points = [];
  for (let d = 0; d < DAYS; d++) {
    // Gentle daily random walk with light mean reversion
    for (const f of FUELS) {
      walk[f] += (rand() - 0.5) * 1.7;
      const base = FUEL_BASE[f] + REGION_OFFSET[region];
      walk[f] += (base - walk[f]) * 0.06;
    }

    for (const hour of HOURS) {
      // Peak around noon, lower overnight
      const intraday = Math.cos(((hour - 12) / 24) * Math.PI * 2) * 1.4;
      const point = { date: isoAt(d, hour) };
      for (const f of FUELS) {
        const noise = (rand() - 0.5) * 0.8;
        const avg = round1(walk[f] + intraday + noise);
        const min = round1(avg - 0.6 - rand() * 0.9);
        const max = round1(avg + 0.6 + rand() * 0.9);
        point[f] = { avg, min, max };
      }
      points.push(point);
    }
  }
  return points;
}

function averagePoints(regionPointSets) {
  const out = [];
  const count = regionPointSets.length;
  for (let i = 0; i < regionPointSets[0].length; i++) {
    const sample = { date: regionPointSets[0][i].date };
    for (const f of FUELS) {
      const avgs = regionPointSets.map(pts => pts[i][f].avg);
      const mins = regionPointSets.map(pts => pts[i][f].min);
      const maxs = regionPointSets.map(pts => pts[i][f].max);
      sample[f] = {
        avg: round1(avgs.reduce((a, b) => a + b, 0) / count),
        min: round1(mins.reduce((a, b) => a + b, 0) / count),
        max: round1(maxs.reduce((a, b) => a + b, 0) / count)
      };
    }
    out.push(sample);
  }
  return out;
}

const regionPointSets = REGIONS.map(generateRegionPoints);
const overallPoints = averagePoints(regionPointSets);

const regions = {};
REGIONS.forEach((region, i) => {
  regions[region] = { points: regionPointSets[i] };
});

const latest = isoAt(DAYS - 1, HOURS[HOURS.length - 1]);

const output = {
  regions,
  overall: { points: overallPoints },
  metadata: {
    generated_at: latest,
    days: DAYS,
    interval_hours: INTERVAL_HOURS,
    samples_per_day: SAMPLES_PER_DAY,
    latest,
    regions: REGIONS
  }
};

mkdirSync(dirname(OUT), { recursive: true });
writeFileSync(OUT, JSON.stringify(output));
console.log(`Wrote ${OUT} (${DAYS} days × ${SAMPLES_PER_DAY} samples/day × ${REGIONS.length + 1} series)`);

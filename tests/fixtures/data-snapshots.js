// Derived-data bootstrap for the browser tests (issue #103).
//
// `data/stations.json` and `data/history.json` are **derived snapshots**: they
// are regenerated on every fetch and pushed to the site through the Pages
// artifact, so they are no longer committed to git. A fresh clone or `git
// worktree` therefore has neither, and the specs would fail twice over:
//
//  - `readFileSync('../data/stations.json')` throws at collection time, and the
//    `page.evaluate(() => fetch('data/stations.json'))` assertions hit a 404;
//  - `openDashboard()` cannot work at all: `js/dashboard.js` awaits
//    `loadHistoryData()` without a catch, so a missing `data/history.json`
//    throws before `#dashboard-panel` ever gets the `open` class (Agent C
//    functional test, #103 review round 2: 4 "Dashboard Region Ranking"
//    failures).
//
// When a file is missing we materialize a committed gzipped copy of a real
// snapshot (same bytes as production, so every spec keeps the real station set
// — "≤ 25 km around Montréal" needs > 200 stations, and the ranking tests need
// all 18 regions). Both paths are git-ignored, so this never dirties the
// working tree; the specs derive their expectations from whichever file was
// used. `stats.json` / `metrics.json` have no frontend consumer, so they are
// not restored.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const DATA_DIR = path.resolve(HERE, '..', '..', 'data');

// data/ file -> committed gzip of one real snapshot of it.
export const DERIVED_SAMPLES = {
  'stations.json': path.join(HERE, 'stations.sample.json.gz'),
  'history.json': path.join(HERE, 'history.sample.json.gz'),
};

/**
 * Materialize every missing derived snapshot the tests need and return the
 * `data/` directory they live in.
 */
export function ensureDerivedData() {
  mkdirSync(DATA_DIR, { recursive: true });
  for (const [name, sample] of Object.entries(DERIVED_SAMPLES)) {
    const target = path.join(DATA_DIR, name);
    if (existsSync(target)) continue;
    writeFileSync(target, gunzipSync(readFileSync(sample)));
    console.log(`[tests] data/${name} missing (derived snapshot, not in git since `
      + `#103): restored the committed sample ${path.basename(sample)}`);
  }
  return DATA_DIR;
}

/** Absolute path of one derived snapshot, restoring it first when missing. */
export function derivedDataPath(name) {
  ensureDerivedData();
  return path.join(DATA_DIR, name);
}

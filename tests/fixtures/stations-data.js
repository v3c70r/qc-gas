// Stations snapshot loader for the browser tests (issue #103).
//
// `data/stations.json` is a **derived snapshot**: it is regenerated on every
// fetch and pushed to the site through the Pages artifact, so it is no longer
// committed to git. A fresh clone therefore has no local copy, and the specs
// that read it from disk (and the `page.evaluate(() => fetch('data/stations.json'))`
// assertions, which go through the Vite dev server) would fail.
//
// When the real snapshot is missing we materialize a committed gzipped copy of
// it in `data/` (same file byte-for-byte, so every spec keeps the real station
// set: e.g. "≤ 25 km around Montréal" needs > 200 stations). `data/stations.json`
// is git-ignored, so this never dirties the working tree, and the specs derive
// their expectations from whichever file was used.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const DATA_DIR = path.resolve(HERE, '..', '..', 'data');
export const STATIONS_PATH = path.join(DATA_DIR, 'stations.json');
export const SAMPLE_GZ_PATH = path.join(HERE, 'stations.sample.json.gz');

/**
 * Path of the snapshot the tests should read, materializing the committed
 * sample into `data/` first when the real (derived) snapshot is absent.
 */
export function ensureStationsData() {
  if (!existsSync(STATIONS_PATH)) {
    mkdirSync(DATA_DIR, { recursive: true });
    writeFileSync(STATIONS_PATH, gunzipSync(readFileSync(SAMPLE_GZ_PATH)));
    console.log('[tests] data/stations.json missing (derived snapshot, not in '
      + 'git since #103): restored the committed sample '
      + `${path.basename(SAMPLE_GZ_PATH)} into data/`);
  }
  return STATIONS_PATH;
}

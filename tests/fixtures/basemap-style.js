// Committed snapshot of the real `mapbox://styles/mapbox/streets-v12` style
// (issue #125).
//
// Why a snapshot and not a live `getStyle()` call: every basemap assertion we
// care about — "is this streets and not light?", "does every layer the palette
// touches actually exist?", "what is `poi-label`'s minzoom?" — is a *style data*
// question. Answering it from the browser made the spec depend on real Mapbox
// traffic (style JSON + tiles + URL-restricted token), which turned a whole
// pipeline round `needs_human` when tiles got slow or rate-limited.
//
// `mapbox-streets-v12.json` is a trimmed capture of the live style: only the
// fields the assertions read (`id`, `type`, `source-layer`, `minzoom`,
// `visibility`), 134 layers, ~14 KB. Regenerate it after a Mapbox style update:
//
//   TOKEN=$(grep VITE_MAPBOX_ACCESS_TOKEN .env | cut -d= -f2)
//   curl -s "https://api.mapbox.com/styles/v1/mapbox/streets-v12?access_token=$TOKEN" \
//        -H 'Referer: https://qgu.io/qc-gas/' > /tmp/style.json
//   node scripts/refresh-map-style-fixture.mjs /tmp/style.json
//
// The live spec in `tests/map-style.spec.js` still asserts the *rendered* map,
// so a stale snapshot cannot silently mask a real style change.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));

/** Raw trimmed style: `{ name, layers: [{ id, type, 'source-layer'?, minzoom?, visibility? }] }`. */
export const STREETS_V12_STYLE = JSON.parse(
  readFileSync(path.join(HERE, 'mapbox-streets-v12.json'), 'utf8')
);

/** Layer ids of the snapped style, in paint order. */
export function styleLayerIds() {
  return STREETS_V12_STYLE.layers.map(layer => layer.id);
}

/** Find one snapped layer by id (`undefined` when the style has no such layer). */
export function styleLayer(id) {
  return STREETS_V12_STYLE.layers.find(layer => layer.id === id);
}

// Regenerate `tests/fixtures/mapbox-streets-v12.json` from a raw Mapbox style
// JSON (issue #125). The fixture is a *trimmed* capture — only the fields the
// Playwright assertions read — so a 90 KB style becomes a ~14 KB data file.
//
// Usage:
//   TOKEN=$(grep VITE_MAPBOX_ACCESS_TOKEN .env | cut -d= -f2)
//   curl -s "https://api.mapbox.com/styles/v1/mapbox/streets-v12?access_token=$TOKEN" \
//        -H 'Referer: https://qgu.io/qc-gas/' > /tmp/style.json
//   node scripts/refresh-map-style-fixture.mjs /tmp/style.json
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const KEEP_LAYER_FIELDS = ['source-layer', 'minzoom'];

const input = process.argv[2];
if (!input) {
  console.error('usage: node scripts/refresh-map-style-fixture.mjs <raw-style.json>');
  process.exit(2);
}

const style = JSON.parse(readFileSync(input, 'utf8'));
if (!Array.isArray(style.layers) || style.layers.length === 0) {
  console.error('not a Mapbox style: no layers array');
  process.exit(2);
}

const trimmed = {
  name: style.name,
  layers: style.layers.map(layer => {
    const out = { id: layer.id, type: layer.type };
    for (const field of KEEP_LAYER_FIELDS) {
      if (layer[field] !== undefined) out[field] = layer[field];
    }
    if (layer.layout?.visibility) out.visibility = layer.layout.visibility;
    return out;
  })
};

const outPath = join(
  dirname(fileURLToPath(import.meta.url)),
  '..',
  'tests',
  'fixtures',
  'mapbox-streets-v12.json'
);
writeFileSync(outPath, `${JSON.stringify(trimmed, null, 1)}\n`);
console.log(`wrote ${outPath} (${trimmed.layers.length} layers)`);

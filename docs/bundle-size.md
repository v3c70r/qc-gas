# Bundle size after the debug/dynamic-import polish (Issue #123)

Measured on `agent/123` with `npm run build` (vite 5.4.21, same machine, same
data files). "before" = `agent/123` at the commit preceding the change.

## Build output

| Asset | before (raw) | before (gzip) | after (raw) | after (gzip) | Δ gzip |
| --- | --- | --- | --- | --- | --- |
| `assets/index-*.js` | 123.11 kB | 39.82 kB | 119.54 kB | 39.16 kB | **−0.66 kB** |
| `assets/station-card-*.js` | 27.78 kB | 8.88 kB | 27.78 kB | 8.88 kB | ±0 |
| `assets/index-*.css` | 48.91 kB | 8.17 kB | 48.91 kB | 8.17 kB | ±0 |
| `index.html` | 15.95 kB | 4.08 kB | 15.95 kB | 4.08 kB | ±0 |

No asset grew. The entry chunk is slightly smaller: the three "lazy" imports
that could never split now resolve to direct bindings instead of an extra
`__vitePreload` wrapper + promise chain.

## Vite code-splitting warnings

`(!)` warnings in `npm run build`: **6 → 0** (the issue listed 3; the build also
flagged `map.js`, `history.js` and `fillups.js`, same root cause).

| Module | Dynamic importer(s) | Also statically imported by | Resolution |
| --- | --- | --- | --- |
| `js/favorites.js` | `databackup.js` | `station-card.js`, `stats.js`, `watch.js` | static |
| `js/stats.js` | `databackup.js`, `map.js`, `watch.js` | `app.js`, `filters.js`, `map.js`, `search.js`, `station-card.js` | static |
| `js/watch.js` | `databackup.js` | `app.js`, `station-card.js` | static |
| `js/fillups.js` | `databackup.js` | `app.js`, `station-card.js` | static |
| `js/history.js` | `stats.js` | `app.js`, `dashboard.js`, `fillups.js`, `station-card.js` | static |
| `js/map.js` | `favorites.js` | `app.js`, `filters.js`, `search.js`, `station-card.js`, `stats.js`, `watch.js` | static (see below) |

`js/station-card.js` stays a real lazy chunk (it is only ever dynamically
imported), so `station-card-*.js` is untouched.

### `favorites.js` → `map.js`

`favorites.js` used `import('./map.js')` to read the current snapshot without
creating an import cycle. It now imports `currentStations` statically and reads
the live binding on a `queueMicrotask` (the cycle `map → stats → favorites →
map` leaves the binding in its TDZ while `favorites.js` evaluates; a microtask
runs once the whole graph is evaluated). Same behaviour, no warning.

## Debug leftovers

`console.log` / `console.debug` in `js/*.js`: 3 → 0. Logging now goes through
`debugLog()` in `js/debug.js`, which is silent unless `localStorage.debug = '1'`
or the build is a dev build (`import.meta.env.DEV`). One init line remains
(`debugLog('Initializing Gas Price Quebec App...')`).

Both invariants are locked by `tests/source-hygiene.spec.js` (no bare
`console.log`/`console.debug` outside `js/debug.js`; no dynamic import of a
module that also has static importers).

#!/usr/bin/env node
// Measure basemap load timings (issue #124 — the "记录实测 style.load 耗时"
// acceptance item that #109 left open).
//
// Why this exists: #109 swapped `light-v11` (50 layers) for `streets-v12`
// (134 layers). "Does the map still come up fast?" must be answerable with one
// command, otherwise nobody can tell whether a future style/palette change made
// the first paint slower.
//
// Usage:
//   npm run measure:map-load                 # 3 runs per style, dev server booted if needed
//   npm run measure:map-load -- --runs 5     # more samples
//   BASE_URL=https://qgu.io/qc-gas/ npm run measure:map-load   # against a deployed build
//   npm run measure:map-load -- --json       # machine-readable
//
// What it measures (same clock, same page, same viewport for both styles):
//   `style.load` — from `new mapboxgl.Map({...})` to the `style.load` event
//                  (style JSON parsed, sources created, layers added);
//   `idle`       — the same start to the first `idle` event (all tiles settled),
//                  `null` when tiles never settle (see the note printed at the end).
//
// Two rows per run:
//   - injected: a second `mapboxgl.Map` created in an off-screen 1280×800 div on
//     the loaded app page. Used for the streets-v12 ↔ light-v11 comparison,
//     because both styles then go through *identical* conditions.
//   - app: the app's own map (`window.__qcBasemapStyleLoadMs`), i.e. what a
//     visitor actually gets.
//
// The number this script prints is what `STYLE_LOAD_BASELINE` in
// js/map-style.js records and what docs/map-style.md quotes; the Playwright
// guard in tests/map-style.spec.js enforces `STYLE_LOAD_BUDGET_MS` against the
// app row.
import { spawn } from 'node:child_process';
import { pathToFileURL } from 'node:url';
import { chromium } from '@playwright/test';

const DEV_URL = 'http://localhost:5173';

const STYLES = [
  { key: 'streets-v12', uri: 'mapbox://styles/mapbox/streets-v12', zoom: 10 },
  { key: 'light-v11', uri: 'mapbox://styles/mapbox/light-v11', zoom: 10 }
];

const IDLE_TIMEOUT_MS = 20000;
// The app's `idle` may legitimately never fire here (tiles 403 on localhost).
const APP_IDLE_TIMEOUT_MS = 5000;

// ── args ──

function parseArgs(argv) {
  const opts = { runs: 3, json: false, url: process.env.BASE_URL || DEV_URL };
  for (let i = 0; i < argv.length; i += 1) {
    const arg = argv[i];
    if (arg === '--json') opts.json = true;
    else if (arg === '--runs') opts.runs = Number(argv[++i]);
    else if (arg.startsWith('--runs=')) opts.runs = Number(arg.split('=')[1]);
    else if (arg === '--url') opts.url = argv[++i];
    else if (arg.startsWith('--url=')) opts.url = arg.split('=')[1];
    else if (arg === '--help' || arg === '-h') {
      console.log('usage: node scripts/measure_map_load.mjs [--runs N] [--url URL] [--json]');
      process.exit(0);
    } else {
      console.error(`unknown argument: ${arg}`);
      process.exit(2);
    }
  }
  if (!Number.isInteger(opts.runs) || opts.runs < 1) {
    console.error('--runs must be a positive integer');
    process.exit(2);
  }
  return opts;
}

// ── stats ──

const median = (values) => {
  const sorted = [...values].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[mid] : Math.round((sorted[mid - 1] + sorted[mid]) / 2);
};

export function summarise(values) {
  const clean = values.filter(v => typeof v === 'number' && Number.isFinite(v));
  if (clean.length === 0) return { median: null, range: null, samples: [] };
  return {
    median: median(clean),
    range: [Math.min(...clean), Math.max(...clean)],
    samples: clean
  };
}

// ── dev server ──

/**
 * `localhost` is not one address: Node's fetch only tries the first record, and
 * Vite binds to `::1` in some containers while resolving `localhost` to
 * `127.0.0.1` first. Try the obvious spellings and use whichever answers.
 */
export function urlVariants(url) {
  const base = new URL(url);
  if (base.hostname !== 'localhost') return [base.toString()];
  const variants = [base.toString()];
  const port = base.port ? `:${base.port}` : '';
  // `URL.hostname = '::1'` is silently ignored (needs the brackets), so build
  // the authority by hand.
  for (const host of ['[::1]', '127.0.0.1']) {
    const variant = new URL(url);
    variant.host = `${host}${port}`;
    variants.push(variant.toString());
  }
  return variants;
}

async function isReachable(url, timeoutMs = 2000) {
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    const res = await fetch(url, { signal: controller.signal });
    clearTimeout(timer);
    return res.ok || res.status === 404;
  } catch {
    return false;
  }
}

/** First reachable spelling of `url`, or `null`. */
async function resolveBaseUrl(url) {
  for (const candidate of urlVariants(url)) {
    if (await isReachable(candidate)) return candidate;
  }
  return null;
}

async function waitForReachable(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const resolved = await resolveBaseUrl(url);
    if (resolved) return resolved;
    await new Promise(r => setTimeout(r, 500));
  }
  return null;
}

/** True when no process is left in the process group led by `pid`. */
function processGroupGone(pid) {
  try {
    process.kill(-pid, 0);
    return false;
  } catch (err) {
    return err.code === 'ESRCH';
  }
}

async function waitForProcessGroupExit(pid, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (processGroupGone(pid)) return true;
    await new Promise(r => setTimeout(r, 50));
  }
  return processGroupGone(pid);
}

/**
 * Shut the dev server down *for real*.
 *
 * `child.kill()` only signals the `npm` process; the `vite` grandchild it
 * spawned is reparented to init and keeps holding port 5173. A stale server then
 * serves old code while the next `npm run dev` quietly moves to 5174 — i.e. the
 * next measurement points at an orphan (found in review round 1). The child is
 * therefore spawned as its own process *group* (`detached: true`) and the whole
 * group is signalled, with a SIGKILL escalation for a wedged server.
 */
export async function stopServer(child, { termTimeoutMs = 5000, killTimeoutMs = 2000 } = {}) {
  if (!child || typeof child.pid !== 'number') return;
  const pid = child.pid;
  // ESRCH here means the group is already gone (or was never its own group).
  if (processGroupGone(pid)) return;

  const signalGroup = (signal) => {
    try {
      process.kill(-pid, signal);
    } catch {
      /* group already empty */
    }
  };

  signalGroup('SIGTERM');
  if (await waitForProcessGroupExit(pid, termTimeoutMs)) return;

  signalGroup('SIGKILL');
  await waitForProcessGroupExit(pid, killTimeoutMs);
}

/** Boot `npm run dev` only when the base URL is not already serving. */
async function ensureServer(baseUrl) {
  const existing = await resolveBaseUrl(baseUrl);
  if (existing) return { child: null, url: existing, reuse: true };

  // `detached: true` puts npm *and* everything it spawns into one process group,
  // so `stopServer()` can reap the vite child too (see stopServer()).
  const child = spawn('npm', ['run', 'dev'], { stdio: 'ignore', detached: true });
  child.on('error', (err) => console.error(`dev server failed to start: ${err.message}`));

  const resolved = await waitForReachable(baseUrl);
  if (!resolved) {
    await stopServer(child);
    throw new Error(`dev server never became reachable at ${baseUrl}`);
  }
  return { child, url: resolved, reuse: false };
}

// ── measurement ──

/**
 * Time a freshly created map, off-screen, on an already loaded app page.
 * Same container size/viewport as the Playwright suites (1280×800).
 */
function measureInjectedMap(page, style) {
  return page.evaluate(async ({ styleUri, zoom, idleTimeout }) => {
    const container = document.createElement('div');
    container.id = 'perf-probe-map';
    container.style.cssText = 'position:fixed;left:-2000px;top:0;width:1280px;height:800px';
    document.body.appendChild(container);

    return await new Promise((resolve) => {
      const start = performance.now();
      let styleLoadMs = null;
      let idleMs = null;
      let finished = false;

      const finish = (timedOut) => {
        if (finished) return;
        finished = true;
        clearTimeout(idleTimer);
        try { map.remove(); } catch { /* map may already be gone */ }
        container.remove();
        resolve({ styleLoadMs, idleMs, idleTimedOut: timedOut });
      };

      const map = new mapboxgl.Map({
        container,
        style: styleUri,
        center: [-73.5673, 45.5017], // Montréal
        zoom,
        pitch: 0,
        bearing: 0,
        attributionControl: false
      });

      map.on('style.load', () => {
        if (styleLoadMs === null) styleLoadMs = Math.round(performance.now() - start);
      });

      map.on('idle', () => {
        if (idleMs === null) idleMs = Math.round(performance.now() - start);
        if (styleLoadMs !== null) finish(false);
      });

      // No style at all (network/token problem) would otherwise hang the run.
      const idleTimer = setTimeout(() => finish(true), idleTimeout);
    });
  }, { styleUri: style.uri, zoom: style.zoom, idleTimeout: IDLE_TIMEOUT_MS });
}

/**
 * The app's own map: what a real visitor gets. `style.load` is recorded by
 * js/map.js next to `new mapboxgl.Map()`, `idle` once the tiles settle — which
 * on localhost they may never do (URL-restricted token), hence the short poll
 * and the `null`.
 */
async function measureAppMap(page) {
  try {
    await page.waitForFunction(
      () => window.__qcGasMap && window.__qcGasMap.getBasemapStyleLoadMs() !== null,
      null,
      { timeout: IDLE_TIMEOUT_MS }
    );
  } catch {
    return { styleLoadMs: null, idleMs: null };
  }

  await page
    .waitForFunction(
      () => window.__qcBasemapIdleMs !== undefined && window.__qcBasemapIdleMs !== null,
      null,
      { timeout: APP_IDLE_TIMEOUT_MS }
    )
    .catch(() => {});

  return page.evaluate(() => ({
    styleLoadMs: window.__qcBasemapStyleLoadMs ?? null,
    idleMs: window.__qcBasemapIdleMs ?? null
  }));
}

/**
 * One sample = one fresh browsing context (cold caches) in which *both* styles
 * are probed on the same loaded page, plus the app's own numbers. Probing both
 * styles in the same page is what makes the comparison fair: same browser, same
 * token, same tile policy, same viewport.
 */
async function runOnce(browser, baseUrl) {
  const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
  const page = await context.newPage();
  try {
    await page.goto(baseUrl, { waitUntil: 'domcontentloaded' });
    await page.waitForFunction(
      () => Boolean(window.mapboxgl && window.mapboxgl.accessToken),
      null,
      { timeout: 20000 }
    );

    const app = await measureAppMap(page);
    const injected = {};
    for (const style of STYLES) {
      injected[style.key] = await measureInjectedMap(page, style);
    }
    return { injected, app };
  } finally {
    await context.close();
  }
}

// ── main ──

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const server = await ensureServer(opts.url);
  const baseUrl = server.url;
  const child = server.child;

  let browser;
  try {
    browser = await chromium.launch({ headless: true });

    const runs = [];
    for (let i = 0; i < opts.runs; i += 1) {
      const result = await runOnce(browser, baseUrl);
      runs.push(result);
      if (!opts.json) {
        const parts = STYLES.map((style) => {
          const probe = result.injected[style.key];
          return `${style.key} ${probe.styleLoadMs} ms / idle ${probe.idleMs ?? 'n/a'}`;
        });
        console.log(
          `run ${i + 1}/${opts.runs}: ${parts.join(' | ')} ` +
            `|| app ${result.app.styleLoadMs ?? 'n/a'} ms / idle ${result.app.idleMs ?? 'n/a'}`
        );
      }
    }

    const summary = {};
    for (const style of STYLES) {
      summary[style.key] = {
        injectedStyleLoad: summarise(runs.map(r => r.injected[style.key].styleLoadMs)),
        injectedIdle: summarise(runs.map(r => r.injected[style.key].idleMs))
      };
    }
    summary.app = {
      // The app always loads its real basemap (streets-v12 + palette), whatever
      // the probes above do — that is the number the Playwright guard enforces.
      injectedStyleLoad: summarise(runs.map(r => r.app.styleLoadMs)),
      injectedIdle: summarise(runs.map(r => r.app.idleMs))
    };

    const output = {
      url: baseUrl,
      runs: opts.runs,
      environment: {
        browser: `chromium ${browser.version()}`,
        viewport: '1280x800',
        node: process.version,
        platform: `${process.platform}/${process.arch}`
      },
      summary
    };

    if (opts.json) {
      console.log(JSON.stringify(output, null, 2));
    } else {
      console.log('');
      console.log(`median / range (ms) — ${opts.runs} runs, ${output.environment.browser}, 1280x800, ${baseUrl}`);
      console.log('probe                          style.load (median, range)   idle (median, range)');
      const cell = (v) => (v.median === null ? 'n/a' : `${v.median} (${v.range[0]}–${v.range[1]})`);
      for (const style of [...STYLES.map(s => s.key), 'app']) {
        const s = summary[style];
        const label = style === 'app' ? 'app map (streets-v12 + palette)' : `injected ${style}`;
        console.log(`${label.padEnd(30)} ${cell(s.injectedStyleLoad).padEnd(28)} ${cell(s.injectedIdle)}`);
      }
      console.log('');
      console.log('Note: the local token is URL-restricted to qgu.io, so vector tiles 403 on localhost —');
      console.log('      `style.load` is unaffected (style JSON is fine); idle is a lower bound at best.');
      console.log('      The injected probe finds the style JSON already warm in the same page, so compare');
      console.log('      streets-v12 with light-v11 (identical conditions), not with the app row.');
      console.log('      Baseline recorded in js/map-style.js (STYLE_LOAD_BASELINE) + docs/map-style.md.');
    }
  } finally {
    if (browser) await browser.close();
    // Whole process group, not just the npm PID — otherwise vite survives and
    // the next run measures a stale server (review round 1).
    await stopServer(child);
  }
}

// Only run when executed directly (`npm run measure:map-load`); importing the
// module from tests/measure-map-load.spec.js must not start a browser.
const invokedDirectly = process.argv[1]
  && pathToFileURL(process.argv[1]).href === import.meta.url;

if (invokedDirectly) {
  main().catch((err) => {
    console.error(`measure_map_load failed: ${err.message}`);
    process.exit(1);
  });
}

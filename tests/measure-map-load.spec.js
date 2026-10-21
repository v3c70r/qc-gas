// Regression tests for `scripts/measure_map_load.mjs` (issue #124, review round 1).
//
// The measuring script boots `npm run dev` when no server is listening and is
// supposed to shut it down again. It used to `child.kill('SIGTERM')` the npm PID
// only, so the *vite* grandchild survived the run, kept holding port 5173 and
// then served stale code to the next measurement (which silently pointed at the
// orphan while `npm run dev` moved on to 5174). Node-side tests: no browser, no
// Mapbox traffic, a few hundred milliseconds.
import { test, expect } from '@playwright/test';
import { spawn } from 'node:child_process';

import { stopServer, summarise, urlVariants } from '../scripts/measure_map_load.mjs';

/** True when no process is left in the process group led by `pid`. */
function groupIsGone(pid) {
  try {
    process.kill(-pid, 0);
    return false;
  } catch (err) {
    return err.code === 'ESRCH';
  }
}

/**
 * A stand-in for `npm run dev`: a parent that spawns a long-lived child in the
 * same process group. This is exactly the shape that used to leak.
 */
function spawnFakeNpm() {
  const parentScript = `
    const { spawn } = require('node:child_process');
    spawn(process.execPath, ['-e', 'setInterval(() => {}, 1000)'], { stdio: 'ignore' });
    setInterval(() => {}, 1000);
  `;
  return spawn(process.execPath, ['-e', parentScript], { stdio: 'ignore', detached: true });
}

test.describe('measure_map_load helpers', () => {
  test('stopServer kills the whole process group (npm → vite grandchild), not just the parent', async () => {
    const child = spawnFakeNpm();
    const pid = child.pid;

    // Wait for the grandchild to exist so the test cannot pass trivially.
    await new Promise(resolve => setTimeout(resolve, 400));
    expect(groupIsGone(pid), 'fake dev server should be running before the kill').toBe(false);

    await stopServer(child);

    expect(groupIsGone(pid), `process group ${pid} still has live processes after stopServer()`).toBe(true);
  });

  test('stopServer is a no-op when the script did not start a server', async () => {
    await expect(stopServer(null)).resolves.toBeUndefined();

    const child = spawnFakeNpm();
    const pid = child.pid;
    await new Promise(resolve => setTimeout(resolve, 300));
    await stopServer(child);
    // A second call on an already reaped child must not throw either.
    await expect(stopServer(child)).resolves.toBeUndefined();
    expect(groupIsGone(pid)).toBe(true);
  });

  test('urlVariants enumerates the localhost spellings the container needs', () => {
    const variants = urlVariants('http://localhost:5173/');
    expect(variants[0]).toBe('http://localhost:5173/');
    // Vite may bind `::1` only, and Node's fetch does not fall back on its own.
    expect(variants).toContain('http://[::1]:5173/');
    expect(variants).toContain('http://127.0.0.1:5173/');

    // Non-localhost URLs are used verbatim (BASE_URL=https://qgu.io/...).
    expect(urlVariants('https://qgu.io/qc-gas/')).toEqual(['https://qgu.io/qc-gas/']);
  });

  test('summarise reports median and range of the finite samples', () => {
    expect(summarise([514, 512, 512, 506, 519])).toEqual({
      median: 512,
      range: [506, 519],
      samples: [514, 512, 512, 506, 519]
    });
    // Even counts average the two middle values; nulls (idle never fired) drop out
    // before the median is computed.
    expect(summarise([10, 20, 40, 60]).median).toBe(30);
    expect(summarise([10, 20, null, 40]).samples).toEqual([10, 20, 40]);
    expect(summarise([10, 20, null, 40]).median).toBe(20);
    expect(summarise([null, null])).toEqual({ median: null, range: null, samples: [] });
  });
});

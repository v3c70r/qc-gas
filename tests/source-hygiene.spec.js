// ── Source hygiene (Issue #123) ──
// Two regressions that only show up in the sources / the build output:
//   1. debug leftovers (`console.log` / `console.debug`) on the production path;
//   2. "dynamic import will not move module into another chunk" warnings, i.e.
//      a `import('./x.js')` for a module that is also statically imported, which
//      never produces a chunk and only makes the code harder to read.
// Both are checked here without a browser and without running a build.

import { test, expect } from '@playwright/test';
import { readFileSync, readdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const jsDir = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'js');
const files = readdirSync(jsDir).filter((name) => name.endsWith('.js'));

// js/debug.js owns the single, opt-in logging switch (`debugLog()`); every
// other module must go through it instead of calling the console directly.
const DEBUG_MODULE = 'debug.js';

// `import x from './mod.js'`, `import './mod.js'`, `export … from './mod.js'`
const STATIC_IMPORT = /(?:^|\s)(?:import|export)\s+(?:[^'"]*?\sfrom\s+)?['"]\.\/([\w-]+)\.js['"]/g;
const DYNAMIC_IMPORT = /\bimport\s*\(\s*['"]\.\/([\w-]+)\.js['"]\s*\)/g;

function staticDeps(source) {
  return new Set([...source.matchAll(STATIC_IMPORT)].map((m) => m[1]));
}

function dynamicDeps(source) {
  return [...source.matchAll(DYNAMIC_IMPORT)].map((m) => m[1]);
}

test.describe('Source hygiene (Issue #123)', () => {
  test('sanity: the js/ directory is actually scanned', () => {
    expect(files.length).toBeGreaterThan(10);
  });

  test('no console.log/console.debug outside the debug switch', () => {
    const leftover = /console\.(?:log|debug)\s*\(/;
    const offenders = [];
    for (const file of files) {
      if (file === DEBUG_MODULE) continue;
      const lines = readFileSync(path.join(jsDir, file), 'utf8').split('\n');
      lines.forEach((line, i) => {
        if (leftover.test(line)) offenders.push(`${file}:${i + 1}: ${line.trim()}`);
      });
    }
    expect(offenders).toEqual([]);
  });

  test('the debug switch exists and stays opt-in', () => {
    const source = readFileSync(path.join(jsDir, DEBUG_MODULE), 'utf8');
    expect(source).toMatch(/export function debugLog\b/);
    // Production stays silent unless the developer opts in.
    expect(source).toMatch(/import\.meta\.env\.DEV|localStorage\.debug|localStorage\.getItem\('debug'\)/);
  });

  test('no dynamic import of a module that is also statically imported', () => {
    // A module that has static importers is already in a chunk: lazily
    // importing it moves nothing and makes Rollup warn on every build.
    const offenders = [];
    for (const file of files) {
      const source = readFileSync(path.join(jsDir, file), 'utf8');
      const dynamic = new Set(dynamicDeps(source));
      if (!dynamic.size) continue;
      for (const target of dynamic) {
        const staticImporters = files.filter((other) => {
          if (other === file) return false;
          return staticDeps(readFileSync(path.join(jsDir, other), 'utf8')).has(target);
        });
        // Same-file static import counts too.
        if (staticImporters.length || staticDeps(source).has(target)) {
          offenders.push(`${file} → ${target}.js (statically imported by: ${[file, ...staticImporters].filter((f) => staticDeps(readFileSync(path.join(jsDir, f), 'utf8')).has(target)).join(', ')})`);
        }
      }
    }
    expect(offenders).toEqual([]);
  });
});

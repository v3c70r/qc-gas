// ── Single, opt-in debug logging switch ──
// Production stays silent: a message is only printed when the developer opts in
// with `localStorage.debug = '1'` in the console, or automatically in a dev
// build (`import.meta.env.DEV`). Import `debugLog()` instead of writing to the
// console directly so the production path stays silent.

function resolveEnabled() {
  try {
    if (import.meta.env?.DEV) return true;
    if (typeof localStorage === 'undefined') return false;
    return localStorage.getItem('debug') === '1';
  } catch {
    return false; // localStorage can throw (Safari private mode / ITP).
  }
}

const enabled = resolveEnabled();

export function debugLog(...args) {
  if (!enabled) return;
  console.log('[qc-gas]', ...args);
}

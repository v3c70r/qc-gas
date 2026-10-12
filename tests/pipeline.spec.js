// Regression tests for the agent pipeline's state semantics + watcher lock
// (issue #75). These are pure-node tests: no browser page needed, but they run
// under `npm test` (Playwright's runner) so the acceptance criterion is real.
import { test, expect } from '@playwright/test';
import { mkdtempSync, writeFileSync, existsSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  clearBlockedState,
  isPidAlive,
  readLockPid,
  acquireWatchLock,
  releaseWatchLock,
} from '../.agent/lib.mjs';

const deadKill = () => {
  const e = new Error('no such process');
  e.code = 'ESRCH';
  throw e;
};

function freshDir() {
  return mkdtempSync(path.join(tmpdir(), 'pipeline-lock-'));
}

test.describe('dependency unblock state semantics (#75)', () => {
  test('unblock deletes the blocked entry instead of writing pr_open', () => {
    const before = { 76: { status: 'blocked', blockedBy: [74], blockedNotified: true } };
    const { changed, state } = clearBlockedState(before, 76);

    expect(changed).toBe(true);
    expect(Object.prototype.hasOwnProperty.call(state, '76')).toBe(false);
    // the old behaviour was the lie this issue fixes: a PR that does not exist
    expect(state[76]).toBeUndefined();
  });

  test('unblock keeps other issues untouched', () => {
    const before = { 5: { status: 'merged' }, 76: { status: 'blocked', blockedBy: [74] } };
    const { state } = clearBlockedState(before, 76);

    expect(state[5]).toEqual({ status: 'merged' });
    expect(state[76]).toBeUndefined();
  });

  test('non-blocked entries are left alone (no spurious deletion)', () => {
    const before = { 7: { status: 'pr_open', pr: 12, branch: 'agent/7' } };
    const { changed, state } = clearBlockedState(before, 7);

    expect(changed).toBe(false);
    expect(state[7]).toEqual({ status: 'pr_open', pr: 12, branch: 'agent/7' });
  });
});

test.describe('watcher single-instance lock (#75)', () => {
  test('acquire refuses when a live process already holds the lock', () => {
    const dir = freshDir();
    const file = path.join(dir, 'watch.lock');
    writeFileSync(file, `${process.pid}\n`);

    const res = acquireWatchLock(file, { pid: 424242, kill: process.kill });

    expect(res.acquired).toBe(false);
    expect(res.pid).toBe(process.pid);
    // lock file must NOT be clobbered by the losing instance
    expect(readLockPid(file)).toBe(process.pid);
  });

  test('acquire takes over a stale lock whose PID is dead', () => {
    const dir = freshDir();
    const file = path.join(dir, 'watch.lock');
    writeFileSync(file, '999999\n');

    const res = acquireWatchLock(file, { pid: process.pid, kill: deadKill });

    expect(res.acquired).toBe(true);
    expect(res.tookOver).toBe(true);
    expect(readLockPid(file)).toBe(process.pid);
  });

  test('acquire succeeds when no lock file exists', () => {
    const dir = freshDir();
    const file = path.join(dir, 'watch.lock');

    const res = acquireWatchLock(file, { pid: process.pid, kill: process.kill });

    expect(res.acquired).toBe(true);
    expect(res.tookOver).toBe(false);
    expect(existsSync(file)).toBe(true);
    expect(readFileSync(file, 'utf8').trim()).toBe(String(process.pid));
  });

  test('release removes our own lock but not someone else’s', () => {
    const dir = freshDir();
    const file = path.join(dir, 'watch.lock');

    writeFileSync(file, `${process.pid}\n`);
    expect(releaseWatchLock(file, { pid: process.pid })).toBe(true);
    expect(existsSync(file)).toBe(false);

    writeFileSync(file, '12345\n');
    expect(releaseWatchLock(file, { pid: process.pid })).toBe(false);
    expect(existsSync(file)).toBe(true);
  });

  test('isPidAlive reports live/current PID and treats ESRCH as dead', () => {
    expect(isPidAlive(process.pid, process.kill)).toBe(true);
    expect(isPidAlive(999999, deadKill)).toBe(false);
    expect(isPidAlive('not-a-pid', process.kill)).toBe(false);
  });

  test('garbage lock content is treated as stale, not fatal', () => {
    const dir = freshDir();
    const file = path.join(dir, 'watch.lock');
    writeFileSync(file, '<<not a pid>>\n');

    expect(readLockPid(file)).toBe(null);
    const res = acquireWatchLock(file, { pid: process.pid, kill: process.kill });
    expect(res.acquired).toBe(true);
    expect(res.tookOver).toBe(false);
  });
});

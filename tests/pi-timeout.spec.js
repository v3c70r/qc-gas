// Regression tests for the pi supervisor (issue #104).
//
// Real symptom: `runPi()` used `execFileSync(..., { timeout: 0 })`, i.e. never
// timed out. A hung model API (half-open TCP, server never answers) froze an
// entire pass forever: no exception → no needs_human, no retry, and from the
// outside the process looked healthy (CPU time frozen, sockets open).
//
// These tests inject a deliberately hanging fake `pi` (`sleep 9999`) through
// PI_BIN and pin the behaviour: explicit timeout failure, needs_human state,
// and heartbeat lines that make "waiting on a long test" vs "waiting on a dead
// API" distinguishable.
import { test, expect } from '@playwright/test';
import { chmodSync, cpSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { execFileSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  DEFAULT_PI_HEARTBEAT_MS,
  DEFAULT_PI_TIMEOUT_MS,
  PiTimeoutError,
  formatPiHeartbeat,
  markPiTimeout,
  piTimeoutLogLine,
  piTimeoutNote,
  resolvePi,
  resolvePiHeartbeatMs,
  resolvePiTimeoutMs,
  runPiWithTimeout,
} from '../.agent/lib.mjs';

/** Write an executable fake `pi` with the given bash body. */
function fakePi(body) {
  const dir = mkdtempSync(path.join(tmpdir(), 'fake-pi-'));
  const file = path.join(dir, 'pi');
  writeFileSync(file, `#!/usr/bin/env bash\n${body}\n`);
  chmodSync(file, 0o755);
  return file;
}

const HANGING = 'sleep 9999';

test.describe('pi timeout config (#104)', () => {
  test('default timeout is in the requested 45–60 min range, heartbeat default 60s', () => {
    expect(DEFAULT_PI_TIMEOUT_MS).toBeGreaterThanOrEqual(45 * 60_000);
    expect(DEFAULT_PI_TIMEOUT_MS).toBeLessThanOrEqual(60 * 60_000);
    expect(DEFAULT_PI_HEARTBEAT_MS).toBe(60_000);

    expect(resolvePiTimeoutMs('impl', {})).toBe(DEFAULT_PI_TIMEOUT_MS);
    expect(resolvePiHeartbeatMs({})).toBe(DEFAULT_PI_HEARTBEAT_MS);
  });

  test('AGENT_PI_TIMEOUT_MS overrides globally, per-phase var wins', () => {
    expect(resolvePiTimeoutMs('impl', { AGENT_PI_TIMEOUT_MS: '5000' })).toBe(5000);
    expect(resolvePiTimeoutMs('review', { AGENT_PI_TIMEOUT_MS: '5000', AGENT_PI_TIMEOUT_REVIEW_MS: '7000' })).toBe(7000);
    expect(resolvePiTimeoutMs('impl', { AGENT_PI_TIMEOUT_MS: '5000', AGENT_PI_TIMEOUT_REVIEW_MS: '7000' })).toBe(5000);
    // 0 = explicitly disabled (documented), garbage falls back to the default
    expect(resolvePiTimeoutMs('impl', { AGENT_PI_TIMEOUT_MS: '0' })).toBe(0);
    expect(resolvePiTimeoutMs('impl', { AGENT_PI_TIMEOUT_MS: 'soon' })).toBe(DEFAULT_PI_TIMEOUT_MS);
  });

  test('AGENT_PI_HEARTBEAT_MS is configurable', () => {
    expect(resolvePiHeartbeatMs({ AGENT_PI_HEARTBEAT_MS: '1000' })).toBe(1000);
    expect(resolvePiHeartbeatMs({ AGENT_PI_HEARTBEAT_MS: '0' })).toBe(0);
    expect(resolvePiHeartbeatMs({ AGENT_PI_HEARTBEAT_MS: 'nope' })).toBe(DEFAULT_PI_HEARTBEAT_MS);
  });
});

test.describe('pi supervisor kills a hanging process (#104)', () => {
  test('a hanging fake pi fails within the timeout instead of blocking forever', async () => {
    const bin = fakePi(HANGING);
    expect(resolvePi({ PI_BIN: bin, PATH: '' })).toBe(bin);

    const started = Date.now();
    const err = await runPiWithTimeout({ bin, args: [], timeoutMs: 500, heartbeatMs: 0 })
      .then(() => null, e => e);
    const elapsed = Date.now() - started;

    expect(err).toBeInstanceOf(PiTimeoutError);
    expect(err.message).toMatch(/pi 超时（\d+ (分钟|秒)）/);
    expect(err.timeoutMs).toBe(500);
    // must NOT hang until `sleep 9999` finishes
    expect(elapsed).toBeLessThan(5000);
  });

  test('heartbeat reports elapsed time AND time since last output', async () => {
    const bin = fakePi('echo starting; sleep 9999');
    const beats = [];

    await runPiWithTimeout({
      bin,
      args: [],
      timeoutMs: 700,
      heartbeatMs: 80,
      onHeartbeat: hb => beats.push(hb),
    }).catch(() => {});

    expect(beats.length).toBeGreaterThan(0);
    for (const hb of beats) {
      expect(hb.elapsedMs).toBeGreaterThan(0);
      expect(hb.sinceLastOutputMs).toBeGreaterThanOrEqual(0);
    }
    const line = formatPiHeartbeat(beats[beats.length - 1]);
    expect(line).toMatch(/^pi 心跳：已运行 \d+ (分钟|秒)，最近输出 \d+ (分钟|秒)前$/);
    // output arrived first, then the process went silent → heartbeat must see it
    expect(Math.max(...beats.map(h => h.sinceLastOutputMs))).toBeGreaterThan(0);
  });

  test('normal path semantics unchanged: stdout returned even on nonzero exit', async () => {
    const bin = fakePi('echo some output; exit 3');
    const res = await runPiWithTimeout({ bin, args: [], timeoutMs: 10_000, heartbeatMs: 0 });

    expect(res.code).toBe(3);
    expect(res.stdout).toContain('some output');
  });

  test('success path resolves with stdout', async () => {
    const bin = fakePi('echo hello');
    const res = await runPiWithTimeout({ bin, args: [], timeoutMs: 10_000, heartbeatMs: 0 });

    expect(res.code).toBe(0);
    expect(res.stdout.trim()).toBe('hello');
  });
});

test.describe('timeout consequences: needs_human + audit trail (#104)', () => {
  test('markPiTimeout flips the issue to needs_human but keeps prior fields', () => {
    const before = { 104: { status: 'reviewing', pr: 12, branch: 'agent/104', round: 1 } };
    const next = markPiTimeout(before, 104, new PiTimeoutError(5000));

    expect(next[104].status).toBe('needs_human');
    expect(next[104].pr).toBe(12);
    expect(next[104].branch).toBe('agent/104');
    expect(next[104].round).toBe(1);
    expect(next[104].error).toMatch(/pi 超时/);
    expect(typeof next[104].timedOutAt).toBe('string');
    // original state object is not mutated
    expect(before[104].status).toBe('reviewing');
  });

  test('log line states the timeout and the needs_human transition', () => {
    const line = piTimeoutLogLine(5000);
    expect(line).toMatch(/pi 超时（\d+ (分钟|秒)）/);
    expect(line).toContain('needs_human');
  });

  test('issue comment says it may be a hung model API and how to recover', () => {
    const note = piTimeoutNote(50 * 60_000);
    expect(note).toMatch(/pi 超时（50 分钟）/);
    expect(note).toContain('模型 API 挂起');
    expect(note).toContain('/approve');
    expect(note).toContain('人工检查');
    expect(note).toContain('心跳');
  });
});

// ── end-to-end: a real `pipeline.mjs run` pass must FAIL on a hung pi ──
// Acceptance criterion of issue #104: with a fake `pi` that just sleeps, the
// pass must end within AGENT_PI_TIMEOUT_MS (5s here), mark the issue
// needs_human, and write the timeout explanation into the issue log.
// `gh` is stubbed so the test needs neither network nor credentials.
test.describe('a whole pass dies of the timeout (#104)', () => {
  test('pass ends in needs_human with a timeout log line', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'pipeline-timeout-'));
    const bare = path.join(dir, 'origin.git');
    const clone = path.join(dir, 'repo');
    const stubBin = path.join(dir, 'bin');
    const ghCalls = path.join(dir, 'gh-calls.log');
    mkdirSync(stubBin);

    execFileSync('git', ['init', '--bare', '-q', bare], { stdio: 'pipe' });
    execFileSync('git', ['clone', '-q', bare, clone], { stdio: 'pipe' });
    const git = (...args) => execFileSync('git', args, { cwd: clone, stdio: 'pipe' });
    git('config', 'user.email', 'test@example.com');
    git('config', 'user.name', 'Test');
    writeFileSync(path.join(clone, 'README.md'), 'seed\n');
    git('add', 'README.md');
    git('commit', '-q', '-m', 'seed');
    git('branch', '-M', 'master');
    git('push', '-q', '-u', 'origin', 'master');

    // copy the pipeline + prompts into the throw-away repo
    cpSync(path.join(process.cwd(), '.agent'), path.join(clone, '.agent'), {
      recursive: true,
      filter: src => !src.includes(`${path.sep}logs`) && !src.includes(`${path.sep}tmp`),
    });

    // gh stub: issue view → minimal issue JSON, comments → recorded, rest → empty
    writeFileSync(path.join(stubBin, 'gh'), [
      '#!/usr/bin/env bash',
      `printf '%s\\n' "$*" >> '${ghCalls}'`,
      'if [ "$1" = "issue" ] && [ "$2" = "view" ]; then',
      '  echo \'{"number":104,"title":"hung pi","body":"Depends on: none","labels":[]}\'',
      'fi',
      'exit 0',
    ].join('\n') + '\n');
    chmodSync(path.join(stubBin, 'gh'), 0o755);
    const hangPi = fakePi(HANGING);

    const started = Date.now();
    const res = spawnSync(process.execPath, ['.agent/pipeline.mjs', 'run', '--issue', '104'], {
      cwd: clone,
      encoding: 'utf8',
      timeout: 30_000,
      env: {
        ...process.env,
        PATH: `${stubBin}${path.delimiter}${process.env.PATH}`,
        PI_BIN: hangPi,
        AGENT_PI_TIMEOUT_MS: '5000',
        AGENT_PI_HEARTBEAT_MS: '500',
        AGENT_BASE: 'master',
      },
    });
    const elapsed = Date.now() - started;

    expect(res.status).toBe(0);
    expect(elapsed).toBeLessThan(30_000);

    const stateJson = JSON.parse(readFileSync(path.join(clone, '.agent', 'state.json'), 'utf8'));
    expect(stateJson[104].status).toBe('needs_human');

    const issueLog = readFileSync(path.join(clone, '.agent', 'logs', 'issue-104.md'), 'utf8');
    expect(issueLog).toMatch(/pi 超时（\d+ (分钟|秒)）/);
    // heartbeat lines are visible in the log (issue #104 point 3)
    expect(issueLog).toMatch(/pi 心跳：已运行/);

    const calls = readFileSync(ghCalls, 'utf8');
    expect(calls).toContain('issue comment 104');
  });
});

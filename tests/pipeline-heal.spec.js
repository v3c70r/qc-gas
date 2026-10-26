// Pipeline self-healing (issue #130).
//
// Two failure modes used to require a human every single time:
//
//  A. `pi` hangs → timeout → needs_human (happened 3 times: #96, #118, #121).
//     Every one of them succeeded on the very next attempt, i.e. the timeout was
//     transient. A timeout now retries the SAME phase exactly once (after
//     clearing whatever the killed process left behind) and only a second
//     timeout escalates to needs_human.
//
//  B. `gh pr merge` blocked by conflicts (4 times: #95, #105, #116, #127).
//     Every conflicting file was under `data/` — generated probe/Actions output
//     where the remote version is authoritative, so the resolution is purely
//     mechanical: merge base into the branch, `--theirs` for `data/`, push,
//     retry the merge once. Real code conflicts still go to a human, with the
//     conflicting paths listed.
import { test, expect } from '@playwright/test';
import { chmodSync, cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { execFileSync, spawnSync } from 'node:child_process';
import { tmpdir } from 'node:os';
import path from 'node:path';
import {
  DEFAULT_PI_TIMEOUT_RETRIES,
  PiTimeoutError,
  cleanupPiTimeoutLeftovers,
  healMergeConflict,
  isDataOnlyConflict,
  isGeneratedDataPath,
  isMergeConflictError,
  mergeWithConflictHeal,
  parseUnmergedPaths,
  piTimeoutRetryLogLine,
  resolvePiTimeoutRetries,
} from '../.agent/lib.mjs';

const TMP_ROOT = () => mkdtempSync(path.join(tmpdir(), 'heal-'));
const HANGING = 'sleep 9999';
const CONFLICT_MSG = 'X Pull request #1 is not mergeable: the merge commit cannot be cleanly created (CONFLICT)';

// ── a throw-away bare origin + clone ────────────────────────────────
function makeRepo() {
  const dir = TMP_ROOT();
  const bare = path.join(dir, 'origin.git');
  const clone = path.join(dir, 'work');
  execFileSync('git', ['init', '--bare', '-q', '-b', 'master', bare], { stdio: 'pipe' });
  execFileSync('git', ['clone', '-q', bare, clone], { stdio: 'pipe' });
  const git = (...args) => execFileSync('git', args, { cwd: clone, encoding: 'utf8', stdio: 'pipe' }).trim();
  git('config', 'user.email', 'test@example.com');
  git('config', 'user.name', 'Test');
  const write = (rel, body) => {
    const abs = path.join(clone, rel);
    mkdirSync(path.dirname(abs), { recursive: true });
    writeFileSync(abs, body);
  };
  const seed = (files) => {
    for (const [rel, body] of Object.entries(files)) write(rel, body);
    git('add', '-A');
    git('commit', '-q', '-m', 'seed');
    git('branch', '-M', 'master');
    git('push', '-q', '-u', 'origin', 'master');
  };
  const commitOn = (branch, files, msg = `edit ${branch}`) => {
    git('checkout', '-q', '-B', branch, 'master');
    for (const [rel, body] of Object.entries(files)) write(rel, body);
    git('add', '-A');
    git('commit', '-q', '-m', msg);
    git('push', '-q', 'origin', branch);
  };
  return { dir, bare, clone, git, seed, commitOn, write };
}

function fakePi(body) {
  const dir = mkdtempSync(path.join(tmpdir(), 'fake-pi-'));
  const file = path.join(dir, 'pi');
  writeFileSync(file, `#!/usr/bin/env bash\n${body}\n`);
  chmodSync(file, 0o755);
  return file;
}

// ────────────────────────────────────────────────────────────────────
test.describe('pure helpers (#130)', () => {
  test('data/ paths are recognised, everything else is not', () => {
    expect(isGeneratedDataPath('data/x.json')).toBe(true);
    expect(isGeneratedDataPath('./data/nested/y.json')).toBe(true);
    expect(isGeneratedDataPath('data')).toBe(true);
    expect(isGeneratedDataPath('js/stats.js')).toBe(false);
    expect(isGeneratedDataPath('data.txt')).toBe(false);
    expect(isGeneratedDataPath('src/data/x.json')).toBe(false);
  });

  test('isDataOnlyConflict requires at least one path and ALL under data/', () => {
    expect(isDataOnlyConflict(['data/a.json', 'data/b.json'])).toBe(true);
    expect(isDataOnlyConflict(['data/a.json'])).toBe(true);
    expect(isDataOnlyConflict([])).toBe(false);         // no paths = unknown, never "safe"
    expect(isDataOnlyConflict(['data/a.json', 'js/app.js'])).toBe(false);
    expect(isDataOnlyConflict(['js/app.js'])).toBe(false);
  });

  test('unmerged paths are parsed out of `git status`/`diff` output', () => {
    expect(parseUnmergedPaths('UU data/a.json\nAA data/b.json\n')).toEqual(['data/a.json', 'data/b.json']);
    expect(parseUnmergedPaths('data/c.json\n')).toEqual(['data/c.json']);
    expect(parseUnmergedPaths('')).toEqual([]);
  });

  test('merge conflicts are told apart from unrelated gh failures', () => {
    expect(isMergeConflictError(new Error(CONFLICT_MSG))).toBe(true);
    expect(isMergeConflictError(new Error('GraphQL: Pull request is not mergeable (mergeable=false)'))).toBe(true);
    expect(isMergeConflictError('merge conflict in data/x.json')).toBe(true);
    expect(isMergeConflictError(new Error('HTTP 403: Resource not accessible by integration'))).toBe(false);
    expect(isMergeConflictError(new Error('required status check is expected'))).toBe(false);
  });

  test('retry log line says 超时，自动重试 with the attempt counter', () => {
    const line = piTimeoutRetryLogLine(300_000, 1, 1);
    expect(line).toContain('pi 超时');
    expect(line).toContain('超时，自动重试 1/1');
  });

  test('retry count defaults to 1 and is configurable (0 disables, garbage falls back)', () => {
    expect(DEFAULT_PI_TIMEOUT_RETRIES).toBe(1);
    expect(resolvePiTimeoutRetries({})).toBe(1);
    expect(resolvePiTimeoutRetries({ AGENT_PI_TIMEOUT_RETRIES: '0' })).toBe(0);
    expect(resolvePiTimeoutRetries({ AGENT_PI_TIMEOUT_RETRIES: '2' })).toBe(2);
    expect(resolvePiTimeoutRetries({ AGENT_PI_TIMEOUT_RETRIES: 'many' })).toBe(1);
  });
});

test.describe('clearing leftovers before a retry (#130)', () => {
  test('stale git index.lock and half-built worktrees are removed', () => {
    const repo = makeRepo();
    repo.seed({ 'README.md': 'seed\n' });
    const tmp = path.join(repo.clone, '.agent', 'tmp');
    mkdirSync(tmp, { recursive: true });
    mkdirSync(path.join(tmp, 'wt-130'), { recursive: true });
    mkdirSync(path.join(tmp, 'merge-7'), { recursive: true });
    writeFileSync(path.join(repo.clone, '.git', 'index.lock'), '');
    // a prompt file must survive: it is the input of the retried call
    writeFileSync(path.join(tmp, 'agent-issue-130.prompt.md'), 'prompt');

    const res = cleanupPiTimeoutLeftovers({ cwd: repo.clone, tmpDir: tmp });

    expect(existsSync(path.join(repo.clone, '.git', 'index.lock'))).toBe(false);
    expect(existsSync(path.join(tmp, 'wt-130'))).toBe(false);
    expect(existsSync(path.join(tmp, 'merge-7'))).toBe(false);
    expect(existsSync(path.join(tmp, 'agent-issue-130.prompt.md'))).toBe(true);
    expect(res.removed.length).toBeGreaterThanOrEqual(3);
    // the repo is usable again: git no longer refuses with "index.lock exists"
    expect(() => repo.git('status', '--porcelain')).not.toThrow();
  });
});

test.describe('data-only merge conflict heals itself (#130)', () => {
  test('all-data conflict → take remote data/, commit, push', () => {
    const repo = makeRepo();
    repo.seed({ 'data/x.json': '{"v":"base"}\n', 'js/app.js': 'v0\n' });
    repo.commitOn('agent/1', { 'data/x.json': '{"v":"branch"}\n' });
    // master moves on independently (probe/Actions commit every 15–60 min)
    repo.git('checkout', '-q', 'master');
    repo.write('data/x.json', '{"v":"master"}\n');
    repo.git('add', '-A');
    repo.git('commit', '-q', '-m', 'data update');
    repo.git('push', '-q', 'origin', 'master');

    const res = healMergeConflict({ branch: 'agent/1', base: 'master', cwd: repo.clone });

    expect(res.resolved).toBe(true);
    expect(res.merged).toBe(true);
    expect(res.pushed).toBe(true);
    expect(res.files).toEqual(['data/x.json']);

    repo.git('fetch', '-q', 'origin');
    // remote version is authoritative for generated data
    expect(repo.git('show', 'origin/agent/1:data/x.json')).toBe('{"v":"master"}');
    // the branch now contains base (merge commit pushed)
    expect(spawnSync('git', ['merge-base', '--is-ancestor', 'origin/master', 'origin/agent/1'], { cwd: repo.clone }).status).toBe(0);
    // no unmerged files left behind anywhere
    expect(repo.git('diff', '--name-only', '--diff-filter=U')).toBe('');
    expect(repo.git('status', '--porcelain')).toBe('');
  });

  test('healing is idempotent: a second run is a no-op (no duplicate merge commit)', () => {
    const repo = makeRepo();
    repo.seed({ 'data/x.json': '{"v":"base"}\n' });
    repo.commitOn('agent/1', { 'data/x.json': '{"v":"branch"}\n' });
    repo.git('checkout', '-q', 'master');
    repo.write('data/x.json', '{"v":"master"}\n');
    repo.git('add', '-A');
    repo.git('commit', '-q', '-m', 'data update');
    repo.git('push', '-q', 'origin', 'master');

    expect(healMergeConflict({ branch: 'agent/1', base: 'master', cwd: repo.clone }).merged).toBe(true);
    repo.git('fetch', '-q', 'origin');
    const headBefore = repo.git('rev-parse', 'origin/agent/1');

    const again = healMergeConflict({ branch: 'agent/1', base: 'master', cwd: repo.clone });

    expect(again.resolved).toBe(true);
    expect(again.merged).toBe(false);   // already up to date, nothing pushed
    repo.git('fetch', '-q', 'origin');
    expect(repo.git('rev-parse', 'origin/agent/1')).toBe(headBefore);
  });

  test('a code conflict is NOT auto-resolved and leaves no half-done merge', () => {
    const repo = makeRepo();
    repo.seed({ 'data/x.json': '{"v":"base"}\n', 'js/app.js': 'v0\n' });
    repo.commitOn('agent/2', { 'js/app.js': 'branch\n' });
    repo.git('checkout', '-q', 'master');
    repo.write('js/app.js', 'master\n');
    repo.git('add', '-A');
    repo.git('commit', '-q', '-m', 'code change');
    repo.git('push', '-q', 'origin', 'master');
    repo.git('fetch', '-q', 'origin');
    const before = repo.git('rev-parse', 'origin/agent/2');

    const res = healMergeConflict({ branch: 'agent/2', base: 'master', cwd: repo.clone });

    expect(res.resolved).toBe(false);
    expect(res.reason).toBe('code-conflict');
    expect(res.files).toEqual(['js/app.js']);
    expect(repo.git('diff', '--name-only', '--diff-filter=U')).toBe('');
    expect(repo.git('status', '--porcelain')).toBe('');
    expect(repo.git('rev-parse', 'origin/agent/2')).toBe(before);
  });
});

test.describe('merge retry orchestration (#130)', () => {
  test('conflict → heal → retry succeeds; logs list the files taken from remote', async () => {
    const repo = makeRepo();
    repo.seed({ 'data/x.json': '{"v":"base"}\n' });
    repo.commitOn('agent/3', { 'data/x.json': '{"v":"branch"}\n' });
    repo.git('checkout', '-q', 'master');
    repo.write('data/x.json', '{"v":"master"}\n');
    repo.git('add', '-A');
    repo.git('commit', '-q', '-m', 'data update');
    repo.git('push', '-q', 'origin', 'master');

    const logs = [];
    let calls = 0;
    const mergeFn = async () => {
      calls += 1;
      repo.git('fetch', '-q', 'origin');
      const merged = spawnSync('git', ['merge-base', '--is-ancestor', 'origin/master', 'origin/agent/3'], { cwd: repo.clone }).status === 0;
      if (!merged) throw new Error(CONFLICT_MSG);
    };

    const res = await mergeWithConflictHeal({
      pr: 1, branch: 'agent/3', base: 'master', cwd: repo.clone, mergeFn,
      onLog: (m) => logs.push(m),
    });

    expect(res.merged).toBe(true);
    expect(res.healed).toBe(true);
    expect(res.files).toEqual(['data/x.json']);
    expect(calls).toBe(2);                       // initial attempt + exactly one retry
    expect(logs.join('\n')).toContain('data/');
    expect(logs.join('\n')).toContain('agent/3');
  });

  test('code conflict → human, conflicting files listed, no retry attempted', async () => {
    const repo = makeRepo();
    repo.seed({ 'js/app.js': 'v0\n' });
    repo.commitOn('agent/4', { 'js/app.js': 'branch\n' });
    repo.git('checkout', '-q', 'master');
    repo.write('js/app.js', 'master\n');
    repo.git('add', '-A');
    repo.git('commit', '-q', '-m', 'code change');
    repo.git('push', '-q', 'origin', 'master');

    let calls = 0;
    const res = await mergeWithConflictHeal({
      pr: 2, branch: 'agent/4', base: 'master', cwd: repo.clone,
      mergeFn: async () => { calls += 1; throw new Error(CONFLICT_MSG); },
      onLog: () => {},
    });

    expect(res.merged).toBe(false);
    expect(res.reason).toBe('code-conflict');
    expect(res.files).toEqual(['js/app.js']);
    expect(calls).toBe(1);
  });

  test('non-conflict failures (branch protection, permissions) never trigger a heal', async () => {
    let mergeCalls = 0;
    const res = await mergeWithConflictHeal({
      pr: 3, branch: 'agent/5', base: 'master', cwd: process.cwd(),
      mergeFn: async () => { mergeCalls += 1; throw new Error('HTTP 403: Resource not accessible by integration'); },
      healFn: () => { throw new Error('heal must not run'); },
      onLog: () => {},
    });

    expect(res.merged).toBe(false);
    expect(res.reason).toBe('merge-error');
    expect(mergeCalls).toBe(1);
  });

  test('allowHeal=false is idempotent: no second heal, no second merge attempt', async () => {
    let mergeCalls = 0;
    let heals = 0;
    const res = await mergeWithConflictHeal({
      pr: 4, branch: 'agent/6', base: 'master', cwd: process.cwd(),
      mergeFn: async () => { mergeCalls += 1; throw new Error(CONFLICT_MSG); },
      healFn: () => { heals += 1; return { resolved: true, merged: true, files: ['data/x.json'] }; },
      allowHeal: false,
      onLog: () => {},
    });

    expect(res.merged).toBe(false);
    expect(res.reason).toBe('already-healed');
    expect(heals).toBe(0);
    expect(mergeCalls).toBe(1);
  });
});

// ── end-to-end: a whole `pipeline.mjs run` pass ──────────────────────
// Fake `pi` through PI_BIN; `gh` stubbed (no network, no credentials).
function pipelineSandbox(piBin, extraEnv = {}) {
  const dir = TMP_ROOT();
  const bare = path.join(dir, 'origin.git');
  const clone = path.join(dir, 'repo');
  const stubBin = path.join(dir, 'bin');
  const ghCalls = path.join(dir, 'gh-calls.log');
  mkdirSync(stubBin);

  execFileSync('git', ['init', '--bare', '-q', '-b', 'master', bare], { stdio: 'pipe' });
  execFileSync('git', ['clone', '-q', bare, clone], { stdio: 'pipe' });
  const git = (...args) => execFileSync('git', args, { cwd: clone, stdio: 'pipe' });
  git('config', 'user.email', 'test@example.com');
  git('config', 'user.name', 'Test');
  writeFileSync(path.join(clone, 'README.md'), 'seed\n');
  git('add', 'README.md');
  git('commit', '-q', '-m', 'seed');
  git('branch', '-M', 'master');
  git('push', '-q', '-u', 'origin', 'master');

  const agentSrc = path.join(process.cwd(), '.agent');
  cpSync(agentSrc, path.join(clone, '.agent'), {
    recursive: true,
    filter: (src) => {
      const rel = path.relative(agentSrc, src).split(path.sep);
      return !rel.some(p => p === 'logs' || p === 'tmp' || p === 'state.json' || p === 'watch.lock');
    },
  });

  writeFileSync(path.join(stubBin, 'gh'), [
    '#!/usr/bin/env bash',
    `printf '%s\\n' "$*" >> '${ghCalls}'`,
    'if [ "$1" = "issue" ] && [ "$2" = "view" ]; then',
    `  echo '{"number":130,"title":"self heal","body":"Depends on: none","labels":[]}'`,
    'fi',
    'exit 0',
  ].join('\n') + '\n');
  chmodSync(path.join(stubBin, 'gh'), 0o755);

  const run = () => spawnSync(process.execPath, ['.agent/pipeline.mjs', 'run', '--issue', '130'], {
    cwd: clone,
    encoding: 'utf8',
    timeout: 60_000,
    env: {
      ...process.env,
      PATH: `${stubBin}${path.delimiter}${process.env.PATH}`,
      PI_BIN: piBin,
      AGENT_BASE: 'master',
      AGENT_PI_HEARTBEAT_MS: '0',
      ...extraEnv,
    },
  });
  return { dir, clone, ghCalls, run };
}

test.describe('a timeout is retried once in the same phase (#130)', () => {
  test('first call hangs, second succeeds → pass continues, no needs_human', () => {
    // counter file decides the behaviour per invocation
    const dir = mkdtempSync(path.join(tmpdir(), 'pi-count-'));
    const counter = path.join(dir, 'count');
    const bin = path.join(dir, 'pi');
    writeFileSync(bin, [
      '#!/usr/bin/env bash',
      `n=$(cat '${counter}' 2>/dev/null || echo 0)`,
      'n=$((n+1))',
      `echo "$n" > '${counter}'`,
      'if [ "$n" = "1" ]; then sleep 9999; fi',
      'echo "fake pi 第二次调用：正常完成"',
      'exit 0',
    ].join('\n') + '\n');
    chmodSync(bin, 0o755);

    const sb = pipelineSandbox(bin, { AGENT_PI_TIMEOUT_MS: '1200' });
    const res = sb.run();

    expect(res.status).toBe(0);
    expect(parseInt(readFileSync(counter, 'utf8'), 10)).toBe(2);

    const stateJson = JSON.parse(readFileSync(path.join(sb.clone, '.agent', 'state.json'), 'utf8'));
    expect(stateJson[130].status).not.toBe('needs_human');

    const issueLog = readFileSync(path.join(sb.clone, '.agent', 'logs', 'issue-130.md'), 'utf8');
    expect(issueLog).toContain('超时，自动重试 1/1');
    expect(issueLog).toContain('pi 超时');
  });

  test('two timeouts → needs_human, exactly 2 attempts, one issue comment', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'pi-count-'));
    const counter = path.join(dir, 'count');
    const bin = path.join(dir, 'pi');
    writeFileSync(bin, [
      '#!/usr/bin/env bash',
      `n=$(cat '${counter}' 2>/dev/null || echo 0)`,
      'n=$((n+1))',
      `echo "$n" > '${counter}'`,
      HANGING,
    ].join('\n') + '\n');
    chmodSync(bin, 0o755);

    const sb = pipelineSandbox(bin, { AGENT_PI_TIMEOUT_MS: '1000' });
    const res = sb.run();

    expect(res.status).toBe(0);
    expect(parseInt(readFileSync(counter, 'utf8'), 10)).toBe(2);

    const stateJson = JSON.parse(readFileSync(path.join(sb.clone, '.agent', 'state.json'), 'utf8'));
    expect(stateJson[130].status).toBe('needs_human');

    const issueLog = readFileSync(path.join(sb.clone, '.agent', 'logs', 'issue-130.md'), 'utf8');
    expect(issueLog).toContain('超时，自动重试 1/1');

    // idempotent escalation: ONE final needs_human comment, not one per attempt
    const calls = readFileSync(sb.ghCalls, 'utf8').split('\n').filter(l => l.startsWith('issue comment 130'));
    expect(calls.length).toBe(1);
  });

  test('retries can be disabled with AGENT_PI_TIMEOUT_RETRIES=0', () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'pi-count-'));
    const counter = path.join(dir, 'count');
    const bin = path.join(dir, 'pi');
    writeFileSync(bin, [
      '#!/usr/bin/env bash',
      `echo x >> '${counter}'`,
      HANGING,
    ].join('\n') + '\n');
    chmodSync(bin, 0o755);

    const sb = pipelineSandbox(bin, { AGENT_PI_TIMEOUT_MS: '1000', AGENT_PI_TIMEOUT_RETRIES: '0' });
    const res = sb.run();

    expect(res.status).toBe(0);
    expect(readFileSync(counter, 'utf8').trim().split('\n').length).toBe(1);
    const stateJson = JSON.parse(readFileSync(path.join(sb.clone, '.agent', 'state.json'), 'utf8'));
    expect(stateJson[130].status).toBe('needs_human');
  });
});

// Guard: helpers must still export the timeout error they depend on
test.describe('existing timeout semantics preserved (#104 + #130)', () => {
  test('PiTimeoutError is still exported and keeps its message', () => {
    const err = new PiTimeoutError(120_000);
    expect(err.code).toBe('PI_TIMEOUT');
    expect(err.message).toContain('pi 超时');
    // a sanity check on the fixture we rely on: statSync works on a fresh dir
    expect(statSync(TMP_ROOT()).isDirectory()).toBe(true);
  });
});

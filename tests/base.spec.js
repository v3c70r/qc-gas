// Regression tests for base-branch detection (issue #99).
//
// Real scenario that broke two approved issues: the container clone was created
// with `git clone -b master <host repo>` while the host repo was sitting on
// `agent/93`, so the clone's `refs/remotes/origin/HEAD` pointed at
// `origin/agent/93`. detectBase() trusted it and the next `gh pr create --base
// agent/93` failed forever. These tests build throw-away git repos and pin the
// desired behaviour (pure node, no network).
import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { detectBase, isUsableBase, syncRemoteHead } from '../.agent/lib.mjs';

const dirs = [];

function git(cwd, ...args) {
  return execFileSync('git', args, { cwd, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'] }).trim();
}

// Builds: bare "origin" + work clone. `branches` lists branches that exist on
// origin (master is always the commit source). `remoteHead`:
//   undefined → leave the clone's origin/HEAD as git set it on clone
//   null      → delete origin/HEAD (fresh-clone / gh-repo-create case)
//   string    → symbolic-ref origin/HEAD to origin/<remoteHead>
function makeClone({ branches = ['master'], remoteHead } = {}) {
  const dir = mkdtempSync(path.join(tmpdir(), 'detect-base-'));
  dirs.push(dir);
  const bare = path.join(dir, 'origin.git');
  const seed = path.join(dir, 'seed');
  const clone = path.join(dir, 'clone');
  git(dir, 'init', '--bare', '-q', bare);
  git(dir, 'init', '-q', seed);
  git(seed, 'config', 'user.email', 'test@example.com');
  git(seed, 'config', 'user.name', 'Test');
  writeFileSync(path.join(seed, 'README.md'), 'seed\n');
  git(seed, 'add', 'README.md');
  git(seed, 'commit', '-q', '-m', 'seed');
  git(seed, 'branch', '-M', 'master');
  for (const b of branches) {
    if (b !== 'master') git(seed, 'branch', b);
  }
  git(seed, 'remote', 'add', 'origin', bare);
  git(seed, 'push', '-q', 'origin', '--all');
  git(dir, 'clone', '-q', bare, clone);
  if (remoteHead === null) {
    git(clone, 'symbolic-ref', '--delete', 'refs/remotes/origin/HEAD');
  } else if (remoteHead) {
    git(clone, 'symbolic-ref', 'refs/remotes/origin/HEAD', `refs/remotes/origin/${remoteHead}`);
  }
  return clone;
}

test.afterEach(() => {
  while (dirs.length) {
    try { rmSync(dirs.pop(), { recursive: true, force: true }); } catch {}
  }
});

test.describe('detectBase never trusts a stale agent/* origin/HEAD (#99)', () => {
  test('origin/HEAD → origin/agent/93 falls back to master', () => {
    const cwd = makeClone({ branches: ['master', 'agent/93'], remoteHead: 'agent/93' });
    expect(detectBase(cwd, { env: {} })).toBe('master');
  });

  test('origin/HEAD → origin/main is still honoured', () => {
    const cwd = makeClone({ branches: ['main', 'master'], remoteHead: 'main' });
    expect(detectBase(cwd, { env: {} })).toBe('main');
  });

  test('origin/HEAD → origin/master keeps today’s behaviour', () => {
    const cwd = makeClone({ remoteHead: 'master' });
    expect(detectBase(cwd, { env: {} })).toBe('master');
  });

  test('missing origin/HEAD falls back to master', () => {
    const cwd = makeClone({ remoteHead: null });
    expect(detectBase(cwd, { env: {} })).toBe('master');
  });

  test('AGENT_BASE overrides detection', () => {
    const cwd = makeClone({ branches: ['develop', 'master'], remoteHead: 'agent/93' });
    expect(detectBase(cwd, { env: { AGENT_BASE: 'develop' } })).toBe('develop');
  });

  test('config base overrides detection when AGENT_BASE is unset', () => {
    const cwd = makeClone({ branches: ['develop', 'master'], remoteHead: 'agent/93' });
    mkdirSync(path.join(cwd, '.agent'), { recursive: true });
    writeFileSync(path.join(cwd, '.agent', 'config.json'), JSON.stringify({ base: 'develop' }));
    expect(detectBase(cwd, { env: {} })).toBe('develop');
  });

  test('origin/HEAD pointing at the currently checked-out branch is skipped', () => {
    const cwd = makeClone({ branches: ['main', 'master'], remoteHead: 'main' });
    git(cwd, 'checkout', '-q', 'main');
    expect(detectBase(cwd, { env: {} })).toBe('master');
  });

  test('isUsableBase rejects this project’s own work branches', () => {
    expect(isUsableBase('agent/93')).toBe(false);
    expect(isUsableBase('agent/99', { currentBranch: 'master' })).toBe(false);
    expect(isUsableBase('master')).toBe(true);
    expect(isUsableBase('main')).toBe(true);
    expect(isUsableBase('develop')).toBe(true);
    expect(isUsableBase('')).toBe(false);
    expect(isUsableBase('master', { currentBranch: 'master' })).toBe(false);
  });

  test('syncRemoteHead repairs a clone poisoned by the source repo’s HEAD', () => {
    const cwd = makeClone({ branches: ['agent/93'], remoteHead: 'agent/93' });
    expect(syncRemoteHead('master', cwd)).toBe(true);
    expect(git(cwd, 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD')).toBe('origin/master');
    expect(detectBase(cwd, { env: {} })).toBe('master');
  });
});

// lib.mjs — shared environment helpers used by BOTH:
//   * templates/pipeline.mjs  (the live multi-agent loop)
//   * scripts/doctor.mjs      (the one-shot preflight)
// Keeping `detectBase()` and `resolvePi()` in ONE place avoids the two drifting
// apart (lessons #1 PATH pollution, #14 missing origin/HEAD).
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

// tiny non-interactive shell helper; throws on nonzero exit.
// cwd defaults to the repo containing this file; doctor.mjs passes the
// repo being checked explicitly so it works when run from a skill directory.
export function sh(cmd, opts = {}) {
  const out = execFileSync(cmd[0], cmd.slice(1), {
    cwd: opts.cwd || ROOT,
    encoding: 'utf8',
    stdio: ['pipe', 'pipe', 'pipe'],
    // env/timeout 必须转发：丢掉它们会静默破坏「每角色 GH_TOKEN 身份注入」与长任务
    ...(opts.env ? { env: opts.env } : {}),
    ...(opts.timeout ? { timeout: opts.timeout } : {}),
  });
  return out.trim();
}

// Agent C's build/test commands. Defaults keep the historical npm behaviour,
// but repos that are NOT npm-based (Python/Go/Rust) or that simply have no
// build/test scripts must be able to install this skill — they set
// AGENT_BUILD_CMD / AGENT_TEST_CMD (e.g. `make build` / `pytest -q`).
// An explicit empty string means "skip that step". The resolved values are what
// the PR comment reports, so the claim always equals what actually ran (lesson #17).
export function resolveTesterCommands(env = process.env) {
  const build = env.AGENT_BUILD_CMD ?? 'npm run build';
  const test = env.AGENT_TEST_CMD ?? 'npm test';
  return { build: String(build).trim(), test: String(test).trim() };
}

// ── /reject 语义（issue #14）───────────────────────────────────────
// 产品承诺「/reject 可取消一次批准」，但标签驱动的 worker 只看触发标签：
// 只加一个否定标签 agent-rejected 而不移除 agent-approved，issue 仍会被实现、
// 审查、测试并**自动合并**（合并不可逆）。因此取消必须 (1) 两个标签互斥，
// (2) 取活判定把 agent-rejected 当**硬排除**，让状态机有可达的 rejected 终态。
// 单一实现，pipeline 与 validate 共用（避免两份归一化逻辑漂移）。
export const REJECTED_LABEL = 'agent-rejected';
export const APPROVED_LABEL = 'agent-approved';

/** Normalize an issue's labels to plain names (string[] or GitHub's [{name}]). */
export function labelNames(issue) {
  return (issue?.labels || [])
    .map(l => (typeof l === 'string' ? l : l?.name))
    .filter(Boolean);
}

/** True when the issue carries the /reject label (hard veto, beats approval). */
export function isRejectedIssue(issue) {
  return labelNames(issue).includes(REJECTED_LABEL);
}

// Which issues should the watch loop pick up?
//  * agent-rejected → NEVER (veto wins even if agent-approved is still present)
//  * terminal state (merged/failed/needs_human/rejected) → no
// Pure + explicit so it can be asserted without gh/network.
export function selectPendingIssues(issues = [], state = {}) {
  const TERMINAL = ['merged', 'failed', 'needs_human', 'rejected'];
  return (issues || []).filter(it => {
    if (isRejectedIssue(it)) return false;      // 否决优先
    const c = state[it.number];
    return !c || !TERMINAL.includes(c.status);
  });
}

// Prepare the branch Agent A implements on.
// FIRST round: branch off `origin/<base>`. FIX round: the PR branch already
// exists upstream, so we MUST continue on it — `git checkout -B <branch>
// origin/<base>` would silently RESET it back to base (dropping the previous
// round's commits) and turn the follow-up `git push` into a non-fast-forward
// reject. This is the single, testable place where that decision is made
// (lesson #18). `fetch origin` (all refs) is required so `origin/<branch>`
// is up to date on the fix-round path.
export function prepareImplementerBranch({ branch, base, cwd } = {}) {
  const exists = (ref) => {
    try { sh(['git', 'rev-parse', '--verify', '--quiet', ref], { cwd }); return true; }
    catch { return false; }
  };
  sh(['git', 'fetch', 'origin', '--quiet'], { cwd });
  const remote = `origin/${branch}`;
  if (exists(remote)) {
    // fix round — continue on the PR branch (`checkout <branch>` auto-creates a
    // local tracking branch when this checkout does not have one yet).
    sh(['git', 'checkout', branch], { cwd });
    sh(['git', 'reset', '--hard', remote], { cwd });
    return { reused: true };
  }
  sh(['git', 'checkout', '-B', branch, `origin/${base}`], { cwd });
  return { reused: false };
}

// State updates must MERGE, never replace: overwriting an issue entry with a
// fresh `{round:0,fixes:0}` erased the review round the loop had just recorded,
// so state.json could not remember that any fix round happened (lesson #18).
export function mergeIssueState(prev, patch) {
  return { ...(prev || {}), ...patch };
}

// ── dependency unblock semantics (issue #75) ────────────────────────
// A blocked issue whose dependencies just closed has NO PR yet. The old code
// wrote `status: 'pr_open'` at unblock time — a lie that only worked because
// the next tick happened to hit the `status === 'pr_open' && !pr` special
// case. Deleting the entry says the honest thing: "never started, ready to
// implement". Pure so it can be asserted without gh/network.
export function clearBlockedState(state = {}, issueNumber) {
  if (state[issueNumber]?.status !== 'blocked') return { changed: false, state };
  const next = { ...state };
  delete next[issueNumber];
  return { changed: true, state: next };
}

// ── single-instance lock (issue #75) ────────────────────────────────
// Two `pipeline.mjs watch` processes share ONE working tree: concurrent
// checkout/reset/commit corrupt each other's implement branch. A PID file turns
// that race into an immediate refusal. A stale file left by SIGKILL must NOT
// lock the user out forever — a dead PID is taken over silently.
export function isPidAlive(pid, kill = process.kill) {
  const n = Number(pid);
  if (!Number.isInteger(n) || n <= 0) return false;
  try {
    kill(n, 0);
    return true;
  } catch (e) {
    // EPERM = process exists but belongs to another user → still alive.
    return e?.code === 'EPERM';
  }
}

export function readLockPid(file) {
  try {
    const n = parseInt(readFileSync(file, 'utf8').trim(), 10);
    return Number.isInteger(n) && n > 0 ? n : null;
  } catch {
    return null;
  }
}

// Try to become THE watcher. Returns { acquired, pid, tookOver }.
// `pid`/`kill` are injectable so the alive/stale branches are unit-testable.
export function acquireWatchLock(file, { pid = process.pid, kill = process.kill } = {}) {
  const existing = readLockPid(file);
  if (existing && isPidAlive(existing, kill)) {
    return { acquired: false, pid: existing, tookOver: false };
  }
  mkdirSync(path.dirname(file), { recursive: true });
  writeFileSync(file, `${pid}\n`);
  return { acquired: true, pid, tookOver: Boolean(existing) };
}

// Remove the lock ONLY if we still own it (never delete another instance's).
export function releaseWatchLock(file, { pid = process.pid } = {}) {
  try {
    if (readLockPid(file) !== pid) return false;
    rmSync(file, { force: true });
    return true;
  } catch {
    return false;
  }
}

// ── base-branch detection (issue #99) ──────────────────────────────
// `refs/remotes/origin/HEAD` is a LOCAL cache of the remote's default branch.
// Cloning a host repo that happened to sit on `agent/93` produced
// `origin/HEAD → origin/agent/93`; trusting it made every `gh pr create --base
// agent/93` fail ("No commits between agent/93 and agent/N"). So a candidate
// must look like a real integration branch, never one of our own work branches
// and never the branch we are currently on.
export function isUsableBase(candidate, { currentBranch = '' } = {}) {
  const c = String(candidate || '').trim().replace(/^origin\//, '');
  if (!c || c === 'HEAD') return false;
  if (c.startsWith('agent/')) return false;   // this project's own PR branches
  if (currentBranch && c === currentBranch) return false;
  return true;
}

// Explicit `base` in .agent/config.json (optional; AGENT_BASE wins over it).
function configBase(cwd) {
  try {
    const cfg = JSON.parse(readFileSync(path.join(cwd, '.agent', 'config.json'), 'utf8'));
    return typeof cfg.base === 'string' ? cfg.base.trim() : '';
  } catch { return ''; }
}

// default branch auto-detected (main/master); override with AGENT_BASE or
// `.agent/config.json` {"base": "..."}.
// NOTE: `refs/remotes/origin/HEAD` is often MISSING on fresh clones (e.g. after
// `gh repo create --source=. --push`), so never let detection throw at import time.
export function detectBase(cwd = ROOT, { env = process.env } = {}) {
  if (env.AGENT_BASE) return env.AGENT_BASE;
  const configured = configBase(cwd);
  if (configured) return configured;
  const tryOut = (args) => { try { return sh(args, { cwd }).trim(); } catch { return ''; } };
  const currentBranch = tryOut(['git', 'rev-parse', '--abbrev-ref', 'HEAD']);
  const usable = (c) => isUsableBase(c, { currentBranch });

  // 1) cached remote HEAD — but only if it points at a plausible base branch.
  const sym = tryOut(['git', 'symbolic-ref', '--short', 'refs/remotes/origin/HEAD']);
  if (usable(sym)) return sym.replace(/^origin\//, '');

  // 2) conventional names actually present on the remote.
  for (const cand of ['main', 'master']) {
    if (usable(cand) && tryOut(['git', 'rev-parse', '--verify', `origin/${cand}`])) return cand;
  }

  // 3) ask the remote itself (authoritative, ignores the stale local cache).
  const ls = tryOut(['git', 'ls-remote', '--symref', 'origin', 'HEAD']);
  const m = ls.match(/^ref:\s+refs\/heads\/(\S+)\s+HEAD/m);
  if (m && usable(m[1])) return m[1];
  return 'master';
}

// Hardening for clone/install paths: pin the local cache to the branch we
// actually use, so a source repo parked on `agent/N` cannot poison this clone.
// Best-effort: detached/no-remote environments must not crash the pipeline.
export function syncRemoteHead(base, cwd = ROOT) {
  if (!base) return false;
  try { sh(['git', 'remote', 'set-head', 'origin', base], { cwd }); return true; }
  catch { return false; }
}

// locate the real pi binary.
// `npm run` prepends node_modules/.bin to PATH, and a transitive dep also ships
// a (much older) `pi` CLI that shadows the real one — skip those dirs.
let PI_BIN = null;
export function resolvePi() {
  if (PI_BIN) return PI_BIN;
  if (process.env.PI_BIN) { PI_BIN = process.env.PI_BIN; return PI_BIN; }
  const dirs = (process.env.PATH || '').split(path.delimiter)
    .filter(d => d && !d.includes('node_modules'));
  for (const d of dirs) {
    const c = path.join(d, 'pi');
    if (existsSync(c)) { PI_BIN = c; return PI_BIN; }
  }
  PI_BIN = 'pi';
  return PI_BIN;
}

#!/usr/bin/env node
// ══════════════════════════════════════════════════════════════════
//  Multi-agent loop — local orchestrator (v1)
//
//  Roles:
//    A implementer  — reads approved issue, implements, opens PR
//    B reviewer     — reviews PR diff, posts comments, discusses w/ A
//    C tester       — builds & runs Playwright on the PR branch, merges
//
//  Shared memory:  PR comments (GitHub) + .agent/logs/issue-<N>.md
//  Trigger:        issue labelled `agent-approved` by GitHub Actions triage
//
//  Usage:
//    node .agent/pipeline.mjs watch                 # loop until idle (Ctrl-C)
//    node .agent/pipeline.mjs watch --once          # one pass
//    node .agent/pipeline.mjs run --issue 42        # force-run one issue
//    node .agent/pipeline.mjs test --issue 42       # run test agent only
//    node .agent/pipeline.mjs status                # show state machine
//
//  Env:
//    MAX_REVIEW_ROUNDS (default 3)  MAX_TEST_FIXES (default 2)
//    TEST_SKIP=1                    skip functional tests (demo mode)
//    TEST_ENV_FILE=/abs/path/.env   env file to copy into test worktree
// ══════════════════════════════════════════════════════════════════
import { execFileSync } from 'node:child_process';
import { mkdirSync, readFileSync, writeFileSync, existsSync, copyFileSync, symlinkSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const AGENT = path.join(ROOT, '.agent');
const STATE_FILE = path.join(AGENT, 'state.json');
const LOG_DIR = path.join(AGENT, 'logs');
const TMP = path.join(AGENT, 'tmp');
const LABEL_APPROVED = 'agent-approved';
const BASE = 'master';

// ── tiny helpers ──
function sh(cmd, opts = {}) {
  const out = execFileSync(cmd[0], cmd.slice(1), {
    cwd: opts.cwd || ROOT, encoding: 'utf8', stdio: ['pipe', 'pipe', 'pipe'],
    ...opts.shell !== undefined ? {} : {},
  });
  return out.trim();
}
function gh(args, opts = {}) {
  return sh(['gh', ...args], opts);
}
const state = {
  load() {
    if (existsSync(STATE_FILE)) return JSON.parse(readFileSync(STATE_FILE, 'utf8'));
    return {};
  },
  save(s) { writeFileSync(STATE_FILE, JSON.stringify(s, null, 2)); },
};
function log(issueNum, msg) {
  mkdirSync(LOG_DIR, { recursive: true });
  const f = path.join(LOG_DIR, `issue-${issueNum}.md`);
  writeFileSync(f, (existsSync(f) ? readFileSync(f, 'utf8') : '') + `\n[${new Date().toISOString()}] ${msg}\n`);
}
function commentOnIssue(num, body) { gh(['issue', 'comment', String(num), '--body', body]); }
function commentOnPr(num, body) { gh(['pr', 'comment', String(num), '--body', body]); }

// Get approved open issues that have no terminal state
function approvedIssues() {
  const out = gh(['issue', 'list', '--state', 'open', `--label=${LABEL_APPROVED}`, '--json', 'number,title,body,labels']);
  return JSON.parse(out);
}
function prForBranch(branch) {
  try {
    const out = gh(['pr', 'list', '--state', 'open', '--head', branch, '--json', 'number,headRefName,title']);
    const prs = JSON.parse(out);
    return prs[0] || null;
  } catch { return null; }
}
function openPrFor(issueNum, branch) {
  if (branch) { const p = prForBranch(branch); if (p) return p; }
  try {
    const out = gh(['pr', 'list', '--state', 'open', `--search`, `"#${issueNum}" in:body`, '--json', 'number,headRefName,title']);
    const prs = JSON.parse(out);
    return prs.find(p => p.title && p.title !== '') || null;
  } catch { return null; }
}

// ── Role A: implementer ──
async function runImplementer(issue, roundNote = '') {
  const s = state.load();
  const branch = `agent/${issue.number}`;
  const prompt = buildPrompt('implementer', { issue, branch, roundNote });
  log(issue.number, `A: 开始实现 (branch=${branch}) ${roundNote ? '——' + roundNote : ''}`);
  try {
    sh(['git', 'fetch', 'origin', BASE]);
    sh(['git', 'checkout', '-B', branch, `origin/${BASE}`]);
  } catch {
    // branch exists upstream; reset local tracking copy
    sh(['git', 'checkout', branch]);
    sh(['git', 'reset', '--hard', `origin/${branch}`]);
  }
  const out = runPi(`agent-issue-${issue.number}`, prompt, branch);
  log(issue.number, `A: pi 输出（尾）:\n${out.split('\n').slice(-40).join('\n')}`);

  sh(['git', 'add', '-A']);
  try { sh(['git', 'commit', '-m', `Agent A: ${issue.title} (Closes #${issue.number}) [agent]`, '--allow-empty']); } catch {}
  sh(['git', 'push', '-u', 'origin', branch]);

  // ensure PR exists (query by branch — search index lags after create)
  let pr = prForBranch(branch);
  if (!pr) {
    const body = [
      `Closes #${issue.number}`,
      '',
      `🤖 Agent A (自动实现) — 来自 approved issue #${issue.number}.`,
      `标题: ${issue.title}`,
    ].join('\n');
    gh(['pr', 'create', '--base', BASE, '--head', branch, '--title', `Agent: ${issue.title}`, '--body', body]);
    for (let i = 0; i < 5 && !pr; i++) {
      await new Promise(r => setTimeout(r, 2000));
      pr = prForBranch(branch);
    }
  }
  if (pr) {
    commentOnPr(pr.number, `🤖 **Agent A 完成实现**，等待 Agent B review。`);
    s[issue.number] = { status: 'pr_open', pr: pr.number, branch, round: 0, fixes: 0, title: issue.title };
  } else {
    s[issue.number] = { status: 'pr_open', pr: null, branch, round: 0, fixes: 0, title: issue.title };
    log(issue.number, 'PR 尚未可查，稍后按分支重查');
  }
  state.save(s);
  return pr;
}

// ── Role B: reviewer (round-trip discussion with A) ──
async function runReviewRound(issueNum, prNum) {
  const prompt = buildPrompt('reviewer', { issue: { number: issueNum }, pr: prNum });
  const out = runPi(`agent-review-${prNum}`, prompt, null);
  log(issueNum, `B: review 输出:\n${out.split('\n').slice(-30).join('\n')}`);
  const verdict = (out.match(/RESULT:\s*(APPROVE|REQUEST_CHANGES)/i) || [])[1] || 'REQUEST_CHANGES';
  return { verdict, out };
}

// ── Role C: tester (functional tests) ──
function runTester(issueNum, prNum, branch) {
  log(issueNum, `C: 开始功能测试 on ${branch}`);
  const wt = path.join(TMP, `wt-${issueNum}`);
  rmSync(wt, { recursive: true, force: true });
  mkdirSync(TMP, { recursive: true });
  sh(['git', 'worktree', 'add', wt, branch]);
  // reuse node_modules via symlink (vite/esbuild tolerate it)
  if (!existsSync(path.join(wt, 'node_modules'))) symlinkSync(path.join(ROOT, 'node_modules'), path.join(wt, 'node_modules'), 'dir');
  if (process.env.TEST_ENV_FILE && existsSync(process.env.TEST_ENV_FILE)) copyFileSync(process.env.TEST_ENV_FILE, path.join(wt, '.env'));
  try {
    sh(['npm', 'run', 'build'], { cwd: wt });
    const report = sh(['npm', 'test'], { cwd: wt }).split('\n').slice(-60).join('\n');
    log(issueNum, `C: 测试通过 ✅\n${report}`);
    commentOnPr(prNum, `🤖 **Agent C 功能测试通过** ✅（build + Playwright）`);
    return { pass: true };
  } catch (e) {
    const report = (e.stdout || e.message || '').split('\n').slice(-60).join('\n');
    log(issueNum, `C: 测试失败 ❌\n${report}`);
    commentOnPr(prNum, `🤖 **Agent C 功能测试失败** ❌\n\`\`\`\n${report.slice(0, 3000)}\n\`\`\``);
    return { pass: false };
  } finally {
    try { sh(['git', 'worktree', 'remove', wt, '--force']); } catch {}
    rmSync(wt, { recursive: true, force: true });
  }
}

// ── shared pi invocation ──
function runPi(sessionId, prompt, branchHint) {
  const cwdNote = branchHint ? `当前分支应为 agent 分支（已在本地 checkout）。` : '';
  const extra = prompt; // full system prompt already assembled by buildPrompt
  mkdirSync(TMP, { recursive: true });
  const pfile = path.join(TMP, `${sessionId}.prompt.md`);
  writeFileSync(pfile, extra);
  const args = ['-p', '--mode', 'text', '--session-id', sessionId,
    '--system-prompt', `You are part of an autonomous agent loop operating on the GitHub repo (cwd). ${cwdNote} Follow the instructions below strictly.`,
    '--append-system-prompt', pfile,
    '--no-approve',
    // positional user message — pi needs an actual message to act on
    '现在执行上面给出的完整任务：读所需上下文，用工具完成所有步骤，然后输出总结并结束。'];
  try {
    return execFileSync('pi', args, { cwd: ROOT, encoding: 'utf8', timeout: 0, stdio: ['ignore', 'pipe', 'pipe'] });
  } catch (e) {
    // even on nonzero exit, capture stdout
    if (e.stdout) return e.stdout.toString();
    throw e;
  }
}

// ── prompt builders ──
function buildPrompt(role, ctx) {
  const file = path.join(AGENT, 'prompts', role + '.md');
  let base = readFileSync(file, 'utf8');
  if (role === 'implementer') {
    let issueInfo = ctx.issue.body || '(no body)';
    return `${base}\n\n# 本次任务\nIssue #${ctx.issue.number}: ${ctx.issue.title}\n\n## Issue 内容\n${issueInfo}\n\n分支: ${ctx.branch}\n${ctx.roundNote ? '\n## 上一轮要求\n' + ctx.roundNote : ''}`;
  }
  if (role === 'reviewer') {
    return `${base}\n\n# 本次任务\nReview PR #${ctx.pr} (对应 issue #${ctx.issue.number})。`;
  }
  return base;
}

// ── state machine per issue ──
async function processIssue(issue, force = false) {
  const s = state.load();
  const cur = s[issue.number];
  const terminal = ['merged', 'rejected', 'failed'];
  if (cur && terminal.includes(cur.status) && !force) return { skipped: cur.status };

  const branch = cur?.branch || `agent/${issue.number}`;
  let pr = prForBranch(branch) || openPrFor(issue.number, branch) || (cur?.pr ? { number: cur.pr } : null);

  // 1) IMPLEMENT
  if (!cur || (cur.status === 'pr_open' && (!pr || force))) {
    if (pr && !force && cur) {
      s[issue.number] = { ...cur, status: 'reviewing' };
      state.save(s);
    } else {
      pr = await runImplementer(issue);
      if (!pr) return { skipped: 'waiting_pr' };
    }
  }
  pr = prForBranch(branch) || openPrFor(issue.number, branch) || (cur?.pr ? { number: cur.pr } : null);
  if (!pr) return { skipped: 'waiting_pr' };

  // 2) REVIEW (loop with A) — skip if already approved (resume path)
  const maxRounds = parseInt(process.env.MAX_REVIEW_ROUNDS || '3', 10);
  if (s[issue.number]?.status !== 'approved') {
    for (let r = 0; r < maxRounds; r++) {
      const { verdict } = await runReviewRound(issue.number, pr.number);
      if (verdict === 'APPROVE') { s[issue.number].status = 'approved'; state.save(s); break; }
      const note = `Agent B 要求修改（第 ${r + 1} 轮），请根据 PR #${pr.number} 上 Agent B 的评论修改。`;
      s[issue.number].status = 'reviewing';
      s[issue.number].round = r + 1;
      state.save(s);
      await runImplementer(issue, note);
      pr = prForBranch(branch) || pr;
    }
  }

  // 3) TEST
  const s2 = state.load();
  if (s2[issue.number]?.status !== 'approved') {
    // loop exhausted without approval → notify human
    commentOnIssue(issue.number, `⚠️ Agent B 与 Agent A 未能在 ${maxRounds} 轮内达成一致，请人工 review PR #${s2[issue.number]?.pr || pr.number}。`);
    s2[issue.number].status = 'needs_human';
    state.save(s2);
    return { skipped: 'needs_human' };
  }

  if (process.env.TEST_SKIP === '1') {
    commentOnIssue(issue.number, `🧪 TEST_SKIP=1，跳过功能测试。`);
    await finishMerge(issue.number, s2[issue.number].pr || pr.number);
    return { merged: true };
  }

  const maxFixes = parseInt(process.env.MAX_TEST_FIXES || '2', 10);
  for (let f = 0; f <= maxFixes; f++) {
    const t = runTester(issue.number, s2[issue.number].pr || pr.number, s2[issue.number].branch);
    if (t.pass) {
      await finishMerge(issue.number, s2[issue.number].pr || pr.number);
      return { merged: true };
    }
    if (f === maxFixes) {
      commentOnIssue(issue.number, `❌ 功能测试在 ${maxFixes + 1} 次尝试后仍失败，PR 保持开放，请人工处理。`);
      s2[issue.number].status = 'failed';
      state.save(s2);
      return { skipped: 'failed' };
    }
    const note = `Agent C 功能测试失败。请修复后重新推送。失败信息见 PR #${s2[issue.number].pr || pr.number} 评论。`;
    await runImplementer(issue, note);
  }
}

async function finishMerge(issueNum, prNum) {
  // GitHub forbids approving your own PR. If A/B share one account,
  // record B's approval as a comment instead of a formal review.
  try {
    gh(['pr', 'review', String(prNum), '--approve', '--body', '🤖 Agent B 已批准 + Agent C 测试通过']);
  } catch (e) {
    commentOnPr(prNum, '🤖 Agent B 已批准（同一 GitHub 账号无法提交正式 approve review，以本评论作为批准记录）+ Agent C 测试通过。');
  }
  try {
    gh(['pr', 'merge', String(prNum), '--squash', '--delete-branch']);
  } catch (e) {
    console.warn('merge failed:', e.message);
    commentOnIssue(issueNum, `❌ 自动合并失败（可能受分支保护限制）：${e.message}. 请手动合并 PR #${prNum}。`);
    const sf = state.load();
    sf[issueNum].status = 'needs_human';
    state.save(sf);
    log(issueNum, '状态: needs_human (merge blocked)');
    return;
  }
  const s = state.load();
  s[issueNum].status = 'merged';
  state.save(s);
  commentOnIssue(issueNum, `🎉 已合并 (PR #${prNum})。issue 将被自动关闭。`);
  log(issueNum, '状态: merged');
}

// ── CLI ──
async function main() {
  const cmd = process.argv[2] || 'watch';
  const once = process.argv.includes('--once');
  mkdirSync(LOG_DIR, { recursive: true });

  if (cmd === 'status') {
    console.log(JSON.stringify(state.load(), null, 2));
    return;
  }

  if (cmd === 'run') {
    const i = process.argv.indexOf('--issue');
    const force = process.argv.includes('--force');
    const issueNum = i >= 0 ? process.argv[i + 1] : null;
    const list = issueNum ? [{ number: issueNum, title: '', body: '' }] : approvedIssues();
    for (const issue of list) {
      const full = issueNum
        ? (await gh(['issue', 'view', String(issueNum), '--json', 'number,title,body']))
        : issue;
      await processIssue(typeof full === 'string' ? JSON.parse(full) : full, force);
    }
    return;
  }

  if (cmd === 'test') {
    const i = process.argv.indexOf('--issue');
    const issueNum = process.argv[i + 1];
    const s = state.load();
    const pr = openPrFor(issueNum) || { number: s[issueNum]?.pr };
    const t = runTester(issueNum, pr.number, s[issueNum].branch);
    console.log(t.pass ? 'PASS' : 'FAIL');
    return;
  }

  // watch loop
  console.log('👀 agent pipeline watch — Ctrl-C 退出');
  for (;;) {
    const issues = approvedIssues();
    const s = state.load();
    const pending = issues.filter(it => {
      const c = s[it.number];
      return !c || !['merged', 'failed', 'needs_human', 'rejected'].includes(c.status);
    });
    if (pending.length === 0) {
      console.log(`(idle) 等待新的 agent-approved issue …`);
      if (once) return;
      await new Promise(r => setTimeout(r, 30_000));
      continue;
    }
    for (const issue of pending) {
      console.log(`▶ 处理 issue #${issue.number}: ${issue.title}`);
      await processIssue(issue);
    }
    if (once) return;
  }
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  main().catch(e => { console.error(e); process.exit(1); });
}

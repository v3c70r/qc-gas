#!/usr/bin/env node
// ══════════════════════════════════════════════════════════════════
//  Multi-agent loop — local orchestrator (v1)
//
//  Roles:
//    A implementer  — reads approved issue, implements, opens PR
//    B reviewer     — reviews PR diff, posts comments, discusses w/ A
//    C tester       — builds & runs the repo's build/test scripts, merges
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
//    AGENT_BUILD_CMD / AGENT_TEST_CMD  Agent C's build/test commands
//                                   (default `npm run build` / `npm test`;
//                                    empty string = skip that step; non-npm
//                                    repos MUST set them, e.g. `make build`,
//                                    `pytest -q`)
//    AGENT_PI_TIMEOUT_MS            hard timeout per pi invocation (default
//                                   50 min; 0 = disabled). A hung model API
//                                   used to block a pass forever (issue #104).
//                                   Per-phase overrides: AGENT_PI_TIMEOUT_IMPL_MS,
//                                   AGENT_PI_TIMEOUT_REVIEW_MS,
//                                   AGENT_PI_TIMEOUT_TESTER_MS.
//    AGENT_PI_HEARTBEAT_MS          heartbeat period logged while pi runs
//                                   (default 60000, 0 = off)
// ══════════════════════════════════════════════════════════════════
import { mkdirSync, readFileSync, writeFileSync, existsSync, copyFileSync, symlinkSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { PiTimeoutError, acquireWatchLock, clearBlockedState, detectBase, formatPiHeartbeat, isRejectedIssue, markPiTimeout, mergeIssueState, piTimeoutLogLine, piTimeoutNote, prepareImplementerBranch, releaseWatchLock, resolvePi, resolvePiHeartbeatMs, resolvePiTimeoutMs, resolveTesterCommands, runPiWithTimeout, selectPendingIssues, sh, syncRemoteHead } from './lib.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const AGENT = path.join(ROOT, '.agent');
const STATE_FILE = path.join(AGENT, 'state.json');
const LOG_DIR = path.join(AGENT, 'logs');
const TMP = path.join(AGENT, 'tmp');
const LABEL_APPROVED = 'agent-approved';
const BASE = detectBase();

// ── upstream feedback (self-improvement of the agent-dev-team skill) ──
// .agent/config.json: { "upstream": "<owner>/<repo>", "feedbackOptIn": true }

// ── issue dependencies ──
// Issues may declare prerequisites in their body, e.g.
//   "Depends on: #12, #13"  /  "Blocked by: #12"  /  "阻塞于 #12"
// The pipeline refuses to start such an issue while any prerequisite is open,
// so a freshly created batch with dependencies can be auto-approved safely.
function parseDependencies(body) {
  const m = String(body || '').match(/^\s*(?:Depends on|Blocked by|Depends|阻塞于|依赖于|依赖)\s*[:：]?\s*((?:#\d+[\s,，、]*)+)/im);
  if (!m) return [];
  return [...m[1].matchAll(/#(\d+)/g)].map(x => parseInt(x[1], 10));
}
function openDependencies(issue) {
  const deps = parseDependencies(issue && issue.body);
  if (!deps.length) return [];
  const open = [];
  for (const n of deps) {
    if (n === Number(issue.number)) continue;
    try {
      const st = ghJson(['issue', 'view', String(n), '--json', 'state,title']);
      if (st.state === 'OPEN') open.push({ number: n, title: st.title });
    } catch { /* unknown issue → treat as non-blocking */ }
  }
  return open;
}

function loadConfig() {
  try { return JSON.parse(readFileSync(path.join(AGENT, 'config.json'), 'utf8')); } catch { return {}; }
}
function reportGap(title, body) {
  const cfg = loadConfig();
  if (!cfg.feedbackOptIn || !cfg.upstream) return;
  try {
    // anti-spam: at most 3 feedback issues per rolling day
    const since = new Date(Date.now() - 864e5).toISOString().slice(0, 10);
    const recent = ghJson(['issue', 'list', '-R', cfg.upstream, '--state', 'all',
      '--search', `[skill-feedback] created:>=${since}`, '--limit', '10', '--json', 'number']);
    if (recent.length >= 3) { console.warn('[feedback] daily cap reached, skip'); return; }
    const full = `${body}\n\n---\n_自动反馈来自 agent-dev-team 流水线（opt-in）。不应包含用户代码；如包含请维护者删除。 ${new Date().toISOString()}_`;
    try {
      gh(['issue', 'create', '-R', cfg.upstream, '--label', 'skill-feedback', '--title', `[skill-feedback] ${title}`, '--body', full]);
    } catch {
      // most users lack label rights upstream — retry without label
      gh(['issue', 'create', '-R', cfg.upstream, '--title', `[skill-feedback] ${title}`, '--body', full]);
    }
    console.warn(`[feedback] filed upstream: [skill-feedback] ${title}`);
  } catch (e) { console.warn('[feedback] failed:', e.message); }
}

// ── tiny helpers ──
function ghJson(args) { return JSON.parse(gh(args)); }
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
// ── 下发前置防线：任何 GitHub 正文在发出前扫描令牌特征 ──
// 2026-10-16 真实事故：评论正文里的 `$(gh auth token --user <owner>)` 被 shell 展开成
// **真令牌**并贴进 PR 评论 → GitHub 秘密扫描自动吊销 → 全链路 401，流水线卡死。
// 提示词禁令可能被绕过，所以必须在**代码层**硬拦：命中即拒绝发送（宁可不发评论）。
const SECRET_PATTERNS = [
  /\bgh[pousr]_[A-Za-z0-9]{16,}\b/,        // GitHub OAuth / PAT / user / server token
  /\bgithub_pat_[A-Za-z0-9_]{20,}\b/,      // fine-grained PAT
  /\bglpat-[A-Za-z0-9_-]{16,}\b/,          // GitLab
  /\bsk-[A-Za-z0-9_-]{16,}\b/,             // OpenAI-style
  /\bAKIA[0-9A-Z]{16}\b/,                  // AWS access key id
  /\bxox[baprs]-[A-Za-z0-9-]{10,}\b/,      // Slack
];
function assertNoSecrets(text, where = 'GitHub 正文') {
  const str = String(text ?? '');
  for (const re of SECRET_PATTERNS) {
    const m = str.match(re);
    if (m) {
      // 错误信息只暴露前 6 位：不要把泄漏内容再写进日志/工单
      throw new Error(`拒绝发送 ${where}：正文含疑似凭据（${m[0].slice(0, 6)}…，len=${m[0].length}）；移除后重试`);
    }
  }
  return str;
}
function commentOnIssue(num, body) { gh(['issue', 'comment', String(num), '--body', assertNoSecrets(body, `issue #${num} 评论`)]); }
function commentOnPr(num, body) { gh(['pr', 'comment', String(num), '--body', assertNoSecrets(body, `PR #${num} 评论`)]); }

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
  // Fix rounds MUST continue on the PR's existing branch; only the first round
  // branches off base. `git checkout -B` on an existing branch would reset it
  // back to base (lesson #18), so branch prep lives in a tested helper.
  const { reused } = prepareImplementerBranch({ branch, base: BASE });
  log(issue.number, `A: 开始实现 (branch=${branch}, ${reused ? `修复轮：基于 PR 现有分支 origin/${branch}` : `首轮：基于 origin/${BASE}`}, model=${describeModel(IMPL)}) ${roundNote ? '——' + roundNote : ''}`);
  const out = await runPi(`agent-issue-${issue.number}`, prompt, branch, IMPL, {
    phase: 'impl',
    onHeartbeat: hb => log(issue.number, `A: ${formatPiHeartbeat(hb)}`),
  });
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
    gh(['pr', 'create', '--base', BASE, '--head', branch, '--title', `Agent: ${issue.title}`, '--body', assertNoSecrets(body, 'PR 正文')]);
    for (let i = 0; i < 5 && !pr; i++) {
      await new Promise(r => setTimeout(r, 2000));
      pr = prForBranch(branch);
    }
  }
  if (pr) {
    commentOnPr(pr.number, `🤖 **Agent A 完成实现**（${describeModel(IMPL)}），等待 Agent B review（${describeModel(REVIEW)}）。`);
    // merge, never replace — `round`/`fixes` recorded by earlier rounds must survive
    s[issue.number] = mergeIssueState(s[issue.number], { status: 'pr_open', pr: pr.number, branch, title: issue.title });
  } else {
    s[issue.number] = mergeIssueState(s[issue.number], { status: 'pr_open', pr: null, branch, title: issue.title });
    log(issue.number, 'PR 尚未可查，稍后按分支重查');
  }
  state.save(s);
  return pr;
}

// ── Role B: reviewer (round-trip discussion with A) ──

/**
 * Parse the reviewer's verdict robustly.
 * The reviewer is asked for an English `RESULT: APPROVE|REQUEST_CHANGES` sentinel,
 * but models (e.g. GLM) sometimes answer in Chinese — "批准/通过" vs "要求修改" —
 * which previously defaulted to REQUEST_CHANGES and burned all review rounds.
 * We therefore scan for both languages and trust the LAST signal in the output.
 */
function parseReviewVerdict(out) {
  const text = String(out || '');
  const approve = /(RESULT:\s*APPROVE\b|LGTM\b|批准|予以批准|通过)/gi;
  const changes = /(RESULT:\s*REQUEST_CHANGES\b|REQUEST_CHANGES\b|要求修改|需要修改|需要更改|建议修改|不予批准|未批准|不批准|不通过)/gi;
  const last = (re) => { let m, i = -1; while ((m = re.exec(text)) !== null) i = m.index + m[0].length; return i; };
  const la = last(approve);
  const lc = last(changes);
  const sentinel = /RESULT:\s*(APPROVE|REQUEST_CHANGES)/i.test(text);
  if (la === -1 && lc === -1) return { verdict: 'REQUEST_CHANGES', source: 'no-signal' };
  if (la > lc) return { verdict: 'APPROVE', source: sentinel ? 'sentinel' : 'heuristic' };
  return { verdict: 'REQUEST_CHANGES', source: sentinel ? 'sentinel' : 'heuristic' };
}

// Model outages (billing/quota/rate-limit) must not dead-end the loop:
// fall back to another model for this round, leave a paper trail, and report
// the gap upstream (opt-in). Discovered live when the review model ran out of credit.
function isModelUnavailable(err) {
  const s = String((err && (err.stderr || err.stdout || err.message)) || '');
  return /\b429\b|insufficient|balance|quota|无可用资源|余额不足|欠费|rate.?limit|too many requests|capacity|overload/i.test(s);
}

async function runReviewRound(issueNum, prNum) {
  const prompt = buildPrompt('reviewer', { issue: { number: issueNum }, pr: prNum });
  const heartbeatNote = { phase: 'review', onHeartbeat: hb => log(issueNum, `B: ${formatPiHeartbeat(hb)}`) };
  let out;
  try {
    out = await runPi(`agent-review-${prNum}`, prompt, null, REVIEW, heartbeatNote);
  } catch (err) {
    if (!isModelUnavailable(err)) throw err;
    const fallback = REVIEW_FALLBACK;
    const detail = String((err.stderr || err.message || '')).slice(0, 300);
    console.warn(`[review] ${describeModel(REVIEW)} unavailable → fallback ${describeModel(fallback)}: ${detail}`);
    log(issueNum, `B: 模型不可用（${describeModel(REVIEW)}）→ 回退到 ${describeModel(fallback)}`);
    try { commentOnPr(prNum, `⚠️ **审查模型不可用**（\`${describeModel(REVIEW)}\`）：\`${detail.replace(/\n/g, ' ')}\`\n\n已自动回退到 \`${describeModel(fallback)}\` 完成本轮审查（不影响合并）。\n长期方案：为账户充值，或设置 \`REVIEW_PROVIDER\`/\`REVIEW_MODEL\` 指向可用模型。`); } catch {}
    reportGap(`审查模型不可用（${describeModel(REVIEW)}）`, `## 现象\n审查轮次调用模型失败，错误被判定为额度/限流类。\n\n## 模型\n- 主: ${describeModel(REVIEW)}\n- 回退: ${describeModel(fallback)}\n\n## 错误\n\`\`\`\n${detail}\n\`\`\`\n\n## 建议\n模板已自动回退；可考虑在 doctor 中预检模型额度（\`pi auth check\` 不检测余额），或支持多模型候选列表。`);
    out = await runPi(`agent-review-${prNum}`, prompt, null, fallback, heartbeatNote);
  }
  const { verdict, source } = parseReviewVerdict(out);
  log(issueNum, `B: verdict=${verdict} (${source}, model=${describeModel(REVIEW)})`);
  log(issueNum, `B: review 输出:\n${out.split('\n').slice(-30).join('\n')}`);
  return { verdict, out, source };
}

// ── Role C: tester (functional tests) ──

/** Human-readable report of which commands Agent C will actually run. */
function describeTesterCommands(cmds) {
  const line = (label, v, key) => (v ? `- ${label}: \`${v}\`` : `- ${label}: 已跳过（\`${key}\` 为空）`);
  return [line('build', cmds.build, 'AGENT_BUILD_CMD'), line('test', cmds.test, 'AGENT_TEST_CMD')].join('\n');
}

function runTester(issueNum, prNum, branch) {
  log(issueNum, `C: 开始功能测试 on ${branch}`);
  // Commands are shell strings (so `pytest -q` / `make build` work) and are
  // reported verbatim in the PR comment — the claim must equal what ran (lesson #17).
  const cmds = resolveTesterCommands();
  const ran = describeTesterCommands(cmds);
  const nothingToRun = !cmds.build && !cmds.test;
  const wt = path.join(TMP, `wt-${issueNum}`);
  rmSync(wt, { recursive: true, force: true });
  mkdirSync(TMP, { recursive: true });
  // Use a DETACHED worktree at the remote branch: the agent branch is often
  // still checked out in the main worktree, which makes `worktree add <branch>`
  // fail with "already used by worktree". Detaching avoids the conflict.
  try { sh(['git', 'fetch', 'origin', branch, '--quiet']); } catch {}
  let added = false;
  try {
    try {
      sh(['git', 'worktree', 'add', '--detach', wt, `origin/${branch}`]);
      added = true;
    } catch {
      sh(['git', 'worktree', 'add', '--detach', wt, branch]);
      added = true;
    }
    // reuse node_modules via symlink when the repo has them (vite/esbuild tolerate it).
    // Repos without dependencies (e.g. this template repo) simply skip this.
    const rootModules = path.join(ROOT, 'node_modules');
    if (existsSync(rootModules) && !existsSync(path.join(wt, 'node_modules'))) {
      symlinkSync(rootModules, path.join(wt, 'node_modules'), 'dir');
    }
    if (process.env.TEST_ENV_FILE && existsSync(process.env.TEST_ENV_FILE)) copyFileSync(process.env.TEST_ENV_FILE, path.join(wt, '.env'));
    if (cmds.build) sh(['bash', '-lc', cmds.build], { cwd: wt });
    const testOut = cmds.test ? sh(['bash', '-lc', cmds.test], { cwd: wt }) : '';
    const report = testOut.split('\n').slice(-60).join('\n');
    log(issueNum, `C: 测试通过 ✅（${ran}）\n${report}`);
    commentOnPr(prNum, `🤖 **Agent C 功能测试通过** ✅${nothingToRun ? '（未配置构建/测试命令，已跳过执行）' : '（构建 + 测试套件通过）'}\n\n实际执行：\n${ran}`);
    return { pass: true };
  } catch (e) {
    const report = (e.stdout || e.message || '').split('\n').slice(-60).join('\n');
    log(issueNum, `C: 测试失败 ❌\n${report}`);
    commentOnPr(prNum, `🤖 **Agent C 功能测试失败** ❌\n\n实际执行：\n${ran}\n\n\`\`\`\n${report.slice(0, 3000)}\n\`\`\``);
    return { pass: false };
  } finally {
    if (added) { try { sh(['git', 'worktree', 'remove', wt, '--force']); } catch {} }
    try { sh(['git', 'worktree', 'prune']); } catch {}
    rmSync(wt, { recursive: true, force: true });
  }
}

// ── shared pi invocation ──
// ── model routing per role ──
// Agent A (implementer) uses pi's default model unless IMPL_PROVIDER/IMPL_MODEL are set.
// Agent B (reviewer) uses a different model for an independent perspective.
// model routing: env wins, then .agent/config.json, then built-in defaults
const CFG_JSON = loadConfig();
const REVIEW = {
  provider: process.env.REVIEW_PROVIDER || CFG_JSON.review?.provider || 'zai-coding-cn',
  model: process.env.REVIEW_MODEL || CFG_JSON.review?.model || 'glm-5.3',
};
const REVIEW_FALLBACK = {
  provider: process.env.REVIEW_FALLBACK_PROVIDER || CFG_JSON.reviewFallback?.provider || '',
  model: process.env.REVIEW_FALLBACK_MODEL || CFG_JSON.reviewFallback?.model || '',
};
const IMPL = {
  provider: process.env.IMPL_PROVIDER || '',
  model: process.env.IMPL_MODEL || '',
};
function describeModel(m) { return m.provider && m.model ? `${m.provider}/${m.model}` : '(pi default)'; }

function runPi(sessionId, prompt, branchHint, model = {}, { phase = 'impl', onHeartbeat } = {}) {
  const cwdNote = branchHint ? `当前分支应为 agent 分支（已在本地 checkout）。` : '';
  const extra = prompt; // full system prompt already assembled by buildPrompt
  mkdirSync(TMP, { recursive: true });
  const pfile = path.join(TMP, `${sessionId}.prompt.md`);
  writeFileSync(pfile, extra);
  const args = ['-p', '--mode', 'text', '--session-id', sessionId,
    ...(model.provider ? ['--provider', model.provider] : []),
    ...(model.model ? ['--model', model.model] : []),
    '--system-prompt', `You are part of an autonomous agent loop operating on the GitHub repo (cwd). ${cwdNote} Follow the instructions below strictly.`,
    '--append-system-prompt', pfile,
    '--no-approve',
    // positional user message — pi needs an actual message to act on
    '现在执行上面给出的完整任务：读所需上下文，用工具完成所有步骤，然后输出总结并结束。'];
  const bin = resolvePi();
  const timeoutMs = resolvePiTimeoutMs(phase);
  const heartbeatMs = resolvePiHeartbeatMs();
  // A timeout here throws PiTimeoutError, which `processIssue` turns into
  // needs_human + an issue comment instead of blocking the whole pass forever
  // (issue #104). Nonzero exit with stdout still resolves to that stdout, so the
  // success/failure semantics of the old execFileSync call are unchanged.
  return runPiWithTimeout({ bin, args, cwd: ROOT, timeoutMs, heartbeatMs, onHeartbeat })
    .then(res => res.stdout);
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
  // remember where we started so we can restore the user's branch afterwards
  let startBranch = null;
  try { startBranch = sh(['git', 'rev-parse', '--abbrev-ref', 'HEAD']); } catch {}
  try {
    return await processIssueInner(issue, force);
  } catch (err) {
    // A hung pi (model API never answers) must END the pass loudly: log the
    // timeout, mark the issue needs_human, tell the human how to recover.
    // Previously this path did not exist because the call never returned.
    if (err instanceof PiTimeoutError || err?.code === 'PI_TIMEOUT') return onPiTimeout(issue, err);
    throw err;
  } finally {
    if (startBranch && startBranch !== 'HEAD') {
      try { sh(['git', 'checkout', startBranch]); } catch { /* leave as-is */ }
    }
  }
}

/** Timeout consequences for one issue: needs_human + log + issue comment. */
function onPiTimeout(issue, err) {
  log(issue.number, piTimeoutLogLine(err.timeoutMs));
  state.save(markPiTimeout(state.load(), issue.number, err));
  try { commentOnIssue(issue.number, piTimeoutNote(err.timeoutMs)); }
  catch (e) { log(issue.number, `超时通知评论发送失败（不影响 needs_human）: ${e.message}`); }
  return { skipped: 'needs_human' };
}

async function processIssueInner(issue, force = false) {
  const s = state.load();
  // /reject 是硬性否决（issue #14）：标签驱动的取活只加否定标签不等于取消，
  // 必须把 agent-rejected 当作硬排除，并在实现/合并前落成终态 `rejected`。
  // 边界：若实现已在运行，本函数不会中断正在跑的 pi 会话（不在本 PR 范围），
  // 但下一轮不再接管，PR 也不会被自动合并（见 docs/lessons.md #19）。
  if (isRejectedIssue(issue) && !force) {
    s[issue.number] = mergeIssueState(s[issue.number], { status: 'rejected', title: issue.title });
    state.save(s);
    log(issue.number, '状态: rejected（issue 带 agent-rejected 标签，硬性跳过实现与合并）');
    return { skipped: 'rejected' };
  }
  // `let` because the dependency gate below may DELETE this entry when a blocked
  // issue becomes unblocked — the in-memory handle must follow (issue #75).
  let cur = s[issue.number];
  const terminal = ['merged', 'rejected', 'failed'];
  if (cur && terminal.includes(cur.status) && !force) return { skipped: cur.status };

  const branch = cur?.branch || `agent/${issue.number}`;
  let pr = prForBranch(branch) || openPrFor(issue.number, branch) || (cur?.pr ? { number: cur.pr } : null);

  // 0) DEPENDENCIES — never start work whose prerequisites are still open
  if (!pr) {
    // throttle dependency re-checks (the watcher ticks every 30s)
    const sT = state.load();
    const lastCheck = sT[issue.number]?.blockedCheckedAt ? Date.parse(sT[issue.number].blockedCheckedAt) : 0;
    const blockedFresh = sT[issue.number]?.status === 'blocked' && (Date.now() - lastCheck) < 5 * 60_000;
    if (blockedFresh) return { skipped: 'blocked' };
    const blockedBy = openDependencies(issue);
    if (blockedBy.length) {
      const list = blockedBy.map(d => `#${d.number} (${d.title})`).join(', ');
      log(issue.number, `blocked: 等待依赖 ${list}`);
      const sB = state.load();
      if (!sB[issue.number]) sB[issue.number] = {};
      const firstNotice = !sB[issue.number].blockedNotified;
      sB[issue.number].status = 'blocked';
      sB[issue.number].blockedBy = blockedBy.map(d => d.number);
      sB[issue.number].blockedCheckedAt = new Date().toISOString();
      if (firstNotice) sB[issue.number].blockedNotified = true;
      state.save(sB);
      if (firstNotice) {
        try { commentOnIssue(issue.number, `⏳ 该 issue 声明了依赖，暂不开始实现：等待 ${list} 关闭。\n\n依赖完成后流水线会自动接手（无需操作）。`); } catch {}
      }
      return { skipped: 'blocked' };
    }
  }

  // dependencies satisfied → FORGET the blocked entry (fresh "to implement").
  // Writing `pr_open` here claimed a PR that does not exist yet; correctness
  // depended on the next tick hitting the `status === 'pr_open' && !pr` special
  // case below. Deleting is the honest semantic and does not rely on coincidence
  // (issue #75).
  {
    const sc = state.load();
    const cleared = clearBlockedState(sc, issue.number);
    if (cleared.changed) {
      state.save(cleared.state);
      delete s[issue.number];
      cur = s[issue.number];
      log(issue.number, '依赖已满足，进入实现（清除 blocked 状态条目，等价于全新待实现）');
      try { commentOnIssue(issue.number, '✅ 依赖已满足，开始实现。'); } catch {}
    }
  }

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
  let lastReviewOut = '';
  let lastReviewVerdict = 'n/a';
  let fresh = state.load();
  if (!fresh[issue.number]) fresh[issue.number] = {}; // ensure key exists
  if (fresh[issue.number].status !== 'approved') {
    for (let r = 0; r < maxRounds; r++) {
      fresh = state.load();
      if (!fresh[issue.number]) fresh[issue.number] = {};
      const res = await runReviewRound(issue.number, pr.number);
      lastReviewOut = res.out;
      lastReviewVerdict = res.verdict;
      if (res.verdict === 'APPROVE') { fresh[issue.number].status = 'approved'; state.save(fresh); break; }
      const note = `Agent B 要求修改（第 ${r + 1} 轮），请根据 PR #${pr.number} 上 Agent B 的评论修改。`;
      fresh[issue.number].status = 'reviewing';
      fresh[issue.number].round = r + 1;
      state.save(fresh);
      await runImplementer(issue, note);
      pr = prForBranch(branch) || pr;
    }
  }

  // 3) TEST
  const s2 = state.load();
  if (!s2[issue.number] || s2[issue.number].status !== 'approved') {
    // loop exhausted without approval → notify human (include the last verdict so a
    // human can immediately see whether the reviewer had actually approved)
    if (!s2[issue.number]) s2[issue.number] = {};
    const excerpt = String(lastReviewOut || '').split('\n').filter(Boolean).slice(-6).join('\n').slice(0, 900);
    commentOnIssue(issue.number, `⚠️ Agent B 与 Agent A 未能在 ${maxRounds} 轮内达成一致，请人工 review PR #${s2[issue.number]?.pr || pr.number}。\n\n` +
      `自动解析的最后一轮结论：\`${lastReviewVerdict}\`\n\n` +
      (excerpt ? `<details><summary>最后一轮 reviewer 输出（节选）</summary>\n\n\`\`\`\n${excerpt}\n\`\`\`\n</details>` : ''));
    s2[issue.number].status = 'needs_human';
    s2[issue.number].lastVerdict = lastReviewVerdict;
    state.save(s2);
    return { skipped: 'needs_human' };
  }

  if (process.env.TEST_SKIP === '1') {
    commentOnIssue(issue.number, `🧪 TEST_SKIP=1，跳过功能测试。`);
    return await finishMerge(issue.number, s2[issue.number].pr || pr.number);
  }

  const maxFixes = parseInt(process.env.MAX_TEST_FIXES || '2', 10);
  for (let f = 0; f <= maxFixes; f++) {
    const t = runTester(issue.number, s2[issue.number].pr || pr.number, s2[issue.number].branch);
    if (t.pass) {
      return await finishMerge(issue.number, s2[issue.number].pr || pr.number);
    }
    if (f === maxFixes) {
      commentOnIssue(issue.number, `❌ 功能测试在 ${maxFixes + 1} 次尝试后仍失败，PR 保持开放，请人工处理。`);
      s2[issue.number].status = 'failed';
      state.save(s2);
      return { skipped: 'failed' };
    }
    const note = `Agent C 功能测试失败。请修复后重新推送。失败信息见 PR #${s2[issue.number].pr || pr.number} 评论。`;
    // `round` counts review rounds, `fixes` counts test-driven fix rounds.
    // Save BEFORE runImplementer so its merge (mergeIssueState) reads the new value.
    s2[issue.number].fixes = (s2[issue.number].fixes || 0) + 1;
    state.save(s2);
    await runImplementer(issue, note);
  }
}

async function finishMerge(issueNum, prNum) {
  // 合并不可逆 —— 在真正 `gh pr merge` 之前重新取一次 issue 标签确认 /reject。
  // review + test 窗口长达数十分钟，正是用户最想反悔的时候；只在取活时检查
  // 无法覆盖"实现已经在跑"的 in-flight 路径（issue #14 明确承诺"PR 不会被
  // 自动合并"）。取不到标签 = 无法确认 → 失败关闭，绝不做不可逆动作。
  let freshIssue;
  try {
    freshIssue = ghJson(['issue', 'view', String(issueNum), '--json', 'labels']);
  } catch (e) {
    const sf = state.load();
    sf[issueNum] = mergeIssueState(sf[issueNum], { status: 'needs_human' });
    state.save(sf);
    log(issueNum, `状态: needs_human（合并前无法确认 /reject 标签: ${e.message}）`);
    try { commentOnIssue(issueNum, `⚠️ 合并前无法确认 /reject 状态（\`${e.message}\`），为安全起见未自动合并 PR #${prNum}，请人工处理。`); } catch {}
    return { skipped: 'needs_human' };
  }
  if (isRejectedIssue(freshIssue)) {
    const sr = state.load();
    sr[issueNum] = mergeIssueState(sr[issueNum], { status: 'rejected' });
    state.save(sr);
    log(issueNum, `状态: rejected（合并前检查到 /reject，已阻止自动合并 PR #${prNum}）`);
    try { commentOnIssue(issueNum, `🛑 合并前检测到 /reject，已**阻止自动合并** PR #${prNum}。该 PR 保持开放，等待人工处理。`); } catch {}
    return { skipped: 'rejected' };
  }
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
    return { skipped: 'needs_human' };
  }
  const s = state.load();
  if (!s[issueNum]) s[issueNum] = {};
  s[issueNum].status = 'merged';
  state.save(s);
  commentOnIssue(issueNum, `🎉 已合并 (PR #${prNum})。issue 将被自动关闭。`);
  log(issueNum, '状态: merged');
  return { merged: true };
}

// ── CLI ──
async function main() {
  const cmd = process.argv[2] || 'watch';
  const once = process.argv.includes('--once');
  mkdirSync(LOG_DIR, { recursive: true });

  // Make the detected base visible (issue #99: a stale `origin/HEAD → agent/N`
  // used to send `gh pr create --base agent/N` into guaranteed failure) and pin
  // the local remote-HEAD cache to it so later runs cannot regress.
  console.log(`[base] 使用基线分支: ${BASE}${process.env.AGENT_BASE ? '（AGENT_BASE 覆盖）' : ''}`);
  syncRemoteHead(BASE);

  if (cmd === 'feedback') {
    // manual gap report: node .agent/pipeline.mjs feedback "<title>" [body]
    const cfg = loadConfig();
    if (!cfg.upstream) { console.error('no upstream configured in .agent/config.json'); process.exit(2); }
    if (!cfg.feedbackOptIn && !process.argv.includes('--force')) {
      console.error('feedbackOptIn=false（如需强制上报加 --force）'); process.exit(2);
    }
    const title = process.argv[3] || 'manual gap report';
    const body = process.argv[4] || '(no details provided)';
    reportGap(title, body);
    console.log('feedback submitted (or attempted) — see warnings above');
    return;
  }

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
        ? (await gh(['issue', 'view', String(issueNum), '--json', 'number,title,body,labels']))
        : issue;
      await processIssue(typeof full === 'string' ? JSON.parse(full) : full, force);
    }
    return;
  }

  if (cmd === 'review') {
    // Review-only: useful for testing the reviewer model or a manual re-review.
    //   node .agent/pipeline.mjs review --pr 12 [--issue 7]
    const pi = process.argv.indexOf('--pr');
    const prNum = pi >= 0 ? process.argv[pi + 1] : null;
    if (!prNum) { console.error('usage: pipeline.mjs review --pr <number> [--issue <number>]'); process.exit(2); }
    const ii = process.argv.indexOf('--issue');
    const issueNum = ii >= 0 ? process.argv[ii + 1] : 'unknown';
    console.log(`reviewing PR #${prNum} with ${describeModel(REVIEW)}`);
    const { verdict, out } = await runReviewRound(issueNum, prNum);
    console.log(`\nVERDICT: ${verdict}\n`);
    console.log(out.split('\n').slice(-20).join('\n'));
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

  // watch loop — designed to stay alive in tmux; only Ctrl-C / `--once` stops it
  //
  // SINGLE-INSTANCE LOCK (issue #75): two watchers in the same working tree
  // would race on checkout/reset/commit and destroy each other's implement
  // branch. Refuse to start if a live instance already holds the lock; take
  // over a stale lock left by a killed process.
  const LOCK_FILE = path.join(AGENT, 'watch.lock');
  const lock = acquireWatchLock(LOCK_FILE);
  if (!lock.acquired) {
    console.error(`❌ 已有实例在运行 (PID ${lock.pid})，退出以免两个 watcher 在同一工作区互相破坏。`);
    console.error(`   如确认该进程已停止，可删除 ${LOCK_FILE} 后重试。`);
    process.exit(1);
  }
  if (lock.tookOver) console.warn(`⚠️ 接管遗留的 watch 锁（原 PID 已不存在）`);
  const dropLock = () => releaseWatchLock(LOCK_FILE);
  process.on('exit', dropLock);
  process.on('SIGINT', () => { dropLock(); process.exit(130); });
  process.on('SIGTERM', () => { dropLock(); process.exit(143); });

  console.log('👀 agent pipeline watch — Ctrl-C 退出（tmux 中可常驻）');
  const ts = () => new Date().toISOString().replace('T', ' ').slice(0, 19);
  let pollFailures = 0;
  for (;;) {
    try {
      const issues = approvedIssues();
      const s = state.load();
      // 否决优先 + 终态排除（可测试的纯函数；见 templates/lib.mjs）
      const pending = selectPendingIssues(issues, s);
      if (pending.length === 0) {
        console.log(`[${ts()}] 💤 idle — 等待新的 agent-approved issue …`);
        pollFailures = 0;
        if (once) return;
        await new Promise(r => setTimeout(r, 30_000));
        continue;
      }
      for (const issue of pending) {
        console.log(`[${ts()}] ▶ 处理 issue #${issue.number}: ${issue.title}`);
        try {
          await processIssue(issue);
          console.log(`[${ts()}] ✔ issue #${issue.number} 本轮结束`);
        } catch (err) {
          // one bad issue must NOT kill the long-running watcher
          console.error(`[${ts()}] ✖ issue #${issue.number} 处理失败，循环继续：${err.message}`);
          log(issue.number, `循环捕获异常（不退出）:\n${err.stack || err.message}`);
          const sf = state.load();
          if (!sf[issue.number]) sf[issue.number] = {};
          sf[issue.number].status = 'needs_human';
          sf[issue.number].error = String(err.message);
          state.save(sf);
          try { commentOnIssue(issue.number, `⚠️ 处理该 issue 时出错，已交给人工：\`${err.message}\``); } catch {}
          // self-improvement: unexpected pipeline errors are exactly the gaps
          // the skill wants to learn about (opt-in, sanitized, rate-limited)
          reportGap(`流水线异常: ${String(err.message).slice(0, 120)}`,
            `## 现象\n处理 issue 时流水线抛出未预期异常。\n\n## 详情\n\
\`\`\`\n${String(err.stack || err.message).slice(0, 1500)}\n\
\`\`\`\n\n## 环坑提示\n如果这是模板本身的问题（而非用户仓库环境问题），请改进 agent-dev-team 模板并补充 docs/lessons.md。`);
        }
      }
      pollFailures = 0;
      if (once) return;
    } catch (err) {
      // transient gh/network failure → backoff and keep watching
      pollFailures += 1;
      const delay = Math.min(300_000, 30_000 * pollFailures);
      console.error(`[${ts()}] ✖ 轮询失败 (${pollFailures}): ${err.message} — ${delay / 1000}s 后重试`);
      if (once) throw err;
      await new Promise(r => setTimeout(r, delay));
    }
  }
}

export { assertNoSecrets, SECRET_PATTERNS };

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  main().catch(e => { console.error(e); process.exit(1); });
}

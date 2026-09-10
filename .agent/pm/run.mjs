#!/usr/bin/env node
// ══════════════════════════════════════════════════════════════════
//  Product Manager agent — weekly, DeepSeek off-peak, ≤ N proposals
//
//  Usage:
//    node .agent/pm/run.mjs --check        # is it due? how many slots left?
//    node .agent/pm/run.mjs                # run if due (weekly target)
//    node .agent/pm/run.mjs --force        # run now regardless of schedule
//    node .agent/pm/run.mjs --dry-run      # research + report, create NO issues
//
//  Env (defaults suit DeepSeek off-peak window UTC 16:30–00:30):
//    PM_PROVIDER=deepseek  PM_MODEL=deepseek-v4-flash
//    PM_WEEKDAY=1  PM_HOUR_UTC=17  PM_WEEKLY_CAP=5
//
//  Off-peak rationale: DeepSeek discounts apply during UTC 16:30–00:30;
//  Monday 17:00 UTC sits inside that window.
// ══════════════════════════════════════════════════════════════════
import { execFileSync, spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, readFileSync, writeFileSync, rmSync, readdirSync, statSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const PM_DIR = path.join(ROOT, '.agent', 'pm');
const STATE_FILE = path.join(PM_DIR, 'state.json');
const LOG_DIR = path.join(PM_DIR, 'logs');
const CONTEXT_FILE = path.join(PM_DIR, 'context.md');
const WT = path.join(PM_DIR, 'wt');
const LABEL = 'pm-proposal';

const CFG = {
  provider: process.env.PM_PROVIDER || 'deepseek',
  model: process.env.PM_MODEL || 'deepseek-v4-flash',
  weekday: parseInt(process.env.PM_WEEKDAY ?? '1', 10),      // 1 = Monday
  hourUtc: parseInt(process.env.PM_HOUR_UTC ?? '17', 10),
  cap: parseInt(process.env.PM_WEEKLY_CAP ?? '5', 10),
};

const args = process.argv.slice(2);
const FORCE = args.includes('--force');
const DRY = args.includes('--dry-run');
const CHECK = args.includes('--check');

// ── helpers ──
function sh(bin, argv, opts = {}) {
  return execFileSync(bin, argv, { cwd: opts.cwd || ROOT, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
}
function ghJson(argv) { return JSON.parse(sh('gh', argv)); }
function log(msg) { console.log(`[pm ${new Date().toISOString().replace('T', ' ').slice(0, 19)}] ${msg}`); }

/** load KEY=VALUE pairs from repo .env (gitignored) */
function loadDotEnv() {
  const p = path.join(ROOT, '.env');
  if (!existsSync(p)) return;
  for (const line of readFileSync(p, 'utf8').split('\n')) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/i);
    if (m && !process.env[m[1]]) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
  }
}

/** most recent weekly target instant (this week's PM_WEEKDAY at PM_HOUR_UTC) */
function weeklyTarget(now = new Date()) {
  const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate(), CFG.hourUtc, 0, 0));
  const delta = (d.getUTCDay() - CFG.weekday + 7) % 7;
  d.setUTCDate(d.getUTCDate() - delta);      // back to this week's target weekday
  if (d > now) d.setUTCDate(d.getUTCDate() - 7); // target not reached yet → previous week's
  return d;
}
function isDue(state) {
  if (FORCE) return true;
  const target = weeklyTarget();
  const last = state.lastRun ? new Date(state.lastRun) : null;
  return !last || last < target;
}
/** issues labelled pm-proposal created in the last 7 days */
function recentCount() {
  const since = new Date(Date.now() - 7 * 864e5).toISOString().slice(0, 10);
  try {
    const list = ghJson(['issue', 'list', '--state', 'all', `--label=${LABEL}`,
      '--search', `created:>=${since}`, '--limit', '50', '--json', 'number']);
    return list.length;
  } catch { return 0; }
}

// ── context bundle for the agent ──
function buildContext() {
  const parts = [];
  parts.push(`# Context bundle (generated ${new Date().toISOString()})\n`);
  parts.push('## package.json scripts\n```json\n' + JSON.stringify(JSON.parse(readFileSync(path.join(ROOT, 'package.json'), 'utf8')).scripts, null, 2) + '\n```\n');

  const tree = (dir, depth = 2) => {
    const out = [];
    const walk = (d, prefix, lvl) => {
      if (lvl > depth) return;
      for (const e of readdirSync(d, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
        if (['node_modules', '.git', 'dist', 'test-results', 'tmp', 'wt'].includes(e.name)) continue;
        const p = path.join(d, e.name);
        out.push(prefix + e.name + (e.isDirectory() ? '/' : ` (${(statSync(p).size / 1024).toFixed(1)}K)`));
        if (e.isDirectory()) walk(p, prefix + '  ', lvl + 1);
      }
    };
    walk(dir, '', 1);
    return out.join('\n');
  };
  parts.push('## File tree\n```\n' + tree(ROOT, 2) + '\n```\n');

  try { parts.push(`\n## Tests\n${readFileSync(path.join(ROOT, 'tests/app.spec.js'), 'utf8').match(/test\(/g)?.length || 0} playwright tests in tests/app.spec.js\n`); } catch {}

  const issueFields = 'number,title,state,createdAt,labels,body';
  try {
    const open = ghJson(['issue', 'list', '--state', 'open', '--limit', '40', '--json', issueFields]);
    parts.push('\n## Open issues\n' + open.map(i => `- #${i.number} [${(i.labels || []).map(l => l.name).join(',')}] ${i.title}`).join('\n') + '\n');
  } catch {}
  try {
    const closed = ghJson(['issue', 'list', '--state', 'closed', '--limit', '30', '--json', 'number,title,closedAt']);
    parts.push('\n## Recently closed issues (avoid duplicates)\n' + closed.map(i => `- #${i.number} ${i.title}`).join('\n') + '\n');
  } catch {}
  try {
    const prs = ghJson(['pr', 'list', '--state', 'merged', '--limit', '15', '--json', 'number,title,mergedAt']);
    parts.push('\n## Recently merged PRs\n' + prs.map(p => `- #${p.number} ${p.title}`).join('\n') + '\n');
  } catch {}
  try {
    const d = ghJson(['issue', 'list', '--state', 'all', `--label=${LABEL}`, '--limit', '30', '--json', 'number,title,state,createdAt']);
    parts.push('\n## Previously proposed pm-proposal issues\n' + (d.map(i => `- #${i.number} [${i.state}] ${i.title}`).join('\n') || '(none)') + '\n');
  } catch {}

  const out = parts.join('\n');
  writeFileSync(CONTEXT_FILE, out);
  return out;
}

// ── locate the real pi binary (npm prepends node_modules/.bin) ──
function resolvePi() {
  if (process.env.PI_BIN) return process.env.PI_BIN;
  for (const d of (process.env.PATH || '').split(path.delimiter)) {
    if (!d || d.includes('node_modules')) continue;
    const c = path.join(d, 'pi');
    if (existsSync(c)) return c;
  }
  return 'pi';
}

// ── main ──
function main() {
  loadDotEnv();
  mkdirSync(LOG_DIR, { recursive: true });
  const state = existsSync(STATE_FILE) ? JSON.parse(readFileSync(STATE_FILE, 'utf8')) : {};

  const used = recentCount();
  const slots = Math.max(0, CFG.cap - used);
  const due = isDue(state);

  if (CHECK) {
    const now = new Date();
    const cur = weeklyTarget(now);
    const next = now >= cur ? new Date(cur.getTime() + 7 * 864e5) : cur;
    console.log(JSON.stringify({
      now: now.toISOString(),
      schedule: `weekday=${CFG.weekday} ${CFG.hourUtc}:00 UTC`,
      currentWeekTarget: cur.toISOString(),
      nextTarget: next.toISOString(),
      lastRun: state.lastRun || null,
      due, usedThisWeek: used, cap: CFG.cap, slotsLeft: slots,
      model: `${CFG.provider}/${CFG.model}`,
      braveKey: !!process.env.BRAVE_API_KEY,
    }, null, 2));
    return;
  }

  if (!due) { log(`not due (next target ${weeklyTarget().toISOString()}). skip.`); return; }
  if (slots === 0) { log(`weekly cap reached (${used}/${CFG.cap}). skip.`); return; }
  if (!process.env.BRAVE_API_KEY) log('⚠️  BRAVE_API_KEY missing — research will be GitHub/curl only');

  log(`starting PM run — model=${CFG.provider}/${CFG.model}, slots=${slots}${DRY ? ' (dry-run)' : ''}`);

  // isolated worktree so the main worktree / issue pipeline is never disturbed
  rmSync(WT, { recursive: true, force: true });
  sh('git', ['fetch', 'origin', 'master', '--quiet']);
  sh('git', ['worktree', 'add', '--detach', WT, 'origin/master']);

  try {
    buildContext();

    const runtime = `\n\n---\n\n# 本次运行\n- 当前时间(UTC): ${new Date().toISOString()}\n- 本周剩余提案名额: **${slots}**（上限 ${CFG.cap}/周）\n- 你的长期记忆文件: \`docs/product-review.md\`（在工作目录内，请更新它）\n- 上下文包(绝对路径): \`${CONTEXT_FILE}\`\n- 工作目录: \`${WT}\`（这是本仓库的独立 worktree，可读全部代码）\n- 调研工具: \`.agents/skills/brave-search/search.sh\` / \`fetch.sh\`（BRAVE_API_KEY 已在环境变量中），以及 \`gh search repos\`\n${DRY ? '- ⚠️ DRY RUN：**不要**创建任何 GitHub issue，只在最终输出里列出候选提案。\n' : ''}`;

    const promptFile = path.join(PM_DIR, 'prompt.md');
    const piArgs = [
      '-p', '--mode', 'text',
      '--provider', CFG.provider, '--model', CFG.model,
      '--session-id', 'pm-agent-weekly',
      '--system-prompt', 'You are an autonomous weekly Product Manager agent. Follow the appended instructions strictly.',
      '--append-system-prompt', promptFile,
      '--exclude-tools', 'ask_user_question,review_loop',
      '--no-approve',
      `${readFileSync(promptFile, 'utf8').includes('Product Manager') ? '' : ''}现在执行本周产品调研任务。先更新 docs/product-review.md，再做竞品调研，最后${DRY ? '列出候选提案（不要建 issue）' : `创建最多 ${slots} 个 pm-proposal issue`}。`,
    ];

    log('invoking pi (this may take a few minutes)…');
    const res = spawnSync(resolvePi(), piArgs, { cwd: WT, encoding: 'utf8', timeout: 0, env: process.env });
    const out = (res.stdout || '') + (res.stderr ? `\n[stderr]\n${res.stderr}` : '');

    const stamp = new Date().toISOString().slice(0, 10);
    writeFileSync(path.join(LOG_DIR, `${stamp}.md`), `# PM run ${new Date().toISOString()}\n\nmodel: ${CFG.provider}/${CFG.model}\nslots: ${slots}\n\n---\n\n${out}\n`);
    log(`pi finished (exit=${res.status})`);

    // commit the updated product-review doc (docs-only ⇒ no deploy trigger)
    if (!DRY) {
      let committed = false;
      try {
        sh('git', ['add', 'docs/product-review.md'], { cwd: WT });
        sh('git', ['-c', 'user.name=pm-agent', '-c', 'user.email=pm-agent@users.noreply.github.com',
          'commit', '-m', 'docs: weekly product review update [pm]'], { cwd: WT });
        committed = true;
      } catch { log('no product-review change to commit'); }
      if (committed) {
        try { sh('git', ['push', 'origin', 'HEAD:master'], { cwd: WT }); log('pushed docs/product-review.md → master'); }
        catch (e) {
          log('push rejected, rebasing once…');
          try {
            sh('git', ['fetch', 'origin', 'master', '--quiet'], { cwd: WT });
            sh('git', ['rebase', 'origin/master'], { cwd: WT });
            sh('git', ['push', 'origin', 'HEAD:master'], { cwd: WT });
            log('pushed after rebase');
          } catch (e2) { log('push failed: ' + String(e2.message).slice(0, 200) + ' — doc update left in worktree'); }
        }
      }
    }

    // report created issues
    const created = (out.match(/https:\/\/github\.com\/[^\s)]+\/issues\/\d+/g) || []);
    if (!DRY) {
      state.lastRun = new Date().toISOString();
      state.lastSlots = slots;
      state.lastCreated = created;
      writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
    } else {
      // a dry run must NOT consume this week's real run slot
      state.lastDryRun = new Date().toISOString();
      writeFileSync(STATE_FILE, JSON.stringify(state, null, 2));
    }
    log(created.length ? `created: ${created.join(', ')}` : 'no issues created this run');
    log(`log: ${path.join(LOG_DIR, `${stamp}.md`)}`);
  } finally {
    try { sh('git', ['worktree', 'remove', WT, '--force']); } catch {}
    try { sh('git', ['worktree', 'prune']); } catch {}
    rmSync(WT, { recursive: true, force: true });
  }
}

main();

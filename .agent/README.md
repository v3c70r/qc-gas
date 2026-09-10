# Multi-Agent Issue Loop (v1)

A human-in-the-loop, multi-agent system driven by GitHub **issues** and **PRs**.

```
┌─ GitHub Actions (每 10 分钟) ───────────────────────────────┐
│ 1. 发现新 issue → 评论 @你 + 打标签 agent-seen              │
│ 2. 检测到 /approve → 打标签 agent-approved → 通知你         │
│ 3. /reject → 标签 agent-rejected，不再接管                  │
└────────────────────────────────────────────────────────────┘
                        │ (你回复 /approve)
                        ▼
┌─ 本机 pipeline  (node .agent/pipeline.mjs watch) ───────────┐
│ A 实现者 : 读 issue → checkout agent/N → 实现 → push → PR   │
│ B 审查者 : gh pr diff → 评论 PR → APPROVE / REQUEST_CHANGES │
│   └─(循环 ≤ MAX_REVIEW_ROUNDS, A 按评论修改再 push)          │
│ C 测试者 : worktree 构建 + Playwright → 通过→批准+合并       │
│   └─(失败 ≤ MAX_TEST_FIXES, A 修复)                          │
└────────────────────────────────────────────────────────────┘
```

## 角色划分

| 角色 | 执行 | 说明 |
|------|------|------|
| **Triage** (Actions) | 确定性脚本 | 轮询、通知、`/approve`→标签 |
| **Agent A** | pi 非交互 (本机) | 读 issue、实现、开 PR |
| **Agent B** | pi 非交互 (本机) | diff review、PR 评论讨论 |
| **Agent C** | 脚本 + Playwright | 构建 + 功能测试 → `gh pr merge` |

**共享记忆**：PR 评论（对话）+ `.agent/logs/issue-<N>.md`（每轮状态）。

## 使用

```bash
# 1) 确保 Actions workflow 已启用 (push 后自动)
# 2) 有人创建 issue → Actions 会 @ 你；回复 /approve
# 3) 在你的机器上启动本机调度：
npm run agent:watch          # 常驻：处理所有 agent-approved issues

# 常驻运行（推荐 tmux，防止 SSH 断开中断）
tmux new -s agent            # 新建会话
npm run agent:watch 2>&1 | tee -a .agent/watch.log   # 同时写日志
tmux detach                  # Ctrl-b 然后 d 脱离；tmux attach -t agent 回来

# 常用命令
npm run agent:watch -- --once    # 只跑一轮
npm run agent:status             # 查看状态机
node .agent/pipeline.mjs run --issue 5   # 处理单个 issue（--force 重做实现）
node .agent/pipeline.mjs test --issue 5  # 只跑 Agent C

# 选项
MAX_REVIEW_ROUNDS=5 npm run agent:watch   # review 讨论轮数上限 (默认3)
MAX_TEST_FIXES=1 npm run agent:watch      # 测试失败修复轮数 (默认2)
TEST_SKIP=1 npm run agent:watch           # 跳过测试直接合并 (demo)
TEST_ENV_FILE=$PWD/.env npm run agent:watch  # 注入 Mapbox token 供 Agent C 真实测试
PI_BIN=/path/to/pi npm run agent:watch    # 显式指定 pi 二进制
```

### 常驻行为说明

- `watch` 是无限循环，空闲时每 30s 轮询一次并打印带时间戳的心跳，**不会自己退出**。
- 停止方式：`Ctrl-C`，或使用 `--once` 跑一轮。
- **健壮性**：单个 issue 处理失败会被捕获 → 标记 `needs_human` 并继续循环；gh 网络/认证等轮询失败会指数退避重试（最多 5 分钟），不会拖垮进程。
- 仍会失效的情况：tmux server 被杀、机器休眠、`gh` token 过期。建议用 `tee` 留日志便于事后排查。
- 每个 issue 处理完会自动切回你原来的分支。

## 状态机

`notified → approved(标签) → implementing → pr_open → reviewing ⇄ (fix) → approved → merged`
终态：`merged` / `failed` / `needs_human` / `rejected`

## 注意事项

- Actions 使用 `github-actions[bot]` 身份评论；需要真实用户时 @ `v3c70r`。
- pi 三个角色共用同一模型/账号，通过**独立 session** 与 **role prompt** 隔离。
- 实现/讨论需要你本机在线并运行 `watch`；离线期间 Actions 只做 triage。
- ⚠️ **`watch`/`run` 运行时不要在仓库工作区手动改文件**：Agent 会 `git add -A`/`reset` 分支，可能覆盖未提交的本地改动。需要改代码请先暂停 pipeline。
- 单账号限制：GitHub 不允许自己 approve 自己的 PR，Agent B 批准以**评论记录**代替正式 review（已自动处理）；合并不受影响。
- 测试若需要 Mapbox token：把 `.env`（含 `VITE_MAPBOX_ACCESS_TOKEN`）路径给 `TEST_ENV_FILE`。
- 合并策略为 squash；PR body 带 `Closes #N` → 合并后 issue 自动关闭。
- 版本要求：`pi` 在 PATH、`gh` 已认证（repo+workflow）、Playwright 浏览器已安装。

## 目录

```
.agent/
  pipeline.mjs         # 本机编排器 (状态机)
  prompts/             # A/B 角色提示词
    implementer.md
    reviewer.md
  state.json           # 状态持久化（本地）
  logs/issue-<N>.md    # 每轮日志
  tmp/                 # pi prompt / test worktree（勿提交）
.github/workflows/
  issue-agent.yml      # triage workflow
```

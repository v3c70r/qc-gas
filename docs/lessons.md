# Pipeline lessons

Hard-won lessons about the local multi-agent loop (`node .agent/pipeline.mjs watch`).
Numbered so code comments (`见 docs/lessons.md #N`) stay stable.

## #20 — 跨 agent 共享状态：不要用状态字段「预告」尚未发生的事实

`processIssue` 的依赖闸门在依赖满足时曾把 issue 状态写成 `pr_open`。那一刻
**根本没有 PR**：没有 `pr` / `branch` / `title`。它之所以还能跑，是因为下一轮
恰好命中 `cur.status === 'pr_open' && !pr` 这个特例分支才进入实现 —— 这是**巧合**，
不是语义。一旦实现分支的条件被调整（例如有人顺手收紧这条特例），issue 就会
永远卡在 `pr_open` 且没有 PR 的状态。

修法：依赖解锁时**删除该 issue 的状态条目**，等价于「全新待实现」。共享状态
（`.agent/state.json`）是多个 agent 轮次共同读写的契约，只记录**已经发生**的事实；
「即将实现」这种意图不应落盘成状态。`data/state.json` 里出现的
`{"status":"pr_open"}` 无 PR 字段，就是这条经验留下的疤。

实现：`.agent/lib.mjs` 的 `clearBlockedState()`（纯函数，可单测）；
`pipeline.mjs` 在闸门放行时调用它，并同步清掉内存里的 `cur` 句柄。

## #21 — 长驻 watcher 必须有单实例锁

同一仓库里不小心起了两个 `pipeline.mjs watch` 时，两者共用**同一个工作区**，
会并发执行 checkout / reset / commit，互相把对方的实现分支改乱。

修法：启动时写 `.agent/watch.lock`（内容为 PID），并在启动前校验已有 PID 是否
存活：

- 存活 → 打印 `已有实例在运行 (PID …)` 并 `exit(1)`，绝不覆盖对方的锁；
- 已死（如被 `kill -9` 强杀留下的陈旧锁）→ 自动**接管**，避免永久锁死；
- 正常退出 / `SIGINT` / `SIGTERM` → 清理锁，且只删自己持有的锁。

实现：`.agent/lib.mjs` 的 `acquireWatchLock()` / `releaseWatchLock()` / `isPidAlive()`。
锁文件是运行期产物，已加入 `.gitignore`。

## #22 — 长驻进程必须能自我重载（合并不等于生效）

#113 在 2026-10-19T07:05 合并了「探针跳过 history stage + 同步冲突可解」，但
**一整天没有生效**：2026-10-19 是 28 ok / 11 failed，2026-10-20 反而 16 ok /
36 failed。原因不是代码错，而是探针是**长驻 bash 进程**
（`bash scripts/local_fetch_loop.sh`），脚本只在启动时加载；`git pull` 把新版本
写到磁盘，进程继续跑旧代码。证据：只有新版本才打印的 `probe stages skipped` 在
`.agent/probe.log` 里出现 **0** 次，手工 `docker compose restart probe` 后立刻出现。

「改完代码就修好了」对常驻进程永远不成立。同一个坑还有一个连体兄弟：同步冲突
解掉了一部分文件却**没有断言「结束时 unmerged = 0」**，于是 7 个 `UU` 文件留在
克隆里，之后每轮都撞 `error: Pulling is not possible because you have unmerged
files`（日志 40 次）。

修法（`scripts/local_fetch_loop.sh`）：

- 每轮开始前（以及每轮 pull 之后）比对脚本**内容哈希**与启动时记录的值，不一致就
  `exec` 自身（PID 不变，tmux 会话不受影响）——**合并在 1 个周期内自动生效**；
- 重启前先验证新版本**能启动**（`bash -n` + 它自己的 `PROBE_CHECK_ONLY` 启动校验），
  并在同一血缘内限制自动重启次数（`PROBE_MAX_SELF_RESTARTS`，默认 3）：不通过就
  写 `ALARM` 并**保持旧进程运行**，绝不重启死循环；
- 同步成功路径的最后一步断言 `git diff --name-only --diff-filter=U` 为空，不为空
  就 abort 掉残留的 merge/rebase 并 `reset --hard '@{u}'`，日志写明丢弃了几个文件；
- 历史遗留状态交给 `scripts/probe_doctor.sh`（默认只体检，`--fix` 才动手）。

教训：**凡是「合并后要重启才生效」的东西，都必须把重启写进代码里**，否则那次
合并只是在仓库里留了个记录。探针的验收口径因此从「提交了」改成「日志里出现了新
文案」。

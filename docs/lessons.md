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

# Role: Proposal Screener Agent（提案筛选员）

你是**独立的提案筛选员**。你不写代码，也不提出新功能 —— 你的唯一职责是：拿到一条
`pm-proposal` issue，判断**它值不值得实现**、以及**它的 scope 是否适合现有流水线一次做完**，
然后给出可执行的结论。

**关键原则：你不是提案者。**提案由 PM agent 写出，你要以怀疑的眼光审视它，
而不是替它辩护。宁可误杀一个平庸提案，也不要放行一个模糊或过大的提案 ——
被放行的提案会立刻消耗 Agent A/B/C 的完整实现-审查-测试链路。

---

## 输入

1. issue 正文（提案：背景/证据/用户价值/建议范围/验收标准）
2. `.agent/pm/context.md`（若存在：仓库现状、测试、近期 issue/PR）
3. `docs/product-review.md`（产品长期认知、已评估但不建议的方向）
4. `docs/proposal-audit.md`（历史审计，**必读**）：
   - 若本提案与 REJECT 条目同方向 → 直接判 REJECT（并引用原审计条目编号）；
   - 若与 SPLIT 条目同方向但**没有**收敛到建议的更小范围 → 判 SPLIT 并重申拆分方案；
   - 若收敛到了建议范围 → 正常评估（这是审计鼓励的行为）
5. 仓库本体（读代码确认提案涉及的区域是否真实存在、是否已有类似实现）
6. 交付链路的能力边界（**这是本次评估的重点**）：

| 角色 | 能力边界 |
|---|---|
| **Agent A 实现** | 一个 PR 完成；不引入新的付费依赖/服务；必须能跑通仓库的 `build` 与 `test`；改动应集中在少量文件 |
| **Agent B 审查** | 独立模型审查 diff；**diff 过大或跨度过广会显著降低审查质量**（经验阈值：≤ ~10 个文件、≤ ~600 行） |
| **Agent C 测试** | 只能用仓库**已有**的测试设施验证（如 Playwright e2e / validate 脚本 / 纯函数单测）；若提案涉及的区域**没有任何测试覆盖**，必须明确指出并给出可自动验证的替代方案，否则判 SPLIT/REJECT |
| 数据/密钥 | 不能要求新增密钥到前端；不能要求付费 API（除非仓库已具备） |
| 时间 | 不能是"持续观察/长期运营"类任务（那属于流程而非一次 PR） |

## 评估维度（每项 1–5 分）

- **VALUE**：用户价值是否真实、可量化？（提案里的证据是否引用了具体来源）
- **EFFORT**：实现成本（1=很小，5=很大）
- **CONFIDENCE**：你对"一次 PR 能做完且能自动验证"的信心

## 判定规则

- **IMPLEMENT**：证据充分 + 单个 PR 可完成 + Agent C 能用现有测试设施验证。
  此时必须给出 `REFINED_TASK`：把提案收紧成**给 Agent A 的直接任务说明**
  （明确的文件范围、必须遵守的约束、验收清单），让它不需要再猜。
- **SPLIT**：方向有价值，但一个 PR 装不下、或缺少前置条件。
  必须给出 `SPLIT_PLAN`：拆成 2–3 个可独立完成的小步骤（每步都能单独验证）。
- **REJECT**：证据不足/主观美化、与既有实现或已关闭 issue 重复、
  纯外观偏好、无测试可验证、或收益明显低于成本。必须给出明确 `WHY`。

## 输出格式（严格，便于自动解析）

```
<!-- agent-screener -->
VERDICT: IMPLEMENT | REJECT | SPLIT
VALUE: n/5  EFFORT: n/5  CONFIDENCE: n/5
WHY: <2–4 句，直接给结论依据；引用你实际读到的代码/issue 证据>
SCOPE: <涉及文件/模块；是否一个 PR 能完成>
TESTABILITY: <Agent C 具体如何验证；若无法自动验证，说明原因>
RISKS: <可能让 A/B/C 失败的点>
REFINED_TASK: <VERDICT=IMPLEMENT 时必填：给 Agent A 的收紧任务>
SPLIT_PLAN: <VERDICT=SPLIT 时必填>
```

最后一行必须是英文 ASCII 哨兵（供机器解析，即使正文用中文）：

```
RESULT: IMPLEMENT
```
或 `RESULT: REJECT` / `RESULT: SPLIT`。

## 禁止

- 不要修改代码、不要创建新 issue、不要开 PR。
- 不要评价提案的措辞，只评价它的**价值与可执行性**。
- 不要因为"看起来不难"就放行一个没有证据支持的提案。


---

# 本次任务
筛选 issue #89: 收藏改用坐标主键（收敛到 #59 的 STEP 2）：新收藏写坐标键 + 一次性幂等迁移 + 双键兜底

## 提案正文
## 收敛说明（针对 #59 的 SPLIT，STEP 2）

#59 被判 **SPLIT**，其拆分方案里 STEP 1（watch 静默失联）**已由 #65 合并**。本 issue 只做 **STEP 2：favorites 侧**，并把不做的事写死：

- ✅ **只改 `js/favorites.js`**（+ `tests/app.spec.js` 新增用例）；**不改** `js/stats.js`、`js/station-card.js`（它们通过 `isFavorite()`/`toggleFavorite()` 间接受益）、**不改** `js/watch.js`（#65 已完成其坐标兜底）
- ✅ **保持存储格式 `string[]`**：`js/databackup.js` 的 `isValidFavoriteId()` 只要求"非空字符串" ⇒ **零 schema 改动、`BACKUP_VERSION` 不动、旧备份仍可导入**
- ❌ **不做** STEP 3 的"跨模块 helper 统一"、**不改** `fillups` 的 `stationId`、**不改** `watch.js`、**不新增导出给别处使用的公共 helper**（坐标键助手留在 `favorites.js` 内部）
- 预计 **1–2 文件 / <200 行**（Agent B 阈值 ≤~10 文件、≤~600 行）

## 背景 / 证据

### 1）现状：收藏的键是文本，官方改名/改地址后就静默失效

`js/favorites.js`：

```js
export function stationId(feature) {                     // 文本优先，coord: 仅兜底
  const key = [p.name, p.address, p.postal_code].map(v => (v ?? '').trim()).join('|');
  if (key.replace(/\|/g, '')) return key;
  const c = feature?.geometry?.coordinates;
  return c ? `coord:${c[0]},${c[1]}` : '';
}
export function isFavorite(feature) { return !!id && favorites.includes(id); }   // 精确字符串比较
```
`js/stats.js` 用 `isFavorite()` 决定星标、"Favoris seulement"过滤与**收藏置顶**；`js/station-card.js` 用 `toggleFavorite()`。

### 2）这不是理论风险（实测，见 `docs/product-review.md` §7）

用仓库内历史快照逐日 diff：**30 天内 1.09%（27/2466）的站点文本键消失**，其中 **12 个（44%）坐标完全不变**（纯改名/改地址，可用坐标救回）；14 天为 0.45%（5/11 可救回）；坐标键流失率仅 **0.61%**（比文本键低近一半）。
消失样例多为公司名/地址被官方更正的站（`Les entreprises Hardy inc.|501 av. 7, Barraute`、`9456-9654 Quebec Inc|2135 route 344, SAINT-PLACIDE`）。

**用户可见后果**：该站的星标熄灭、退出"Favoris seulement"、不再置顶——**没有任何提示**。收藏是应用里最高频的个人功能（#14），而关注侧已由 #65 修好（+「找不到该站」提示），收藏侧仍缺。

### 3）现成可复用件（保证小 diff）

- `js/watch.js`（#65）已验证"多一条坐标兜底 + 幂等"的模式；
- `js/history.js` 已有坐标键约定（`${lng.toFixed(5)},${lat.toFixed(5)}`），`stationId()` 的兜底形式也是 `coord:` 前缀；
- 测试设施齐备：`tests/app.spec.js` 已用 `addInitScript` + `localStorage.setItem('qc-gas-favorites', …)` 播种（8 处）、`page.route('**/data/stations.json')` 造快照、`serveSnapshot`/`waitForStations` 夹具。

## 用户价值

1. **收藏不再静默消失**：官方改名/改地址后（占流失的 44%）星标、置顶与"Favoris seulement"照常工作；
2. **零数据风险**：存储格式不变、无法解析的旧键**原样保留**（不丢数据），站点回来即可再次匹配；
3. **对新收藏一次性生效**：新收藏写坐标键，此后再也不受文本改动影响；
4. 纯前端、无新依赖、无新增网络请求；备份/导入/导出流程完全不变。

## 建议实现范围（一个 PR，全部在 `js/favorites.js` 内）

- 新增**内部**助手：
  - `coordinateKey(feature)` → `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`（与 `js/history.js` 约定一致，坐标必须合法）；
  - `favoriteId(feature)` → 有合法坐标时返回坐标键，否则回落 `stationId(feature)`（文本键）。
- `isFavorite(feature)`：**双键匹配** —— 命中 `favoriteId(feature)`（坐标键）**或** `stationId(feature)`（旧文本键）都算已收藏；
- `toggleFavorite(feature)`：新增时写入 `favoriteId(feature)`；取消时**同时移除两种形态**（保证旧条目也能被取消）；
- **一次性幂等迁移** `resolveFavoriteIds(stations)`：对每个**非 `coord:` 前缀**的已存 id，若能在当前快照里找到 `stationId(feature) === id` 的站，则改写为 `favoriteId(feature)`；无法解析的原样保留；**仅在有变化时持久化**；
  - 触发点：模块加载（若快照已就绪）、`window.addEventListener('stations:loaded', …)`（在 `favorites.js` 内自建监听，像 `js/watch.js` 那样）、以及 `reloadFavorites()`（#57 的备份导入后）之后；
  - 幂等：迁移完成后再跑不会产生任何改动（测试断言）。
- **保持** `stationId()` 的现有导出与语义不变（其他模块依赖它）；`getFavoriteCount()`/`subscribe()`/`reloadFavorites()`/`STAR_ICON` 行为不变。

## 验收标准

- [ ] 旧文本键收藏在升级后**仍然有效**（星标、"Favoris seulement"计数、置顶顺序不变）；可解析的条目被改写为 `coord:` 形态（断言 `localStorage['qc-gas-favorites']` 内容）
- [ ] **迁移幂等**：再次加载/再次触发 `stations:loaded` 不再产生任何改动（断言 localStorage 内容不变）
- [ ] `page.route` 返回**改名/改地址（坐标不变）**的快照：该站在侧栏与卡片**仍显示为已收藏**，仍出现在"Favoris seulement"中且**仍置顶**
- [ ] **无法解析的旧键不被丢弃**（断言仍存在于 localStorage）；当该站以原文本再次出现时**重新匹配**（旧键兜底）
- [ ] 取消收藏对**旧文本键条目**同样有效（不会留下"删不掉的收藏"）；新的收藏写入坐标键
- [ ] v1 备份（含旧文本键）**导入后**收藏照常可用（迁移在 `reloadFavorites()` 后自动生效）；`js/databackup.js` **未被改动**、`BACKUP_VERSION` 不变
- [ ] 无新依赖、无新增网络请求；现有 Playwright 测试全绿（尤其 `Favorites (localStorage)` 系列）；新增 ≥4 个测试（迁移 + 幂等 / 改名后仍收藏且置顶 / 不可解析键保留并在站回时匹配 / 取消旧键条目 + 新键写入）

## 参考

- 筛检结论与拆分方案：#59 的关闭评论（`docs/proposal-audit.md` 的 SPLIT 条目）；STEP 1 = #65（已合并）
- PM agent 调研记录：`docs/product-review.md`（§4 本日提案、§7 站点键流失实测、§9 本轮提案）
- 代码位置：`js/favorites.js`（`stationId`/`isFavorite`/`toggleFavorite`/`reloadFavorites`）、`js/history.js`（坐标键约定）、`js/watch.js`（#65 的坐标兜底范式）、`js/databackup.js`（`isValidFavoriteId`，**不改**）、`js/stats.js`（消费方，**不改**）、`tests/app.spec.js`（播种与夹具）

---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` 确认 `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较；`js/watch.js` #65 已用坐标兜底并保留 missing（`resolveWatchFeature` L137-144，`stations:loaded` 监听 L473），favorites 侧确无对应能力。`docs/product-review.md` §7 实测量化 30 天文本键流失 27/2466=1.09%、其中 12 个坐标不变（44% 可救）。本提案已收敛到 #59 SPLIT 的 STEP 2（只 favorites，不碰 stats/station-card/watch）、并回答 §11 待调研的"坐标主键 + 文本键兜底匹配的最小形态"。（注：§10 曾写"等 #87 落地"，但 #87 是无关的 Par région 视图且碰 stats.js/tests，不是 favorites 的真实前置条件 → 不构成 SPLIT 理由，仅作并行冲突风险。）实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移在现快照上单射，无合并/丢失。
- **SCOPE**：`js/favorites.js`（新内部 helper `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` 新增 ≥4 用例。2 文件、~200 行 → 一个 PR 装得下。**不动** `stationId()` 导出与语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 都依赖它）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变。三个消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount`，无直接读 list → 改动可封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。直接复用 `tests/app.spec.js:1346-1560` 的 #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot(page,…)` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。可用断言：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))` 内容。可达用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，且 localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 后 localStorage 字节不变；③ rename 快照（坐标同）→ 仍亮仍置顶仍在 Favoris seulement；④ 不可解析键（如 `Station X|1 rue|H0H 0H0`）在快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键，取消后不再残留。**注意**：验收项"取消旧文本键条目"无法在"站不可见"状态下点星，须用"同时播种文本键+坐标键"路径验证（见 REFINED_TASK 的去重规则），否则该项不可自动验证。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 SPLIT 的 STEP 1（watch）已由 #65 合并；本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救，真实用户价值。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移在现快照上单射，无合并/丢失。§10 那句"等 #87 落地"非硬前置（#87 是无关的 Par région 视图），仅算并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。直接复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))` 内容。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移在现快照上单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移在现快照上单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移在现快照上单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移在现快照上单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移在现快照上单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


---

<!-- agent-screener -->
## Agent 筛选结论与实现范围（自动追加）

🤖 **提案筛选（独立 agent，deepseek/deepseek-v4-flash）**

**判定：IMPLEMENT**

- **WHY**：证据可核对。`js/favorites.js:39-43` `stationId()` 文本优先（`name|address|postal_code`），`coord:` 仅兜底；`isFavorite()`/`toggleFavorite()` 精确字符串比较，无迁移、无坐标兜底（复核：仓库当前 `js/favorites.js` 对 `resolveFavoriteIds`/`coordinateKey` 0 命中 ⇒ 尚未实现，非重复）。`js/watch.js` #65 已有同范式（`resolveWatchFeature` L137-144 坐标兜底 + `stations:loaded` 监听 L473），favorites 侧确缺。#59 被审计判 SPLIT；STEP 1（watch）已由 #65 合并，本提案收敛到 STEP 2（只 favorites），并回答 `docs/product-review.md` §11 待调研的"坐标主键 + 文本键兜底匹配最小形态"。§7 实测量化 30 天文本键流失 27/2466=1.09%、44% 坐标不变可救。实测 `data/stations.json` 2461 站：文本键 0 碰撞、`toFixed(5)` 坐标键 0 碰撞 → 迁移单射，无合并/丢失。§10 "等 #87 落地"非硬前置（#87 是无关 Par région 视图），仅并行冲突风险，不构成 SPLIT。
- **SCOPE**：`js/favorites.js`（内部 `coordinateKey`/`favoriteId`、改 `isFavorite`/`toggleFavorite`、加 `resolveFavoriteIds` 迁移 + `stations:loaded` 监听 + `reloadFavorites()` 后触发）+ `tests/app.spec.js` ≥4 用例。2 文件、~200 行，一个 PR 装得下。不动 `stationId()` 导出/语义（`js/watch.js:15`、`js/station-card.js:11`、`js/stats.js:4` 依赖）、不动 `js/databackup.js`（`isValidFavoriteId()` L47-49 只要求非空字符串 ⇒ 存储维持 `string[]`）、`BACKUP_VERSION` 不变、不动 stats/station-card/watch。三消费方只走 `isFavorite`/`toggleFavorite`/`getFavoriteCount` → 改动封闭在 favorites.js 内。
- **TESTABILITY**：现有 Playwright 足够，无需新设施。复用 `tests/app.spec.js:1346-1560` #65 范式：`snapshotBody({rename})`（改 name/address、坐标不动）+ `serveSnapshot` + `waitForStations` + `addInitScript` 播种 `qc-gas-favorites`。断言可达：`.fav-star.on`、`#favorites-count`、置顶顺序、`JSON.parse(localStorage.getItem('qc-gas-favorites'))`。用例：① 播种文本键 + 正常快照 → 星亮/置顶/计数不变，localStorage 变 `coord:<lng.toFixed(5)>,<lat.toFixed(5)>`；② 幂等：再次 `stations:loaded`/reload 字节不变；③ rename 快照（坐标同）→ 仍亮、仍置顶、仍在 Favoris seulement；④ 不可解析键（`Station X|1 rue|H0H 0H0`）快照缺失时**保留**，快照含原文本后改写；⑤ 新收藏写坐标键、取消后归零。注意：验收项"取消旧文本键条目"在站不可见时点不到星 → 须用"同时播种文本+坐标键"路径验证（见下）。

### 给 Agent A 的收紧任务
只改 `js/favorites.js`（+ `tests/app.spec.js` 用例）。实现：


## 上下文
- 仓库: /home/qgu/Projects/qc-gas
- 上下文包: `/home/qgu/Projects/qc-gas/.agent/pm/context.md`（若存在）
- 产品长期认知: `docs/product-review.md`
- 每周自动实现余额: 0

请按格式输出，最后一行必须是 `RESULT: IMPLEMENT` / `RESULT: REJECT` / `RESULT: SPLIT`。
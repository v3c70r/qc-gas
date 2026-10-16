# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses。

> ⚠️ **环境提示（2026-10-16 实测，影响交付）**：本 worktree 里 **owner 的 `v3c70r` token 已失效**（`gh auth status`: token invalid），活动账号 `qgu-reviewer`
> **可读、可创建 issue，但没有任何加标签权限**：`gh issue edit --add-label` → `GraphQL: AddLabelsToLabelable` 权限不足；
> REST `POST /repos/:o/:r/issues/<n>/labels` → `403 Must have admin rights to Repository`。
> 所有 gh 命令都要 `export GH_TOKEN=$(gh auth token --user qgu-reviewer)`（默认活动账号是失效的 owner token）。
> **后果：新建提案会缺 `pm-proposal` 标签，而筛选器只挑「open + `pm-proposal` + 无 `pm-screened`」→ 会被静默跳过。**
> 处置：本轮已在 **#96 上留言请 owner 补标签/刷新 token**（#89 当时靠尚未失效的 owner token 补上）。→ 每轮创建后**必须核对标签**。

_Last updated: 2026-10-16（第 23 次运行）。**#90（价格+距离综合排序）被筛检员 REJECT**（证据口径错误）——已记入下方"不得重复提案"。
我的 #87/#88/#89 仍**通过筛检在等 owner 批准**。owner 的 P0 计划继续推进（#91 合并、#92/#93 已批准，PR #95 在实现"本机每 5 分钟唤醒 + 闸门"）。
本轮提案转向一个**由 P0 新数据解锁**的能力：站点级"价格何时变化"（数据与文档已就绪，前端未用）。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（可安装 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方 GeoJSON + **官方每周 Bulletin PDF**（零售毛利/区域均价）；
  抓取：**本机 crontab 每 5 分钟唤醒 → `repository_dispatch` → 闸门按时刻表判定**（#91/#93），Actions `schedule` 仅作小时级兜底
- 规模（2026-10-16 实测）：**2 463 个站点**、18 区、三油品；regular **169.9 / 中位 189.9 / 239.3**；快照 `generated_at` 20:00Z；摄取延迟 p50 **144 s**
- 多语言：法语（默认）/ 英语 / 简体中文（三类字典各 183 键，键位一致）；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-10-16）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 / 价格标签 / 最低价高亮 | 聚类（z<10）+ 渐变点 + 价格标签（z≥11）+ 最低价脉冲 | 既有 |
| 全省优先视图 + 位置记忆 | 首次访问不套半径、全省 fitBounds；半径/定位/点地图进入半径模式；`qc-gas-view` | ✅ #39 |
| 筛选即所得（区域=范围，半径=我周围） | 选区域 → 退出半径 + `fitRegionBounds()`；0 结果给出「Voir toute la région (N)」 | ✅ #60 / #61 |
| 侧栏列表：看全 + 排序 | 30/页分批 + `prix ↔ distance`（`qc-gas-list`） | ✅ #50 / #53（综合排序=**已 REJECT**，见 §10） |
| 「Par région」视图 / 列表键盘可达 | 纯增量区域视图；行 `role="button"` + Enter/Space + 焦点环 | 🚧 #87 / #88（IMPLEMENT，待批准） |
| 收藏 / 关注提醒 | 置顶与仅看收藏；阈值触发 + 真实变动；找不到的站不再静默删除 | ✅ #14 / #31 / #65；🚧 #89 收藏坐标主键（待批准） |
| **站点级价格变更时间** | `data/history/station-changes.json` 已存每站 `last_changed_at`/`last_seen_at`（#68），`docs/history-data.md` **明确写它的用途是站点卡片 "observed X ago"**——**前端尚未使用** | ⚠️ **本日提案** |
| 站点当日基准 / 官方周报毛利 | 区域中位/分位/50 L 差额（#34）；Régie 17 区毛利 + 装车价（#49/#54） | ✅ |
| 卡片/面板与快照一致 / 「Mes données」 | 订阅 `stations:loaded` 重渲染；版本化 JSON 导出/导入（合并/替换） | ✅ #51 / #52 / #57 / #58 |
| 加油日志「Mes pleins」 | 记录 + 本月/今年/全部汇总 + 当前期间 CSV（RFC 4180、UTF-8 BOM） | ✅ #29 / #62 / #64 |
| PWA / 更新策略 / 新鲜度 | network-first 导航 + 版本化缓存 + 更新提示；相对时间 + 可见时自动刷新 | ✅ #37 / #45 / #47 |
| 抓取管线（P0 计划） | 区域/品牌分层学习节奏 + 显式时刻表（忙时 20 min、夜间 60 min）+ **本机 5 分钟唤醒器** + `data/metrics.json` 延迟/陈旧度台账 + 站点变更台账 | ✅ #71 / #74–#77 / #82–#86 / #91；🚧 #92 / #93（PR #95） |
| 品牌对标 / 区域排行 / 趋势看板 / 行程油费 / 导航 / 历史 | `js/stats.js`、`js/dashboard.js`、`js/station-card.js`、`js/history.js` | ✅ #15 / #17 / #16 / #32 |
| 体验 / 测试 | 移动底部抽屉、44px、键盘快捷键、三语；Playwright e2e 142 项；pipeline 有 `scripts/test_*.py` | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——Protégez-Vous 点出官方平台"无法筛选、只能逐个 hover"（最硬差异）。
2. **官方"贵不贵"口径齐全**：Régie 每周零售毛利（#49）+ 站级中位/分位（#34）；**区域=范围**语义（#60）。
3. **新鲜度已接近解决**：外部触发器端到端实测把陈旧度从 3.3 h 压到 **5 min**（#93），摄取延迟 p50 **144 s**；探针/唤醒器停掉才会退回小时级。
4. **数据诚实性 + 个人数据自持**：无合成/外推、覆盖门控、离线标注、无效坐标不冒充、卡片与快照一致、引用不静默失联（#65）、导出/导入 + CSV。
5. **没有后端也能做决策工具**：搜索/收藏/关注/排行/油费/日志/基准/距离排序，全部本地；三语；零运维。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 功能（**本日提案**） | **站点卡片完全没有"这个价多久没变"的信息**：数据侧已有 `data/history/station-changes.json`（每站 `last_changed_at`/`last_seen_at`，坐标键与 `js/history.js` 的 `stationKey()` 一致，`docs/history-data.md` L19/L398 已写明用途），但 `js/station-card.js`/`js/history.js` **都没读它** | 用户最关心的"这个价是不是旧的"在**站点粒度**无答案（只有 #47 的全局"mis à jour il y a X"）；而竞品评论里"prix pas à jour"是最高频抱怨（9/50 提到准确性） |
| 功能 | #87/#88/#89 已过筛检但在等批准（自 10-11 无新批准） | 交付瓶颈在批准环节 |
| 数据 | 探针曾把仓库撑大：**≈39 MiB/天（4.1 天 403 次 data 提交 ≈500 MB 原始增量；`data/stations.json` 占 73%）→ 约 22 天触及 1 GB 软限制**（#92 正在修：停提交快照 + 本地事件日志） | 已识别并在修；提醒：**新鲜度不能靠"把每次快照提交进仓库"来换** |
| 数据 | `update-profile.json` 样本骤降到 5（P0 变更重置）；`data/metrics.json` 提供延迟/陈旧度台账（`latency_seconds.p50=144`、`stale.never_changed` 等） | "何时变价"洞察改为直接读新台账（§11），不再依赖旧的样本计数 |
| 数据 | 站点级历史 180 天保留、无月 rollup；`station-history.json` 29 天 ≈1.3 MB、`station-changes.json` 244 KB（均有 `.br`） | 1A 对站点不可达；盯 `check_data_size.py` 40 MB 预算 |
| 数据 | 上游无站点 ID、无单站更新时间（平台无其他公开接口，2026-10-13 实测） | 站点级"何时变"只能靠**我们自己的观测**（本日提案的关键诚实前提） |
| 功能 | 无分享/深链（竞品有 App Clip）；无深色模式 | 见 §10 |
| 工程 | 无前端单元测试（只有 E2E） | 重构风险 |

## 5. 用户声音

- **owner 一手反馈**：#41（网站 192.9 vs 现场 186.9 → 部署旧 checkout，PR #42）；#43（"到达现场后油价不准"→ 引发 P0 抓取计划）；#55（iPhone Safari 半径筛选被遮挡 → #56）。
- **竞品 App 评论（Prix Essence Québec，最近 50 条，最新仍是 2026-09-28；均分 3.42★、17 条 1–2★）**：11–13 条骂广告、**9 条质疑价格准确性/时效**、提醒推错站、要直达降价站、要主屏小组件。
- **infoEssence 评论**："Cette station n'affiche pas le bon prix sur le site"、"Les prix sont pas à jour!" → **"这个价是不是旧的"是全赛道最高频的信任痛点**（本日提案直接针对它）。

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- 站点 GeoJSON 属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。
- 区名匹配（2026-10-03）：`normalizeText()` 能匹配周报的 en dash/无重音写法；只有 `Municipalités hors MRC \ CMM`（1 站）无对应行。
- 平台无其他公开接口（2026-10-13 实测 `/api*`、`swagger.json` 等全 404）→ Reddit 所说"公共 API"即我们已在用的 `stations.geojson.gz`。

## 7. 关键实测数据（供后续提案引用）

- **站点变更（新数据源，2026-10-16）**：`data/history/station-changes.json`（244 KB，`.br` 24 KB）→ `last: {observed: 2463, changed: **135**}`（最近一次观测有 135 站变价）；每站 `{t: last_changed_at|null, u: last_seen_at, p:[regular,super,diesel]}`；坐标键与 `js/history.js` 一致；`docs/history-data.md` L398 已写明用途 = 站点卡片 "observed X ago"。
- **摄取延迟/陈旧度**：`data/metrics.json` → `latency_seconds.p50 = 144.4 s`（n=300）；`stale` 字段记录 `never_changed` / `over_7d` 等分布。
- **⭐ 综合排序的"实现级收益"实测（2026-10-16，回应 #90 的 REJECT 前置条件）**：以每第 4 个站点为合成起点（612 个）、25 km 半径、regular 油品、直线距离 ×2、50 L、8 L/100 km：
  - **首选是否变化：457/607 = 75%**；
  - **按成本档的首选 vs 按价格档的首选，净节省中位数 $1.32**（p75 $3.44、p90 $5.04、max $7.62）；74% 的情形 > $0.05、57% > $1.00。
  ⚠️ 与 §7 旧条目（"价差中位 0.00¢"）**不是同一指标**：旧指标比的是"最近站 vs ≤5 km 最便宜站"，新指标比的是**列表默认首行（最低价，可能很远）vs 成本档首行**——这正是 #90 的 REJECT 要求补的口径。
  ⚠️ 仍缺：**按区分解**（Montréal 之外是否 ≈0）与可被第三方用 `data/stations.json` 复现的脚本化定义。
- **抓取节奏**：外部触发器（每 5 分钟唤醒 + 闸门）实测把陈旧度 3.3 h → **5 min**；Actions `schedule` 侧 p50 121 min / p90 285 min（不达标，仅兜底）。
- **站点键流失（2026-10-04）**：30 天文本键消失 **27/2466 = 1.09%**（其中 12 个坐标仍在）；坐标键 0.61%。
- **首屏列表区域偏斜（计数稳定、构成随日变化）**：10-05 3/18 可见、10-07 5/18、10-08 13/18 区 0 行、10-11 15/18、10-12 16/18。⚠️ 标注快照日期；测试从 fixture 现算。
- **价格水平 / 体量**：regular 169.9 / 中位 189.9 / 239.3；站点历史 29 天 1.3 MB；e2e 142 项。

## 8. 竞争格局（2026-10-16）

> 数据已商品化（2026-04-01 起强制上报、延迟 <5 分钟；GitHub 上另有 8 个 0★ 同源克隆）→ 竞争点＝**怎么用数据 + 交付是否新鲜**。

| 竞品 | 形态 | 关键能力 | 我们的差距/机会 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错；**无筛选**（Protégez-Vous 点名）；发布周报毛利 | 筛选/标签 + 毛利口径已补齐；它自身也不显示"该站价格多久没变" |
| **infoEssence – Prix Essence QC** | 原生 App | "每 5 分钟更新"；明日预测；区域气压计；提醒；排序（价格/距离/综合）；Apple Watch + 小组件 + App Clip | 原生能力无法对齐；**站点级"多久没变"是我们的机会**（其文案只讲全局更新频率） |
| **GasBuddy** | 原生 App（跨市场参照） | 站点页显示价格**更新时间**与社区校验 | 数据源不同（众包 vs 官方）；但"每站显示时效"是同类工具的常见做法 → 支持本日提案 |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」、按城市阈值提醒、城市/区域独立页面、SEO、API | 首屏区域入口在 #87 补齐中 |
| **CAA-Québec Info Essence** | 会员 Web 工具 | "si c'est le bon moment de faire un détour" | 完整绕路模型已否决；综合排序已 REJECT（§10） |
| **Prix Essence Québec（App）** | 可安装原生 App | 收藏+提醒、"愿意开的距离"、预测涨价概率 | 我们不预测（样本不足 + 诚实性优先） |
| **Radio-Canada / achetezlemeilleur** | 媒体 / 内容站 | 区域均价看板、区域表（Min/Max/Écart/vs Moy.） | 区域优先呈现是行业惯例（#87） |
| **Google Maps / Waze** | 通用地图/导航 | 会显示油价（偶有区域失效） | 通用巨头不稳定 = 机会窗口 |

## 9. 本轮提案（已建 issue）

**站点卡片显示"这个价多久没变"（用 `station-changes.json` 的观测台账）** → issue **#96**（`pm-proposal`）。
为什么是它：① 数据与**文档已就绪**（`docs/history-data.md` L398 已写明该文件的用途就是站点卡片 "observed X ago"），前端只是没接；② 直击**最高频的信任痛点**（"prix pas à jour"，竞品评论 9/50）；③ 上游无单站时间戳（已实测），所以**必须明确标注"由 Essence Québec 观测"**而非"加油站申报"——与我们的诚实定位一致；
④ 范围小：复用 `js/history.js` 的 `stationKey()` 与 `js/freshness.js` 的 `formatRelativeTime()`，懒加载（首次打开卡片时取），失败静默。

## 10. 已评估但不建议 / 待提（避免重复提议）

- **🚫 "价格 + 距离综合排序"（#90）已被 REJECT**（审计条目 2026-10-15）：我的头号指标（"33% 的候选是假便宜"）**口径错误且不可复现**，真正与决策相关的收益按当时口径 ≈ 0。
  **唯一重提路径**（审计要求）：先给出**"第三档 vs 价格档"的实现级收益实测**——首选是否变化、被越过候选的净节省中位数与分位数、**按区分解**（Montréal 之外是否 ≈0）、并有可被第三方用 `data/stations.json` 复现的指标定义。
  今天的初测（§7）已给出**正向**结果（75% 首选变化、中位节省 $1.32），但**仍缺按区分解与脚本化定义** → 补齐前不重提。
  教训（写给未来的我）：**排序/推荐类提案的价值证据必须是"首选是否变化 + 用户实际省多少钱"，不是"有多少候选属于某类"**。
- **深色模式**：仅 1 条评论明确要求，属筛检员列明的"纯外观偏好"风险 → 暂不提。
- **#59 STEP 3（fillups/watch 也切坐标主键、helper 统一、备份 schema）**：#65 已让 watch 自洽（用自身 `lng`/`lat`），#89 覆盖 favorites；**fill-ups 的 `stationId` 目前没有下游匹配用途** → 先确认价值再谈，避免纯重构被否。
- **"明日预测"**：竞品已做；我们坚持不预测（样本不足 + 失真风险）。
- **#63 STEP 2（省级行补位置线索）**：不要碰 `.details`（L2885 用它查 map source）与 `.distance`（L3231 解析）→ 需先定安全形态（§11）。
- **分享/深链 / App Clip 等价物**：无直接用户证据 → 暂缓。
- **站点级 1A rollup / 月粒度降采样**、**加油日志 L/100 km（里程表）**、**每日 `composantes`/`rqe.pdf`**：均暂缓/不接入（理由同前几轮）。

## 11. 待调研问题（下次优先）

- 本日提案落地后：是否也给**列表行**（而不只是卡片）显示时效？以及"从未观察到变化"（`t: null`）该如何措辞才不误导。
- **综合排序重提**：按区分解 + 脚本化指标定义（`scripts/` 里落一个可复现的小脚本？）——若 Montréal 之外 ≈0，则价值集中在市区，需据此重新定位（例如只在半径模式提供）。
- **"何时变价"洞察**：现在可直接读 `data/metrics.json` / `station-changes.json` / `update-profile.json`（后者样本被 P0 重置）→ 需要重新确认"每时段 ≥14 天"这一类门槛是否还适用，或改用"观测到的变价事件分布"。
- **#87/#88/#89 落地后**：Par région 是否显示该区官方周均价/毛利；列表行键盘体验与卡片焦点联动。
- **#63 STEP 2**：不改 `.details`/`.distance` 的前提下给省级行补位置线索的安全形态。
- 站点历史 180 天后体积与 40 MB 预算余量（`station-history.json` 1.3 MB / 29 天；`station-changes.json` 244 KB）。

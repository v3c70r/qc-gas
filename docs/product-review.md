# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-29（第 11 次运行）。**#49 与 #50 都仍在等 owner 批准**（标签仅 `agent-seen`，triage bot 已 @owner，两天无回复）；
无 open PR；自 #47 之后没有新的代码改动（只有数据提交）。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（可安装 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方 GeoJSON（`regieessencequebec.ca/stations.geojson.gz`），GitHub Actions 抓取并提交回仓库；
  抓取时机由学到的变价时段 + 每日预算 + 3h 兜底门控（#43）
- 规模（2026-09-29 实测）：**2 460 个站点**（1 条无效坐标被前端排除）、18 区、三油品；
  regular **166.9 / 中位 192.9 / 239.3**；快照 `generated_at` 12:50Z
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-09-29）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 / 价格标签 / 最低价高亮 | 聚类（z<10）+ 渐变点 + 价格标签（z≥11）+ 最低价脉冲 `js/map.js`、`js/stats.js` | 既有 |
| 全省优先视图 + 位置记忆 | 首次访问不套半径、全省 fitBounds；半径/定位/点地图进入半径模式；`qc-gas-view` | ✅ #39 |
| 筛选即所得 / 离线搜索 | 品牌、油品、价格、区域、半径；搜索可跨全省 `js/filters.js`、`js/search.js` | ✅ #13 |
| 收藏 / 关注提醒 | 置顶与仅看收藏；阈值触发 + "自上次访问以来的真实变动" `js/favorites.js`、`js/watch.js` | ✅ #14 / #31 |
| 站点当日基准 / 覆盖门控 | 区域中位、与中位差、区域内百分位、50 L 金额差；区间超覆盖即禁用 `js/benchmark.js` | ✅ #34（**刷新一致性缺口见 §4**） |
| 官方周报毛利 + 官方区域均价 | pipeline + 趋势看板列 + 站点卡片行（`data/regie-margin.json`） | 🚧 #49 待批准 |
| 侧栏列表看全 + 距离排序 | 分批渲染（解除 30 条截断）+ `prix ↔ distance` 排序 | 🚧 #50 待批准 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/对比真实区域历史均价的节省额 `js/fillups.js` | ✅ #29 |
| 可安装 PWA + 更新策略 + 新鲜度 | 导航 network-first + 版本化缓存 + 更新提示；`js/freshness.js`：header 相对时间、可见时每 10 min/回前台自动原地刷新（`applyStations()`） | ✅ #37 / #45 / #47 |
| 品牌对标 / 区域排行 / 趋势看板 | `js/stats.js`、`js/dashboard.js` | ✅ #15 / #17 |
| 行程油费 / 导航 / 数据透明度 | 站点详情 L/100km × 距离；Google/Apple Maps 跳转；卡片显示 `generated_at` | ✅ #16 |
| 价格历史 | 区域级真实 6h 桶 + 站点级真实日粒度（180 天；现 12 天 594 KB） | ✅ #32 |
| 自适应抓取（pipeline） | 学到的小时权重 → 36 槽/天计划 + 门控（min gap 10 min、3h 兜底）+ 体积预算 `scripts/fetch_strategy.py` | ✅ #43 |
| 体验 / 测试 | 移动底部抽屉、44px、键盘、三语；Playwright E2E 80+；pipeline 有 `scripts/test_*.py` | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——Protégez-Vous 明确点出官方平台"**无法用筛选找出最低价，只能逐个 hover**"（我们的最硬差异，2026-09-29 复核）。
2. **打开即用、无广告、可安装**：竞品 App 近 50 条评论里 11–13 条在骂广告；我们免安装可用也能装主屏。
3. **数据诚实性成为资产**：无合成/外推（#32）、覆盖门控（#34）、离线标注"最后同步"（#37）、无效坐标不冒充站点（#39）、
   新鲜度可见且自动刷新（#47）。
4. **没有后端也能做决策工具**：搜索/收藏/关注/排行/油费/加油日志/站级基准，全部 localStorage + 静态数据。
5. **全省优先 + 位置记忆**；**三语**（fr/en/zh，竞品中未见同等支持）；隐私友好、零运维。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 功能（本日提案） | **自动刷新后，已打开的站点卡片/详情面板仍持有旧快照的 feature 对象**：`js/station-card.js` 的 `currentFeature` / `detailFeature` 只在打开时赋值；`stations:loaded` 事件**只有 `js/watch.js` 在监听**（全仓 grep 确认）。而 `benchmarkFor()` 混用**新的 `currentStations`** 与**旧 feature 的 `props[priceKey]`** → **同一屏出现"旧价格 + 新基准"**；`popupUpdated`、"≈ $/L"、行程油费、`openFillupForm()` 的**预填单价**同样来自旧对象 | ① 与 #41（用户看到的价格不对）同类的信任问题：列表/地图标签已是新价，卡片还是旧价；② 加油日志可能**记录错误单价**，进而让"对比区域均价的节省额"失真；③ `setWatch()` 的 `lastSeenPriceCents` 可能记下旧价，污染 #31 的"自上次访问以来的变动" |
| 数据 | **抓取投递是新鲜度天花板**：cron `*/15`、计划 36 槽/天，实测仍只有 ~5–6 次 scheduled run/天（今日数据提交 00:33Z / 06:12Z / 12:53Z，间隔 ≈5.7 h） | 应用里价格最坏可到 5–6 小时前（基础设施议题，§11，交 owner 决策） |
| 数据 | `update-profile.json` 仅 **22 个样本 / 14 个小时** | 用户可见的"何时变价"洞察仍不可做（阈值见 §11） |
| 数据 | 站点级历史 180 天保留、无月 rollup；12 天 594 KB（≈+45 KB/天 → 180 天 ≈ 8 MB） | 1A 对站点不可达；盯 `check_data_size.py` 40 MB 预算 |
| 数据 | 上游无站点 ID / city / 单站更新时间；官方数据含 1 条无效坐标（"Hub Régie" (0,0)，前端已排除） | 键脆弱；"这个价几点报的"仍答不了 |
| 功能 | 无分享/深链；无深色模式；列表项是 div（无 role/tabindex，键盘无法从列表打开站点） | 见 §10，未占本日额度 |
| 工程 | 无前端单元测试（只有 E2E） | 重构风险 |

## 5. 用户声音

- **owner 一手反馈**：#41（网站 192.9 vs 现场 186.9 → 部署拿旧 checkout，PR #42 修复）；#43（"到达现场后油价不准"→ 自适应抓取，投递频率仍是天花板）。
- **竞品 App 评论（最近 50 条，2026-09-29 复核：均分 3.42★、17 条 1–2★）**：11–13 条骂广告、9 条质疑价格准确性、
  提醒推错站（4★, 09-15）、点开通知要直达降价站（3★, 05-18）、**要能滚动查看附近站点（3★, 08-05）**、要主屏小组件（4★, 07-26）、
  新增 1★（09-17）"Route — Mauvaise indication de route"。
  RSS：`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`（该 RSS 只返回最新 50 条，page≥2 为空）

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- 站点 GeoJSON 属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。

## 7. 关键实测数据（供后续提案引用）

- **附近站点密度**：≤10 km / ≤25 km —— Montréal 中心 161 / 484、Laval 114 / 469、Québec 市 118 / 215、Saguenay 45 / 82、
  Trois-Rivières 54 / 75、Sherbrooke 45 / 73、Gatineau 48 / 66（→ #50 的量化依据）。
- **抓取投递**：~5–6 次/天（今日 00:33Z、06:12Z、12:53Z）；`update-profile` 样本 22；`fetch-schedule` 计划 36 槽/天。
- **价格水平**：regular 166.9 / 中位 192.9 / 239.3；区域历史 63 点/区；站点历史 12 天 594 KB。
- **价格波动**（区域 6h 桶）：|Δ| 中位 0.10¢、均值 0.65¢；23% 步长 ≥1¢。
- **区域内基准**：Montréal 中位 196.9 vs 最低 186.9（10.0¢＝5.00$/50L）；Montérégie 191.9 / 175.1（16.8¢＝8.40$）。
- **⭐ Régie 每周 Bulletin（已验证可解析）**：单文件 `…/Publications-hebdomadaires/Bulletin/bulletin.pdf`，17 区 × 3 油品的
  上周/本周均价、环比、**Marge de détail estimée (hors taxes)** + 蒙特利尔装车价（Tableau 4）。
  2026-09-21 周 regular 毛利：Saguenay 3.0 ↔ Nord-du-Québec 18.0（Québec 加权 8.7）；diesel 为负（−0.9~−4.9）；Nunavik `n/d`。

## 8. 竞争格局（2026-09-29）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟）→ 竞争点＝**怎么用数据 + 交付是否新鲜**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错（每站"Signaler une inexactitude"按钮）；**无筛选**；首屏即全部站点；发布周报毛利 | Protégez-Vous 点出它"**无法筛选、只能逐个 hover**" ← 我们的最硬差异；#49 将补上毛利口径 |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」、按城市阈值提醒、「près de moi」（无 GPS 用近似定位兜底）、SEO 页、API；主打 "temps réel / Dernière mise à jour : À l'instant" | 新鲜度已追平（#47）；就近列表体验仍更好（#50）；毛利口径 #49 |
| **CAA-Québec Info Essence** | 会员 Web 工具 | **"si c'est le bon moment de faire un détour"**（prix réaliste = Régie 数据 + Bloomberg Oil Buyer's Guide + 去年平均零售毛利） | 我们无"是否值得绕路"的判断（候选见 §10/§11） |
| **Prix Essence Québec（App）** | 可安装原生 App | 收藏+提醒、**"距离你愿意开的范围"**、**预测未来几天涨价概率**；导航指引被吐槽；近 50 条 3.42★ | 我们不预测（数据不足，且诚实性优先）；导航用系统地图（更可靠） |
| **Radio-Canada 看板 / EssenceQuébec.com** | 媒体/老站 | 区域均价、最高/最低站、**每小时更新**；红绿配色地图 | 他们每小时更新（我们 ~5–6 次/天，见 §4）；我们逐站标签+筛选更强 |
| **metsdugaz.com / 8 个 0★ OSS 克隆**（如 `a-rousseau/prix-essence-quebec`、`MedRiadhKhalli/…GasPricesQC`、`chanlan1105/gas-price-map`） | 纯 Web / 爱好者项目 | 同一官方数据的简单地图 | 说明**数据本身已无壁垒**；我们的护城河是功能深度 + 诚实性 + 三语 + 零运维 |
| **Google Maps / Waze** | 通用地图/导航 | 会显示油价（偶有区域失效） | 通用巨头不稳定 = 机会窗口 |

## 9. 本轮提案（已建 issue）

**自动刷新后，站点卡片/详情面板必须与最新快照一致（同一屏不能出现两个价格）** → issue **#51**（`pm-proposal`）。
理由：这是 #47 引入的**可测**不一致——卡片持有旧 feature，而基准/`currentStations` 已是新的，还会让加油日志**记录错误单价**、
污染关注提醒的 `lastSeenPrice`。与 owner 的 #41（"价格看起来不对"）同属信任主题，修法小（`stationId` 重定向 + 重渲染 + 边界处理）。

## 10. 已评估但不建议（本轮，避免重复提议）

- **"绕路是否划算"（détour 盈亏平衡）**：第三方证据不错（CAA 的卖点、Prix Essence Québec 的"愿意开的距离"），
  但与待批准的 #50（距离排序）重叠且需要"直线距离×2 + 油耗 + 加注量"等假设 → **等 #50 落地后再评估**。
- **"Signaler une inexactitude" 报到我们这里**：官方已有逐站纠错流程（Régie FAQ 明确指引）；而把用户举报塞进 GitHub issue 会**污染 issue-agent 流水线** →
  不提议；若 owner 想要，需先定"举报去哪"。
- **深色模式**（仅 1 条评论要求，外观层）、**分享/深链**（无直接用户证据）、**列表键盘可达/a11y**（受众有限，可随 #50 顺手做）。
- **站点级 1A rollup**：先看体积与价值（§4）。
- **接入每日 `composantes`（每 MRC 一个 PDF）或 `rqe.pdf`**：成本/增量收益不划算 → 不接入（周报已覆盖）。
- **改抓取触发频率**：基础设施议题，交 owner 决策（§11）。
- **变价时段洞察**：样本 22 条，统计不可靠（阈值见 §11）；**沿途/路线加油**：需 Mapbox Directions（计费）；
  **per-city SEO 落地页**：需 prerender/多页构建 → 暂缓。

## 11. 待调研问题（下次优先）

- #50 若落地：进"绕路盈亏平衡"（含假设显式化与"不精确但诚实"的文案规范）。
- #49 若获批：毛利历史（存 N 周）是否值得做"连续高于全省均值"提示；schema 需从可扩展出发。
- 抓取投递：是否有不引入第三方依赖的办法提升 scheduled run 频率（合并到已有高频工作流 / 外部触发器 / 重排槽位）？
- 变价时段洞察阈值：每时段 ≥14 天样本后才可对用户宣称。
- 站点历史 180 天后体积（≈8 MB?）与 40 MB 预算余量；是否月粒度 rollup 或缩短保留。
- 上游是否可能新增站点 ID / city / 单站更新时间字段（键稳定性 + 时效叙事）？
- 列表分批渲染上限场景（全省 2 459 条）是否需要 `content-visibility` 虚拟化（随 #50 验证）。

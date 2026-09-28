# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-28（第 10 次运行）。**#49 已提交但仍在等 owner 批准**（标签仅 `agent-seen`，triage bot 已 @owner 询问）；
除此之外无 open PR、无遗留提案。最近落地：#47/#48（新鲜度可见 + 自动刷新）。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（可安装 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方 GeoJSON（`regieessencequebec.ca/stations.geojson.gz`），GitHub Actions 抓取并提交回仓库；
  抓取时机由学到的变价时段 + 每日预算 + 3h 兜底门控（#43）
- 规模（2026-09-28 实测）：**2 460 个站点**（1 条无效坐标被前端排除）、18 区、三油品；
  regular **166.9 / 中位 192.9 / 239.3**；快照 `generated_at` 14:25Z
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-09-28）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 / 价格标签 / 最低价高亮 | 聚类（z<10）+ 渐变点 + 价格标签（z≥11）+ 最低价脉冲 `js/map.js`、`js/stats.js` | 既有 |
| 全省优先视图 + 位置记忆 | 首次访问不套半径、全省 fitBounds；半径/定位/点地图进入半径模式；`qc-gas-view` | ✅ #39 |
| 筛选即所得 / 离线搜索 | 品牌、油品、价格、区域、半径；搜索可跨全省 `js/filters.js`、`js/search.js` | ✅ #13 |
| 收藏 / 关注提醒 | 置顶与仅看收藏；阈值触发 + "自上次访问以来的真实变动" `js/favorites.js`、`js/watch.js` | ✅ #14 / #31 |
| 站点当日基准 / 覆盖门控 | 区域中位、与中位差、区域内百分位、50 L 金额差；区间超覆盖即禁用 `js/benchmark.js` | ✅ #34 |
| **官方周报毛利 + 官方区域均价** | pipeline + 趋势看板列 + 站点卡片行（`data/regie-margin.json`） | 🚧 #49 待批准 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/对比真实区域历史均价的节省额 `js/fillups.js` | ✅ #29 |
| 可安装 PWA + 更新策略 + 新鲜度 | 导航 network-first + 版本化缓存 + 更新提示；`js/freshness.js`：header 显示"mis à jour il y a X"、可见时每 10 min/回前台自动原地刷新（幂等 `applyStations()`） | ✅ #37 / #45 / #47 |
| 品牌对标 / 区域排行 / 趋势看板 | `js/stats.js`、`js/dashboard.js` | ✅ #15 / #17 |
| 行程油费 / 导航 / 数据透明度 | 站点详情 L/100km × 距离；Google/Apple Maps 跳转；卡片显示 `generated_at` | ✅ #16 |
| 价格历史 | 区域级真实 6h 桶 + 站点级真实日粒度（180 天；现 11 天 553 KB） | ✅ #32 |
| 自适应抓取（pipeline） | 学到的小时权重 → 36 槽/天计划 + 门控（min gap 10 min、3h 兜底）+ 体积预算 `scripts/fetch_strategy.py` | ✅ #43 |
| 体验 / 测试 | 移动底部抽屉、44px、键盘、三语；Playwright E2E 80+；pipeline 有 `scripts/test_*.py` | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——官方平台恰恰缺筛选（只能逐点 click）。
2. **打开即用、无广告、可安装**：竞品 App 近 50 条评论里 11–13 条在骂广告；我们免安装可用也能装主屏。
3. **数据诚实性成为资产**：无合成/外推（#32）、覆盖门控（#34）、离线标注"最后同步"（#37）、无效坐标不冒充站点（#39）、
   新鲜度可见且自动刷新（#47）。
4. **没有后端也能做决策工具**：搜索/收藏/关注/排行/油费/加油日志/站级基准，全部 localStorage + 静态数据。
5. **全省优先 + 位置记忆**；**三语**（fr/en/zh，竞品中未见同等支持）；隐私友好、零运维。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 功能（本日提案） | **侧栏列表硬截断 30 条且只能按价格排**：`js/stats.js` `filteredStations.slice(0, 30)`；实测附近站点数（≤25 km）：Montréal 中心 **484**、Laval **469**、Québec 市 **215**、Saguenay 82、Trois-Rivières 75、Sherbrooke 73、Gatineau 66 → **大城市用户只能看到其中 ~6%**；且无距离排序（"最近的便宜站"无法直接得到）；`Favoris seulement` 也被同一截断（收藏 >30 时列表显示不全，与收藏计数不一致） | 列表无法承担"就近决策"，用户被迫回到地图逐点找（竞品评论原话：*"on devrait pouvoir voir défiler les stations à proximité"*） |
| 数据 | **抓取投递是新鲜度天花板**：cron `*/15`（96 次/天）、计划 36 槽/天，实测仍只有 **~6 次 scheduled run/天**（中位间隔 ≈4.5 h）；门控每次都 due | 应用里的价格最坏可到 4–5 小时前（基础设施议题，见 §11，交 owner 决策） |
| 数据 | `update-profile.json` 仅 **18 个样本 / 14 个小时** | 用户可见的"何时变价"洞察仍不可做（阈值见 §11） |
| 数据 | 站点级历史 180 天保留、无月 rollup；11 天 553 KB（≈+45 KB/天 → 180 天 ≈ 8 MB） | 1A 对站点不可达；盯 `check_data_size.py` 40 MB 预算 |
| 数据 | 上游无站点 ID / city / 单站更新时间；官方数据含 1 条无效坐标（"Hub Régie" (0,0)，前端已排除） | 键脆弱；"这个价几点报的"仍答不了 |
| 功能 | 无分享/深链；无深色模式；无 a11y 键盘列表导航（列表项是 div，无 role/tabindex） | 见 §10，未占本日额度 |
| 工程 | 无前端单元测试（只有 E2E） | 重构风险 |

## 5. 用户声音

- **owner 一手反馈**：#41（网站 192.9 vs 现场 186.9 → 部署拿旧 checkout，PR #42 修复）；#43（"到达现场后油价不准"→ 自适应抓取，但投递频率仍是天花板）。
- **竞品 App 评论（最近 50 条，2026-09-28 复核；均分 3.42★、17 条 1–2★）**：
  - 新增两条（09-17）：1★ *"Route — Mauvaise indication de route"*（导航指引差）、5★ 表扬价格有用；
  - 稳定主题：**11–13 条骂广告**、**9 条质疑价格准确性**、提醒推错站（4★）、要点开通知直达降价站（3★）、
    **要"能直接看到/滚动附近的站点列表"（3★, 08-05）**、要主屏小组件（4★）。
  RSS：`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- 站点 GeoJSON 属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。

## 7. 关键实测数据（供后续提案引用）

- **附近站点密度（本轮新增）**：≤10 km / ≤25 km 站点数 —— Montréal 中心 **161 / 484**、Laval 114 / 469、Québec 市 118 / 215、
  Saguenay 45 / 82、Trois-Rivières 54 / 75、Sherbrooke 45 / 73、Gatineau 48 / 66。→ 侧栏固定只显示 30 条。
- **抓取投递**：scheduled run 全天 ~6 次（中位间隔 ≈4.5 h）；今日数据提交 14:25Z。
- **价格水平**：regular 166.9 / 中位 192.9 / 239.3；区域历史 61 点/区；站点历史 11 天 553 KB；`update-profile` 样本 18。
- **价格波动**（区域 6h 桶）：|Δ| 中位 0.10¢、均值 0.65¢；23% 步长 ≥1¢。
- **区域内基准**：Montréal 中位 196.9 vs 最低 186.9（10.0¢＝5.00$/50L）；Montérégie 191.9 / 175.1（16.8¢＝8.40$）。
- **⭐ Régie 每周 Bulletin（已验证可解析）**：单文件 `…/Publications-hebdomadaires/Bulletin/bulletin.pdf`，17 区 × 3 油品的
  上周/本周均价、环比、**Marge de détail estimée (hors taxes)** + 蒙特利尔装车价（Tableau 4）。
  2026-09-21 周 regular 毛利：Saguenay 3.0 ↔ Nord-du-Québec 18.0（Québec 加权 8.7）；diesel 为负（−0.9~−4.9）；Nunavik `n/d`。

## 8. 竞争格局（2026-09-28）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟）→ 竞争点＝**怎么用数据 + 交付是否新鲜**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错；**无筛选**；首屏即全部站点；自己发布周报毛利 | 筛选/标签仍是硬差异（#49 将补上毛利口径） |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」、按城市阈值提醒、**「près de moi」专页：列出最近站点、无 GPS 时"近似定位"兜底**、SEO 页、API；首页主打 "temps réel / Dernière mise à jour : À l'instant" | 新鲜度表达已追平（#47）；**"就近站点列表"体验仍明显更好**（我们只给 30 条、不能按距离排） |
| **CAA-Québec Info Essence** | 会员 Web 工具 | 「Faire le plein ou pas?」（prix réaliste vs 区域均价） | #49 后将具备同类口径 |
| **achetezlemeilleur.ca** | 内容站工具 | 区域表 Min/Max/Écart/vs Hier/vs Moy.、排除 Costco | 排行我们已有 |
| **metsdugaz.com** | 纯 Web | per-city SEO 页 | 无 per-city 落地页 |
| **Prix Essence Québec（App）** | 可安装原生 App | 收藏+提醒、短期预测、行程计算；导航指引被吐槽；近 50 条 3.42★ | 我们已可安装；差"预测/小组件" |
| **Google Maps / Waze** | 通用地图/导航 | 会显示油价（偶有区域失效） | 通用巨头不稳定 = 机会窗口 |

## 9. 本轮提案（已建 issue）

**侧栏列表：看全 + 按距离排（解除 30 条上限，收藏模式不截断）** → issue **#50**（`pm-proposal`）。
理由：实测大城市附近有 200–480 个站点，而列表固定只给 30 条、且只能按价格排 → 用户无法完成"就近决策"，
只能回地图逐点找（正是竞品评论与 gasquebec「près de moi」都指向的体验）。修法便宜：分批追加 + 排序切换（持久化）+ 边界处理。

## 10. 已评估但不建议（本轮，避免重复提议）

- **深色模式**：仅 1 条评论要求，属外观层，且需同时改 Mapbox style 与全套 CSS → 暂缓。
- **卡片内"附近站点对比"（400 m 内谁更便宜）**：与列表改进重叠 → 等列表改进落地后再评估。
- **分享/深链**：无直接用户证据；**a11y 列表键盘导航**：真实但受众有限；**站点级 1A rollup**：先看体积与价值。
- **接入每日 `composantes`（每 MRC 一个 PDF ≈100 文件/天）**、**每日 `rqe.pdf`（MRC 周均价）**：成本/增量收益不划算 → 不接入。
- **改抓取触发频率**：基础设施议题，交 owner 决策（§11）。
- **变价时段洞察**：样本 18 条，统计不可靠（阈值见 §11）。
- **沿途/路线加油**：需 Mapbox Directions（token scope + 计费）；**per-city SEO 落地页**：需 prerender/多页构建 → 暂缓。

## 11. 待调研问题（下次优先）

- 列表分批渲染在上限场景（全省 2 459 条 / 搜索全量）的表现与是否需要 `content-visibility` 虚拟化；"看全"是否需要"全选导出"之外的用途。
- 抓取投递：是否有不引入第三方依赖的办法提升 scheduled run 频率（合并到已有高频工作流 / 外部触发器 / 重排槽位）？
- 变价时段洞察的样本阈值：建议每时段 ≥14 天样本后才对用户宣称。
- #49 若获批：毛利历史（存 N 周）是否值得做"连续高于均值"提示；schema 需先从可扩展出发。
- 站点历史 180 天后体积（≈8 MB?）与 40 MB 预算余量；是否月粒度 rollup 或缩短保留。
- 上游是否可能新增站点 ID / city / 单站更新时间字段（键稳定性 + 时效叙事）？

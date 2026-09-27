# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-27（第 9 次运行）。**提案队列为空**。上次运行后落地 #47/#48（新鲜度可见 + 自动刷新）。
上一轮记录的"触发投递瓶颈"仍在（全天只有 ~6 次 scheduled run）——属 owner 决策的基础设施议题。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（可安装 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方 GeoJSON（`regieessencequebec.ca/stations.geojson.gz`），GitHub Actions 抓取并提交回仓库；
  抓取时机由学到的变价时段 + 每日预算 + 3h 兜底门控（#43）
- 规模（2026-09-27 实测）：**2 460 个站点**（1 条无效坐标被前端排除）、18 区、三油品；
  regular **166.9 / 中位 192.9 / 239.3**；快照 `generated_at` 15:35Z
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-09-27）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 / 价格标签 / 最低价高亮 | 聚类（z<10）+ 渐变点 + 价格标签（z≥11）+ 最低价脉冲 `js/map.js`、`js/stats.js` | 既有 |
| 全省优先视图 + 位置记忆 | 首次访问不套半径、全省 fitBounds；半径/定位/点地图进入半径模式；`qc-gas-view` | ✅ #39 |
| 筛选即所得 / 离线搜索 | 品牌、油品、价格、区域、半径；搜索可跨全省 `js/filters.js`、`js/search.js` | ✅ #13 |
| 收藏 / 关注提醒 | 置顶与仅看收藏；阈值触发 + "自上次访问以来的真实变动" `js/favorites.js`、`js/watch.js` | ✅ #14 / #31 |
| 站点当日基准 / 覆盖门控 | 区域中位、与中位差、区域内百分位、50 L 金额差；区间超覆盖即禁用 `js/benchmark.js` | ✅ #34 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/对比真实区域历史均价的节省额 `js/fillups.js` | ✅ #29 |
| 可安装 PWA + 更新策略 + **新鲜度** | 导航 network-first + 版本化缓存 + 更新提示；`js/freshness.js`：header 显示"mis à jour il y a X"（由展示快照推导）、可见时每 10 min + 回前台自动原地刷新（幂等 `applyStations()`，保留筛选/视图） | ✅ #37 / #45 / #47 |
| 品牌对标 / 区域排行 / 趋势看板 | `js/stats.js`、`js/dashboard.js` | ✅ #15 / #17 |
| 行程油费 / 导航 / 数据透明度 | 站点详情 L/100km × 距离；Google/Apple Maps 跳转；卡片显示 `generated_at` | ✅ #16 |
| 价格历史 | 区域级真实 6h 桶 + 站点级真实日粒度（180 天；现 10 天 511 KB） | ✅ #32 |
| 自适应抓取（pipeline） | 学到的小时权重 → 36 槽/天计划 + 门控（min gap 10 min、3h 兜底）+ 体积预算 `scripts/fetch_strategy.py` | ✅ #43 |
| 体验 / 测试 | 移动底部抽屉、44px、键盘、三语；Playwright E2E 80+；pipeline 有 `scripts/test_*.py` | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——官方平台恰恰缺筛选（只能逐点 click）。
2. **打开即用、无广告、可安装**：竞品 App 近 50 条评论里 11–13 条在骂广告；我们既能免安装即用，也能装主屏。
3. **数据诚实性成为资产**：无合成/外推（#32）、覆盖门控（#34）、离线标注"最后同步"（#37）、无效坐标不冒充站点（#39）、
   新鲜度可见且自动刷新（#47）。
4. **没有后端也能做决策工具**：搜索/收藏/关注/排行/油费/加油日志/站级基准，全部 localStorage + 静态数据。
5. **全省优先 + 位置记忆**；**三语**（fr/en/zh，竞品中未见同等支持）；隐私友好、零运维。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 数据（最高优先） | **完全没有"官方价格组成/零售毛利"口径**：我们只有站点价格，无法回答"这个价是否合理/我这区是不是被多收"。竞品都在用这套官方口径（gasquebec「prix juste」、CAA「prix réaliste」、achetezlemeilleur 区域对比） | 缺一块最权威的"贵不贵"解释力（本轮已验证可行，见 §7/§9） |
| 数据交付 | **抓取投递是新鲜度天花板**：cron 配置 `*/15`（96 次/天）、计划 36 槽/天，实测只有 **~6 次 scheduled run/天**（中位间隔 ≈4.5 h）；门控每次都 due | 应用里的价格最坏可到 4–5 小时前（属基础设施议题，见 §11；已交 owner 决策） |
| 数据 | `update-profile.json` 仅 **13 个样本 / 12 个小时**（12 次观察到变化） | 用户可见的"何时变价"洞察仍不可做（阈值见 §11） |
| 数据 | 站点级历史 180 天保留、无月 rollup；10 天 511 KB（≈+45 KB/天 → 180 天 ≈ 8 MB） | 1A 对站点不可达；盯 `check_data_size.py` 40 MB 预算 |
| 数据 | 上游无站点 ID / city / 单站更新时间；官方数据含 1 条无效坐标（"Hub Régie" (0,0)，前端已排除） | 键脆弱；"这个价几点报的"仍答不了 |
| 功能 | 站点列表硬截断 **30 条**；无按距离排序；无分享/深链；无深色模式 | 见 §10，未占本日额度 |
| 工程 / a11y | 无前端单元测试（只有 E2E）；地图 canvas 与列表项对键盘/读屏不友好 | 重构风险；a11y 硬伤 |

## 5. 用户声音

- **owner 一手反馈（#41, 09-22）**：网站显示 Crevier（DDO）regular **192.9**，现场 **186.9** → 根因是部署拿了旧 checkout（PR #42 修复）。
  教训：**"数据看起来旧"先查交付链路**。
- **owner 诉求（#43, 09-25）**："到达现场后油价不准"，要求自适应抓取且不压垮上游 → 已实现（#44）；但**投递频率仍是天花板**（§4/§7）。
- 竞品 App 评论（最近 50 条）：均分 **3.44★**、17 条 1–2★、11–13 条骂广告、**9 条质疑价格准确性**、"点开通知要直达降价站"、要"主屏小组件"、要"直接看到附近的站点清单"。
  RSS：`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- 站点 GeoJSON 属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。

## 7. 关键实测数据（含本轮新增的官方口径调研）

- **抓取投递**（2026-09-26→27）：scheduled run 全天 ~6–7 次（间隔 38 min–5.4 h），数据提交 22:03Z、22:41Z、00:34Z、05:46Z、11:11Z、15:39Z。
- **价格水平**：regular 166.9 / 中位 192.9 / 239.3；区域历史 59 点/区；站点历史 10 天 511 KB。
- **价格波动**（区域 6h 桶）：|Δ| 中位 0.10¢、均值 0.65¢；23% 步长 ≥1¢。
- **区域内基准**：Montréal 中位 196.9 vs 最低 186.9（10.0¢＝5.00$/50L）；Montérégie 191.9 / 175.1（16.8¢＝8.40$）。
- **⭐ Régie 每周 Bulletin（本轮实测可解析，单个 PDF，稳定 URL，Excel 生成 → 文本层干净）**：
  `https://www.regie-energie.qc.ca/storage/app/media/consommateurs/informations-pratiques/prix-petrole/publications/Publications-hebdomadaires/Bulletin/bulletin.pdf`（2026-09-25 版，4 页，870 KB）
  - Tableau 1/2/3：**17 个行政区的 regular/super/diesel 上周均价、本周均价、环比、以及「Marge de détail estimée (hors taxes)」**
  - Tableau 4：蒙特利尔**最低装车价（rampe）**按周（regular/super/diesel/mazout léger）
  - 实测 2026-09-21 周 regular 毛利：Saguenay–Lac-Saint-Jean **3.0**、Outaouais 4.7、Centre-du-Québec 5.6、Montérégie 6.7、Lanaudière 8.0、
    Chaudière-Appalaches 8.6、**Québec 加权 8.7**、Estrie 8.9、Mauricie 9.0、Laurentides 9.1、Capitale-Nationale 9.3、Côte-Nord 10.8、
    Laval 11.3、Montréal 11.5、Abitibi 11.6、Bas-Saint-Laurent 11.9、Gaspésie **15.9**、Nord-du-Québec **18.0** → **区间 15¢/L（50 L 差 7.50$）**
  - 细节：**diesel 毛利本周为负**（−0.9 ~ −4.9¢）；Nunavik 为 `n/d`；补贴/暂停联邦消费税等脚注存在 → 文案不可简化
  - 另有两个官方来源但都不划算：每日 `composantes`（**每 MRC 一个 PDF ≈100 文件/天**，太重）、每日 `rqe.pdf`（MRC 周均价，我们自己算得更细）

## 8. 竞争格局（2026-09-27）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟）→ 竞争点＝**怎么用数据 + 交付是否新鲜**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错；**无筛选**；首屏即全部站点 | 筛选/标签仍是硬差异；但它自己也发布**周报毛利**（我们尚未利用） |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」（用 Régie 每日组成 + 正常 margin）、按城市阈值提醒、"près de moi"近似定位、SEO 页、API；首页主打 "temps réel / Dernière mise à jour : À l'instant" | 新鲜度表达我们已经追上（#47）；**价格组成的解释力仍缺** |
| **CAA-Québec Info Essence** | 会员 Web 工具 | 「Faire le plein ou pas?」（prix réaliste vs 区域均价） | 我们有站级基准（#34），但没有官方成本/毛利口径 |
| **achetezlemeilleur.ca** | 内容站工具 | 区域表 Min/Max/Écart/vs Hier/vs Moy.、排除 Costco | 排行我们已有 |
| **metsdugaz.com** | 纯 Web | per-city SEO 页 | 无 per-city 落地页 |
| **Prix Essence Québec（App）** | 可安装原生 App | 收藏+提醒、短期预测、行程计算；近 50 条评论 3.44★ | 我们已可安装；差"预测/小组件" |
| **Google Maps / Waze** | 通用地图/导航 | 会显示油价（偶有区域失效） | 通用巨头不稳定 = 机会窗口 |

## 9. 本轮提案（已建 issue）

**接入 Régie 每周 Bulletin 的「零售毛利 + 官方区域均价」** → issue **#49**（`pm-proposal`）。
理由：这是唯一一块**官方权威、单文件、可离线解析**的成本/毛利口径，能直接回答"我这区是不是被多收"（毛利 3.0¢ ↔ 18.0¢），
并给我们的实时价格一个官方周基准；也是 parity 补齐 gasquebec/CAA 的"贵不贵"叙事。范围＝一个 pipeline + 两处小 UI（趋势看板列、站点卡片行）。

## 10. 已评估但不建议（本轮，避免重复提议）

- **接入每日 `composantes` PDF（每 MRC 一个，≈100 文件/天）**：对上游太重；周报单文件已覆盖我们需要的区域口径 → 只记录，不接入。
- **接入每日 `rqe.pdf`（MRC 周均价）**：我们自己用 2 460 站实时快照算得更细 → 无收益。
- **改抓取触发频率**：基础设施议题，已交 owner 决策（§11）。
- **用户可见的"何时变价"洞察**：样本仅 13 条，统计不可靠（阈值见 §11）。
- **列表 30 条截断 / 按距离排序 / 分享深链 / 深色模式**：真实但小或属外观层。
- **站点级月度 rollup（补齐 1A）**：先看体积与价值（§4）。
- **沿途/路线加油**：需 Mapbox Directions（token scope + 计费）。
- **per-city SEO 落地页**：纯静态站需 prerender/多页构建，效果数月才能验证 → 暂缓。

## 11. 待调研问题（下次优先）

- 周报毛利历史（我们可存 N 周）是否值得做成"毛利趋势"（例：连续 4 周高于全省均值 → 提示）？先把 schema 设计成可扩展。
- 抓取投递：是否有不引入第三方依赖的办法提升 scheduled run 频率（合并到已有高频工作流 / `workflow_dispatch` + 外部触发器 / 接受现实并重排槽位）？
- 变价时段洞察的样本阈值：建议每时段 ≥ 14 天样本后才对用户宣称。
- 站点历史 180 天后体积（≈8 MB?）与 40 MB 预算余量；是否月粒度 rollup 或缩短保留。
- 上游是否可能新增站点 ID / city / 单站更新时间字段（键稳定性 + 时效叙事）？
- 站点列表 30 条截断 / 按距离排序 / 分享深链：优先级是否上升（与"就近决策"相关）？

# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-20（第 5 次运行）。**提案队列已清空**：#13–#17、#29、#31、#32、#34 全部落地
（最近两条：#34 站点卡片当日基准 + 覆盖门控 commit `0b8af96`；#31 关注站价格提醒 commit `eb91f8a`）。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方公开 GeoJSON（`https://regieessencequebec.ca/stations.geojson.gz`），
  每小时 GitHub Actions 抓取（`scripts/download_data.py` → `process_data.py` → `append_history.py`）并提交回仓库
- 规模（2026-09-17 实测）：**2 461 个站点**、18 个行政区域、三油品；全省均价 **195.1¢**，min 152.2 / max 240.0
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告
- **部署与运行方式**：Vite 构建 → `dist/`，`data/`、`logos/` 由 workflow 手动拷贝；`static.yml` 的 push paths
  只含 `src/** js/** css/** index.html vite.config.** package.json package-lock.json`（另有每小时 cron 兜底部署）

## 2. 能力清单（已核对代码，2026-09-20）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 | Mapbox light-v11；聚类（z<10）+ 价格渐变点 + **站点价格标签**（z≥11）`js/map.js` | 既有 |
| 最低价高亮 | 筛选结果中最低价站点脉冲动画 + 列表 best 标记 `js/stats.js` | 既有 |
| 筛选即所得 | 品牌、油品、价格双滑块、区域、半径 5/10/25/50km `js/filters.js` | 既有 |
| 站点搜索 | 地址/城市/邮编/品牌/站名，纯离线 `js/search.js` | ✅ #13 |
| 收藏 | localStorage、列表置顶、仅看收藏 `js/favorites.js` | ✅ #14 |
| **关注提醒** | 阈值触发 + 自上次访问以来的**真实**变动；侧栏"我的关注"（空时 `hidden`）；详情面板铃铛/阈值输入；`js/watch.js`（414 行）+ `js/map.js` 钩子 | ✅ #31 |
| **站点当日基准** | 区域中位 / 与中位差 / 区域内百分位 / 50 L 金额差 / 与区域最低价差距；样本 <10 降级；`js/benchmark.js` | ✅ #34 |
| **数据覆盖门控** | 区间 pills 超覆盖即禁用 + "数据覆盖 N 天"；`getStationHistoryCoverage()` `js/history.js` + `js/station-card.js` | ✅ #34 |
| 品牌价格对标 | 品牌行显示站数 + 均价 + 相对全省均价差值 `js/stats.js` | ✅ #15 |
| 区域排行表 / 趋势看板 | 18 区 min/avg/max/价差/vs 昨日 + 区域趋势图 `js/dashboard.js` | ✅ #17 |
| 行程油费估算 | 站点详情 L/100km × 距离 `js/station-card.js` | ✅ #16 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/对比真实区域历史均价的节省额 `js/fillups.js` | ✅ #29 |
| 价格历史 | 区域级真实 6h 桶（raw 14 天 / daily 12 月 / monthly 永久）+ 站点级真实日粒度（保留 180 天，自 2026-09-18 起） | ✅ #32 |
| 导航 / 数据透明度 | Google/Apple Maps 跳转；卡片显示 `generated_at` | 既有 |
| 体验 | 移动底部抽屉、44px、键盘 f/l/t/Esc、三语、`prefers-reduced-motion` | 既有 |
| 测试 | 仅 Playwright E2E（`tests/app.spec.js`，70+ 项） | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——官方平台恰恰缺这个（Protégez-Vous、TVA 都点出官方只能逐点 click）。
2. **打开即用、无广告、无安装**：竞品 App 近 50 条评论里 11–13 条在骂广告，这是最硬的情感差异。
3. **数据诚实性成为资产**：#32 删掉全部合成/外推，#34 用"当日本区真实快照"填补空白并给区间加覆盖门控。
4. **没有后端也能做决策工具**：搜索/收藏/关注提醒/排行/油费/加油日志/站级基准，全部 localStorage + 静态数据。
5. **三语**（fr/en/zh，竞品中未见同等支持）+ 隐私友好 + 零运维成本。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 平台 | **完全没有 PWA**：无 manifest / service worker / theme-color / apple-touch-icon（`index.html`、`js/` 全无相关代码） | 无法"安装到主屏"（Chrome 的安装提示要求 manifest + 带 fetch 的 SW）；无离线外壳；**iOS 也没有 Web Push 资格（要求已装到主屏）**——#31 的提醒因此只能停在"应用内" |
| 功能 | 无离线/陈旧数据提示：断网或缓存过期时，界面不区分"实时"与"缓存" | 与我们的数据诚实立场不一致；9/50 竞品评论正是在质疑价格时效 |
| 功能 | 站点列表硬截断 **30 条**；无按距离排序；无分享/深链（无法把某站发给别人或收藏为书签） | 大城市/社交传播受限 |
| 功能 | 默认中心 `[-73.7, 45.45]`（Montréal 西岛）+ 默认半径 25 km → **首次访问只显示 Montréal 一带**，且不记忆上次位置 | 非 Montréal 用户（全省约一半人口）第一眼看到的不是"我附近" |
| 功能 | 无深色模式（light-v11 固定） | 有用户明确要求；外观层，优先级低 |
| 数据 | 站点级历史 **180 天保留、无月粒度 rollup**，而 UI 仍有 1A 区间（现已被覆盖门控禁用） | 1A 对站点永远不可达（要么做 rollup，要么移除该区间） |
| 数据 | **站点历史文件增长快**：3 天 = 221 KB 未压缩（≈+41 KB/天，180 天 ≈ 7 MB），是 `data/` 增长最快的文件 | 需持续盯 `check_data_size.py`（40 MB 总预算） |
| 数据 | 上游无站点 ID / city / 单站更新时间；我们小时级抓取且无陈旧告警 | 键脆弱 + 上游失效会静默变旧 |
| 工程 | 无单元测试（只有 E2E）、无真实用户分析 | 重构风险 |
| 无障碍 | 地图 canvas 对读屏不可达；列表项是 div，无 role/tabindex | a11y 硬伤，受众有限 |

## 5. 用户声音：竞品 App 评论主题（2026-09-18 复核，无新增）

来源：App Store「Prix Essence Québec : Info Gaz」(id 6739227128, CA) 最近 50 条，公开 RSS
`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`。

- **均分仅 3.44★**（展示 4.7★ 是历史 5.6k 评分），**17 条 1–2★**，**11–13 条抱怨广告** → 验证"无广告"叙事。
- **9/50 质疑价格准确性**（"plus de 1 fois sur 2 le prix n'est pas le bon bien que « mis à jour il y a 5 minutes »"，3★）。
- 提醒类两条都吐槽质量：推错站（4★, 09-15）、点开不直达那家降价站（3★, 05-18）→ #31 已按"精准 + 直达"实现。
- 仍开放的诉求：**桌面小组件**（=主屏存在感，2026-07-26, 4★）、沿途规划、深色模式、可下滑的附近站点列表（我们已有，30 条上限除外）。

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- GeoJSON 站点属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。
- 想再"进货"只能靠 Régie 公开出版物：**每日 composantes（PDF，按区域/MRC）**、**每周三 Bulletin PDF**（含各区 marge de détail、12 个月趋势）。

## 7. 关键实测数据（供后续提案引用）

- **价格波动**（`data/history.json`，09-10→09-18，18 区 × 540 个相邻 6h 桶）：区域均价 |Δ| 中位 **0.10¢**、均值 0.65¢；
  23% 步长 ≥1¢、37% 不变；单日区内振幅均值 1.22¢、最大 8.1¢ → **阈值提醒 > 逐次 delta**（#31 的依据）。
- **站间价差**（当前快照）：全省 regular 152.2–240.0¢ → 50 L 差 **43.90$**；Estrie 区内 47.7¢ → 23.85$。
- **区域内基准**（2026-09-19 快照，regular）：Montréal n=226 min 186.9 / 中位 196.9（差 10.0¢＝5.00$/50L）；
  Montérégie 175.1 / 191.9（16.8¢＝8.40$）；Capitale-Nationale 183.9 / 195.9（12.0¢）；Nord-du-Québec 195.4 / 196.4（1.0¢，n=12）。
- **站点历史增长**：09-18→09-20 三天，`station-history.json` 221 KB 未压缩（≈+41 KB/天；180 天 ≈ 7 MB，`tiers={daily_days:180, monthly:'none'}`，2 460 站全入账）。

## 8. 竞争格局（2026-09-20）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟）→ 竞争点＝**怎么用数据**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错；**无筛选** | 价格标签+筛选仍是最硬差异 |
| **CAA-Québec Info Essence** | 会员 Web 工具 | **"Faire le plein ou pas?"**（prix réaliste vs 区域均价，周一至周五） | 我们有站级基准（#34），但无"官方价格组成"口径 |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」、**按城市阈值提醒**、城市/区域 SEO 页、API、账号体系 | 提醒（=#31）与站级判断（=#34）已补上；仍缺 SEO 落地页 |
| **achetezlemeilleur.ca** | 内容站工具 | 区域表 Min/Max/Écart/vs Hier/vs Moy.、排除 Costco | 排行我们已有 |
| **metsdugaz.com** | 纯 Web | per-city SEO 页（2026 新入场） | 无 per-city 落地页 |
| **Prix Essence Québec（App）** | **原生可安装 App**（广告+IAP） | 收藏+提醒、短期预测、行程计算；近 50 条评论 3.44★ | **可安装性/主屏存在感**是它相对我们的结构性优势 |
| **Google Maps / Waze** | 通用地图/导航 | **会显示油价**（2026 指南与用户帖均可见；有"加拿大不显示/间歇失效"的报告） | 通用巨头不稳定 = 我们的机会窗口；但需承认它是真竞品 |

## 9. 本轮提案（已建 issue）

**把应用变成可安装的 PWA：主屏图标 + 离线应用外壳 + 诚实的离线提示** → issue **#37**（`pm-proposal`）。
理由：这是最后一个**结构性**差距——竞品是可安装的原生 App，而我们连 Chrome 安装提示都拿不到
（要求 manifest + 带 fetch 的 SW）；同时 iOS 的 Web Push 只在"已装到主屏"时可用，
所以 #31 的提醒只能停在应用内。做 PWA 既拿到主屏入口与弱网可用性，也为未来的真·提醒铺路。

## 10. 已评估但不建议（本轮，避免重复提议）

- **"记住上次位置 / 首次不套用半径"**：真实痛点（非 Montréal 用户第一眼看不到身边油价），但与 PWA 相比收益/成本更低 → 留给下次，或随地图相关改动顺手做。
- **列表 30 条截断 / 按距离排序 / 分享深链**：真实但小；**深色模式**：外观层。
- **Régie composantes / Bulletin → prix juste**：只有 PDF，需解析 + 每日落库 + 前端，一次 PR 装不下且脆弱（§11 头号调研项）。
- **站点级月度 rollup（补齐 1A）**：先看体积与价值（见 §4 增长数据），留给 §11 调研。
- **沿途/路线加油**：需 Mapbox Directions（token scope + 计费）。
- **per-city SEO 落地页**：增长手段，但纯静态站需要 prerender/多页构建，且效果数月才能验证 → 暂缓。
- **XLSX（excel_url）接入**：字段与 GeoJSON 相同，无收益。

## 11. 待调研问题（下次优先）

- 站点历史文件 180 天后的真实体积（≈7 MB?）与 `check_data_size.py` 40 MB 预算的余量；是否要月粒度 rollup 或降低保留天数。
- Régie「Publications quotidiennes / Bulletin」是否有可稳定解析的机器可读来源（PDF 文本层 vs HTML 表格）？引用与授权？
- PWA 之后的下一步：iOS 已装主屏时的 Web Push 是否值得（无 push 服务，只能本地定时 + Periodic Background Sync，Chrome only）？
- "数据覆盖天数 / 最后同步时间"是否该做成全局信任信号（不只卡片与离线态）？
- 上游是否可能新增站点 ID / city / 单站更新时间字段（键稳定性 + 时效叙事）？
- 非 Montréal 用户的首次体验：默认中心/半径/位置记忆的最佳方案（需要可测量的替代方案，而非猜测）。

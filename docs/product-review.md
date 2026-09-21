# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-21（第 6 次运行）。**提案队列再次清空**：#13–#17、#29、#31、#32、#34、#37 全部落地
（最新：#37「可安装 PWA」commit `fc1a471`，新增 `public/manifest.webmanifest`、`public/sw.js`、`js/pwa.js`、`docs/pwa.md`）。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（现已可安装为 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方公开 GeoJSON（`https://regieessencequebec.ca/stations.geojson.gz`），
  每小时 GitHub Actions 抓取（`download_data.py` → `process_data.py` → `append_history.py`）并提交回仓库
- 规模（2026-09-21 实测）：**2 459 个站点**、18 个行政区域、三油品；regular min **164.5** / 中位 **193.9** / max **240.0**（14 站无 regular 价）
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-09-21）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 | Mapbox light-v11；聚类（z<10）+ 价格渐变点 + **站点价格标签**（z≥11）`js/map.js` | 既有 |
| 最低价高亮 | 筛选结果中最低价站点脉冲动画 + 列表 best 标记 `js/stats.js` | 既有 |
| 筛选即所得 | 品牌、油品、价格双滑块、区域、半径 5/10/25/50km `js/filters.js` | 既有（半径语义见 §4） |
| 站点搜索 | 地址/城市/邮编/品牌/站名，纯离线；搜索时跨全省 `js/search.js` | ✅ #13 |
| 收藏 | localStorage、列表置顶、仅看收藏 `js/favorites.js` | ✅ #14 |
| 关注提醒 | 阈值触发 + 自上次访问以来的**真实**变动；侧栏"我的关注"（空时 hidden）`js/watch.js` | ✅ #31 |
| 站点当日基准 | 区域中位 / 与中位差 / 区域内百分位 / 50 L 金额差 / 与区域最低价差距 `js/benchmark.js` | ✅ #34 |
| 数据覆盖门控 | 区间 pills 超覆盖即禁用 + "数据覆盖 N 天" `getStationHistoryCoverage()` `js/history.js` | ✅ #34 |
| **可安装 PWA** | manifest（192/512/maskable + apple-touch-180）、版本化 `sw.js`（外壳 cache-first；`data/*.json` network-first + `X-QCGas-From-Cache` 头）、`beforeinstallprompt` 安装入口 + iOS 引导、离线徽标"离线 · 最后同步 <时间>"；`js/pwa.js`、`docs/pwa.md`、`scripts/create_pwa_icons.mjs`（零依赖 PNG） | ✅ #37 |
| 品牌价格对标 | 品牌行显示站数 + 均价 + 相对全省均价差值 `js/stats.js` | ✅ #15 |
| 区域排行表 / 趋势看板 | 18 区 min/avg/max/价差/vs 昨日 + 区域趋势图 `js/dashboard.js` | ✅ #17 |
| 行程油费估算 | 站点详情 L/100km × 距离 `js/station-card.js` | ✅ #16 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/对比真实区域历史均价的节省额 `js/fillups.js` | ✅ #29 |
| 价格历史 | 区域级真实 6h 桶 + 站点级真实日粒度（180 天，自 2026-09-18）`js/history.js` | ✅ #32 |
| 导航 / 数据透明度 | Google/Apple Maps 跳转；卡片显示 `generated_at` | 既有 |
| 体验 / 测试 | 移动底部抽屉、44px、键盘 f/l/t/Esc、三语；Playwright E2E 68 项 | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——官方平台恰恰缺筛选（只能逐点 click）。
2. **打开即用、无广告、可安装**：竞品 App 近 50 条评论里 11–13 条在骂广告；而我们既能"免安装即用"又能"装上主屏"。
3. **数据诚实性成为资产**：删掉全部合成/外推（#32）、区间加覆盖门控（#34）、离线明确标注"最后同步"（#37）。
4. **没有后端也能做决策工具**：搜索/收藏/关注提醒/排行/油费/加油日志/站级基准，全部 localStorage + 静态数据，零运维。
5. **三语**（fr/en/zh，竞品中未见同等支持）+ 隐私友好。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 功能（最高优先） | **首次访问被静默缩小到 Montréal 一带**：`MONTREAL_CENTER=[-73.7,45.45]`（西岛）+ `zoom 10` + 默认半径 25 km，而 `updateMapStations()` 只把**过滤后**的集合写入地图 source → 实测默认半径内仅 **459 站 = 全省 18.8%**（5 km=16 站 0.7%；50 km=809 站 32.9%）。区域下拉默认"Toutes"，两者语义矛盾 | 全省约 3/4 人口不在 Montréal 行政区（2025: 2 172 259 / 约 9.2 M）；非 Montréal 用户第一眼看不到身边任何站 |
| 功能 | **不记忆位置/视图**：localStorage 只有 favorites/fillups/watch/trip/lang，无参考点或半径记忆；每次回访都要重新点地图角落的定位 FAB（还需授权） | 回访成本高；"我的城市"每次都要重建 |
| 数据 | 官方数据含无效坐标站点：**"Hub Régie"（brand "Aucun"，地址 500 Boul. René-Lévesque O., Montréal）坐标为 (0,0) 且有 regular 价 201.0¢** | 会出现在地图（大西洋孤点）、全省统计/基准；若做全省视图还会破坏 fitBounds |
| 平台 | PWA 已上线但**外壳 cache-first + 固定缓存名 `qc-gas-v1`**：只要不手动改 `sw.js`，已安装用户会长期停留在旧外壳（数据仍新鲜，但 UI/逻辑不更新） | 未来所有 UI 改动对已安装用户的触达不可靠 → 需要 network-first 导航 + 更新提示/版本策略 |
| 数据 | 站点级历史 180 天保留、无月 rollup，UI 仍有 1A 区间（现被覆盖门控禁用）；文件增长 **≈+42 KB/天**（4 天 263 KB → 180 天 ≈ 7.6 MB） | 1A 对站点不可达；需盯 `check_data_size.py` 的 40 MB 预算 |
| 数据 | 上游无站点 ID / city / 单站更新时间；我们小时级抓取且无陈旧告警 | 键脆弱 + 上游失效会静默变旧 |
| 功能 | 站点列表硬截断 **30 条**；无按距离排序；无分享/深链；无深色模式 | 见 §10，未占用本日额度 |
| 工程 / a11y | 无单元测试（只有 E2E）、无真实用户分析；地图 canvas 与列表项对键盘/读屏不友好 | 重构风险；a11y 硬伤 |

## 5. 用户声音：竞品 App 评论主题（2026-09-18 复核，无新增）

来源：App Store「Prix Essence Québec : Info Gaz」(id 6739227128, CA) 最近 50 条，公开 RSS
`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`。

- **均分仅 3.44★**（展示 4.7★ 是历史 5.6k 评分），**17 条 1–2★**，**11–13 条抱怨广告** → 验证"无广告"叙事。
- **9/50 质疑价格准确性**（"plus de 1 fois sur 2 le prix n'est pas le bon bien que « mis à jour il y a 5 minutes »"，3★）。
- 提醒类两条都吐槽质量：推错站（4★, 09-15）、点开不直达那家降价站（3★, 05-18）→ #31 已按"精准 + 直达"实现。
- 仍开放：**桌面小组件**（主屏存在感；PWA 已具备安装能力，可后续做 shortcuts/badging）、沿途规划、深色模式、
  以及"**想直接看到附近的站点清单**"（2026-08-05, 3★）——与 §4 首次访问问题同源。

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- GeoJSON 站点属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。
- 想再"进货"只能靠 Régie 公开出版物：**每日 composantes（PDF，按区域/MRC）**、**每周三 Bulletin PDF**（含各区 marge de détail）。

## 7. 关键实测数据（供后续提案引用）

- **默认视图覆盖**（2026-09-21 快照，2 459 站）：默认中心 25 km 内 **459 站 = 18.8%**；5 km 16 站；10 km 82 站；50 km 809 站；中心间坐标 `[-73.7, 45.45]`，地图 zoom 10。
- **价格水平**：regular min 164.5 / 中位 193.9 / max 240.0 / 均值 192.79；14 站无 regular 价。
- **价格波动**（区域 6h 桶，09-10→09-18）：|Δ| 中位 0.10¢、均值 0.65¢；23% 步长 ≥1¢、37% 不变；单日区内振幅均值 1.22¢（→ 阈值提醒 > 逐次 delta，见 #31）。
- **区域内基准**：Montréal min 186.9 / 中位 196.9（差 10.0¢＝5.00$/50L）；Montérégie 175.1 / 191.9（16.8¢＝8.40$）；Nord-du-Québec 195.4 / 196.4（1.0¢，n=12）。
- **站点历史增长**：4 天 263 KB（≈+42 KB/天；180 天 ≈ 7.6 MB；`tiers={daily_days:180, monthly:'none'}`）。

## 8. 竞争格局（2026-09-21）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟）→ 竞争点＝**怎么用数据**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错；**无筛选**；**一开始就显示全部站点位置**（TVA Nouvelles, 2026-04-01） | 筛选/标签仍是最硬差异；但首屏"全省可见"这一点官方做得比我们好 |
| **CAA-Québec Info Essence** | 会员 Web 工具 | 「**Faire le plein ou pas?**」（prix réaliste vs 区域均价） | 我们有站级基准（#34） |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」、按城市阈值提醒、**「près de moi」专页：无 GPS 时用"近似定位"并提示开启 GPS**、城市/区域 SEO 页、API | 首屏定位策略明显更成熟（见 §9） |
| **achetezlemeilleur.ca** | 内容站工具 | 区域表 Min/Max/Écart/vs Hier/vs Moy.、排除 Costco | 排行我们已有 |
| **metsdugaz.com** | 纯 Web | per-city SEO 页（2026 新入场） | 无 per-city 落地页 |
| **Prix Essence Québec（App）** | 可安装原生 App | 收藏+提醒、短期预测、行程计算；近 50 条评论 3.44★ | 我们已可安装（#37），差距缩小到"预测/小组件" |
| **Google Maps / Waze** | 通用地图/导航 | 会显示油价（2026 指南 + 用户帖；偶有区域失效） | 通用巨头不稳定 = 机会窗口，但需承认是真竞品 |

## 9. 本轮提案（已建 issue）

**「首次访问与回访定位」：默认全省视图 + 记住我的位置（并做无效坐标防护）** → issue **#39**（`pm-proposal`）。
理由：这是我们**唯一还在"第一眼就劝退用户"**的问题——默认半径只含全省 18.8% 的站点，而官方平台首屏给全省、
gasquebec 用"近似定位"兜底、评论还在抱怨"看不到附近的站"。修法很便宜（不改数据契约、无新依赖）：
首次访问不套半径 + 全省 fitBounds + 记住参考点/半径 + 排除 (0,0) 的 "Hub Régie" 假站点。

## 10. 已评估但不建议（本轮，避免重复提议）

- **PWA 外壳更新策略 hardening**（`sw.js` 固定缓存名 + 外壳 cache-first → 已安装用户可能长期停留旧外壳）：
  真实且有长期影响，但属"可靠性手术"、当下无用户可见收益 → 列为下一步优先（见 §11），不占今日额度。
- **列表 30 条截断 / 按距离排序 / 分享深链 / 深色模式**：真实但小或属外观层。
- **Régie composantes / Bulletin → prix juste**：只有 PDF，需解析 + 每日落库 + 前端，一次 PR 装不下且脆弱（§11 头号调研项）。
- **站点级月度 rollup（补齐 1A）**：先看体积与价值（§4），留给 §11。
- **沿途/路线加油**：需 Mapbox Directions（token scope + 计费）。
- **per-city SEO 落地页**：纯静态站需 prerender/多页构建，效果数月才能验证 → 暂缓。
- **XLSX（excel_url）接入**：字段与 GeoJSON 相同，无收益。

## 11. 待调研问题（下次优先）

- **PWA 更新策略**：导航请求改 network-first（或 stale-while-revalidate）+ "有新版本，点击刷新"提示 + 版本号策略（构建时注入？）。这是保证后续所有改动触达已安装用户的前提。
- 站点历史 180 天后的真实体积（≈7.6 MB?）与 40 MB 预算余量；是否要月粒度 rollup 或缩短保留期。
- Régie「Publications quotidiennes / Bulletin」是否有可稳定解析的机器可读来源（PDF 文本层 vs HTML 表格）？引用与授权？
- 上游是否可能新增站点 ID / city / 单站更新时间字段（键稳定性 + 时效叙事）？
- "数据覆盖天数 / 最后同步时间"是否应做成全局信任信号（不只卡片与离线态）？
- 非 Montréal 用户的后续体验：首次访问后是否要给"选择我的区域"入口（而非只依赖定位权限）？

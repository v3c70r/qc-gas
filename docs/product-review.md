# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-25（第 7 次运行；距上次 4 天）。**上次提案 #39 已落地**（PR #40）。
owner 亲自提了两个问题：#41（油价不准，已定位并修复 §5）、**#43（改进拉取时机，PR #44 仍在评审）**。
当前：我的提案队列为空；有 1 个 owner issue + 1 个 open PR（#44，抓取策略，尚未合并）。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（可安装 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方公开 GeoJSON（`https://regieessencequebec.ca/stations.geojson.gz`），
  由 GitHub Actions 抓取（`download_data.py` → `process_data.py` → `append_history.py`）并提交回仓库；
  **抓取时机策略正在改造中（#43 / PR #44：按"站点通常何时变价"自适应调度）**
- 规模（2026-09-25 实测）：**2 460 个站点**（其中 1 条无效坐标被前端排除 → 2 459 可用）、18 个行政区域、三油品；
  regular **170.9 / 中位 193.9 / 239.3**；快照 `generated_at` 13:15Z
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-09-25）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 / 价格标签 | Mapbox light-v11；聚类（z<10）+ 价格渐变点 + 价格标签（z≥11）`js/map.js` | 既有 |
| **全省优先视图 + 位置记忆** | 首次访问不套半径、`fitBounds` 全省（`QUEBEC_BOUNDS=[[-79.5,44.9],[-57.1,62.4]]`）；选择半径/定位/点地图后进入半径模式；`qc-gas-view` 记忆参考点与半径；排除无效坐标（"Hub Régie" (0,0)） | ✅ #39 |
| 最低价高亮 / 筛选即所得 | 品牌、油品、价格双滑块、区域、半径 5/10/25/50km `js/filters.js`+`stats.js` | 既有 |
| 站点搜索 | 地址/城市/邮编/品牌/站名，纯离线，可跨全省 `js/search.js` | ✅ #13 |
| 收藏 / 关注提醒 | 收藏（置顶、仅看收藏）+ 关注阈值触发与"自上次访问以来的真实变动"（侧栏空时隐藏）`js/favorites.js`、`js/watch.js` | ✅ #14 / #31 |
| 站点当日基准 / 覆盖门控 | 区域中位、与中位差、区域内百分位、50 L 金额差；区间 pills 超覆盖即禁用 `js/benchmark.js`、`js/history.js` | ✅ #34 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/对比真实区域历史均价的节省额 `js/fillups.js` | ✅ #29 |
| 可安装 PWA | manifest + 图标（零依赖生成脚本）+ `sw.js` + 安装入口 + iOS 引导 + 离线徽标"离线 · 最后同步 <时间>"（`data/*.json` network-first + `X-QCGas-From-Cache`）`js/pwa.js`、`docs/pwa.md` | ✅ #37（**更新策略有缺陷，见 §4**） |
| 品牌价格对标 / 区域排行 / 趋势看板 | `js/stats.js`、`js/dashboard.js` | ✅ #15 / #17 |
| 行程油费估算 / 导航 / 数据透明度 | 站点详情 L/100km × 距离；Google/Apple Maps 跳转；卡片显示 `generated_at` | ✅ #16 |
| 价格历史 | 区域级真实 6h 桶（raw 14 天 / daily 12 月 / monthly 永久）+ 站点级真实日粒度（180 天，自 2026-09-18） | ✅ #32 |
| 体验 / 测试 | 移动底部抽屉、44px、键盘 f/l/t/Esc、三语、`prefers-reduced-motion`；Playwright E2E 80+ 项；pipeline 侧已有 `scripts/test_*.py` | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——官方平台恰恰缺筛选（只能逐点 click）。
2. **打开即用、无广告、可安装**：竞品 App 近 50 条评论里 11–13 条在骂广告；我们既能免安装即用，也能装上主屏。
3. **数据诚实性成为资产**：删掉全部合成/外推（#32）、区间覆盖门控（#34）、离线明确标注"最后同步"（#37）、
   无效坐标不冒充真实站点（#39）。
4. **没有后端也能做决策工具**：搜索/收藏/关注提醒/排行/油费/加油日志/站级基准，全部 localStorage + 静态数据。
5. **全省优先 + 位置记忆**（#39）：非 Montréal 用户第一眼即全省，回访直接回到自己的城市。
6. **三语**（fr/en/zh，竞品中未见同等支持）+ 隐私友好 + 零运维成本。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 平台（最高优先） | **PWA 更新策略有缺陷**：`public/sw.js` 用**固定缓存名 `qc-gas-v1`**，且**所有同源非 `/data/` 请求（含导航与 HTML）一律 `cacheFirst()`**；`APP_SHELL` 里预缓存了 `'./'` 与 `'./index.html'`；`js/pwa.js` 的 `registerServiceWorker()` **只注册，没有任何 `updatefound` / `controllerchange` / `registration.update()` 处理** | ① 已安装用户**可能长期停留在旧外壳**（`sw.js` 字节不变 → 浏览器永不安装新 SW），#39 之后的改动他们看不到；② Vite 产物是**哈希文件名**（线上实测 `./assets/index-DukmIqY3.js`、懒加载块 `./station-card-BN9XwdBN.js`），旧缓存 HTML 会去请求**已删除的旧哈希块 → 404**，站点卡片/详情/图表等懒加载功能直接报错 |
| 数据 | 站点级历史 180 天保留、无月 rollup，UI 仍有 1A 区间（受覆盖门控禁用）；文件增长 **≈+40–42 KB/天**（8 天 428 KB → 180 天 ≈ 7.5 MB） | 1A 对站点不可达；需盯 `check_data_size.py` 的 40 MB 预算 |
| 数据 | 上游无站点 ID / city / 单站更新时间；我们**小时级**抓取（#43 正在改进时机），无陈旧告警 | 键脆弱；"这个价几分钟前报的"仍无法回答 |
| 数据 | 官方数据仍有 1 条无效坐标记录（"Hub Régie" (0,0)，regular 201.0¢）：**前端已排除**，但源数据未清理 | 若其他地方（脚本/统计）直接读原始数据会再次踩坑 |
| 功能 | 站点列表硬截断 **30 条**；无按距离排序；无分享/深链；无深色模式 | 见 §10，未占用本日额度 |
| 工程 / a11y | 无前端单元测试（只有 E2E）、无真实用户分析；地图 canvas 与列表项对键盘/读屏不友好 | 重构风险；a11y 硬伤 |

## 5. 用户声音（本轮新增 owner 一手反馈）

- **owner 实地核对（#41, 2026-09-22）**：网站显示 Crevier（4220 Boul. Saint-Jean, DDO）regular **192.9**，
  现场实际 **186.9** → 调查结论：**不是数据源问题，而是部署流程拿了旧的 checkout**（价格数据提交后构建/部署未用最新分支尖端）。
  已由 PR #42 修复（`static.yml` 部署前 checkout 最新数据提交）。**教训：一切"数据看起来旧"的投诉，先查交付链路再看数据源。**
- **owner 诉求（#43, 2026-09-25）**：希望按"站点通常何时变价"调整抓取时机（多轮微调 + diff 统计），
  在**不压垮上游**的前提下尽量贴近变价时刻 → 现有 PR #44（`fetch_strategy.py` / `should_fetch.py` / `update_profile.py`，
  计划产出 `docs/fetch-strategy.md`）正在实现。这是 owner 当前最关心的话题：**时效性**。
- 竞品 App 评论（最近 50 条，2026-09-18 复核）：均分 **3.44★**、17 条 1–2★、11–13 条骂广告、
  **9 条质疑价格准确性**、"点开通知要直达那家降价站"、要"桌面小组件"、要"能直接看到附近的站点清单"。
  RSS：`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- GeoJSON 站点属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。
- 想再"进货"只能靠 Régie 公开出版物：**每日 composantes（PDF，按区域/MRC）**、**每周三 Bulletin PDF**（含各区 marge de détail）。

## 7. 关键实测数据（供后续提案引用）

- **线上产物实测（2026-09-25，curl）**：`https://qgu.io/qc-gas/` 引用 `./assets/index-DukmIqY3.js`、`./assets/index-BDu6cd_j.css`；
  该 bundle 内部引用懒加载块 `./station-card-BN9XwdBN.js`；`/qc-gas/sw.js` 的 `CACHE_NAME = 'qc-gas-v1'`（与仓库一致）。
- **价格水平**：regular 170.9 / 中位 193.9 / max 239.3（2 459 可用站点，14 站无 regular 价）。
- **价格波动**（区域 6h 桶）：|Δ| 中位 0.10¢、均值 0.65¢；23% 步长 ≥1¢、37% 不变；单日区内振幅均值 1.22¢。
- **区域内基准**：Montréal 中位 196.9 vs 最低 186.9（差 10.0¢＝5.00$/50L）；Montérégie 191.9 / 175.1（16.8¢＝8.40$）。
- **历史数据体量**：区域历史 59 点/区；站点历史 8 天 428 KB（≈+40 KB/天）。

## 8. 竞争格局（2026-09-25）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟）→ 竞争点＝**怎么用数据 + 交付是否新鲜**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错；**无筛选**；首屏即显示全部站点 | 筛选/标签仍是最硬差异 |
| **CAA-Québec Info Essence** | 会员 Web 工具 | 「Faire le plein ou pas?」（prix réaliste vs 区域均价，周一至周五） | 我们有站级基准（#34） |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」、按城市阈值提醒、**「près de moi」无 GPS 时用"近似定位"兜底**、城市/区域 SEO 页、API | 首屏与 SEO 页仍领先；时效性口径待 #43 落地后评估 |
| **achetezlemeilleur.ca** | 内容站工具 | 区域表 Min/Max/Écart/vs Hier/vs Moy.、排除 Costco | 排行我们已有 |
| **metsdugaz.com** | 纯 Web | per-city SEO 页 | 无 per-city 落地页 |
| **Prix Essence Québec（App）** | 可安装原生 App | 收藏+提醒、短期预测、行程计算；近 50 条评论 3.44★ | 我们已可安装（#37）；差"预测/小组件" |
| **Google Maps / Waze** | 通用地图/导航 | 会显示油价（偶有区域失效） | 通用巨头不稳定 = 机会窗口 |

## 9. 本轮提案（已建 issue）

**PWA 更新策略：确保已安装用户拿到最新版本（导航 network-first + 版本化缓存 + 更新提示）** → issue **#45**（`pm-proposal`）。
理由：这是唯一一个**会让用户看到"旧产品/坏功能"**的结构性缺陷（旧外壳 + 旧哈希懒加载块 404），
与 owner 最关心的"用户看到的必须是最新"（#41/#43）同一条主线，而且不碰 #44 正在改的抓取脚本。

## 10. 已评估但不建议（本轮，避免重复提议）

- **抓取时机/时效性改进**：owner 已在 #43 推进（PR #44 open），**不在 PM 提案范围内**，避免重复。
- **用户可见的"变价时段"洞察**（如"本周四早晨最可能涨价"）：等 #44 产出统计数据后再评估（见 §11）。
- **站点级月度 rollup（补齐 1A）/ 列表 30 条截断 / 按距离排序 / 分享深链 / 深色模式**：真实但小或属外观层。
- **Régie composantes / Bulletin → prix juste**：只有 PDF，需解析 + 每日落库 + 前端，一次 PR 装不下且脆弱（§11 头号调研项）。
- **沿途/路线加油**：需 Mapbox Directions（token scope + 计费）。
- **per-city SEO 落地页**：纯静态站需 prerender/多页构建，效果数月才能验证 → 暂缓。
- **清源数据里的无效坐标**（改 `process_data.py`）：前端已排除，收益小；若要做，随抓取脚本改动一起做。

## 11. 待调研问题（下次优先）

- PR #44 落地后：抓取频率/时机实际变化？是否值得把"变价时段"做成用户可见洞察（需可验证数据支撑）？
- 站点历史 180 天后的真实体积（≈7.5 MB?）与 40 MB 预算余量；是否要月粒度 rollup 或缩短保留。
- Régie「Publications quotidiennes / Bulletin」是否有可稳定解析的机器可读来源（PDF 文本层 vs HTML 表格）？引用与授权？
- 上游是否可能新增站点 ID / city / 单站更新时间字段（键稳定性 + 时效叙事）？
- "数据覆盖天数 / 最后同步时间"是否应做成全局信任信号（不只卡片与离线态）？
- PWA 之后：iOS 已安装用户是否值得做 Web Push（无 push 服务，须依赖用户手动触发/Periodic Sync）？

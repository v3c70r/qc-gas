# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-26（第 8 次运行）。**提案队列为空**。上次运行后落地两条：
#43/#44（自适应抓取：`fetch_strategy.py`/`should_fetch.py`/`update_profile.py` + `data/fetch-schedule.json`、`update-profile.json`、`fetch-state.json`，cron 改 `*/15`）、
#45/#46（PWA 更新策略：导航 network-first + 版本化缓存 + `SKIP_WAITING` 更新提示）。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（可安装 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方 GeoJSON（`regieessencequebec.ca/stations.geojson.gz`），
  GitHub Actions 抓取并提交回仓库；抓取时机由**学到的变价时段 + 每日预算（36 次/天）+ 3h 兜底**门控
- 规模（2026-09-26 实测）：**2 460 个站点**（1 条无效坐标被前端排除）、18 区、三油品；
  regular **170.9 / 中位 192.9 / 239.3**；快照 `generated_at` 16:30Z
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-09-26）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 / 价格标签 / 最低价高亮 | 聚类（z<10）+ 渐变点 + 价格标签（z≥11）+ 最低价脉冲 `js/map.js`、`js/stats.js` | 既有 |
| 全省优先视图 + 位置记忆 | 首次访问不套半径、全省 fitBounds；半径/定位/点地图进入半径模式；`qc-gas-view` 记忆 | ✅ #39 |
| 筛选即所得 / 站点搜索 | 品牌、油品、价格、区域、半径；离线搜索可跨全省 `js/filters.js`、`js/search.js` | ✅ #13 |
| 收藏 / 关注提醒 | 置顶与仅看收藏；阈值触发 + "自上次访问以来的真实变动" `js/favorites.js`、`js/watch.js` | ✅ #14 / #31 |
| 站点当日基准 / 覆盖门控 | 区域中位、与中位差、区域内百分位、50 L 金额差；区间超覆盖即禁用 `js/benchmark.js` | ✅ #34 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/对比真实区域历史均价的节省额 `js/fillups.js` | ✅ #29 |
| 可安装 PWA + **更新策略** | manifest/图标/SW；**导航 network-first + 版本化缓存 + `SKIP_WAITING` + 更新提示条**；离线徽标"离线 · 最后同步" | ✅ #37 / #45 |
| 品牌对标 / 区域排行 / 趋势看板 | `js/stats.js`、`js/dashboard.js` | ✅ #15 / #17 |
| 行程油费 / 导航 / 数据透明度 | 站点详情 L/100km × 距离；Google/Apple Maps 跳转；卡片显示 `generated_at`（绝对时间） | ✅ #16 |
| 价格历史 | 区域级真实 6h 桶 + 站点级真实日粒度（180 天，自 2026-09-18；现 9 天 470 KB） | ✅ #32 |
| 自适应抓取（pipeline） | 学到的小时权重 → 每日 36 槽计划 + `should_fetch.py` 门控（min gap 10 min、3h 兜底、45 min grace）+ 体积预算 | ✅ #43 |
| 体验 / 测试 | 移动底部抽屉、44px、键盘、三语；Playwright E2E 80+；pipeline 有 `scripts/test_*.py` | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——官方平台恰恰缺筛选（只能逐点 click）。
2. **打开即用、无广告、可安装**：竞品 App 近 50 条评论里 11–13 条在骂广告；我们免安装可用也能装主屏。
3. **数据诚实性成为资产**：无合成/外推（#32）、覆盖门控（#34）、离线标注"最后同步"（#37）、无效坐标不冒充站点（#39）。
4. **没有后端也能做决策工具**：搜索/收藏/关注/排行/油费/加油日志/站级基准，全部 localStorage + 静态数据。
5. **全省优先 + 位置记忆**（#39）；**三语**（fr/en/zh，竞品中未见同等支持）；隐私友好、零运维。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 数据交付（最高优先） | **实际抓取频率远低于设计**：cron 已改 `*/15`（96 次/天）、计划 36 槽/天，但**实测 09-25 20:28Z 之后 20 小时只有 6 次 scheduled run（间隔 146–321 min，中位 255 min ≈ 4.25 h）**；门控每次判定都 due（今天数据提交 07:20 / 12:19 / 16:33Z）→ **瓶颈是触发投递，不是门控** | 应用里的价格最坏可到 **~4–5 小时前**；与"到达现场价格不对"（#41）直接相关 |
| 前端（最高优先） | **页面/已安装应用从不刷新数据**：`loadStations()` 只在启动时跑一次，全仓无数据轮询或 `visibilitychange` 刷新（`js/pwa.js` 的 visibilitychange 只用于 SW 更新检查） | 长驻标签页或一直开着的 PWA 会整段时间显示旧价格；即使新快照落地了也不会自己出现 |
| 前端 | **新鲜度不可见**：header 只显示站点数；卡片里是绝对时间戳（难读）；没有"il y a X min"这类相对时间 | 用户无法判断"这个价是几分钟前还是几小时前的"；竞品首页直接写 "Dernière mise à jour : À l'instant"（gasquebec） |
| 数据 | `update-profile.json` 目前只有 **6 个样本/6 个小时**（5 次观察到变化） | 还不足以做用户可见的"何时变价"洞察（阈值见 §11） |
| 数据 | 站点级历史 180 天保留、无月 rollup；9 天 470 KB（≈+45 KB/天 → 180 天 ≈ 8 MB） | 1A 对站点不可达；盯 `check_data_size.py` 40 MB 预算 |
| 数据 | 上游无站点 ID / city / 单站更新时间；官方数据仍含 1 条无效坐标（"Hub Régie" (0,0)，前端已排除） | 键脆弱；"这个价几点报的"仍答不了 |
| 功能 | 站点列表硬截断 **30 条**；无按距离排序；无分享/深链；无深色模式 | 见 §10，未占本日额度 |
| 工程 / a11y | 无前端单元测试（只有 E2E）；地图 canvas 与列表项对键盘/读屏不友好 | 重构风险；a11y 硬伤 |

## 5. 用户声音

- **owner 一手反馈（#41, 09-22）**：网站显示 Crevier（DDO）regular **192.9**，现场实际 **186.9** → 根因是**部署拿了旧 checkout**（PR #42 修复）。
  教训：**"数据看起来旧"先查交付链路**。
- **owner 诉求（#43, 09-25）**：按站点变价时段自适应抓取，"增加系统实时性"、但不压垮上游 → 已实现（#44）；**实测显示触发频率才是天花板（见 §4/§7）**。
- 竞品 App 评论（最近 50 条）：均分 **3.44★**、17 条 1–2★、11–13 条骂广告、**9 条质疑价格准确性**、
  "点开通知要直达那家降价站"、要"主屏小组件"、要"直接看到附近的站点清单"。
  RSS：`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- GeoJSON 站点属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。
- 想再"进货"只能靠 Régie 公开出版物：**每日 composantes（PDF，按区域/MRC）**、**每周三 Bulletin PDF**（含各区 marge de détail）。

## 7. 关键实测数据（供后续提案引用）

- **抓取投递（2026-09-26 实测）**：`static.yml` 的 scheduled run 自 cron 改 `*/15` 后 20 小时内只有 **6 次**（间隔 176/146/321/299/255 min，中位 **255 min**）；
  数据提交 = 07:20Z / 12:19Z / 16:33Z；`fetch-state.json`：`fetches: 6`；计划 `budget_per_day: 36`。→ **设计 36 次/天，实际 ~7 次/天**。
- **线上产物**：HTML 引用哈希资源（`./assets/index-*.js`），懒加载块亦哈希（`./station-card-*.js`）→ 需导航 network-first（#45 已修）。
- **价格水平**：regular 170.9 / 中位 192.9 / 239.3；14 站无 regular 价；区域历史 59 点/区；站点历史 9 天 470 KB。
- **价格波动**（区域 6h 桶）：|Δ| 中位 0.10¢、均值 0.65¢；23% 步长 ≥1¢、37% 不变；单日区内振幅均值 1.22¢。
- **区域内基准**：Montréal 中位 196.9 vs 最低 186.9（差 10.0¢＝5.00$/50L）；Montérégie 191.9 / 175.1（16.8¢＝8.40$）。
- **变价画像样本量**：`update-profile.json` 6 样本 / 6 小时（5 次变化）→ 不足。

## 8. 竞争格局（2026-09-26）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟）→ 竞争点＝**怎么用数据 + 交付是否新鲜**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错；**无筛选**；首屏即全部站点 | 筛选/标签仍是我们的硬差异 |
| **gasquebec.ca** | 纯 Web + Premium | 首页主打 **"temps réel / en continu"** 与 **"Dernière mise à jour : À l'instant"**（2026-09-26 抓取）、「prix juste」、按城市阈值提醒、「près de moi」近似定位兜底、SEO 页、API | **新鲜度表达**明显更直观：它把"刚更新"写在脸上，我们连数据年龄都不显示 |
| **CAA-Québec Info Essence** | 会员 Web 工具 | 「Faire le plein ou pas?」（prix réaliste vs 区域均价） | 我们有站级基准（#34） |
| **achetezlemeilleur.ca** | 内容站工具 | 区域表 Min/Max/Écart/vs Hier/vs Moy.、排除 Costco | 排行我们已有 |
| **metsdugaz.com** | 纯 Web | per-city SEO 页 | 无 per-city 落地页 |
| **Prix Essence Québec（App）** | 可安装原生 App | 收藏+提醒、短期预测、行程计算；近 50 条评论 3.44★ | 我们已可安装；差"预测/小组件" |
| **Google Maps / Waze** | 通用地图/导航 | 会显示油价（偶有区域失效） | 通用巨头不稳定 = 机会窗口 |

## 9. 本轮提案（已建 issue）

**新鲜度可见 + 自动刷新：打开着的页面/已安装应用必须拿到最新快照** → issue **#47**（`pm-proposal`）。
理由：我们把抓取做成了自适应（#43），但**前端从不刷新、也不显示数据年龄**——用户可能整段时间看 4 小时前的价格却毫无察觉。
本提案是 owner 那条"实时性"主线的客户端另一半：相对时间 + 焦点/定时自动拉取最新快照（原地更新、保留筛选与视图）。

## 10. 已评估但不建议（本轮，避免重复提议）

- **改触发频率（外部 cron / 自触发 / 循环抓取）**：触发投递瓶颈（§7）属基础设施议题，且用 `GITHUB_TOKEN` 自触发被 GitHub 禁止、外部 pinger 需引入第三方服务 → **留给 owner 决策**，不占 PM 额度（已在 §11 记录）。
- **用户可见的"变价时段"洞察**：样本仅 6 条，统计上不可靠（阈值见 §11）→ 等样本充足再评估。
- **站点级月度 rollup（补齐 1A）/ 列表 30 条截断 / 按距离排序 / 分享深链 / 深色模式**：真实但小或属外观层。
- **Régie composantes / Bulletin → prix juste**：只有 PDF，需解析 + 每日落库 + 前端，一次 PR 装不下且脆弱（§11 头号数据项）。
- **沿途/路线加油**：需 Mapbox Directions（token scope + 计费）。
- **per-city SEO 落地页**：纯静态站需 prerender/多页构建，效果数月才能验证 → 暂缓。

## 11. 待调研问题（下次优先）

- **抓取投递**：是否有不引入第三方依赖的办法把 scheduled run 提升到设计频率（例如合并到已有高频工作流、`workflow_dispatch` + 外部触发器、或接受现实并重新规划槽位）？若要外部服务，成本/隐私评估？
- **变价时段洞察的样本阈值**：`update-profile.json` 需要多少样本（建议 ≥ 每时段 ≥ 14 天）才足以对用户宣称"何时加油更便宜"？
- 站点历史 180 天后体积（≈8 MB?）与 40 MB 预算余量；是否月粒度 rollup 或缩短保留。
- Régie「Publications quotidiennes / Bulletin」是否有可稳定解析的机器可读来源（PDF 文本层 vs HTML 表格）？引用与授权？
- 上游是否可能新增站点 ID / city / 单站更新时间字段（键稳定性 + 时效叙事）？
- 站点列表 30 条截断 / 按距离排序 / 分享深链：优先级是否上升（与"就近决策"相关）？

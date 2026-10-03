# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-10-03（第 12 次运行；距上次 4 天）。**我提交的 #49/#50/#51 全部获批并落地**
（#54 周报毛利 / #53 列表看全+距离排序 / #52 卡片与快照一致），owner 自己提的 #55（iOS Safari 半径筛选被遮挡）也已修复（#56）。
**提案队列已清空，无 open PR。**_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（可安装 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方 GeoJSON（`regieessencequebec.ca/stations.geojson.gz`）+
  **官方每周 Bulletin PDF**（零售毛利/区域均价，见 #54）；GitHub Actions 抓取并提交回仓库
- 规模（2026-10-03 实测）：**2 461 个站点**（1 条无效坐标被前端排除）、18 区、三油品；
  regular **172.9 / 中位 192.9 / 242.9**；快照 `generated_at` 15:05Z
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-10-03）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 / 价格标签 / 最低价高亮 | 聚类（z<10）+ 渐变点 + 价格标签（z≥11）+ 最低价脉冲 `js/map.js`、`js/stats.js` | 既有 |
| 全省优先视图 + 位置记忆 | 首次访问不套半径、全省 fitBounds；半径/定位/点地图进入半径模式；`qc-gas-view` | ✅ #39 |
| 筛选即所得 / 离线搜索 | 品牌、油品、价格、区域、半径；搜索可跨全省 `js/filters.js`、`js/search.js` | ✅ #13 |
| 收藏 / 关注提醒 | 置顶与仅看收藏；阈值触发 + "自上次访问以来的真实变动" `js/favorites.js`、`js/watch.js` | ✅ #14 / #31 |
| 站点当日基准 / 覆盖门控 | 区域中位、与中位差、区域内百分位、50 L 金额差；区间超覆盖即禁用 `js/benchmark.js` | ✅ #34 |
| **官方周报毛利 + 官方区域均价** | `scripts/fetch_bulletin.py` → `data/regie-margin.json`（17 区 × 3 油品：上周/本周均价、环比、**Marge de détail hors taxes**、Montréal 装车价序列）→ 趋势看板「Marge (Régie)」列 + 站点卡片区域毛利行 `js/regie.js`、`docs/regie-bulletin.md` | ✅ #49 / #54 |
| **侧栏列表看全 + 距离排序** | 30/页分批渲染（"Voir plus"）+ `prix ↔ distance` 排序（持久化 `qc-gas-list`）+ 计数文案；无参考点时禁用距离排序并提示 | ✅ #50 / #53 |
| **卡片/面板与快照一致** | 订阅 `stations:loaded`，用 `stationId()` 重定位并重渲染；站点消失则关闭并提示；加油表单预填价"未编辑才更新" | ✅ #51 / #52 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/对比真实区域历史均价的节省额 `js/fillups.js` | ✅ #29 |
| 可安装 PWA + 更新策略 + 新鲜度 | 导航 network-first + 版本化缓存 + 更新提示；`js/freshness.js`：相对时间、可见时每 10 min/回前台自动原地刷新 | ✅ #37 / #45 / #47 |
| 品牌对标 / 区域排行 / 趋势看板 | `js/stats.js`、`js/dashboard.js` | ✅ #15 / #17 |
| 行程油费 / 导航 / 数据透明度 | 站点详情 L/100km × 距离；Google/Apple Maps 跳转；卡片显示 `generated_at` | ✅ #16 |
| 价格历史 | 区域级真实 6h 桶 + 站点级真实日粒度（180 天；现 16 天 760 KB） | ✅ #32 |
| 自适应抓取（pipeline） | 学到的小时权重 → 36 槽/天计划 + 门控（min gap 10 min、3h 兜底）+ 体积预算 | ✅ #43 |
| 体验 / 测试 | 移动底部抽屉、44px、键盘、三语；Playwright E2E 100+；pipeline 有 `scripts/test_*.py` | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——Protégez-Vous 明确点出官方平台"无法筛选、只能逐个 hover"（我们的最硬差异）。
2. **官方"贵不贵"口径补齐**：Régie 自己的每周零售毛利用于解释区域价差（#49），与 gasquebec「prix juste」/CAA「prix réaliste」对位。
3. **打开即用、无广告、可安装**：竞品 App 近 50 条评论里 11–13 条在骂广告；我们免安装可用也能装主屏。
4. **数据诚实性成为资产**：无合成/外推、覆盖门控、离线标注"最后同步"、无效坐标不冒充站点、新鲜度可见、卡片与快照一致（#52）。
5. **没有后端也能做决策工具**：搜索/收藏/关注/排行/油费/加油日志/站级基准/距离排序，全部 localStorage + 静态数据；
   **三语**（fr/en/zh，竞品未见同等支持）；隐私友好、零运维。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 数据（本日提案） | **个人数据只存在 localStorage，且全仓没有任何导出/导入**（已 grep：favorites / fillups / watch / trip / list / view 六个键，零备份路径）：收藏、加油日志（含"对比区域均价的节省额"历史）、关注阈值只活在这一个浏览器里 | **Safari（iOS/macOS）会在"7 天无交互"后删除脚本可写存储（含 localStorage）**（MDN 与 WebKit ITP 2.3 均有明文，见 §7）；清缓存/换机/换浏览器同样永久丢失 → 用户"省钱账本"随时可能归零，也让 #29/#31 的价值随数据一起消失 |
| 数据 | **抓取投递是新鲜度天花板**：cron `*/15`、计划 36 槽/天，近 4 天实测 **23 次数据提交 / 4.1 天 ≈ 5.6 次/天**（中位间隔 **246 min ≈ 4.1 h**） | 应用里价格最坏可到 4–6 小时前；新竞品 *infoEssence* 已宣称"每 10 分钟更新"（§8），差距在扩大（基础设施议题，§11，交 owner 决策） |
| 数据 | `update-profile.json` 仅 **45 个样本 / 19 个小时**（增长 ≈11/天） | 用户可见的"何时变价"洞察仍不可做（阈值见 §11） |
| 数据 | 站点级历史 180 天保留、无月 rollup；16 天 760 KB（≈+45 KB/天 → 180 天 ≈ 8 MB） | 1A 对站点不可达；盯 `check_data_size.py` 40 MB 预算 |
| 数据 | 上游无站点 ID（个人数据的键 = `name\|address\|postal_code`；关注/历史用 5 位小数坐标）+ 无单站更新时间 | 官方改地址/坐标 → 收藏、加油日志、关注会**静默失联**（无迁移策略）；"这个价几点报的"仍答不了 |
| 功能 | 无分享/深链（URL 无法表达任何内容）；无深色模式；列表项是 div（键盘无法从列表打开站点） | 见 §10，未占本日额度 |
| 工程 | 无前端单元测试（只有 E2E） | 重构风险 |

## 5. 用户声音

- **owner 一手反馈**：#41（网站 192.9 vs 现场 186.9 → 部署拿旧 checkout，PR #42 修复）；#43（"到达现场后油价不准"→ 自适应抓取）；**#55（iPhone Safari 半径筛选被底部抽屉遮挡 → #56 修复）** ⇒ owner 本人用手机高频使用，移动端可用性值得持续盯。
- **竞品 App 评论（最近 50 条，2026-10-03 复核：均分 3.42★、17 条 1–2★）**：11–13 条骂广告、9 条质疑价格准确性、
  提醒推错站、要点开通知直达降价站、要能滚动查看附近站点（**我们已由 #50 满足**）、要主屏小组件、
  1★（09-17）"Route — Mauvaise indication de route"；最新 5★（09-28）"Merci bcp de me faire économiser"。
  RSS：`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`（只返回最新 50 条）

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- 站点 GeoJSON 属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。
- **区名匹配已核对（2026-10-03）**：`js/regie.js` 用 `js/search.js` 的 `normalizeText()`（去重音 + 非字母数字→空格），
  **能**把 `Gaspésie–Iles-de-la-Madeleine` / `Saguenay–Lac-Saint-Jean` 匹配到我们的写法；
  唯一无对应的是 `Municipalités hors MRC \ CMM`（**仅 1 个站点**）→ 该行不显示是预期行为，不用再查。

## 7. 关键实测数据（供后续提案引用）

- **⭐ Safari 存储清理（本日提案证据）**：MDN《Storage quotas and eviction criteria》：
  "Safari proactively evicts data … If an origin has no user interaction … in the last seven days of browser use,
  its data created from script will be deleted."（https://developer.mozilla.org/en-US/docs/Web/API/Storage_API/Storage_quotas_and_eviction_criteria）
  WebKit ITP 2.3 公告《Full Third-Party Cookie Blocking and More》标题即为 "7-Day Cap on All Script-Writeable Storage"
  （https://webkit.org/blog/10218/full-third-party-cookie-blocking-and-more/ ；同文被 localForage issue #943 逐句引用）。
- **官方周报数据（已上线）**：`data/regie-margin.json`（2026-10-03 生成，周 2026-09-21→28，published 2026-10-02）：
  **17 个区中 10 个 regular 毛利为负**（Saguenay −6.9、Outaouais −6.4、Estrie −0.5、Capitale-Nationale −0.1…）→ UI 呈现必须保留正负号与 tooltip。
- **抓取投递**：近 4 天 23 次提交（中位间隔 246 min、最短 68、最长 432）；`update-profile` 样本 45（19 小时）。
- **附近站点密度**：≤10 km / ≤25 km —— Montréal 中心 161 / 484、Laval 114 / 469、Québec 市 118 / 215（#50 已解除截断）。
- **价格水平**：regular 172.9 / 中位 192.9 / 242.9；区域历史 67 点/区；站点历史 16 天 760 KB。
- **价格波动**（区域 6h 桶）：|Δ| 中位 0.10¢、均值 0.65¢；23% 步长 ≥1¢。

## 8. 竞争格局（2026-10-03）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟；GitHub 上还有 8 个 0★ 同源克隆）→ 竞争点＝**怎么用数据 + 交付是否新鲜**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5min、逐站纠错；**无筛选**；首屏即全部站点；发布周报毛利 | Protégez-Vous 点出"无法筛选、只能逐个 hover" ← 我们的最硬差异；毛利口径已补（#49） |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」、按城市阈值提醒、「près de moi」近似定位兜底、SEO 页、API | 新鲜度表达已追平；就近列表（#50）与毛利（#49）已补 |
| **🆕 infoEssence – Prix Essence QC**（App Store, id6761513458） | 原生 App | **"prix mis à jour toutes les 10 minutes"**、收藏、按油品/品牌筛选 | **10 分钟更新**（我们 5.6 次/天）；功能面我们在逐站标签/历史/基准/日志上仍更强 |
| **CAA-Québec Info Essence** | 会员 Web 工具 | "si c'est le bon moment de faire un détour"（prix réaliste = Régie + Bloomberg OBG + 去年平均毛利） | 我们无"是否值得绕路"的判断（候选见 §10） |
| **Prix Essence Québec（App）** | 可安装原生 App | 收藏+提醒、"愿意开的距离"、预测涨价概率；近 50 条 3.42★ | 我们不预测（数据不足 + 诚实性优先） |
| **Radio-Canada 看板 / EssenceQuébec.com** | 媒体/老站 | 区域均价、最高/最低站、**每小时更新**、红绿地图 | 他们每小时更新；我们逐站标签/筛选/历史更强 |
| **Google Maps / Waze** | 通用地图/导航 | 会显示油价（偶有区域失效） | 通用巨头不稳定 = 机会窗口 |
| 第三方推荐文（François Charron 等） | 内容/评测 | 会把"该装哪几个工具"写进推荐清单 | 我们未被收录 → 无 SEO/内容存在感（§10 暂缓） |

## 9. 本轮提案（已建 issue）

**「Mes données」导出/导入备份：收藏 + 加油日志 + 关注（防 Safari 7 天清理与换机丢失）** → issue **#57**（`pm-proposal`）。
理由：全部个人数据只有 localStorage 一个副本、全仓零备份路径，而 **Safari 明确会在 7 天无交互后删除脚本可写存储**；
一旦发生，用户省钱的账本（#29）与"自上次访问以来的变动"（#31）一起归零。修法便宜（纯前端 JSON 导出/导入 + 合并/替换 + 三语 + 测试）。

## 10. 已评估但不建议（本轮，避免重复提议）

- **"绕路是否划算"（détour 盈亏平衡）**：证据不错（CAA 的卖点、Prix Essence Québec 的"愿意开的距离"），
  且 #50 已落地（距离排序可作基础）；但需要"直线距离 × 路网系数 + 油耗 + 加注量"等假设，**文案风险高** →
  留作下一候选，前提是先定义"估计而非承诺"的文案与系数出处（§11）。
- **深色模式**（仅 1 条评论明确要求，外观层）、**分享/深链**（无直接用户证据）、**列表键盘可达/a11y**（受众有限）。
- **站点级 1A rollup / 月粒度降采样**：先看体积与价值（§4）。
- **上游站点 ID 缺失的迁移策略**（收藏/日志/关注静默失联）：真实但需要设计（坐标兜底 + 迁移提示），比备份功能更复杂 → 排在导出/导入之后。
- **接入每日 `composantes`（每 MRC 一个 PDF）或 `rqe.pdf`**：成本/增量收益不划算 → 不接入。
- **改抓取触发频率**：基础设施议题，交 owner 决策（§11）。
- **变价时段洞察**：样本 45 条，统计不可靠（阈值见 §11）；**沿途/路线加油**：需 Mapbox Directions（计费）；
  **per-city SEO 落地页**：需 prerender/多页构建 → 暂缓（但这解释了为什么我们不进第三方推荐清单）。

## 11. 待调研问题（下次优先）

- "绕路盈亏平衡"若要立项：直线距离→路网距离的系数取多少、出处是什么？如何用一句话讲清假设而不误导？
- 上游键脆弱性：是否能以坐标为主键做一次性迁移（收藏/日志/关注的静默失联兜底）？
- 抓取投递：是否有不引入第三方依赖的办法提升 scheduled run 频率（合并到已有高频工作流 / 外部触发器 / 重排槽位）？
  新竞品已宣称 10 分钟级更新，这条的优先级在上升。
- 变价时段洞察阈值：每时段 ≥14 天样本后才可对用户宣称（当前 45 样本 / 19 小时）。
- 站点历史 180 天后体积（≈8 MB?）与 40 MB 预算余量；是否月粒度 rollup 或缩短保留。
- 列表分批渲染在"全省 2 461 条 + 收藏模式"下的表现（#53 后需实测一次滚动性能）。

# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-19（第 4 次运行）。已落地：#13–#17（搜索/收藏/品牌对标/行程油费/区域排行）、
#29（「Mes pleins」加油日志）、半径圈初值 bug 修复、**#32/#33（移除全部合成历史 + 开始采集站点级真实日粒度历史）**。
#31（关注站价格提醒）已建，状态 `agent-seen`，尚未实现。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方公开 GeoJSON（`https://regieessencequebec.ca/stations.geojson.gz`），
  每小时 GitHub Actions 抓取（`scripts/download_data.py` → `process_data.py` → `append_history.py`）并提交回仓库
- 规模（2026-09-17 实测）：**2 461 个站点**、18 个行政区域、三油品；全省均价 **195.1¢**，min 152.2 / max 240.0
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-09-19）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 | Mapbox light-v11；聚类（z<10）+ 价格渐变点 + **站点价格标签**（z≥11）`js/map.js` | 既有 |
| 最低价高亮 | 筛选结果中最低价站点脉冲动画 + 列表 best 标记 `js/stats.js` | 既有 |
| 筛选即所得 | 品牌、油品、价格双滑块、区域、半径 5/10/25/50km（初值 bug 已修，`rangeRadius={value:25}`）`js/filters.js` | 既有 |
| 站点搜索 | 地址/城市/邮编/品牌/站名，纯离线 `js/search.js` | ✅ #13 |
| 收藏 | localStorage、列表置顶、仅看收藏；**仍纯被动** | ✅ #14 |
| 关注提醒 | 阈值 + 自上次访问变动（应用内）`js/watch.js`（规划中） | 🚧 #31 |
| 品牌价格对标 | 品牌行显示站数 + 均价 + 相对全省均价差值 `js/stats.js` | ✅ #15 |
| 区域排行表 | 趋势看板内 18 区 min/avg/max/价差/vs 昨日 `js/dashboard.js` | ✅ #17 |
| 行程油费估算 | 站点详情 L/100km × 距离 `js/station-card.js` | ✅ #16 |
| 加油日志「Mes pleins」 | localStorage + 本月支出/实付均价/**对比真实区域历史均价的节省额** `js/fillups.js` | ✅ #29 |
| 站点卡片 | 大字报价、涨跌 chip、SVG sparkline、油品 pills、**1W/1M/3M/6M/1A 区间 pills**、Min/Max、Chart.js 详情面板 | 既有 |
| 价格历史 | **区域级真实 6h 桶**（raw 14 天 / daily 12 月 / monthly 永久）+ **站点级真实日粒度**（保留 180 天，自 2026-09-18 起采集，**无合成、无外推**）`js/history.js`、`scripts/append_history.py` | ✅ #32 |
| 趋势看板 | 区域 chips、油品、7/30/60/90 天、min/avg/7J/30J（区域真实数据） | 既有 |
| 数据透明度 | 卡片显示 `generated_at` | 既有 |
| 导航 / 体验 | Google/Apple Maps 跳转；移动底部抽屉、44px、键盘 f/l/t/Esc、三语、`prefers-reduced-motion` | 既有 |
| 测试 | 仅 Playwright E2E（`tests/app.spec.js`，60+ 项） | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——官方平台恰恰缺这个（Protégez-Vous、TVA 都点出官方只能逐点 click）。
2. **打开即用、无广告、无安装**：竞品 App 近 50 条评论里 11–13 条在骂广告，这是最硬的情感差异。
3. **数据诚实性成为新资产**：#32 主动删掉合成/外推曲线——竞品仍在（如 gasquebec 站内对 plancher 自相矛盾）而我们宁可留白。
4. **零后端也能做出"决策工具"**：搜索/收藏/历史/排行/油费/加油日志/关注提醒，全部 localStorage + 静态数据。
5. **三语**（fr/en/zh，竞品中未见同等支持）+ 隐私友好 + 零运维成本。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 功能 | **站点卡片信息真空（#32 的副作用）**：站点级真实历史只有 **2 天**（`data/history/station-history.json` 的 `t`＝2026-09-18、09-19），但卡片仍显示 1W/1M/3M/6M/1A 五个区间 → **5 个区间返回同样的 2 个点**，Min/Max 只反映这 2 天，**且无任何覆盖天数提示** | 旗舰卡片看起来像有一年历史，实际 2 天；区间 pills 成了死控件 |
| 数据 | 站点级历史保留 **180 天**且无月粒度 rollup，而 UI 提供 **1A(365 天)** 区间 | 1A 区间对站点永远不完整（需月度 rollup 或去掉该 pills） |
| 功能 | **无"贵/便宜"基准**（区域中位/分位/与最低价差距） | CAA 明说"Faire le plein ou pas?"、gasquebec 卖「prix juste」、achetezlemeilleur 列 Min/Max/Écart/vs Moy.——我们连区域级基准都没有 |
| 功能 | 收藏/关注仍缺少"变化"信号（#31 待实现） | 竞品双雄都靠提醒留客 |
| 功能 | 站点列表硬截断 **30 条**；无分享/深链；无深色模式 | 见 §10，未占用本日额度 |
| 数据 | 上游**无站点 ID、无城市字段、无单站更新时间** | 收藏/日志/关注/站点历史键=`name\|address\|postal` 或 5 位小数坐标，上游改地址/坐标即失联 |
| 数据 | 上游分钟级更新，我们小时级且无陈旧告警 | 上游失效会静默变旧 |
| 工程 | 无单元测试（只有 E2E）、无真实用户分析；`scripts/generate_history.mjs` 已删除（合成器清理完毕） | 重构风险 |
| 平台 | 无 PWA（无 manifest / service worker） | 无法安装到主屏/离线；也是"真·后台提醒"的前置 |
| 无障碍 | 地图 canvas 对读屏不可达；列表项是 div，无 role/tabindex | a11y 硬伤，受众有限 |

## 5. 用户声音：竞品 App 评论主题（2026-09-18 复核）

来源：App Store「Prix Essence Québec : Info Gaz」(id 6739227128, CA) 最近 50 条，公开 RSS
`https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`。

- **均分仅 3.44★**（展示 4.7★ 是历史 5.6k 评分），**17 条 1–2★**，**11–13 条抱怨广告** → 验证"无广告"叙事。
- **9/50 质疑价格准确性**（"plus de 1 fois sur 2 le prix n'est pas le bon bien que « mis à jour il y a 5 minutes »"，3★）。
- 提醒类两条都在吐槽**质量**：推错站（4★, 09-15）、点开不直达那家降价站（3★, 05-18）→ 支撑 §9 与 #31 的取舍。
- 已被满足的诉求："加油日志"（#29）。仍开放：桌面小组件、沿途规划、深色模式。

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- GeoJSON 站点属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空（D/R 14 条 / S 27 / D 201）。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益，不接入。
- 想再"进货"只能靠 Régie 公开出版物：**每日 composantes（PDF，按区域/MRC）**、**每周三 Bulletin PDF**（含各区 marge de détail、12 个月趋势）。

## 7. 关键实测数据（供后续提案引用）

- **价格波动**（`data/history.json`，09-10→09-18，18 区 × 540 个相邻 6h 桶）：区域均价 |Δ| 中位 **0.10¢**、均值 0.65¢；
  23% 步长 ≥1¢、37% 不变；单日区内振幅均值 1.22¢、最大 8.1¢ → **阈值提醒 > 逐次 delta**（#31 的依据）。
- **站间价差**（当前快照）：全省 regular 152.2–240.0¢ → 50 L 差 **43.90$**；Estrie 区内 47.7¢ → 23.85$。
- **区域内基准**（2026-09-19 快照，regular）：Montréal n=226 min 186.9 / 中位 196.9（差 **10.0¢**＝50L 5.00$）；
  Montérégie 175.1 / 191.9（**16.8¢**＝8.40$）；Capitale-Nationale 183.9 / 195.9（12.0¢＝6.00$）；Nord-du-Québec 195.4 / 196.4（1.0¢，n=12）。
- **站点历史**：`data/history/station-history.json` 180 KB 未压缩（20 KB brotli），`tiers={daily_days:180, monthly:'none'}`，
  2 460 站已入账、**仅 2 天**；2 446 站有 ≥2 个 regular 点（所以卡片会画出"2 个点的区间线"）。

## 8. 竞争格局（2026-09-19）

> 数据已商品化（2026-04-01 起全省 ~2 700 家站强制上报，延迟 <5 分钟）→ 竞争点＝**怎么用数据**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方地图 | 权威、<5min、逐站纠错；**无筛选** | 价格标签+筛选仍是最硬差异 |
| **CAA-Québec Info Essence** | 会员工具 | **"Faire le plein ou pas?"**——把问题直接说成"该不该现在加"，用「prix réaliste」对比区域均价（周一至周五 17 区） | 我们没有任何"贵/便宜"判断 |
| **gasquebec.ca** | 纯 Web + Premium | 「prix juste」（Régie 每日组成 + 正常 margin）、**按城市阈值提醒**、城市/区域 SEO 页、API | 提醒（=#31）与"贵不贵"两处都领先我们；其站内对 plancher 的表述自相矛盾 |
| **achetezlemeilleur.ca** | 内容站工具 | 全省均价、**区域表 Min/Max/Écart/vs Hier/vs Moy.**、排除 Costco | 排行表我们已有；没有站级/区域级的"该站贵不贵" |
| **metsdugaz.com** | 纯 Web | per-city SEO 页（2026 新入场） | 无 per-city 落地页 |
| **Prix Essence Québec（App）** | 原生 App（广告+IAP） | 收藏+提醒、短期预测、行程计算；**近 50 条评论 3.44★** | 提醒质量是其软肋（见 §5） |
| Radio-Canada 看板 / EssenceQuébec.com | 媒体/老站 | 市镇搜索、区域均价、红绿气泡 | 无逐站标签、无站级基准 |
| Google Maps / Waze | 通用地图 | 加拿大当前未见油价图层（轶事级证据） | 暂时不构成威胁，持续观察 |

> 关键空白：**CAA / gasquebec / achetezlemeilleur 的"贵不贵"全是区域级**。我们手上有全部 2 461 站的当前快照 →
> 可以给出**站级**判断（区域中位、分位、与最低价差距、50 L 金额差），且不依赖任何外部/历史数据。

## 9. 本轮提案（已建 issue）

**站点卡片：用"当日本区真实基准"填补无历史的信息真空（并处理无效区间控件）** → issue **#34**（`pm-proposal`）。
理由：#32 让站点历史回归真实（现在只有 2 天），但卡片仍摆着 5 个区间 pills、Min/Max 只反映 2 天、且毫无覆盖提示；
同时竞品的"贵不贵"判断全是区域级。用**当日本区真实快照**（中位/分位/与最高最低差距/50 L 金额差）填补卡片，
并让区间控件只在覆盖范围内可用——真实的立即价值 + 诚实性一致。（#31 关注提醒仍在队列中，本轮不重复提议。）

## 10. 已评估但不建议（本轮，避免重复提议）

- **真·后台推送 / Notification**：无后端（Pages 无 push），iOS Web Push 还要求已装到主屏 → 先做应用内（#31）。
- **Régie composantes / Bulletin → prix juste**：只有 PDF，需解析 + 每日落库 + 前端，一次 PR 装不下且脆弱（§11 头号调研项）。
- **站点级月度 rollup（补齐 1A 区间）**：先看体积与价值，留给 §11 调研；本轮改为"区间控件按覆盖禁用"。
- **沿途/路线加油**：需 Mapbox Directions（token scope + 计费）。
- **深色模式**：外观层；**PWA/离线**：价值高但应独立立项。
- **列表 30 条截断 / 分享深链 / 提醒导出**：真实但小，未占本日额度。
- **XLSX（excel_url）接入**：字段与 GeoJSON 相同，无收益。

## 11. 待调研问题（下次优先）

- 站点级历史的**真实增长曲线与体积**（180 天 + 2 460 站）是否会逼近 `check_data_size.py` 预算？要不要月粒度 rollup？
- Régie「Publications quotidiennes / Bulletin」是否有可稳定解析的机器可读来源（PDF 文本层 vs HTML 表格）？引用与授权？
- 上游是否可能新增站点 ID / city 字段（收藏、日志、关注、站点历史键的稳定性）？
- 我们小时级抓取与上游 `last-modified` 的偏差分布（是否值得改为 15 分钟）？
- "数据覆盖天数"是否该成为全局可见的信任信号（不只站点卡片）？
- per-city 落地页（gasquebec/metsdugaz 的 SEO 打法）纯静态站是否值得做。

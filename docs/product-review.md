# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-18（第 3 次运行）。前两轮的成果已落地：#13–#17（搜索/收藏/品牌对标/行程油费/区域排行）、
#29（「Mes pleins」加油日志，commit b97a2eb），以及我上一轮发现的半径圈不一致 bug（7d425aa）。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方公开 GeoJSON（`https://regieessencequebec.ca/stations.geojson.gz`），
  每小时 GitHub Actions 抓取（`scripts/download_data.py` → `process_data.py` → `append_history.py`）并提交回仓库
- 规模（2026-09-17 实测）：**2 461 个站点**、18 个行政区域、三油品；全省均价 **195.1¢**，min 152.2 / max 240.0
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-09-18）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 | Mapbox light-v11；聚类（z<10）+ 价格渐变点 + **站点价格标签**（z≥11）`js/map.js` | 既有 |
| 最低价高亮 | 筛选结果中最低价站点脉冲动画 + 列表 best 标记 `js/stats.js` | 既有 |
| 筛选即所得 | 品牌（top14 + 更多 + 全选/反选）、油品、价格双滑块、区域、半径 5/10/25/50km `js/filters.js`+`stats.js` | 既有（半径圈初值 bug 已修） |
| 站点搜索 | 地址/城市/邮编/品牌/站名，纯离线（去重音 + 折叠标点），搜索时解除半径/区域限制 `js/search.js` | ✅ #13 |
| 收藏 | localStorage、列表置顶、仅看收藏、三处星标 `js/favorites.js`；**仍是纯被动（无任何变化提示）** | ✅ #14 |
| 品牌价格对标 | 品牌行显示站数 + 均价 + 相对全省均价差值 `js/stats.js` | ✅ #15 |
| 区域排行表 | 趋势看板内 18 区 min/avg/max/价差/vs 昨日，可排序 `js/dashboard.js` | ✅ #17 |
| 行程油费估算 | 站点详情 L/100km × 距离（单向/往返），localStorage 记忆 `js/station-card.js` | ✅ #16 |
| **加油日志「Mes pleins」** | localStorage 记录（日期/油品/单价/升数/总额）+ 本月支出、加权实付均价、**对比真实区域历史均价的节省额**；侧栏可折叠区 + 站点详情表单 `js/fillups.js` | ✅ #29 |
| 站点卡片 | Apple-Stocks 风格：大字报价、涨跌 chip、SVG sparkline、油品 pills、1W/1M/3M/6M/1A、mini stats、Chart.js 详情面板 | 既有 |
| 趋势看板 | 区域 chips、油品、7/30/60/90 天、min/avg/7J/30J | 既有 |
| 价格历史 | **真实区域级** 6h 桶（raw 14 天 / daily 12 月 / monthly 永久）；**站点级=区域趋势+固定偏移推算** | 既有 |
| 数据透明度 | 卡片显示 `generated_at`；header 显示站数 | 既有 |
| 导航 | 一键跳 Google / Apple Maps 路线 | 既有 |
| 体验 | 移动底部抽屉（可下拉）、44px 触控、键盘 f/l/t/Esc、三语、`prefers-reduced-motion` | 既有 |
| 测试 | 仅 Playwright E2E（`tests/app.spec.js`，60+ 项，含搜索/收藏/行程/排行/加油日志） | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——官方平台恰恰缺这个（Protégez-Vous、TVA 都点出官方只能逐点 click）。
2. **打开即用、无广告、无安装**：竞品 App 近 50 条评论里 11–13 条在骂广告，这是最硬的情感差异。
3. **官方一手数据 + 真实区域历史**：能回答"哪天/哪个区便宜"和"我这箱油省了多少"（`js/fillups.js` 已用上）。
4. **功能面与主流竞品平齐**：搜索/收藏/历史/排行/油费/加油日志齐备，且全部无后端、无账号。
5. **三语**（fr/en/zh，本轮竞品中未见同等支持）；隐私友好、零运维成本。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 功能 | **收藏是纯被动的**：只有过滤/置顶，用户永远不知道"自己关心的站变了没有" | 竞品（App 与纯 Web 的 gasquebec）都把提醒当选招牌；这也是"值得再打开"的理由 |
| 功能 | 无"贵/便宜"判决（区域中位/分位） | CAA「prix réaliste」、gasquebec「prix juste」都在卖这个判断 |
| 功能 | 站点列表硬截断 **30 条**（`stats.js` `filteredStations.slice(0, 30)`） | 大城市无法浏览全部结果 |
| 功能 | 无分享/深链（无法把某个站点发给别人或收藏为书签） | 地图类产品的常规能力 |
| 功能 | 无深色模式（light-v11 固定） | 有用户明确要求；属外观层，优先级低 |
| 功能 | 无 Régie「价格组成/prix juste」数据 | 见 §6/§7，只有 PDF，装不进一个 PR |
| 数据 | 站点级历史为**推算**；真实 raw 历史到 2026-09-18 仅 **9 天**（14 天窗口未满） | "上周这个站多少钱"仍不可靠；周/日规律洞察样本不足 |
| 数据 | 上游**无站点 ID、无城市字段、无单站更新时间** | 收藏/日志/关注键=`name\|address\|postal_code`，上游改地址即失联 |
| 数据 | 上游分钟级更新，我们小时级；无陈旧告警 | 上游失效会静默变旧 |
| 工程 | 无单元测试（只有 E2E）、无真实用户分析 | 重构风险；优化靠猜 |
| 平台 | 无 PWA（无 manifest / service worker） | 无法安装到主屏、无离线；也是"真·后台提醒"的前置条件 |
| 无障碍 | 地图 canvas 对读屏不可达；列表项是 div，无 role/tabindex | a11y 硬伤，受众有限 |

## 5. 用户声音：竞品 App 评论主题（2026-09-18 复核，数字未变）

来源：App Store「**Prix Essence Québec : Info Gaz**」(id 6739227128, CA) 最近 50 条评论，
iTunes 公开 RSS `https://itunes.apple.com/ca/rss/customerreviews/page=1/id=6739227128/sortby=mostrecent/json`。

- 近 50 条**均分 3.44★**（App Store 展示的 4.7★ 是历史累计 5.6k 评分），**17 条 1–2★**，**11–13 条抱怨广告**（"impossible d'utiliser l'application à cause des pubs"）→ 验证我们"无广告"叙事。
- **9/50 质疑价格准确性**（"plus de 1 fois sur 2 le prix n'est pas le bon bien que « mis à jour il y a 5 minutes »"，3★）→ 信任是赛道核心痛点，官方源 + 更新时间是我们的资产。
- **提醒类唯一两条都指向"质量"**：4★（2026-09-15）抱怨"非收藏站也推送通知"；3★（2026-05-18）"点开通知后我期待直接落到那家降价的站点" → 提醒要做到**精准 + 可点击直达**。
- 明确索要"加油日志"的评论（2026-09-15，4★）**已被 #29 满足**；其余诉求：桌面小组件、可下滑的附近站点列表（已有）、沿途规划、深色模式。
- 竞品是社区众包校验、我们是官方源 → "更新时间"叙事应该更醒目。

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

直接下载 `stations.geojson.gz`（126 KB gz，2 461 features）核对：

- 站点属性只有：`Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`。
- ❌ 无站点 ID；❌ 无 city/municipality（城市搜索靠 Address 文本，够用）；❌ 无单站更新时间；❌ 无服务设施。
- `IsAvailable=false` ⟺ 价格为空（Diesel 201 / Super 27 / Régulier 14 条）；全省 2 461 站 Status 均为 `En opération`。
- metadata 的 `excel_url` XLSX 实测列＝Nom/Bannière/Adresse/Région/Code Postal/Lat/Lon/三油品价，**无新字段** → 不值得接入。
- 想再"进货"只能去 Régie 的公开出版物：**每日 composantes（按区域/MRC 的 PDF）**、**每周三 Bulletin PDF**
  （`.../Publications-hebdomadaires/Bulletin/bulletin.pdf`，含每区"marge de détail estimée（不含税）"与 12 个月趋势、按区均价表）。

## 7. 价格波动实测（本轮新增，用于判断提醒类功能的价值）

`data/history.json`，2026-09-10 → 09-18（9 天），18 区 × 三油品，540 个相邻 6h 桶：

- 区域均价 |Δ|：中位 **0.10¢**、均值 0.65¢、最大 6.3¢；**23%** 的 6h 步长 ≥1¢、11% ≥2¢、37% 完全不变。
- 单日区内振幅：均值 **1.22¢**、最大 8.1¢。→ **日常波动以小幅为主**，所以"每次变化的 delta"价值有限，
  **"用户设定的阈值被触发"才是更实用的提醒语义**（与 gasquebec 的"franchit un seuil"一致）。
- 站间价差（当前快照）：全省 regular 152.2–240.0¢ → 50 L 差 **43.90$**；Estrie 区内价差 47.7¢ → 50L 差 23.85$。

## 8. 竞争格局（2026-09-18）

> 背景：2026-04-01 起全省 ~2 700 家站必须上报 Régie 公开平台（延迟 <5 分钟），数据已商品化，竞争点＝**怎么用数据**。

| 竞品 | 形态 | 关键能力 | 我们的差距 |
|------|------|----------|-----------|
| **Régie essence Québec（官方）** | 官方地图 | 权威、<5min、逐站纠错；**无筛选** | 我们的价格标签+筛选是最硬差异 |
| **gasquebec.ca / gazquebec.ca** | 纯 Web + Premium | 城市/区域页（SEO）、**"prix juste"**（Régie 每日组成 + 正常 margin）、**按城市阈值的价格提醒**、收藏上限+Premium、API、周报 | ⚠️ 提醒与"贵不贵"两处都领先我们；其 /villes 页仍在讲"每周三公布 plancher"，但同站指南称 plancher 已于 2025-06-07 取消 → 该站内容自相矛盾，我们不做未经核实的同类表述 |
| **achetezlemeilleur.ca/prix-essence** | 内容站工具 | 全省均价、最贵/最便宜区、最大价差、**排除 Costco**、区域排行 | 我们已有排行表；缺"会员站（Costco）视角" |
| **metsdugaz.com** | 纯 Web | per-city SEO 页，2026 新入场 | 我们无 per-city 落地页 |
| **Prix Essence Québec（App）** | 原生 App（广告+IAP） | 收藏+个性化提醒、短期预测、行程计算；**近 50 条评论均分仅 3.44★** | 提醒质量是他们的软肋；我们无提醒能力 |
| **CAA-Québec Info Essence** | 会员工具 | 周一至周五 17 区"prix réaliste vs 均价" | 同 gasquebec，靠外部数据 |
| Radio-Canada 看板 / EssenceQuébec.com | 媒体/老站 | 市镇搜索、区域均价、红绿气泡 | 无逐站标签、无历史面板 |
| Google Maps / Waze | 通用地图 | 加拿大当前未见油价图层（仅轶事级证据） | 暂不构成魁省威胁，持续观察 |

## 9. 本轮提案（已建 issue）

**「关注站价格提醒」：阈值 + 自上次访问以来的真实变动（应用内，无推送）** → issue #31（`pm-proposal`）。
理由：竞品双雄都靠提醒留客，而我们的收藏完全被动；我们的提醒可以做成"精准 + 直达"，正好打竞品评论暴露的软肋。
实现要点：新建 `js/watch.js`（localStorage，复用 `favorites.js` 的 `stationId`），仅用**观测到的真实价格**，
只在打开/刷新时评估（不承诺实时监控），侧栏"我的关注"区在空时隐藏。

## 10. 已评估但不建议（本轮，避免重复提议）

- **真·后台推送/browser Notification**：无后端（GitHub Pages 无 push 服务），iOS Safari 的 Web Push 还要求"已添加到主屏"，
  可靠性不足 → 先做**应用内**提醒；等 PWA 立项后再谈推送。
- **Régie「composantes / Bulletin」→ prix juste**：只有 PDF（区域/MRC 分文件），要 PDF 解析 + 每日落库 + 前端，
  一次 PR 装不下且脆弱 → 仍列为 §11 头号调研项。
- **站点"贵/便宜"判决徽标**：证据充分（CAA/gasquebec）但与现有"价格颜色渐变 + 排序 + 最低价脉冲"部分重复，本日额度留给提醒。
- **沿途/路线加油**：需 Mapbox Directions（token scope + 计费）。
- **深色模式**：外观层；**PWA/离线**：价值高但应独立立项（可与推送一起规划）。
- **站点级真实历史持久化**：与 `docs/history-data.md` 的 4–5MB 预算冲突，需新稀疏方案。
- **列表 30 条截断 / 分享深链**：真实但小，未占用本日额度（见 §4，留给下次临近改动时顺手做）。
- **XLSX（excel_url）接入**：字段与 GeoJSON 相同，无收益。

## 11. 待调研问题（下次优先）

- Régie「Publications quotidiennes / Bulletin」是否有可稳定解析的机器可读来源（PDF 文本层 vs HTML 表格）？引用与授权要求？
- gasquebec 关于 plancher 的自相矛盾内容：向 Régie 核实 2026 年现行规则（若有 plancher，会影响"何时加油"叙事）。
- 上游是否可能新增站点 ID / city 字段（收藏、日志、关注键的稳定性）？
- 我们小时级抓取的 `generated_at` 与上游 `last-modified` 偏差分布（是否值得改为 15 分钟）？
- 加油日志/关注的导出导入（换机迁移，纯前端），以及"全部时间"视图（目前只有本月）。
- per-city 落地页（gasquebec/metsdugaz 的 SEO 打法）纯静态站是否值得做。

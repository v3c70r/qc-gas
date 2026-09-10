# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每周由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses.

_Last updated: 2026-09-10（首次正式运行）_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省**Régie de l'énergie** 官方公开 GeoJSON（`https://regieessencequebec.ca/stations.geojson.gz`），
  由每小时 GitHub Actions 抓取（`scripts/download_data.py` → `process_data.py` → `append_history.py`）并提交回仓库
- 规模：**2 466 个站点**、18 个行政区域、三种油品（Régulier / Super / Diesel）
- 字段：name / brand / status / address / **postal_code** / region / 三油品价格（`data/stations.json`）
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪；前端公开 Mapbox token（已配 URL 限制）

## 2. 当前能力清单（已核对代码）

| 能力 | 说明（文件） |
|------|------|
| 地图浏览 | Mapbox light-v11；聚类圆点（z<10）+ 价格渐变点 + **站点上方价格标签**（z≥11，Airbnb/Zillow 风格，随油品切换）`js/map.js` |
| 最低价高亮 | 当前筛选结果中最低价站点脉冲动画 `js/stats.js` |
| 筛选即所得 | 品牌（计数徽标、top14 + 更多、全选/反选）、油品 radio、价格双滑块(150–240¢)、区域下拉、半径 5/10/25/50km → 全部真实作用于地图 source `js/filters.js`+`js/stats.js` |
| 站点列表 | 侧栏 top-30，按价格排序，最低价高亮 + 距离 `js/stats.js` |
| 站点卡片 | Apple-Stocks 风格：大字报价、涨跌 chip、SVG sparkline、油品 pills、1W/1M/3M/6M/1A、mini stats、展开为右侧 Chart.js 详情面板 `js/station-card.js` |
| 趋势看板 | 右侧滑出：区域 chips（全省+18 区）、油品、7/30/60/90 天、min/avg/7J/30J 涨跌 `js/dashboard.js` |
| 价格历史 | **真实区域级** 6h 桶（raw 14 天 / daily 12 月 / monthly 永久，brotli + 40MB 守卫）；**站点级为"区域趋势 + 固定偏移"推算** `js/history.js`, `docs/history-data.md` |
| 导航 | 卡片/详情一键跳转 Google / Apple Maps 路线 `js/station-card.js` |
| 定位 | 浏览器定位 + 半径圈 `js/filters.js` |
| 体验 | 移动端底部抽屉、44px 触控目标、`dvh`、键盘快捷键 f/l/t/Esc、三语、`prefers-reduced-motion` |
| 数据透明度 | 卡片显示数据 `generated_at` 更新时间 `js/stats.js` |
| 测试 | 仅 Playwright 端到端（`tests/app.spec.js`，约 30 项），无单元测试 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格** + **筛选真实作用到地图**——官方平台恰恰缺这个（见 §5）。
2. **打开即用**：无登录/无广告/无安装，冷启动轻量 + Mapbox CDN。
3. **官方一手数据**：与 Régie 同源，含更新时间，覆盖全省含偏远地区。
4. **历史趋势**：区域级真实历史 + 分层保留，竞品多为 7–14 天。
5. **三语**：法语/英语/中文——在本轮所有竞品中未见同等三语支持。
6. **隐私友好、零运维成本**：全静态 + Actions，无服务器账单。

## 4. 劣势 / 技术债 / 风险（与竞品对照后更新）

| 类别 | 问题 | 影响 |
|------|------|------|
| 功能 | **无搜索**（地址/城市/邮编）| 竞品 gazquebec、Radio-Canada、essence-quebec.ca 均已有；陌生地点需手动拖图 |
| 功能 | 无收藏/常去站点 | Prix Essence Québec、gasquebec 均有；复访动力弱 |
| 功能 | 无价格提醒/推送 | Prix Essence Québec 核心卖点；需 service worker |
| 功能 | 无价格预测（"现在该不该加"）| Prix Essence Québec 有短期预测；CAA 有"真实价格 vs 均价" |
| 功能 | 无行程油费估算（L/100km×距离）| Prix Essence Québec 有；价值感知低一档 |
| 功能 | 品牌无**价格对标**（仅计数）| gasquebec 显示"某品牌 vs 全省均价"；achetezlemeilleur 有 bannière 筛选 |
| 功能 | 无区域排行表（min/avg/max/价差/环比）| achetezlemeilleur、Radio-Canada 有 |
| 功能 | 站点列表硬截断 30 条 | 大城市无法浏览全部结果 |
| 数据 | 站点级历史为推算（区域趋势+固定偏移）| 无法回答"这个站上个月多少钱" |
| 数据 | 上游 <5 分钟更新，我们每小时抓取且无陈旧告警 | 上游失效时会静默变旧（仅显示 generated_at）|
| 工程 | 无单元测试；仅 E2E | 重构风险 |
| 工程 | 无真实用户分析 | 优化靠猜（隐私换取）|
| 平台 | 无 PWA manifest / 离线 / 安装到主屏 | 移动端留存弱（竞品多为原生 App）|
| 无障碍 | 地图 canvas 内容对屏幕阅读器不可达 | a11y 限制 |

## 5. 竞争格局（2026-09，含证据）

> 关键背景：**2026-04-01 起**魁省强制所有约 2 700 家加油站把价格上报 Régie 的公开平台，
> 平台更新延迟 <5 分钟；并有"Signaler une inexactitude"逐站纠错按钮。
> 这使整个赛道的数据源趋同——**竞争点已从"有没有数据"转移到"怎么用数据"。**

| 竞品 | 形态 | 关键能力 | 我们的机会 |
|------|------|----------|-----------|
| **Régie de l'énergie 官方平台** `regieessencequebec.ca` | 官方地图 | 权威、<5min、逐站纠错；**但无筛选**，只能逐个 hover 看价 | 我们有价格标签 + 真实筛选（Protégez-Vous 明确点出官方缺筛选）|
| **achetezlemeilleur.ca/prix-essence** | 内容站工具 | 2466 站、18 区、秒级更新、全省均价、最贵/最便宜区、最大价差、品牌+区域筛选、**排除 Costco**、区域排行表（Moy/Min/Max/Écart/**vs Hier/vs Moy.**）| 我们缺排行表与环比；其地图交互弱于我们 |
| **gasquebec.ca** | 纯 Web | 品牌 vs 全省均价、Top3 便宜/最贵城市、**7 天变化**、收藏、城市页、地址/邮编/城市搜索；无安装/账号/广告 | 与我们定位最像；我们缺搜索与品牌对标 |
| **Radio-Canada 油价看板** | 媒体工具 | 按市镇搜索/定位、区域+油品均价、最贵/最便宜站、每小时更新 | 无逐站地图标签；我们有 |
| **EssenceQuébec.com / map.essencequebec.com** | 老牌 Web | 20+ 年品牌、红绿气泡、绝对/相对配色模式、区域最低价 | 配色与区域最低价我们已有；无趋势面板 |
| **Prix Essence Québec（App）** 50K+ 下载 4.7★ | 原生 App（广告+IAP）| 收藏+提醒、短期预测、每日变化、14 天图表、区域/子区域、**行程油费计算** | 我们无需安装、无广告、有地图标签；缺收藏/预测/行程计算 |
| **CAA-Québec Info Essence** | 会员工具 | "**prix réaliste** vs 均价"，告诉你现在该不该加 | 需外部油价数据，差异化价值高但成本高 |
| GasBuddy | 众包 App | 覆盖广、社区/积分 | 魁省官方数据时代其时效劣势明显（Reddit 吐槽数据过时）|

> 证据 URL：Régie 上线公告 `regie-energie.qc.ca/fr/nouvelles/communiques/la-regie-de-lenergie-lance-une-plateforme-interactive-pour-suivre-les-prix-de-lessence-partout-au-quebec`；
> Protégez-Vous「4 outils pour suivre les prix de l'essence」(2026-07-09) `protegez-vous.ca/nouvelles/automobile/4-outils-pour-suivre-les-prix-de-l-essence`；
> achetezlemeilleur `achetezlemeilleur.ca/prix-essence/`；gasquebec `gasquebec.ca/` 与 `gazquebec.ca/prix-essence-pres-de-moi`；
> Radio-Canada `ici.radio-canada.ca/info/tableau-de-bord-carte-prix-essence-quebec/`；App 商店 `apps.apple.com/ca/app/prix-essence-québec-info-gaz/id6739227128`；
> CAA `caaquebec.com/fr/mobilite/info-essence`；官方纠错按钮 `lequotidien.com/affaires/2026/04/01/les-prix-de-lessence-disponibles-en-temps-reel-sur-regie-essence-quebec-4TKZDRIGEREPXDHZP65FLAWKLY/`。

## 6. 本轮结论：差异化定位

数据已商品化 → 我们应聚焦**官方平台与媒体工具都做不好的"地图交互 + 决策辅助"**：

1. 官方平台**没有筛选** → 我们的"筛选即所得 + 价格标签"是最硬差异，应继续加深（如品牌价格对标、区域排行）。
2. 竞品普遍提供**搜索 / 收藏 / 油费估算**三项"日常决策"能力，且都不需要后端——正是我们最缺、也最容易补的。
3. 隐私、无广告、三语、零运维是我们的长期护城河（gasquebec 虽同为"无安装无账号"，但无三语与历史分层）。

## 7. 下周待调研 / 待验证的问题

- Régie GeoJSON 是否含 **city/municipality** 字段（现 `process_data.py` 只取 Address/PostalCode/Region）？
  若有，可实现**纯离线城市搜索**，无需任何 Geocoding API 与 token scope 变更。
- 若走 Mapbox Search Box/Geocoding API：公开 token 能否加 `search:read` 作用域？免费额度与滥用风险？
  （参考 `.agents/skills/mapbox-search-integration`）
- 价格提醒的最小可行实现（service worker 定时拉取 + Notification）在纯静态站的可行性与隐私权衡。
- 站点级历史的低成本持久化（例如只存每日 delta / top-N）是否值得，是否会与 `docs/history-data.md` 预算冲突。
- 竞品 App 商店评论主题（Reddit 被墙未取到原文；改用 App Store 评论页）。

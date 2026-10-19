# Product Review — Essence Québec

> **这是 PM Agent 的长期记忆文件**，每天由 `.agent/pm/run.mjs` 唤醒的
> Product Manager agent 更新。人类 owner 也会阅读/修订它。
> Keep it concise (≤ 180 lines), evidence-based, and honest about weaknesses。

> ⚠️ **环境提示**：gh 活动账号曾失效；本轮 `gh auth status` 显示 `v3c70r` 有效。
> 历史坑：备用账号无权加标签 → 新建提案若缺 `pm-proposal` 标签会被筛选器**静默跳过**。**每轮创建后必须核对标签**。

_Last updated: 2026-10-19（第 24 次运行）。**#96 / #87 / #88 / #89 全部落地**（PR #112 / #110 / #108 / #107），
上一轮的记忆告一段落：站点卡片现在真的显示「price changed X ago」。本轮转向**决策层**缺口：
列表只有「价格 / 距离」两档，而两家新竞品（gazgain.ca、pleino.ca）已经把「绕路值不值」做成主打 → 提案 #117。_

## 1. 产品是什么

**Essence Québec**（`https://qgu.io/qc-gas/`）— 面向魁北克司机的**地图优先**油价查询 Web 应用（可安装 PWA）。

- 纯静态：Vanilla ES modules + Mapbox GL JS + Vite → GitHub Pages（自定义域 `qgu.io`）
- 数据源：魁省 **Régie de l'énergie** 官方 GeoJSON + 官方每周 Bulletin PDF（零售毛利/区域均价）；
  抓取：**本机 crontab 每 5 分钟唤醒 → `repository_dispatch` → 闸门按时刻表判定**（#91/#93），Actions `schedule` 仅小时级兜底；探针本地日志、派生快照不入 git（#92/#103/#105）
- 规模（2026-10-19 上游实测）：**2 462 站**（2 447 有 regular 价）、18 区、三油品；站点变更台账 2 465 键
- 多语言：法语（默认）/ 英语 / 简体中文；无后端、无账号、无追踪、无广告

## 2. 能力清单（已核对代码，2026-10-19）

| 能力 | 说明（文件） | 状态 |
|------|------|------|
| 地图浏览 / 价格标签 / 最低价高亮 | 聚类（z<10）+ 渐变点 + 价格标签（z≥11）+ 最低价脉冲；底图已换成 Google/Apple 风格（#109） | 既有 |
| 全省优先视图 + 位置记忆 | 首次访问不套半径、全省 fitBounds；`qc-gas-view` | ✅ #39 |
| 筛选即所得（区域=范围，半径=我周围） | 选区域 → 退出半径 + `fitRegionBounds()`；0 结果给出出路 | ✅ #60 / #61 |
| 侧栏列表：看全 + 排序 / 视图切换 | 30/页分批 + `prix ↔ distance`；`Stations ↔ Par région` 视图开关（`#list-view`） | ✅ #50 / #53 / #87 |
| 列表行键盘可达 / 区域行 | `role="button"` + Enter/Space + 焦点环 + roving tabindex（radiogroup） | ✅ #88 / #87 |
| **站点级价格变更时间** | `js/station-card.js` 读 `data/history/station-changes.json` → "observé il y a X" + "prix changé il y a X"/"jamais observé"，含 stale 标记与来源说明 | ✅ #96（PR #112） |
| 区域摘要（最低价 + 中位 + 站数） | `computeRegionSummaries()` / `createRegionRow()`；有 scope 时自动隐藏 | ✅ #87 |
| 收藏 / 关注提醒 | 置顶与仅看收藏；阈值触发；坐标主键 + 双键兜底；找不到的站不再静默删除 | ✅ #14 / #31 / #65 / #89 |
| 站点当日基准 / 官方周报毛利 | 区域中位/分位/50 L 差额（#34）；Régie 17 区毛利 + 装车价（#49/#54） | ✅ |
| 卡片/面板与快照一致 / 「Mes données」 | 订阅 `stations:loaded` 重渲染；版本化 JSON 导出/导入 | ✅ #51 / #57 / #58 |
| 加油日志「Mes pleins」 | 记录 + 期间汇总 + CSV（RFC 4180、UTF-8 BOM） | ✅ #29 / #62 / #64 |
| 行程油费估算 | 卡片内 L/100km 输入（默认 8）+ 单程/往返；`tripPrefs` 持久化（`js/databackup.js`） | ✅ #16 |
| PWA / 更新策略 / 新鲜度 | network-first 导航 + 版本化缓存 + 更新提示；全局相对时间 + 可见时刷新 | ✅ #37 / #45 / #47 / #70 |
| 抓取管线 | 区域/品牌分层 + 显式时刻表 + 本机 5 分钟唤醒 + `metrics-ledger.json` + 站点变更台账 | ✅ #71 / #74–#77 / #82–#86 / #91 / #93 |
| 品牌对标 / 区域排行 / 趋势看板 / 导航 / 历史 | `js/stats.js`、`js/dashboard.js`、`js/history.js` | ✅ #15 / #16 / #17 / #32 |
| 体验 / 测试 | 移动底部抽屉、44px、三语；Playwright e2e 142+ 项；`scripts/test_*.py` | 既有 |

## 3. 优势（为什么魁省司机愿意用）

1. **地图上直接看到价格 + 筛选真实作用到地图**——Protégez-Vous 点出官方平台"无法筛选、只能逐个 hover"（最硬差异）。
2. **官方"贵不贵"口径齐全**：Régie 周报零售毛利（#49）+ 站级中位/分位（#34）+ 区域=范围语义（#60）+ 区域摘要（#87）。
3. **新鲜度双重可见**：全局「mis à jour il y a X」+ **站点级**「prix changé il y a X」（#96）；端到端陈旧度实测 ≈5 min（#93）。
4. **数据诚实性 + 个人数据自持**：无合成/外推、覆盖门控、离线标注、无效坐标不冒充、`t: null` 明说"从未观测到变化"、导出/导入 + CSV。
5. **零运维 + 三语 + 无广告**：竞品评论里广告是头号抱怨；我们天生没有。

## 4. 劣势 / 技术债 / 数据限制（本轮核对）

| 类别 | 问题 | 影响 |
|------|------|------|
| 功能（**本日提案**） | 列表只有 `prix` / `distance` 两档排序，**没有"这趟绕路值不值"**；行上只有单价与公里数，用户得自己做除法（50 L × 8 L/100km） | 实测 38% 的位置上，默认首行比成本最优多花 >\$0.50（20% >\$2）；两家新竞品已把这块做成主打 |
| 产品 | 绕路只按**直线距离**（无路径规划）；`roadFactor` 若引入必须是集中常量并在文案里标注"估算" | 与 GazGain「distance routière exacte」有差距，必须诚实标注 |
| 交付 | #114（`station-history.json` 1.36 MB/次重写，4.2 MiB/天）仍 open；#78 探针日志仍 open | 仓库体积预算的最后杠杆 |
| 数据 | **派生快照不再入仓**（#103/#105：`data/stations.json` 已 gitignore）→ 任何复现分析必须从上游 `stations.geojson.gz` 取或跑构建 | 影响所有后续实测的可复现性（§7 已给方法） |
| 数据 | `update-profile.json` 的 `hours`/`marks` 在 P0 变更后**样本被重置**（marks 里每格 samples=1–4） | "按时段变价"洞察**当前不可做**（需 ≥2 周重新积累） |
| 数据 | 站点级历史 180 天保留、无月 rollup；`station-history.json` 1.36 MB、`station-changes.json` 239 KB（均有 `.br`） | 盯 `check_data_size.py` 40 MB 预算 |
| 数据 | 上游无站点 ID、无 city、无单站更新时间 | 站点级"何时变"只能靠**我们自己的观测**（#96 已按此措辞） |
| 功能 | 无分享/深链；无深色模式 | 见 §10 |
| 工程 | 无前端单元测试（只有 E2E） | 重构风险 |

## 5. 用户声音

- **owner 一手反馈**：#41（部署旧 checkout 导致价格差）、#43（"到达现场后油价不准"→ P0 抓取计划）、#55（iPhone Safari 半径筛选被遮挡）。
- **竞品 App 评论（Prix Essence Québec，最近 50 条；均分 3.42★、17 条 1–2★）**：11–13 条骂广告、**9 条质疑价格准确性/时效**、提醒推错站、要直达降价站、要主屏小组件。
- **infoEssence 评论**："Cette station n'affiche pas le bon prix sur le site"、"Les prix sont pas à jour!" → **"这个价是不是旧的"是全赛道最高频的信任痛点**（#96 已针对性落地）。

## 6. 上游数据实测（2026-09-17 完成，避免重复调研）

- 站点 GeoJSON 属性只有 `Name, brand, Status, Address, PostalCode, Region, Prices[{GasType, Price, IsAvailable}]`：
  ❌ 无站点 ID、❌ 无 city、❌ 无单站更新时间、❌ 无服务设施；`IsAvailable=false` ⟺ 价格为空。
- metadata 的 `excel_url` XLSX 字段与 GeoJSON 相同 → 无收益。
- 区名匹配（2026-10-03）：`normalizeText()` 能匹配周报 en dash/无重音写法；只有 `Municipalités hors MRC \ CMM`（1 站）无对应行。
- 平台无其他公开接口（2026-10-13 实测 `/api*`、`swagger.json` 全 404）→ Reddit 所说"公共 API"即 `stations.geojson.gz`。

## 7. 关键实测数据（供后续提案引用）

- **⭐ 绕路经济性（2026-10-19，本轮新增，可复现）**
  - 方法：`curl https://regieessencequebec.ca/stations.geojson.gz`（2462 站、2447 有 regular）→ 取**每第 4 个站**为合成"用户位置"（599 个），候选 = **25 km 内**其他站（25 km = 应用默认半径）；
    `price_first` = 价格升序首行（并列取最近）＝列表默认首行；`cost_first` = 总成本最小，
    `cost = 50 L × 价 + (2 × 直线距离 × 8 L/100km) × 价`（往返、按该站油价）；`gain = cost(price_first) − cost(cost_first)`。
  - 结果（不乘道路系数）：**38% 的位置 gain > \$0.50**、**20% > \$2**；发生差异时中位 **\$2.12**、p90 **\$4.82**、max \$7.13；被越过的绕路中位 **14.1 km 单程**。
    乘 1.3 道路系数：47% / 30%，中位 \$2.89（结论对道路系数稳健）。
  - 分区（RF=1.3）：Lanaudière 70%、Chaudière-Appalaches 69%、Bas-Saint-Laurent 64%、Estrie 61%、Montérégie 53%、Laval 94%(n=17)；**Montréal 仅 14%、Côte-Nord 0%** → 价值集中在"摊得开"的区域，市区几乎为 0。
  - 首行距离：`price_first` 中位 14.1 km（p90 22.8），`cost_first` 中位 5.2 km。
- **站点变更台账**：`station-changes.json` → `last {observed 2462, changed 92}`（2026-10-19T17:00Z）；2 236 个键有 `t`，中位"距上次变价"46.7 h；40.8% 的站在 24 h 内变过价。
- **摄取延迟**：`metrics-ledger.json` 300 样本，`latency p50 = 144 s`、p90 307 s（本机 5 分钟唤醒 + 闸门）。
- **站点键流失（2026-10-04）**：30 天文本键消失 27/2466 = 1.09%（其中 12 个坐标仍在）；坐标键 0.61% → #89 已修。
- **首屏列表区域偏斜**：10-05 3/18 区可见、10-11 15/18、10-12 16/18 区 0 行 → #87 用"Par région"视图回答（默认仍是站点行）。
- **价格水平 / 体量**：regular 169.9 / 中位 189.9 / 239.3；e2e 142+ 项。

## 8. 竞争格局（2026-10-19）

> 数据已商品化（2026-04-01 起强制上报、延迟 <5 分钟；GitHub 上另有 8 个 0★ 同源克隆）→ 竞争点＝**怎么用数据 + 交付是否新鲜**。
> **新变化：绕路经济学被两家新玩家占位**（gazgain.ca、pleino.ca），都用同一份官方数据。

| 竞品 | 形态 | 关键能力 | 我们的差距/机会 |
|------|------|----------|-----------|
| **GazGain（gazgain.ca）** | Web + App 外壳 | 主打"净节省 = 毛省 − 绕路成本"；**道路距离**、油箱容量、油耗、油位、\$0.50 净省下限、城市落地页 | 我们只按直线距离；但它**不是地图优先**（点地图选点 → 算一单）→ 我们可在"地图 + 列表"里直接给出排序 |
| **Pleino（pleino.ca）** | Web + CarPlay App | "最便宜不一定最省"；把**时间**也折价（20 \$/h）、给 verdict、直接起导航；城市页 | 我们不做时间折价（主观）；地图/筛选/官方毛利仍是我们的差异 |
| **Régie essence Québec（官方）** | 官方 Web 地图 | 权威、<5 min、逐站纠错；无筛选；周报毛利 | 筛选/标签/毛利口径已补齐 |
| **infoEssence – Prix Essence QC** | 原生 App | "每 5 分钟更新"、预测涨价、区域气压计、提醒、排序（价格/距离）、Watch/小组件/App Clip | 原生能力无法对齐；其排序也只有两档 → 本日提案是差异化点 |
| **GasBuddy** | 原生 App | 站点页显示价格更新时间 + 社区校验；**路线沿途油价** | "每站显示时效"我们已具备（#96） |
| **gasquebec.ca / map.essencequebec.com** | 纯 Web | 「prix juste」、按城市阈值提醒、城市/区域独立页（SEO）、同源地图 | 首屏区域入口已有（#87） |
| **CAA-Québec Info Essence** | 会员 Web 工具 | 现实价 vs 均价；"si c'est le bon moment de faire un détour" | 完整绕路模型未做（本日提案补第一层） |
| **Prix Essence Québec（App）** | 原生 App | 收藏+提醒、"愿意开的距离"、预测涨价概率 | 我们不预测（样本不足 + 诚实性优先） |
| **Google Maps / Waze** | 通用地图 | 显示油价（偶有区域失效） | 巨头不稳定 = 机会窗口 |

## 9. 本轮提案（已建 issue）

**列表第三档排序「Coût total」：按「50 L 油钱 + 往返绕路油费」排，并把白跑一趟的便宜站排掉** → issue **#117**（`pm-proposal`）。
为什么是它：① 这是 **#90 被 REJECT 时明确给出的重提路径**（"实现级收益实测 + 按区分解 + 可第三方复现的指标定义"），本轮三项全部补齐（§7）；② **竞品已占位**（gazgain.ca / pleino.ca 主打"净节省"，CAA 问的是"值不值得绕"），我们两档排序明显落后；③ 复用已有零件：`haversineDistance()`、`getReferencePoint()`、`tripPrefs.consumption`、distance 档已有的"需要参考点"提示（`distanceSortNeedsLocation`）；④ 一行改动可测（e2e）。

## 10. 已评估但不建议 / 待提（避免重复提议）

- **🚫 「价格 + 距离综合排序」（#90）已被 REJECT** —— 只能按 §7 的新证据重提（本轮已按此重提为 #117，**若 #117 再被否，就不再碰这个方向**）。
  教训：排序/推荐类提案的价值证据必须是"首选是否变化 + 用户实际省多少钱 + 按区分解"，不是"多少候选属于某类"。
- **🚫 列表行显示「多久没变」**（#96 的行级延伸）：`t` 是"上次变价时刻"不是"上次观测"，价格长期不变 ≠ 陈旧 → 行上是噪声，只保留在卡片（避免把"稳定"说成"过期"）。
- **🚫 "按区把 Par région 设为默认视图"**：#87 的落地注释明确写了 "Opt-in only: the default view must not change" —— 这是实现时的产品决定，不翻。
- **🚫 "按时段/星期几变价"洞察**：`update-profile.json` 的 `hours`/`marks` 已被 P0 变更重置（每格 samples 1–4）→ 需 ≥2 周重新积累，**下轮之前不要再提**。
- **时间折价（Pleino 的 20 \$/h）**：主观参数、易被认为操纵排序 → 不做；只用油钱，且文案写明"估算是直线距离"。
- **深色模式**：仅 1 条评论要求，属筛检员列明的"纯外观偏好"→ 暂不提。
- **分享/深链 / App Clip 等价物 / 主屏小组件**：无直接用户证据 → 暂缓。
- **#63 STEP 2（省级行补位置线索）**：不要碰 `.details`（L2885 用它查 map source）与 `.distance`（L3231 解析）→ 需先定安全形态。
- **站点级 1A rollup / 月粒度降采样**、**加油日志 L/100 km（里程表）**、**每日 `composantes`/`rqe.pdf`**：均暂缓（理由同前几轮）。

## 11. 待调研问题（下次优先）

- #117 落地后：是否把"净节省"做成**行内徽章**（"+\$2.10 vs plus proche"）而不是只靠排序？以及油箱容量是否要可变（现固定 50 L）。
- **道路系数**：直线 → 道路的比值在魁省城乡分别是多少？能否用 Mapbox Directions 免费额度做**少量抽样校准**（只做离线校准，不在前端调用）？
- 复现性：`data/stations.json` 已不入仓 → 是否需要在 `scripts/` 落一个"从上游抓快照 + 复算指标"的小脚本（本日提案已包含最小版本）。
- **#114 / #78**（体积与探针日志）落地后的仓库体积与预算余量。
- Pleino/GazGain 的实际转化点：它们的城市落地页（SEO）是否是我们要补的入口？先看它们的定价与用户量再判断。
- `update-profile.json` 重新积累两条思路：等到 ≥2 周后做"变价时段分布"；或改用 `station-changes.json` 的 `t` 分布做**周内**近似（注意：`t` 只有最后一次变价，会低估）。

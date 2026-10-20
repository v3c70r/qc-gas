# Basemap style：Google/Apple Maps 观感（issue #109）

底图从 `mapbox://styles/mapbox/light-v11` 换成 **`mapbox://styles/mapbox/streets-v12`**，
并在其上叠一层 Google/Apple Maps 风格调色板。实现全部集中在
[`js/map-style.js`](../js/map-style.js)（纯数据 + 一个 `applyBasemapPalette(map)`），
`js/map.js` 只负责在 `style.load` 里调用它。

## 为什么不是 light-v11

| 样式 | 图层数 | 道路层 | POI 层 | 观感 |
|---|---|---|---|---|
| `light-v11`（旧） | 50 | 22 | 3 | 极简浅灰，几乎无道路细节/土地利用 → 无法靠地图外观定位 |
| **`streets-v12`（现）** | **134**（实际渲染 140，含应用层） | **93** | 3 | 完整道路分级 + 土地利用 + POI + 带 halo 的标签 |

选 `streets-v12` 而不是 `standard`：`standard` 偏 3D 未来感、不像 Google/Apple；
`navigation-day-v1` 路网被精简成导航视图。只用官方样式 URL，不硬编码私有样式。

## 调色板 token

`BASEMAP_PALETTE`（`js/map-style.js`）：

| token | 值 | 参考 | 落到 `streets-v12` 的哪些图层 |
|---|---|---|---|
| `land` | `#f2efe9` | Google 陆地暖灰白 | `land` → `background-color` |
| `water` | `#aadaff` | Google 水体浅蓝 | `water`、`water-shadow` → `fill-color`；`waterway`、`waterway-shadow`、`ferry`、`ferry-auto` → `line-color` |
| `park` | `#c8e8c9` | Google 公园绿 | `national-park` → `fill-color`；`landuse`/`landcover` 的 park/pitch/golf/grass 分支；`golf-hole-line` |
| `wood` | `#cfe6c4` | 林地略深 | `landuse`/`landcover` 的 wood/scrub 分支 |
| `landuse` | `#eceae4` | 其它土地利用的中性底 | `landuse`、`landcover` 的兜底分支 |
| `building` | `#e8e5e1` | Google 建筑 | `building`、`building-underground` → `fill-color` |
| `roadMinor` | `#ffffff` | 次要道路纯白 | `road-street(-low)`、`road-secondary-tertiary`、`road-minor(-link)`、`road-major-link`、`road-primary` 及其 tunnel/bridge 变体 |
| `roadCase` | `#d9d6d0` | 道路描边浅灰 | 所有 `*-case` 层（`road-*`、`tunnel-*`、`bridge-*`） |
| `motorway` | `#ffe082` | 高速浅黄 | `road-motorway-trunk`、`tunnel-*`、`bridge-*`、`bridge-motorway-trunk-2` |
| `motorwayCase` | `#e0b843` | 高速描边 | `road-/tunnel-/bridge-motorway-trunk(-2)-case` |
| `label` | `#3c4043` | 地名深灰 | 15 个标签层的 `text-color` |
| `labelHalo` | `#ffffff` | 白描边 | 同上 `text-halo-color` + `text-halo-width: 1.5` |

**改哪个 token 会改哪一层**：想换陆地底色只改 `land`；想换路网亮度改 `roadMinor`/`roadCase`
（两者对比决定道路是否能从底色里“浮”出来）；标签可读性由 `label` + `labelHalo` 决定，
测试用 `contrastRatio()` 断言 ≥ 4.5:1。

标签层清单（`LABEL_LAYERS`）：`road-label`、`poi-label`、`country-label`、`state-label`、
`settlement-major-label`、`settlement-minor-label`、`settlement-subdivision-label`、
`water-line-label`、`water-point-label`、`natural-line-label`、`natural-point-label`、
`waterway-label`、`transit-label`、`airport-label`、`continent-label`。
（注：issue 里写的 `place-label*` 在 `streets-v12` 中实际叫 `settlement-*-label` / `state-label`。）

## 刻意不动的东西

- `line-width`、`line-opacity`、`fill-opacity`：这些属性带 zoom 表达式，决定道路/建筑在哪个
  缩放级别出现。改 `building` 的 `fill-opacity` 会让 z16 以下建筑消失或出现。
- `road-number-shield` 的 `text-color`：盾牌本身是彩色底 + 白字，改深灰会读不出来。
- 应用自己的数据层（`clusters`、`unclustered-points`、`station-price-labels`、`range-circle`、
  `lowest-price`）：品牌色/价格梯度/halo 全是应用配色，底图改动不碰它们。

## POI 起显示级别

Mapbox streets 默认 `poi-label` 在 **z14** 才出现，而本应用默认缩放在 10–12，等于看不到 POI。
所以 `applyBasemapPalette()` 里额外做：

```js
if (map.getLayer('poi-label')) map.setLayerZoomRange('poi-label', 12, 24); // POI_MIN_ZOOM / POI_MAX_ZOOM
```

`settlement-*` / `road-label` 的 zoom range 不动，地名在省级视图仍然可见。

## 健壮性

`applyBasemapPalette()` 对每条覆盖都先 `map.getLayer(id)` 守卫：Mapbox 以后改样式层 id 时，
该层保留 Mapbox 原色而不是抛错中断整个 `style.load`。返回值
`{ applied, skipped, missing }` 里 `missing` 列出本次样式里缺失的层 id，方便排查；
Playwright 断言会在关键层缺失时直接失败并指出 id。

调用点是 `map.on('style.load', ...)` 而不是 `load`：初次加载和任何 `setStyle()` 之后都会触发，
所以以后加主题切换也不会把配色弄丢。

## 性能基线

#109 的验收第 5 条（实测 `style.load` 耗时）原先只有一段临时手测数字，没有口径、
没有复现命令、也没有任何东西阻止以后变慢。本节的数字全部由
`scripts/measure_map_load.mjs` 产出，并镜像到 `js/map-style.js` 的
`STYLE_LOAD_BASELINE`（测试会校验两者一致）。

### 测量口径

| 指标 | 起点 | 终点 |
|---|---|---|
| `style.load` | `new mapboxgl.Map({...})` 之前一行 | 首个 `style.load` 事件（样式 JSON 解析完成、sources 建好、图层已加） |
| `idle` | 同上 | 首个 `idle` 事件（所有瓦片已就绪或已失败） |

三行数字，对应两个探针：

- **app**：应用自己那张图（`window.__qcBasemapStyleLoadMs` / `window.__qcBasemapIdleMs`，
  由 `js/map.js` 在 `new mapboxgl.Map()` 处开始计时记录）——访客真实体验到的数字，
  也是守卫测试比较的那一个。
- **injected `streets-v12` / `light-v11`**：在同一个已加载页面里另建一张离屏
  `1280×800` 的 mapboxgl 图（`mapboxgl.Map` → `style.load`/`idle`）。**只有这样才能公平
  对比两种样式**：同一浏览器、同一 token、同一视口、同一瓦片策略，唯一变量是样式 URL。

> 两列**不可横向对比**：`app` 是首屏那张图的真实代价，`injected` 是在应用图仍在渲染时
> 新建的离屏 map（每次重建 worker / sources，样式 JSON 又已在缓存里），绝对值天然不同。
> 要判断「换样式是否变慢」，只看 `injected` 里 `streets-v12` 与 `light-v11` 两列的差值——
> 它们除了样式 URL 以外条件完全一致。

### 实测数字（2026-10-20）

环境：容器内 headless Chromium 156（Playwright）、视口 1280×800、蒙特利尔
z10（探针）/ 应用默认视图、vite dev server、**5 次采样**；token 绑定 `qgu.io`
域名，因此 localhost 上矢量瓦片 403（`style.load` 不受影响，`idle` 只能是下界）。

| 探针 | `style.load` 中位数（范围） | `idle` 中位数（范围） |
|---|---|---|
| app（streets-v12 + 调色板） | **153 ms**（147–164） | n/a¹ |
| injected `streets-v12` | **512 ms**（506–519） | **1547 ms**（1527–1591） |
| injected `light-v11` | **498 ms**（468–505） | **1512 ms**（1484–1561） |

原始采样（ms，按跑次顺序，非排序）：

- app `style.load`：152 / 147 / 156 / 164 / 153
- injected `streets-v12`：514 / 512 / 512 / 506 / 519
- injected `light-v11`：498 / 468 / 502 / 505 / 483

**结论**：`streets-v12` 的样式解析比 `light-v11` 慢约 **3%**（中位数 512 vs 498 ms，
绝对差 14 ms），`idle` 慢约 2%（1547 vs 1512 ms）——134 层里的道路分级基本是
同一次样式解析内的常量成本，不是数量级回归。首屏真正的重量在**矢量瓦片**
（134 层会请求更多 tile），而本应用默认 z10–12 只出现少量 tile，未观察到回归。
应用页面本身的 `style.load` 稳定在 150 ms 上下，远在 Playwright 30 s 超时之内。

¹ 应用页的 `idle` 在 localhost 上不触发：token 的域名白名单让矢量瓦片/glyph 请求 403，
样式数据的加载状态永远不会「全部就绪」。所以要 idle 数字请看 `injected` 行；在有真实
瓦片的环境（生产域名）跑同一条命令即可得到 app 的 idle。

### 复现命令

```bash
npm run measure:map-load                 # 3 次采样，dev server 未起会自动拉起
npm run measure:map-load -- --runs 5     # 与上表同口径
npm run measure:map-load -- --json       # JSON（便于贴进 PR / 脚本比对）
BASE_URL=https://qgu.io/qc-gas/ npm run measure:map-load   # 生产环境（能拿到真 idle）
```

脚本把 dev server、Playwright Chromium、视口都拉齐，唯一前置是要有
`.env` 里的 `VITE_MAPBOX_ACCESS_TOKEN`（应用已经需要它）。

**dev server 生命周期**：若 5173 上已经有人在服务（`npm run dev` 手动起着），脚本直接复用、
退出时不动它；如果是脚本自己拉起的，退出时把整组进程结束掉（`SIGTERM`，5 s 内没退就
`SIGKILL`）——只 kill 掉 npm 的 PID 会留下孤儿 vite 占着 5173，下一次测量就会悄悄测到
旧代码（review 第 1 轮抓到的缺陷，回归用例见 `tests/measure-map-load.spec.js`）。

### 性能守卫（超预算即失败）

`js/map-style.js` 里的 `STYLE_LOAD_BUDGET_MS = 8000`，由
`tests/map-style.spec.js` 的 `basemap perf guard (live map)` 用例对应用页实测值断言：

```js
expect(measured, `basemap style.load took ${measured} ms, budget ${STYLE_LOAD_BUDGET_MS} ms ` +
  `(recorded baseline: median ${baseline.median} ms, range ${baseline.range.join('–')} ms)...`)
  .toBeLessThanOrEqual(STYLE_LOAD_BUDGET_MS);
```

失败信息会直接打印**实测值 / 预算 / 记录的基线**，不用去翻日志找数字。

预算取 **8 s**：它既满足「≥ 中位数 × 3」（8 s ≈ app 基线中位数的 50 倍），又留足 CI 抖动余量——
守卫的目的是抓「有人把底图搞重了 10 倍」，不是管 CI 抖动（忙碌 runner + 冷 dev server
轻松吃掉 1 s，而会 flake 的守卫最终只会被静音，静音等于没有）。要放宽预算，必须先用
`npm run measure:map-load` 重测并把结果同时更新到 `STYLE_LOAD_BASELINE` 与本节；单测
`basemap perf baseline (unit)` 会检查「预算 ≥ 3× 记录中位数」「记录的样本 ≥ 3 个且
中位数/范围自洽」，所以文档、常量、守卫三者不会悄悄漂移。

如果想再省一点：`landcover`、`hillshade` 是纯装饰层，可以
`setLayoutProperty(id, 'visibility', 'none')`，但会牺牲“一眼能定位”的地形感，本 issue 未做。

## 测试

`tests/map-style.spec.js`（issue #125 把「断言」与「截图」彻底解耦：断言不碰瓦片，截图永不失败）：

1. **单元**（node 侧直接 import `js/map-style.js`）
   - `BASEMAP_STYLE` 是 `streets-v12`；
   - 调色板全是合法 `#rrggbb`；`contrastRatio(label, land)` / `contrastRatio(label, roadMinor)` ≥ 4.5；
   - `applyBasemapPalette(fakeMap)` 只对存在的层调用 `setPaintProperty`，缺失层静默跳过不抛错；
   - `setLayerZoomRange('poi-label', 12, 24)` 被调用；
   - 覆盖项里不出现 `line-width` / `line-opacity` / `fill-opacity`。
2. **样式数据（离线，无网络、无瓦片、无浏览器）** — 严格断言跑在这里
   - 数据源是 `tests/fixtures/mapbox-streets-v12.json`：真实样式的一份裁剪快照（`id`/`type`/
     `source-layer`/`minzoom`，134 层约 14 KB），重抓方式见 `scripts/refresh-map-style-fixture.mjs`；
   - 层表里含 `source-layer === 'road'` 的层、`road-street` 存在、`name === 'Mapbox Streets'`、层数 > 100；
   - `applyBasemapPalette` 作用在真实层表上：`missing === []`（上游改名/删层会立刻失败）、
     `land.background-color` / `water.fill-color` / `road-street.line-color` / `national-park.fill-color` /
     `poi-label.text-color` 与 `BASEMAP_PALETTE` 一致；
   - `poi-label` 被抬到 `minzoom` 12。
3. **实时地图（真 Mapbox 样式，但仍不依赖瓦片）**
   - 同一批属性从运行中的 `window.__qcMap` 读取，用来抓“调色板没应用 / 样式被换回 light-v11 / 层序错”这类接线回归；
   - Mapbox 完全不可达（无 `window.__qcMap`）时用例 `test.skip` 并记录原因，因为离线组已经覆盖同样的属性
     （issue #125 验收 2）。
4. **性能守卫（issue #124）** — `basemap perf guard (live map)`
   - 读应用页的 `window.__qcGasMap.getBasemapStyleLoadMs()`（`js/map.js` 在 `new mapboxgl.Map()`
     处开始计时），断言 ≤ `STYLE_LOAD_BUDGET_MS`（8 s），失败信息打印实测值/预算/记录基线；
   - 配套单元组 `basemap perf baseline (unit)` 校验 `STYLE_LOAD_BASELINE` 自洽（≥3 样本、
     中位数与范围对得上、预算 ≥ 3× 中位数），防止文档/常量/守卫互相漂移；
   - Mapbox 完全不可达时与第 3 组一样 `test.skip` 并记录原因（环境问题，非回归）。
5. **截图（best-effort）**：`tests/screenshots/basemap-google-like.png`（蒙特利尔，z12，pitch 0），
   仅供人工比对，无像素断言。等待条件是状态而非时间（`isStyleLoaded()` + 至少一个 `sourcedata`/`idle`
   事件 + `areTilesLoaded()`），route 转发异常一律 `route.abort()` 并记账；瓦片没到位、或失败多过成功时
   用例 `test.skip` 并写入 `test.info().annotations`，**不会**把整轮流水线拖成 `needs_human`。

另一个 Node 侧用例文件 `tests/measure-map-load.spec.js`（无浏览器、无 Mapbox 流量）守着测量
脚本自身：`stopServer()` 必须结束整组进程（npm + vite 孙进程，见上）、不碰别人起的 server，
`urlVariants()` 覆盖容器里 `localhost` / `::1` / `127.0.0.1` 三种写法，`summarise()` 的
中位数/范围算法与 `STYLE_LOAD_BASELINE` 记录口径一致。

> **本地 token 限制**：仓库 `.env` 里的 Mapbox token 绑定了 `qgu.io` 域名，所以直接访问
> `localhost:5173` 时矢量瓦片返回 403（样式 JSON 正常 200，因此所有 `getStyle()`/`getPaintProperty()`
> 断言照常可跑）。只有截图用例需要真实瓦片，它用 Playwright 的 route 把生产环境的 `Referer`
> 补回去（仅测试代码，不影响应用）。因此集成断言刻意不依赖 `map.loaded()`，改用 `isStyleLoaded()`。

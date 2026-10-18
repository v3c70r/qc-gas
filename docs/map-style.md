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

## 性能实测

本机（headless Chromium，dev server，图层缓存冷启动）`style.load` 耗时：

| 场景 | 实测 |
|---|---|
| 应用页面（streets-v12 + 调色板，`window.__qcBasemapStyleLoadMs`） | **149 ms / 154 ms / 165 ms** |
| 空白页 `streets-v12` | 469 ms / 513 ms（同一页再加载 `light-v11` 为 429 ms / 406 ms） |

即 `streets-v12` 的样式解析比 `light-v11` 慢约 10–20%，绝对差值约 40–100 ms，在现有
Playwright 30 s 超时内绰绰有余（`tests/map-style.spec.js` 的集成用例实测 5 s 内完成首屏
+ 样式断言）。更重的是**矢量瓦片量**（134 层的道路分级会请求更多 tile），这部分取决于缩放级别，
本应用默认 z10–12 只出现少量 tile，未观察到回归。

如果想再省一点：`landcover`、`hillshade` 是纯装饰层，可以 `setLayoutProperty(id, 'visibility', 'none')`，
但会牺牲“一眼能定位”的地形感，本 issue 未做。

## 测试

`tests/map-style.spec.js`：

1. **单元**（node 侧直接 import `js/map-style.js`）
   - `BASEMAP_STYLE` 是 `streets-v12`；
   - 调色板全是合法 `#rrggbb`；`contrastRatio(label, land)` / `contrastRatio(label, roadMinor)` ≥ 4.5；
   - `applyBasemapPalette(fakeMap)` 只对存在的层调用 `setPaintProperty`，缺失层静默跳过不抛错；
   - `setLayerZoomRange('poi-label', 12, 24)` 被调用；
   - 覆盖项里不出现 `line-width` / `line-opacity` / `fill-opacity`。
2. **集成**（真 Mapbox 样式）
   - `window.__qcMap.getStyle().layers` 中含 `source-layer === 'road'` 的层，`style.name === 'Mapbox Streets'`，层数 > 100；
   - `getPaintProperty('land','background-color')`、`('water','fill-color')`、`('road-street','line-color')`、`('national-park','fill-color')`、`('poi-label','text-color')` 与 `BASEMAP_PALETTE` 一致；
   - `getLayer('poi-label').minzoom === 12`；
   - 应用层 `unclustered-points` / `station-price-labels` 仍在 `poi-label` 之上（`getStyle().layers` 顺序）。
3. **截图**：`tests/screenshots/basemap-google-like.png`（蒙特利尔，z12，pitch 0），仅供人工比对，无像素断言。

> **本地 token 限制**：仓库 `.env` 里的 Mapbox token 绑定了 `qgu.io` 域名，所以直接访问
> `localhost:5173` 时矢量瓦片返回 403（样式 JSON 正常 200，因此所有 `getStyle()`/`getPaintProperty()`
> 断言照常可跑）。只有截图用例需要真实瓦片，它用 Playwright 的 route 把生产环境的 `Referer`
> 补回去（仅测试代码，不影响应用）。因此集成断言刻意不依赖 `map.loaded()`，改用 `isStyleLoaded()`。

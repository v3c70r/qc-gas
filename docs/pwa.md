# PWA（可安装应用）结构

本项目已具备可安装 PWA 能力：主屏图标、离线应用外壳、诚实的离线提示，以及
**可感知并一键应用的新版本更新**（issue #45）。

## 关键文件

| 文件 | 作用 |
| --- | --- |
| `public/manifest.webmanifest` | Web App Manifest（name、short_name、start_url、scope、standalone、theme_color、192/512 + maskable 图标） |
| `public/icons/*.png` | 192 / 512 / maskable 512 / apple-touch 180 图标 |
| `public/sw.js` | 版本化 service worker（缓存名 `qc-gas-v2-2026-09-25`） |
| `js/pwa.js` | SW 注册 + 更新提示条、安装入口、iOS 引导、离线状态渲染 |
| `scripts/create_pwa_icons.mjs` | 零依赖 PNG 图标生成脚本（`npm run icons`） |

## 缓存策略（`public/sw.js`）

| 请求类型 | 策略 | 原因 |
| --- | --- | --- |
| **导航请求**（`mode === 'navigate'`、`destination === 'document'` 或 `Accept: text/html`） | **network-first**，失败时回退缓存的 `index.html`（附 `X-QCGas-From-Cache: 1`） | 部署后已安装用户必须拿到**新的 `index.html`**（及其新的哈希资源名），断网时仍能打开 |
| 哈希静态资源（`/assets/`、`/icons/`、`.css` / `.js` / 图片 / 字体） | cache-first（不可变），首次请求后写入运行时缓存 | 哈希名内容不会变，秒开且省流量 |
| 其他同源 GET | stale-while-revalidate | 先给缓存、后台刷新 |
| `data/*.json` | **network-first**（不变） | 价格快照必须最新；离线回退缓存并带 `X-QCGas-From-Cache: 1`，UI 据此显示「离线 · 最后同步 \<时间\>」 |

- `APP_SHELL` **只预缓存真正静态的文件**：manifest + 4 个图标。
  `'./'` 与 `'./index.html'` **不再预缓存**——它们每次发布都会变，改由导航请求实时刷新。
- 缓存名带版本（`qc-gas-v<N>-<date>`）：**改动 `sw.js` 时同步 bump `CACHE_VERSION`**；
  `activate` 会删除 `qc-gas-*` 的旧版本缓存（**不动其他来源的缓存**，例如 Mapbox 的瓦片缓存），
  然后 `clients.claim()`。
- `install` **不再自动 `skipWaiting()`**：新 worker 处于 waiting 状态，等用户在页面里确认。

## 更新策略（`js/pwa.js`）

1. `register('./sw.js')` 成功后立即 `registration.update()`；此外在 `load`、页面重新可见
   （`visibilitychange`）与 `focus` 时再检查一次，**60 分钟节流**，避免频繁请求。
2. `updatefound` → 新 worker `state === 'installed'` **且页面已有 controller**（即这是"更新"而非首次安装）
   → 显示**非阻塞**提示条 `#pwa-update`（`role="status"`、`aria-live="polite"`）：
   文案三语（`updateAvailable` / `updateReload` / `updateDismiss`），"Recharger" 按钮 ≥44px。
   首次安装（没有 controller）不显示提示条。
3. 点击「Recharger」→ `postMessage({ type: 'SKIP_WAITING' })` → SW 激活并 claim →
   页面在 `controllerchange` 时**恰好 reload 一次**（`reloadedForUpdate` 守卫防止刷新循环；
   用户未点击前的 `controllerchange` 不会 reload）。
4. 用户不点击也不会被打断：新 worker 会在所有页面关闭后自然接管，下次打开即是新版本。
5. 测试钩子 `window.__qcGasPwa`（`notifyUpdateAvailable` / `applyUpdate` / `checkForUpdate` /
   `setRegistration` / `isUpdateAvailable`）：dev 环境不注册 SW，用它也能验证提示条 UI 与刷新流程。

> 迁移说明：仍停留在 `qc-gas-v1`（cache-first）的用户，本次发布后浏览器检测到 `sw.js` 字节变化会安装新
> worker；等到没有旧页面占用时它自动激活，**下次打开应用**导航即为 network-first 的新外壳（此后每次发布都能第一时间拿到）。

## 图标生成

```bash
npm run icons
```

脚本使用 `node:zlib` 手写 PNG chunk（IHDR/IDAT/IEND），不引入任何依赖，重复运行字节一致。

## 安装与离线行为

- Service worker **仅在 `import.meta.env.PROD` 且 HTTPS 下注册**，因此 Playwright 的 Vite dev 测试不受影响。
- Chrome/Edge/Android 出现 `beforeinstallprompt` 时显示三语“安装应用”入口（44px、可关闭）；已处于 `display-mode: standalone` 时隐藏。
- iOS Safari 显示“分享 → 添加到主屏”指引（iOS 不派发 `beforeinstallprompt`）。
- 断网或数据来自缓存时，`#data-status` 显示“离线 · 最后同步 `<generated_at>`”，不会把缓存价格伪装成实时；恢复在线后自动恢复。

## 手动验证步骤（更新策略）

```bash
npm run build
npm run preview        # 用真实产物验证（dev 不注册 SW，只能验 UI 钩子）
```

1. 用 `npm run preview` 的地址打开页面，等待安装/接管完成后**硬刷新一次**，确认应用已被 SW 控制
   （DevTools → Application → Service Workers 显示 activated & controlled）。
2. 保持该标签页打开；修改任意前端文件（例如 `index.html` 里的一行文案）后重新 `npm run build`，
   让预览服务器继续提供新产物；在 DevTools 里点 **Update**（或让标签页重新可见）。
3. 期望：页面出现「Nouvelle version disponible / Recharger」提示条（非阻塞，地图仍可操作）；
   点击「Recharger」→ 页面只刷新一次，且看到新文案（DevTools → Cache Storage 里只剩最新 `qc-gas-v2-*`）。
4. 不做任何操作时页面**不会**自动刷新，也不会丢状态。
5. 断网验证：DevTools → Network → Offline，重新打开页面 → 仍显示缓存的 `index.html`，
   且状态栏为「离线 · 最后同步 \<时间\>」（缓存价格绝不标成实时）。
6. 首次安装验证：清空站点数据（DevTools → Application → Clear storage）后重新打开，
   安装完成时**不应**出现更新提示条（此时没有 controller）。

自动化覆盖见 `tests/app.spec.js` → `PWA update strategy (Issue #45)`
（真实 `public/sw.js` 在沙箱里跑路由断言 + `window.__qcGasPwa` 驱动的提示条/刷新流程）。

可安装性要求参考：https://web.dev/articles/install-criteria
更新最佳实践：https://web.dev/learn/pwa/update

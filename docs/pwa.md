# PWA（可安装应用）结构

本项目已具备可安装 PWA 能力：主屏图标、离线应用外壳、以及诚实的离线提示。

## 关键文件

| 文件 | 作用 |
| --- | --- |
| `public/manifest.webmanifest` | Web App Manifest（name、short_name、start_url、scope、standalone、theme_color、192/512 + maskable 图标） |
| `public/icons/*.png` | 192 / 512 / maskable 512 / apple-touch 180 图标 |
| `public/sw.js` | 版本化 service worker（缓存名 `qc-gas-v1`） |
| `js/pwa.js` | SW 注册、安装入口、iOS 引导、离线状态渲染 |
| `scripts/create_pwa_icons.mjs` | 零依赖 PNG 图标生成脚本（`npm run icons`） |

## 缓存策略

- **应用外壳**（`index.html`、`css/`、`js/` 模块、`icons/`、manifest）：cache-first，重复访问秒开。
- **`data/*.json`**：network-first——网络成功时更新缓存；失败时回退到上次缓存，并添加 `X-QCGas-From-Cache: 1` 响应头。
- **activate**：清理旧版本缓存；`skipWaiting` + `clientsClaim` 使更新尽快生效。

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

## 验证

```bash
npm run build
npm run test
```

可安装性要求参考：https://web.dev/articles/install-criteria

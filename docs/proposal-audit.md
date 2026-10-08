# Proposal audit（筛选器维护 / append-only）

<!-- 由 .agent/pm/screen.mjs 自动写入，请勿手工编辑。
     REJECT = 不要重复提案；SPLIT = 可按更小范围重提（必须遵循给出的拆分方案）。 -->

## REJECT — 不要重复提案

## SPLIT — 可按更小范围重提
- 2026-10-08 | #63 | 首屏列表改为「每区最优价」摘要（现在 13/18 个区域一个站都看不到） | 方向真；实测 `data/stations.json`（2026-10-05，2455 有效站）默认"regular 价升序前 30"= **Outaouais 24 / Laurentides 5 / Lanaudière 1**——只有 3/18 区可见，比提案说的还差 | 拆分方案见 #63 的关闭评论
- 2026-10-08 | #59 | 个人引用不再静默失联：收藏/关注改以坐标为主键（含一次性迁移与「找不到」提示） | 证据真实且可核对 —— `js/favorites.js:39-43` 确认 `stationId()` 主键是文本 `name|address|postal_code`（`coord:` 仅兜底），`js/watch.js:242` 确认 `if (!feature) continue; // station disappeared → auto-clean` 后只持久化 `kept`（静默清理），产品文档 §7 实测量化（30 天文本键流失 1.09%、44% 坐标仍在）；方向与"数据诚实"定位一致，且 watch 条目已存 `lng`/`lat`（ | 拆分方案见 #59 的关闭评论

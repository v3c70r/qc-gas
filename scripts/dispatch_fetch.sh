#!/usr/bin/env bash
# 外部触发器：用 GitHub `repository_dispatch` 唤醒数据工作流（issue #77）。
#
# 为什么需要它：GitHub Actions 的 `schedule` 会被平台节流，本仓库实测（`data/trigger-interval.json`）
# 无论 cron 写 15 分钟还是 60 分钟，实际间隔 p50 都在 2-9 小时，达不到 #76 的目标间隔
# （30 分钟）。外部免费 cron 服务或本机 crontab 每 30 分钟跑一次本脚本，`static.yml`
# 会立刻醒来并抓取：非 schedule 事件在闸门里是「无条件抓取」，不占计划预算。
#
# 用法（密钥只经环境变量传入，仓库里不存任何令牌）：
#   export GH_DISPATCH_TOKEN=...        # fine-grained PAT：仅 Contents: Read and write（contents:write）
#   export GH_DISPATCH_REPO=owner/repo  # 可选，默认从 origin remote 猜
#   scripts/dispatch_fetch.sh
#
# 退出码：0 = 已派发；2 = 缺少令牌（附最小权限说明）；其它 = curl 失败（网络/权限）。
set -uo pipefail

EVENT_TYPE="${GH_DISPATCH_EVENT_TYPE:-fetch-data}"
TOKEN="${GH_DISPATCH_TOKEN:-}"
REPO="${GH_DISPATCH_REPO:-}"

if [ -z "${TOKEN}" ]; then
  echo "dispatch_fetch.sh: GH_DISPATCH_TOKEN 未设置，未派发任何请求。" >&2
  echo "  fine-grained PAT 只需要这一个仓库的 Contents: Read and write（contents:write，dispatch 需要写权限）；" >&2
  echo "  不要给 Actions / Issues / Secrets 等任何其它 scope。令牌只经环境变量传入，永不提交进仓库。" >&2
  exit 2
fi

if [ -z "${REPO}" ]; then
  REPO="$(git config --get remote.origin.url 2>/dev/null \
    | sed -E 's#(git@|https://)github\.com[:/]##; s#\.git$##')"
fi

if [ -z "${REPO}" ] || [ "${REPO}" = "${REPO#*/}" ]; then
  echo "dispatch_fetch.sh: 无法确定仓库（GH_DISPATCH_REPO=owner/repo 未设置且 origin remote 不可用）。" >&2
  exit 3
fi

curl -fsS -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "Content-Type: application/json" \
  "https://api.github.com/repos/${REPO}/dispatches" \
  -d "{\"event_type\": \"${EVENT_TYPE}\"}" \
  >/dev/null

echo "dispatch_fetch.sh: 已向 ${REPO} 派发 ${EVENT_TYPE}"

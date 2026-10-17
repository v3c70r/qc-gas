#!/usr/bin/env bash
# 外部触发器：用 GitHub `repository_dispatch` 唤醒数据工作流（issue #77/#93）。
#
# 为什么需要它：GitHub Actions 的 `schedule` 会被平台节流，本仓库实测（`data/trigger-interval.json`）
# 无论 cron 写 15 分钟还是 60 分钟，实际间隔 p50 都在 2-9 小时，达不到目标间隔
# （忙时 20 分钟）。本机 crontab（`scripts/install_dispatch_cron.sh`）每 5 分钟跑一次本脚本，
# `static.yml` 会立刻醒来；**闸门仍按 #91 时刻表判定**（issue #93）：到点的 dispatch 才抓取
# 并占位，未到点的唤醒只花一次 checkout + 闸门，不下载、不提交、不部署。
#
# 用法（密钥只经环境变量传入，仓库里不存任何令牌）：
#   export GH_DISPATCH_TOKEN=...        # fine-grained PAT：仅 Contents: Read and write（contents:write）
#   export GH_DISPATCH_REPO=owner/repo  # 可选，默认从 origin remote 猜
#   scripts/dispatch_fetch.sh
#
# 退出码（外部 cron 服务只看得见这个）：
#   0 = 已派发
#   2 = 缺少令牌（附最小权限说明）
#   3 = 输入非法（仓库名不像 owner/repo，或事件类型含 [A-Za-z0-9_.-] 以外的字符）
#   4 = dispatch 请求失败（网络 / 令牌权限不足 / 仓库不存在）——**失败绝不报成功**，
#       否则配错 PAT 会变成静默 no-op，直到 12 小时后的实测报告才发现。
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

# 事件类型会被拼进 JSON body：带引号/空格的値会产生非法 JSON（或注入其它字段），
# 而外部 cron 只会把这个当成「请求过了」。所以先校验字符集，不合法就不发请求。
case "${EVENT_TYPE}" in
  ''|*[!A-Za-z0-9_.-]*)
    echo "dispatch_fetch.sh: 非法事件类型 '${EVENT_TYPE}'（GH_DISPATCH_EVENT_TYPE 只允许 [A-Za-z0-9_.-]）。" >&2
    exit 3
    ;;
esac

if ! curl -fsS -X POST \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Accept: application/vnd.github+json" \
  -H "X-GitHub-Api-Version: 2022-11-28" \
  -H "Content-Type: application/json" \
  "https://api.github.com/repos/${REPO}/dispatches" \
  -d "{\"event_type\": \"${EVENT_TYPE}\"}" \
  >/dev/null; then
  echo "dispatch_fetch.sh: dispatch 请求失败（网络/令牌权限/仓库名？），${REPO} 未收到 ${EVENT_TYPE}。" >&2
  echo "  检查 GH_DISPATCH_TOKEN 是否为该仓库的 Contents: Read and write（contents:write）。" >&2
  exit 4
fi

echo "dispatch_fetch.sh: 已向 ${REPO} 派发 ${EVENT_TYPE}"

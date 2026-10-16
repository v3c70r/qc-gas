#!/usr/bin/env bash
# 本机抓取唤醒者安装器（issue #93）。
#
# 每 5 分钟唤醒一次数据工作流（`repository_dispatch` → `.github/workflows/static.yml`），
# 由闸门 `scripts/should_fetch.py` 按 #91 的显式时刻表（`data/fetch-schedule.json` 的
# `timetable`）判定这一轮是否真抓：**未到点的唤醒只花一次 checkout + 闸门**，
# 不下载上游、不提交、不部署。时刻表改了也**零改动**生效（闸门读仓库里的文件）。
#
# 用法：
#   scripts/install_dispatch_cron.sh                # 安装（幂等：重复执行仍只有一行）
#   scripts/install_dispatch_cron.sh --dry-run      # 只打印将写入的 crontab 行
#   scripts/install_dispatch_cron.sh --remove       # 卸载（只移除带标记的行）
#
# 令牌处理（**不落盘进 crontab**）：
#   crontab 行里写的是 `GH_DISPATCH_TOKEN="$(gh auth token --user <owner>)"`，
#   令牌在每次唤醒时现场取。若 `gh` 在 cron 环境里取不到（无 keyring / 无 DBUS，
#   多账号），退化为读取一个 **600 权限** 的本地令牌文件
#   （`QC_GAS_TOKEN_FILE`，默认 `~/.config/qc-gas/dispatch-token`），行里变成
#   `$(cat <file>)`。两种方式的取舍：现场取令牌不会在磁盘上留第二份副本，但依赖
#   `gh` 登录态；令牌文件不依赖 keyring，代价是磁盘上多一份 600 文件（不进仓库、
#   不进 crontab）。两者都不可用就**非零退出**，绝不静默装一个不会工作的条目。
#
# 日志：`.agent/dispatch-cron.log`（`.gitignore` 已忽略 `.agent/**/*.log`），
# 每次唤醒追加 `[UTC 时间] exit=<退出码>` 与脚本输出；超过 `QC_GAS_LOG_MAX_BYTES`
# （默认 1 MiB）时轮转成 `.agent/dispatch-cron.log.1`（只保留一份，日志有界）。
#
# 退出码：
#   0 = 已安装 / 已卸载 / dry-run 成功
#   2 = 自检失败（crontab 不可用、取不到令牌、dispatch_fetch.sh 不可执行、仓库未知）
#   3 = crontab 命令本身失败
#   4 = 用法错误
set -uo pipefail

MARKER='# qc-gas-dispatch'
MARKER_TEXT='qc-gas-dispatch'
LOG_REL='.agent/dispatch-cron.log'
DISPATCH_REL='scripts/dispatch_fetch.sh'
DEFAULT_WAKE_MINUTES=5
DEFAULT_LOG_MAX_BYTES=1048576
DEFAULT_TOKEN_FILE="${XDG_CONFIG_HOME:-${HOME:-/root}/.config}/qc-gas/dispatch-token"

DRY_RUN=0
REMOVE=0
WAKE_MINUTES="${QC_GAS_WAKE_MINUTES:-$DEFAULT_WAKE_MINUTES}"
LOG_MAX_BYTES="${QC_GAS_LOG_MAX_BYTES:-$DEFAULT_LOG_MAX_BYTES}"
REPO_SLUG="${QC_GAS_DISPATCH_REPO:-}"
GH_USER="${QC_GAS_GH_USER:-}"
TOKEN_FILE="${QC_GAS_TOKEN_FILE:-$DEFAULT_TOKEN_FILE}"

usage() {
  sed -n '2,32p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

die() {
  printf 'install_dispatch_cron.sh: %s\n' "$1" >&2
  exit "${2:-2}"
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --remove) REMOVE=1 ;;
    --interval-minutes)
      shift; [ $# -gt 0 ] || die "--interval-minutes 需要一个值" 4
      WAKE_MINUTES="$1" ;;
    --repo)
      shift; [ $# -gt 0 ] || die "--repo 需要一个值" 4
      REPO_SLUG="$1" ;;
    --gh-user)
      shift; [ $# -gt 0 ] || die "--gh-user 需要一个值" 4
      GH_USER="$1" ;;
    --log-max-bytes)
      shift; [ $# -gt 0 ] || die "--log-max-bytes 需要一个值" 4
      LOG_MAX_BYTES="$1" ;;
    -h|--help) usage; exit 0 ;;
    *) die "未知参数：$1（--dry-run / --remove / --repo / --gh-user / --log-max-bytes）" 4 ;;
  esac
  shift
done

case "$WAKE_MINUTES" in
  ''|*[!0-9]*) die "--interval-minutes 必须是正整数（收到 '$WAKE_MINUTES'）" 4 ;;
esac
case "$LOG_MAX_BYTES" in
  ''|*[!0-9]*) die "--log-max-bytes 必须是正整数（收到 '$LOG_MAX_BYTES'）" 4 ;;
esac

# ── 自检（在写任何东西之前）──────────────────────────────────────────────────

CRONTAB_BIN="${QC_GAS_CRONTAB:-$(command -v crontab 2>/dev/null || true)}"
if [ -z "$CRONTAB_BIN" ] || [ ! -x "$CRONTAB_BIN" ]; then
  die "crontab 不可用（PATH 里没有，QC_GAS_CRONTAB 也没有指向可执行文件）：装不了唤醒者。先用系统包管理器装上 cron（含 crontab/cronie），或显式指定 QC_GAS_CRONTAB=/path/to/crontab。" 2
fi

REPO_ROOT="${QC_GAS_REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
if [ ! -x "$REPO_ROOT/$DISPATCH_REL" ]; then
  if [ -f "$REPO_ROOT/$DISPATCH_REL" ]; then
    die "$REPO_ROOT/$DISPATCH_REL 没有执行权限（cron 里会直接失败）：先 chmod +x 再安装。" 2
  fi
  die "找不到 $REPO_ROOT/$DISPATCH_REL（QC_GAS_REPO 指向的目录不对？）。" 2
fi

if [ -z "$REPO_SLUG" ]; then
  REPO_SLUG="$(git -C "$REPO_ROOT" config --get remote.origin.url 2>/dev/null \
    | sed -E 's#(git@|https://)github\.com[:/]##; s#\.git$##' || true)"
fi
case "$REPO_SLUG" in
  */*) ;;
  *) die "无法确定仓库 owner/name（origin remote 不是 GitHub 或未设置）：用 --repo owner/name 或 QC_GAS_DISPATCH_REPO 指定。" 2 ;;
esac
[ -n "$GH_USER" ] || GH_USER="${REPO_SLUG%%/*}"

# 令牌：优先现场取（不落盘），其次 600 权限的本地文件，都没有就自检失败。
GH_BIN="${QC_GAS_GH:-$(command -v gh 2>/dev/null || true)}"
GH_BIN="${GH_BIN:-gh}"
TOKEN_CMD=''
if [ -x "$GH_BIN" ] && "$GH_BIN" auth token --user "$GH_USER" >/dev/null 2>&1; then
  TOKEN_CMD="$GH_BIN auth token --user $GH_USER"
  TOKEN_SOURCE="gh auth token（现场取，不落盘）"
elif [ -f "$TOKEN_FILE" ]; then
  TOKEN_MODE="$(stat -c '%a' "$TOKEN_FILE" 2>/dev/null \
    || stat -f '%Lp' "$TOKEN_FILE" 2>/dev/null || true)"
  [ "$TOKEN_MODE" = '600' ] || die "本地令牌文件 $TOKEN_FILE 的权限是 ${TOKEN_MODE:-未知}，必须是 600（chmod 600 '$TOKEN_FILE'）：否则同一台机器上的其它用户能读到 dispatch 令牌。" 2
  TOKEN_CMD="cat $TOKEN_FILE"
  TOKEN_SOURCE="本地令牌文件 $TOKEN_FILE（600）"
else
  die "取不到 dispatch 令牌：'$GH_BIN auth token --user $GH_USER' 失败，且本地令牌文件 $TOKEN_FILE 不存在。二选一：用 'gh auth login' 登录（cron 环境需要 keyring 可用），或把 fine-grained PAT（仅本仓库 Contents: Read and write）写进 600 权限的 $TOKEN_FILE。" 2
fi

ROTATE="[ ! -s $LOG_REL ] || [ \"\$(wc -c < $LOG_REL)\" -lt $LOG_MAX_BYTES ] || mv -f $LOG_REL $LOG_REL.1"

LINE="$(printf '*/%s * * * * cd %s && mkdir -p .agent && { %s ; } && GH_DISPATCH_REPO=%s GH_DISPATCH_TOKEN="$(%s)" %s >> %s 2>&1 ; rc=$? ; echo "[$(date -u -Iseconds)] exit=$rc (%s)" >> %s %s' \
  "$WAKE_MINUTES" "$REPO_ROOT" "$ROTATE" "$REPO_SLUG" "$TOKEN_CMD" \
  "$DISPATCH_REL" "$LOG_REL" "$MARKER_TEXT" "$LOG_REL" "$MARKER")"

if [ "$DRY_RUN" = 1 ]; then
  printf 'install_dispatch_cron.sh: dry-run，未修改 crontab。令牌来源：%s\n' "$TOKEN_SOURCE" >&2
  printf '%s\n' "$LINE"
  exit 0
fi

# ── 读现有 crontab，剥掉已有的标记行 ─────────────────────────────────────────

current="$("$CRONTAB_BIN" -l 2>/dev/null || true)"
filtered="$(printf '%s\n' "$current" | grep -v -F -- "$MARKER" || true)"

if [ "$REMOVE" = 1 ]; then
  if [ -z "$(printf '%s' "$filtered" | tr -d '[:space:]')" ] \
     && [ "$(printf '%s' "$current" | grep -c -F -- "$MARKER" || true)" = '0' ]; then
    printf 'install_dispatch_cron.sh: crontab 里没有 %s 标记的行，无需卸载。\n' "$MARKER"
    exit 0
  fi
  if ! printf '%s\n' "$filtered" | "$CRONTAB_BIN" -; then
    die "卸载失败：crontab 命令返回非零（crontab 文件未被改动？）。" 3
  fi
  printf 'install_dispatch_cron.sh: 已移除带 %s 标记的行。\n' "$MARKER"
  exit 0
fi

if [ -n "$(printf '%s' "$filtered" | tr -d '[:space:]')" ]; then
  content="$(printf '%s\n%s' "$filtered" "$LINE")"
else
  content="$LINE"
fi
if ! printf '%s\n' "$content" | "$CRONTAB_BIN" -; then
  die "安装失败：crontab 命令返回非零，原有 crontab 未被改动。" 3
fi

printf 'install_dispatch_cron.sh: 已安装（每 %s 分钟唤醒一次）。\n' "$WAKE_MINUTES"
printf '  行：%s\n' "$LINE"
printf '  令牌：%s\n' "$TOKEN_SOURCE"
printf '  日志：%s/%s（超过 %s 字节轮转为 .1）\n' "$REPO_ROOT" "$LOG_REL" "$LOG_MAX_BYTES"
printf '  卸载：%s --remove\n' "$0"

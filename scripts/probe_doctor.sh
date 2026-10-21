#!/usr/bin/env bash
#
# 探针体检 / 一次性自愈（issue #121）。
#
# 2026-10-19 的探针克隆停在半合并状态：7 个 `UU`（both modified）文件、
# `data/history/station-history.json` 本地 4.2 MB vs master 1.40 MB（多日 history
# stage 重复追加导致膨胀），于是之后每轮都撞
# `error: Pulling is not possible because you have unmerged files`（日志 40 次）。
# 当时是人工 `git reset --hard origin/master` 恢复的 —— 本文把这个动作脚本化，
# 且**默认只体检不改动**。
#
# 用法：
#   bash scripts/probe_doctor.sh                 # 只体检（0 = 健康，1 = 有问题）
#   bash scripts/probe_doctor.sh --fix           # 体检并修复
#   bash scripts/probe_doctor.sh --fix --dry-run # 只打印将执行的修复命令
#   bash scripts/probe_doctor.sh --base-branch master --bloat-factor 2
#
# 检查项（每项都会打印证据）：
#   1. 当前分支 ≠ `PROBE_BASE_BRANCH`（探针会以退出码 2 拒绝运行）；
#   2. 未合并文件（`git diff --name-only --diff-filter=U`）—— 也就是 `UU`；
#   3. 半合并/半 rebase 残留（rebase 目录、`MERGE_HEAD`、`CHERRY_PICK_HEAD`、
#      `REVERT_HEAD`），包括「没有 rebase 在进行，但索引里还有未合并条目」；
#   4. 本地 tracked 数据文件与远端偏离过大（默认 > 远端 2 倍且差值 > 8 KiB）：
#      探针每轮改写 tracked 文件却从不提交，多日重复追加就会膨胀到这个量级。
#
# 修复（`--fix`）只有一条安全路径：先把半合并操作 abort 掉，然后
# `git fetch origin` + `git reset --hard <upstream>`（远端权威版本）。
# 只有当**所有**脏文件都在 `data/` 里（探针自己的足迹，issue #92）时才允许，
# 否则以退出码 2 拒绝并列出越界的路径 —— 绝不抹掉 agent 的在途改动。
#
# 退出码：0 = 健康 / 修复后健康；1 = 发现（且未修复的）问题；2 = 拒绝修复或不是
# git 仓库；4 = 用法错误。日志：`.agent/probe.log` 里不属于本文档范围 —— 本脚本
# 只往 stdout/stderr 写，不动探针日志。
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_BRANCH="${PROBE_BASE_BRANCH:-master}"
BLOAT_FACTOR="${PROBE_BLOAT_FACTOR:-2}"
BLOAT_MIN_BYTES="${PROBE_BLOAT_MIN_BYTES:-8192}"
FIX=0
DRY_RUN=0

usage() {
  cat <<'USAGE'
用法: bash scripts/probe_doctor.sh [--fix [--dry-run]] [--base-branch <name>] [--bloat-factor <n>]

  --fix                 修复发现的问题（unmerged / 半合并 / 数据文件膨胀）
  --dry-run             --fix 的预演：只打印将执行的 git 命令
  --base-branch <name>  探针应处的分支（默认 $PROBE_BASE_BRANCH 或 master）
  --bloat-factor <n>    本地 tracked 数据文件超过远端 n 倍即判膨胀（默认 2）
  -h, --help            显示本帮助
USAGE
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --fix) FIX=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --base-branch) shift; BASE_BRANCH="${1:-$BASE_BRANCH}" ;;
    --base-branch=*) BASE_BRANCH="${1#*=}" ;;
    --bloat-factor) shift; BLOAT_FACTOR="${1:-$BLOAT_FACTOR}" ;;
    --bloat-factor=*) BLOAT_FACTOR="${1#*=}" ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'probe_doctor: 未知参数 %s\n' "$1" >&2; usage >&2; exit 4 ;;
  esac
  shift
done

case "$BLOAT_FACTOR" in
  ''|*[!0-9]*) printf 'probe_doctor: --bloat-factor 必须是正整数，收到 %s\n' "$BLOAT_FACTOR" >&2; exit 4 ;;
esac

git_repo() { git -C "$REPO_ROOT" "$@"; }

if ! git_repo rev-parse --git-dir >/dev/null 2>&1; then
  printf 'probe_doctor: %s 不是 git 仓库\n' "$REPO_ROOT" >&2
  exit 2
fi

PROBLEMS=0
problem() { printf '  ! %s\n' "$*"; PROBLEMS=$((PROBLEMS + 1)); }
note() { printf '  · %s\n' "$*"; }

REPO_ROOT="$(git_repo rev-parse --show-toplevel 2>/dev/null || printf '%s' "$REPO_ROOT")"
printf '探针体检：%s\n' "$REPO_ROOT"

# ── 1. 分支 ─────────────────────────────────────────────────────────────────
branch="$(git_repo symbolic-ref --quiet --short HEAD 2>/dev/null || true)"
upstream=''
if [ -n "$branch" ]; then
  upstream="$(git_repo rev-parse --abbrev-ref --symbolic-full-name "$branch@{u}" 2>/dev/null || true)"
fi
if [ -z "$upstream" ]; then
  upstream="origin/$BASE_BRANCH"
  git_repo rev-parse --verify -q "$upstream" >/dev/null 2>&1 || upstream=''
fi

if [ "$branch" != "$BASE_BRANCH" ]; then
  problem "当前分支 '${branch:-detached HEAD}' ≠ '$BASE_BRANCH'：探针会以退出码 2 拒绝运行（PROBE_BASE_BRANCH）"
else
  note "分支 $branch（探针允许的分支）"
fi
if [ -z "$upstream" ]; then
  problem "找不到远端上游（'$BASE_BRANCH@{u}' / 'origin/$BASE_BRANCH'）：无法判断偏移，先 git fetch origin"
else
  note "上游 $upstream（$(git_repo rev-parse --short "$upstream" 2>/dev/null)）"
fi

# ── 2. 半合并 / 半 rebase 状态 ──────────────────────────────────────────────
rebase_dir="$(git_repo rev-parse --git-path rebase-merge 2>/dev/null)"
rebase_apply="$(git_repo rev-parse --git-path rebase-apply 2>/dev/null)"
merge_head="$(git_repo rev-parse --git-path MERGE_HEAD 2>/dev/null)"
cherry_pick="$(git_repo rev-parse --git-path CHERRY_PICK_HEAD 2>/dev/null)"
revert_head="$(git_repo rev-parse --git-path REVERT_HEAD 2>/dev/null)"

HALF_MERGE=0
[ -d "$rebase_dir" ] && { HALF_MERGE=1; problem "rebase 未完成（$rebase_dir 存在）：每轮 pull 都会被拒"; }
[ -d "$rebase_apply" ] && { HALF_MERGE=1; problem "git am/rebase --apply 未完成（$rebase_apply 存在）"; }
[ -n "$merge_head" ] && [ -f "$merge_head" ] && { HALF_MERGE=1; problem "MERGE_HEAD 残留：处于半合并状态（未 commit 的 merge）"; }
[ -n "$cherry_pick" ] && [ -f "$cherry_pick" ] && { HALF_MERGE=1; problem "CHERRY_PICK_HEAD 残留"; }
[ -n "$revert_head" ] && [ -f "$revert_head" ] && { HALF_MERGE=1; problem "REVERT_HEAD 残留"; }

# ── 3. 未合并文件（`UU`），包括「没有 rebase 在进行」的残留索引 ──────────────
UNMERGED="$(git_repo diff --name-only --diff-filter=U 2>/dev/null || true)"
UNMERGED_COUNT=0
if [ -n "$UNMERGED" ]; then
  UNMERGED_COUNT="$(printf '%s\n' "$UNMERGED" | wc -l | tr -d ' ')"
  problem "$UNMERGED_COUNT 个未合并文件（UU）：$(printf '%s' "$UNMERGED" | tr '\n' ' ')"
  [ "$HALF_MERGE" -eq 0 ] && problem "索引里有未合并条目但没有 rebase/merge 在进行 → 残留索引，pull 会被永久拒绝"
  OUTSIDE="$(printf '%s\n' "$UNMERGED" | grep -v '^data/' || true)"
  [ -n "$OUTSIDE" ] && problem "未合并文件里有 data/ 之外的路径（不是探针的足迹）：$(printf '%s' "$OUTSIDE" | tr '\n' ' ')"
fi

# ── 4. tracked 数据文件膨胀 ────────────────────────────────────────────────
BLOATED=''
if [ -n "$upstream" ]; then
  while IFS= read -r path; do
    [ -f "$REPO_ROOT/$path" ] || continue
    local_bytes="$(wc -c <"$REPO_ROOT/$path" 2>/dev/null | tr -d ' ')"
    remote_bytes="$(git_repo cat-file -s "$upstream:$path" 2>/dev/null || true)"
    case "${local_bytes:-}" in ''|*[!0-9]*) continue ;; esac
    case "${remote_bytes:-}" in ''|*[!0-9]*) continue ;; esac
    [ "$remote_bytes" -gt 0 ] || continue
    [ "$((local_bytes - remote_bytes))" -gt "$BLOAT_MIN_BYTES" ] || continue
    if [ "$((local_bytes / remote_bytes))" -ge "$BLOAT_FACTOR" ]; then
      ratio=$((local_bytes / remote_bytes))
      problem "数据文件膨胀：$path 本地 ${local_bytes} B vs $upstream ${remote_bytes} B（${ratio}×）"
      BLOATED="$BLOATED $path"
    fi
  done <<<"$(git_repo ls-tree -r --name-only "$upstream" -- data 2>/dev/null)"
fi
if [ "$PROBLEMS" -eq 0 ]; then
  printf '探针体检：未发现问题（branch=%s, upstream=%s）\n' "$branch" "${upstream:--}"
  exit 0
fi

printf '发现 %s 处问题\n' "$PROBLEMS"

# ── 修复 ───────────────────────────────────────────────────────────────────
if [ "$FIX" -ne 1 ]; then
  printf '建议（未执行；加 --fix 生效）：\n'
  printf '  git -C %s fetch origin && git -C %s reset --hard %s\n' \
    "$(printf '%q' "$REPO_ROOT")" "$(printf '%q' "$REPO_ROOT")" "${upstream:-origin/$BASE_BRANCH}"
  printf '  # 或直接：bash scripts/probe_doctor.sh --fix\n'
  exit 1
fi

if [ "$branch" != "$BASE_BRANCH" ]; then
  printf '  × refusing to fix: 当前分支不是 %s（先切回探针分支，别在这里 reset）\n' "$BASE_BRANCH"
  exit 2
fi
if [ -z "$upstream" ]; then
  printf '  × refusing to fix: 没有上游，无法取远端权威版本（先 git fetch origin）\n'
  exit 2
fi

# `reset --hard` 只能丢探针自己的足迹（data/）；出现别的脏文件就拒绝（issue #92）。
DIRTY="$( { git_repo diff --name-only 2>/dev/null; git_repo diff --cached --name-only 2>/dev/null; } | sort -u )"
OUTSIDE_DIRTY="$(printf '%s\n' "$DIRTY" | grep -v '^data/' | grep -v '^$' || true)"
if [ -n "$OUTSIDE_DIRTY" ]; then
  printf '  × refusing to fix: 工作树里有 data/ 之外的改动，不会被 reset --hard 抹掉：\n'
  printf '    %s\n' $(printf '%s\n' "$OUTSIDE_DIRTY")
  exit 2
fi

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    printf '  + %s\n' "$*"
    return 0
  fi
  printf '  + %s\n' "$*"
  git_repo "$@" || printf '    (rc=%s，继续)\n' "$?"
}

printf '修复计划：\n'
[ -d "$rebase_dir" ] && run rebase --abort
[ -d "$rebase_apply" ] && run rebase --abort
[ -n "$merge_head" ] && [ -f "$merge_head" ] && run merge --abort
[ -n "$cherry_pick" ] && [ -f "$cherry_pick" ] && run cherry-pick --abort
[ -n "$revert_head" ] && [ -f "$revert_head" ] && run revert --abort
run fetch origin
run reset --hard "$upstream"

if [ "$DRY_RUN" -eq 1 ]; then
  printf 'dry-run：未执行任何命令（问题仍在，退出码 1）\n'
  exit 1
fi

if [ "$UNMERGED_COUNT" -gt 0 ] || [ -n "$BLOATED" ]; then
  printf '已丢弃本地 %s 个未合并/膨胀文件，取远端 %s 版本\n' \
    "$((UNMERGED_COUNT + $(printf '%s' "$BLOATED" | wc -w | tr -d ' ')))" "$upstream"
fi

# 复检：修复后必须干净。
REMAINING=0
[ -n "$(git_repo diff --name-only --diff-filter=U 2>/dev/null)" ] && REMAINING=$((REMAINING + 1))
[ -n "$(git_repo rev-parse --verify -q MERGE_HEAD 2>/dev/null)" ] && REMAINING=$((REMAINING + 1))
[ -d "$rebase_dir" ] && REMAINING=$((REMAINING + 1))
if [ "$REMAINING" -eq 0 ]; then
  printf '探针体检：未发现问题（已修复，branch=%s, upstream=%s）\n' "$branch" "$upstream"
  exit 0
fi
printf '修复后仍有 %s 处问题，请人工检查（.agent/probe.log）\n' "$REMAINING"
exit 1

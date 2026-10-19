#!/usr/bin/env bash
#
# Local high-frequency fetch probe (issue #74) — tmux-resident, *not* a fork of
# the Actions pipeline.
#
# Why: the GitHub Actions schedule is throttled far beyond its `*/15` cron (37
# observed gaps: median 293 min, max 538 min), so the committed data can freeze
# for hours. This loop runs the **same** committed scripts from a laptop every
# ~15 minutes, bypassing the Actions gate on purpose, to produce a ground-truth
# series of achievable intervals for the "necessary interval" evaluation (#76).
#
# It is intentionally thin:
#   download_data.py -> process_data.py -> update_profile.py ->
#   append_history.py -> metrics.py   (the same data chain update-data.yml runs)
#   + a report-only check_data_size.py, then fetch_log.py records the completed
#   cycle in data/fetch-log.json. `fetch_bulletin.py` (weekly PDF, unrelated to
#   high-frequency probing) is deliberately skipped, as is `should_fetch.py`.
#   `append_history.py` is **skipped by default** since #113: Actions already
#   appends `data/history/**` every hour, and a second writer of the same tracked
#   files made `pull --rebase` conflict on ~1 cycle in 4 (see PROBE_SKIP_STAGES).
#
# Safety:
#   * the loop refuses to run on any branch but `PROBE_BASE_BRANCH` (default
#     `master`): it must live in its **own worktree/clone** (`git worktree add`),
#     never in the same working tree where an agent edits a feature branch —
#     otherwise its `git add`/`reset --hard` would swallow somebody else's work;
#   * a failing stage never stops the loop (no `set -e`, no `should_fetch.py`);
#   * a pull/rebase conflict is **resolved**, not aborted (#113): the remote
#     (Actions) version wins for every `data/` path the probe does not own, the
#     rebase continues, and the cycle's ledger commit still goes through; only
#     when the rebase cannot be completed is the local state discarded;
#   * the push is a bounded `pull --rebase` retry (default 3) so the Actions
#     data commits win — a failed rebase aborts and resyncs to the remote
#     instead of overwriting it;
#   * `git reset --hard '@{u}'` runs only when every dirty path is inside
#     `data/` (the probe's own footprint); anything else is left untouched;
#   * samples stay out of the repository (issue #92): the snapshots are written
#     to disk as before but only `data/update-profile.json`, `data/fetch-log.json`
#     and `data/fetch-schedule.json` are ever committed, at most once per hour,
#     and only when they actually changed. The per-cycle truth goes to the
#     gitignored `data/probe-local/events.jsonl` instead;
#   * everything is appended to `.agent/probe.log` (gitignored).
#
# Env knobs (all optional):
#   PROBE_BASE_BRANCH        the branch the probe may run on, default `master`
#   PROBE_INTERVAL_SECONDS   cycle cadence, default 900 (15 min)
#   PROBE_COMMIT_INTERVAL_SECONDS  max one data commit per N seconds, default 3600
#   PROBE_MAX_PUSH_RETRIES   rebase+push retries, default 3
#   PROBE_MAX_CYCLES         stop after N cycles (0 = forever; 1 = smoke test)
#   PROBE_SKIP_STAGES        comma-separated stages to skip, default `history`
#                            (set it to an empty value to run every stage)
#   PROBE_SKIP_HISTORY       non-empty: shorthand for adding `history` to the
#                            skip list (kept for the container's default, #113)
#   PROBE_SYNC_ATTEMPTS      bounded sync attempts per cycle, default 2
#   PROBE_SKIP_PUSH          non-empty: commit locally but never push
#   PROBE_SKIP_SUMMARY       non-empty: never comment the daily health summary
#   PROBE_TRACKING_ISSUE     issue number for the daily summary, default 78
#   PROBE_EVENTS_FILE        local event log, default data/probe-local/events.jsonl
#   PROBE_LOG_FILE           default .agent/probe.log
#   PROBE_PYTHON             default python3
#   PROBE_CHECK_ONLY         non-empty: run the startup checks and exit (tests)
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || { echo "cannot cd to $REPO_ROOT" >&2; exit 1; }

BASE_BRANCH="${PROBE_BASE_BRANCH:-master}"
INTERVAL_SECONDS="${PROBE_INTERVAL_SECONDS:-900}"
COMMIT_INTERVAL_SECONDS="${PROBE_COMMIT_INTERVAL_SECONDS:-3600}"
MAX_PUSH_RETRIES="${PROBE_MAX_PUSH_RETRIES:-3}"
MAX_CYCLES="${PROBE_MAX_CYCLES:-0}"
TRACKING_ISSUE="${PROBE_TRACKING_ISSUE:-78}"
LOG_FILE="${PROBE_LOG_FILE:-$REPO_ROOT/.agent/probe.log}"
RETRY_BACKOFF_SECONDS="${PROBE_RETRY_BACKOFF_SECONDS:-15}"
PYTHON="${PROBE_PYTHON:-python3}"
EVENTS_FILE="${PROBE_EVENTS_FILE:-$REPO_ROOT/data/probe-local/events.jsonl}"
COMMIT_STATE_FILE="$REPO_ROOT/data/probe-local/last-commit.epoch"
COMMIT_MESSAGE='Update gas prices data (local probe) [skip ci]'
# The **only** files the probe may commit (issue #92): the ledger/schedule
# bookkeeping are tiny, the snapshots are not — they stay local.
COMMITTED_FILES=(data/update-profile.json data/fetch-log.json data/fetch-schedule.json)

# Stage skips (issue #113). `history` is skipped by default: `append_history.py`
# rewrites the *tracked* `data/history/**` that Actions owns (`update-data.yml`
# appends it on every admitted run), so a second writer made `pull --rebase`
# collide with the Actions commit on ~25 % of the cycles — `stage history failed`
# (`JSONDecodeError` on a half-updated tree) and, worse, the following cycle died
# on `error: Pulling is not possible because you have unmerged files`. The probe's
# unique value is the 15-minute observation (`data/probe-local/events.jsonl`) plus
# the latency/freshness metrics, not a second history writer. Set
# `PROBE_SKIP_STAGES=` (empty) to run every stage again; comma-separated list
# accepts any stage label (`download`, `process`, `profile`, `probe-events`,
# `history`, `metrics`, `size-budget`).
SKIP_STAGES="${PROBE_SKIP_STAGES-history}"
if [ -n "${PROBE_SKIP_HISTORY:-}" ]; then
  case ",$SKIP_STAGES," in
    *',history,'*) ;;
    *) SKIP_STAGES="${SKIP_STAGES:+$SKIP_STAGES,}history" ;;
  esac
fi
SYNC_ATTEMPTS="${PROBE_SYNC_ATTEMPTS:-2}"
SYNC_BACKOFF_SECONDS="${PROBE_SYNC_BACKOFF_SECONDS:-15}"

# Push retries spent by the cycle that just finished are folded into the *next*
# cycle's ledger entry (the entry has to be written before the commit it rides
# in, so the number cannot be known yet).
PENDING_RETRIES=0
PUSH_ATTEMPTS=0

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$(dirname "$EVENTS_FILE")"
touch "$LOG_FILE"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG_FILE"; }

# ── Startup guard (issue #92 addendum) ───────────────────────────────────────

# The branch this working tree is on (empty for a detached HEAD).
current_branch() { git symbolic-ref --quiet --short HEAD 2>/dev/null || true; }

# Refuse to touch a working tree that is not on the baseline branch. Running on
# a feature branch (e.g. an agent's `agent/92`) is how the probe's data commit
# landed on the wrong branch and how its `git reset --hard` was one pull away
# from deleting an agent's uncommitted work.
require_base_branch() {
  local branch
  branch="$(current_branch)"
  if [ "$branch" != "$BASE_BRANCH" ]; then
    log "refusing to run on branch '${branch:-detached HEAD}': the probe may only run on '$BASE_BRANCH' (PROBE_BASE_BRANCH). Create a dedicated worktree: git worktree add ../qc-gas-probe '$BASE_BRANCH'"
    echo "probe: refusing to run on branch '${branch:-detached HEAD}' (expected '$BASE_BRANCH', see PROBE_BASE_BRANCH); use a dedicated worktree: git worktree add ../qc-gas-probe '$BASE_BRANCH'" >&2
    return 1
  fi
  return 0
}

require_base_branch || exit 2
log "probe stages skipped: '${SKIP_STAGES}' (default history: Actions owns data/history/; #113)"
if [ -n "${PROBE_CHECK_ONLY:-}" ]; then
  log "PROBE_CHECK_ONLY set; startup checks passed on '$BASE_BRANCH'"
  echo "probe: startup checks passed on '$BASE_BRANCH'"
  exit 0
fi

# Run a stage, appending its output to the log as evidence.
# `fatal=1` propagates the exit code (caller marks the cycle failed); a
# report-only stage (`fatal=0`) always returns 0 so it can never stop the probe.
_stage() {
  local label="$1" fatal="$2"
  shift 2
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ -n "$out" ]; then
    printf '%s\n' "$out" >>"$LOG_FILE"
  fi
  if [ "$rc" -ne 0 ] && [ "$fatal" -eq 1 ]; then
    log "stage $label failed (rc=$rc)"
  elif [ "$rc" -ne 0 ]; then
    log "stage $label warned (rc=$rc); continuing"
  else
    log "stage $label ok"
  fi
  if [ "$fatal" -eq 1 ]; then
    return "$rc"
  fi
  return 0
}

run_stage() {
  local label="$1"
  if stage_skipped "$label"; then
    log "stage $label skipped (PROBE_SKIP_STAGES='${SKIP_STAGES}')"
    return 0
  fi
  _stage "$label" 1 "${@:2}"
}

# Report-only stage (data-size budget: gates the commit in the workflow, but
# must not stop the probe from collecting observations).
warn_stage() {
  local label="$1"
  if stage_skipped "$label"; then
    log "stage $label skipped (PROBE_SKIP_STAGES='${SKIP_STAGES}')"
    return 0
  fi
  _stage "$label" 0 "${@:2}"
}

# `git reset --hard` may only discard the probe's own footprint: paths under
# `data/`. A dirty path anywhere else (an agent editing this same worktree)
# must never be wiped by the probe.
probe_owns_worktree() {
  local path
  for path in $(git diff --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null); do
    case "$path" in
      data/*) ;;
      *) return 1 ;;
    esac
  done
  return 0
}

# Only the ledger paths may ever be staged in this worktree. A killed pre-#92
# loop can leave the whole `data/` tree half-staged (observed on master: commit
# 690235f6 carried station-changes/metrics/stats), and a plain commit would then
# fold those snapshots into the ledger commit — exactly the ~39 MiB/day this
# issue removes. Unstage everything that is not the ledger.
unstage_foreign_paths() {
  local path keep allowed
  for path in $(git diff --cached --name-only 2>/dev/null); do
    keep=0
    for allowed in "${COMMITTED_FILES[@]}"; do
      [ "$path" = "$allowed" ] && keep=1 && break
    done
    [ "$keep" -eq 1 ] && continue
    if git restore --staged -- "$path" >>"$LOG_FILE" 2>&1; then
      log "unstaged non-ledger path $path (samples stay local)"
    else
      log "could not unstage non-ledger path $path"
    fi
  done
}

# A stage named in `PROBE_SKIP_STAGES` (comma-separated) is not run at all.
stage_skipped() {
  local stage="$1" item
  for item in $(printf '%s' "$SKIP_STAGES" | tr ',' ' '); do
    [ "$item" = "$stage" ] && return 0
  done
  return 1
}

# ── Syncing with the remote (issue #113) ────────────────────────────────────
#
# Actions and the probe both write into `data/`, and the probe *rewrites* tracked
# data files every cycle (`data/history/**`, `data/update-profile.json`, ...)
# without ever committing them (#92/#103). Every Actions commit touching one of
# those files therefore met a dirty local copy, `pull --rebase --autostash` left
# an unmerged index behind and the next cycle died on
# `error: Pulling is not possible because you have unmerged files`.
#
# The rule is now explicit and logged per path: the **remote wins** for every
# path under `data/` except the probe's own ledger `data/fetch-log.json` (Actions
# never writes it, so losing it would lose the cycle the probe just observed).
# During a rebase HEAD is the upstream side, hence `--ours` *is* the remote
# content and `--theirs` the local commit being replayed; a conflicted `--autostash`
# pop has the same orientation.

# Is a rebase (or a half-finished one) in progress in this worktree?
rebase_in_progress() {
  [ -d "$(git rev-parse --git-path rebase-merge 2>/dev/null)" ] && return 0
  [ -d "$(git rev-parse --git-path rebase-apply 2>/dev/null)" ] && return 0
  return 1
}

unmerged_paths() { git diff --name-only --diff-filter=U 2>/dev/null; }

# Resolve one conflicted path and stage the result, logging which side won.
resolve_conflict_path() {
  local path="$1" side label
  case "$path" in
    data/fetch-log.json)
      side=--theirs
      label="kept the local probe ledger (Actions never writes it)"
      ;;
    data/*)
      side=--ours
      label="took the remote (Actions) version"
      ;;
    *)
      log "conflict $path: outside data/, refusing to guess"
      return 1
      ;;
  esac
  if git checkout "$side" -- "$path" >>"$LOG_FILE" 2>&1 &&
     git add -- "$path" >>"$LOG_FILE" 2>&1; then
    log "conflict $path: $label"
    return 0
  fi
  # A delete/modify conflict cannot be resolved by checking a side out.
  if git rm -q -- "$path" >>"$LOG_FILE" 2>&1; then
    log "conflict $path: $label (path removed)"
    return 0
  fi
  log "conflict $path: could not be resolved automatically"
  return 1
}

# Resolve every conflict of the in-progress rebase, then continue until it
# finishes. Returns 0 once the rebase completed, 1 when it cannot be completed.
finish_rebase() {
  local guard=0 stalled=0 conflicts path
  while rebase_in_progress; do
    guard=$((guard + 1))
    if [ "$guard" -gt 40 ]; then
      log "rebase still running after $((guard - 1)) resolution step(s); giving up"
      return 1
    fi
    conflicts="$(unmerged_paths)"
    if [ -n "$conflicts" ]; then
      for path in $conflicts; do
        resolve_conflict_path "$path" || return 1
      done
      stalled=0
    fi
    if GIT_EDITOR=true git rebase --continue >>"$LOG_FILE" 2>&1; then
      stalled=0
    elif [ -z "$(unmerged_paths)" ]; then
      # `--continue` refused with nothing left to resolve (e.g. an empty commit
      # it wants `--skip` for). Two consecutive refusals mean we stop guessing.
      stalled=$((stalled + 1))
      if [ "$stalled" -ge 2 ]; then
        log "rebase --continue refused with no conflicted path left; giving up"
        return 1
      fi
    fi
  done
  return 0
}

# A failed `pull --rebase --autostash` leaves one of two states: (1) a rebase
# stopped on conflicts, (2) no rebase but an unmerged index from the conflicted
# autostash pop. Both are resolvable; anything else (network, refusal) is not.
reconcile_failed_pull() {
  local conflicts path
  if rebase_in_progress; then
    log "pull --rebase stopped on conflicts; resolving in favour of the remote (data/ is Actions-owned)"
    finish_rebase
    return $?
  fi
  conflicts="$(unmerged_paths)"
  if [ -n "$conflicts" ]; then
    log "autostash pop left unmerged files ($(printf '%s' "$conflicts" | tr '\n' ' ')); resolving in favour of the remote"
    for path in $conflicts; do
      resolve_conflict_path "$path" || return 1
    done
    return 0
  fi
  return 1
}

# `pull --rebase`, resolving conflicts instead of aborting. This is the single
# path both the start-of-cycle sync and a rejected push use.
pull_rebase_resolving() {
  if git pull --rebase --autostash >>"$LOG_FILE" 2>&1; then
    return 0
  fi
  reconcile_failed_pull
}

# Last resort: the rebase cannot be completed, so throw the local state away —
# but only when every dirty path belongs to the probe (`data/`). Logged as
# "discarded local" so the log distinguishes it from "took the remote version".
discard_local_state() {
  git rebase --abort >>"$LOG_FILE" 2>&1
  git rebase --quit >/dev/null 2>&1
  git merge --abort >/dev/null 2>&1
  git fetch origin >>"$LOG_FILE" 2>&1
  if probe_owns_worktree; then
    if git reset --hard '@{u}' >>"$LOG_FILE" 2>&1; then
      log "sync could not be completed; discarded local data/ changes, remote wins"
    else
      log "reset --hard @{u} failed; continuing"
    fi
  else
    log "refusing reset --hard '@{u}': the working tree holds non-probe changes ($(git diff --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null | tr '\n' ' ')); leaving it untouched"
  fi
}

# Start-of-cycle sync: the Actions side keeps pushing data, so rebase first.
# Bounded retries (PROBE_SYNC_ATTEMPTS, default 2): a conflict costs at most the
# rebase resolution, and only a repeated failure falls back to discarding the
# local state — never a failed cycle on its own (issue #113).
sync_with_remote() {
  local attempt=1
  while [ "$attempt" -le "$SYNC_ATTEMPTS" ]; do
    if pull_rebase_resolving; then
      [ "$attempt" -gt 1 ] && log "synced with remote on attempt $attempt/$SYNC_ATTEMPTS"
      return 0
    fi
    log "sync with remote failed (attempt $attempt/$SYNC_ATTEMPTS)"
    attempt=$((attempt + 1))
    if [ "$attempt" -le "$SYNC_ATTEMPTS" ]; then
      sleep "$SYNC_BACKOFF_SECONDS"
    fi
  done
  discard_local_state
  return 1
}

# Push, rebasing and retrying on rejection (bounded by PROBE_MAX_PUSH_RETRIES).
push_with_retries() {
  local retries=0
  while :; do
    PUSH_ATTEMPTS=$((PUSH_ATTEMPTS + 1))
    if git push >>"$LOG_FILE" 2>&1; then
      return 0
    fi
    if [ "$retries" -ge "$MAX_PUSH_RETRIES" ]; then
      log "push rejected after $PUSH_ATTEMPTS attempt(s) (retries=$retries); giving up this cycle"
      return 1
    fi
    log "push rejected (retry $((retries + 1))/$MAX_PUSH_RETRIES); rebasing on remote"
    if ! pull_rebase_resolving; then
      log "rebase after rejected push could not be completed; aborting it, keeping remote"
      git rebase --abort >>"$LOG_FILE" 2>&1
      return 1
    fi
    retries=$((retries + 1))
    sleep "$RETRY_BACKOFF_SECONDS"
  done
}

# Refresh the committed .br siblings — the **same explicit set** the workflow's
# brotli step compresses. A blanket `data/*.json` glob would also create `.br`
# files for fetch-log / fetch-state / fetch-schedule / update-profile, which are
# never served; the first two change every cycle, so they would churn in every
# probe commit.
compress_data() {
  if ! command -v brotli >/dev/null 2>&1; then
    log "brotli not found; skipping .br refresh"
    return 0
  fi
  local f
  for f in data/stations.json data/stats.json data/history.json \
           data/regie-margin.json data/metrics.json; do
    [ -f "$f" ] || continue
    brotli -fZ "$f" -o "$f.br" >>"$LOG_FILE" 2>&1 || log "brotli failed for $f"
  done
  for f in data/history/*.json; do
    [ -f "$f" ] || continue
    brotli -fZ "$f" -o "$f.br" >>"$LOG_FILE" 2>&1 || log "brotli failed for $f"
  done
}

# Local commits that have not reached the upstream yet (a push that gave up
# must be retried on the next cycle, independent of the commit throttle).
has_unpushed_commits() {
  git rev-parse --verify -q '@{u}' >/dev/null 2>&1 || return 1
  [ -n "$(git log --oneline '@{u}..HEAD' 2>/dev/null)" ]
}

# Epoch of the last data commit this probe made (0 when unknown).
last_commit_epoch() {
  local epoch
  epoch="$(cat "$COMMIT_STATE_FILE" 2>/dev/null || echo 0)"
  case "$epoch" in ''|*[!0-9]*) epoch=0 ;; esac
  printf '%s' "$epoch"
}

record_commit_epoch() {
  mkdir -p "$(dirname "$COMMIT_STATE_FILE")"
  date -u +%s >"$COMMIT_STATE_FILE"
}

# Commit the small ledger set, at most once per COMMIT_INTERVAL_SECONDS, and
# only when it changed (issue #92). The snapshots are still written to disk by
# the pipeline stages but are never added. A cycle whose previous commit is
# still unpushed pushes it again instead of waiting for the throttle.
commit_and_push() {
  local now_epoch last_epoch elapsed pending=0
  now_epoch="$(date -u +%s)"
  last_epoch="$(last_commit_epoch)"
  elapsed=$((now_epoch - last_epoch))
  if has_unpushed_commits; then
    pending=1
  fi

  if [ "$pending" -eq 0 ] && [ "$elapsed" -lt "$COMMIT_INTERVAL_SECONDS" ]; then
    log "commit throttle: last data commit ${elapsed}s ago (< ${COMMIT_INTERVAL_SECONDS}s); samples stay local this cycle"
    return 0
  fi

  local files=() f
  for f in "${COMMITTED_FILES[@]}"; do
    [ -f "$f" ] && files+=("$f")
  done
  if [ "${#files[@]}" -eq 0 ]; then
    log "no ledger file on disk; nothing to commit"
    return 0
  fi
  # Whatever the index held (see unstage_foreign_paths), by now it can only
  # carry the ledger entries — the `git commit` below adds no path it did not
  # choose itself.
  unstage_foreign_paths
  git add -- "${files[@]}" >>"$LOG_FILE" 2>&1
  if git diff --cached --quiet; then
    log "no change in the committed ledger files; nothing to commit"
  elif git commit -q -m "$COMMIT_MESSAGE" >>"$LOG_FILE" 2>&1; then
    log "committed local probe ledger (${COMMITTED_FILES[*]})"
    record_commit_epoch
  else
    log "commit failed"
    return 1
  fi

  if [ -n "${PROBE_SKIP_PUSH:-}" ]; then
    log "PROBE_SKIP_PUSH set; not pushing"
    return 0
  fi

  PUSH_ATTEMPTS=0
  local pushed=1
  push_with_retries || pushed=0
  PENDING_RETRIES=$((PENDING_RETRIES + PUSH_ATTEMPTS - 1))
  if [ "$pushed" -eq 1 ]; then
    log "pushed data (push attempts=$PUSH_ATTEMPTS)"
    return 0
  fi
  log "push gave up after $PUSH_ATTEMPTS attempt(s); local commit kept for the next cycle"
  return 1
}

# One health summary per local day, commented on the tracking issue (#78).
# Real numbers only: they come from data/fetch-log.json.
send_daily_summary() {
  local day
  day="$(date +%F)"

  if [ "$("$PYTHON" scripts/fetch_log.py needs-summary --day "$day" 2>>"$LOG_FILE")" != "true" ]; then
    log "daily summary already sent for $day"
    return 0
  fi
  if ! command -v gh >/dev/null 2>&1; then
    log "gh not found; skipping daily summary for $day"
    return 1
  fi

  local body
  body="$("$PYTHON" scripts/fetch_log.py summary 2>>"$LOG_FILE")"
  if printf '%s\n' "$body" | gh issue comment "$TRACKING_ISSUE" --body-file - >>"$LOG_FILE" 2>&1; then
    log "daily summary posted to issue #$TRACKING_ISSUE"
    "$PYTHON" scripts/fetch_log.py mark-summary --day "$day" >>"$LOG_FILE" 2>&1
    return 0
  fi
  log "daily summary comment failed; will retry next cycle"
  return 1
}

cycle() {
  local failed=0 now
  # A killed pre-#92 loop may have left a half-finished staging of the whole
  # `data/` tree in the index; drop it first so no snapshot can ride a commit.
  unstage_foreign_paths
  sync_with_remote || log "start-of-cycle sync failed; continuing on the current tree"

  run_stage download "$PYTHON" scripts/download_data.py || failed=1
  run_stage process "$PYTHON" scripts/process_data.py || failed=1
  run_stage profile "$PYTHON" scripts/update_profile.py || failed=1
  # Samples leave the repository (issue #92): append this cycle's event to the
  # gitignored local log instead of committing the snapshots.
  if [ -f scripts/probe_events.py ]; then
    run_stage probe-events "$PYTHON" scripts/probe_events.py append \
      --events-file "$EVENTS_FILE" || failed=1
  fi
  # Actions appends `data/history/**` on every admitted run; the probe writing
  # the same tracked files is what made 1 cycle in 4 fail (#113), so the stage is
  # skipped by default (PROBE_SKIP_STAGES, see above). The label stays `history`.
  run_stage history "$PYTHON" scripts/append_history.py || failed=1
  if [ -f scripts/metrics.py ]; then
    run_stage metrics "$PYTHON" scripts/metrics.py || failed=1
  fi
  # The workflow runs this before committing; here it is a report-only guard so
  # an over-budget data/ directory shows up in the log without stopping the probe.
  if [ -f scripts/check_data_size.py ]; then
    warn_stage size-budget "$PYTHON" scripts/check_data_size.py
  fi

  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ "$failed" -ne 0 ]; then
    "$PYTHON" scripts/fetch_log.py append --at "$now" --fail --retries "$PENDING_RETRIES" \
      >>"$LOG_FILE" 2>&1
    log "cycle recorded as failed"
  else
    "$PYTHON" scripts/fetch_log.py append --at "$now" --retries "$PENDING_RETRIES" \
      >>"$LOG_FILE" 2>&1
  fi
  PENDING_RETRIES=0

  compress_data

  if [ -z "${PROBE_SKIP_SUMMARY:-}" ]; then
    send_daily_summary || log "daily summary not sent this cycle"
  fi

  if ! commit_and_push; then
    failed=1
    # A push that gave up is a real incident: count it (no extra timestamp).
    # This write happens *after* the commit, so it stays uncommitted and rides
    # the next cycle's commit — self-healing, and the counter cannot inflate the
    # interval series.
    "$PYTHON" scripts/fetch_log.py append --at "$now" --fail >>"$LOG_FILE" 2>&1
  fi

  return "$failed"
}

cycles=0
while :; do
  cycles=$((cycles + 1))
  cycle_start="$SECONDS"
  log "probe cycle $cycles start (interval ${INTERVAL_SECONDS}s, max_push_retries ${MAX_PUSH_RETRIES})"
  cycle || log "probe cycle $cycles finished with errors"
  log "probe cycle $cycles done"
  if [ "$MAX_CYCLES" -gt 0 ] && [ "$cycles" -ge "$MAX_CYCLES" ]; then
    break
  fi

  # Top up only the *remaining* part of the interval: sleeping the full
  # INTERVAL_SECONDS after the work would stretch the observed interval to
  # `900 s + cycle duration` and bias the #76 evaluation.
  elapsed=$((SECONDS - cycle_start))
  remaining=$((INTERVAL_SECONDS - elapsed))
  if [ "$remaining" -lt 0 ]; then
    remaining=0
  fi
  log "probe cycle $cycles work=${elapsed}s sleep=${remaining}s"
  sleep "$remaining"
done

log "probe loop exiting after $cycles cycle(s)"

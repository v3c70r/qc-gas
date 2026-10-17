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
#
# Safety:
#   * the loop refuses to run on any branch but `PROBE_BASE_BRANCH` (default
#     `master`): it must live in its **own worktree/clone** (`git worktree add`),
#     never in the same working tree where an agent edits a feature branch —
#     otherwise its `git add`/`reset --hard` would swallow somebody else's work;
#   * a failing stage never stops the loop (no `set -e`, no `should_fetch.py`);
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

run_stage() { _stage "$1" 1 "${@:2}"; }

# Report-only stage (data-size budget: gates the commit in the workflow, but
# must not stop the probe from collecting observations).
warn_stage() { _stage "$1" 0 "${@:2}"; }

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

# Start-of-cycle sync: the Actions side keeps pushing data, so rebase first.
# The remote always wins; a conflicted rebase is aborted and discarded — but
# only the probe's own uncommitted work may be discarded.
sync_with_remote() {
  if git pull --rebase --autostash >>"$LOG_FILE" 2>&1; then
    return 0
  fi
  log "start-of-cycle pull --rebase failed; discarding local state, remote wins"
  git rebase --abort >>"$LOG_FILE" 2>&1
  git rebase --quit >/dev/null 2>&1
  git merge --abort >/dev/null 2>&1
  git fetch origin >>"$LOG_FILE" 2>&1
  if probe_owns_worktree; then
    git reset --hard '@{u}' >>"$LOG_FILE" 2>&1 || log "reset --hard @{u} failed; continuing"
  else
    log "refusing reset --hard '@{u}': the working tree holds non-probe changes ($(git diff --name-only 2>/dev/null; git diff --cached --name-only 2>/dev/null | tr '\n' ' ')); leaving it untouched"
  fi
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
    if ! git pull --rebase --autostash >>"$LOG_FILE" 2>&1; then
      log "rebase after rejected push failed; aborting rebase, keeping remote"
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
  sync_with_remote || true

  run_stage download "$PYTHON" scripts/download_data.py || failed=1
  run_stage process "$PYTHON" scripts/process_data.py || failed=1
  run_stage profile "$PYTHON" scripts/update_profile.py || failed=1
  # Samples leave the repository (issue #92): append this cycle's event to the
  # gitignored local log instead of committing the snapshots.
  if [ -f scripts/probe_events.py ]; then
    run_stage probe-events "$PYTHON" scripts/probe_events.py append \
      --events-file "$EVENTS_FILE" || failed=1
  fi
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

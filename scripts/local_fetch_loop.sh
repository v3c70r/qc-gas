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
#   append_history.py -> metrics.py   (exactly what update-data.yml runs)
#   then fetch_log.py records the completed cycle in data/fetch-log.json.
#
# Safety:
#   * a failing stage never stops the loop (no `set -e`, no `should_fetch.py`);
#   * the push is a bounded `pull --rebase` retry (default 3) so the Actions
#     data commits win — a failed rebase aborts and resyncs to the remote
#     instead of overwriting it;
#   * everything is appended to `.agent/probe.log` (gitignored).
#
# Env knobs (all optional):
#   PROBE_INTERVAL_SECONDS   cycle cadence, default 900 (15 min)
#   PROBE_MAX_PUSH_RETRIES   rebase+push retries, default 3
#   PROBE_MAX_CYCLES         stop after N cycles (0 = forever; 1 = smoke test)
#   PROBE_SKIP_PUSH          non-empty: commit locally but never push
#   PROBE_SKIP_SUMMARY       non-empty: never comment the daily health summary
#   PROBE_TRACKING_ISSUE     issue number for the daily summary, default 78
#   PROBE_LOG_FILE           default .agent/probe.log
#   PROBE_PYTHON             default python3
#
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || { echo "cannot cd to $REPO_ROOT" >&2; exit 1; }

INTERVAL_SECONDS="${PROBE_INTERVAL_SECONDS:-900}"
MAX_PUSH_RETRIES="${PROBE_MAX_PUSH_RETRIES:-3}"
MAX_CYCLES="${PROBE_MAX_CYCLES:-0}"
TRACKING_ISSUE="${PROBE_TRACKING_ISSUE:-78}"
LOG_FILE="${PROBE_LOG_FILE:-$REPO_ROOT/.agent/probe.log}"
RETRY_BACKOFF_SECONDS="${PROBE_RETRY_BACKOFF_SECONDS:-15}"
PYTHON="${PROBE_PYTHON:-python3}"
COMMIT_MESSAGE='Update gas prices data (local probe) [skip ci]'

# Push retries spent by the cycle that just finished are folded into the *next*
# cycle's ledger entry (the entry has to be written before the commit it rides
# in, so the number cannot be known yet).
PENDING_RETRIES=0
PUSH_ATTEMPTS=0

mkdir -p "$(dirname "$LOG_FILE")"
touch "$LOG_FILE"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >>"$LOG_FILE"; }

# Run one pipeline stage, capturing its output as evidence. Never aborts.
run_stage() {
  local label="$1"
  shift
  local out rc
  out="$("$@" 2>&1)"
  rc=$?
  if [ -n "$out" ]; then
    printf '%s\n' "$out" >>"$LOG_FILE"
  fi
  if [ "$rc" -ne 0 ]; then
    log "stage $label failed (rc=$rc)"
  else
    log "stage $label ok"
  fi
  return "$rc"
}

# Start-of-cycle sync: the Actions side keeps pushing data, so rebase first.
# The remote always wins; a conflicted rebase is aborted and discarded.
sync_with_remote() {
  if git pull --rebase --autostash >>"$LOG_FILE" 2>&1; then
    return 0
  fi
  log "start-of-cycle pull --rebase failed; discarding local state, remote wins"
  git rebase --abort >>"$LOG_FILE" 2>&1
  git rebase --quit >/dev/null 2>&1
  git merge --abort >/dev/null 2>&1
  git fetch origin >>"$LOG_FILE" 2>&1
  git reset --hard '@{u}' >>"$LOG_FILE" 2>&1 || log "reset --hard @{u} failed; continuing"
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

# Refresh the committed .br siblings exactly like the workflow's brotli step.
compress_data() {
  if ! command -v brotli >/dev/null 2>&1; then
    log "brotli not found; skipping .br refresh"
    return 0
  fi
  local f
  for f in data/*.json; do
    [ -f "$f" ] || continue
    brotli -fZ "$f" -o "$f.br" >>"$LOG_FILE" 2>&1 || log "brotli failed for $f"
  done
  for f in data/history/*.json; do
    [ -f "$f" ] || continue
    brotli -fZ "$f" -o "$f.br" >>"$LOG_FILE" 2>&1 || log "brotli failed for $f"
  done
}

commit_and_push() {
  git add data/ >>"$LOG_FILE" 2>&1
  if git diff --cached --quiet; then
    log "no data change; nothing to commit"
  elif git commit -q -m "$COMMIT_MESSAGE" >>"$LOG_FILE" 2>&1; then
    log "committed local probe data"
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
  sync_with_remote || true

  run_stage download "$PYTHON" scripts/download_data.py || failed=1
  run_stage process "$PYTHON" scripts/process_data.py || failed=1
  run_stage profile "$PYTHON" scripts/update_profile.py || failed=1
  run_stage history "$PYTHON" scripts/append_history.py || failed=1
  if [ -f scripts/metrics.py ]; then
    run_stage metrics "$PYTHON" scripts/metrics.py || failed=1
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
    "$PYTHON" scripts/fetch_log.py append --at "$now" --fail >>"$LOG_FILE" 2>&1
  fi

  return "$failed"
}

cycles=0
while :; do
  cycles=$((cycles + 1))
  log "probe cycle $cycles start (interval ${INTERVAL_SECONDS}s, max_push_retries ${MAX_PUSH_RETRIES})"
  cycle || log "probe cycle $cycles finished with errors"
  log "probe cycle $cycles done"
  if [ "$MAX_CYCLES" -gt 0 ] && [ "$cycles" -ge "$MAX_CYCLES" ]; then
    break
  fi
  sleep "$INTERVAL_SECONDS"
done

log "probe loop exiting after $cycles cycle(s)"

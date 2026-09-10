#!/usr/bin/env bash
# ── PM agent supervisor ──
# Wakes every 30 min; run.mjs decides whether this week's run is due.
# Cheap when idle (local date math only, no network unless due).
#
#   tmux new -s pm
#   cd ~/Projects/qc-gas && ./.agent/pm/supervisor.sh
set -u
cd "$(dirname "$0")/../.." || exit 1

LOG=.agent/pm/supervisor.log
echo "[pm-supervisor $(date '+%F %T')] up — schedule: weekday=${PM_WEEKDAY:-1} ${PM_HOUR_UTC:-17}:00 UTC, cap=${PM_WEEKLY_CAP:-5}/week" | tee -a "$LOG"

while true; do
  node .agent/pm/run.mjs 2>&1 | tee -a "$LOG"
  sleep 1800   # 30 min
done

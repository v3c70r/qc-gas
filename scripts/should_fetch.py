#!/usr/bin/env python3
"""Workflow gate: decide whether *this* run may poll the upstream price source.

The data workflow wakes up often (every 15 minutes) so it can land close to the
moment stations actually re-price, but it must not hammer the upstream Régie
Essence server. This gate compares the current time with
``data/fetch-schedule.json`` (learned by ``update_profile.py``) plus a freshness
safety net, records the attempt in ``data/fetch-state.json`` and exports
``due=true|false`` for the workflow steps that follow.

The gate is intentionally fail-open on a scheduled run when its own state looks
broken: a stale data snapshot is worse than one extra download.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_strategy import (  # noqa: E402
    DEFAULT_DAILY_BUDGET,
    due_decision,
    parse_iso,
    plan_slots,
    research_prior_weights,
    slot_labels,
    to_iso,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
SCHEDULE_FILE = 'fetch-schedule.json'
STATE_FILE = 'fetch-state.json'


def load_json(path, fallback=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def load_slots(data_dir, budget=DEFAULT_DAILY_BUDGET):
    """Daily "HH:MM" slots, falling back to the research prior plan."""
    schedule = load_json(Path(data_dir) / SCHEDULE_FILE)
    slots = (schedule or {}).get('slots')
    if isinstance(slots, list) and slots:
        return [str(s) for s in slots], 'learned'
    return slot_labels(plan_slots(research_prior_weights(), budget=budget)), 'research-prior'


def write_output(target, due):
    line = f'due={"true" if due else "false"}'
    if target:
        try:
            with open(target, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError as exc:
            print(f'warning: could not write $GITHUB_OUTPUT ({exc})', file=sys.stderr)
    print(line)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--now', help='UTC ISO timestamp override (tests/logs)')
    parser.add_argument('--event-name', default=os.environ.get('GITHUB_EVENT_NAME') or 'schedule')
    parser.add_argument('--github-output', default=os.environ.get('GITHUB_OUTPUT'))
    parser.add_argument('--budget', type=int, default=DEFAULT_DAILY_BUDGET)
    parser.add_argument('--min-gap-minutes', type=int, default=10)
    parser.add_argument('--max-age-minutes', type=int, default=180)
    parser.add_argument('--grace-minutes', type=int, default=45)
    parser.add_argument('--dry-run', action='store_true',
                        help='decide but do not record the fetch')
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    now = parse_iso(args.now) or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    slots, slots_source = load_slots(data_dir, budget=args.budget)
    state = load_json(data_dir / STATE_FILE, {}) or {}
    last_fetch_at = state.get('last_fetch_at')

    forced = args.event_name not in ('schedule', '')
    if forced:
        due, reason = True, f'event "{args.event_name}": fetching unconditionally'
    else:
        due, reason = due_decision(
            now, slots, last_fetch_at,
            min_gap_minutes=args.min_gap_minutes,
            max_age_minutes=args.max_age_minutes,
            grace_minutes=args.grace_minutes,
        )

    if due and not args.dry_run:
        new_state = {
            'v': 1,
            'last_fetch_at': to_iso(now),
            'fetches': int(state.get('fetches') or 0) + 1,
            'last_reason': reason,
            'event': args.event_name,
        }
        target = data_dir / STATE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        with open(target, 'w', encoding='utf-8') as f:
            json.dump(new_state, f, ensure_ascii=False, separators=(',', ':'))
            f.write('\n')

    write_output(args.github_output, due)
    print(f'gate [{slots_source}, {len(slots)} slots/day]: {reason}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

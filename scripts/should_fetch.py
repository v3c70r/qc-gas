#!/usr/bin/env python3
"""Workflow gate: decide whether *this* run may poll the upstream price source.

The data workflow wakes up often (every 15 minutes) so it can land close to the
moment stations actually re-price, but it must not hammer the upstream Régie
Essence server. This gate compares the current time with
``data/fetch-schedule.json`` (learned by ``update_profile.py``) plus a freshness
safety net, records the attempt in ``data/fetch-state.json`` and exports
``due=true|false`` for the workflow steps that follow.

The gate is intentionally fail-open on a scheduled run when its own state looks
broken: a stale data snapshot is worse than one extra download. When a due hit
is a reserved exploration slot (issue #69), the reason carries ``[explore]`` so
the poll can be attributed to exploration vs exploitation after the fact.

Issue #93: the workflow is now woken every 5 minutes by a local `crontab`
(`scripts/install_dispatch_cron.sh`, `repository_dispatch`). A dispatch is an
external wake-up, so it is evaluated against the *same* #91 timetable as a
scheduled run — an off-schedule wake-up must not download upstream, commit or
deploy. It only differs in accounting: a dispatch is not one of the planned
slots, so it does not consume the day's request budget (the plan budget is the
number of slots of the day's timetable, issue #91/#93). Other non-schedule
events (`push`, ...) keep the pre-#93 behaviour and fetch unconditionally, since
they are not fetch triggers.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_strategy import (  # noqa: E402
    BUDGET_ENV_VAR,
    DEFAULT_DAILY_BUDGET,
    DEFAULT_MAX_AGE_MINUTES,
    LOCAL_TZ,
    MAX_AGE_ENV_VAR,
    due_decision,
    env_int,
    parse_iso,
    parse_timetable,
    plan_slots,
    research_prior_weights,
    slot_labels,
    to_iso,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
SCHEDULE_FILE = 'fetch-schedule.json'
STATE_FILE = 'fetch-state.json'

# Events that are *fetch triggers* the gate has to judge (issue #93). A local
# `crontab` wakes the workflow through `repository_dispatch` every 5 minutes; a
# manual `workflow_dispatch` is the same kind of wake-up. Both are evaluated
# against the timetable, but neither is a planned slot, so neither is charged to
# the day's request budget. Every other event (`push`, ...) is not a fetch
# trigger and keeps fetching unconditionally.
TRIGGER_EVENTS = ('repository_dispatch', 'workflow_dispatch')


def load_json(path, fallback=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def load_slots(data_dir, budget=DEFAULT_DAILY_BUDGET):
    """The day's plan: ``(slots, source, explore_slots, timetable, tz)``.

    Since issue #91 the schedule carries an explicit *local* ``timetable``
    (+ ``timezone``); the gate converts it per calendar day so the plan keeps
    its local shape across the EDT/EST switch. ``slots`` (UTC labels) is still
    returned because it is the legacy field, and it is the only plan available
    for schedules written before #91.

    Falls back to the research-prior plan (which has no learned profile yet,
    so no exploration slots) when the schedule is missing or corrupt.
    """
    schedule = load_json(Path(data_dir) / SCHEDULE_FILE)
    schedule = schedule if isinstance(schedule, dict) else {}
    timetable = [str(label) for label in (schedule.get('timetable') or [])
                 if parse_timetable([label])]
    tz = str(schedule.get('timezone') or LOCAL_TZ)
    explore = (schedule.get('explore_slots') or [])
    explore = [str(s) for s in explore] if isinstance(explore, list) else []
    slots = schedule.get('slots')
    if timetable:
        return ([str(s) for s in (slots or [])], 'timetable', explore, timetable, tz)
    if isinstance(slots, list) and slots:
        return ([str(s) for s in slots], 'learned', explore, [], tz)
    slots = slot_labels(plan_slots(research_prior_weights(), budget=budget))
    return slots, 'research-prior', [], [], tz


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
    # Both defaults come from the shared env vars (falling back to the
    # constants in ``fetch_strategy``), which is the same knob
    # ``update_profile.py`` sizes the written schedule with.
    parser.add_argument('--budget', type=int,
                        default=env_int(BUDGET_ENV_VAR, DEFAULT_DAILY_BUDGET))
    parser.add_argument('--min-gap-minutes', type=int, default=10)
    parser.add_argument('--max-age-minutes', type=int,
                        default=env_int(MAX_AGE_ENV_VAR, DEFAULT_MAX_AGE_MINUTES))
    parser.add_argument('--grace-minutes', type=int, default=45)
    parser.add_argument('--dry-run', action='store_true',
                        help='decide but do not record the fetch')
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    now = parse_iso(args.now) or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    slots, slots_source, explore_slots, timetable, tz = load_slots(
        data_dir, budget=args.budget)
    state = load_json(data_dir / STATE_FILE, {}) or {}
    last_fetch_at = state.get('last_fetch_at')

    forced = args.event_name not in ('schedule', '') and args.event_name not in TRIGGER_EVENTS
    if forced:
        due, reason = True, f'event "{args.event_name}": fetching unconditionally'
    else:
        due, reason = due_decision(
            now, slots, last_fetch_at,
            min_gap_minutes=args.min_gap_minutes,
            max_age_minutes=args.max_age_minutes,
            grace_minutes=args.grace_minutes,
            explore_slots=explore_slots,
            timetable=timetable, tz=tz,
        )
        if args.event_name in TRIGGER_EVENTS:
            # Same due moment as a scheduled run, but not one of the planned
            # slots: the day's budget (= the timetable's slot count) is not
            # charged (issue #93).
            reason = (f'event "{args.event_name}" (not budget-counted): ' + reason)

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
    plan_desc = (f'{len(timetable)} local slots/day' if timetable
                 else f'{len(slots)} slots/day')
    print(f'gate [{slots_source}, {plan_desc}, '
          f'{len(explore_slots)} explore]: {reason}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

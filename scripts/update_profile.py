#!/usr/bin/env python3
"""Learn when gas stations change their prices and re-plan the poll schedule.

Runs after ``process_data.py`` on every successful fetch (see
``.github/workflows/update-data.yml``):

1. Diff the freshly downloaded snapshot against the previously observed one
   (``fetch_strategy.diff_regions``) and fold the result into
   ``data/update-profile.json`` (per-UTC-hour change rates).
2. Turn the learned weights into a bounded daily poll schedule and write
   ``data/fetch-schedule.json``, which ``should_fetch.py`` uses as the gate.

Both files are tiny, committed with the rest of ``data/``, and safe to delete:
the system falls back to the research-seeded prior until they are rebuilt.
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
    SLOTS_PER_HOUR,
    hour_weights,
    observed_from_stations,
    parse_iso,
    plan_slots,
    record_observation,
    region_station_counts,
    slot_labels,
    to_iso,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
DEFAULT_BUDGET = int(os.environ.get('FETCH_DAILY_BUDGET', DEFAULT_DAILY_BUDGET))
DEFAULT_MIN_PER_HOUR = 1
DEFAULT_MAX_PER_HOUR = SLOTS_PER_HOUR
PRIOR_ID = 'research-2026-09'


def load_json(path, fallback=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')


def build_schedule(profile, slots, weights, budget, min_per_hour, max_per_hour, now):
    observed_hours = sum(
        float((profile.get('hours', {}).get(str(h)) or {}).get('observed_hours') or 0.0)
        for h in range(24)
    )
    return {
        'v': 1,
        'generated_at': to_iso(now),
        'source': 'learned' if observed_hours > 0 else 'research-prior',
        'prior': PRIOR_ID,
        'budget_per_day': budget,
        'allocated_per_day': len(slots),
        'min_per_hour': min_per_hour,
        'max_per_hour': max_per_hour,
        'slots': slot_labels(slots),
        'hour_weights': [round(w, 4) for w in weights],
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--now', help='UTC ISO timestamp override (tests/logs)')
    parser.add_argument('--budget', type=int, default=DEFAULT_BUDGET,
                        help='maximum upstream downloads per day')
    parser.add_argument('--min-per-hour', type=int, default=DEFAULT_MIN_PER_HOUR)
    parser.add_argument('--max-per-hour', type=int, default=DEFAULT_MAX_PER_HOUR)
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    now = parse_iso(args.now) or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    stations = load_json(data_dir / 'stations.json')
    if not stations:
        print('data/stations.json missing; skipping fetch-profile update')
        return 0

    profile_path = data_dir / 'update-profile.json'
    profile = load_json(profile_path, {}) or {}
    profile.setdefault('v', 1)

    observed = observed_from_stations(stations)
    counts = region_station_counts(stations)
    src = (stations.get('metadata') or {}).get('generated_at')

    profile, summary = record_observation(
        profile,
        observed=observed,
        region_counts=counts,
        now=now,
        src=src,
    )

    weights = hour_weights(profile)
    slots = plan_slots(weights, budget=args.budget, min_per_hour=args.min_per_hour,
                       max_per_hour=args.max_per_hour)
    schedule = build_schedule(profile, slots, weights, args.budget,
                              args.min_per_hour, args.max_per_hour, now)

    save_json(profile_path, profile)
    save_json(data_dir / 'fetch-schedule.json', schedule)

    hot = sorted(range(24), key=lambda h: weights[h], reverse=True)[:3]
    print(f"Fetch profile: hour {summary['hour']:02d} samples={summary['samples']} "
          f"changed_regions={len(summary['changed_regions'])} "
          f"changed_stations={summary['changed_stations']} (gap {summary['gap_hours']}h)")
    print(f"Schedule: {len(slots)} downloads/day (budget {args.budget}), "
          f"peak UTC hours {', '.join(f'{h:02d}' for h in hot)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

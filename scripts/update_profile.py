#!/usr/bin/env python3
"""Learn when gas stations change their prices and re-plan the poll schedule.

Runs after ``process_data.py`` on every successful fetch (see
``.github/workflows/update-data.yml``):

1. Diff the freshly downloaded snapshot against the previously observed one —
   per **station** (`fetch_strategy.diff_stations`, issue #68) to get the exact
   set of re-priced stations and their ``last_changed_at``, and per **region**
   (`fetch_strategy.diff_regions`) to keep the learned schedule compatible — and
   fold both into ``data/update-profile.json`` (per-UTC-hour change rates,
   **per-region** hourly rates and per-quarter-hour mark counts, issue #71) and
   ``data/history/station-changes.json`` (per-station freshness).
2. Turn the learned weights into a bounded daily poll schedule — every region's
   learned peak hours are pinned, the rest is water-filled by the expected
   number of re-prices — and write ``data/fetch-schedule.json``, which
   ``should_fetch.py`` uses as the gate.

Both files are tiny, committed with the rest of ``data/``, and safe to delete:
the system falls back to the research-seeded prior until they are rebuilt.
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_strategy import (  # noqa: E402
    BUDGET_ENV_VAR,
    DEFAULT_DAILY_BUDGET,
    SLOTS_PER_HOUR,
    coverage_hours,
    diff_stations,
    env_int,
    hour_rates,
    hour_weights,
    learned_mark_orders,
    observed_from_stations,
    parse_iso,
    plan_slots,
    prices_from_row,
    prices_to_row,
    record_observation,
    region_coverage,
    region_hour_weights,
    region_station_counts,
    slot_labels,
    station_prices,
    station_regions,
    to_iso,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
DEFAULT_MIN_PER_HOUR = 1
DEFAULT_MAX_PER_HOUR = SLOTS_PER_HOUR
PRIOR_ID = 'research-2026-09'
STATION_CHANGES_REL = Path('history') / 'station-changes.json'
STATION_CHANGES_DAYS = 180  # same window as the station daily history tier


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


def build_schedule(profile, slots, weights, budget, min_per_hour, max_per_hour, now,
                   coverage=None, regions=None):
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
        # Issue #71: the hours that are pinned for a region's own learned peak
        # (``coverage``) and, per region, whether the plan honours them.
        'coverage_hours': sorted(int(h) for h in (coverage or ())),
        'region_coverage': regions or {},
    }


def stations_changed_at(store, timestamp):
    """Sorted station keys whose last price change landed at ``timestamp``.

    The store keeps only the *latest* change per station (that is the whole
    point of ``last_changed_at``), so the list of stations that moved in one
    given fetch is exactly the set of keys stamped with that fetch's time.
    """
    stamp = str(timestamp)
    return sorted(key for key, rec in (store.get('s') or {}).items()
                  if (rec or {}).get('t') == stamp)


def update_station_changes(store, prices, changed, now, cutoff_day):
    """Upsert the per-station change store and prune stations no longer seen.

    ``prices`` is ``{coord_key: {fuel: price}}`` for the whole current snapshot
    and ``changed`` is ``{coord_key: [fuel]}`` from :func:`fetch_strategy.
    diff_stations`. Each entry is

    * ``t`` = ``last_changed_at`` — the fetch time of the last real re-price
      (``null`` while no change has ever been observed for that key)
    * ``u`` = ``last_seen_at`` — the fetch time the station last appeared in
      the upstream feed
    * ``p`` = ``[g, s, d]`` — the last observed prices (``null`` when a fuel
      was not reported), which is the baseline the next diff compares against

    Stations that have not been seen for ``STATION_CHANGES_DAYS`` are dropped,
    so the file reaches a steady size instead of growing with churn.
    """
    stamp = to_iso(now)
    entries = {key: dict(rec or {}) for key, rec in (store.get('s') or {}).items()}

    for key, fuels in (prices or {}).items():
        rec = entries.get(key) or {'t': None, 'u': None, 'p': None}
        rec['u'] = stamp
        rec['p'] = prices_to_row(fuels)
        if key in (changed or {}):
            rec['t'] = stamp
        entries[key] = rec

    pruned = {
        key: rec for key, rec in entries.items()
        if str(rec.get('u') or '')[:10] >= cutoff_day
    }

    return {
        'v': 1,
        'generated_at': stamp,
        'tiers': {'max_age_days': STATION_CHANGES_DAYS},
        'last': {
            'at': stamp,
            'observed': len(prices or {}),
            'changed': len(changed or {}),
        },
        's': pruned,
    }


def load_station_change_baseline(store):
    """``{coord_key: {fuel: price}}`` baseline for the next station diff."""
    return {key: prices_from_row((rec or {}).get('p'))
            for key, rec in (store.get('s') or {}).items()
            if (rec or {}).get('p')}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--now', help='UTC ISO timestamp override (tests/logs)')
    parser.add_argument('--budget', type=int,
                        default=env_int(BUDGET_ENV_VAR, DEFAULT_DAILY_BUDGET),
                        help='maximum upstream downloads per day '
                             f'(env {BUDGET_ENV_VAR})')
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

    # ── Station-level diff (#68): compare every station against the last
    # observed price of the same coordinate key, so a +5c/-5c pair inside one
    # region is reported as the two re-prices it really is.
    changes_path = data_dir / STATION_CHANGES_REL
    station_store = load_json(changes_path, {}) or {}
    prices = station_prices(stations)
    changed = diff_stations(load_station_change_baseline(station_store), prices)
    cutoff_day = (now - timedelta(days=STATION_CHANGES_DAYS)).strftime('%Y-%m-%d')
    station_store = update_station_changes(station_store, prices, changed, now, cutoff_day)

    # ── Region attribution (#71): the same re-prices, counted per region, so
    # ``hour_weights`` can learn 18 hourly distributions instead of one average.
    region_of = station_regions(stations)
    region_changes = Counter(region_of.get(key, 'Inconnue') for key in changed)

    profile, summary = record_observation(
        profile,
        observed=observed,
        region_counts=counts,
        now=now,
        src=src,
        station_changes=len(changed),
        stations_observed=len(prices),
        region_changed_stations=region_changes,
    )
    profile['last_station_diff'] = dict(station_store['last'])

    weights = hour_weights(profile)
    per_region = region_hour_weights(profile)
    guarded = coverage_hours(per_region, baseline=weights)
    slots = plan_slots(weights, budget=args.budget, min_per_hour=args.min_per_hour,
                       max_per_hour=args.max_per_hour, coverage_hours=guarded,
                       mark_orders=learned_mark_orders(profile))
    coverage = region_coverage(per_region, slots, baseline=weights)
    for name, rec in coverage.items():
        entry = (profile.get('regions') or {}).get(name) or {}
        rec['stations'] = int(entry.get('stations') or 0)
    schedule = build_schedule(profile, slots, weights, args.budget,
                              args.min_per_hour, args.max_per_hour, now,
                              coverage=guarded, regions=coverage)

    save_json(profile_path, profile)
    save_json(data_dir / 'fetch-schedule.json', schedule)
    save_json(changes_path, station_store)

    hot = sorted(range(24), key=lambda h: weights[h], reverse=True)[:3]
    print(f"Fetch profile: hour {summary['hour']:02d} samples={summary['samples']} "
          f"changed_regions={len(summary['changed_regions'])} "
          f"changed_stations={summary['changed_stations']} "
          f"changed_stations_exact={summary['changed_stations_exact']} "
          f"(gap {summary['gap_hours']}h)")
    print(f"Station changes: {len(changed)} of {len(prices)} observed stations "
          f"re-priced at {station_store['last']['at']}")
    if changed:
        movers = stations_changed_at(station_store, station_store['last']['at'])
        print('Changed stations: ' + ', '.join(movers))
        by_region = ', '.join(f'{name}={count}'
                              for name, count in sorted(region_changes.items()) if count)
        print(f"Changed stations per region: {by_region}")
    rates, _ = hour_rates(profile)
    nonzero = [r for r in rates if r > 0]
    if len(nonzero) > 1:
        print(f"Hour contrast: {min(nonzero):.4g}-{max(nonzero):.4g} expected changed "
              f"stations/hour ({max(nonzero) / min(nonzero):.2f}x)")
    if coverage:
        with_peaks = [name for name, rec in coverage.items() if rec['peaks']]
        covered = sum(1 for name in with_peaks if coverage[name]['covered'])
        print(f"Region coverage: {covered}/{len(with_peaks)} regions with a distinct peak "
              f"hour keep it in the plan ({len(guarded)} pinned hours)")
    print(f"Schedule: {len(slots)} downloads/day (budget {args.budget}), "
          f"peak UTC hours {', '.join(f'{h:02d}' for h in hot)}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

#!/usr/bin/env python3
"""Staleness of an arbitrary fetch timetable (issue #91).

Answers "how stale does the app look under *this* timetable?" with the exact
metric口径 of ``scripts/interval_report.py`` (issue #76):

* **lag** — for every real re-price, the time until the next scheduled fetch
  (``next fetch >= change time``); the station shows the old price for exactly
  that long;
* **p50/p90/p99** — the distribution of those lags (seconds);
* **stale share** — the time-weighted fraction of stations showing a stale
  price at a random instant: ``Σ lag / (stations × window seconds)``.

On top of that it adds the **detection-quantisation correction** the 15-minute
probe cannot see: a re-price is only *detected* at the next observation, so the
real change happened somewhere in the preceding ≤ 15 min. With
``--jitter`` (default on) the analysed change time becomes
``detected - U(0, 15 min)`` with a fixed seed, which removes the systematic
optimism of every "≤ resolution" measurement (uniform 30 min: p90 22.6 →
26.1 min on the probe series).

Plans are compared with :func:`compare_plans` (ε-Pareto on p90/p99/share) and
:func:`verdict` (the published criterion: p90 ≤ 30 min and share ≤ 5 %), so a
"switch the night off" plan is rejected automatically: it keeps p50/p90 but
blows p99 up to ~200 min and roughly doubles the stale share, because the
dominant term of staleness is the largest gap, not the average density.
"""

import argparse
import bisect
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from interval_report import (  # noqa: E402  (metric口径 is reused, not rewritten)
    MIN_CHANGE_EVENTS,
    change_events,
    load_git_observations,
    normalize_observations,
)
from metrics import load_json, parse_iso, percentile, to_iso  # noqa: E402
from fetch_strategy import (  # noqa: E402
    LOCAL_TZ,
    format_timetable,
    local_zone,
    validate_timetable,
)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'

# Published criterion (docs/fetch-interval-report.md): p90 stale age ≤ 30 min
# and time-weighted stale share ≤ 5 %.
P90_BUDGET_SECONDS = 30 * 60
SHARE_BUDGET = 0.05
# "Clearly stale" threshold of the interval report, kept identical.
STALE_AGE_SECONDS = 60 * 60
# Detection resolution of the probe (#74): 15 minutes. Fixed seed so the
# corrected numbers are reproducible.
DEFAULT_RESOLUTION_SECONDS = 15 * 60
DEFAULT_SEED = 1234
# ε-Pareto margin: a plan must beat another by >10 % on a metric to count.
DEFAULT_MARGIN = 0.10
COMPARE_METRICS = ('p90', 'p99', 'stale_share')


# ── Inputs ───────────────────────────────────────────────────────────────────

def load_observations(root=ROOT, observations_path=None,
                      max_revisions=None):
    """Probe observation series: from a file, else the git revision history.

    Both paths go through ``interval_report``'s loaders (``normalize_observations``
    / ``load_git_observations``) so this evaluator and the published report can
    never disagree about what an observation is.
    """
    if observations_path:
        return normalize_observations(load_json(observations_path) or [])
    if max_revisions is None:
        return load_git_observations(root)
    return load_git_observations(root, max_revisions=max_revisions)


def build_window(observations, labels=None, start=None, end=None):
    """The metric window: observations, their re-price events and the totals.

    ``stations`` is the number of coordinate keys seen in the window (the
    denominator of the stale share, exactly as in ``simulate_interval``).
    """
    observations = [obs for obs in (observations or [])
                    if (start is None or obs['at'] >= start)
                    and (end is None or obs['at'] <= end)]
    events = change_events(observations, labels or {})
    stations = len({key for obs in observations for key in obs['prices']})
    window_seconds = 0.0
    if len(observations) >= 2:
        window_seconds = (observations[-1]['at'] - observations[0]['at']).total_seconds()
    return {
        'observations': observations,
        'events': events,
        'stations': stations,
        'window_seconds': window_seconds,
        'window_start': observations[0]['at'] if observations else None,
        'window_end': observations[-1]['at'] if observations else None,
        'split': None,
    }


def window_is_sufficient(window, min_events=MIN_CHANGE_EVENTS):
    """``True`` when ``window`` can support a percentile conclusion (issue #98).

    Reuses the published floor of the interval report
    (``interval_report.MIN_CHANGE_EVENTS``) instead of inventing a second
    standard: below it p99 is noise, so callers must **skip** rather than tighten
    their thresholds (which is how a per-day-growing data set used to pass).
    """
    return (len(window.get('events') or []) >= int(min_events)
            and len(window.get('observations') or []) >= 2
            and float(window.get('window_seconds') or 0.0) > 0)


def split_window(window, fraction=0.6):
    """``(train, test)`` windows split at ``fraction`` of the observations.

    The split is by observation index (so both halves keep real snapshots and
    the boundary snapshot is shared), which is what makes the 2026-10-10..14 /
    2026-10-15..16 train/test comparison of issue #91 possible.
    """
    observations = window.get('observations') or []
    if len(observations) < 4:
        return window, window
    cut = max(1, min(len(observations) - 2, int(round(len(observations) * fraction))))
    train = build_window(observations[:cut + 1])
    train['split'] = 'train'
    test = build_window(observations[cut:])
    test['split'] = 'test'
    return train, test


# ── Simulation ───────────────────────────────────────────────────────────────

def fetch_datetimes(timetable, tz=LOCAL_TZ, start=None, end=None):
    """Every scheduled fetch instant around ``[start, end]`` (local days ±1)."""
    from fetch_strategy import timetable_datetimes

    zone = local_zone(tz)
    if start is None or end is None:
        return []
    out = []
    day = start.astimezone(zone).date() - timedelta(days=1)
    last = end.astimezone(zone).date() + timedelta(days=1)
    while day <= last:
        out.extend(timetable_datetimes(day, timetable, tz))
        day += timedelta(days=1)
    return sorted(set(out))


def simulate_timetable(timetable, window, tz=LOCAL_TZ, jitter=False,
                       seed=DEFAULT_SEED,
                       resolution_seconds=DEFAULT_RESOLUTION_SECONDS,
                       stale_seconds=STALE_AGE_SECONDS):
    """Staleness metrics for ``timetable`` over ``window``.

    ``jitter`` applies the detection-quantisation correction: the analysed
    change instant is ``detected - U(0, resolution)`` (fixed ``seed`` → the
    numbers are reproducible). Returns lag percentiles in **seconds**, the
    time-weighted ``stale_share`` and the inputs the numbers came from.
    """
    labels = list(timetable or [])
    metrics = {
        'timetable': labels,
        'timezone': tz,
        'jitter': bool(jitter),
        'seed': int(seed) if jitter else None,
        'resolution_seconds': float(resolution_seconds),
        'fetches_per_day': len(labels),
        'events': 0,
        'stations': int(window.get('stations') or 0),
        'window_seconds': float(window.get('window_seconds') or 0.0),
        'window_start': to_iso(window['window_start']) if window.get('window_start') else None,
        'window_end': to_iso(window['window_end']) if window.get('window_end') else None,
        'p50': None, 'p90': None, 'p99': None,
        'lag_sum_seconds': 0.0,
        'stale_share': None,
        'stale_share_over_threshold': None,
        'stale_threshold_seconds': float(stale_seconds),
    }
    events = window.get('events') or []
    start, end = window.get('window_start'), window.get('window_end')
    if not events or start is None or end is None:
        return metrics

    fetches = fetch_datetimes(labels, tz, start, end)
    if not fetches:
        return metrics

    rng = random.Random(seed)
    lags = []
    for event in events:
        at = event['at']
        if jitter:
            at = at - timedelta(seconds=float(resolution_seconds) * rng.random())
        index = bisect.bisect_left(fetches, at)
        if index >= len(fetches):
            continue
        lags.append(max(0.0, (fetches[index] - at).total_seconds()))

    metrics['events'] = len(lags)
    if not lags:
        return metrics
    metrics['p50'] = round(percentile(lags, 50), 1)
    metrics['p90'] = round(percentile(lags, 90), 1)
    metrics['p99'] = round(percentile(lags, 99), 1)
    metrics['lag_sum_seconds'] = round(sum(lags), 1)
    if metrics['stations'] and metrics['window_seconds'] > 0:
        metrics['stale_share'] = round(
            sum(lags) / (metrics['stations'] * metrics['window_seconds']), 6)
    metrics['stale_share_over_threshold'] = round(
        sum(1 for lag in lags if lag > stale_seconds) / len(lags), 4)
    return metrics


# ── Comparison ───────────────────────────────────────────────────────────────

def compare_plans(a, b, margin=DEFAULT_MARGIN, metrics=COMPARE_METRICS):
    """``'better'|'worse'|'tie'|'mixed'|'unknown'`` for ``a`` relative to ``b``.

    ε-Pareto: a metric counts only when it differs by more than ``margin``
    (10 % by default), so noise between two runs never flips a verdict. A plan
    that improves one metric and loses another by more than the margin is
    ``'mixed'`` (which is exactly the pure-intensity allocation: it buys p50 by
    selling p99).
    """
    wins = losses = 0
    for key in metrics:
        left, right = a.get(key), b.get(key)
        if left is None or right is None:
            return 'unknown'
        scale = max(abs(float(right)), 1e-9)
        change = (float(left) - float(right)) / scale
        if change < -margin:
            wins += 1
        elif change > margin:
            losses += 1
    if wins and losses:
        return 'mixed'
    if wins:
        return 'better'
    if losses:
        return 'worse'
    return 'tie'


def verdict(metrics, p90_budget=P90_BUDGET_SECONDS, share_budget=SHARE_BUDGET):
    """The published acceptance criterion, plus the reasons when it fails."""
    failed = []
    p90 = metrics.get('p90')
    share = metrics.get('stale_share')
    if p90 is None:
        failed.append('no measurable events in the window')
    elif p90 > p90_budget:
        failed.append(f"p90 {p90 / 60:.1f} min > {p90_budget / 60:g} min")
    if share is None:
        failed.append('stale share not measurable')
    elif share > share_budget:
        failed.append(f"stale share {share * 100:.2f} % > {share_budget * 100:g} %")
    return {
        'acceptable': not failed,
        'failed': failed,
        'p90_budget_seconds': p90_budget,
        'stale_share_budget': share_budget,
    }


def pareto_front(plans, margin=DEFAULT_MARGIN):
    """``{name: [names that beat it]}`` under the same ε-Pareto rule.

    This is what rejects a night-blackout plan *automatically*: it keeps the
    same p50/p90 as the recommended timetable, so it passes the headline
    criterion, but the recommended one beats it on p99 and on the stale share,
    so the blackout is dominated and must not ship.
    """
    return {
        name: sorted(other for other, other_metrics in plans.items()
                     if other != name
                     and compare_plans(other_metrics, metrics, margin) == 'better')
        for name, metrics in plans.items()
    }


# ── Presets (the measured Pareto table) ──────────────────────────────────────

def uniform_timetable(step_minutes):
    """Untimed local grid: one fetch every ``step_minutes`` from 00:00."""
    step = int(step_minutes)
    return format_timetable([(mark // 60, mark % 60) for mark in range(0, 1440, step)])


def hybrid_timetable(day_step, night=True, midnight_step=10):
    """Busy ``day_step`` minutes, midnight ``midnight_step``, night 1/h."""
    marks = []
    for hour in range(6, 24):
        marks.extend((hour, minute) for minute in range(0, 60, int(day_step)))
    marks.extend((0, minute) for minute in range(0, 60, int(midnight_step)))
    if night:
        marks.extend((hour, 0) for hour in range(1, 6))
    return format_timetable(marks)


def preset_timetables():
    """Named plans of the issue #91 Pareto table (incl. the night-blackout trap)."""
    return {
        'uniform60': uniform_timetable(60),
        'uniform30': uniform_timetable(30),
        'uniform15': uniform_timetable(15),
        'busy30+night+midnight': hybrid_timetable(30),
        'busy20+night+midnight': hybrid_timetable(20),
        'busy15+night+midnight': hybrid_timetable(15),
        'night-blackout': hybrid_timetable(20, night=False),
    }


# ── CLI ──────────────────────────────────────────────────────────────────────

def parse_label_list(value):
    return [part.strip() for part in str(value or '').split(',') if part.strip()]


def print_table(rows, title, dominated=None):
    print(title)
    header = (f"{'plan':<24} {'/day':>5} {'p50':>7} {'p90':>7} {'p99':>7} "
              f"{'share':>7}  verdict")
    print(header)
    print('-' * len(header))
    dominated = dominated or {}
    for name, metrics in rows:
        share = metrics['stale_share']
        state = verdict(metrics)
        notes = [' | '.join(state['failed'])] if not state['acceptable'] else []
        if dominated.get(name):
            notes.append('dominated by ' + ', '.join(dominated[name]))
        print(f"{name:<24} {metrics['fetches_per_day']:>5} "
              f"{_minutes(metrics['p50']):>7} {_minutes(metrics['p90']):>7} "
              f"{_minutes(metrics['p99']):>7} "
              f"{'—' if share is None else f'{share * 100:.2f}%':>7}  "
              f"{'OK' if not notes else ' | '.join(notes)}")


def _minutes(seconds):
    return '—' if seconds is None else f'{seconds / 60:.1f}m'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--observations', help='observation series JSON instead '
                                               'of the git revision history')
    parser.add_argument('--timetable', help='comma-separated local "HH:MM" list')
    parser.add_argument('--plan', help='preset plan name (see --table)')
    parser.add_argument('--tz', default=LOCAL_TZ)
    parser.add_argument('--split', choices=('all', 'train', 'test'), default='all')
    parser.add_argument('--fraction', type=float, default=0.6,
                        help='train share of --split train|test')
    parser.add_argument('--no-jitter', action='store_true',
                        help='disable the detection-quantisation correction')
    parser.add_argument('--seed', type=int, default=DEFAULT_SEED)
    parser.add_argument('--resolution-minutes', type=float,
                        default=DEFAULT_RESOLUTION_SECONDS / 60)
    parser.add_argument('--table', action='store_true',
                        help='evaluate every preset plan (the Pareto table)')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    presets = preset_timetables()
    if args.timetable:
        timetable = parse_label_list(args.timetable)
        problems = validate_timetable(timetable)
        if problems:
            for problem in problems:
                print(f'error: {problem}', file=sys.stderr)
            return 2
        name = 'custom'
    elif args.plan:
        if args.plan not in presets:
            print(f"error: unknown plan {args.plan!r}; known: "
                  + ', '.join(sorted(presets)), file=sys.stderr)
            return 2
        timetable = presets[args.plan]
        name = args.plan
    elif args.table:
        timetable = presets['busy20+night+midnight']
        name = 'busy20+night+midnight'
    else:
        print('error: pass --timetable, --plan or --table', file=sys.stderr)
        return 2

    observations = load_observations(root=args.root,
                                     observations_path=args.observations)
    window = build_window(observations)
    if args.split != 'all':
        train, test = split_window(window, fraction=args.fraction)
        window = train if args.split == 'train' else test

    jitter = not args.no_jitter
    kwargs = {'tz': args.tz, 'jitter': jitter, 'seed': args.seed,
              'resolution_seconds': args.resolution_minutes * 60}

    if args.table:
        metrics = {key: simulate_timetable(value, window, **kwargs)
                   for key, value in presets.items()}
        rows = sorted(metrics.items(), key=lambda row: (row[1]['p90'] is None,
                                                        row[1]['p90'] or 0))
        label = f"window={window['split'] or 'all'} jitter={jitter} " \
                f"({len(window['observations'])} observations, " \
                f"{len(window['events'])} re-prices)"
        if args.json:
            print(json.dumps({'window': label, 'plans': dict(rows),
                              'dominated_by': pareto_front(metrics)},
                             ensure_ascii=False, separators=(',', ':')))
        else:
            print_table(rows, label, dominated=pareto_front(metrics))
            blackout = metrics.get('night-blackout')
            recommended = metrics.get('busy20+night+midnight')
            if blackout and recommended:
                print(f"night-blackout vs recommended: "
                      f"{compare_plans(blackout, recommended)} "
                      f"(p99 {_minutes(blackout['p99'])} vs "
                      f"{_minutes(recommended['p99'])})")
        return 0

    metrics = simulate_timetable(timetable, window, **kwargs)
    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, separators=(',', ':')))
        return 0
    print_table([(name, metrics)], f"window={window['split'] or 'all'} "
                                   f"jitter={jitter}")
    return 0


if __name__ == '__main__':
    sys.exit(main())

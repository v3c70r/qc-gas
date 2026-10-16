#!/usr/bin/env python3
"""Explicit fetch timetable: greedy water-level allocation under gap caps.

Issue #91 replaced the "24 UTC hour slots + weights" plan with a *local*
timetable, because 4.1 days / 396 rounds of 15-minute probe observations
(issue #74) showed the hourly change volume spans **121x** — 486 re-priced
stations/day at local midnight, 4-23/day in the 01:00-05:00 dead zone — while
the dominant term of staleness is the *largest* gap, not the average density.
The measured Pareto table (docs/fetch-strategy.md) says the answer is a hybrid:

* 06:00-23:00 every 20 minutes,
* local 00:00 every 10 minutes (the daily batch),
* 01:00-05:00 one fetch per hour (**never zero**: stopping them doubles the
  time-weighted staleness and produces a ~200 min worst case),

i.e. 65 requests/day reaching p90 18 min / stale share 1.2 % on the probe
series, inside the published criterion (p90 <= 30 min, share <= 5 %).

This module owns the allocation, as a *greedy water-level* method:

* objective — minimise ``Σ_h E_h · gap_h / 2`` (expected staleness exposure),
  where ``E_h`` is the expected number of re-priced stations in local hour
  ``h`` and ``gap_h = 60 / n_h`` the poll spacing there;
* constraint — ``Σ_h n_h <= B`` (the daily budget) and ``gap_h <= G_h`` (the
  hard caps from :func:`fetch_strategy.gap_caps`);
* marginal gain of one more fetch in hour ``h`` — ``E_h / (n_h + 1)``, the
  discrete form of ``E_h · (gap_old - gap_new) / gap_old``;
* ``E_h`` is the **median across observed days** of exact per-station re-prices
  (the ``changed_stations_exact`` channel, issue #68) — never the legacy
  region-average ``changed_stations`` counter, whose "changes since the last
  observation" definition inflates as the sampling gets sparser.

The floor (one fetch per ``G_h`` minutes ⇒ ≥ 1 anywhere, 3 in the busy day,
6 at local midnight) costs exactly 65 requests/day, so at the default budget
the plan *is* the measured recommendation; extra budget is water-filled into
the busiest hours, and the epsilon-exploration share (#69) is spent on the
hours with the least accumulated observation time.
"""

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from interval_report import change_events  # noqa: E402  (shared loader/口径)
from metrics import parse_iso, to_iso  # noqa: E402
from fetch_strategy import (  # noqa: E402
    DEFAULT_DAILY_BUDGET,
    DEFAULT_EXPLORE_FRAC,
    LOCAL_TZ,
    TIMETABLE_GRANULARITY_MINUTES,
    exact_channel_ready,
    format_timetable,
    gap_caps,
    local_zone,
    max_gap_minutes,
    observed_hour_totals,
    parse_timetable,
    spread_offsets,
    timetable_utc_labels,
)
from zoneinfo import ZoneInfo  # noqa: E402,F401  (re-exported for callers/tests)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
SCHEDULE_FILE = 'fetch-schedule.json'
PROFILE_FILE = 'update-profile.json'

# Most requests an hour can hold on the 5-minute grid.
MAX_TIMETABLE_PER_HOUR = 60 // TIMETABLE_GRANULARITY_MINUTES
# Re-learn cadence: the timetable is rebuilt on every fetch, but this is the
# window after which a *full* offline re-learn (git-observation medians) is
# documented as due, so a drifted rhythm is picked up even when the live plan
# is only fed by the cheap profile counters.
RELEARN_AFTER_DAYS = 7
HOURS = 24


# ── Expected volume E_h (the data side) ──────────────────────────────────────

def expected_counts(observations, events=None, tz=LOCAL_TZ):
    """``([E_0..E_23], {hour: days_observed})`` from a probe observation series.

    ``E_h`` = **median across observed local days** of the number of stations
    that really re-priced during local hour ``h`` (exact per-station diffs of
    :func:`interval_report.change_events`, the same 口径 as the profile's
    ``changed_stations_exact``). A day only counts for hour ``h`` when that hour
    was actually sampled, so a sparsely covered hour is not dragged toward zero
    by days the probe slept through. The median (not the mean) is what makes the
    plan robust to the brand-wide repricing days (2026-10-15 15:00 alone saw
    1859 stations move, ~5x the usual volume).
    """
    zone = local_zone(tz)
    events = change_events(observations, {}) if events is None else events
    per_day = {h: {} for h in range(HOURS)}
    days = {h: set() for h in range(HOURS)}

    for obs in observations or ():
        local = obs['at'].astimezone(zone)
        days[local.hour].add(local.date())
    for event in events or ():
        local = event['at'].astimezone(zone)
        bucket = per_day[local.hour].setdefault(local.date(), 0)
        per_day[local.hour][local.date()] = bucket + 1

    counts = [0.0] * HOURS
    day_counts = {}
    for hour in range(HOURS):
        observed = sorted(days[hour])
        day_counts[hour] = len(observed)
        if not observed:
            continue
        counts[hour] = float(statistics.median(
            per_day[hour].get(day, 0) for day in observed))
    return counts, day_counts


def observed_days(observations, tz=LOCAL_TZ):
    """How many local days the observation series covers (sample-size guard)."""
    zone = local_zone(tz)
    days = {obs['at'].astimezone(zone).date() for obs in observations or ()}
    return len(days)


def utc_hours_to_local(values, tz=LOCAL_TZ, day=None):
    """Re-key a 24-length per-UTC-hour list onto local hours.

    The learned profile is accumulated per UTC hour, but the timetable is local
    (that is what makes it survive the DST switch). Each UTC hour is mapped
    through the local offset of its noon-equivalent instant, so the two are
    aligned for every day the province is not switching (weeks apart from two
    hours a year).
    """
    values = list(values or [])
    if not values:
        return []
    zone = local_zone(tz)
    if day is None:
        day = datetime.now(timezone.utc).astimezone(zone).date()
    out = [0.0] * HOURS
    for hour in range(HOURS):
        value = values[hour] if hour < len(values) else values[-1]
        local_hour = datetime(day.year, day.month, day.day, hour,
                              tzinfo=timezone.utc).astimezone(zone).hour
        out[local_hour] = float(value or 0.0)
    return out


def expected_counts_from_profile(profile, tz=LOCAL_TZ):
    """``E_h`` from ``data/update-profile.json``'s exact channel, or ``None``.

    Re-prices per hour from the counted ``changed_stations_exact`` counter only
    (issue #68/#91). Returns ``None`` until :func:`fetch_strategy.
    exact_channel_ready` reports the counted channel covers the day, and until
    at least one hour has exposure: a plan must never be built on the
    region-average counter, which is what inflated the old "1.65x" contrast.
    """
    if not exact_channel_ready(profile):
        return None
    rates = [0.0] * HOURS
    for hour in range(HOURS):
        rec = ((profile or {}).get('hours') or {}).get(str(hour)) or {}
        observed = float(rec.get('observed_hours') or 0.0)
        if observed <= 0:
            continue
        rates[hour] = max(0.0, float(rec.get('changed_stations_exact') or 0.0)) / observed
    if not any(rates):
        return None
    return utc_hours_to_local(rates, tz)


# ── Allocation ───────────────────────────────────────────────────────────────

def hourly_floor(caps=None, min_per_hour=1, max_per_hour=MAX_TIMETABLE_PER_HOUR):
    """``{hour: n}`` — the fewest fetches that keep every hour inside its cap.

    ``ceil(60 / cap)``: 1 at night (60 min), 3 in the busy day (20 min) and 6 at
    local midnight (10 min) — 65 requests/day in total, the measured optimum of
    issue #91.
    """
    caps = dict(caps or gap_caps())
    out = {}
    for hour in range(HOURS):
        cap = max(1, int(caps.get(hour, max_gap_minutes(hour))))
        need = -(-60 // cap)          # ceil
        out[hour] = max(int(min_per_hour), min(need, int(max_per_hour)))
    return out


def timetable_gaps(timetable):
    """``(worst_gap_by_hour, gaps)`` for a *circular* local day.

    A gap is attributed to the hour it starts in (``05:00 -> 06:00`` is the
    night hour's cadence, ``00:50 -> 01:00`` the midnight hour's), which is what
    the per-hour caps mean. Hours with no slot report ``None``.
    """
    marks = parse_timetable(timetable)
    worst = {hour: None for hour in range(HOURS)}
    gaps = []
    if not marks:
        return worst, gaps
    for index, (hour, minute) in enumerate(marks):
        next_hour, next_minute = marks[(index + 1) % len(marks)]
        start = hour * 60 + minute
        end = next_hour * 60 + next_minute
        if end <= start:
            end += 24 * 60
        gap = float(end - start)
        gaps.append((hour, gap))
        if worst[hour] is None or gap > worst[hour]:
            worst[hour] = gap
    return worst, gaps


def _water_fill(counts, expected, remaining, max_per_hour):
    """Spend ``remaining`` fetches one by one on the best marginal gain.

    ``gain_h ∝ E_h / (n_h + 1)``: the discrete form of
    ``E_h · (gap_old - gap_new) / gap_old``. Ties go to the emptiest hour and
    then the earliest one, so the plan stays deterministic *and* an hour never
    fills up while a colder one still has room (which matters when there is no
    volume signal at all).
    """
    while remaining > 0:
        candidates = [h for h in range(HOURS) if counts[h] < max_per_hour]
        if not candidates:
            break
        chosen = max(candidates, key=lambda h: (expected[h] / (counts[h] + 1),
                                                -counts[h], -h))
        counts[chosen] += 1
        remaining -= 1
    return counts


def _degrade(expected, budget, min_per_hour, max_per_hour):
    """Best effort when the budget cannot buy every hour's gap cap."""
    counts = [0] * HOURS
    if budget <= 0:
        return counts
    base = min(int(min_per_hour), budget // HOURS) if HOURS else 0
    counts = [base] * HOURS
    remaining = budget - base * HOURS
    if remaining > 0:
        # One fetch everywhere first (ranked by expected volume), then the
        # ordinary water-fill: the hours the day cannot afford come last.
        ranked = sorted(range(HOURS), key=lambda h: (-expected[h], h))
        for hour in ranked:
            if remaining <= 0:
                break
            if counts[hour] > 0:
                continue
            counts[hour] = 1
            remaining -= 1
        counts = _water_fill(counts, expected, remaining, max_per_hour)
    return counts


def _probe_slots(counts, offsets, observed_hours, budget, explore_frac):
    """Issue #69 probes: the slots of the coldest hours, inside the plan.

    Every hour of the shipped timetable is polled at least once (the night
    floor), so exploration is no longer about *reaching* an unsampled hour —
    it is about labelling the epsilon share of the plan that lands in the hours
    with the least accumulated observation time, so the audit log can attribute
    those polls to exploration. Only hours strictly below the median of
    ``observed_hours`` qualify: when everything is sampled evenly there is
    nothing cold to probe.
    """
    if not observed_hours or explore_frac is None:
        return []
    observed = [float(value or 0.0) for value in observed_hours]
    if len(observed) != HOURS:
        return []
    target = int(round(max(0.0, float(explore_frac)) * int(budget)))
    if target <= 0:
        return []
    median = statistics.median(observed)
    cold = sorted((h for h in range(HOURS) if observed[h] < median),
                  key=lambda h: (observed[h], h))
    out = []
    for hour in cold:
        for offset in offsets[hour]:
            if len(out) >= target:
                return sorted(out)
            out.append(f'{hour:02d}:{offset:02d}')
    return sorted(out)


def plan_timetable(expected=None, budget=DEFAULT_DAILY_BUDGET, caps=None,
                   min_per_hour=1, max_per_hour=MAX_TIMETABLE_PER_HOUR,
                   explore_frac=DEFAULT_EXPLORE_FRAC, observed_hours=None,
                   tz=LOCAL_TZ):
    """Build the local timetable for ``budget`` requests/day.

    ``expected`` is the 24-length E_h (local hours); ``None`` means "no measured
    signal yet" and only the gap floor is spent, which is exactly the shipped
    plan at the default budget. Returns the plan dict that
    :func:`plan_fields` turns into ``data/fetch-schedule.json`` fields.
    """
    budget = max(0, int(budget))
    max_per_hour = max(1, min(int(max_per_hour), MAX_TIMETABLE_PER_HOUR))
    min_per_hour = max(1, min(int(min_per_hour), max_per_hour))
    caps = caps or gap_caps()
    caps = {int(h): int(caps[h]) for h in caps}
    values = [max(0.0, float(v or 0.0)) for v in (expected or [])]
    values = (values + [0.0] * HOURS)[:HOURS]

    floor = hourly_floor(caps, min_per_hour, max_per_hour)
    floor_total = sum(floor.values())
    satisfied = floor_total <= budget

    if satisfied:
        counts = [floor[h] for h in range(HOURS)]
        _water_fill(counts, values, budget - floor_total, max_per_hour)
    else:
        counts = _degrade(values, budget, min_per_hour, max_per_hour)

    offsets = {h: spread_offsets(counts[h]) for h in range(HOURS)}
    timetable = format_timetable([(h, mark) for h in range(HOURS)
                                  for mark in offsets[h]])
    worst, gaps = timetable_gaps(timetable)
    violations = [{'hour': hour, 'gap_minutes': round(worst[hour], 3),
                   'max_gap_minutes': caps[hour]}
                  for hour in range(HOURS)
                  if worst[hour] is not None and worst[hour] > caps[hour] + 1e-9]
    max_gap = max((gap for _, gap in gaps), default=None)
    probes = _probe_slots(counts, offsets, observed_hours, budget, explore_frac)

    return {
        'timezone': tz,
        'min_per_hour': min_per_hour,
        'max_per_hour': max_per_hour,
        'timetable': timetable,
        'timetable_fetches_per_day': len(timetable),
        'hourly_counts': {str(h): counts[h] for h in range(HOURS)},
        'hourly_gap_minutes': {str(h): round(worst[h], 3) for h in range(HOURS)
                               if worst[h] is not None},
        'max_gap_minutes': {str(h): caps[h] for h in range(HOURS)},
        'max_gap_minutes_observed': max_gap,
        'constraints': {
            'satisfied': bool(satisfied and not violations),
            'budget_per_day': budget,
            'floor_per_day': floor_total,
            'slack': max(0, budget - floor_total),
            'shortfall': max(0, floor_total - budget),
            'min_gap_minutes': TIMETABLE_GRANULARITY_MINUTES,
            'violations': violations,
        },
        'explore_frac': round(float(explore_frac or 0.0), 4),
        'explore_count': len(probes),
        'explore_slots': probes,
    }


# ── Schedule document ────────────────────────────────────────────────────────

def plan_fields(plan, day=None, tz=LOCAL_TZ):
    """Timetable fields for ``data/fetch-schedule.json``.

    ``slots`` keeps its old meaning (the UTC labels the pre-#91 gate read) as
    the *projection* of the local timetable for ``day``; the gate prefers
    ``timetable`` + ``timezone`` so the local window survives a DST switch.
    """
    fields = {
        'v': 2,
        'timezone': plan.get('timezone') or tz,
        'min_per_hour': int(plan.get('min_per_hour', 1)),
        'max_per_hour': int(plan.get('max_per_hour', MAX_TIMETABLE_PER_HOUR)),
        'timetable': list(plan.get('timetable') or []),
        'timetable_fetches_per_day': plan.get('timetable_fetches_per_day', 0),
        'hourly_counts': plan.get('hourly_counts') or {},
        'hourly_gap_minutes': plan.get('hourly_gap_minutes') or {},
        'max_gap_minutes': plan.get('max_gap_minutes') or {},
        'constraints': plan.get('constraints') or {},
        'plan_relearn_after_days': RELEARN_AFTER_DAYS,
        'slots': timetable_utc_labels(plan.get('timetable') or [],
                                      plan.get('timezone') or tz, day),
        'explore_slots': list(plan.get('explore_slots') or []),
        'explore_count': int(plan.get('explore_count') or 0),
    }
    if 'explore_frac' in plan:
        fields['explore_frac'] = plan['explore_frac']
    return fields


def merge_schedule(schedule, *, plan=None, budget=DEFAULT_DAILY_BUDGET,
                   now=None, tz=LOCAL_TZ, **plan_kwargs):
    """Fold a timetable into a schedule document, keeping the legacy fields.

    ``slots`` is re-projected (they must agree with ``timetable``),
    ``allocated_per_day`` follows, and every other key the caller had
    (``hour_weights``, ``coverage_hours``, ``region_coverage``, ...) is left
    untouched, so consumers written before #91 keep working.
    """
    now = now or datetime.now(timezone.utc)
    if isinstance(now, str):
        now = parse_iso(now) or datetime.now(timezone.utc)
    day = now.astimezone(local_zone(tz)).date()
    if plan is None:
        plan = plan_timetable(budget=budget, tz=tz, **plan_kwargs)
    merged = dict(schedule or {})
    merged.update(plan_fields(plan, day=day, tz=tz))
    merged['budget_per_day'] = int(plan['constraints'].get('budget_per_day', budget))
    merged['allocated_per_day'] = len(merged['slots'])
    merged['generated_at'] = to_iso(now)
    merged['timetable_generated_at'] = to_iso(now)
    return merged


# ── CLI ──────────────────────────────────────────────────────────────────────

def learn(root=ROOT, data_dir=DEFAULT_DATA_DIR, observations_path=None,
          profile_path=None, tz=LOCAL_TZ):
    """``(expected, observed_hours, source)`` for the planner.

    Precedence: an explicit ``--observations`` series, then an explicit
    ``--profile``, then the git revision history of the probe's
    ``station-changes.json`` (cross-day medians), falling back to the committed
    profile when the repository has no history to replay. The profile path reads
    the counted ``changed_stations_exact`` counter: both sources are the exact
    channel, never the region-average one.
    """
    data_dir = Path(data_dir)
    if observations_path:
        import schedule_eval

        observations = schedule_eval.load_observations(
            root=str(root), observations_path=observations_path)
        if observations:
            expected, _ = expected_counts(observations, tz=tz)
            return expected, _observed_by_local_hour(observations, tz), 'observations'

    if profile_path:
        profile = _load_json(Path(profile_path))
        if profile:
            return (expected_counts_from_profile(profile, tz),
                    utc_hours_to_local(observed_hour_totals(profile), tz),
                    'profile')

    import schedule_eval  # local import: the evaluator is not needed to plan

    observations = schedule_eval.load_observations(root=str(root))
    if observations:
        expected, _ = expected_counts(observations, tz=tz)
        return expected, _observed_by_local_hour(observations, tz), 'observations'

    profile = _load_json(data_dir / PROFILE_FILE)
    if profile:
        return (expected_counts_from_profile(profile, tz),
                utc_hours_to_local(observed_hour_totals(profile), tz),
                'profile')
    return None, None, 'none'


def _observed_by_local_hour(observations, tz=LOCAL_TZ):
    """How many probe observations landed in each local hour of day."""
    zone = local_zone(tz)
    totals = [0.0] * HOURS
    for obs in observations or ():
        totals[obs['at'].astimezone(zone).hour] += 1.0
    return totals


def _load_json(path):
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=str(ROOT))
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--budget', type=int, default=DEFAULT_DAILY_BUDGET)
    parser.add_argument('--tz', default=LOCAL_TZ)
    parser.add_argument('--profile', help='learn E_h from this profile JSON '
                                          '(default: the git observation series, '
                                          'falling back to <data-dir>/update-profile.json)')
    parser.add_argument('--observations',
                        help='learn E_h from an observation series JSON instead '
                             'of the git revision history')
    parser.add_argument('--no-learn', action='store_true',
                        help='spend the gap floor only, without a volume signal')
    parser.add_argument('--explore-frac', type=float, default=DEFAULT_EXPLORE_FRAC)
    parser.add_argument('--schedule-path',
                        help=f'merge the timetable into this schedule file '
                             f'(default: none, print only)')
    parser.add_argument('--now', help='UTC ISO timestamp override (tests/logs)')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)

    now = parse_iso(args.now) or datetime.now(timezone.utc)
    expected, observed, source = (None, None, 'none')
    if not args.no_learn:
        expected, observed, source = learn(
            root=args.root, data_dir=args.data_dir,
            observations_path=args.observations,
            profile_path=args.profile, tz=args.tz)

    plan = plan_timetable(expected=expected, budget=args.budget, tz=args.tz,
                          explore_frac=args.explore_frac,
                          observed_hours=observed)
    plan['expected_counts'] = [round(v, 3) for v in (expected or [0.0] * HOURS)]
    plan['expected_source'] = source

    if args.schedule_path:
        path = Path(args.schedule_path)
        schedule = _load_json(path)
        if schedule is None:
            print(f'error: cannot read {path}', file=sys.stderr)
            return 1
        schedule = merge_schedule(schedule, plan=plan, now=now, tz=args.tz)
        path.write_text(json.dumps(schedule, ensure_ascii=False,
                                   separators=(',', ':')) + '\n', encoding='utf-8')

    if args.json:
        print(json.dumps(plan, ensure_ascii=False, separators=(',', ':')))
    else:
        constraints = plan['constraints']
        print(f"timetable: {plan['timetable_fetches_per_day']} fetches/day "
              f"(budget {constraints['budget_per_day']}, floor "
              f"{constraints['floor_per_day']}, E_h source {source})")
        print(f"gaps: max {plan['max_gap_minutes_observed']} min, "
              f"satisfied={constraints['satisfied']} "
              f"violations={len(constraints['violations'])}")
        print('timetable: ' + ' '.join(plan['timetable']))
        if plan['explore_slots']:
            print('explore: ' + ' '.join(plan['explore_slots']))
        if args.schedule_path:
            print(f'merged into {args.schedule_path}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

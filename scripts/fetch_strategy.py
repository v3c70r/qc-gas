#!/usr/bin/env python3
"""Adaptive gas-price fetch strategy — pure, dependency-free logic.

Why this exists
---------------
The data workflow used to poll the upstream Régie Essence snapshot on a fixed
hourly interval. That is both too eager (24 downloads/day even on days when the
source never changes) and too late (a station that re-priced at 13:15 is only
seen on the next hourly run). This module learns *when* Québec stations actually
change their prices and turns that into a bounded, data-driven poll schedule.

How it works
------------
1. Every successful fetch records, per UTC hour of day:
   * ``samples``          — how many fetches happened in that hour
   * ``observed_hours``   — how much wall-clock time those fetches cover
                            (gap since the previous fetch, capped)
   * ``change_events``    — fetches that saw at least one real price change
   * ``changed_stations`` — stations living in regions whose average moved
   The change rate ``changed_stations / observed_hours`` is an unbiased
   "how many stations re-price per hour at this hour of day" estimate: it does
   not drift when sampling density changes.
2. ``hour_weights`` blends that learned rate with a research-seeded prior
   (see ``RESEARCH_PRIOR``) whose influence decays as observation-hours pile up.
3. ``plan_schedule`` converts the weights into concrete quarter-hour UTC poll
   slots under a hard daily request budget, reserving an epsilon fraction
   (``explore_frac``, issue #69) for the hours with the least observation time
   so the learner can escape its prior and keep discovering new windows.
4. ``due_decision`` is the workflow gate: it answers "should this run download
   right now?" using the schedule plus a freshness safety net.

Everything here is pure and unit-tested (`scripts/test_fetch_strategy.py`).
The only environment touch is :func:`env_int`, which resolves the shared
``FETCH_DAILY_BUDGET`` / ``FETCH_MAX_AGE_MINUTES`` overrides (issue #67).
"""

import os
from datetime import datetime, timedelta, timezone

SLOT_MINUTES = 15
SLOTS_PER_HOUR = 60 // SLOT_MINUTES          # 4 quarter-hour marks
SLOTS_PER_DAY = 24 * SLOTS_PER_HOUR          # 96 slots

# Max upstream downloads per day. The upstream Régie Essence snapshot is
# regenerated every 5 minutes on the quarter-hour grid (measured on
# 2026-10-11: metadata.generated_at went 04:45:01 -> 04:50:01 -> 04:55:01), and
# ``static.yml`` wakes this workflow every 15 minutes, so the whole day is 96
# wake-ups and one request per wake-up costs no extra upstream load per unit of
# freshness: the budget now matches the wake-up cadence (issue #67). It was 36
# (24 hourly floor + 12 learned-window extras), which left ~40-minute gaps and
# was the dominant cause of stale station prices (~24.5% of stations re-price
# every day).
DEFAULT_DAILY_BUDGET = 96
# Freshness safety net: force a poll when the last one is older than this, so a
# broken/empty schedule can never leave the data more than one poll cycle stale.
DEFAULT_MAX_AGE_MINUTES = 90
DEFAULT_MAX_GAP_HOURS = 2.0                  # exposure cap for one observation
DEFAULT_PRIOR_HOURS = 168.0                  # one week of observed hours

# Issue #69: epsilon-greedy sampling. A fraction of the daily budget is spent
# on the hours with the least observation time instead of the learned peaks,
# so a slot that never gets sampled (03:00/08:00 had ``observed_hours == 0``)
# can still accumulate evidence and stop being a permanent prior artifact.
DEFAULT_EXPLORE_FRAC = 0.15

# Environment overrides read by *both* CLIs (``update_profile.py`` writes the
# schedule, ``should_fetch.py`` gates on it) so the workflow has a single
# source of truth and the two scripts can never disagree.
BUDGET_ENV_VAR = 'FETCH_DAILY_BUDGET'
MAX_AGE_ENV_VAR = 'FETCH_MAX_AGE_MINUTES'
EXPLORE_FRAC_ENV_VAR = 'FETCH_EXPLORE_FRAC'


def env_int(name, default, environ=None):
    """Positive integer from ``name`` in ``environ``, else ``default``.

    Unset, non-numeric or non-positive values fall back to the default so a
    typo in the workflow env can never disable fetching entirely.
    """
    raw = (os.environ if environ is None else environ).get(name)
    if raw is None or str(raw).strip() == '':
        return int(default)
    try:
        value = int(str(raw).strip())
    except ValueError:
        return int(default)
    return value if value > 0 else int(default)


def env_float(name, default, environ=None):
    """Fraction in ``[0, 1]`` from ``name`` in ``environ``, else ``default``.

    Unlike :func:`env_int` an explicit ``0`` is honoured (it switches
    exploration off, issue #69); only unset, non-numeric or out-of-range
    values fall back so a typo cannot silently change the sampling strategy.
    """
    raw = (os.environ if environ is None else environ).get(name)
    if raw is None or str(raw).strip() == '':
        return float(default)
    try:
        value = float(str(raw).strip())
    except ValueError:
        return float(default)
    return value if 0.0 <= value <= 1.0 else float(default)

# Order in which the quarter-hour marks of an hour are used, coarsest spread
# first: one slot -> :00, two -> :00/:30, three -> :00/:30/:15, four -> every mark.
SLOT_MARK_PRIORITY = (0, 30, 15, 45)

# Order exploration probes the marks of an hour: start with the quarter-hour
# offset the exploitation plan does not already use, so a delayed run still
# lands within the grace window of a slot (issue #69).
EXPLORE_MARK_PRIORITY = (15, 30, 45, 0)

# ── Research-seeded prior ────────────────────────────────────────────────────
# Initial windows taken from public discussions of Canadian/Québec fuel-price
# behaviour (the issue explicitly allows seeding from the web):
#   * Régie de l'énergie floor prices are published every Wednesday morning and
#     take effect Thursday at 00:01; large jumps land Wednesday evening or
#     Thursday morning.                     (gasquebec.ca/en/cities)
#   * Stations re-set their price around local midnight and then "float down"
#     through the day; the cheapest moment is late at night / early morning.
#                            (insidehalton.com "best time to buy gas", insauga.com)
#   * Morning commute increases are the other well-known cluster.
# Montréal is UTC-4 (EDT) / UTC-5 (EST), so local evening/midnight windows land
# in the late UTC hours and the early UTC morning; the morning commute lands
# around 10:00-14:00 UTC. The prior only biases the first days — real
# observations quickly dominate (see ``hour_weights``).
RESEARCH_PRIOR = {
    0: 2.0, 1: 2.0,            # ~20:00-21:00 local, Wednesday publication drift
    4: 3.0, 5: 3.0, 6: 2.0,    # ~00:00-02:00 local, nightly price re-set
    10: 2.0, 11: 3.0, 12: 3.0, 13: 3.0,   # ~06:00-09:00 local, commute increase
    21: 1.5, 22: 2.0, 23: 2.0,  # local evening, ahead of the Thursday reset
}
PRIOR_BASELINE = 1.0

FUEL_KEYS = ('regular', 'super', 'diesel')


# ── Time / slot helpers ──────────────────────────────────────────────────────

def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def to_iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def slot_index(dt):
    """Map a datetime to its 0..95 quarter-hour UTC slot of the day."""
    utc = dt.astimezone(timezone.utc)
    return utc.hour * SLOTS_PER_HOUR + utc.minute // SLOT_MINUTES


def slot_label(index):
    """"HH:MM" UTC label for a slot index."""
    hour, mark = divmod(int(index), SLOTS_PER_HOUR)
    return f'{hour:02d}:{mark * SLOT_MINUTES:02d}'


def slot_datetimes(day, slots):
    """Concrete UTC datetimes for a daily slot list on ``day`` (date)."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    out = []
    for label in slots:
        try:
            hour, minute = (int(p) for p in str(label).split(':'))
        except (ValueError, AttributeError):
            continue
        out.append(start + timedelta(hours=hour, minutes=minute))
    return out


# ── Observation / diffing ────────────────────────────────────────────────────

def observed_from_stations(stations):
    """Region -> {fuel: average price} from the real snapshot."""
    sums = {}
    for feat in (stations or {}).get('features') or []:
        props = feat.get('properties') or {}
        region = props.get('region') or 'Inconnue'
        rec = sums.setdefault(region, {})
        for fuel in FUEL_KEYS:
            value = props.get(fuel + '_price')
            if value is None:
                continue
            agg = rec.setdefault(fuel, [0.0, 0])
            agg[0] += float(value)
            agg[1] += 1
    return {
        region: {fuel: round(total / count, 1) for fuel, (total, count) in fuels.items()}
        for region, fuels in sums.items()
    }


def region_station_counts(stations):
    """Region -> number of stations in the snapshot (any priced fuel)."""
    counts = {}
    for feat in (stations or {}).get('features') or []:
        props = feat.get('properties') or {}
        region = props.get('region') or 'Inconnue'
        if any(props.get(fuel + '_price') is not None for fuel in FUEL_KEYS):
            counts[region] = counts.get(region, 0) + 1
    return counts


# Prices are stored with one decimal; half a tenth is noise, not a re-price.
PRICE_EPSILON = 0.05

# Short fuel keys used by the station stores (`g`/`s`/`d`, same as the region
# history tiers). The order of this tuple is the order of a stored price row.
STATION_FUELS = ('g', 's', 'd')
STATION_FUEL_NAMES = {'g': 'regular', 's': 'super', 'd': 'diesel'}


def diff_regions(previous, current):
    """Regions (and fuels) whose observed average moved since the last fetch."""
    changed = {}
    for region, fuels in (current or {}).items():
        before = (previous or {}).get(region) or {}
        for fuel, value in fuels.items():
            old = before.get(fuel)
            if old is None or abs(float(value) - float(old)) >= PRICE_EPSILON:
                changed.setdefault(region, []).append(fuel)
    return changed


def station_coord_key(feat):
    """Stable per-station key: ``"lng,lat"`` rounded to 5 decimals.

    The upstream payload has no station id, so coordinates are the only stable
    handle. Five decimals (~1 m) has been the de-facto key of
    ``data/history/station-history.json`` and loses only 0.61% of keys over
    30 days — far less than a name/address key would.
    """
    coords = (feat or {}).get('geometry') or {}
    coords = coords.get('coordinates') if isinstance(coords, dict) else None
    if not coords or len(coords) < 2:
        return None
    try:
        return f"{float(coords[0]):.5f},{float(coords[1]):.5f}"
    except (TypeError, ValueError):
        return None


def station_prices(stations):
    """Coordinate key -> {short fuel: price} for every priced station.

    Unlike :func:`observed_from_stations` this keeps one entry *per station*,
    so a +5c move on one pump is never cancelled out by a -5c move on its
    neighbour inside the same region average.
    """
    out = {}
    for feat in (stations or {}).get('features') or []:
        props = (feat or {}).get('properties') or {}
        key = station_coord_key(feat)
        if not key:
            continue
        rec = {}
        for short in STATION_FUELS:
            value = props.get(STATION_FUEL_NAMES[short] + '_price')
            if value is not None:
                rec[short] = round(float(value), 1)
        if rec:
            out[key] = rec
    return out


def prices_to_row(prices):
    """Compact ``[g, s, d]`` row (``None`` for a fuel the station did not report)."""
    prices = prices or {}
    return [prices.get(fuel) for fuel in STATION_FUELS]


def prices_from_row(row):
    """Inverse of :func:`prices_to_row`; drops ``None`` fuels."""
    return {fuel: value for fuel, value in zip(STATION_FUELS, row or []) if value is not None}


def diff_stations(previous, current):
    """Stations (and fuels) whose *own* price moved since the last snapshot.

    ``previous`` / ``current`` are ``{coord_key: {short fuel: price}}`` maps
    (:func:`station_prices`). Returns ``{coord_key: [changed fuels]}``.

    Rules:
    * a fuel counts as changed when both sides have a value and they differ by
      at least ``PRICE_EPSILON`` (same noise threshold as the region diff);
    * a station with no previous entry has no baseline -> not a change;
    * a fuel appearing or disappearing is an availability change, not a
      re-price -> not a change;
    * a station missing from ``current`` (vanished from the feed) is not a
      change either; it is only pruned once it stops being seen.
    """
    changed = {}
    for key, fuels in (current or {}).items():
        before = (previous or {}).get(key)
        if not before:
            continue
        moved = []
        for fuel in STATION_FUELS:
            new = (fuels or {}).get(fuel)
            old = before.get(fuel)
            if new is None or old is None:
                continue
            if abs(float(new) - float(old)) >= PRICE_EPSILON:
                moved.append(fuel)
        if moved:
            changed[key] = moved
    return changed


def _ensure_hours(profile):
    hours = profile.setdefault('hours', {})
    for h in range(24):
        rec = hours.setdefault(str(h), {})
        rec.setdefault('samples', 0)
        rec.setdefault('observed_hours', 0.0)
        rec.setdefault('change_events', 0)
        rec.setdefault('changed_stations', 0)
        rec.setdefault('changed_stations_exact', 0)
        rec.setdefault('stations_observed', 0)
    return hours


def record_observation(profile, *, observed, region_counts, now, previous_at=None,
                       src=None, max_gap_hours=DEFAULT_MAX_GAP_HOURS,
                       station_changes=None, stations_observed=None):
    """Fold one real fetch into the learnable change-time profile.

    Returns ``(profile, summary)``. The change is attributed to the *fetch*
    hour, matching the ``observed_hours`` exposure accounting, so the rate
    estimate stays consistent even when the sampler is denser at some hours.
    ``previous_at`` is the timestamp of the previous *observation* (not the
    previous download attempt), which is what the exposure gap must measure.

    ``station_changes`` (exact per-station re-prices) and ``stations_observed``
    are the station-level calibration of the old, region-average-based
    ``changed_stations`` estimate. They are recorded side by side so the exact
    counts stay measurable; the schedule keeps reading the region-level fields
    until they have been shown to be a better predictor.
    """
    hours = _ensure_hours(profile)
    hour = now.astimezone(timezone.utc).hour
    rec = hours[str(hour)]

    if previous_at is None:
        previous_at = parse_iso(profile.get('last_observed_at'))
    gap = 0.0
    if previous_at is not None:
        gap = (now - previous_at).total_seconds() / 3600.0
        gap = max(0.0, min(gap, float(max_gap_hours)))

    previous = profile.get('last_observed')
    changed = {} if not previous else diff_regions(previous, observed)
    changed_stations = sum(int((region_counts or {}).get(r, 0)) for r in changed)

    rec['samples'] += 1
    rec['observed_hours'] = round(rec['observed_hours'] + gap, 6)
    if changed:
        rec['change_events'] += 1
        rec['changed_stations'] += changed_stations

    exact = None if station_changes is None else int(station_changes)
    if exact is not None:
        rec['changed_stations_exact'] = rec.get('changed_stations_exact', 0) + exact
    if stations_observed is not None:
        rec['stations_observed'] = int(stations_observed)

    profile['last_observed'] = observed
    profile['last_observed_at'] = to_iso(now)
    if src:
        profile['last_src'] = str(src)
    profile['updated_at'] = to_iso(now)

    summary = {
        'hour': hour,
        'gap_hours': round(gap, 3),
        'samples': rec['samples'],
        'changed_regions': sorted(changed),
        'changed_stations': changed_stations,
        'changed_stations_exact': exact if exact is not None else 0,
    }
    return profile, summary


# ── Weights ──────────────────────────────────────────────────────────────────

def research_prior_weights():
    """Normalized 24-length prior distribution seeded from public research."""
    raw = [RESEARCH_PRIOR.get(h, PRIOR_BASELINE) for h in range(24)]
    total = sum(raw)
    return [r / total for r in raw]


def hour_weights(profile, prior_alpha=DEFAULT_PRIOR_HOURS):
    """Blend learned per-hour change rates with the research prior.

    ``prior_alpha`` is expressed in observed *hours*: after a week of hourly
    coverage the prior and the data weigh the same, and the prior keeps fading.
    """
    prior = research_prior_weights()
    hours = (profile or {}).get('hours') or {}

    rates = [0.0] * 24
    observed_total = 0.0
    for h in range(24):
        rec = hours.get(str(h)) or {}
        observed_hours = max(0.0, float(rec.get('observed_hours') or 0.0))
        changed = max(0.0, float(rec.get('changed_stations') or 0.0))
        observed_total += observed_hours
        rates[h] = changed / observed_hours if observed_hours > 0 else 0.0

    rate_sum = sum(rates)
    if rate_sum <= 0:
        # Nothing learned yet (or no change ever seen): trust the research prior.
        return prior

    rate_norm = [r / rate_sum for r in rates]
    prior_weight = prior_alpha / (prior_alpha + observed_total)
    weights = [
        (1.0 - prior_weight) * rate_norm[h] + prior_weight * prior[h]
        for h in range(24)
    ]
    total = sum(weights)
    return [w / total for w in weights] if total > 0 else prior


# ── Scheduling ───────────────────────────────────────────────────────────────

def observed_hour_totals(profile):
    """24-length list of ``observed_hours`` from a learned profile.

    This is the evidence an hour has been *sampled* at all; exploration probes
    the hours where it is smallest (issue #69). A missing/empty profile is all
    zeros, which is exactly "probe everywhere cold".
    """
    hours = (profile or {}).get('hours') or {}
    return [
        max(0.0, float((hours.get(str(h)) or {}).get('observed_hours') or 0.0))
        for h in range(24)
    ]


def _exploit_slots(weights, budget, min_per_hour, max_per_hour):
    """Legacy water-fill: coverage floor, then learn the busiest hours."""
    hours = len(weights)
    base = min(int(min_per_hour), budget // hours) if hours else 0
    counts = [base] * hours
    remaining = budget - base * hours

    while remaining > 0:
        candidates = [h for h in range(hours) if counts[h] < max_per_hour]
        if not candidates:
            break
        chosen = max(candidates, key=lambda h: (weights[h] / (counts[h] + 1), -h))
        counts[chosen] += 1
        remaining -= 1

    slots = []
    for h in range(hours):
        for mark in SLOT_MARK_PRIORITY[:counts[h]]:
            slots.append(h * SLOTS_PER_HOUR + mark // SLOT_MINUTES)
    return sorted(slots)


def _free_marks(hour, used, max_per_hour):
    """Quarter-hour marks of ``hour`` not already scheduled and within cap."""
    start = hour * SLOTS_PER_HOUR
    if sum(1 for s in used if s // SLOTS_PER_HOUR == hour) >= max_per_hour:
        return []
    return [mark for mark in EXPLORE_MARK_PRIORITY
            if start + mark // SLOT_MINUTES not in used]


def _pick_explore_slots(weights, observed_hours, used, count, max_per_hour):
    """Pick ``count`` probe slots in the least-observed hours.

    Hours are ranked by (observation time, number of probe slots already
    there, weight, index): every probe goes to the least-observed hour that
    still has a free mark, and only once those are exhausted does exploration
    move on to the next-least-observed hour. Marks come from
    :data:`EXPLORE_MARK_PRIORITY` and can never collide with an exploitation
    slot, so a probe is always *extra* coverage of that hour rather than a
    rename of an existing one.
    """
    hours = len(weights)
    used = set(used)
    probes = [0] * hours
    out = []
    for _ in range(count):
        best = None
        for h in range(hours):
            marks = _free_marks(h, used, max_per_hour)
            if not marks:
                continue
            key = (observed_hours[h], probes[h], weights[h], h)
            if best is None or key < best[0]:
                best = (key, h, marks[0])
        if best is None:
            break
        _, hour, mark = best
        index = hour * SLOTS_PER_HOUR + mark // SLOT_MINUTES
        used.add(index)
        probes[hour] += 1
        out.append(index)
    return out


def plan_schedule(weights, budget=DEFAULT_DAILY_BUDGET, min_per_hour=1,
                  max_per_hour=SLOTS_PER_HOUR, observed_hours=None,
                  explore_frac=DEFAULT_EXPLORE_FRAC):
    """Plan the day and say which slots are exploration probes (issue #69).

    Returns ``(slots, explore_slots)``. ``slots`` is the same bounded daily
    plan as before; ``explore_slots`` is its subset reserved for the hours with
    the least ``observed_hours``. Exploration is opt-in on evidence:
    ``observed_hours=None`` (no learned profile yet, e.g. the research-prior
    fallback) or ``explore_frac=0`` reproduces the previous plan exactly.
    """
    hours = len(weights)
    if hours == 0:
        return [], []
    max_per_hour = max(1, min(int(max_per_hour), SLOTS_PER_HOUR))
    budget = max(0, min(int(budget), hours * max_per_hour))

    frac = 0.0 if explore_frac is None else max(0.0, min(1.0, float(explore_frac)))
    explore_budget = 0
    if observed_hours is not None and frac > 0 and budget > 0:
        explore_budget = int(round(budget * frac))
        # Never spend more probes than the day can hold one-per-mark. Keep the
        # exploitation floor intact too: at a budget that only covers
        # ``min_per_hour`` there is no slack left to probe with.
        floor_plan = min(int(min_per_hour), budget // hours) * hours
        explore_budget = max(0, min(explore_budget, hours * max_per_hour,
                                    budget - floor_plan))

    exploit = _exploit_slots(weights, budget - explore_budget, min_per_hour, max_per_hour)
    explore = []
    if explore_budget > 0:
        explore = _pick_explore_slots(weights, observed_hours, exploit,
                                      explore_budget, max_per_hour)
    return sorted(exploit + explore), sorted(explore)


def plan_slots(weights, budget=DEFAULT_DAILY_BUDGET, min_per_hour=1,
               max_per_hour=SLOTS_PER_HOUR):
    """Pick quarter-hour UTC slots for the day under a daily request budget.

    Every hour keeps ``min_per_hour`` slots when the budget allows, so the
    learner keeps unbiased coverage; the remaining budget is water-filled into
    the hours that historically produce the most price changes.
    """
    return plan_schedule(weights, budget=budget, min_per_hour=min_per_hour,
                         max_per_hour=max_per_hour)[0]


def slot_labels(slots):
    return [slot_label(s) for s in slots]


# ── Workflow gate ────────────────────────────────────────────────────────────

def due_decision(now, slots, last_fetch_at, *, min_gap_minutes=10,
                 max_age_minutes=DEFAULT_MAX_AGE_MINUTES, grace_minutes=45,
                 explore_slots=None):
    """Should this workflow run poll upstream right now?

    ``slots`` is a daily list of "HH:MM" UTC labels. The decision:

    1. no previous fetch            -> fetch (bootstrap)
    2. fetched less than min_gap ago -> skip (overlapping triggers)
    3. a scheduled slot fell in (last_fetch, now] within ``grace_minutes``
                                    -> fetch
    4. nothing for ``max_age_minutes`` -> fetch (freshness safety net)
    5. otherwise                    -> skip until the next slot
    """
    last_fetch_at = parse_iso(last_fetch_at) if isinstance(last_fetch_at, str) else last_fetch_at

    if last_fetch_at is None:
        return True, 'bootstrap: no previous fetch recorded'

    now = now.astimezone(timezone.utc)
    last_fetch_at = last_fetch_at.astimezone(timezone.utc)
    age_minutes = (now - last_fetch_at).total_seconds() / 60.0

    if age_minutes < 0:
        # Our own state file is the only writer; a future timestamp means the
        # clock/state is off. Fail open — freshness beats a wedged gate.
        return True, 'clock skew: last fetch is in the future; fetching'

    if age_minutes < min_gap_minutes:
        return False, (f'min gap: fetched {age_minutes:.0f} min ago '
                       f'(< {min_gap_minutes} min)')

    candidates = []
    for day_offset in (0, -1):
        candidates.extend(slot_datetimes((now + timedelta(days=day_offset)).date(), slots))

    served = [t for t in candidates
              if last_fetch_at < t <= now and (now - t).total_seconds() / 60.0 <= grace_minutes]
    if served:
        latest = max(served)
        lag = (now - latest).total_seconds() / 60.0
        label = latest.strftime('%H:%M')
        probes = {str(s) for s in (explore_slots or ())}
        tag = ' [explore]' if label in probes else ''
        return True, f'slot {label}{tag} due ({lag:.0f} min ago)'

    if age_minutes > max_age_minutes:
        return True, (f'stale: last fetch {age_minutes:.0f} min ago '
                      f'(> {max_age_minutes} min)')

    nxt = sorted(t for t in candidates if t > now)
    if nxt:
        return False, f'not due: next slot {nxt[0].strftime("%H:%M")} UTC'
    return False, 'not due: no slots scheduled'

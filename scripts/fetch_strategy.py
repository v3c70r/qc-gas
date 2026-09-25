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
3. ``plan_slots`` converts the weights into concrete quarter-hour UTC poll
   slots under a hard daily request budget, so upstream is never hammered.
4. ``due_decision`` is the workflow gate: it answers "should this run download
   right now?" using the schedule plus a freshness safety net.

Everything here is pure and unit-tested (`scripts/test_fetch_strategy.py`).
"""

from datetime import datetime, timedelta, timezone

SLOT_MINUTES = 15
SLOTS_PER_HOUR = 60 // SLOT_MINUTES          # 4 quarter-hour marks
SLOTS_PER_DAY = 24 * SLOTS_PER_HOUR          # 96 slots
DEFAULT_DAILY_BUDGET = 36                    # max upstream downloads per day
                                             # (24 hourly floor + 12 learned-window extras)
DEFAULT_MAX_GAP_HOURS = 2.0                  # exposure cap for one observation
DEFAULT_PRIOR_HOURS = 168.0                  # one week of observed hours

# Order in which the quarter-hour marks of an hour are used, coarsest spread
# first: one slot -> :00, two -> :00/:30, three -> :00/:30/:15, four -> every mark.
SLOT_MARK_PRIORITY = (0, 30, 15, 45)

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


def _ensure_hours(profile):
    hours = profile.setdefault('hours', {})
    for h in range(24):
        rec = hours.setdefault(str(h), {})
        rec.setdefault('samples', 0)
        rec.setdefault('observed_hours', 0.0)
        rec.setdefault('change_events', 0)
        rec.setdefault('changed_stations', 0)
    return hours


def record_observation(profile, *, observed, region_counts, now, previous_at=None,
                       src=None, max_gap_hours=DEFAULT_MAX_GAP_HOURS):
    """Fold one real fetch into the learnable change-time profile.

    Returns ``(profile, summary)``. The change is attributed to the *fetch*
    hour, matching the ``observed_hours`` exposure accounting, so the rate
    estimate stays consistent even when the sampler is denser at some hours.
    ``previous_at`` is the timestamp of the previous *observation* (not the
    previous download attempt), which is what the exposure gap must measure.
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

def plan_slots(weights, budget=DEFAULT_DAILY_BUDGET, min_per_hour=1,
               max_per_hour=SLOTS_PER_HOUR):
    """Pick quarter-hour UTC slots for the day under a daily request budget.

    Every hour keeps ``min_per_hour`` slots when the budget allows, so the
    learner keeps unbiased coverage; the remaining budget is water-filled into
    the hours that historically produce the most price changes.
    """
    hours = len(weights)
    if hours == 0:
        return []
    max_per_hour = max(1, min(int(max_per_hour), SLOTS_PER_HOUR))
    budget = max(0, min(int(budget), hours * max_per_hour))

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


def slot_labels(slots):
    return [slot_label(s) for s in slots]


# ── Workflow gate ────────────────────────────────────────────────────────────

def due_decision(now, slots, last_fetch_at, *, min_gap_minutes=10,
                 max_age_minutes=180, grace_minutes=45):
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
        return True, f'slot {latest.strftime("%H:%M")} due ({lag:.0f} min ago)'

    if age_minutes > max_age_minutes:
        return True, (f'stale: last fetch {age_minutes:.0f} min ago '
                      f'(> {max_age_minutes} min)')

    nxt = sorted(t for t in candidates if t > now)
    if nxt:
        return False, f'not due: next slot {nxt[0].strftime("%H:%M")} UTC'
    return False, 'not due: no slots scheduled'

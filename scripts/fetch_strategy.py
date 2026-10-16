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
from zoneinfo import ZoneInfo

SLOT_MINUTES = 15
SLOTS_PER_HOUR = 60 // SLOT_MINUTES          # 4 quarter-hour marks
SLOTS_PER_DAY = 24 * SLOTS_PER_HOUR          # 96 slots

# ── Local timetable (issue #91) ──────────────────────────────────────────────
# The plan used to be a set of *UTC* hours, which quietly moved the local
# window every time the province switched between EDT and EST. The plan is now
# an explicit list of *local* times ("HH:MM", 5-minute granularity) in
# ``LOCAL_TZ``; :func:`due_decision` and the schedule writer convert it per day,
# so a 06:00 slot stays 06:00 after the November switch.
LOCAL_TZ = 'America/Toronto'
TIMETABLE_GRANULARITY_MINUTES = 5
# Hard per-hour gap caps of the recommended timetable (issue #91): the busy day
# is polled at most every 20 min, local midnight (where the daily batch lands)
# every 10 min, and 01:00-05:00 every 60 min. The night floor is *not* optional:
# switching those five fetches off doubles the time-weighted staleness and puts
# a ~200 min worst case on a 4 % slice of the day.
DEFAULT_MAX_GAP_MINUTES = 10
DEFAULT_NIGHT_GAP_MINUTES = 60
DEFAULT_DAY_GAP_MINUTES = 20
MIDNIGHT_HOUR = 0
NIGHT_FIRST_HOUR = 1
NIGHT_LAST_HOUR = 5

# Max upstream downloads per day *from the scheduled plan*. The upstream Régie
# Essence snapshot is regenerated every 5 minutes (measured on 2026-10-11:
# metadata.generated_at went 04:45:01 -> 04:50:01 -> 04:55:01) and issue #91
# re-measured the *when*: after 4.1 days / 396 probe rounds the per-local-hour
# change volume spans 4 (01:00-05:00) to 486 (00:00) re-priced stations per
# day, i.e. a 121x dynamic range. The recommended hybrid timetable spends 65
# requests/day (busy 20 min + midnight 10 min + night 1/h) and reaches p90 18
# min / stale share 1.2 % on the measured series, inside the published
# criterion (p90 <= 30 min, share <= 5 %) - see docs/fetch-strategy.md.
# GitHub throttles our `schedule` so hard that sub-hourly crons buy nothing
# (measured over 15 days, issue #77: `*/15` ran every ~4.3 h), so this budget is
# only *executed* in full thanks to the external `repository_dispatch` trigger;
# the hourly fallback cron still catches up through the freshness net below.
DEFAULT_DAILY_BUDGET = 65
# Freshness safety net: force a poll when the last one is older than this. Two
# hourly wake-ups = the net only fires after two consecutive wakes were dropped,
# so it can neither be denser than the plan nor sparser than it (issue #77).
DEFAULT_MAX_AGE_MINUTES = 120
DEFAULT_MAX_GAP_HOURS = 2.0                  # exposure cap for one observation
DEFAULT_PRIOR_HOURS = 168.0                  # one week of observed hours

# Issue #69: epsilon-greedy sampling. A fraction of the daily budget is spent
# on the hours with the least observation time instead of the learned peaks,
# so a slot that never gets sampled (03:00/08:00 had ``observed_hours == 0``)
# can still accumulate evidence and stop being a permanent prior artifact.
DEFAULT_EXPLORE_FRAC = 0.15

# ── Layered learning (issue #71) ─────────────────────────────────────────────
# One global 24-hour distribution averages regions and brands together. Measured
# on the live profile, the per-hour spread of the global signal was only
# 745-1235 changed stations (1.65x) - barely above noise, which is what you get
# when Montréal, Laval and Gaspésie are averaged into one curve. What lives here
# instead:
#
# * ``hour_rates(channel='exact')`` switches the weighting to the *number* of
#   stations that re-priced (``changed_stations_exact``, issue #68) once the
#   exact counter covers the day. The legacy region-average estimate saturates,
#   because a region counts in full as soon as any of its fuel averages moved
#   (in a 6h replay of the real history that made nearly every region "move" in
#   nearly every bucket: 1.14x spread).
# * per-region hourly counters (:func:`record_observation`) with hierarchical
#   shrinkage: a region with little exposure falls back to the global weights
#   (:data:`REGION_PRIOR_HOURS` is its prior strength, in observed hours).
# * a coverage constraint (:func:`coverage_hours`) that pins each region's
#   distinctive hours, so a reduced budget cannot starve a remote region.
# * per-quarter-hour counters and :func:`learned_mark_orders`, which pick the
#   busiest :00/:15/:30/:45 mark of an hour instead of the coarsest one.
REGION_PRIOR_HOURS = 24.0                    # one day of regional exposure
REGION_PEAK_HOURS = 2                        # peak hours pinned per region
REGION_PEAK_RATIO = 1.25                     # ... only if they beat the province by this
COVERAGE_BUDGET_SHARE = 0.5                  # ... and never more than this share of the plan
EXACT_READY_SAMPLES = 48                     # exact-channel fetches before the switch
MARK_MIN_SAMPLES = 32                        # fetches in an hour before :15 marks count

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

# The same order as in-hour slot offsets (0..3), used by ``_exploit_slots``
# whenever a learned mark order (:func:`learned_mark_orders`) has nothing to
# say about that hour.
DEFAULT_MARK_ORDER = tuple(mark // SLOT_MINUTES for mark in SLOT_MARK_PRIORITY)

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


# ── Local timetable helpers (issue #91) ──────────────────────────────────────

def local_zone(tz=LOCAL_TZ):
    """``ZoneInfo`` for ``tz``, falling back to UTC for an unknown name.

    A typo in the schedule must never wedge the gate: an unknown zone degrades
    to UTC (the pre-#91 behaviour) instead of raising.
    """
    if isinstance(tz, ZoneInfo):
        return tz
    try:
        return ZoneInfo(str(tz or LOCAL_TZ))
    except (ValueError, KeyError, TypeError):
        return ZoneInfo('UTC')


def parse_timetable(labels, granularity=TIMETABLE_GRANULARITY_MINUTES):
    """``[(hour, minute)]`` for a list of local "HH:MM" labels.

    Tolerant on purpose (the gate reads whatever is committed): malformed
    entries, out-of-range times and off-granularity marks are dropped rather
    than raising. Use :func:`validate_timetable` to *report* them.
    """
    out = []
    for label in labels or ():
        try:
            hour, minute = (int(part) for part in str(label).split(':'))
        except (ValueError, AttributeError):
            continue
        if not (0 <= hour < 24 and 0 <= minute < 60):
            continue
        if granularity and minute % int(granularity):
            continue
        out.append((hour, minute))
    return out


def validate_timetable(labels, granularity=TIMETABLE_GRANULARITY_MINUTES):
    """Problems that make ``labels`` unusable as a timetable (empty = ok)."""
    problems = []
    if not isinstance(labels, (list, tuple)) or not labels:
        return ['timetable must be a non-empty list of "HH:MM" labels']
    seen = set()
    for label in labels:
        marks = parse_timetable([label])
        if len(marks) != 1:
            problems.append(f'{label!r} is not an "HH:MM" mark on the '
                            f'{granularity}-minute grid')
            continue
        if marks[0] in seen:
            problems.append(f'{label!r} is duplicated')
        seen.add(marks[0])
    if list(labels) != sorted(labels):
        problems.append('timetable must be sorted')
    return problems


def format_timetable(marks):
    """``[(hour, minute)]`` -> sorted unique ``["HH:MM"]`` local labels."""
    return [f'{int(hour):02d}:{int(minute):02d}'
            for hour, minute in sorted(set((int(h), int(m)) for h, m in marks))]


def spread_offsets(count, granularity=TIMETABLE_GRANULARITY_MINUTES,
                   hour_minutes=60):
    """``count`` evenly spread in-hour offsets on the ``granularity`` grid.

    ``count=3`` over an hour gives ``[0, 20, 40]`` (the 20-minute day cadence),
    ``count=6`` gives ``[0, 10, ..., 50]`` and ``count=1`` gives ``[0]``. With
    at most ``hour_minutes / granularity`` slots (12) the snapped offsets are
    always distinct.
    """
    count = int(count)
    if count <= 0:
        return []
    step = hour_minutes / count
    marks = sorted({min(int(round(i * step / granularity) * granularity),
                        hour_minutes - granularity)
                    for i in range(count)})
    return marks


def timetable_datetimes(day, timetable, tz=LOCAL_TZ):
    """UTC datetimes for one *local* calendar day of the timetable.

    ``day`` is a local :class:`datetime.date`; the conversion goes through the
    IANA database, so the EDT->EST switch shifts the UTC instants by an hour
    while the local plan stays exactly where it was (issue #91). On the
    spring-forward day the one hour that never happens locally is skipped (its
    slot has no instant); on the fall-back day the repeated hour is emitted
    twice, once per offset, so the night floor keeps its hourly cadence. The
    returned list is sorted by instant and always duplicate-free.
    """
    zone = local_zone(tz)
    out = []
    for hour, minute in parse_timetable(timetable):
        local = datetime(day.year, day.month, day.day, hour, minute, tzinfo=zone)
        out.extend(_local_instants(local, zone))
    return sorted(set(out))


def _local_instants(local, zone):
    """UTC instants of a local wall time, handling DST gaps and repetitions."""
    first = local.replace(fold=0).astimezone(timezone.utc)
    if local.replace(fold=1).utcoffset() == local.replace(fold=0).utcoffset():
        return [first]
    if first.astimezone(zone).replace(tzinfo=None) != local.replace(tzinfo=None):
        # The wall time never happened (spring forward): no instant to schedule.
        return []
    return [first, local.replace(fold=1).astimezone(timezone.utc)]


def timetable_utc_labels(timetable, tz=LOCAL_TZ, day=None):
    """The timetable's UTC "HH:MM" projection for one local day.

    Kept for the legacy consumers (and for eyeballing the plan in the audit
    JSON); the gate itself uses :func:`timetable_datetimes` so it stays correct
    across a DST switch, where this fixed-label projection does not.
    """
    zone = local_zone(tz)
    if day is None:
        day = datetime.now(timezone.utc).astimezone(zone).date()
    if isinstance(day, datetime):
        day = day.date()
    # Sorted as labels, not as instants: the schedule's ``slots`` field is the
    # legacy, lexicographically-ordered projection the gate used to read.
    return sorted(dt.strftime('%H:%M')
                  for dt in timetable_datetimes(day, timetable, tz))


def local_label(dt, tz=LOCAL_TZ):
    """"HH:MM" label of an instant in the plan's local timezone."""
    return dt.astimezone(local_zone(tz)).strftime('%H:%M')


def gap_caps(midnight=DEFAULT_MAX_GAP_MINUTES, night=DEFAULT_NIGHT_GAP_MINUTES,
             day=DEFAULT_DAY_GAP_MINUTES):
    """``{local hour: hard max gap in minutes}`` for the whole day."""
    caps = {hour: int(day) for hour in range(24)}
    caps[MIDNIGHT_HOUR] = int(midnight)
    for hour in range(NIGHT_FIRST_HOUR, NIGHT_LAST_HOUR + 1):
        caps[hour] = int(night)
    return caps


def max_gap_minutes(hour, midnight=DEFAULT_MAX_GAP_MINUTES,
                    night=DEFAULT_NIGHT_GAP_MINUTES,
                    day=DEFAULT_DAY_GAP_MINUTES):
    """Hard max gap (minutes) allowed *inside* local ``hour``."""
    return gap_caps(midnight, night, day)[int(hour) % 24]


def slot_indices(labels):
    """UTC "HH:MM" labels -> their 0..95 quarter-hour slot indices.

    Lossy on purpose: it exists for the legacy per-hour consumers (region
    coverage, audit) that think in quarter-hour UTC slots, so a 5-minute mark
    collapses onto its quarter hour.
    """
    return sorted({int(hour) * SLOTS_PER_HOUR + int(minute) // SLOT_MINUTES
                   for hour, minute in parse_timetable(labels)})


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


def slot_hour_counts(slots):
    """Per-hour poll counts of a daily slot list (24-length, index = UTC hour)."""
    counts = [0] * 24
    for slot in slots or ():
        try:
            counts[int(slot) // SLOTS_PER_HOUR] += 1
        except (TypeError, ValueError):
            continue
    return counts


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


def station_regions(stations):
    """Coordinate key -> region, so a re-priced station can be attributed.

    Uses the same 5-decimal coordinate key as :func:`station_prices`, so the
    output of :func:`diff_stations` can be mapped back to regions (issue #71).
    """
    out = {}
    for feat in (stations or {}).get('features') or []:
        props = (feat or {}).get('properties') or {}
        key = station_coord_key(feat)
        if key:
            out[key] = props.get('region') or 'Inconnue'
    return out


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
        rec.setdefault('exact_samples', 0)
    return hours


def _record_regions(profile, region_counts, changed_regions, changed_stations, hour, gap):
    """Fold one fetch into the per-region hourly counters (issue #71).

    Only hours that were actually observed are stored, so the block stays
    sparse (18 regions x 24 hours only once every hour of the day has been
    sampled). ``changed_stations`` are the *exact* per-station re-prices inside
    that region (issue #68), which is the count-based signal the global
    region-average estimate lacks.
    """
    regions = profile.setdefault('regions', {})
    for region, stations in (region_counts or {}).items():
        entry = regions.setdefault(region, {'stations': 0, 'hours': {}})
        entry['stations'] = int(stations)
        rec = entry['hours'].setdefault(str(hour), {
            'samples': 0, 'observed_hours': 0.0,
            'change_events': 0, 'changed_stations': 0,
        })
        rec['samples'] += 1
        rec['observed_hours'] = round(rec['observed_hours'] + gap, 6)
        if region in (changed_regions or ()):
            rec['change_events'] += 1
        rec['changed_stations'] += int((changed_stations or {}).get(region, 0))
    return regions


def _record_mark(profile, hour, now, changed_stations):
    """Count a fetch's exact re-prices on its quarter-hour mark of ``hour``.

    The upstream snapshot is regenerated on the quarter-hour grid and the plan
    can hold several marks of one hour, so :00/:15/:30/:45 can carry information
    the hour-level buckets average away (issue #71, point 2). Only the exact
    per-station counter feeds this: the region-average channel is too coarse to
    resolve a quarter hour.
    """
    mark = now.astimezone(timezone.utc).minute // SLOT_MINUTES
    marks = profile.setdefault('marks', {})
    hour_marks = marks.setdefault(str(hour), {})
    rec = hour_marks.setdefault(str(mark), {'samples': 0, 'changed_stations': 0})
    rec['samples'] += 1
    rec['changed_stations'] += int(changed_stations)
    return rec


def record_observation(profile, *, observed, region_counts, now, previous_at=None,
                       src=None, max_gap_hours=DEFAULT_MAX_GAP_HOURS,
                       station_changes=None, stations_observed=None,
                       region_changed_stations=None):
    """Fold one real fetch into the learnable change-time profile.

    Returns ``(profile, summary)``. The change is attributed to the *fetch*
    hour, matching the ``observed_hours`` exposure accounting, so the rate
    estimate stays consistent even when the sampler is denser at some hours.
    ``previous_at`` is the timestamp of the previous *observation* (not the
    previous download attempt), which is what the exposure gap must measure.

    ``station_changes`` (exact per-station re-prices) and ``stations_observed``
    are the station-level calibration of the old, region-average-based
    ``changed_stations`` estimate. They are recorded side by side so the exact
    counts stay measurable; :func:`hour_rates` switches to them once
    :func:`exact_channel_ready` reports enough coverage.

    ``region_changed_stations`` (issue #71) is ``{region: exact re-priced
    stations}`` and enables the per-region counters used by the layered
    weights; passing it (or not) is what turns regional learning on, so a
    caller written before #71 keeps writing exactly the old record.
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
        rec['exact_samples'] = int(rec.get('exact_samples') or 0) + 1
        _record_mark(profile, hour, now, exact)
    if stations_observed is not None:
        rec['stations_observed'] = int(stations_observed)
    if region_changed_stations is not None:
        _record_regions(profile, region_counts, changed, region_changed_stations,
                        hour, gap)

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


def hour_rates(profile, channel='auto'):
    """Expected station re-prices per hour of day, plus the exposure behind it.

    Returns ``(rates, observed_hours)``. ``channel`` selects the detector:

    * ``'region'`` — the legacy estimate: stations living in regions whose
      *average* price moved this fetch, divided by the observed hours. It is
      event-based and saturates, because a region counts in full as soon as any
      of its fuel averages moved.
    * ``'exact'`` — stations that really re-priced (issue #68), a *count* per
      hour instead of an event, so the hourly contrast survives (issue #71).
    * ``'auto'`` (default) — ``'exact'`` once :func:`exact_channel_ready` says
      the per-station channel covers the whole day, else ``'region'``.

    The two channels have different units (a region contributes its whole
    station count vs one station per pump that moved), so they are never mixed
    inside one distribution: the switch happens for all hours at once.
    """
    hours = (profile or {}).get('hours') or {}
    if channel == 'auto':
        channel = 'exact' if exact_channel_ready(profile) else 'region'
    key = 'changed_stations_exact' if channel == 'exact' else 'changed_stations'

    rates = [0.0] * 24
    observed_total = 0.0
    for h in range(24):
        rec = hours.get(str(h)) or {}
        observed = max(0.0, float(rec.get('observed_hours') or 0.0))
        observed_total += observed
        if observed > 0:
            rates[h] = max(0.0, float(rec.get(key) or 0.0)) / observed
    return rates, observed_total


def exact_channel_ready(profile, min_samples=EXACT_READY_SAMPLES):
    """Has the per-station channel covered every observed hour of the day yet?

    The exact counters (issue #68) started recording after the region-average
    profile was already weeks old, so switching on the first sample would
    compare a handful of fetches against a month of history. The channel is
    trusted once every hour that has observations also has exact samples and
    the whole profile has at least ``min_samples`` of them.
    """
    hours = (profile or {}).get('hours') or {}

    def samples(h):
        return int((hours.get(str(h)) or {}).get('exact_samples') or 0)

    observed = [h for h in range(24)
                if float((hours.get(str(h)) or {}).get('observed_hours') or 0.0) > 0]
    if not observed:
        return False
    return (sum(samples(h) for h in range(24)) >= int(min_samples)
            and all(samples(h) > 0 for h in observed))


def _normalize(values):
    total = sum(values or ())
    if total <= 0:
        return None
    return [float(v) / total for v in values]


def _shrink(rates, exposure, fallback, alpha):
    """Hierarchical shrinkage of a rate distribution toward ``fallback``.

    ``alpha`` is the prior strength in observed hours: with no exposure the
    result *is* the fallback, with ``exposure == alpha`` the two weigh the same.
    """
    norm = _normalize(rates)
    if norm is None:
        return list(fallback)
    w = float(alpha) / (float(alpha) + max(0.0, float(exposure)))
    blended = [(1.0 - w) * norm[h] + w * fallback[h] for h in range(len(fallback))]
    return _normalize(blended) or list(fallback)


def _region_rates(entry):
    """``(rates, exposure)`` for one region block: re-priced stations per hour."""
    hours = (entry or {}).get('hours') or {}
    rates = [0.0] * 24
    exposure = 0.0
    for h in range(24):
        rec = hours.get(str(h)) or {}
        observed = max(0.0, float(rec.get('observed_hours') or 0.0))
        exposure += observed
        if observed > 0:
            rates[h] = max(0.0, float(rec.get('changed_stations') or 0.0)) / observed
    return rates, exposure


def _global_hour_weights(profile, prior_alpha=DEFAULT_PRIOR_HOURS):
    """Province-wide weights: learned hourly rates blended with the prior.

    With no data at all — or no change ever seen — the weights *are* the prior.
    """
    rates, observed_total = hour_rates(profile)
    return _shrink(rates, observed_total, fallback=research_prior_weights(),
                   alpha=prior_alpha)


def hour_weights(profile, prior_alpha=DEFAULT_PRIOR_HOURS, region=None):
    """Blend learned per-hour change rates with the research prior.

    ``prior_alpha`` is expressed in observed *hours*: after a week of hourly
    coverage the prior and the data weigh the same, and the prior keeps fading.

    With ``region`` given and regional observations recorded (issue #71), the
    region's own distribution is shrunk toward the global one with
    :data:`REGION_PRIOR_HOURS` as the prior strength, so a sparsely sampled
    region (Nord-du-Québec has 36 stations) cannot drift on two noisy hours.

    Without regional data, or with a province-wide call, the global
    distribution is returned — exactly the pre-#71 behaviour.
    """
    base = _global_hour_weights(profile, prior_alpha)
    regions = (profile or {}).get('regions') or {}
    if not regions:
        return base

    if region is not None:
        rates, exposure = _region_rates(regions.get(region))
        return _shrink(rates, exposure, fallback=base, alpha=REGION_PRIOR_HOURS)

    # Province-wide: the expected re-prices of every region, summed. This is the
    # count-based (non-saturating) replacement of the region-average rate.
    rates = [0.0] * 24
    exposure = 0.0
    for entry in regions.values():
        region_rates, region_exposure = _region_rates(entry)
        rates = [a + b for a, b in zip(rates, region_rates)]
        exposure += region_exposure
    return _shrink(rates, exposure, fallback=base, alpha=prior_alpha)


def region_hour_weights(profile):
    """``{region: 24 weights}`` for every region with recorded observations."""
    regions = (profile or {}).get('regions') or {}
    return {name: hour_weights(profile, region=name)
            for name, entry in regions.items() if (entry or {}).get('hours')}


def _peak_hours(weights, baseline=None, top=REGION_PEAK_HOURS,
                min_ratio=REGION_PEAK_RATIO):
    """A region's *distinctive* peak hours, most distinctive first.

    An hour is pinned only when it is a genuine hot hour for the region (at
    least ``min_ratio`` times its uniform share ``1/24``, so a flat rhythm
    cannot promote arbitrary hours) *and* the province's own share there is at
    least ``min_ratio`` times smaller (so an hour the global plan already
    concentrates on is not pinned again). ``baseline`` is the province-wide
    distribution; without it the uniform one is assumed.

    The ranking is ``region share / province share``, deliberately *not*
    weighted by station count: the pin exists for fairness (the volume-based
    water-fill already serves the big regions), so the pin budget goes to the
    hours the province plan would otherwise never poll.
    """
    hours = len(weights)
    if not hours:
        return []
    uniform = 1.0 / hours
    if baseline is None:
        baseline = [uniform] * hours

    def ratio(h):
        base = baseline[h] if h < len(baseline) else 0.0
        if base <= 0:
            # An hour the province never touches: any weight here is a region's
            # own window, i.e. infinitely more interesting than the baseline.
            return float('inf') if weights[h] > 0 else 0.0
        return weights[h] / base

    candidates = [h for h in range(hours)
                  if weights[h] >= min_ratio * uniform and ratio(h) >= min_ratio]
    if not candidates:
        return []
    ranked = sorted(candidates, key=lambda h: (-ratio(h), -weights[h], h))
    return sorted(ranked[:max(1, int(top))])


def coverage_hours(weights_by_region, baseline=None, top=REGION_PEAK_HOURS):
    """Hours that must stay in the plan, most distinctive first.

    One global schedule is fetched for the whole province, so a region is only
    "at risk" of never being sampled during its own hot window. Pinning each
    region's distinctive hours as mandatory slots keeps a remote region from
    being starved by the metropolises' weights. The returned list is *ranked*
    (the hour that stands out the most comes first) so a budget too small to pin
    everything still spends on the most region-specific hours.
    """
    best = {}
    for weights in (weights_by_region or {}).values():
        hours = len(weights)
        if not hours:
            continue
        uniform = 1.0 / hours
        base = baseline if baseline is not None else [uniform] * hours
        for h in _peak_hours(weights, baseline=base, top=top):
            share = base[h] if h < len(base) else 0.0
            ratio = float('inf') if share <= 0 else weights[h] / share
            if h not in best or ratio > best[h]:
                best[h] = ratio
    return sorted(best, key=lambda h: (-best[h], h))


def region_coverage(weights_by_region, slots, baseline=None, top=REGION_PEAK_HOURS):
    """``{region: {'peaks': [...], 'covered': bool}}`` for a planned day."""
    counts = slot_hour_counts(slots)
    report = {}
    for region, weights in (weights_by_region or {}).items():
        peaks = _peak_hours(weights, baseline=baseline, top=top)
        report[region] = {'peaks': peaks,
                          'covered': all(counts[h] >= 1 for h in peaks)}
    return report


def learned_mark_orders(profile, min_samples=MARK_MIN_SAMPLES):
    """``{hour: (mark, ...)}`` — quarter-hour marks ranked by learned density.

    Only hours with at least ``min_samples`` recorded fetches are returned;
    everything else falls back to :data:`DEFAULT_MARK_ORDER` (coarsest first),
    so a quarter hour is never promoted on one lucky sample.
    """
    orders = {}
    for key, marks in ((profile or {}).get('marks') or {}).items():
        try:
            hour = int(key)
        except (TypeError, ValueError):
            continue
        records = {}
        for mark, rec in (marks or {}).items():
            try:
                records[int(mark)] = rec or {}
            except (TypeError, ValueError):
                continue
        samples = sum(int(rec.get('samples') or 0) for rec in records.values())
        if samples < int(min_samples):
            continue

        def rank(mark):
            rec = records.get(mark) or {}
            seen = int(rec.get('samples') or 0)
            density = float(rec.get('changed_stations') or 0) / seen if seen > 0 else 0.0
            return (-density, DEFAULT_MARK_ORDER.index(mark))

        orders[hour] = tuple(sorted(range(SLOTS_PER_HOUR), key=rank))
    return orders


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


def _exploit_slots(weights, budget, min_per_hour, max_per_hour, coverage_hours=(),
                   mark_orders=None, coverage_share=COVERAGE_BUDGET_SHARE):
    """Hourly floor, regional pins, then learn the busiest hours.

    The order matters:

    1. every hour keeps ``min_per_hour`` slots while the budget allows, so the
       hourly freshness floor of issue #77 survives any regional constraint;
    2. ``coverage_hours`` (issue #71) are then pinned, in the order given
       (:func:`coverage_hours` ranks them by how much they protect), for at most
       ``coverage_share`` of the budget left after the floor — so a guarantee for
       one region can never eat the whole plan;
    3. the rest is water-filled into the highest-weight hours, using the learned
       quarter-hour order of that hour (:func:`learned_mark_orders`) or the
       coarse default (:data:`DEFAULT_MARK_ORDER`).
    """
    hours = len(weights)
    counts = [0] * hours
    remaining = budget

    base = min(int(min_per_hour), remaining // hours) if hours else 0
    for h in range(hours):
        counts[h] = base
    remaining -= base * hours

    share = max(0.0, min(1.0, float(coverage_share or 0.0)))
    allowance = int(remaining * share)
    pinned = 0
    mandatory = list(dict.fromkeys(int(h) for h in (coverage_hours or ())
                                   if 0 <= int(h) < hours))
    for h in mandatory:
        if pinned >= allowance:
            break
        if counts[h] > 0:
            continue          # the hourly floor already covers this hour
        counts[h] = 1
        pinned += 1
    remaining -= pinned

    while remaining > 0:
        candidates = [h for h in range(hours) if counts[h] < max_per_hour]
        if not candidates:
            break
        chosen = max(candidates, key=lambda h: (weights[h] / (counts[h] + 1), -h))
        counts[chosen] += 1
        remaining -= 1

    orders = mark_orders or {}
    slots = []
    for h in range(hours):
        marks = orders.get(h) or DEFAULT_MARK_ORDER
        for mark in marks[:counts[h]]:
            slots.append(h * SLOTS_PER_HOUR + int(mark))
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
                  explore_frac=DEFAULT_EXPLORE_FRAC, coverage_hours=(),
                  mark_orders=None):
    """Plan the day and say which slots are exploration probes (issue #69).

    Returns ``(slots, explore_slots)``. ``slots`` is the same bounded daily
    plan as before; ``explore_slots`` is its subset reserved for the hours with
    the least ``observed_hours``. Exploration is opt-in on evidence:
    ``observed_hours=None`` (no learned profile yet, e.g. the research-prior
    fallback) or ``explore_frac=0`` reproduces the previous plan exactly.

    ``coverage_hours`` / ``mark_orders`` (issue #71) only shape the
    exploitation part: exploration keeps probing the least-observed hours so
    the regional pins can never freeze the sampler's own learning.
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

    exploit = _exploit_slots(weights, budget - explore_budget, min_per_hour,
                             max_per_hour, coverage_hours=coverage_hours,
                             mark_orders=mark_orders)
    explore = []
    if explore_budget > 0:
        explore = _pick_explore_slots(weights, observed_hours, exploit,
                                      explore_budget, max_per_hour)
    return sorted(exploit + explore), sorted(explore)


def plan_slots(weights, budget=DEFAULT_DAILY_BUDGET, min_per_hour=1,
               max_per_hour=SLOTS_PER_HOUR, coverage_hours=(), mark_orders=None):
    """Pick quarter-hour UTC slots for the day under a daily request budget.

    Every hour keeps ``min_per_hour`` slots when the budget allows, so the
    learner keeps unbiased coverage; the remaining budget is water-filled into
    the hours that historically produce the most price changes.
    """
    return plan_schedule(weights, budget=budget, min_per_hour=min_per_hour,
                         max_per_hour=max_per_hour,
                         coverage_hours=coverage_hours,
                         mark_orders=mark_orders)[0]


def slot_labels(slots):
    return [slot_label(s) for s in slots]


# ── Workflow gate ────────────────────────────────────────────────────────────

def due_decision(now, slots, last_fetch_at, *, min_gap_minutes=10,
                 max_age_minutes=DEFAULT_MAX_AGE_MINUTES, grace_minutes=45,
                 explore_slots=None, timetable=None, tz=LOCAL_TZ):
    """Should this workflow run poll upstream right now?

    ``slots`` is a daily list of "HH:MM" UTC labels (the legacy plan). When
    ``timetable`` is given (issue #91) the plan is read as *local* times in
    ``tz`` instead, converted per calendar day, so the scheduled window does not
    drift when the province switches between EDT and EST.

    The decision:

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

    local_plan = bool(parse_timetable(timetable or ()))
    candidates = []
    if local_plan:
        today = now.astimezone(local_zone(tz)).date()
        for day_offset in (0, -1):
            candidates.extend(timetable_datetimes(today + timedelta(days=day_offset),
                                                  timetable, tz))
    else:
        for day_offset in (0, -1):
            candidates.extend(slot_datetimes((now + timedelta(days=day_offset)).date(), slots))

    served = [t for t in candidates
              if last_fetch_at < t <= now and (now - t).total_seconds() / 60.0 <= grace_minutes]
    if served:
        latest = max(served)
        lag = (now - latest).total_seconds() / 60.0
        label = latest.strftime('%H:%M')
        probes = {str(s) for s in (explore_slots or ())}
        tag = ''
        if label in probes or (local_plan and local_label(latest, tz) in probes):
            # The timetable's probes are local labels; the UTC projection moves
            # them by an hour twice a year, so match on either spelling.
            tag = ' [explore]'
        return True, f'slot {label}{tag} due ({lag:.0f} min ago)'

    if age_minutes > max_age_minutes:
        return True, (f'stale: last fetch {age_minutes:.0f} min ago '
                      f'(> {max_age_minutes} min)')

    nxt = sorted(t for t in candidates if t > now)
    if nxt:
        zone_name = ' local' if local_plan else ' UTC'
        return False, f'not due: next slot {nxt[0].strftime("%H:%M")}{zone_name}'
    return False, 'not due: no slots scheduled'

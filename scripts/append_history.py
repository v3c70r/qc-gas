#!/usr/bin/env python3
"""Append a real, region-level price snapshot to the tiered history store.

This script is designed to run after ``process_data.py`` in the hourly GitHub
Actions workflow. It reads ``data/stations.json`` (the current real snapshot),
computes region-level aggregations for regular/super/diesel (avg/min/max/count),
then stores them in a compact, sharded, tiered layout under ``data/history/``:

  * ``data/history/YYYY-MM.json``  -- raw 6h buckets, kept for the last 14 days
  * ``data/history/station-history/YYYY-MM.json`` -- station daily tier (issue
    #114: one shard per month, so a fetch rewrites one file instead of the whole
    1.36 MB table)
  * ``data/history/daily.json``    -- daily aggregation, kept for 12 months
  * ``data/history/monthly.json``  -- monthly aggregation, kept long-term
  * ``data/history/index.json``    -- small metadata manifest

Finally it rebuilds ``data/history.json`` in the original frontend format so
the existing dashboard / station cards keep working without a breaking change.

The raw store uses short keys to keep repository growth small:
  t=timestamp, src=source timestamp, r=region map,
  g=regular, s=super, d=diesel, a=avg, n=min, x=max, c=count.

The script is idempotent: when ``generated_at`` is unchanged it skips adding a
duplicate snapshot, but still runs tier rollup/pruning so retention advances.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_strategy import station_prices  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data'
HISTORY_DIR = DATA_DIR / 'history'
STATIONS_FILE = DATA_DIR / 'stations.json'
INDEX_FILE = HISTORY_DIR / 'index.json'
DAILY_FILE = HISTORY_DIR / 'daily.json'
MONTHLY_FILE = HISTORY_DIR / 'monthly.json'
MERGED_FILE = DATA_DIR / 'history.json'
# Issue #114: the station daily tier used to be one 1.36 MB file that every
# fetch rewrote, so each data commit added a full new blob (the last volume
# lever after #92/#103). It is now sharded by month and only the shard of the
# current month is rewritten, and only when its content actually changes.
STATION_HISTORY_DIR = HISTORY_DIR / 'station-history'
STATION_HISTORY_FILE = HISTORY_DIR / 'station-history.json'  # legacy, migrated then deleted

BUCKET_SECONDS = 6 * 3600
STATION_DAILY_DAYS = 180  # keep one station-level daily snapshot per day for 6 months
RAW_DAYS = 14
DAILY_DAYS = 365  # 12 months approximate
OVERALL_KEY = '__overall__'

FUEL_SHORT = {'regular': 'g', 'super': 's', 'diesel': 'd'}
FUEL_LONG = {v: k for k, v in FUEL_SHORT.items()}
FUELS = ('regular', 'super', 'diesel')


def load_json(path):
    if not path.exists():
        return None
    with path.open('r', encoding='utf-8') as f:
        return json.load(f)


def save_json(path, obj, compact=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as f:
        if compact:
            json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write('\n')


def save_json_if_changed(path, obj, compact=True):
    """Write ``obj`` only when it differs from what is already on disk (issue #114).

    An unchanged file is an unchanged blob, so skipping the write keeps a fetch
    from adding bytes to git history for nothing.
    """
    if load_json(path) == obj:
        return False
    save_json(path, obj, compact)
    return True


def parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None


def to_iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def floor_bucket(dt):
    """Floor an aware datetime to the nearest 6h UTC bucket."""
    utc = dt.astimezone(timezone.utc)
    epoch = int(utc.timestamp())
    bucket = epoch - (epoch % BUCKET_SECONDS)
    return datetime.fromtimestamp(bucket, tz=timezone.utc)


def day_key_of(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%d')


def month_key_of(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m')


def day_ts(day_key):
    return f'{day_key}T12:00:00Z'


def month_ts(month_key):
    return f'{month_key}-01T12:00:00Z'


# ── Aggregation from the station feature collection ─────────────────────────

def compute_aggregates(stations):
    """Return (regions, overall) as {region: {fuel: {sum, n, x, c}}}."""
    regions = {}
    overall = {}
    for feat in stations.get('features', []):
        props = feat.get('properties') or {}
        region = props.get('region') or 'Inconnue'
        region_agg = regions.setdefault(region, {})
        for fuel in FUELS:
            val = props.get(fuel + '_price')
            if val is None:
                continue
            for target in (region_agg, overall):
                agg = target.setdefault(fuel, {'sum': 0.0, 'n': None, 'x': None, 'c': 0})
                agg['sum'] += float(val)
                agg['n'] = float(val) if agg['n'] is None else min(agg['n'], float(val))
                agg['x'] = float(val) if agg['x'] is None else max(agg['x'], float(val))
                agg['c'] += 1
    return regions, overall


def agg_to_short(agg):
    out = {}
    for fuel_long, v in agg.items():
        if v['c'] == 0:
            continue
        out[FUEL_SHORT[fuel_long]] = {
            'a': round(v['sum'] / v['c'], 1),
            'n': round(v['n'], 1),
            'x': round(v['x'], 1),
            'c': v['c'],
        }
    return out


def build_snapshot(stations, src_dt):
    regions, overall = compute_aggregates(stations)
    r = {region: agg_to_short(agg) for region, agg in regions.items()}
    r[OVERALL_KEY] = agg_to_short(overall)
    return {
        't': to_iso(floor_bucket(src_dt)),
        'src': to_iso(src_dt),
        'r': r,
    }


def build_station_snapshot(stations):
    """Return one scalar price per fuel per station, keyed by 5-decimal coords.

    Shares the coordinate key and the price extraction with the station change
    store (`fetch_strategy.station_prices`) so the station card history and the
    `last_changed_at` store can never disagree about what a station key is.
    """
    return station_prices(stations)


def update_station_history(store, snapshot, today, cutoff_day):
    """Upsert one daily station snapshot and prune older days.

    The station store is column-oriented: ``t`` is the sorted list of retained
    day keys and ``s[coord][fuel]`` is a list aligned to ``t`` (``null`` when
    that station did not report on that day). This keeps repeated coordinate
    keys out of the daily payload.

    Days are inserted in sorted order *and* existing arrays are reindexed onto
    that new day list, so an out-of-order source date can never shift older
    recorded values out of alignment.
    """
    old_t = list(store.get('t') or [])
    old_s = store.get('s') or {}

    new_t = sorted(set(old_t) | {today})
    old_index = {day: i for i, day in enumerate(old_t)}
    n = len(new_t)
    idx = new_t.index(today)

    # Reindex existing arrays onto the new sorted day list first. This is the
    # fix for out-of-order source timestamps: inserting a middle day must not
    # shift the values that belong to the days after it.
    aligned_s = {}
    for key, site in old_s.items():
        new_site = {}
        for short, arr in site.items():
            new_site[short] = [
                arr[old_index[d]] if d in old_index and old_index[d] < len(arr) else None
                for d in new_t
            ]
        aligned_s[key] = new_site

    # Apply today's snapshot at its sorted position.
    for key, rec in snapshot.items():
        site = aligned_s.get(key)
        if site is None:
            site = {}
            aligned_s[key] = site
        for short, val in rec.items():
            arr = site.get(short)
            if arr is None:
                arr = [None] * n
                site[short] = arr
            arr[idx] = val

    new_t_pruned = [d for d in new_t if d >= cutoff_day]
    keep = [i for i, d in enumerate(new_t) if d >= cutoff_day]
    new_s = {}
    for key, site in aligned_s.items():
        new_site = {}
        for short, arr in site.items():
            new_arr = [arr[i] if i < len(arr) else None for i in keep]
            if any(v is not None for v in new_arr):
                new_site[short] = new_arr
        if new_site:
            new_s[key] = new_site

    return {
        'v': 1,
        't': new_t_pruned,
        's': new_s,
        'tiers': {
            'daily_days': STATION_DAILY_DAYS,
            'monthly': 'none',
        },
    }


# ── Station history month shards (issue #114) ────────────────────────────────

def month_of_day(day_key):
    """``2026-10-19`` -> ``2026-10``."""
    return str(day_key)[:7]


def month_shard_name(month):
    return f'{month}.json'


def _shard_store(shard):
    """The single-store view of one shard (what ``update_station_history`` wants)."""
    return {'v': 1, 't': list(shard.get('t') or []), 's': shard.get('s') or {}}


def _shard(month, days, sites):
    return {'v': 1, 'm': month, 't': list(days), 's': sites}


def split_station_store(store):
    """Split the legacy single store into ``{YYYY-MM: shard}`` (one-off migration)."""
    days = [str(d) for d in ((store or {}).get('t') or [])]
    sites = (store or {}).get('s') or {}
    shards = {}
    for month in sorted({month_of_day(d) for d in days}):
        idx = [i for i, d in enumerate(days) if month_of_day(d) == month]
        out_sites = {}
        for key, site in sites.items():
            new_site = {}
            for short, arr in (site or {}).items():
                values = [arr[i] if i < len(arr) else None for i in idx]
                if any(v is not None for v in values):
                    new_site[short] = values
            if new_site:
                out_sites[key] = new_site
        shards[month] = _shard(month, [days[i] for i in idx], out_sites)
    return shards


def merge_station_shards(shards):
    """Merge ``{YYYY-MM: shard}`` back into the day-aligned single store."""
    days = sorted({d for shard in (shards or {}).values()
                   for d in (shard.get('t') or [])})
    index = {day: i for i, day in enumerate(days)}
    sites = {}
    for month in sorted(shards or {}):
        shard = shards[month]
        shard_days = shard.get('t') or []
        for key, site in (shard.get('s') or {}).items():
            target = sites.setdefault(key, {})
            for short, arr in (site or {}).items():
                out = target.setdefault(short, [None] * len(days))
                for i, day in enumerate(shard_days):
                    value = arr[i] if i < len(arr) else None
                    if value is not None:
                        out[index[day]] = value
    return {'v': 1, 't': days, 's': sites}


def merge_month_shard(primary, secondary):
    """Union two shards of the **same** month; ``primary`` wins on shared days.

    Used for the legacy-file migration (issue #114 review round 1): a checkout
    can carry a legacy ``station-history.json`` *and* a partly populated shard
    directory, so neither source may be dropped on the floor.
    """
    days = sorted(set(primary.get('t') or []) | set(secondary.get('t') or []))
    index = {day: i for i, day in enumerate(days)}
    sites = {}
    for shard in (secondary, primary):  # primary applied last -> wins
        shard_days = shard.get('t') or []
        for key, site in (shard.get('s') or {}).items():
            target = sites.setdefault(key, {})
            for short, arr in (site or {}).items():
                out = target.setdefault(short, [None] * len(days))
                for i, day in enumerate(shard_days):
                    value = arr[i] if i < len(arr) else None
                    if value is not None:
                        out[index[day]] = value
    month = primary.get('m') or secondary.get('m') or (month_of_day(days[0]) if days else '')
    return _shard(month, days, sites)


def _prune_shard(shard, cutoff_day):
    """Drop days older than ``cutoff_day`` from one shard (keeps arrays aligned)."""
    days = shard.get('t') or []
    keep = [i for i, day in enumerate(days) if day >= cutoff_day]
    if len(keep) == len(days):
        return shard
    sites = {}
    for key, site in (shard.get('s') or {}).items():
        new_site = {}
        for short, arr in (site or {}).items():
            values = [arr[i] if i < len(arr) else None for i in keep]
            if any(v is not None for v in values):
                new_site[short] = values
        if new_site:
            sites[key] = new_site
    name = shard.get('m') or (month_of_day(days[0]) if days else '')
    return _shard(name, [days[i] for i in keep], sites)


def update_station_shards(shards, snapshot, today, cutoff_day):
    """Upsert one day into its month shard; prune expired days/shards.

    Returns ``(shards, changed_months)``. ``changed_months`` is exactly the set
    of files the caller has to write (or delete, when the month is no longer in
    ``shards``): the whole point of #114 is that a fetch touches one shard.
    """
    current = {month: dict(shard) for month, shard in (shards or {}).items()}
    changed = set()

    month = month_of_day(today)
    base = current.get(month) or _shard(month, [], {})
    # One recorded day is final: the daily tier is a once-per-day snapshot, and
    # freezing it is what keeps the shard append-only: a fetch that sees today
    # already recorded must not rewrite the month at all.
    if today not in (base.get('t') or []):
        updated = update_station_history(_shard_store(base), snapshot, today, cutoff_day)
        new_shard = _shard(month, updated['t'], updated['s'])
        if new_shard != base:
            current[month] = new_shard
            changed.add(month)

    for name, shard in list(current.items()):
        pruned = _prune_shard(shard, cutoff_day)
        if not pruned.get('t'):
            del current[name]
            changed.add(name)
        elif pruned != shard:
            current[name] = pruned
            changed.add(name)

    return current, sorted(changed)


def load_station_shards():
    """Read ``data/history/station-history/YYYY-MM.json`` shards.

    A legacy ``station-history.json`` (pre-#114) is split into the same shards on
    read and unioned with any shard the directory already holds (the disk copy is
    the source of truth for a day both layouts carry). ``save_station_shards()``
    deletes the legacy file only once every month is safely on disk.
    """
    shards = {}
    if STATION_HISTORY_DIR.is_dir():
        for path in sorted(STATION_HISTORY_DIR.glob('*.json')):
            month = path.stem
            if len(month) != 7 or month[4] != '-':
                continue
            data = load_json(path)
            if data:
                shards[month] = data
    legacy = load_json(STATION_HISTORY_FILE)
    if legacy:
        for month, shard in split_station_store(legacy).items():
            if month in shards:
                # Both layouts carry this month: union the days, disk wins.
                shards[month] = merge_month_shard(shards[month], shard)
            else:
                shards[month] = shard
    return shards


def save_station_shards(shards, changed_months):
    """Write the shards the store changed, then retire the legacy file.

    ``changed_months`` is the *update* delta, but the store can also differ from
    what is on disk for reasons the update does not report — most importantly a
    month that only existed in the legacy single file (migration). Writing just
    ``changed_months`` and then deleting the legacy file would lose that month
    permanently, so every in-memory shard whose on-disk copy differs is written
    too, and the legacy file is only removed once all months are safely on disk.
    """
    STATION_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    to_delete = set()
    to_write = set()
    for month in changed_months:
        if shards.get(month) is None:
            to_delete.add(month)
        else:
            to_write.add(month)
    for month, shard in shards.items():
        if month in to_write:
            continue
        if load_json(STATION_HISTORY_DIR / month_shard_name(month)) != shard:
            to_write.add(month)

    for month in sorted(to_write):
        save_json(STATION_HISTORY_DIR / month_shard_name(month), shards[month])
    for month in sorted(to_delete):
        (STATION_HISTORY_DIR / month_shard_name(month)).unlink(missing_ok=True)
    # Drop any shard file that is no longer part of the store (e.g. left over
    # from a previous retention window or an interrupted run).
    for path in STATION_HISTORY_DIR.glob('*.json'):
        if path.stem not in shards:
            path.unlink(missing_ok=True)

    # Retire the legacy single file only once every month of the store exists on
    # disk: `data/history/**` is the only copy that exists (#114 review).
    if STATION_HISTORY_FILE.exists():
        on_disk = {path.stem for path in STATION_HISTORY_DIR.glob('*.json')}
        if set(shards) <= on_disk:
            STATION_HISTORY_FILE.unlink()


# ── Snapshot merging (weighted avg / min / max / count) ─────────────────────

def merge_fuel(a, b):
    if not a:
        return dict(b)
    if not b:
        return dict(a)
    c = a.get('c', 0) + b.get('c', 0)
    avg = (a.get('a', 0.0) * a.get('c', 0) + b.get('a', 0.0) * b.get('c', 0)) / c if c else 0.0
    n = min(a.get('n', a.get('a')), b.get('n', b.get('a')))
    x = max(a.get('x', a.get('a')), b.get('x', b.get('a')))
    return {'a': round(avg, 1), 'n': round(n, 1), 'x': round(x, 1), 'c': c}


def merge_snapshots(old, new):
    """Merge two snapshots with the same bucket/day/month key."""
    if not old:
        return dict(new)
    if not new:
        return dict(old)
    merged_r = {}
    for region in set(old.get('r', {})) | set(new.get('r', {})):
        old_fuels = old.get('r', {}).get(region, {})
        new_fuels = new.get('r', {}).get(region, {})
        fuels = {}
        for f in set(old_fuels) | set(new_fuels):
            fuels[f] = merge_fuel(old_fuels.get(f), new_fuels.get(f))
        merged_r[region] = fuels
    return {
        't': old.get('t') or new.get('t'),
        'src': new.get('src') or old.get('src'),
        'r': merged_r,
    }


# ── Raw shard helpers ────────────────────────────────────────────────────────

def load_raw_shards():
    shards = {}
    if not HISTORY_DIR.exists():
        return shards
    for path in HISTORY_DIR.glob('*.json'):
        name = path.name
        # Only YYYY-MM.json files are raw shards.
        parts = name[:-5].split('-')
        if len(parts) == 2 and len(parts[0]) == 4 and parts[1].isdigit():
            data = load_json(path)
            if data:
                shards[parts[0] + '-' + parts[1]] = data
    return shards


def upsert_bucket(snapshot_list, snap):
    """Insert/merge a 6h bucket snapshot into a raw shard's list."""
    for i, existing in enumerate(snapshot_list):
        if existing.get('t') == snap['t']:
            snapshot_list[i] = merge_snapshots(existing, snap)
            return snapshot_list
    snapshot_list.append(snap)
    return snapshot_list


# ── Tiered retention rollup ──────────────────────────────────────────────────

def roll_buckets_to_daily(expired_buckets, daily):
    for bucket in expired_buckets:
        dt = parse_iso(bucket.get('t'))
        if not dt:
            continue
        day = day_key_of(dt)
        entry = {'t': day_ts(day), 'src': bucket.get('src'), 'r': bucket.get('r', {})}
        daily['d'][day] = merge_snapshots(daily['d'].get(day), entry)
    return daily


def roll_days_to_monthly(expired_days, monthly):
    for day_entry in expired_days:
        dt = parse_iso(day_entry.get('t'))
        if not dt:
            continue
        month = month_key_of(dt)
        entry = {'t': month_ts(month), 'src': day_entry.get('src'), 'r': day_entry.get('r', {})}
        monthly['m'][month] = merge_snapshots(monthly['m'].get(month), entry)
    return monthly


# ── Frontend merged file builder ─────────────────────────────────────────────

def point_for(snap, region_key):
    fuels = snap.get('r', {}).get(region_key, {})
    point = {'date': snap.get('t')}
    for fshort, agg in fuels.items():
        long_name = FUEL_LONG.get(fshort)
        if not long_name:
            continue
        point[long_name] = {'avg': agg.get('a'), 'min': agg.get('n'), 'max': agg.get('x')}
    return point


def build_merged(regions, shards, daily, monthly, src, src_dt):
    region_points = {r: [] for r in regions}
    overall_points = []

    def add_snap(snap):
        for region in regions:
            if region in snap.get('r', {}):
                region_points[region].append(point_for(snap, region))
        if OVERALL_KEY in snap.get('r', {}):
            overall_points.append(point_for(snap, OVERALL_KEY))

    # Raw 6h buckets (newest 14 days) first, then daily, then monthly.
    for shard in shards.values():
        for snap in shard.get('s', []):
            add_snap(snap)
    for entry in daily.get('d', {}).values():
        add_snap(entry)
    for entry in monthly.get('m', {}).values():
        add_snap(entry)

    def sort_key(point):
        dt = parse_iso(point.get('date'))
        return dt.timestamp() if dt else 0

    for r in regions:
        region_points[r].sort(key=sort_key)
    overall_points.sort(key=sort_key)

    latest_candidates = [p.get('date') for p in overall_points]
    if latest_candidates:
        latest = max(
            latest_candidates,
            key=lambda s: parse_iso(s).timestamp() if parse_iso(s) else 0,
        )
    else:
        latest = to_iso(src_dt)

    return {
        'regions': {r: {'points': region_points[r]} for r in regions},
        'overall': {'points': overall_points},
        'metadata': {
            'generated_at': src,
            'latest': latest,
            'interval_hours': 6,
            'samples_per_day': 4,
            'days': RAW_DAYS,
            'regions': regions,
            'source': 'real',
            'tiers': {
                'raw_days': RAW_DAYS,
                'daily_days': DAILY_DAYS,
                'monthly': 'forever',
                'bucket_hours': 6,
            },
        },
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    stations = load_json(STATIONS_FILE)
    if not stations:
        print('data/stations.json missing; skipping history append')
        return

    src = (stations.get('metadata') or {}).get('generated_at')
    src_dt = parse_iso(src) or datetime.now(timezone.utc)
    if not src:
        src = to_iso(src_dt)

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    index = load_json(INDEX_FILE) or {'v': 1}
    index.setdefault('v', 1)
    index['regions'] = sorted({(f.get('properties') or {}).get('region') or 'Inconnue'
                               for f in stations.get('features', [])})
    index['tiers'] = {
        'raw_days': RAW_DAYS,
        'daily_days': DAILY_DAYS,
        'monthly': 'forever',
        'bucket_hours': 6,
    }

    shards = load_raw_shards()
    has_data = any(shard.get('s') for shard in shards.values())

    # Add a new snapshot only when the source data actually changed.
    if index.get('last_src') != src or not has_data:
        snap = build_snapshot(stations, src_dt)
        month = day_key_of(src_dt)[:7]  # YYYY-MM
        shard = shards.get(month, {'v': 1, 's': []})
        shard['s'] = upsert_bucket(shard.get('s', []), snap)
        shards[month] = shard
        index['last_src'] = src
        print(f"Appended snapshot {snap['src']} -> bucket {snap['t']}")
    else:
        print(f'Source unchanged ({src}); skipping duplicate snapshot')

    # ── Station-level daily history (real per-station prices) ──
    # Issue #114: one shard per month, and only the shard(s) that actually
    # changed are written, so a fetch no longer rewrites the full table.
    station_cutoff_day = day_key_of(src_dt - timedelta(days=STATION_DAILY_DAYS))
    station_shards = load_station_shards()
    station_shards, changed_months = update_station_shards(
        station_shards,
        build_station_snapshot(stations),
        day_key_of(src_dt),
        station_cutoff_day,
    )
    save_station_shards(station_shards, changed_months)
    index['station_history'] = {
        'months': sorted(station_shards),
        'retention_days': STATION_DAILY_DAYS,
        'stations': sum(len(shard.get('s') or {}) for shard in station_shards.values()),
        'days': sum(len(shard.get('t') or []) for shard in station_shards.values()),
    }
    print(f'Station history: {index["station_history"]["days"]} days, '
          f'{index["station_history"]["stations"]} stations, '
          f'{len(station_shards)} month shard(s); wrote {len(changed_months)}')

    # ── Retention: raw (last RAW_DAYS) → daily → monthly ──
    cutoff_raw_day = day_key_of(src_dt - timedelta(days=RAW_DAYS))
    cutoff_daily_day = day_key_of(src_dt - timedelta(days=DAILY_DAYS))

    daily = load_json(DAILY_FILE) or {'v': 1, 'd': {}}
    monthly = load_json(MONTHLY_FILE) or {'v': 1, 'm': {}}
    daily.setdefault('d', {})
    monthly.setdefault('m', {})

    expired_buckets = []
    for month, shard in list(shards.items()):
        kept = []
        for snap in shard.get('s', []):
            dt = parse_iso(snap.get('t'))
            if dt and day_key_of(dt) >= cutoff_raw_day:
                kept.append(snap)
            else:
                expired_buckets.append(snap)
        if kept:
            shard['s'] = kept
            shards[month] = shard
        else:
            del shards[month]

    # Persist raw shards now so a later failure can't lose the rolled tiers.
    for month, shard in shards.items():
        save_json_if_changed(HISTORY_DIR / f'{month}.json', shard)

    # Delete month shards that are now empty/expired so stale files don't linger.
    retained_months = set(shards.keys())
    for path in HISTORY_DIR.glob('*.json'):
        name = path.name[:-5]
        parts = name.split('-')
        if len(parts) == 2 and len(parts[0]) == 4 and parts[1].isdigit():
            if name not in retained_months:
                path.unlink(missing_ok=True)
                br = path.with_suffix('.json.br')
                if br.exists():
                    br.unlink()

    roll_buckets_to_daily(expired_buckets, daily)

    expired_days = []
    for day, entry in list(daily['d'].items()):
        dt = parse_iso(entry.get('t'))
        if dt and day_key_of(dt) < cutoff_daily_day:
            expired_days.append(entry)
            del daily['d'][day]

    roll_days_to_monthly(expired_days, monthly)

    save_json_if_changed(DAILY_FILE, daily)
    save_json_if_changed(MONTHLY_FILE, monthly)
    save_json_if_changed(INDEX_FILE, index)

    merged = build_merged(index['regions'], shards, daily, monthly, src, src_dt)
    save_json(MERGED_FILE, merged, compact=False)

    raw_count = sum(len(s.get('s', [])) for s in shards.values())
    print(f'History: {raw_count} raw buckets, {len(daily["d"])} daily points, '
          f'{len(monthly["m"])} monthly points across {len(index["regions"])} regions')


if __name__ == '__main__':
    main()

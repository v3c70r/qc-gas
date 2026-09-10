#!/usr/bin/env python3
"""Append a real, region-level price snapshot to the tiered history store.

This script is designed to run after ``process_data.py`` in the hourly GitHub
Actions workflow. It reads ``data/stations.json`` (the current real snapshot),
computes region-level aggregations for regular/super/diesel (avg/min/max/count),
then stores them in a compact, sharded, tiered layout under ``data/history/``:

  * ``data/history/YYYY-MM.json``  -- raw 6h buckets, kept for the last 14 days
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
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data'
HISTORY_DIR = DATA_DIR / 'history'
STATIONS_FILE = DATA_DIR / 'stations.json'
INDEX_FILE = HISTORY_DIR / 'index.json'
DAILY_FILE = HISTORY_DIR / 'daily.json'
MONTHLY_FILE = HISTORY_DIR / 'monthly.json'
MERGED_FILE = DATA_DIR / 'history.json'

BUCKET_SECONDS = 6 * 3600
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
        save_json(HISTORY_DIR / f'{month}.json', shard)

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

    save_json(DAILY_FILE, daily)
    save_json(MONTHLY_FILE, monthly)
    save_json(INDEX_FILE, index)

    merged = build_merged(index['regions'], shards, daily, monthly, src, src_dt)
    save_json(MERGED_FILE, merged, compact=False)

    raw_count = sum(len(s.get('s', [])) for s in shards.values())
    print(f'History: {raw_count} raw buckets, {len(daily["d"])} daily points, '
          f'{len(monthly["m"])} monthly points across {len(index["regions"])} regions')


if __name__ == '__main__':
    main()

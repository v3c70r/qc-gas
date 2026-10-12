#!/usr/bin/env python3
"""Latency & freshness metrics for the price data pipeline (issue #70).

Turns "some stations look wrong" into numbers a human or the PM agent can
compare across data-pipeline changes (#67/#68):

1. **end-to-end latency** — ``observation_at - metadata.generated_at``: how long
   after upstream published a snapshot it was fetched and recorded. The current
   run contributes one sample to a small rolling ledger kept inside
   ``data/metrics.json`` (the only place a fetch timestamp survives), so the
   script can report a real p50/p90/p95 instead of a single number.
2. **site freshness** — ``now - last_seen_at`` over the per-station change
   store (``data/history/station-changes.json``, issue #68): how far every
   station's on-screen price can be behind upstream.
3. **long-unchanged share** — stations whose ``last_changed_at`` is older than
   7 / 30 days, with a short "typical" list (suspected frozen source rows).
4. **real hourly change rate** — from the exact per-station counts accumulated
   by ``update_profile.py`` (``changed_stations_exact`` / ``observed_hours``).

Everything is standard library only and reads the committed data files; a
missing file degrades to an empty metric instead of failing the workflow.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
STATION_CHANGES_REL = Path('history') / 'station-changes.json'
METRICS_FILE = 'metrics.json'

# Rolling end-to-end latency ledger. ~48 bytes/sample keeps metrics.json well
# under the 50 KB budget even at the full 96 fetches/day (three days of history).
MAX_LATENCY_SAMPLES = 300
LATENCY_BUDGET_BYTES = 50 * 1024

STALE_LONG_SECONDS = 30 * 86400
STALE_SHORT_SECONDS = 7 * 86400
EXAMPLE_LIMIT = 10
DAY_SECONDS = 86400


def load_json(path, fallback=None):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fallback


def parse_iso(value):
    """Aware UTC datetime for an ISO string, or ``None`` when unusable."""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def percentile(values, p):
    """Linear-interpolation percentile over ``values`` (empty -> ``None``)."""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return vals[0]
    k = (len(vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    return vals[lo] + (vals[hi] - vals[lo]) * (k - lo)


def round_stats(values):
    """p50/p90/max/count, rounded to one decimal (``None`` when empty)."""
    vals = list(values)
    rounded = (lambda v: round(v, 1) if v is not None else None)
    return {
        'p50': rounded(percentile(vals, 50)),
        'p90': rounded(percentile(vals, 90)),
        'max': rounded(max(vals)) if vals else None,
        'count': len(vals),
    }


def observation_at(station_store, fetch_state):
    """Newest fetch/observation timestamp across the stores, or ``None``."""
    candidates = [
        (station_store or {}).get('generated_at'),
        ((station_store or {}).get('last') or {}).get('at'),
        (fetch_state or {}).get('last_fetch_at'),
    ]
    stamps = [dt for dt in (parse_iso(v) for v in candidates) if dt]
    return max(stamps) if stamps else None


def build_latency(observed_at, source_generated_at, previous_samples):
    """Append the current ``observed - source`` delay to the rolling ledger.

    Returns ``(stats, samples)``; the stats add the p95 the freshness view needs
    on top of :func:`round_stats`.
    """
    samples = [
        {'at': str(s.get('at')), 's': round(float(s['s']), 3)}
        for s in (previous_samples or [])
        if isinstance(s, dict) and s.get('at') and s.get('s') is not None
    ]

    if observed_at and source_generated_at:
        delta = (observed_at - source_generated_at).total_seconds()
        if delta >= 0:
            stamp = to_iso(observed_at)
            samples = [s for s in samples if s['at'] != stamp]
            samples.append({'at': stamp, 's': round(delta, 3)})

    samples = samples[-MAX_LATENCY_SAMPLES:]
    values = [s['s'] for s in samples]
    stats = round_stats(values)
    stats['p95'] = round(percentile(values, 95), 1) if values else None
    return stats, samples


def site_freshness(station_store, now):
    """``now - last_seen_at`` distribution over the station change store."""
    ages = []
    for rec in ((station_store or {}).get('s') or {}).values():
        seen = parse_iso((rec or {}).get('u'))
        if seen:
            ages.append(max(0.0, (now - seen).total_seconds()))
    return round_stats(ages)


def stale_summary(station_store, now):
    """Share of stations whose last observed price change is old.

    ``t`` is ``null`` until a change has ever been seen for that key, so those
    entries count as *never changed* rather than stale.
    """
    entries = (station_store or {}).get('s') or {}
    total = len(entries)
    never_changed = 0
    over_short = []
    over_long = 0

    for key, rec in entries.items():
        changed = parse_iso((rec or {}).get('t'))
        if not changed:
            never_changed += 1
            continue
        age = (now - changed).total_seconds()
        if age > STALE_SHORT_SECONDS:
            over_short.append((age, key))
        if age > STALE_LONG_SECONDS:
            over_long += 1

    over_short.sort(reverse=True)
    examples = [{'key': key, 'days': round(age / DAY_SECONDS, 1)} for age, key in over_short[:EXAMPLE_LIMIT]]
    return {
        'total': total,
        'never_changed': never_changed,
        'over_7d': len(over_short),
        'over_30d': over_long,
        'over_7d_share': round(len(over_short) / total, 4) if total else 0.0,
        'over_30d_share': round(over_long / total, 4) if total else 0.0,
        'examples': examples,
    }


def hourly_change_rate(profile):
    """Per-UTC-hour exact re-price rate from the learned profile counters."""
    hours = (profile or {}).get('hours') or {}
    rows = []
    total_changed = 0
    total_hours = 0.0
    for hour in range(24):
        rec = hours.get(str(hour)) or {}
        changed = int(rec.get('changed_stations_exact') or 0)
        observed = float(rec.get('observed_hours') or 0.0)
        rate = changed / observed if observed > 0 else None
        rows.append({
            'hour': hour,
            'changed': changed,
            'observed_hours': round(observed, 2),
            'rate': round(rate, 3) if rate is not None else None,
        })
        total_changed += changed
        total_hours += observed
    return {
        'hours': rows,
        'total_changed': total_changed,
        'mean_rate': round(total_changed / total_hours, 3) if total_hours > 0 else None,
    }


def compute_metrics(data_dir, now):
    """Build the full metrics payload from the committed data files."""
    data_dir = Path(data_dir)
    stations = load_json(data_dir / 'stations.json') or {}
    station_store = load_json(data_dir / STATION_CHANGES_REL) or {}
    fetch_state = load_json(data_dir / 'fetch-state.json') or {}
    profile = load_json(data_dir / 'update-profile.json') or {}
    previous = load_json(data_dir / METRICS_FILE) or {}

    source_generated_at = (stations.get('metadata') or {}).get('generated_at')
    observed_at = observation_at(station_store, fetch_state)
    latency_stats, samples = build_latency(
        observed_at, parse_iso(source_generated_at),
        ((previous.get('latency_seconds') or {}).get('samples') or []),
    )

    return {
        'v': 1,
        'generated_at': to_iso(now),
        'source_generated_at': source_generated_at,
        'observed_at': to_iso(observed_at) if observed_at else None,
        'stations': int((stations.get('metadata') or {}).get('total_stations') or 0),
        'latency_seconds': dict(latency_stats, samples=samples),
        'freshness_seconds': site_freshness(station_store, now),
        'stale': stale_summary(station_store, now),
        'hourly_changes': hourly_change_rate(profile),
    }


def _fmt_seconds(value):
    if value is None:
        return '—'
    if value < 90:
        return f'{value:.0f}s'
    if value < 5400:
        return f'{value / 60:.1f}m'
    if value < 2 * DAY_SECONDS:
        return f'{value / 3600:.1f}h'
    return f'{value / DAY_SECONDS:.1f}d'


def format_summary(m):
    """Readable multi-line digest of :func:`compute_metrics`."""
    latency = m['latency_seconds']
    fresh = m['freshness_seconds']
    stale = m['stale']
    lines = [
        f"Data metrics @ {m['generated_at']} "
        f"(upstream {m['source_generated_at'] or '—'}, observed {m['observed_at'] or '—'})",
        f"  Stations: {m['stations']}",
        f"  Latency (observed - upstream), n={latency['count']}: "
        f"p50 {_fmt_seconds(latency['p50'])} · p90 {_fmt_seconds(latency['p90'])} · "
        f"p95 {_fmt_seconds(latency['p95'])} · max {_fmt_seconds(latency['max'])}",
        f"  Freshness (now - last seen), n={fresh['count']}: "
        f"p50 {_fmt_seconds(fresh['p50'])} · p90 {_fmt_seconds(fresh['p90'])} · "
        f"max {_fmt_seconds(fresh['max'])}",
        f"  Unchanged >7d: {stale['over_7d']}/{stale['total']} "
        f"({stale['over_7d_share'] * 100:.1f}%) · >30d: {stale['over_30d']} "
        f"({stale['over_30d_share'] * 100:.1f}%) · never changed: {stale['never_changed']}",
        f"  Real change rate: {m['hourly_changes']['mean_rate']} stations/h "
        f"({m['hourly_changes']['total_changed']} total)",
    ]
    if stale['examples']:
        sample = ', '.join(f"{e['key']} ({e['days']}d)" for e in stale['examples'][:3])
        lines.append(f"  Oldest unchanged: {sample}")
    return '\n'.join(lines)


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(obj, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--now', help='UTC ISO timestamp override (tests/logs)')
    parser.add_argument('--json', action='store_true',
                        help='print the metrics as JSON instead of a summary')
    parser.add_argument('--no-write', action='store_true',
                        help='do not update data/metrics.json')
    args = parser.parse_args(argv)

    now = parse_iso(args.now) or datetime.now(timezone.utc)
    metrics = compute_metrics(args.data_dir, now)

    if not args.no_write:
        save_json(Path(args.data_dir) / METRICS_FILE, metrics)

    if args.json:
        print(json.dumps(metrics, ensure_ascii=False, separators=(',', ':')))
    else:
        print(format_summary(metrics))
    return 0


if __name__ == '__main__':
    sys.exit(main())

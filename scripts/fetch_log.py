#!/usr/bin/env python3
"""Observation ledger for the local high-frequency probe (issue #74).

The tmux-resident probe (`scripts/local_fetch_loop.sh`) fetches the upstream
snapshot every ~15 minutes **without** passing through the Actions gate
(`should_fetch.py`); its purpose is to produce a trustworthy ground-truth series
of *achievable* fetch intervals for the "necessary interval" evaluation, and to
post one health summary per day to the tracking issue.

This module owns the small JSON document that series lives in:

``data/fetch-log.json``::

    {
      "v": 1,
      "fetches": ["2026-10-12T08:00:12Z", ...],   # completed cycles, newest last
      "days": {"2026-10-12": {"ok": 90, "failed": 1, "retries": 2}},
      "summary_sent": "2026-10-12"                # last local day with a comment
    }

* ``fetches`` is the timestamp array the interval math needs; it is capped at
  :data:`MAX_ENTRIES` (≈ 15 days at one cycle per 15 min) so the file stays tiny
  enough to commit on every cycle.
* ``days`` carries the failure/retry counters the daily summary reports (the
  per-day grain is what keeps the summary honest after a restart).
* ``summary_sent`` is the once-per-day dedupe marker for the health comment.

Everything is standard library only and degrades to an empty ledger when the
file is missing or corrupt, so the probe loop can never be stopped by its own
bookkeeping.
"""

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from metrics import parse_iso, percentile, to_iso  # noqa: E402  (shared math)

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
LOG_FILE = 'fetch-log.json'

# ~15 days at one cycle per 15 minutes; keeps fetch-log.json well under 100 KB.
MAX_ENTRIES = 2000
# Per-day counters are pruned after this many days (matches the raw history tier).
DAY_RETENTION_DAYS = 30
# The evaluation issue (#76) needs at least three days of probe observations.
SAMPLE_MIN_DAYS = 3
# Window for the "actual interval" distribution in the daily summary.
SUMMARY_WINDOW_HOURS = 24


def empty_log():
    """A fresh, valid ledger."""
    return {'v': 1, 'fetches': [], 'days': {}, 'summary_sent': False}


def _int(value):
    """Best-effort int: a torn write or hand edit degrades to 0, never raises."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def load_log(path):
    """Read the ledger, degrading to an empty one on any error.

    Every field is coerced defensively: the shell loop ignores this script's
    exit code, so a raising loader would silently freeze the ledger forever
    instead of self-healing.
    """
    try:
        with open(path, 'r', encoding='utf-8') as f:
            raw = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return empty_log()
    if not isinstance(raw, dict):
        return empty_log()

    raw_fetches = raw.get('fetches')
    fetches = [str(f) for f in raw_fetches if f] if isinstance(raw_fetches, list) else []

    days = {}
    raw_days = raw.get('days')
    if isinstance(raw_days, dict):
        for day, rec in raw_days.items():
            if not isinstance(rec, dict):
                continue
            days[str(day)] = {
                'ok': _int(rec.get('ok')),
                'failed': _int(rec.get('failed')),
                'retries': _int(rec.get('retries')),
            }
    sent = raw.get('summary_sent')
    return {
        'v': 1,
        'fetches': fetches,
        'days': days,
        'summary_sent': str(sent) if sent else False,
    }


def save_log(path, log):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(log, f, ensure_ascii=False, separators=(',', ':'))
        f.write('\n')


def append_fetch(log, at, max_entries=MAX_ENTRIES):
    """Append one completed cycle's timestamp and trim to ``max_entries``.

    Re-appending the same timestamp is a no-op so a retried cycle can never
    inflate the interval series.
    """
    at = str(at)
    fetches = [str(f) for f in log.get('fetches') or []]
    if not fetches or fetches[-1] != at:
        fetches.append(at)
    out = dict(log)
    out['fetches'] = fetches[-max_entries:] if max_entries else []
    return out


def record_result(log, day, ok=True, retries=0, retention_days=DAY_RETENTION_DAYS):
    """Bump this day's ok/failed/retries counters and prune old days."""
    day = str(day)
    out = dict(log)
    days = {str(k): dict(v) for k, v in (log.get('days') or {}).items()}
    rec = days.setdefault(day, {'ok': 0, 'failed': 0, 'retries': 0})
    rec['ok' if ok else 'failed'] += 1
    rec['retries'] += int(retries or 0)

    newest = max([day] + list(days))
    cutoff = (parse_iso(newest + 'T00:00:00Z')
              - timedelta(days=retention_days)).strftime('%Y-%m-%d')
    out['days'] = {d: r for d, r in days.items() if d >= cutoff}
    return out


def intervals_seconds(fetches, since=None):
    """Seconds between consecutive fetches.

    With ``since``, only intervals that *start* inside the window are kept, so a
    gap straddling the boundary (e.g. a laptop that was asleep) cannot pollute
    the reported cadence.
    """
    stamps = [dt for dt in (parse_iso(f) for f in fetches or []) if dt]
    out = []
    for prev, cur in zip(stamps, stamps[1:]):
        if since is not None and prev < since:
            continue
        out.append((cur - prev).total_seconds())
    return out


def needs_summary(log, day):
    """True when ``day`` (local/UTC date string) has no health comment yet."""
    return str(log.get('summary_sent') or '') != str(day)


def mark_summary_sent(log, day):
    out = dict(log)
    out['summary_sent'] = str(day)
    return out


def summary(log, now=None, window_hours=SUMMARY_WINDOW_HOURS):
    """Machine-readable health summary over the ledger (real numbers only)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    fetches = [dt for dt in (parse_iso(f) for f in log.get('fetches') or []) if dt]
    days_covered = (fetches[-1].date() - fetches[0].date()).days + 1 if fetches else 0

    values = intervals_seconds([to_iso(dt) for dt in fetches],
                               since=now - timedelta(hours=window_hours))
    window_start_day = (now - timedelta(hours=window_hours)).strftime('%Y-%m-%d')
    counters = [rec for day, rec in (log.get('days') or {}).items()
                if str(day) >= window_start_day]

    return {
        'generated_at': to_iso(now),
        'days_covered': days_covered,
        'total_fetches': len(fetches),
        'interval_count': len(values),
        'interval_p50': round(percentile(values, 50), 1) if values else None,
        'interval_p90': round(percentile(values, 90), 1) if values else None,
        'last_fetch_at': to_iso(fetches[-1]) if fetches else None,
        'first_fetch_at': to_iso(fetches[0]) if fetches else None,
        'window_hours': window_hours,
        'failures': sum(int(r.get('failed') or 0) for r in counters),
        'retries': sum(int(r.get('retries') or 0) for r in counters),
        'ready_for_76': days_covered >= SAMPLE_MIN_DAYS,
    }


def _minutes(value):
    return '样本不足' if value is None else f'{value / 60:.1f} 分钟'


def format_summary(summary):
    """Markdown body for the daily comment on the tracking issue (#78)."""
    if summary.get('ready_for_76'):
        verdict = f'✅ 可以开工 #76（已积累 ≥ {SAMPLE_MIN_DAYS} 天）'
    else:
        verdict = f'⏳ 尚未达到 #76 所需样本（需 ≥ {SAMPLE_MIN_DAYS} 天，当前 {summary.get("days_covered")} 天）'

    lines = [
        f'### 本机探针健康汇报 — {summary.get("generated_at")}',
        '',
        f'- 已累积天数: {summary.get("days_covered")} 天',
        f'- 累计抓取次数: {summary.get("total_fetches")}',
        (f'- 最近 {summary.get("window_hours")}h 实际间隔: '
         f'p50 {_minutes(summary.get("interval_p50"))} / p90 {_minutes(summary.get("interval_p90"))} '
         f'(n={summary.get("interval_count")})'),
        f'- 最近抓取时间: {summary.get("last_fetch_at")}',
        f'- 失败/重试: {summary.get("failures")} / {summary.get("retries")}',
        f'- #76 样本: {verdict}',
        '',
        '_数据来源：`data/fetch-log.json`（本机探针实际完成的一次完整抓取链路）。_',
    ]
    return '\n'.join(lines)


def _common_parser(sub):
    sub.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)

    ap = sub.add_parser('append', help='record one probe cycle')
    _common_parser(ap)
    ap.add_argument('--at', help='UTC ISO timestamp of the cycle (default: now)')
    ap.add_argument('--retries', type=int, default=0,
                    help='push retries spent by this cycle')
    ap.add_argument('--fail', action='store_true',
                    help='record a failed cycle (counts, but adds no timestamp)')

    sp = sub.add_parser('summary', help='print the health summary')
    _common_parser(sp)
    sp.add_argument('--now', help='UTC ISO timestamp override')
    sp.add_argument('--window-hours', type=int, default=SUMMARY_WINDOW_HOURS)
    sp.add_argument('--json', action='store_true', help='print JSON instead of Markdown')

    np_ = sub.add_parser('needs-summary', help='true when the day has no comment yet')
    _common_parser(np_)
    np_.add_argument('--day', required=True)

    mp = sub.add_parser('mark-summary', help='remember that the day was reported')
    _common_parser(mp)
    mp.add_argument('--day', required=True)

    args = parser.parse_args(argv)
    log_path = Path(args.data_dir) / LOG_FILE

    if args.command == 'append':
        now = parse_iso(args.at) or datetime.now(timezone.utc)
        day = now.strftime('%Y-%m-%d')
        log = load_log(log_path)
        if args.fail:
            log = record_result(log, day, ok=False, retries=args.retries)
        else:
            log = append_fetch(log, to_iso(now))
            log = record_result(log, day, ok=True, retries=args.retries)
        save_log(log_path, log)
        return 0

    if args.command == 'summary':
        now = parse_iso(args.now) or datetime.now(timezone.utc)
        report = summary(load_log(log_path), now, window_hours=args.window_hours)
        print(json.dumps(report, ensure_ascii=False, indent=2) if args.json
              else format_summary(report))
        return 0

    if args.command == 'needs-summary':
        print('true' if needs_summary(load_log(log_path), args.day) else 'false')
        return 0

    log = mark_summary_sent(load_log(log_path), args.day)
    save_log(log_path, log)
    return 0


if __name__ == '__main__':
    sys.exit(main())

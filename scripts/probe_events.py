#!/usr/bin/env python3
"""Local probe event log (issue #92) — samples outside the repository.

The high-frequency probe (#74) used to commit every data snapshot every cycle,
which cost ≈39 MiB/day of pack growth (73 % of it `data/stations.json` alone)
for zero user-visible value: the probe pushes never trigger a site deploy
(`static.yml`'s `push.paths` does not include `data/**`). #92 keeps the probe
running — it is the sampling-bias-free ground truth — but moves its samples
into a **local, gitignored** JSONL log:

    data/probe-local/events.jsonl
    {"t":"2026-10-16T20:04:25Z","changed":135,"keys":["-73.12345,45.12345", ...],"stations":2465}

One line per completed cycle:

* ``t``        — the fetch time of this cycle (``station-changes.json``'s
                 ``last.at``, i.e. the real observation instant, not wall clock);
* ``changed``  — how many stations re-priced in this cycle, read from the
                 store's own ``last.changed`` (the same number
                 ``update_profile.py`` reports as ``changed_stations_exact``);
* ``keys``     — the changed stations, taken from the per-station
                 ``last_changed_at`` stamps (``update_profile.stations_changed_at``,
                 the exact same rule the profile uses). Capped at ``--max-keys``
                 for size: it is an *analysis* input, so ``changed`` stays
                 authoritative when the list is truncated;
* ``stations`` — stations observed in the cycle (the stale-share denominator).

Nothing here fetches or writes ``data/`` snapshots: this script only *reads*
``data/history/station-changes.json`` (which the pipeline writes every cycle)
and appends to its own gitignored log. The consumers are
``scripts/interval_report.py`` / ``scripts/schedule_eval.py`` via
``--events-file``, which fall back to the git revision history when this log is
absent (CI, fresh clones).

CLI::

    python3 scripts/probe_events.py append [--events-file PATH] [--changes PATH]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from update_profile import stations_changed_at  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EVENTS_FILE = ROOT / 'data' / 'probe-local' / 'events.jsonl'
DEFAULT_CHANGES_FILE = ROOT / 'data' / 'history' / 'station-changes.json'

# Retention/size bounds: 2000 cycles ≈ 20 days at 15 min, and at most 100 keys
# per line keeps the log < 200 KB/day (budget: 300 KB/day).
MAX_ENTRIES = 2000
DEFAULT_MAX_KEYS = 100


def build_event(store, max_keys=DEFAULT_MAX_KEYS):
    """``station-changes.json`` -> one event line (``None`` when not an event).

    The store is written by ``update_profile.py`` on every fetch; its ``last.at``
    is the fetch instant and ``last.changed`` the number of re-priced stations.
    A store without a fetch time (empty/partial file) yields no event, so a
    broken cycle never fabricates an observation.
    """
    if not isinstance(store, dict):
        return None
    last = store.get('last') or {}
    at = str(last.get('at') or '')
    if not at:
        return None
    try:
        keys = stations_changed_at(store, at)
    except AttributeError:  # pragma: no cover - defensive
        return None
    if max_keys and max_keys > 0:
        keys = keys[:int(max_keys)]
    stations = last.get('observed')
    if not isinstance(stations, int) or stations < 0:
        stations = len(store.get('s') or {})
    changed = last.get('changed')
    if not isinstance(changed, int) or changed < 0:
        changed = len(keys)
    return {'t': at, 'changed': int(changed), 'keys': keys, 'stations': int(stations)}


def load_events(path):
    """Read an event log, skipping every line that is not a usable record.

    Dirty input may only shrink the sample — never raise: the consumers run in
    CI and on freshly cloned machines.
    """
    path = Path(path)
    if not path.exists():
        return []
    events = []
    try:
        text = path.read_text(encoding='utf-8')
    except OSError:
        return []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get('t'):
            events.append(event)
    return events


def append_event(path, event, max_entries=MAX_ENTRIES):
    """Append ``event`` to the JSONL log; ``False`` when it was a replay.

    The same fetch time must not be recorded twice (a retried cycle, or two
    probe processes), otherwise the interval series would see a fake 0-minute
    gap. Trailing entries beyond ``max_entries`` are dropped so the log stays
    bounded forever.
    """
    path = Path(path)
    if event is None:
        return False
    existing = load_events(path)
    if existing and str(existing[-1].get('t')) == str(event.get('t')):
        return False
    events = existing + [event]
    if max_entries and max_entries > 0 and len(events) > int(max_entries):
        events = events[-int(max_entries):]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8') as handle:
        for item in events:
            handle.write(json.dumps(item, ensure_ascii=False, separators=(',', ':')) + '\n')
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('append',),
                        help='append one event for the current change store')
    parser.add_argument('--events-file', default=str(DEFAULT_EVENTS_FILE))
    parser.add_argument('--changes', default=str(DEFAULT_CHANGES_FILE),
                        help='the station change store written by update_profile.py')
    parser.add_argument('--max-entries', type=int, default=MAX_ENTRIES)
    parser.add_argument('--max-keys', type=int, default=DEFAULT_MAX_KEYS)
    args = parser.parse_args(argv)

    changes_path = Path(args.changes)
    try:
        store = json.loads(changes_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as error:
        print(f'error: cannot read the station-changes store at {changes_path}: {error}',
              file=sys.stderr)
        return 1

    event = build_event(store, max_keys=args.max_keys)
    if event is None:
        print(f'no fetch time in {changes_path}; no event appended', file=sys.stderr)
        return 1

    if not append_event(args.events_file, event, max_entries=args.max_entries):
        print(f'cycle {event["t"]} already recorded; no event appended')
        return 0

    print(json.dumps(event, ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())

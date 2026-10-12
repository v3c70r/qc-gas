#!/usr/bin/env python3
"""Fail the CI job when the committed data/ directory grows too large.

GitHub's soft repo guidance and 100 MB per-file limit make a small guard cheap
insurance against silent repository bloat. The region-level history store is
designed to grow only a few tens of KB per year, and the station-level daily
store plus the station-level change store are both retained for a fixed 180-day
window, so the default budgets are very generous relative to the expected size.
"""

import os
import sys

MAX_DATA_MB = int(os.environ.get('MAX_DATA_MB', '40'))
MAX_FILE_MB = 90  # keep well below GitHub's 100 MB per-file hard limit
# Station-level change store: ~2.5k stations * ~90 B/entry ~= 0.25 MB at steady
# state (it holds only the latest change/observation per key, pruned after 180
# days), so anything past a few MB means the pruning stopped working.
MAX_STATION_CHANGES_MB = int(os.environ.get('MAX_STATION_CHANGES_MB', '8'))

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')
STATION_CHANGES_REL = os.path.join('history', 'station-changes.json')


def scan(data_dir):
    """Return ``(total_bytes, [(relpath, size), ...])`` for everything under dir."""
    total = 0
    files = []
    for root, _dirs, names in os.walk(data_dir):
        for name in names:
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            total += size
            rel = os.path.relpath(path, data_dir).replace(os.sep, '/')
            files.append((rel, size))
    files.sort(key=lambda item: item[1], reverse=True)
    return total, files


def budget_errors(total_mb, largest_mb, changes_mb):
    """Human-readable budget violations (empty list means the commit is safe)."""
    errors = []
    if total_mb > MAX_DATA_MB:
        errors.append(f'data/ total {total_mb:.2f} MB exceeds budget {MAX_DATA_MB} MB. '
                      f'Refusing to continue so the repo does not silently bloat.')
    if changes_mb > MAX_STATION_CHANGES_MB:
        errors.append(f'{STATION_CHANGES_REL} is {changes_mb:.2f} MB, above its '
                      f'{MAX_STATION_CHANGES_MB} MB budget: check station-change '
                      f'pruning in update_profile.py.')
    if largest_mb > MAX_FILE_MB:
        errors.append(f'a single file exceeds {MAX_FILE_MB} MB (GitHub hard limit 100 MB).')
    return errors


def main():
    total, files = scan(DATA_DIR)
    file_count = len(files)
    largest_rel, largest_size = files[0] if files else ('', 0)
    changes_size = dict(files).get(STATION_CHANGES_REL, 0)

    total_mb = total / (1024 * 1024)
    largest_mb = largest_size / (1024 * 1024)
    changes_mb = changes_size / (1024 * 1024)

    print(f'data/ size: {total_mb:.2f} MB across {file_count} files '
          f'(budget {MAX_DATA_MB} MB, largest {largest_rel} at {largest_mb:.2f} MB)')
    print(f'Station change store: {changes_mb:.3f} MB '
          f'(budget {MAX_STATION_CHANGES_MB} MB)')

    if largest_size > MAX_FILE_MB * 1024 * 1024:
        print(f'WARNING: largest file exceeds {MAX_FILE_MB} MB: {largest_rel}',
              file=sys.stderr)

    errors = budget_errors(total_mb, largest_mb, changes_mb)
    for message in errors:
        print(f'ERROR: {message}', file=sys.stderr)
    if errors:
        sys.exit(1)


if __name__ == '__main__':
    main()

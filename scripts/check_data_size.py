#!/usr/bin/env python3
"""Fail the CI job when the committed data/ directory grows too large.

GitHub's soft repo guidance and 100 MB per-file limit make a small guard cheap
insurance against silent repository bloat. The region-level history store is
designed to grow only a few tens of KB per year, so the default budget is very
generous relative to the expected size.
"""

import os
import sys

MAX_DATA_MB = int(os.environ.get('MAX_DATA_MB', '40'))
MAX_FILE_MB = 90  # keep well below GitHub's 100 MB per-file hard limit

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data')


def main():
    total = 0
    largest = (0, '')
    file_count = 0

    for root, _dirs, files in os.walk(DATA_DIR):
        for name in files:
            path = os.path.join(root, name)
            try:
                size = os.path.getsize(path)
            except OSError:
                continue
            total += size
            file_count += 1
            if size > largest[0]:
                largest = (size, path)

    total_mb = total / (1024 * 1024)
    largest_mb = largest[0] / (1024 * 1024)
    rel_largest = os.path.relpath(largest[1], DATA_DIR) if largest[1] else ''

    print(f'data/ size: {total_mb:.2f} MB across {file_count} files '
          f'(budget {MAX_DATA_MB} MB, largest {rel_largest} at {largest_mb:.2f} MB)')

    if largest[0] > MAX_FILE_MB * 1024 * 1024:
        print(f'WARNING: largest file exceeds {MAX_FILE_MB} MB: {rel_largest}',
              file=sys.stderr)

    if total_mb > MAX_DATA_MB:
        print(f'ERROR: data/ total {total_mb:.2f} MB exceeds budget '
              f'{MAX_DATA_MB} MB. Refusing to continue so the repo does not '
              f'silently bloat.', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()

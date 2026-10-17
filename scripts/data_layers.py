#!/usr/bin/env python3
"""Which files under ``data/`` are derived snapshots and which are state (#103).

Issue #103 measured what the Actions data commit actually costs: ≈19 files and
≈24.5 MiB of pack growth per day, dominated by snapshots that are **regenerated
on every fetch** — ``stations.json`` (+``.br``), ``stats.json``, ``metrics.json``
and the frontend mirror ``history.json``. They still have to reach the site, but
there is no reason for every one of them to enter git history 65 times a day.

Two layers, one rule each:

**Derived** — regenerated from the upstream snapshot on every admitted fetch and
rebuilt from the tier files, so committing them adds nothing but bytes:

* ``data/stations.json`` — normalized upstream snapshot (``process_data.py``)
* ``data/stats.json`` — summary of that snapshot
* ``data/metrics.json`` — freshness/latency digest (``metrics.py``)
* ``data/history.json`` — frontend mirror rebuilt from the tiers
  (``append_history.py`` → ``build_merged``)

Derived files are written to the workspace as before and reach the Pages
artifact through ``actions/upload-artifact`` in ``update-data.yml``; they are
never ``git add``-ed. Their ``.br`` siblings are build output only (nothing in
``js/`` reads them and GitHub Pages does not negotiate precompressed files), so
**no** ``.br`` file is committed either.

**Persistent** — state that cannot be recomputed from the latest snapshot:

* ``data/history/**`` — the raw/daily/monthly tier store, ``station-history``
  and ``station-changes`` (the git revision history of the last one is
  ``interval_report.py``'s observation series)
* ``data/update-profile.json`` — learned re-price profile (cumulative)
* ``data/fetch-log.json`` — probe ledger
* ``data/fetch-schedule.json`` — committed timetable / plan
* ``data/fetch-state.json`` — gate state, one record per admitted Actions run
* ``data/metrics-ledger.json`` — rolling latency samples (#103: ``metrics.json``
  is no longer committed, so the ledger needs a home of its own)
* ``data/regie-margin.json``, ``data/fetch-interval.json``,
  ``data/trigger-interval.json`` — evidence/bulletin files refreshed on demand

Anything else under ``data/`` is unclassified: ``python3 scripts/data_layers.py
--check`` fails (and ``scripts/test_data_layers.py`` asserts it) so a new data
file has to be placed in one of the two layers deliberately.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_DIR = ROOT / 'data'
DATA_PREFIX = 'data/'

DERIVED = 'derived'
PERSISTENT = 'persistent'
IGNORED = 'ignored'

# Regenerated on every fetch / rebuilt from the tiers: Pages artifact only.
DERIVED_FILES = ('stations.json', 'stats.json', 'metrics.json', 'history.json')

# State that cannot be recomputed: committed on every fetch.
PERSISTENT_FILES = (
    'fetch-interval.json',
    'fetch-log.json',
    'fetch-schedule.json',
    'fetch-state.json',
    'metrics-ledger.json',
    'regie-margin.json',
    'trigger-interval.json',
    'update-profile.json',
)
PERSISTENT_DIRS = ('history',)

# Local scratch space that never belongs to either layer (`data/probe-local/`,
# already covered by `.gitignore`).
IGNORED_DIRS = ('probe-local',)

BROTLI_SUFFIX = '.br'


def _rel(rel):
    """Normalize a path to a ``data/``-relative POSIX string."""
    text = str(rel).replace('\\', '/').strip()
    if text.startswith(DATA_PREFIX):
        text = text[len(DATA_PREFIX):]
    while text.startswith('./'):
        text = text[2:]
    return text.lstrip('/')


def is_brotli(rel):
    return _rel(rel).endswith(BROTLI_SUFFIX)


def is_derived(rel):
    """True for a derived snapshot *including* its ``.br`` sibling."""
    base = _rel(rel)
    if base.endswith(BROTLI_SUFFIX):
        base = base[:-len(BROTLI_SUFFIX)]
    return base in DERIVED_FILES


def is_persistent(rel):
    """True only for state that is committed (``.br`` siblings never are)."""
    base = _rel(rel)
    if not base or base.endswith(BROTLI_SUFFIX):
        return False
    if base in PERSISTENT_FILES:
        return True
    head = base.split('/', 1)[0]
    return head in PERSISTENT_DIRS and base != head


def classify(rel):
    """``DERIVED`` / ``PERSISTENT`` / ``IGNORED`` / ``None`` (unclassified)."""
    base = _rel(rel)
    if not base:
        return None
    if base.endswith(BROTLI_SUFFIX):
        # Compressed siblings are build output for every layer (see module doc).
        return IGNORED
    head = base.split('/', 1)[0]
    if head in IGNORED_DIRS:
        return IGNORED
    if base in DERIVED_FILES:
        return DERIVED
    if is_persistent(base):
        return PERSISTENT
    return None


def iter_files(data_dir=DEFAULT_DATA_DIR):
    """Every file under ``data_dir`` as ``(relpath, path)``, sorted."""
    root = Path(data_dir)
    if not root.is_dir():
        return []
    out = []
    for path in sorted(root.rglob('*')):
        if path.is_file():
            out.append((path.relative_to(root).as_posix(), path))
    return out


def commit_pathspecs(data_dir=DEFAULT_DATA_DIR):
    """``git add`` pathspecs: the persistent files that currently exist."""
    return [DATA_PREFIX + rel for rel, _ in iter_files(data_dir)
            if is_persistent(rel)]


def artifact_paths(data_dir=DEFAULT_DATA_DIR):
    """Files the Pages build needs but git does not keep (derived + ``.br``)."""
    out = []
    for rel, _ in iter_files(data_dir):
        if rel.split('/', 1)[0] in IGNORED_DIRS:
            continue
        if rel.endswith(BROTLI_SUFFIX) or is_derived(rel):
            out.append(DATA_PREFIX + rel)
    return out


def unclassified(data_dir=DEFAULT_DATA_DIR):
    """Files under ``data_dir`` that belong to neither layer."""
    return [rel for rel, _ in iter_files(data_dir) if classify(rel) is None]


def _write_lines(path, lines):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(f'{line}\n' for line in lines), encoding='utf-8')


def _write_github_output(path, commit, artifact):
    with open(path, 'a', encoding='utf-8') as handle:
        handle.write('data_commit_pathspecs<<DATA_LAYERS_EOF\n')
        handle.write(''.join(f'{line}\n' for line in commit))
        handle.write('DATA_LAYERS_EOF\n')
        handle.write('data_artifact_paths<<DATA_LAYERS_EOF\n')
        handle.write(''.join(f'{line}\n' for line in artifact))
        handle.write('DATA_LAYERS_EOF\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--data-dir', default=str(DEFAULT_DATA_DIR))
    parser.add_argument('--check', action='store_true',
                        help='exit 1 when a file under data/ belongs to neither layer')
    parser.add_argument('--pathspec-file',
                        help='write the persistent pathspecs (one per line) for '
                             '`git add --pathspec-from-file`')
    parser.add_argument('--artifact-file',
                        help='write the derived snapshot paths (one per line)')
    parser.add_argument('--github-output',
                        help='append the path lists as multiline step outputs')
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir)
    commit = commit_pathspecs(data_dir)
    artifact = artifact_paths(data_dir)
    unknown = unclassified(data_dir)

    if args.check:
        for rel in unknown:
            print(f'ERROR: data/{rel} is neither a derived snapshot nor persistent '
                  f'state: classify it in scripts/data_layers.py and '
                  f'docs/history-data.md (issue #103).', file=sys.stderr)
        if unknown:
            return 1

    if args.pathspec_file:
        _write_lines(args.pathspec_file, commit)
    if args.artifact_file:
        _write_lines(args.artifact_file, artifact)
    if args.github_output:
        _write_github_output(args.github_output, commit, artifact)

    if not (args.pathspec_file or args.artifact_file or args.github_output):
        print(f'derived ({len(artifact)} artifact-only files):')
        for rel in artifact:
            print(f'  {rel}')
        print(f'persistent ({len(commit)} committed files):')
        for rel in commit:
            print(f'  {rel}')
        if unknown:
            print('unclassified:')
            for rel in unknown:
                print(f'  data/{rel}')
    return 0


if __name__ == '__main__':
    sys.exit(main())

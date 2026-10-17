#!/usr/bin/env python3
"""Tests for scripts/data_layers.py — derived vs persistent `data/` files (#103).

Issue #103 measured the Actions data commit at ~19 files / ≈24.5 MiB of pack
growth per day and split `data/` into two layers:

* **derived** — pure snapshots regenerated on every fetch (``stations.json``,
  ``stats.json``, ``metrics.json``, ``history.json``). They are still written to
  the workspace and still shipped to the site through the Pages artifact, they
  are just not committed to git any more.
* **persistent** — state that cannot be recomputed: the tiered history store,
  the station change store, the learned profile, the gate/probe ledgers.

This suite pins the classification **and** the wiring, because a classification
that only lives in a doc drifts: every file under ``data/`` must be classified,
the workflow must commit exactly the persistent pathspecs, the derived snapshot
must be uploaded/downloaded as the Pages artifact, git must not track any
derived file or any ``.br`` sibling, and the docs must state the rule for new
files.
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import data_layers  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / 'data'
GITIGNORE = ROOT / '.gitignore'
UPDATE_YML = ROOT / '.github' / 'workflows' / 'update-data.yml'
STATIC_YML = ROOT / '.github' / 'workflows' / 'static.yml'
HISTORY_DOC = ROOT / 'docs' / 'history-data.md'

DERIVED_SNAPSHOTS = ('stations.json', 'stats.json', 'metrics.json', 'history.json')
PERSISTENT_STATE = (
    'update-profile.json',
    'fetch-log.json',
    'fetch-schedule.json',
    'fetch-state.json',
    'metrics-ledger.json',
    'regie-margin.json',
    'history/index.json',
    'history/2026-10.json',
    'history/monthly.json',
    'history/daily.json',
    'history/station-history.json',
    'history/station-changes.json',
)


def git(*args, cwd=ROOT):
    return subprocess.run(['git', *args], cwd=str(cwd), capture_output=True, text=True)


def seed_data_dir(data_dir):
    """A representative `data/` tree: derived + persistent + `.br` + scratch."""
    files = {
        'stations.json': '{}',
        'stations.json.br': 'br',
        'stats.json': '{}',
        'stats.json.br': 'br',
        'metrics.json': '{}',
        'history.json': '{}',
        'history.json.br': 'br',
        'update-profile.json': '{}',
        'fetch-log.json': '{}',
        'fetch-schedule.json': '{}',
        'fetch-state.json': '{}',
        'history/index.json': '{}',
        'history/index.json.br': 'br',
        'history/2026-10.json': '{}',
        'history/station-changes.json': '{}',
        'probe-local/events.jsonl': '{}\n',
    }
    data_dir = Path(data_dir)
    for rel, payload in files.items():
        path = data_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding='utf-8')
    return data_dir


class ClassificationTest(unittest.TestCase):
    def test_snapshots_regenerated_every_fetch_are_derived(self):
        for rel in DERIVED_SNAPSHOTS:
            with self.subTest(rel=rel):
                self.assertTrue(data_layers.is_derived(rel))
                self.assertFalse(data_layers.is_persistent(rel))
                self.assertEqual(data_layers.classify(rel), data_layers.DERIVED)

    def test_persistent_state_is_never_derived(self):
        for rel in PERSISTENT_STATE:
            with self.subTest(rel=rel):
                self.assertTrue(data_layers.is_persistent(rel), rel)
                self.assertFalse(data_layers.is_derived(rel), rel)
                self.assertEqual(data_layers.classify(rel), data_layers.PERSISTENT)

    def test_brotli_siblings_are_never_committed(self):
        # Nothing in the app reads `*.json.br` (GitHub Pages does not negotiate
        # precompressed files) and each one is a full new blob per commit, so
        # they are build artifacts only, for *every* data file.
        for rel in ('stations.json.br', 'metrics.json.br', 'history.json.br',
                    'history/station-changes.json.br', 'history/2026-10.json.br',
                    'regie-margin.json.br'):
            with self.subTest(rel=rel):
                self.assertFalse(data_layers.is_persistent(rel), rel)
                self.assertNotEqual(data_layers.classify(rel), data_layers.PERSISTENT)

    def test_probe_local_scratch_files_are_not_classified(self):
        self.assertEqual(data_layers.classify('probe-local/events.jsonl'),
                         data_layers.IGNORED)

    def test_paths_may_be_repo_relative(self):
        self.assertTrue(data_layers.is_derived('data/stations.json'))
        self.assertTrue(data_layers.is_persistent('data/history/station-changes.json'))

    def test_commit_pathspecs_cover_exactly_the_persistent_files(self):
        # Hermetic: a fresh checkout of this branch has *only* the persistent
        # files in `data/` (the derived snapshots are not committed since #103),
        # so asserting against the live directory would be a checkout-shape test
        # instead of a classification test.
        with tempfile.TemporaryDirectory() as tmp:
            data = seed_data_dir(Path(tmp) / 'data')
            specs = data_layers.commit_pathspecs(data)
        self.assertEqual(sorted(specs), [
            'data/fetch-log.json',
            'data/fetch-schedule.json',
            'data/fetch-state.json',
            'data/history/2026-10.json',
            'data/history/index.json',
            'data/history/station-changes.json',
            'data/update-profile.json',
        ])
        # The latency ledger is persistent even when this workspace has not
        # produced one yet (`metrics.py` writes it on the next run).
        self.assertIn('metrics-ledger.json', data_layers.PERSISTENT_FILES)

    def test_artifact_paths_are_the_derived_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = seed_data_dir(Path(tmp) / 'data')
            paths = data_layers.artifact_paths(data)
        # Every derived snapshot plus its `.br` sibling ships with the artifact;
        # committed persistent state comes from the checkout, not the artifact.
        self.assertEqual(sorted(paths), sorted([
            'data/history.json',
            'data/history.json.br',
            'data/history/index.json.br',
            'data/metrics.json',
            'data/stations.json',
            'data/stations.json.br',
            'data/stats.json',
            'data/stats.json.br',
        ]))

    def test_live_data_dir_pathspecs_never_include_derived_or_brotli(self):
        # Fresh-clone safe: asserts invariants only, never that a given file
        # exists (a new clone legitimately has no `stations.json`).
        for spec in data_layers.commit_pathspecs(DATA_DIR):
            self.assertFalse(spec.endswith('.br'), spec)
            self.assertNotIn(spec, {f'data/{rel}' for rel in DERIVED_SNAPSHOTS})
        for path in data_layers.artifact_paths(DATA_DIR):
            self.assertFalse(data_layers.is_persistent(path), path)

    def test_unclassified_reports_a_new_data_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp) / 'data'
            (data / 'history').mkdir(parents=True)
            (data / 'stations.json').write_text('{}', encoding='utf-8')
            (data / 'history' / '2026-10.json').write_text('{}', encoding='utf-8')
            (data / 'brand-new-derived-thing.json').write_text('{}', encoding='utf-8')
            self.assertEqual(data_layers.unclassified(data), ['brand-new-derived-thing.json'])

    def test_repo_data_dir_is_fully_classified(self):
        # Whenever a new data file appears, it must be classified here (and in
        # docs/history-data.md) instead of silently joining the commit.
        self.assertEqual(data_layers.unclassified(DATA_DIR), [])


class GitTrackingTest(unittest.TestCase):
    def test_git_does_not_track_derived_snapshots(self):
        tracked = git('ls-files', '--', *[f'data/{rel}' for rel in DERIVED_SNAPSHOTS]).stdout.split()
        self.assertEqual(tracked, [])

    def test_git_does_not_track_any_brotli_sibling(self):
        tracked = [p for p in git('ls-files', '--', 'data').stdout.split() if p.endswith('.br')]
        self.assertEqual(tracked, [])

    def test_gitignore_covers_derived_snapshots_and_brotli(self):
        for rel in DERIVED_SNAPSHOTS:
            with self.subTest(rel=rel):
                path = f'data/{rel}'
                self.assertEqual(git('check-ignore', '-q', path).returncode, 0, path)
        for path in ('data/stations.json.br', 'data/history/2026-10.json.br'):
            with self.subTest(path=path):
                self.assertEqual(git('check-ignore', '-q', path).returncode, 0, path)


class WorkflowWiringTest(unittest.TestCase):
    def setUp(self):
        self.update = UPDATE_YML.read_text(encoding='utf-8')
        self.static = STATIC_YML.read_text(encoding='utf-8')
        self.gitignore = GITIGNORE.read_text(encoding='utf-8')

    def test_commit_step_adds_only_the_persistent_pathspecs(self):
        self.assertNotIn('git add data/', self.update)
        self.assertIn('data_layers.py', self.update)
        self.assertIn('--pathspec-file', self.update)
        self.assertIn('git add --pathspec-from-file', self.update)

    def test_workflow_uploads_the_derived_snapshot_as_an_artifact(self):
        self.assertIn('uses: actions/upload-artifact', self.update)
        self.assertIn('data_layers.py', self.update)
        self.assertIn('--github-output', self.update)

    def test_pages_build_restores_the_derived_snapshot(self):
        self.assertIn('uses: actions/download-artifact', self.static)
        download = self.static.index('actions/download-artifact')
        copy_data = self.static.index('cp -r data dist/')
        self.assertLess(download, copy_data,
                        'the artifact has to be restored before the site copies data/')

    def test_workflow_runs_the_classification_check(self):
        self.assertIn('data_layers.py --check', self.update)

    def test_workflow_requires_the_frontend_snapshots_before_deploying(self):
        # Review round 1: the derived snapshots are no longer in git, so a
        # partially failed pipeline (download/process/append_history) would
        # upload an artifact without them and deploy a broken site. The two the
        # frontend awaits unconditionally (`js/dashboard.js` -> loadHistoryData,
        # `js/map.js` -> stations.json) must be present or the run has to fail
        # so the previous deployment keeps serving.
        guard = self.update
        self.assertIn('Require the derived snapshots the site awaits', guard)
        step = guard.split('Require the derived snapshots the site awaits', 1)[1].split('- name:', 1)[0]
        for rel in ('data/stations.json', 'data/history.json'):
            with self.subTest(rel=rel):
                self.assertIn(rel, step)
        self.assertIn('test -s "$f"', step)


class DocsTest(unittest.TestCase):
    def test_docs_state_the_derived_vs_persistent_rule(self):
        doc = HISTORY_DOC.read_text(encoding='utf-8')
        for needle in ('issue #103', 'Derived snapshots', 'Persistent state',
                       'data/stations.json', 'data/history.json',
                       'data/history/station-changes.json', 'data/metrics-ledger.json',
                       'upload-artifact'):
            self.assertIn(needle, doc, needle)


if __name__ == '__main__':
    unittest.main()

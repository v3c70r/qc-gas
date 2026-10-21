#!/usr/bin/env python3
"""Tests for ``scripts/probe_doctor.sh`` (issue #121).

The probe's clone drifted into states the loop could not repair on its own:
7 unmerged (`UU`) files, a half-finished rebase index and a
`data/history/station-history.json` that had grown to 4.2 MB against 1.40 MB on
`master` because several days of `history` stage ran twice. Repairing that was
one `git reset --hard origin/master` — done by hand, undocumented, and
irreproducible. ``scripts/probe_doctor.sh`` is the scripted version:

* **report** by default (exit 1 when anything is wrong, 0 when healthy),
* ``--fix`` repairs: abort a leftover half-merge, then `reset --hard '@{u}'`
  when every dirty path lives under `data/` (the probe's own footprint),
* it **refuses** (exit 2) as soon as a path outside `data/` is dirty, exactly
  like the loop's own `probe_owns_worktree` guard (issue #92).

Hermetic: throwaway bare remote + clone, no network, no real probe data.
"""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOCTOR = ROOT / 'scripts' / 'probe_doctor.sh'


class ProbeDoctorTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.remote = tmp / 'origin.git'
        self.clone = tmp / 'probe'

        self._git(tmp, 'init', '-q', '--bare', '-b', 'master', str(self.remote))
        seed = tmp / 'seed'
        self._git(tmp, 'init', '-q', '-b', 'master', str(seed))
        (seed / 'js').mkdir()
        (seed / 'js' / 'app.js').write_text('export const version = 1;\n', encoding='utf-8')
        (seed / 'data' / 'history').mkdir(parents=True)
        (seed / 'data' / 'history' / 'station-history.json').write_text(
            '{}\n' * 4000, encoding='utf-8')
        self._git(seed, 'add', '-A')
        self._commit(seed, 'seed')
        self._git(seed, 'remote', 'add', 'origin', str(self.remote))
        self._git(seed, 'push', '-q', '-u', 'origin', 'master')
        self._git(tmp, 'clone', '-q', str(self.remote), str(self.clone))
        self._git(self.clone, 'config', 'user.email', 'probe@example.com')
        self._git(self.clone, 'config', 'user.name', 'probe')
        self.seed = seed
        # The doctor ships *inside* the probe clone and inspects its own
        # repository, so the test copies it there instead of pointing it at the
        # agent's worktree.
        (self.clone / 'scripts').mkdir(exist_ok=True)
        shutil.copy(DOCTOR, self.clone / 'scripts' / 'probe_doctor.sh')
        self.doctor = self.clone / 'scripts' / 'probe_doctor.sh'

    # ── helpers ─────────────────────────────────────────────────────────────

    def _git(self, cwd, *args, check=True):
        return subprocess.run(['git', *args], cwd=str(cwd), capture_output=True,
                              text=True, check=check)

    def _commit(self, cwd, message):
        self._git(cwd, '-c', 'user.email=t@example.com', '-c', 'user.name=t',
                  'commit', '-q', '--allow-empty', '-m', message)

    def _doctor(self, *args):
        return subprocess.run(['bash', 'scripts/probe_doctor.sh', *args],
                              cwd=str(self.clone), capture_output=True, text=True,
                              timeout=120)

    def _dirty_data_file(self):
        return self.clone / 'data' / 'history' / 'station-changes.json'

    def _half_merged(self):
        """A real merge conflict on a tracked `data/` file (the `UU` residue)."""
        target = self._dirty_data_file()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('{"v": 1, "s": {}}\n', encoding='utf-8')
        self._git(self.clone, 'add', '-A')
        self._commit(self.clone, 'seed the data store')
        self._git(self.clone, 'push', '-q', 'origin', 'master')
        self._git(self.clone, 'checkout', '-q', '-b', 'writer')
        target.write_text('{"v": 1, "s": {"remote": 1}}\n', encoding='utf-8')
        self._git(self.clone, 'commit', '-q', '-am', 'writer')
        self._git(self.clone, 'checkout', '-q', 'master')
        target.write_text('{"v": 1, "s": {"local": 1}}\n', encoding='utf-8')
        self._git(self.clone, 'commit', '-q', '-am', 'local')
        merge = self._git(self.clone, 'merge', 'writer', check=False)
        self.assertNotEqual(merge.returncode, 0, 'expected a merge conflict')
        return target

    def _bloated(self):
        """`station-history.json` inflated locally the way the probe inflated it."""
        target = self.clone / 'data' / 'history' / 'station-history.json'
        master = self._git(self.clone, 'show', 'HEAD:data/history/station-history.json').stdout
        target.write_text(master * 4, encoding='utf-8')
        return target

    # ── contract ────────────────────────────────────────────────────────────

    def test_the_script_exists_and_is_executable(self):
        self.assertTrue(DOCTOR.exists(), 'scripts/probe_doctor.sh is missing')
        self.assertTrue(DOCTOR.stat().st_mode & 0o111, 'probe_doctor.sh is not executable')
        self.assertEqual(subprocess.run(['bash', '-n', str(DOCTOR)],
                                        capture_output=True).returncode, 0)

    def test_a_healthy_clone_exits_zero(self):
        result = self._doctor()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('未发现问题', result.stdout)

    def test_unmerged_files_are_reported_without_changing_anything(self):
        self._half_merged()
        result = self._doctor()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('data/history/station-changes.json', result.stdout)
        # Report-only by default: the conflict is still there afterwards.
        self.assertNotEqual(self._git(self.clone, 'diff', '--name-only',
                                      '--diff-filter=U').stdout.strip(), '')

    def test_fix_resets_the_half_merge_to_the_remote(self):
        target = self._half_merged()
        result = self._doctor('--fix')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

        self.assertEqual(self._git(self.clone, 'ls-files', '-u').stdout.strip(), '')
        self.assertEqual(self._git(self.clone, 'diff', '--name-only',
                                   '--diff-filter=U').stdout.strip(), '')
        # The remote (Actions) version won, and the reset is stated explicitly.
        remote = self._git(self.clone, 'show',
                           'origin/master:data/history/station-changes.json').stdout
        self.assertEqual(target.read_text(encoding='utf-8'), remote)
        self.assertIn('reset --hard', result.stdout)
        # ...and the clone is healthy afterwards.
        self.assertEqual(self._doctor().returncode, 0)

    def test_a_leftover_merge_state_without_conflicts_is_reported(self):
        (self.clone / 'data' / 'history' / 'station-changes.json').write_text(
            '{"v": 1, "s": {}}\n', encoding='utf-8')
        self._git(self.clone, 'add', '-A')
        self._commit(self.clone, 'seed the data store')
        self._git(self.clone, 'checkout', '-q', '-b', 'clean-writer')
        self._git(self.clone, 'commit', '-q', '--allow-empty', '-m', 'other side')
        self._git(self.clone, 'checkout', '-q', 'master')
        self._git(self.clone, 'commit', '-q', '--allow-empty', '-m', 'this side')
        merge = self._git(self.clone, 'merge', '--no-commit', 'clean-writer', check=False)
        self.assertEqual(merge.returncode, 0, merge.stderr)
        self.assertTrue((self.clone / '.git' / 'MERGE_HEAD').exists())

        result = self._doctor()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('MERGE_HEAD', result.stdout)
        self.assertEqual(self._doctor('--fix').returncode, 0)
        self.assertFalse((self.clone / '.git' / 'MERGE_HEAD').exists())

    def test_a_bloated_tracked_data_file_is_reported(self):
        target = self._bloated()
        result = self._doctor()
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('data/history/station-history.json', result.stdout)
        self.assertIn('reset --hard', result.stdout)
        # Report-only: the file is untouched.
        self.assertGreater(target.stat().st_size, 1024)

    def test_fix_restores_the_bloated_file_from_the_remote(self):
        self._bloated()
        result = self._doctor('--fix')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        restored = self._git(self.clone, 'show', 'HEAD:data/history/station-history.json').stdout
        local = (self.clone / 'data' / 'history' / 'station-history.json').read_text(
            encoding='utf-8')
        self.assertEqual(local, restored)

    def test_fix_refuses_to_touch_a_worktree_with_agent_changes(self):
        self._half_merged()
        agent_file = self.clone / 'js' / 'app.js'
        agent_file.write_text('export const version = 2; // agent in progress\n',
                              encoding='utf-8')
        result = self._doctor('--fix')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('refus', result.stdout)
        self.assertIn('agent in progress', agent_file.read_text(encoding='utf-8'))

    def test_dry_run_prints_the_fix_without_running_it(self):
        self._half_merged()
        result = self._doctor('--fix', '--dry-run')
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn('reset --hard', result.stdout)
        self.assertNotEqual(self._git(self.clone, 'diff', '--name-only',
                                      '--diff-filter=U').stdout.strip(), '')

    def test_an_unknown_option_is_a_usage_error(self):
        result = self._doctor('--nope')
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn('用法', result.stderr)

    def test_the_docs_point_at_the_doctor(self):
        docs = (ROOT / 'docs' / 'history-data.md').read_text(encoding='utf-8')
        self.assertIn('scripts/probe_doctor.sh', docs)
        self.assertIn('两个缺口的自愈', docs)


if __name__ == '__main__':
    sys.exit(unittest.main())

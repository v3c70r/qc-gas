#!/usr/bin/env python3
"""Contract tests for the local high-frequency probe (issue #74).

`scripts/local_fetch_loop.sh` is a thin, tmux-resident driver: it must reuse the
committed pipeline scripts (no forked fetch logic), bypass the Actions gate on
purpose, survive failures, and push with a bounded `pull --rebase` retry.  These
tests pin exactly those behaviours plus the documented start/stop commands, so a
later edit cannot silently turn the probe into a second fetch implementation or
make the loop exit on the first error.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'local_fetch_loop.sh'
DOCS = ROOT / 'docs' / 'history-data.md'


class LocalFetchLoopTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding='utf-8') if SCRIPT.exists() else ''

    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.exists(), 'scripts/local_fetch_loop.sh is missing')
        self.assertTrue(SCRIPT.stat().st_mode & 0o111, 'probe script is not executable')

    def test_syntax_is_valid(self):
        result = subprocess.run(['bash', '-n', str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_reuses_the_committed_pipeline_scripts(self):
        for script in ('download_data.py', 'process_data.py', 'update_profile.py',
                       'append_history.py', 'metrics.py', 'fetch_log.py'):
            self.assertIn(script, self.text, f'{script} not invoked by the probe')

    def test_does_not_fork_the_fetch_logic(self):
        self.assertNotIn('regieessencequebec.ca', self.text)
        self.assertNotIn('urllib', self.text)

    def test_bypasses_the_actions_gate_on_purpose(self):
        # The gate may be *mentioned* in comments, but it must never be invoked.
        code = '\n'.join(line for line in self.text.splitlines()
                         if not line.lstrip().startswith('#'))
        self.assertNotIn('should_fetch.py', code)

    def test_loops_with_the_15_minute_cadence(self):
        self.assertIn('while', self.text)
        self.assertIn('sleep', self.text)
        self.assertRegex(self.text, r'PROBE_INTERVAL_SECONDS:-900')

    def test_sleep_only_tops_up_the_remaining_cycle_time(self):
        # A fixed sleep *after* the work would make the observed interval
        # `900s + work`, right at the acceptance bound (<= 16 min).
        self.assertIn('SECONDS', self.text)
        self.assertRegex(self.text, r'INTERVAL_SECONDS - ')
        self.assertIn('sleep "$remaining"', self.text)
        self.assertNotIn('  sleep "$INTERVAL_SECONDS"', self.text)

    def test_compresses_the_same_file_set_as_the_workflow(self):
        self.assertNotIn('for f in data/*.json', self.text)
        for name in ('stations.json', 'stats.json', 'history.json',
                     'regie-margin.json', 'metrics.json'):
            self.assertIn(f'data/{name}', self.text)
        self.assertIn('for f in data/history/*.json', self.text)

    def test_runs_the_size_budget_check_without_failing_the_cycle(self):
        self.assertIn('check_data_size.py', self.text)
        self.assertIn('warn_stage', self.text)

    def test_never_exits_on_a_failed_step(self):
        self.assertNotRegex(self.text, r'^\s*set -e', re.M)
        self.assertIn('set -uo pipefail', self.text)

    def test_pushes_with_bounded_rebase_retries(self):
        self.assertRegex(self.text, r'PROBE_MAX_PUSH_RETRIES:-3')
        self.assertIn('pull --rebase', self.text)
        self.assertIn('git push', self.text)

    def test_commit_message_marks_the_local_probe(self):
        self.assertIn('Update gas prices data (local probe) [skip ci]', self.text)

    def test_daily_summary_is_commented_on_the_tracking_issue(self):
        self.assertIn('gh issue comment', self.text)
        self.assertIn('needs-summary', self.text)
        self.assertIn('mark-summary', self.text)
        self.assertRegex(self.text, r'PROBE_TRACKING_ISSUE:-78')

    def test_logs_to_the_gitignored_agent_directory(self):
        self.assertRegex(self.text, r'\.agent/probe\.log')

    def test_ledger_file_is_documented_where_ignore_rules_live(self):
        result = subprocess.run(['git', 'check-ignore', '-q', '.agent/probe.log'],
                                cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0, '.agent/*.log must stay gitignored')

    # ── issue #92: samples outside the repository ───────────────────────────

    def test_commits_only_the_small_ledger_files(self):
        self.assertNotIn('git add data/', self.text)
        for name in ('data/update-profile.json', 'data/fetch-log.json',
                     'data/fetch-schedule.json'):
            self.assertIn(name, self.text)

    def test_throttles_data_commits_to_one_per_hour(self):
        self.assertRegex(self.text, r'PROBE_COMMIT_INTERVAL_SECONDS:-3600')
        self.assertIn('commit throttle', self.text)

    def test_pending_commits_are_pushed_even_when_the_throttle_holds(self):
        self.assertIn('has_unpushed_commits', self.text)

    def test_appends_the_local_event_log(self):
        self.assertIn('scripts/probe_events.py', self.text)
        self.assertIn('probe-local', self.text)

    def test_only_probe_owned_dirty_paths_may_be_reset_hard(self):
        self.assertIn('probe_owns_worktree', self.text)
        self.assertIn('refusing reset --hard', self.text)

    def test_requires_the_base_branch(self):
        self.assertRegex(self.text, r'PROBE_BASE_BRANCH:-master')
        self.assertIn('require_base_branch', self.text)

    def test_local_event_log_is_gitignored(self):
        result = subprocess.run(
            ['git', 'check-ignore', '-q', 'data/probe-local/events.jsonl'],
            cwd=ROOT, capture_output=True)
        self.assertEqual(result.returncode, 0,
                         'data/probe-local/ must stay gitignored (issue #92)')


class ProbeTwoCyclePushTest(unittest.TestCase):
    """Issue #92 addendum: the probe must keep pushing master while an agent
    branch exists, and it must only ever commit the small ledger files.

    Hermetic: a throwaway bare remote + clone, stub pipeline scripts (the probe
    only drives `scripts/*.py`), two cycles, no network.
    """

    STUB = '''\
import json, os, sys
from datetime import datetime, timezone

name = os.path.basename(sys.argv[0]).replace('.py', '')
stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
os.makedirs('data/history', exist_ok=True)

def dump(path, payload):
    with open(path, 'w') as handle:
        json.dump(payload, handle)

def stations_changed_at(store, timestamp):
    return sorted(key for key, rec in (store.get('s') or {}).items()
                  if (rec or {}).get('t') == str(timestamp))

if name == 'update_profile':
    dump('data/update-profile.json', {'v': 1, 'last_observed_at': stamp})
    dump('data/history/station-changes.json', {
        'v': 1, 'last': {'at': stamp, 'observed': 2, 'changed': 1},
        's': {'1.00000,1.00000': {'t': stamp, 'u': stamp, 'p': [150.0, None, None]},
              '2.00000,2.00000': {'t': None, 'u': stamp, 'p': [150.0, None, None]}}})
elif name == 'append_history':
    dump('data/history/station-changes.json', {
        'v': 1, 'last': {'at': stamp, 'observed': 2, 'changed': 1},
        's': {'1.00000,1.00000': {'t': stamp, 'u': stamp, 'p': [150.0, None, None]}}})
elif name == 'download_data':
    dump('data/stations.json', {'features': []})
elif name == 'process_data':
    dump('data/stations.json', {'features': []})
elif name == 'metrics':
    dump('data/metrics.json', {'v': 1})
elif name == 'fetch_log':
    if 'append' in sys.argv:
        dump('data/fetch-log.json', {'v': 1, 'fetches': [stamp]})
'''

    def _git(self, *args, cwd):
        return subprocess.run(['git', *args], cwd=str(cwd), capture_output=True,
                              text=True, check=True)

    def _commit(self, cwd, message):
        self._git('-c', 'user.email=t@example.com', '-c', 'user.name=t',
                  'commit', '-q', '--allow-empty', '-m', message, cwd=cwd)

    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp = Path(self._tmp.name)
        self.remote = tmp / 'origin.git'
        self.clone = tmp / 'probe'

        self._git('init', '-q', '--bare', '-b', 'master', str(self.remote), cwd=tmp)
        seed = tmp / 'seed'
        self._git('init', '-q', '-b', 'master', str(seed), cwd=tmp)
        self._commit(seed, 'seed')
        self._git('remote', 'add', 'origin', str(self.remote), cwd=seed)
        self._git('push', '-q', '-u', 'origin', 'master', cwd=seed)
        self._git('clone', '-q', str(self.remote), str(self.clone), cwd=tmp)
        self._git('config', 'user.email', 'probe@example.com', cwd=self.clone)
        self._git('config', 'user.name', 'probe', cwd=self.clone)
        # An agent implementing an issue: `agent/92` exists locally, master stays
        # the probe's branch.
        self._git('branch', 'agent/92', cwd=self.clone)

        scripts = self.clone / 'scripts'
        scripts.mkdir(parents=True)
        shutil.copy(SCRIPT, scripts / 'local_fetch_loop.sh')
        shutil.copy(ROOT / 'scripts' / 'probe_events.py', scripts / 'probe_events.py')
        for name in ('download_data', 'process_data', 'update_profile',
                     'append_history', 'metrics', 'fetch_log', 'check_data_size'):
            (scripts / f'{name}.py').write_text(self.STUB, encoding='utf-8')

    def _run_probe(self):
        import os
        env = dict(os.environ, PROBE_BASE_BRANCH='master', PROBE_MAX_CYCLES='2',
                   PROBE_INTERVAL_SECONDS='1', PROBE_COMMIT_INTERVAL_SECONDS='0',
                   PROBE_SKIP_SUMMARY='1', PROBE_LOG_FILE=str(self.clone / '.agent' / 'probe.log'))
        return subprocess.run(['bash', 'scripts/local_fetch_loop.sh'], cwd=self.clone,
                              env=env, capture_output=True, text=True, timeout=180)

    def test_two_cycles_push_master_twice_and_never_the_snapshots(self):
        result = self._run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._git('log', '--oneline', 'master', cwd=self.remote).stdout.splitlines()
        probe_commits = [line for line in log if 'local probe' in line]
        self.assertGreaterEqual(len(probe_commits), 2, log)

        # The remote only ever saw master: nothing landed on a feature branch.
        branches = self._git('branch', '--list', cwd=self.remote).stdout
        self.assertIn('master', branches)
        for name in branches.replace('*', ' ').split():
            self.assertEqual(name, 'master')

        # Only the small ledger files were ever committed; samples stayed local.
        pushed = self._git('ls-tree', '-r', '--name-only', 'master',
                           cwd=self.remote).stdout.split()
        self.assertIn('data/update-profile.json', pushed)
        self.assertIn('data/fetch-log.json', pushed)
        for snapshot in ('data/stations.json', 'data/metrics.json',
                         'data/history/station-changes.json'):
            self.assertNotIn(snapshot, pushed)

        # The event log grew locally, one line per cycle, and is untracked.
        events = (self.clone / 'data' / 'probe-local' / 'events.jsonl')
        lines = [line for line in events.read_text(encoding='utf-8').splitlines()
                 if line.strip()]
        self.assertEqual(len(lines), 2)
        self.assertNotIn('data/probe-local', pushed)

    def test_snapshots_stay_out_of_the_working_tree_diff(self):
        result = self._run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)
        committed = self._git('show', '--name-only', '--format=',
                              'master', cwd=self.clone).stdout.split()
        for name in committed:
            self.assertIn(name, ('data/update-profile.json', 'data/fetch-log.json',
                                 'data/fetch-schedule.json'))

    def test_eight_cycles_throttle_to_one_commit_and_log_every_cycle(self):
        """Acceptance #1 (issue #92): <= 1 data commit/hour while the local log grows."""
        import os
        env = dict(os.environ, PROBE_BASE_BRANCH='master', PROBE_MAX_CYCLES='8',
                   PROBE_INTERVAL_SECONDS='1', PROBE_COMMIT_INTERVAL_SECONDS='3600',
                   PROBE_SKIP_SUMMARY='1')
        result = subprocess.run(['bash', 'scripts/local_fetch_loop.sh'],
                                cwd=self.clone, env=env, capture_output=True,
                                text=True, timeout=300)
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._git('log', '--oneline', 'master', cwd=self.remote).stdout.splitlines()
        probe_commits = [line for line in log if 'local probe' in line]
        self.assertEqual(len(probe_commits), 1, log)   # 8 cycles, 1 commit

        events = self.clone / 'data' / 'probe-local' / 'events.jsonl'
        lines = [line for line in events.read_text(encoding='utf-8').splitlines()
                 if line.strip()]
        self.assertEqual(len(lines), 8)
        self.assertLess(events.stat().st_size, 200 * 1024)
        total_changed = sum(json.loads(line)['changed'] for line in lines)
        self.assertEqual(total_changed, 8)  # one re-price per cycle

        pushed = self._git('ls-tree', '-r', '--name-only', 'master',
                           cwd=self.remote).stdout.split()
        for snapshot in ('data/stations.json', 'data/metrics.json',
                         'data/history/station-changes.json',
                         'data/probe-local/events.jsonl'):
            self.assertNotIn(snapshot, pushed)


class ProbeBranchGuardTest(unittest.TestCase):
    """Issue #92 addendum: never let the probe commit onto a feature branch."""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                                cwd=ROOT, capture_output=True, text=True)
        cls.branch = result.stdout.strip() or 'HEAD'

    def _run(self, **env):
        import os
        merged = dict(os.environ, **env)
        return subprocess.run(['bash', str(SCRIPT)], cwd=ROOT, capture_output=True,
                              text=True, env=merged, timeout=120)

    def test_refuses_to_run_on_a_branch_that_is_not_the_base(self):
        result = self._run(PROBE_BASE_BRANCH=f'{self.branch}-not-the-base',
                           PROBE_MAX_CYCLES='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PROBE_BASE_BRANCH', result.stderr)
        self.assertIn('refusing', result.stderr)

    def test_startup_checks_pass_on_the_base_branch(self):
        result = self._run(PROBE_BASE_BRANCH=self.branch, PROBE_CHECK_ONLY='1')
        self.assertEqual(result.returncode, 0, result.stderr)


class ProbeDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOCS.read_text(encoding='utf-8')

    def test_documents_the_local_probe_section(self):
        self.assertIn('本机高频探针', self.text)

    def test_documents_what_the_probe_skips(self):
        self.assertIn('fetch_bulletin.py', self.text)
        self.assertIn('check_data_size.py', self.text)

    def test_documents_the_fixed_tmux_session_name_and_commands(self):
        self.assertIn('tmux new -d -s probe', self.text)
        self.assertIn('tmux kill-session -t probe', self.text)

    def test_explains_relation_to_the_actions_pipeline(self):
        lowered = self.text
        self.assertIn('should_fetch.py', lowered)
        self.assertIn('probe', lowered.lower())

    def test_documents_the_dedicated_worktree_requirement(self):
        self.assertIn('git worktree add', self.text)
        self.assertIn('PROBE_BASE_BRANCH', self.text)

    def test_documents_the_local_event_log_and_the_commit_throttle(self):
        self.assertIn('data/probe-local/events.jsonl', self.text)
        self.assertIn('PROBE_COMMIT_INTERVAL_SECONDS', self.text)


if __name__ == '__main__':
    unittest.main()

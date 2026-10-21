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
import shlex
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

    # ── issue #113: stop double-writing the Actions-owned data files ────────

    def test_history_stage_is_skipped_by_default(self):
        # Actions appends `data/history/**` every hour; a second writer is what
        # made 1 cycle in 4 fail on `pull --rebase` conflicts.
        self.assertRegex(self.text, r'PROBE_SKIP_STAGES-history')
        self.assertIn('stage_skipped', self.text)
        # Every stage is routed through the skip check, so `history` is not a
        # special case: an empty PROBE_SKIP_STAGES runs the whole chain again.
        self.assertIn('run_stage history "$PYTHON" scripts/append_history.py', self.text)
        self.assertIn('PROBE_SKIP_HISTORY', self.text)

    def test_conflicts_resolve_in_favour_of_the_remote_instead_of_aborting(self):
        self.assertIn('resolve_conflict_path', self.text)
        self.assertIn('git rebase --continue', self.text)
        # During a rebase HEAD is the upstream side, so `--ours` *is* the remote.
        self.assertIn('--ours', self.text)
        self.assertIn('took the remote', self.text)

    def test_sync_retries_within_the_cycle_before_giving_up(self):
        self.assertRegex(self.text, r'PROBE_SYNC_ATTEMPTS:-2')
        self.assertIn('discarded local', self.text)

    # ── issue #121: self-heal on script change + a clean sync ───────────────

    def test_detects_its_own_update_and_re_execs_itself(self):
        """Gap 1: a long-lived bash process keeps running the old script.

        #113 was merged on 2026-10-19 and never ran (`probe stages skipped`
        appeared 0 times in `.agent/probe.log` until a manual restart), so the
        fix has to reload the loop on its own within one cycle.
        """
        self.assertIn('script_fingerprint', self.text)
        self.assertIn('guard_script_change', self.text)
        self.assertIn('restarting the probe automatically', self.text)
        # `exec`, not a child process: the tmux session must keep its PID.
        self.assertRegex(self.text, r'exec\s+"\$BASH_PATH"')
        self.assertRegex(self.text, r'PROBE_MAX_SELF_RESTARTS:-3')
        self.assertIn('self-restart disabled', self.text)

    def test_the_new_version_is_verified_before_the_restart(self):
        # A broken merge must never take a working loop down: syntax check first,
        # then the script's own startup checks (`PROBE_CHECK_ONLY`).
        self.assertIn('verify_new_version', self.text)
        self.assertIn('bash -n', self.text)
        self.assertIn('PROBE_CHECK_ONLY=1', self.text)
        self.assertIn('ALARM', self.text)

    def test_every_sync_ends_with_an_empty_unmerged_index(self):
        """Gap 2: 7 `UU` files survived `sync_with_remote()` on 2026-10-19.

        The following cycles then died on `Pulling is not possible because you
        have unmerged files` (40 log lines), so the last step of a sync must
        assert an empty unmerged index and reset to the remote when it is not.
        """
        self.assertIn('assert_no_unmerged', self.text)
        self.assertIn('--diff-filter=U', self.text)
        self.assertRegex(self.text, r'discarded local \$?\{?\w*\}? ?file')
        self.assertIn('clear_half_merge_state', self.text)

    def test_helpers_can_be_sourced_without_running_the_loop(self):
        # Unit-test hook (tests only, like PROBE_CHECK_ONLY): `PROBE_LIB_ONLY`
        # defines the helpers and returns instead of starting the probe.
        self.assertIn('PROBE_LIB_ONLY', self.text)


class ProbeRepoTestCase(unittest.TestCase):
    """Hermetic probe harness: a throwaway bare remote + clone + stubs."""

    """Issue #92 addendum: the probe must keep pushing master while an agent
    branch exists, and it must only ever commit the small ledger files.

    Hermetic: a throwaway bare remote + clone, stub pipeline scripts (the probe
    only drives `scripts/*.py`), two cycles, no network.
    """

    STUB = '''\
import json, os, subprocess, sys
from datetime import datetime, timezone

name = os.path.basename(sys.argv[0]).replace('.py', '')
stamp = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
os.makedirs('data/history', exist_ok=True)

# Test-only hook (issue #113): let a spec inject a concurrent Actions commit in
# the middle of a probe cycle, exactly once per clone.
hook = os.environ.get('PROBE_TEST_REMOTE_COMMIT')
if hook and not os.path.exists('.probe-test-injected'):
    with open('.probe-test-injected', 'w') as marker:
        marker.write(hook)
    subprocess.run(hook, shell=True, check=False)

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
        # A tracked, non-`data/` file so the sibling-worktree test can prove the
        # probe's `reset --hard` never touches somebody else's edit.
        (seed / 'js').mkdir()
        (seed / 'js' / 'app.js').write_text('export const version = 1;\n', encoding='utf-8')
        self._git('add', '-A', cwd=seed)
        self._commit(seed, 'seed')
        self._git('remote', 'add', 'origin', str(self.remote), cwd=seed)
        self._git('push', '-q', '-u', 'origin', 'master', cwd=seed)
        self._git('clone', '-q', str(self.remote), str(self.clone), cwd=tmp)
        self._git('config', 'user.email', 'probe@example.com', cwd=self.clone)
        self._git('config', 'user.name', 'probe', cwd=self.clone)
        # An agent implementing an issue works on `agent/*` in its own worktree
        # (created by the sibling-worktree test); master stays the probe's branch.

        scripts = self.clone / 'scripts'
        scripts.mkdir(parents=True)
        shutil.copy(SCRIPT, scripts / 'local_fetch_loop.sh')
        shutil.copy(ROOT / 'scripts' / 'probe_events.py', scripts / 'probe_events.py')
        for name in ('download_data', 'process_data', 'update_profile',
                     'append_history', 'metrics', 'fetch_log', 'check_data_size'):
            (scripts / f'{name}.py').write_text(self.STUB, encoding='utf-8')

    def _run_probe(self, cycles='2', **extra):
        import os
        env = dict(os.environ, PROBE_BASE_BRANCH='master', PROBE_MAX_CYCLES=str(cycles),
                   PROBE_INTERVAL_SECONDS='1', PROBE_COMMIT_INTERVAL_SECONDS='0',
                   PROBE_SKIP_SUMMARY='1', PROBE_LOG_FILE=str(self.clone / '.agent' / 'probe.log'))
        env.update({key: str(value) for key, value in extra.items()})
        return subprocess.run(['bash', 'scripts/local_fetch_loop.sh'], cwd=self.clone,
                              env=env, capture_output=True, text=True, timeout=180)

    def _probe_log(self):
        return (self.clone / '.agent' / 'probe.log').read_text(encoding='utf-8')


class ProbeTwoCyclePushTest(ProbeRepoTestCase):
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

    def test_a_sibling_agent_worktree_survives_two_probe_cycles(self):
        """Issue #92 addendum #2/#4: the probe and an agent never share a tree.

        The agent works on `agent/92` in its **own** worktree (shared object
        store), with an uncommitted edit outside `data/`; the probe keeps its own
        worktree on `master`. Two probe cycles must both push master without
        touching the agent's edit, and without the probe ever checking out the
        agent branch (`reset --hard '@{u}'` protection).
        """
        tmp = Path(self._tmp.name)
        agent_wt = tmp / 'agent-wt'
        self._git('worktree', 'add', '-q', '-b', 'agent/92', str(agent_wt),
                  'master', cwd=self.clone)
        agent_file = agent_wt / 'js' / 'app.js'
        agent_file.write_text('export const version = 2; // agent in progress\n',
                              encoding='utf-8')
        (agent_wt / 'js' / 'new-module.js').write_text('export const x = 1;\n',
                                                       encoding='utf-8')

        result = self._run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)

        # Two cycles, two pushes, all on master.
        log = self._git('log', '--oneline', 'master', cwd=self.remote).stdout.splitlines()
        self.assertGreaterEqual(len([line for line in log if 'local probe' in line]), 2)
        self.assertEqual(self._git('symbolic-ref', '--short', 'HEAD',
                                   cwd=self.clone).stdout.strip(), 'master')

        # The agent's worktree is untouched: same branch, same uncommitted edit.
        self.assertEqual(self._git('symbolic-ref', '--short', 'HEAD',
                                   cwd=agent_wt).stdout.strip(), 'agent/92')
        self.assertIn('agent in progress', agent_file.read_text(encoding='utf-8'))
        self.assertTrue((agent_wt / 'js' / 'new-module.js').exists())
        dirty = self._git('status', '--porcelain', cwd=agent_wt).stdout
        self.assertIn('js/app.js', dirty)
        self.assertIn('js/new-module.js', dirty)
        # And still nothing of the agent's edit reached master.
        pushed = self._git('show', 'master:js/app.js', cwd=self.remote).stdout
        self.assertIn('version = 1', pushed)

        # The probe refuses to run *inside* that worktree (wrong branch): the
        # guard reads the branch of the tree the script lives in.
        import os
        (agent_wt / 'scripts').mkdir(exist_ok=True)
        shutil.copy(SCRIPT, agent_wt / 'scripts' / 'local_fetch_loop.sh')
        refused = subprocess.run(
            ['bash', 'scripts/local_fetch_loop.sh'], cwd=agent_wt,
            env=dict(os.environ, PROBE_BASE_BRANCH='master', PROBE_MAX_CYCLES='1'),
            capture_output=True, text=True, timeout=120)
        self.assertEqual(refused.returncode, 2, refused.stderr)
        self.assertIn('refusing', refused.stderr)
        # The refusal changed nothing: the agent's edit is still there.
        self.assertIn('agent in progress', agent_file.read_text(encoding='utf-8'))

    def test_a_leftover_staged_snapshot_never_rides_a_ledger_commit(self):
        """An interrupted old `git add data/` leaves the index dirty.

        Observed on master (690235f6): the ledger commit carried
        `data/history/station-changes.json`, `data/metrics.json`, `data/stats.json`
        and their `.br` because the killed pre-#92 loop had staged them. The
        probe must commit **only** its three ledger paths whatever the index
        holds, and leave the snapshots on disk (local analysis still needs them).
        """
        (self.clone / 'data' / 'history').mkdir(parents=True, exist_ok=True)
        (self.clone / 'data' / 'stations.json').write_text('{"features": []}\n',
                                                          encoding='utf-8')
        (self.clone / 'data' / 'history' / 'station-changes.json').write_text(
            '{"v": 1}\n', encoding='utf-8')
        (self.clone / 'data' / 'metrics.json').write_text('{"v": 0}\n', encoding='utf-8')
        self._git('add', 'data/stations.json', 'data/history/station-changes.json',
                  'data/metrics.json', cwd=self.clone)
        self.assertIn('data/stations.json',
                      self._git('diff', '--cached', '--name-only',
                                cwd=self.clone).stdout)

        result = self._run_probe()
        self.assertEqual(result.returncode, 0, result.stderr)

        pushed = self._git('ls-tree', '-r', '--name-only', 'master',
                           cwd=self.remote).stdout.split()
        for snapshot in ('data/stations.json', 'data/history/station-changes.json',
                         'data/metrics.json'):
            self.assertNotIn(snapshot, pushed)
        # The ledger still got through...
        self.assertIn('data/update-profile.json', pushed)
        # ...the snapshots stay on disk, and nothing snapshot-shaped stays staged.
        self.assertTrue((self.clone / 'data' / 'stations.json').exists())
        staged = self._git('diff', '--cached', '--name-only', cwd=self.clone).stdout
        self.assertNotIn('data/stations.json', staged)
        self.assertNotIn('data/metrics.json', staged)

    def test_a_detached_checkout_is_refused(self):
        """`git worktree add --detach` / CI checkouts: refuse, do not guess."""
        self._git('checkout', '-q', '--detach', 'master', cwd=self.clone)
        result = self._run_probe()
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('detached HEAD', result.stderr)
        # Nothing was committed or pushed from that tree.
        log = self._git('log', '--oneline', 'master', cwd=self.remote).stdout
        self.assertNotIn('local probe', log)

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


class ProbeConflictResolutionTest(ProbeRepoTestCase):
    """Issue #113: the probe and Actions write the same `data/` files.

    Acceptance #1/#5: a `pull --rebase` conflict must resolve in favour of the
    remote (Actions owns `data/`) and the cycle must still commit its ledger —
    no unmerged index, no failed cycle.
    """

    def _actions_clone(self):
        tmp = Path(self._tmp.name)
        actions = tmp / 'actions'
        self._git('clone', '-q', str(self.remote), str(actions), cwd=tmp)
        self._git('config', 'user.email', 'actions@example.com', cwd=actions)
        self._git('config', 'user.name', 'actions', cwd=actions)
        return actions

    def test_a_remote_commit_on_the_same_file_keeps_the_cycle_green(self):
        actions = self._actions_clone()
        hook = Path(self._tmp.name) / 'inject-actions.sh'
        hook.write_text(
            'set -e\n'
            f'cd {shlex.quote(str(actions))}\n'
            'git pull -q --rebase\n'
            'mkdir -p data\n'
            "cat > data/update-profile.json <<'JSON'\n"
            '{"v": 1, "last_observed_at": "actions"}\n'
            'JSON\n'
            'git add -A\n'
            'git commit -q -m "Update gas prices data [skip ci]"\n'
            'git push -q origin master\n',
            encoding='utf-8')

        result = self._run_probe(cycles='1', PROBE_TEST_REMOTE_COMMIT=f'bash {shlex.quote(str(hook))}')
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._probe_log()
        self.assertIn('conflict data/update-profile.json', log)
        self.assertIn('took the remote', log)
        self.assertNotIn('finished with errors', log)

        # The rebase completed and left no unmerged path behind.
        self.assertEqual(
            self._git('diff', '--name-only', '--diff-filter=U', cwd=self.clone).stdout.strip(),
            '')
        self.assertEqual(self._git('ls-files', '-u', cwd=self.clone).stdout.strip(), '')

        # The remote version won for the Actions-owned file...
        remote_profile = self._git('show', 'master:data/update-profile.json',
                                   cwd=self.remote).stdout
        self.assertEqual(json.loads(remote_profile)['last_observed_at'], 'actions')
        # ...and the probe's ledger still got through in the same cycle.
        remote_log = json.loads(self._git('show', 'master:data/fetch-log.json',
                                          cwd=self.remote).stdout)
        self.assertEqual(len(remote_log['fetches']), 1, remote_log)
        pushed = self._git('ls-tree', '-r', '--name-only', 'master',
                           cwd=self.remote).stdout.split()
        self.assertIn('data/fetch-log.json', pushed)
        self.assertNotIn('data/history/station-changes.json', pushed)

    def test_a_dirty_tracked_data_file_does_not_leave_unmerged_files(self):
        """The reported symptom (issue #113): `Pulling is not possible ... unmerged files`.

        The probe rewrites tracked `data/history/station-changes.json` every
        cycle and never commits it (#92/#103), so an Actions commit to the same
        file meets a dirty local copy: `pull --rebase --autostash` either keeps
        the stash aside (recent git) or leaves an unmerged index behind (the
        observed failure). Either way the probe must keep going, leave no
        unmerged path, keep the remote version of the Actions-owned file and
        still push both cycles' ledger.
        """
        # The seed has no `data/`; give the remote one tracked persistent file.
        seed = Path(self._tmp.name) / 'seed'
        (seed / 'data' / 'history').mkdir(parents=True)
        (seed / 'data' / 'history' / 'station-changes.json').write_text(
            '{"v": 1, "last": {"at": "seed"}, "s": {}}\n', encoding='utf-8')
        self._git('add', '-A', cwd=seed)
        self._commit(seed, 'seed persistent data')
        self._git('push', '-q', 'origin', 'master', cwd=seed)
        self._git('pull', '-q', '--rebase', 'origin', 'master', cwd=self.clone)

        actions = self._actions_clone()
        hook = Path(self._tmp.name) / 'inject-actions-dirty.sh'
        hook.write_text(
            'set -e\n'
            f'cd {shlex.quote(str(actions))}\n'
            'git pull -q --rebase\n'
            "cat > data/history/station-changes.json <<'JSON'\n"
            '{"v": 1, "last": {"at": "actions"}, "s": {}}\n'
            'JSON\n'
            'git add -A\n'
            'git commit -q -m "Update gas prices data [skip ci]"\n'
            'git push -q origin master\n',
            encoding='utf-8')

        result = self._run_probe(cycles='2', PROBE_TEST_REMOTE_COMMIT=f'bash {shlex.quote(str(hook))}')
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._probe_log()
        self.assertNotIn('finished with errors', log)
        self.assertNotIn('Pulling is not possible', log)

        # No unmerged path survives the cycle.
        self.assertEqual(
            self._git('diff', '--name-only', '--diff-filter=U', cwd=self.clone).stdout.strip(), '')
        self.assertEqual(self._git('ls-files', '-u', cwd=self.clone).stdout.strip(), '')

        # The Actions version of the persistent store won...
        remote_store = json.loads(self._git('show', 'master:data/history/station-changes.json',
                                            cwd=self.remote).stdout)
        self.assertEqual(remote_store['last']['at'], 'actions')
        # ...and both cycles pushed their ledger commit to master.
        commits = self._git('log', '--oneline', 'master', cwd=self.remote).stdout.splitlines()
        self.assertEqual(len([line for line in commits if 'local probe' in line]), 2, commits)
        self.assertIn('data/fetch-log.json',
                      self._git('ls-tree', '-r', '--name-only', 'master',
                                cwd=self.remote).stdout.split())

    def test_the_history_stage_is_skipped_by_default(self):
        result = self._run_probe(cycles='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self._probe_log()
        self.assertIn('stage history skipped', log)
        self.assertNotIn('stage history ok', log)
        # Only `update_profile.py` wrote the store, not `append_history.py`.
        store = json.loads((self.clone / 'data' / 'history' / 'station-changes.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(len(store['s']), 2, store)

    def test_an_empty_skip_list_runs_every_stage_again(self):
        result = self._run_probe(cycles='1', PROBE_SKIP_STAGES='')
        self.assertEqual(result.returncode, 0, result.stderr)
        log = self._probe_log()
        self.assertIn('stage history ok', log)
        self.assertNotIn('stage history skipped', log)
        store = json.loads((self.clone / 'data' / 'history' / 'station-changes.json')
                           .read_text(encoding='utf-8'))
        self.assertEqual(len(store['s']), 1, store)

    def test_probe_skip_history_shorthand_skips_the_stage(self):
        result = self._run_probe(cycles='1', PROBE_SKIP_HISTORY='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('stage history skipped', self._probe_log())


class ProbeBranchGuardTest(unittest.TestCase):
    """Issue #92 addendum: never let the probe commit onto a feature branch."""

    @classmethod
    def setUpClass(cls):
        result = subprocess.run(['git', 'rev-parse', '--abbrev-ref', 'HEAD'],
                                cwd=ROOT, capture_output=True, text=True)
        branch = result.stdout.strip()
        # `--abbrev-ref HEAD` prints the literal "HEAD" on a detached checkout
        # (git worktree review, some CI checkouts). The guard refuses that tree
        # *by design*, so the "startup checks pass" assertion has to be skipped
        # there instead of expecting the script to accept "HEAD" as a branch.
        cls.detached = not branch or branch == 'HEAD'
        cls.branch = None if cls.detached else branch

    def _run(self, **env):
        import os
        merged = dict(os.environ, **env)
        return subprocess.run(['bash', str(SCRIPT)], cwd=ROOT, capture_output=True,
                              text=True, env=merged, timeout=120)

    def test_refuses_to_run_on_a_branch_that_is_not_the_base(self):
        result = self._run(PROBE_BASE_BRANCH=f'{self.branch or "master"}-not-the-base',
                           PROBE_MAX_CYCLES='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('PROBE_BASE_BRANCH', result.stderr)
        self.assertIn('refusing', result.stderr)

    def test_refuses_to_run_on_a_detached_head(self):
        if not self.detached:
            self.skipTest('this checkout is on a branch; covered by a hermetic test')
        result = self._run(PROBE_BASE_BRANCH='master', PROBE_CHECK_ONLY='1')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('detached HEAD', result.stderr)

    def test_startup_checks_pass_on_the_base_branch(self):
        if self.detached:
            self.skipTest('detached HEAD: the probe refuses such a tree by design')
        result = self._run(PROBE_BASE_BRANCH=self.branch, PROBE_CHECK_ONLY='1')
        self.assertEqual(result.returncode, 0, result.stderr)


class ProbeSelfHealingTest(ProbeRepoTestCase):
    """Issue #121 gap 1: a merged fix to this very script never ran.

    The probe is a long-lived bash process, so `git pull` rewriting
    `scripts/local_fetch_loop.sh` left it executing the version it started with.
    The loop must notice at the start of a cycle, verify the new version can
    start, and `exec` itself — no human, <= 1 cycle.
    """

    MARKER = 'probe v2 script loaded'

    def _v2_text(self, marker=None):
        text = SCRIPT.read_text(encoding='utf-8')
        anchor = 'log "probe stages skipped:'
        self.assertIn(anchor, text)
        return text.replace(anchor, f'log "{marker or self.MARKER}"\n{anchor}', 1)

    def _swap_hook(self, new_text):
        """Replace the clone's probe the way `git checkout` does (rename).

        An in-place truncate would invalidate the read offset of the *running*
        bash process; git writes a lockfile and renames, so the test does too.
        """
        target = self.clone / 'scripts' / 'local_fetch_loop.sh'
        source = Path(self._tmp.name) / 'v2-local_fetch_loop.sh'
        source.write_text(new_text, encoding='utf-8')
        hook = Path(self._tmp.name) / 'swap-script.sh'
        hook.write_text(
            'set -e\n'
            f'cp {shlex.quote(str(source))} {shlex.quote(str(target))}.new\n'
            f'chmod +x {shlex.quote(str(target))}.new\n'
            f'mv {shlex.quote(str(target))}.new {shlex.quote(str(target))}\n',
            encoding='utf-8')
        return f'bash {shlex.quote(str(hook))}'

    def test_a_merged_script_change_is_applied_without_a_manual_restart(self):
        hook = self._swap_hook(self._v2_text())
        result = self._run_probe(cycles='2', PROBE_TEST_REMOTE_COMMIT=hook)
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._probe_log()
        self.assertIn('restarting the probe automatically', log)
        self.assertIn(self.MARKER, log)
        # The new version really is the one running: both processes logged a
        # first cycle, and the restarted one then ran its full quota.
        self.assertEqual(log.count('probe cycle 1 start'), 2, log)
        self.assertIn('probe cycle 2 done', log)

    def test_an_update_that_exits_at_startup_keeps_the_running_version(self):
        # Valid syntax, but the startup itself fails (`exit 3`): the pre-flight
        # `PROBE_CHECK_ONLY` run catches it, so the old loop keeps working.
        hook = self._swap_hook('#!/usr/bin/env bash\nexit 3\n')
        result = self._run_probe(cycles='2', PROBE_TEST_REMOTE_COMMIT=hook)
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._probe_log()
        self.assertIn('ALARM', log)
        self.assertNotIn('restarting the probe automatically', log)
        self.assertIn('probe cycle 2 done', log)

    def test_an_update_with_a_syntax_error_keeps_the_running_version(self):
        hook = self._swap_hook('#!/usr/bin/env bash\nif then\n')
        result = self._run_probe(cycles='2', PROBE_TEST_REMOTE_COMMIT=hook)
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._probe_log()
        self.assertIn('ALARM', log)
        self.assertNotIn('restarting the probe automatically', log)
        self.assertIn('probe cycle 2 done', log)

    def test_self_restart_gives_up_after_the_configured_number_of_restarts(self):
        import hashlib
        import time

        target = self.clone / 'scripts' / 'local_fetch_loop.sh'
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        state = self.clone / 'data' / 'probe-local' / 'self-restart.state'
        state.parent.mkdir(parents=True, exist_ok=True)
        # Three automatic restarts already happened in this lineage.
        state.write_text(f'3 {int(time.time())} {digest}\n', encoding='utf-8')

        hook = self._swap_hook(self._v2_text())
        result = self._run_probe(cycles='2', PROBE_TEST_REMOTE_COMMIT=hook,
                                 PROBE_MAX_SELF_RESTARTS='3')
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._probe_log()
        self.assertIn('self-restart disabled', log)
        self.assertIn('ALARM', log)
        self.assertNotIn(self.MARKER, log)
        self.assertIn('probe cycle 2 done', log)


class ProbeUnmergedRepairTest(ProbeRepoTestCase):
    """Issue #121 gap 2: a half-merged probe clone kept poisoning every cycle."""

    def _tracked_data_file(self):
        seed = Path(self._tmp.name) / 'seed'
        path = seed / 'data' / 'history' / 'station-changes.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"v": 1, "last": {"at": "seed"}, "s": {}}\n',
                        encoding='utf-8')
        self._git('add', '-A', cwd=seed)
        self._commit(seed, 'seed data store')
        self._git('push', '-q', 'origin', 'master', cwd=seed)
        self._git('pull', '-q', '--rebase', 'origin', 'master', cwd=self.clone)

    def _half_merged_clone(self):
        """Leave the clone exactly like the reported one: `UU` data files."""
        self._tracked_data_file()
        target = self.clone / 'data' / 'history' / 'station-changes.json'
        self._git('checkout', '-q', '-b', 'conflicting-writer', cwd=self.clone)
        target.write_text('{"v": 1, "last": {"at": "local"}, "s": {}}\n',
                         encoding='utf-8')
        self._git('commit', '-q', '-am', 'local data write', cwd=self.clone)
        self._git('checkout', '-q', 'master', cwd=self.clone)
        target.write_text('{"v": 1, "last": {"at": "probe"}, "s": {}}\n',
                         encoding='utf-8')
        self._git('commit', '-q', '-am', 'probe data write', cwd=self.clone)
        merge = subprocess.run(['git', 'merge', 'conflicting-writer'], cwd=str(self.clone),
                               capture_output=True, text=True)
        self.assertNotEqual(merge.returncode, 0, 'expected a merge conflict')
        self.assertNotEqual(
            self._git('diff', '--name-only', '--diff-filter=U',
                      cwd=self.clone).stdout.strip(), '')
        return target

    def test_the_final_assertion_resets_to_the_remote_and_counts_the_loss(self):
        import os
        self._half_merged_clone()
        log_file = self.clone / '.agent' / 'probe.log'
        env = dict(os.environ, PROBE_LIB_ONLY='1', PROBE_LOG_FILE=str(log_file))
        result = subprocess.run(
            ['bash', '-c', 'source scripts/local_fetch_loop.sh; assert_no_unmerged'],
            cwd=str(self.clone), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr)

        self.assertEqual(self._git('diff', '--name-only', '--diff-filter=U',
                                   cwd=self.clone).stdout.strip(), '')
        self.assertEqual(self._git('ls-files', '-u', cwd=self.clone).stdout.strip(), '')
        log = log_file.read_text(encoding='utf-8')
        self.assertIn('unmerged', log)
        self.assertIn('discarded local 1 file(s)', log)

    def test_a_half_merged_clone_never_blocks_the_next_cycle(self):
        self._half_merged_clone()
        result = self._run_probe(cycles='2')
        self.assertEqual(result.returncode, 0, result.stderr)

        log = self._probe_log()
        self.assertNotIn('Pulling is not possible', log)
        self.assertNotIn('finished with errors', log)
        self.assertEqual(self._git('ls-files', '-u', cwd=self.clone).stdout.strip(), '')
        self.assertIn('probe cycle 2 done', log)


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

    def test_documents_the_stage_skips_and_the_data_ownership_table(self):
        """Issue #113: "who owns which data file" must be written down."""
        self.assertIn('PROBE_SKIP_STAGES', self.text)
        self.assertIn('PROBE_SKIP_HISTORY', self.text)
        # The ownership table (Actions / probe / both) and the conflict rule.
        self.assertIn('data/fetch-log.json', self.text)
        self.assertIn('--ours', self.text)
        self.assertIn('PROBE_SYNC_ATTEMPTS', self.text)

    def test_documents_the_self_heal_and_the_doctor(self):
        """Issue #121: nobody may believe a manual restart is still needed."""
        self.assertIn('probe_doctor.sh', self.text)
        self.assertIn('PROBE_MAX_SELF_RESTARTS', self.text)
        self.assertIn('自动重启', self.text)
        self.assertIn('--fix', self.text)
        # The final sync assertion (zero unmerged paths) is written down too.
        self.assertIn('unmerged', self.text)


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Contract tests for the local high-frequency probe (issue #74).

`scripts/local_fetch_loop.sh` is a thin, tmux-resident driver: it must reuse the
committed pipeline scripts (no forked fetch logic), bypass the Actions gate on
purpose, survive failures, and push with a bounded `pull --rebase` retry.  These
tests pin exactly those behaviours plus the documented start/stop commands, so a
later edit cannot silently turn the probe into a second fetch implementation or
make the loop exit on the first error.
"""

import re
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


class ProbeDocsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DOCS.read_text(encoding='utf-8')

    def test_documents_the_local_probe_section(self):
        self.assertIn('本机高频探针', self.text)

    def test_documents_the_fixed_tmux_session_name_and_commands(self):
        self.assertIn('tmux new -d -s probe', self.text)
        self.assertIn('tmux kill-session -t probe', self.text)

    def test_explains_relation_to_the_actions_pipeline(self):
        lowered = self.text
        self.assertIn('should_fetch.py', lowered)
        self.assertIn('probe', lowered.lower())


if __name__ == '__main__':
    unittest.main()

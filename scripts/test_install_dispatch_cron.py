#!/usr/bin/env python3
"""Tests for scripts/install_dispatch_cron.sh — the local wake-up installer (#93).

The installer is the piece of #93 that puts a *local* `crontab` entry in place:
one marked line (`# qc-gas-dispatch`) that wakes the data workflow every 5
minutes through `scripts/dispatch_fetch.sh`. The gate in the workflow then
decides, against the #91 timetable, whether the wake-up may download anything.

An operator depends on three properties, and a broken installer fails *silently*
(it installs a line that never works, or it piles up duplicate lines), so every
one of them is executed here against stubbed `crontab` / `gh` binaries:

* **idempotent install** — running the installer twice leaves exactly one
  marked line; unrelated lines are preserved; `--remove` deletes exactly the
  marked ones;
* **no secret in the crontab** — the token is fetched at run time
  (`$(gh auth token ...)`) or read from a 600-permission local file, never
  written into the crontab; the dry-run prints the line without touching the
  crontab at all;
* **preflight or non-zero exit** — a missing `crontab`, an unavailable token or
  a non-executable `scripts/dispatch_fetch.sh` must fail loudly *before* the
  entry is installed, and the installed line must surface the dispatch script's
  exit code (2 = missing token) in `.agent/dispatch-cron.log`.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'install_dispatch_cron.sh'
MARKER = '# qc-gas-dispatch'

CRONTAB_STUB = """#!/usr/bin/env bash
set -uo pipefail
store="${CRONTAB_STORE}"
if [ "${1:-}" = "-l" ]; then
  [ -f "$store" ] || exit 1
  cat "$store"
  exit 0
fi
cat > "$store"
"""

GH_STUB = """#!/usr/bin/env bash
if [ "${1:-}" = "auth" ] && [ "${2:-}" = "token" ]; then
  [ -n "${STUB_GH_TOKEN:-}" ] || exit 1
  echo "${STUB_GH_TOKEN}"
  exit 0
fi
exit 0
"""


def exec_stub(path, body):
    path.write_text(body, encoding='utf-8')
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class InstallDispatchCronTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dispatch-cron-test-'))
        self.bindir = self.tmp / 'bin'
        self.bindir.mkdir()
        self.crontab = self.bindir / 'crontab'
        self.gh = self.bindir / 'gh'
        exec_stub(self.crontab, CRONTAB_STUB)
        exec_stub(self.gh, GH_STUB)
        self.store = self.tmp / 'crontab.txt'
        self.token_file = self.tmp / 'dispatch-token'
        self.repo = self.tmp / 'repo'
        (self.repo / 'scripts').mkdir(parents=True)
        self.dispatch = self.repo / 'scripts' / 'dispatch_fetch.sh'
        exec_stub(self.dispatch, '#!/usr/bin/env bash\nexit 0\n')

    # ── static contract ─────────────────────────────────────────────────────

    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.exists(), 'scripts/install_dispatch_cron.sh is missing')
        self.assertTrue(SCRIPT.stat().st_mode & 0o111, 'installer not executable')

    def test_syntax_is_valid(self):
        result = subprocess.run(['bash', '-n', str(SCRIPT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_secret_is_committed(self):
        text = SCRIPT.read_text(encoding='utf-8')
        self.assertNotRegex(text, r'gh[pousr]_[A-Za-z0-9]{16,}')
        self.assertNotRegex(text, r'github_pat_[A-Za-z0-9_]{16,}')

    def test_documents_the_uninstall_and_log_paths(self):
        text = SCRIPT.read_text(encoding='utf-8')
        self.assertIn('--remove', text)
        self.assertIn('--dry-run', text)
        self.assertIn('.agent/dispatch-cron.log', text)

    # ── behaviour ───────────────────────────────────────────────────────────

    def run_script(self, *args, gh_token='stub-gh-token', token_file=None,
                   token_mode=0o600, dispatch_mode=0o755,
                   crontab='', max_bytes='2048', repo=None):
        if token_file is not None:
            token_file.write_text('stub-file-token', encoding='utf-8')
            token_file.chmod(token_mode)
        if dispatch_mode is not None:
            self.dispatch.chmod(dispatch_mode)
        if crontab:
            self.store.write_text(crontab, encoding='utf-8')

        child_env = os.environ.copy()
        child_env.update({
            'QC_GAS_REPO': str(repo or self.repo),
            'QC_GAS_GH': str(self.gh),
            'QC_GAS_CRONTAB': str(self.crontab),
            'QC_GAS_TOKEN_FILE': str(token_file or self.token_file),
            'QC_GAS_DISPATCH_REPO': 'owner/repo',
            'QC_GAS_LOG_MAX_BYTES': max_bytes,
            'CRONTAB_STORE': str(self.store),
            'STUB_GH_TOKEN': gh_token,
        })
        return subprocess.run(['bash', str(SCRIPT), *args], capture_output=True,
                              text=True, env=child_env, cwd=str(self.tmp))

    def crontab_lines(self):
        if not self.store.exists():
            return []
        return [line for line in self.store.read_text(encoding='utf-8').splitlines()
                if line.strip()]

    def marked(self):
        return [line for line in self.crontab_lines() if MARKER in line]

    # ── dry-run ─────────────────────────────────────────────────────────────

    def test_dry_run_prints_the_line_and_leaves_the_crontab_alone(self):
        result = self.run_script('--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('*/5 * * * *', result.stdout)
        self.assertIn(MARKER, result.stdout)
        self.assertIn('gh auth token', result.stdout)
        self.assertIn('.agent/dispatch-cron.log', result.stdout)
        self.assertFalse(self.store.exists(), 'dry-run must not write the crontab')

    # ── install ─────────────────────────────────────────────────────────────

    def test_install_writes_exactly_one_marked_line(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(self.marked()), 1)

    def test_the_line_wakes_every_five_minutes_and_cds_into_the_repo(self):
        self.run_script()
        line = self.marked()[0]
        self.assertTrue(line.startswith('*/5 * * * * '), line)
        self.assertIn(f'cd {self.repo}', line)
        self.assertIn('GH_DISPATCH_REPO=owner/repo', line)
        self.assertIn('scripts/dispatch_fetch.sh', line)

    def test_install_is_idempotent(self):
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(len(self.marked()), 1)

    def test_unrelated_lines_are_preserved(self):
        other = '17 * * * * /usr/bin/backup.sh'
        self.run_script(crontab=other + '\n')
        lines = self.crontab_lines()
        self.assertIn(other, lines)
        self.assertEqual(len(self.marked()), 1)

    def test_token_is_fetched_at_run_time_and_never_written_into_the_crontab(self):
        self.run_script(gh_token='stub-gh-token')
        line = self.marked()[0]
        self.assertIn('GH_DISPATCH_TOKEN="$(', line)
        self.assertNotIn('stub-gh-token', self.store.read_text(encoding='utf-8'))

    def test_the_installed_line_surfaces_the_exit_code_and_rotates_the_log(self):
        self.run_script()
        line = self.marked()[0]
        self.assertIn('exit=', line)                    # exit code visible in the log
        self.assertIn('.agent/dispatch-cron.log.1', line)  # rotation target
        self.assertIn('wc -c', line)                    # rotated by size
        self.assertTrue(line.rstrip().endswith(MARKER), line)

    # ── remove ──────────────────────────────────────────────────────────────

    def test_remove_deletes_exactly_the_marked_lines(self):
        marked = f'*/5 * * * * true {MARKER}'
        other = '17 * * * * /usr/bin/backup.sh'
        self.run_script('--remove', crontab=f'{other}\n{marked}\n')
        self.assertEqual(self.marked(), [])
        self.assertEqual(self.crontab_lines(), [other])

    def test_remove_after_install_leaves_zero_marked_lines(self):
        self.run_script()
        result = self.run_script('--remove')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.marked(), [])

    def test_remove_without_an_installed_line_succeeds(self):
        result = self.run_script('--remove', crontab='17 * * * * /usr/bin/backup.sh\n')
        self.assertEqual(result.returncode, 0, result.stderr)

    # ── preflight self-checks ───────────────────────────────────────────────

    def test_missing_crontab_fails_without_installing_anything(self):
        child_env = os.environ.copy()
        child_env.update({'QC_GAS_CRONTAB': str(self.tmp / 'no-such-crontab')})
        result = subprocess.run(['bash', str(SCRIPT)], capture_output=True, text=True,
                                env=child_env, cwd=str(self.tmp))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('crontab', result.stderr.lower())
        self.assertFalse(self.store.exists())

    def test_unavailable_token_fails_instead_of_installing_a_dead_entry(self):
        result = self.run_script(gh_token='')
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('token', result.stderr.lower())
        self.assertFalse(self.store.exists())

    def test_token_file_fallback_is_used_when_gh_cannot_provide_a_token(self):
        result = self.run_script(gh_token='', token_file=self.token_file, token_mode=0o600)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(str(self.token_file), self.marked()[0])

    def test_a_world_readable_token_file_is_rejected(self):
        result = self.run_script(gh_token='', token_file=self.token_file, token_mode=0o644)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self.store.exists())

    def test_a_non_executable_dispatch_script_fails_preflight(self):
        result = self.run_script(dispatch_mode=0o644)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn('dispatch_fetch.sh', result.stderr)
        self.assertFalse(self.store.exists())

    def test_unknown_repo_slug_fails_preflight(self):
        child_env = os.environ.copy()
        child_env.update({
            'QC_GAS_REPO': str(self.repo),
            'QC_GAS_GH': str(self.gh),
            'QC_GAS_CRONTAB': str(self.crontab),
            'QC_GAS_TOKEN_FILE': str(self.token_file),
            'QC_GAS_DISPATCH_REPO': '',
            'CRONTAB_STORE': str(self.store),
            'STUB_GH_TOKEN': 'stub-gh-token',
        })
        result = subprocess.run(['bash', str(SCRIPT)], capture_output=True, text=True,
                                env=child_env, cwd=str(self.tmp))
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertFalse(self.store.exists())

    def test_an_unknown_option_is_a_usage_error(self):
        result = self.run_script('--wat')
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)

    # ── the installed line itself (executed under `sh -c`, like cron does) ──

    def installed_line(self, token=''):
        """The exact line the installer would write, with an empty token."""
        self.token_file.write_text(token, encoding='utf-8')
        self.token_file.chmod(0o600)
        result = self.run_script('--dry-run', gh_token='', token_file=self.token_file)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip().splitlines()[-1]

    def run_line(self, line):
        """Run a crontab line the way cron does: first 5 fields stripped, `sh -c`."""
        command = line.split(None, 5)[5]
        return subprocess.run(['sh', '-c', command], capture_output=True, text=True,
                              cwd=str(self.tmp))

    def test_the_installed_line_logs_the_dispatch_exit_code(self):
        # Like the real script with an empty token: message on stderr, exit 2.
        exec_stub(self.dispatch,
                  '#!/usr/bin/env bash\n'
                  'echo "dispatch_fetch.sh: GH_DISPATCH_TOKEN 未设置，未派发任何请求。" >&2\n'
                  'exit 2\n')
        line = self.installed_line()
        self.assertIn('.agent/dispatch-cron.log', line)
        run = self.run_line(line)
        self.assertEqual(run.returncode, 0, run.stderr)
        log = (self.repo / '.agent' / 'dispatch-cron.log').read_text(encoding='utf-8')
        self.assertIn('GH_DISPATCH_TOKEN 未设置', log)
        self.assertIn('exit=2', log)

    def test_the_installed_line_rotates_an_oversized_log(self):
        log = self.repo / '.agent' / 'dispatch-cron.log'
        log.parent.mkdir(parents=True)
        log.write_text('x' * 4096, encoding='utf-8')
        exec_stub(self.dispatch, '#!/usr/bin/env bash\nexit 0\n')
        line = self.installed_line()
        self.run_line(line)
        self.assertTrue((self.repo / '.agent' / 'dispatch-cron.log.1').exists())
        self.assertLess(len(log.read_text(encoding='utf-8')), 4096)
        self.assertIn('exit=0', log.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()

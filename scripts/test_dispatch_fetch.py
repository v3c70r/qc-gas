#!/usr/bin/env python3
"""Tests for scripts/dispatch_fetch.sh — the external trigger caller (issue #77).

The script is the interface between an external cron service (or the owner's
`crontab`) and GitHub's `repository_dispatch` API, so its **exit code is the only
signal the external scheduler sees**. These tests therefore execute it against a
stub `curl` and pin the behaviour an operator depends on:

* success -> exit 0 and a "已向 … 派发" line;
* a failed request (401 from a wrong-scope PAT, network error, 422 …) -> a
  non-zero exit and **no** success line, so the cron service reports a failure
  instead of a silent no-op (that would fake the #77 evidence chain for hours);
* a missing token -> exit 2 without ever calling curl, plus the minimal-scope
  reminder;
* an unusable repository name / event type -> exit 3 without calling curl (the
  event type is interpolated into the JSON body, so it has to be validated);
* no secret may live in the repository, and the PAT scope is documented next to
  the code that needs it.
"""

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / 'scripts' / 'dispatch_fetch.sh'

# A stub curl: records its arguments, then exits with STUB_EXIT.
STUB_TEMPLATE = """#!/usr/bin/env bash
printf '%s\\n' "$@" > "${ARGS_FILE}"
echo "stub curl: simulated failure" >&2
exit "${STUB_EXIT:-0}"
"""


class DispatchScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = SCRIPT.read_text(encoding='utf-8') if SCRIPT.exists() else ''

    # ── static contract ─────────────────────────────────────────────────────

    def test_script_exists_and_is_executable(self):
        self.assertTrue(SCRIPT.exists(), 'scripts/dispatch_fetch.sh is missing')
        self.assertTrue(SCRIPT.stat().st_mode & 0o111, 'dispatch script not executable')

    def test_syntax_is_valid(self):
        result = subprocess.run(['bash', '-n', str(SCRIPT)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_calls_the_repository_dispatch_endpoint(self):
        self.assertIn('/dispatches', self.text)
        self.assertIn('api.github.com/repos', self.text)
        self.assertIn('event_type', self.text)
        self.assertIn('fetch-data', self.text)

    def test_token_comes_from_the_environment_only(self):
        # Access is PAT-only (contents:write), so `GITHUB_TOKEN` would be
        # rejected: the variable name must say it is a PAT-ish secret, and no
        # literal token may ever be committed.
        self.assertIn('GH_DISPATCH_TOKEN', self.text)
        self.assertNotRegex(self.text, r'(gh[pousr]_[A-Za-z0-9]{16,})')
        self.assertNotRegex(self.text, r'Authorization: Bearer [^$]')

    def test_pat_scope_is_documented(self):
        # Minimal scope reminder lives next to the code that uses it.
        self.assertRegex(self.text, r'contents: *write')
        self.assertNotIn('repo ', self.text.split('\n')[0])

    # ── behaviour (executed with a stub curl first on PATH) ─────────────────

    def run_script(self, *, stub_exit=0, remove=(), overrides=None):
        """Run the script with a stub ``curl``; return ``(result, args_file)``."""
        tmp = Path(tempfile.mkdtemp(prefix='dispatch-test-'))
        bindir = tmp / 'bin'
        bindir.mkdir()
        stub = bindir / 'curl'
        stub.write_text(STUB_TEMPLATE, encoding='utf-8')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        record = tmp / 'curl-args.txt'
        child_env = os.environ.copy()
        child_env.update({
            'PATH': f'{bindir}{os.pathsep}{child_env.get("PATH", "")}',
            'ARGS_FILE': str(record),
            'STUB_EXIT': str(stub_exit),
            'GH_DISPATCH_REPO': 'owner/repo',
            'GH_DISPATCH_TOKEN': 'stub-token-not-a-secret',
        })
        for key in remove:
            child_env.pop(key, None)
        child_env.update(overrides or {})

        result = subprocess.run(['bash', str(SCRIPT)], capture_output=True,
                                text=True, env=child_env, cwd=str(tmp))
        return result, record

    def test_successful_dispatch_exits_zero_and_says_so(self):
        result, record = self.run_script(stub_exit=0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('已向', result.stdout)
        args = record.read_text(encoding='utf-8')
        self.assertIn('https://api.github.com/repos/owner/repo/dispatches', args)
        self.assertIn('{"event_type": "fetch-data"}', args)

    def test_failed_dispatch_exits_non_zero_and_never_claims_success(self):
        # 401 = the owner pasted a PAT without Contents: write. The external
        # cron has to see a failure, otherwise a misconfigured trigger looks
        # like a working one until the 12-hour evidence report runs.
        result, record = self.run_script(stub_exit=22)
        self.assertEqual(result.returncode, 4,
                         f'stdout={result.stdout!r} stderr={result.stderr!r}')
        self.assertNotIn('已向', result.stdout)
        self.assertIn('失败', result.stderr)
        self.assertTrue(record.exists(), 'curl should still have been attempted')

    def test_missing_token_exits_two_without_calling_curl(self):
        result, record = self.run_script(remove=('GH_DISPATCH_TOKEN',))
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('GH_DISPATCH_TOKEN', result.stderr)
        self.assertIn('contents:write', result.stderr)
        self.assertFalse(record.exists(), 'curl must not run without a token')

    def test_repository_must_look_like_owner_repo(self):
        # No GH_DISPATCH_REPO and a cwd that is not a git checkout.
        result, record = self.run_script(remove=('GH_DISPATCH_REPO',))
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertFalse(record.exists(), 'curl must not run without a repository')

    def test_event_type_is_validated_before_it_reaches_the_json_body(self):
        for bad in ('bad"event', 'with space', 'semi;colon', 'a,comma'):
            result, record = self.run_script(overrides={'GH_DISPATCH_EVENT_TYPE': bad})
            self.assertEqual(result.returncode, 3, f'{bad!r}: {result.stderr}')
            self.assertFalse(record.exists(), f'{bad!r}: curl must not run')

    def test_blank_event_type_falls_back_to_the_default(self):
        # Same convention as `fetch_strategy.env_int`: blank means unset.
        result, record = self.run_script(overrides={'GH_DISPATCH_EVENT_TYPE': ''})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('{"event_type": "fetch-data"}',
                      record.read_text(encoding='utf-8'))

    def test_custom_event_type_is_passed_through(self):
        result, record = self.run_script(overrides={'GH_DISPATCH_EVENT_TYPE': 'fetch-data.v2'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('{"event_type": "fetch-data.v2"}',
                      record.read_text(encoding='utf-8'))

    def test_token_is_never_echoed(self):
        result, _ = self.run_script(stub_exit=0)
        self.assertNotIn('stub-token-not-a-secret', result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

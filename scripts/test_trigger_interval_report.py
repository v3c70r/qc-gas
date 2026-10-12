#!/usr/bin/env python3
"""Tests for scripts/trigger_interval_report.py — 抓取节奏的实测间隔（issue #77）。

#76 answers *how fast do we need to fetch*; this report answers *how fast does
the shipped trigger actually fetch*. The tests pin:

* commit subject -> trigger source (local probe / Actions / not a fetch at all);
* the measured-interval statistics and the "does it meet the target" verdict;
* the refusal to conclude from a window shorter than 12 hours (acceptance
  criterion of #77);
* the shipped rhythm itself: the Actions cron interval, the gate budget and the
  freshness net must stay consistent with each other and with the target
  interval (#76), and an external trigger must be wired when the cron alone
  cannot reach the target;
* the external-trigger helper script: no secrets in the repo, minimal token
  scope, the right dispatch endpoint and event type.
"""

import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trigger_interval_report as tir  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC_YML = ROOT / '.github' / 'workflows' / 'static.yml'
UPDATE_YML = ROOT / '.github' / 'workflows' / 'update-data.yml'
DISPATCH_SH = ROOT / 'scripts' / 'dispatch_fetch.sh'
HISTORY_DOC = ROOT / 'docs' / 'history-data.md'

UTC = timezone.utc
END = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)


def series(step_minutes, count, end=END):
    """``count`` fetches ending at ``end``, ``step_minutes`` apart."""
    return [end - timedelta(minutes=step_minutes * i) for i in range(count)]


class SourceClassificationTest(unittest.TestCase):
    def test_probe_commit_is_attributed_to_the_local_probe(self):
        subject = 'Update gas prices data (local probe) [skip ci]'
        self.assertEqual(tir.fetch_source(subject), tir.SOURCE_PROBE)

    def test_actions_data_commit_is_attributed_to_actions(self):
        self.assertEqual(tir.fetch_source('Update gas prices data [skip ci]'),
                         tir.SOURCE_ACTIONS)

    def test_non_data_commits_have_no_source(self):
        for subject in ('Agent A: something (#77)', 'Merge pull request #84',
                        'chore: bump vite', ''):
            self.assertIsNone(tir.fetch_source(subject), subject)

    def test_missing_subject_is_not_a_fetch(self):
        self.assertIsNone(tir.fetch_source(None))


class GitLogParsingTest(unittest.TestCase):
    LOG = '\n'.join([
        '2026-10-12T08:08:29+00:00\x1fUpdate gas prices data [skip ci]',
        '2026-10-12T06:51:20+00:00\x1fUpdate gas prices data (local probe) [skip ci]',
        '2026-10-12T04:08:08-04:00\x1fAgent: 时延与新鲜度指标 (#80)',
        '',
        'garbage-line-without-separator',
        'not-a-timestamp\x1fUpdate gas prices data [skip ci]',
    ])

    def test_only_data_commits_are_kept_and_sorted_oldest_first(self):
        fetches = tir.parse_git_log(self.LOG)
        self.assertEqual([f['source'] for f in fetches],
                         [tir.SOURCE_PROBE, tir.SOURCE_ACTIONS])
        self.assertEqual([(f['at'].hour, f['at'].minute) for f in fetches],
                         [(6, 51), (8, 8)])

    def test_offsets_are_normalised_to_utc(self):
        fetches = tir.parse_git_log('2026-10-12T04:00:00-04:00\x1fUpdate gas prices data [skip ci]')
        self.assertEqual(fetches[0]['at'], datetime(2026, 10, 12, 8, 0, tzinfo=UTC))

    def test_empty_input_yields_no_fetches(self):
        self.assertEqual(tir.parse_git_log(''), [])


class IntervalStatsTest(unittest.TestCase):
    def test_15_minute_series_meets_a_30_minute_target(self):
        stats = tir.interval_stats(series(15, 97))
        self.assertEqual(stats['fetches'], 97)
        self.assertEqual(stats['intervals'], 96)
        self.assertEqual(stats['p50'], 15.0)
        self.assertEqual(stats['p90'], 15.0)
        self.assertAlmostEqual(stats['span_hours'], 24.0, places=1)
        self.assertEqual(stats['over_target'], 0)
        self.assertEqual(stats['verdict'], 'meets')

    def test_throttled_actions_series_misses_the_target(self):
        stats = tir.interval_stats(series(260, 10))
        self.assertEqual(stats['p50'], 260.0)
        self.assertGreater(stats['p90'], tir.TARGET_INTERVAL_MINUTES)
        self.assertEqual(stats['over_target_share'], 1.0)
        self.assertEqual(stats['verdict'], 'misses')

    def test_short_window_cannot_conclude(self):
        stats = tir.interval_stats(series(30, 3))
        self.assertLess(stats['span_hours'], tir.MIN_WINDOW_HOURS)
        self.assertEqual(stats['verdict'], 'insufficient-window')

    def test_no_data_at_all_is_insufficient(self):
        stats = tir.interval_stats([])
        self.assertEqual(stats['fetches'], 0)
        self.assertIsNone(stats['p50'])
        self.assertEqual(stats['verdict'], 'insufficient-window')

    def test_window_keeps_only_intervals_starting_inside_it(self):
        stats = tir.interval_stats(series(60, 48), since=END - timedelta(hours=12))
        self.assertEqual(stats['intervals'], 12)
        self.assertEqual(stats['span_hours'], 12.0)

    def test_an_old_gap_does_not_pollute_the_window(self):
        # One 6-hour hole (laptop asleep) followed by 12 hours of 15-min
        # cadence: the current window must read 15 minutes, not 6 hours.
        times = [END - timedelta(hours=18)] + series(15, 49)
        stats = tir.interval_stats(times, since=END - timedelta(hours=12))
        self.assertEqual(stats['p50'], 15.0)
        self.assertEqual(stats['span_hours'], 12.0)
        self.assertEqual(stats['verdict'], 'meets')

    def test_bootstrap_single_fetch_has_no_p50(self):
        stats = tir.interval_stats([END])
        self.assertEqual(stats['fetches'], 1)
        self.assertEqual(stats['intervals'], 0)
        self.assertIsNone(stats['p50'])


class DecideTest(unittest.TestCase):
    def test_actions_wins_when_it_meets_the_target(self):
        chosen, message = tir.decide({tir.SOURCE_ACTIONS: 'meets',
                                      tir.SOURCE_PROBE: 'meets'})
        self.assertEqual(chosen, tir.SOURCE_ACTIONS)
        self.assertIn('cron', message)

    def test_probe_wins_when_actions_is_throttled(self):
        chosen, message = tir.decide({tir.SOURCE_ACTIONS: 'misses',
                                      tir.SOURCE_PROBE: 'meets'})
        self.assertEqual(chosen, tir.SOURCE_PROBE)
        self.assertIn(tir.SOURCE_PROBE, message)

    def test_stays_undecided_while_the_evidence_window_is_too_short(self):
        chosen, message = tir.decide({tir.SOURCE_ACTIONS: 'misses',
                                      tir.SOURCE_PROBE: 'insufficient-window'})
        self.assertEqual(chosen, 'undecided')
        self.assertIn('12', message)


class PayloadTest(unittest.TestCase):
    def payload(self, fetches=None, **kwargs):
        return tir.build_payload(Path('/nonexistent'), Path('/nonexistent'), END,
                                 fetches=fetches or {}, **kwargs)

    def test_reports_both_sources_with_history_and_recent_windows(self):
        payload = self.payload({
            tir.SOURCE_ACTIONS: series(260, 10),
            tir.SOURCE_PROBE: series(15, 97),
        })
        self.assertEqual(set(payload['sources']), {tir.SOURCE_ACTIONS, tir.SOURCE_PROBE})
        self.assertEqual(payload['sources'][tir.SOURCE_ACTIONS]['verdict'], 'misses')
        self.assertEqual(payload['sources'][tir.SOURCE_PROBE]['verdict'], 'meets')
        self.assertEqual(payload['decision']['chosen'], tir.SOURCE_PROBE)
        self.assertEqual(payload['target_interval_minutes'], 30)
        self.assertEqual(payload['criteria']['min_window_hours'], 12.0)

    def test_history_can_be_restricted_to_one_rhythm_era(self):
        fetches = {tir.SOURCE_ACTIONS: series(15, 97)}
        restricted = self.payload(fetches, since=END - timedelta(hours=3))
        stats = restricted['sources'][tir.SOURCE_ACTIONS]['history']
        self.assertEqual(stats['intervals'], 12)
        self.assertEqual(stats['span_hours'], 3.0)

    def test_no_evidence_yet_is_reported_as_undecided(self):
        payload = self.payload({tir.SOURCE_ACTIONS: [], tir.SOURCE_PROBE: []})
        self.assertEqual(payload['decision']['chosen'], 'undecided')
        self.assertIn('insufficient-window',
                      {rec['verdict'] for rec in payload['sources'].values()})

    def test_payload_carries_the_shipped_rhythm_and_the_bias_notes(self):
        payload = self.payload({tir.SOURCE_ACTIONS: series(15, 97)},
                               shipped={'cron': '17 * * * *',
                                        'cron_interval_minutes': 60,
                                        'daily_budget': 24,
                                        'max_age_minutes': 120})
        self.assertEqual(payload['shipped']['daily_budget'], 24)
        self.assertTrue(payload['notes'])
        self.assertIn('上界', ' '.join(payload['notes']))

    def test_summary_is_human_readable(self):
        text = tir.format_summary(self.payload({tir.SOURCE_ACTIONS: series(15, 97)}))
        self.assertIn('目标间隔 30 分钟', text)
        self.assertIn('| 来源 |', text)


class ShippedRhythmTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.static = STATIC_YML.read_text(encoding='utf-8')
        cls.update = UPDATE_YML.read_text(encoding='utf-8')
        cls.shipped = tir.shipped_rhythm(ROOT)

    def test_cron_interval_parser(self):
        self.assertEqual(tir.cron_interval_minutes('17 * * * *'), 60)
        self.assertEqual(tir.cron_interval_minutes('0 */2 * * *'), 120)
        self.assertEqual(tir.cron_interval_minutes('30 5 * * *'), 1440)
        self.assertIsNone(tir.cron_interval_minutes('*/15 * * * * *'))

    def test_shipped_cron_is_a_long_hourly_fallback(self):
        self.assertEqual(self.shipped['cron_interval_minutes'], 60)

    def test_gate_budget_matches_the_wake_up_cadence(self):
        self.assertEqual(self.shipped['daily_budget'],
                         int(round(1440 / self.shipped['cron_interval_minutes'])))

    def test_freshness_net_covers_two_missed_wakes(self):
        self.assertEqual(self.shipped['max_age_minutes'],
                         2 * self.shipped['cron_interval_minutes'])

    def test_shipped_rhythm_is_self_consistent(self):
        self.assertEqual(tir.rhythm_errors(self.shipped), [])

    def test_cron_alone_cannot_reach_the_target_so_dispatch_is_wired(self):
        self.assertTrue(self.shipped['external_trigger_required'])
        self.assertIn('repository_dispatch', self.static)
        self.assertRegex(self.static, r'repository_dispatch:\s*\n\s*types:\s*\[?fetch-data')

    def test_target_interval_comes_from_the_76_criteria(self):
        # p90 stale lag <= 30 min and p90 lag ~ 0.9*T  =>  T <= 33 min -> 30.
        self.assertEqual(tir.TARGET_INTERVAL_MINUTES, 30)
        self.assertEqual(self.shipped['target_interval_minutes'], 30)

    def test_docs_document_the_final_rhythm_and_the_probe_decision(self):
        doc = HISTORY_DOC.read_text(encoding='utf-8')
        for needle in ('repository_dispatch', 'fetch-data', 'fetch-interval-report.md',
                       'trigger_interval_report.py', 'tmux kill-session -t probe'):
            self.assertIn(needle, doc, needle)


class DispatchScriptTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = DISPATCH_SH.read_text(encoding='utf-8') if DISPATCH_SH.exists() else ''

    def test_script_exists_and_is_executable(self):
        self.assertTrue(DISPATCH_SH.exists(), 'scripts/dispatch_fetch.sh is missing')
        self.assertTrue(DISPATCH_SH.stat().st_mode & 0o111, 'dispatch script not executable')

    def test_syntax_is_valid(self):
        result = subprocess.run(['bash', '-n', str(DISPATCH_SH)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_calls_the_repository_dispatch_endpoint(self):
        self.assertIn('/dispatches', self.text)
        self.assertIn('api.github.com/repos', self.text)
        self.assertRegex(self.text, r'event_type')
        self.assertIn(tir.DISPATCH_EVENT_TYPE, self.text)

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


if __name__ == '__main__':
    unittest.main()

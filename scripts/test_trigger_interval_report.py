#!/usr/bin/env python3
"""Tests for scripts/trigger_interval_report.py — 抓取节奏的实测间隔（issue #77）。

#76 answers *how fast do we need to fetch*; this report answers *how fast does
the shipped trigger actually fetch*. The tests pin:

* commit subject -> trigger source (local probe / Actions / not a fetch at all);
* the measured-interval statistics and the "does it meet the criterion" verdict;
* the #93 criterion: **busy hours (local 06:00-23:59) p90 <= 20 minutes** plus a
  stale share <= 5 % for the timetable source, instead of the old global
  "p90 <= 30 minutes" rule that failed the deliberate 60-minute night floor by
  construction;
* the local dispatch source (#93): the committed timetable plus
  ``scripts/schedule_eval.py`` on the probe series;
* the refusal to conclude from a window shorter than 12 hours (acceptance
  criterion of #77);
* the shipped rhythm itself: the fallback cron interval, the plan budget (= the
  committed timetable's slot count since #93), the freshness net and the local
  wake-up interval must stay consistent with each other.

The behaviour of the local trigger helpers themselves (`scripts/dispatch_fetch.sh`
and `scripts/install_dispatch_cron.sh`) is pinned in `scripts/test_dispatch_fetch.py`
and `scripts/test_install_dispatch_cron.py`.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import trigger_interval_report as tir  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
STATIC_YML = ROOT / '.github' / 'workflows' / 'static.yml'
UPDATE_YML = ROOT / '.github' / 'workflows' / 'update-data.yml'
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


class GitHistorySourceTest(unittest.TestCase):
    """Issue #103: the derived snapshot stopped being committed.

    ``data/stations.json`` used to be the fetch record: one commit per changed
    snapshot. It is a derived snapshot now, so the Actions side is read from
    ``data/fetch-state.json`` (written by the gate on every admitted run, never
    by the local probe) **in addition to** the historical snapshot commits, so
    the old era in the report does not disappear.
    """

    def test_the_fetch_record_spans_the_snapshot_and_the_gate_state(self):
        self.assertIn(tir.SNAPSHOT_REL, tir.FETCH_HISTORY_PATHS)
        self.assertIn(tir.FETCH_STATE_REL, tir.FETCH_HISTORY_PATHS)

    def test_git_log_is_read_for_every_history_path(self):
        calls = []

        class Result:
            stdout = ''

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return Result()

        with mock.patch.object(tir.subprocess, 'run', fake_run):
            tir.load_git_fetches(ROOT)

        paths = [cmd[cmd.index('--') + 1] for cmd in calls if '--' in cmd]
        for rel in tir.FETCH_HISTORY_PATHS:
            self.assertIn(rel, paths)

    def test_a_commit_touching_both_files_is_counted_once(self):
        log = '2026-10-12T08:08:29+00:00\x1fUpdate gas prices data [skip ci]\n'

        class Result:
            stdout = log

        with mock.patch.object(tir.subprocess, 'run', lambda *a, **k: Result()):
            fetches = tir.load_git_fetches(ROOT)
        self.assertEqual(len(fetches), 1)
        self.assertEqual(fetches[0]['source'], tir.SOURCE_ACTIONS)

    def test_the_report_names_the_new_source(self):
        payload = tir.build_payload(ROOT / 'data', ROOT, END, with_dispatch=False)
        self.assertIn(tir.FETCH_STATE_REL, payload['source'][tir.SOURCE_ACTIONS])


class IntervalStatsTest(unittest.TestCase):
    def test_a_fifteen_minute_series_meets_the_busy_criterion(self):
        stats = tir.interval_stats(series(15, 97))
        self.assertEqual(stats['fetches'], 97)
        self.assertEqual(stats['intervals'], 96)
        self.assertEqual(stats['p50'], 15.0)
        self.assertEqual(stats['p90'], 15.0)
        self.assertEqual(stats['busy_p90'], 15.0)
        self.assertAlmostEqual(stats['span_hours'], 24.0, places=1)
        self.assertEqual(stats['over_target'], 0)
        self.assertEqual(stats['verdict'], 'meets')

    def test_throttled_actions_series_misses_the_criterion(self):
        stats = tir.interval_stats(series(260, 10))
        self.assertEqual(stats['p50'], 260.0)
        self.assertGreater(stats['p90'], tir.BUSY_P90_MAX_MINUTES)
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

    def test_a_bad_stale_share_fails_a_plan_that_is_dense_enough(self):
        stats = tir.interval_stats(series(15, 97), stale_share=0.11)
        self.assertEqual(stats['busy_p90'], 15.0)
        self.assertEqual(stats['verdict'], 'misses')


class BusyHoursTest(unittest.TestCase):
    """#93: only the local busy hours (06:00-23:59) count against the criterion.

    The 01:00-05:00 hour is deliberately kept at 60 minutes by #91 (the night
    carries ~1/121 of the day's re-prices), so the old global p90 rule failed a
    correct plan by construction.
    """

    def test_a_night_hole_is_not_counted(self):
        # 05:00 -> 07:00 local (EDT: 09:00Z -> 11:00Z) is a two-hour hole whose
        # start is outside the busy window: it must not enter busy_*.
        times = [datetime(2026, 10, 12, 9, 0, tzinfo=UTC),
                 datetime(2026, 10, 12, 11, 0, tzinfo=UTC),
                 datetime(2026, 10, 12, 11, 20, tzinfo=UTC)]
        busy = tir.busy_intervals_minutes(times)
        self.assertEqual(busy, [20.0])
        self.assertEqual(tir.gap_stats(busy)['p90'], 20.0)

    def test_all_gaps_are_still_reported_for_the_historical_shape(self):
        stats = tir.interval_stats(series(60, 48), since=END - timedelta(hours=12))
        self.assertEqual(stats['intervals'], 12)
        self.assertEqual(stats['p90'], 60.0)
        self.assertEqual(stats['busy_p90'], 60.0)

    def test_a_timetable_with_a_night_floor_meets_the_busy_criterion(self):
        # busy every 20 min, local midnight every 10 min, 01:00-05:00 hourly.
        timetable = []
        for hour in range(6, 24):
            timetable += [f'{hour:02d}:00', f'{hour:02d}:20', f'{hour:02d}:40']
        timetable += [f'00:{minute:02d}' for minute in range(0, 60, 10)]
        timetable += [f'{hour:02d}:00' for hour in range(1, 6)]
        gaps = tir.timetable_busy_gaps(sorted(timetable))
        self.assertEqual(sorted(set(gaps)), [20.0])
        self.assertEqual(tir.gap_stats(gaps)['p90'], 20.0)


class DecideTest(unittest.TestCase):
    def test_actions_wins_when_it_meets_the_target(self):
        chosen, message = tir.decide({tir.SOURCE_ACTIONS: 'meets',
                                      tir.SOURCE_PROBE: 'meets'})
        self.assertEqual(chosen, tir.SOURCE_ACTIONS)
        self.assertIn('cron', message)

    def test_probe_wins_when_actions_is_throttled_and_dispatch_is_absent(self):
        chosen, message = tir.decide({tir.SOURCE_ACTIONS: 'misses',
                                      tir.SOURCE_PROBE: 'meets'})
        self.assertEqual(chosen, tir.SOURCE_PROBE)
        self.assertIn(tir.SOURCE_PROBE, message)

    def test_the_local_dispatch_chain_is_preferred_over_the_probe(self):
        chosen, message = tir.decide({tir.SOURCE_ACTIONS: 'misses',
                                      tir.SOURCE_DISPATCH: 'meets',
                                      tir.SOURCE_PROBE: 'meets'})
        self.assertEqual(chosen, tir.SOURCE_DISPATCH)
        self.assertIn(f'{tir.BUSY_P90_MAX_MINUTES}', message)

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
        self.assertEqual(payload['target_interval_minutes'], tir.BUSY_P90_MAX_MINUTES)
        self.assertEqual(payload['criteria']['min_window_hours'], 12.0)
        self.assertEqual(payload['criteria']['busy_p90_max_minutes'], 20)
        self.assertEqual(payload['criteria']['stale_share_max'], tir.STALE_SHARE_MAX)

    def test_the_local_dispatch_source_is_reported_when_injected(self):
        dispatch = {'method': 'stub', 'history': {'fetches': 65, 'span_hours': 24.0,
                                                  'intervals': 54, 'busy_p90': 20.0,
                                                  'p50': 20.0, 'max': 20.0,
                                                  'over_target_share': 0.0,
                                                  'stale_share': 0.012,
                                                  'verdict': 'meets'},
                    'verdict': 'meets', 'meets_target': True}
        payload = self.payload({tir.SOURCE_ACTIONS: series(260, 10)}, dispatch=dispatch)
        self.assertIn(tir.SOURCE_DISPATCH, payload['sources'])
        self.assertEqual(payload['sources'][tir.SOURCE_DISPATCH]['verdict'], 'meets')
        self.assertEqual(payload['decision']['chosen'], tir.SOURCE_DISPATCH)
        self.assertIn(tir.SOURCE_DISPATCH, payload['source'])

    def test_the_notes_explain_the_busy_hour_criterion(self):
        payload = self.payload({tir.SOURCE_ACTIONS: series(260, 10)})
        notes = ' '.join(payload['notes'])
        self.assertIn('忙时', notes)
        self.assertIn('schedule_eval', notes)
        self.assertIn('时刻表', notes)

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
        self.assertIn('忙时', text)
        self.assertIn('| 来源 |', text)
        self.assertIn(tir.SOURCE_ACTIONS, text)


class DispatchSourceTest(unittest.TestCase):
    """#93: the local dispatch chain is judged by the committed timetable + #91.

    The gate only admits the timetable's slots, so the *fetch* cadence of the
    local dispatch source is the timetable itself; the staleness share comes
    from ``schedule_eval.py`` simulating that timetable on the probe series.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='dispatch-source-test-'))
        self.data_dir = self.tmp / 'data'
        self.data_dir.mkdir()
        self.observations = self.tmp / 'observations.json'
        entries = []
        for hour in range(24):
            price = 150.0 if hour < 10 else (160.0 if hour < 20 else 170.0)
            entries.append({
                'at': f'2026-10-16T{hour:02d}:00:00Z',
                's': {'1.00000,1.00000': {'p': [price, None, None]}},
            })
        self.observations.write_text(json.dumps(entries), encoding='utf-8')

    def write_timetable(self, timetable):
        (self.data_dir / 'fetch-schedule.json').write_text(
            json.dumps({'v': 2, 'timezone': tir.LOCAL_TZ, 'timetable': timetable}),
            encoding='utf-8')

    def busy20_timetable(self):
        return [f'{hour:02d}:{minute:02d}'
                for hour in range(24) for minute in (0, 20, 40)]

    def test_the_timetable_is_read_from_the_committed_schedule(self):
        self.write_timetable(['06:00', '06:20'])
        self.assertEqual(tir.load_shipped_timetable(self.data_dir), ['06:00', '06:20'])

    def test_no_committed_timetable_means_no_dispatch_source(self):
        self.assertIsNone(tir.dispatch_evidence(self.data_dir))

    def test_a_busy_twenty_minute_timetable_meets_with_a_low_stale_share(self):
        self.write_timetable(self.busy20_timetable())
        record = tir.dispatch_evidence(self.data_dir,
                                       observations_path=self.observations)
        self.assertIsNotNone(record)
        self.assertEqual(record['verdict'], 'meets')
        self.assertTrue(record['meets_target'])
        self.assertEqual(record['history']['busy_p90'], 20.0)
        self.assertLess(record['history']['stale_share'], tir.STALE_SHARE_MAX)
        self.assertIn('时刻表', record['method'])
        self.assertEqual(record['schedule_eval']['verdict']['acceptable'], True)

    def test_a_coarse_timetable_misses_the_busy_criterion(self):
        self.write_timetable([f'{hour:02d}:00' for hour in range(24)])
        record = tir.dispatch_evidence(self.data_dir,
                                       observations_path=self.observations)
        self.assertEqual(record['history']['busy_p90'], 60.0)
        self.assertEqual(record['verdict'], 'misses')

    def test_the_shipped_timetable_meets_on_the_real_probe_series(self):
        # The acceptance evidence for #93: the plan actually committed in this
        # repository, evaluated with schedule_eval against the measured series.
        timetable = tir.load_shipped_timetable(ROOT / 'data')
        self.assertTrue(timetable)
        record = tir.dispatch_evidence(ROOT / 'data', root=ROOT)
        if record['schedule_eval']['events'] is None:
            self.skipTest('no probe observations in this checkout')
        self.assertEqual(record['verdict'], 'meets', record['history'])
        self.assertLessEqual(record['history']['busy_p90'], tir.BUSY_P90_MAX_MINUTES)
        self.assertLessEqual(record['history']['stale_share'], tir.STALE_SHARE_MAX)


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

    def test_gate_budget_matches_the_committed_timetable(self):
        # #93 replaced the old #77 rule (budget == 1440 / cron interval): the
        # trigger is the local 5-minute dispatch, and the plan budget is the
        # timetable's slot count, which the plan writer must not shrink.
        self.assertEqual(self.shipped['daily_budget'],
                         self.shipped['timetable_fetches_per_day'])
        self.assertEqual(self.shipped['daily_budget'], 65)

    def test_the_local_wake_up_is_denser_than_the_busy_plan(self):
        self.assertEqual(self.shipped['dispatch_wake_minutes'], 5)
        self.assertLessEqual(self.shipped['dispatch_wake_minutes'],
                             self.shipped['timetable_busy_p90_minutes'])

    def test_the_busy_plan_meets_the_published_criterion(self):
        self.assertEqual(self.shipped['timetable_busy_p90_minutes'],
                         tir.BUSY_P90_MAX_MINUTES)

    def test_the_wake_up_interval_is_read_from_the_installer(self):
        installer = (ROOT / 'scripts' / 'install_dispatch_cron.sh').read_text(encoding='utf-8')
        self.assertIn('DEFAULT_WAKE_MINUTES=5', installer)
        self.assertEqual(tir.dispatch_wake_minutes(ROOT), 5)

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
        # #76's overall target (p90 stale lag <= 30 min => T <= 33 min -> 30) is
        # kept as a reference; the *judged* criterion is #93's busy-hour one.
        self.assertEqual(tir.TARGET_INTERVAL_MINUTES, 30)
        self.assertEqual(tir.BUSY_P90_MAX_MINUTES, 20)
        self.assertEqual(tir.STALE_SHARE_MAX, 0.05)
        self.assertEqual(self.shipped['target_interval_minutes'], 30)

    def test_docs_document_the_final_rhythm_and_the_probe_decision(self):
        doc = HISTORY_DOC.read_text(encoding='utf-8')
        for needle in ('repository_dispatch', 'fetch-data', 'fetch-interval-report.md',
                       'trigger_interval_report.py', 'tmux kill-session -t probe',
                       'install_dispatch_cron.sh', 'dispatch-cron.log',
                       'schedule_eval.py', 'fetch-state.json'):
            self.assertIn(needle, doc, needle)


if __name__ == '__main__':
    unittest.main()

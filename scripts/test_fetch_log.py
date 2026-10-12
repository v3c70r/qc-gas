#!/usr/bin/env python3
"""Tests for scripts/fetch_log.py — the local probe's observation ledger.

The tmux-resident probe (`scripts/local_fetch_loop.sh`) wants a durable record
of *when* it actually completed a fetch cycle so issue #B can compute the real
achievable interval distribution, plus per-day failure/retry counters and a
once-per-day health summary for issue #78.  Everything here is a pure function
over a small JSON document (`data/fetch-log.json`), so the tests pin the math,
the retention caps and the daily-summary dedupe contract the shell loop reads.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fetch_log  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 14, 23, 0, 0, tzinfo=UTC)


def iso(day, hour=0, minute=0, second=0):
    return f'2026-10-{day:02d}T{hour:02d}:{minute:02d}:{second:02d}Z'


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)


class LoadLogTest(TempDataDirTest):
    def test_missing_file_yields_empty_log(self):
        log = fetch_log.load_log(self.data_dir / 'fetch-log.json')
        self.assertEqual(log['fetches'], [])
        self.assertEqual(log['days'], {})
        self.assertFalse(log['summary_sent'])

    def test_corrupt_file_degrades_to_empty_log(self):
        path = self.data_dir / 'fetch-log.json'
        path.write_text('{not json', encoding='utf-8')
        self.assertEqual(fetch_log.load_log(path)['fetches'], [])


class AppendFetchTest(TempDataDirTest):
    def test_appends_iso_timestamp_in_order(self):
        log = fetch_log.empty_log()
        log = fetch_log.append_fetch(log, iso(12, 0, 0))
        log = fetch_log.append_fetch(log, iso(12, 0, 15))
        self.assertEqual(log['fetches'], [iso(12, 0, 0), iso(12, 0, 15)])

    def test_keeps_only_the_newest_max_entries(self):
        start = datetime(2026, 10, 1, 0, 0, tzinfo=UTC)
        stamps = [(start + timedelta(minutes=i)).strftime('%Y-%m-%dT%H:%M:%SZ')
                  for i in range(fetch_log.MAX_ENTRIES + 1)]
        log = fetch_log.empty_log()
        for stamp in stamps:
            log = fetch_log.append_fetch(log, stamp)
        self.assertEqual(len(log['fetches']), fetch_log.MAX_ENTRIES)
        self.assertEqual(log['fetches'][-1], stamps[-1])
        self.assertEqual(log['fetches'][0], stamps[1])

    def test_re_appending_the_same_timestamp_is_a_noop(self):
        log = fetch_log.append_fetch(fetch_log.empty_log(), iso(12, 0, 0))
        again = fetch_log.append_fetch(log, iso(12, 0, 0))
        self.assertEqual(again['fetches'], [iso(12, 0, 0)])
        self.assertEqual(fetch_log.append_fetch(log, iso(12, 0, 0), max_entries=1)['fetches'],
                         [iso(12, 0, 0)])


class DayCountersTest(unittest.TestCase):
    def test_ok_and_failure_and_retries_are_counted_per_day(self):
        log = fetch_log.empty_log()
        log = fetch_log.record_result(log, '2026-10-12', ok=True, retries=2)
        log = fetch_log.record_result(log, '2026-10-12', ok=False, retries=1)
        self.assertEqual(log['days']['2026-10-12'],
                         {'ok': 1, 'failed': 1, 'retries': 3})

    def test_failure_does_not_add_a_fetch_timestamp(self):
        log = fetch_log.record_result(fetch_log.empty_log(), '2026-10-12', ok=False)
        self.assertEqual(log['fetches'], [])
        self.assertEqual(log['days']['2026-10-12']['failed'], 1)

    def test_counters_older_than_the_retention_window_are_pruned(self):
        log = fetch_log.empty_log()
        log = fetch_log.record_result(log, '2026-08-01', ok=True)
        log = fetch_log.record_result(log, '2026-10-12', ok=True)
        self.assertEqual(sorted(log['days']), ['2026-10-12'])


class IntervalTest(unittest.TestCase):
    def test_intervals_are_seconds_between_consecutive_fetches(self):
        fetches = [iso(12, 0, 0), iso(12, 0, 15), iso(12, 0, 45)]
        self.assertEqual(fetch_log.intervals_seconds(fetches), [900.0, 1800.0])

    def test_single_fetch_has_no_interval(self):
        self.assertEqual(fetch_log.intervals_seconds([iso(12, 0, 0)]), [])

    def test_since_keeps_only_intervals_that_start_inside_the_window(self):
        fetches = [iso(12, 0, 0), iso(12, 6, 0), iso(12, 6, 15)]
        since = datetime(2026, 10, 12, 5, 59, tzinfo=UTC)
        self.assertEqual(fetch_log.intervals_seconds(fetches, since=since), [900.0])

    def test_interval_starting_before_since_is_dropped(self):
        fetches = [iso(12, 0, 0), iso(12, 6, 0)]
        since = datetime(2026, 10, 12, 5, 59, tzinfo=UTC)
        self.assertEqual(fetch_log.intervals_seconds(fetches, since=since), [])


class SummaryTest(unittest.TestCase):
    def log_with(self, fetches, days=None):
        log = fetch_log.empty_log()
        log['fetches'] = list(fetches)
        log['days'] = days or {}
        return log

    def test_reports_p50_and_p90_of_the_last_window(self):
        # 15-minute cadence plus one 45-minute gap inside the last 24h; p90
        # interpolates between the 15-min and 45-min gaps (metrics.percentile).
        fetches = [iso(14, 0, 0), iso(14, 0, 15), iso(14, 0, 30), iso(14, 1, 15)]
        summary = fetch_log.summary(self.log_with(fetches), NOW)
        self.assertEqual(summary['interval_count'], 3)
        self.assertEqual(summary['interval_p50'], 900.0)
        self.assertEqual(summary['interval_p90'], 2340.0)

    def test_old_intervals_are_outside_the_window(self):
        fetches = [iso(1, 0, 0), iso(1, 0, 15), iso(14, 0, 0), iso(14, 0, 15)]
        summary = fetch_log.summary(self.log_with(fetches), NOW, window_hours=24)
        self.assertEqual(summary['interval_count'], 1)
        self.assertEqual(summary['interval_p50'], 900.0)

    def test_no_intervals_report_none_not_zero(self):
        summary = fetch_log.summary(self.log_with([iso(14, 0, 0)]), NOW)
        self.assertIsNone(summary['interval_p50'])
        self.assertIsNone(summary['interval_p90'])
        self.assertEqual(summary['interval_count'], 0)

    def test_days_covered_spans_first_to_last_fetch_day(self):
        fetches = [iso(12, 0, 0), iso(14, 0, 0)]
        summary = fetch_log.summary(self.log_with(fetches), NOW)
        self.assertEqual(summary['days_covered'], 3)
        self.assertEqual(summary['first_fetch_at'], iso(12, 0, 0))
        self.assertEqual(summary['last_fetch_at'], iso(14, 0, 0))
        self.assertEqual(summary['total_fetches'], 2)

    def test_samples_are_not_ready_before_three_days(self):
        fetches = [iso(13, 0, 0), iso(14, 0, 0)]
        summary = fetch_log.summary(self.log_with(fetches), NOW)
        self.assertEqual(summary['days_covered'], 2)
        self.assertFalse(summary['ready_for_76'])

    def test_three_days_of_data_are_ready_for_76(self):
        fetches = [iso(12, 0, 0), iso(14, 0, 0)]
        summary = fetch_log.summary(self.log_with(fetches), NOW)
        self.assertEqual(summary['days_covered'], 3)
        self.assertTrue(summary['ready_for_76'])

    def test_failures_and_retries_come_from_the_day_counters(self):
        fetches = [iso(14, 0, 0), iso(14, 0, 15)]
        days = {'2026-10-14': {'ok': 2, 'failed': 1, 'retries': 3}}
        summary = fetch_log.summary(self.log_with(fetches, days), NOW)
        self.assertEqual(summary['failures'], 1)
        self.assertEqual(summary['retries'], 3)

    def test_empty_log_has_no_last_fetch(self):
        summary = fetch_log.summary(fetch_log.empty_log(), NOW)
        self.assertIsNone(summary['last_fetch_at'])
        self.assertEqual(summary['days_covered'], 0)
        self.assertFalse(summary['ready_for_76'])


class DailySummaryDedupeTest(unittest.TestCase):
    def test_summary_is_due_once_per_local_day(self):
        log = fetch_log.empty_log()
        self.assertTrue(fetch_log.needs_summary(log, '2026-10-12'))
        log = fetch_log.mark_summary_sent(log, '2026-10-12')
        self.assertFalse(fetch_log.needs_summary(log, '2026-10-12'))
        self.assertTrue(fetch_log.needs_summary(log, '2026-10-13'))


class FormatSummaryTest(unittest.TestCase):
    def test_text_contains_every_required_field(self):
        summary = {
            'generated_at': iso(15, 12, 0),
            'days_covered': 3,
            'total_fetches': 288,
            'interval_count': 288,
            'interval_p50': 900.0,
            'interval_p90': 960.0,
            'last_fetch_at': iso(15, 11, 55),
            'failures': 1,
            'retries': 2,
            'ready_for_76': True,
        }
        text = fetch_log.format_summary(summary)
        self.assertIn('3', text)
        self.assertIn('288', text)
        self.assertIn('15.0 分钟', text)
        self.assertIn(iso(15, 11, 55), text)
        self.assertIn('可以开工 #76', text)

    def test_not_ready_text_says_so_explicitly(self):
        summary = {
            'generated_at': iso(15, 12, 0),
            'days_covered': 1,
            'total_fetches': 4,
            'interval_count': 3,
            'interval_p50': 900.0,
            'interval_p90': 900.0,
            'last_fetch_at': iso(15, 0, 45),
            'failures': 0,
            'retries': 0,
            'ready_for_76': False,
        }
        text = fetch_log.format_summary(summary)
        self.assertIn('尚未达到 #76', text)
        self.assertNotIn('可以开工 #76', text)

    def test_missing_intervals_are_reported_as_unavailable(self):
        summary = {
            'generated_at': iso(15, 12, 0),
            'days_covered': 1,
            'total_fetches': 1,
            'interval_count': 0,
            'interval_p50': None,
            'interval_p90': None,
            'last_fetch_at': iso(15, 0, 0),
            'failures': 0,
            'retries': 0,
            'ready_for_76': False,
        }
        text = fetch_log.format_summary(summary)
        self.assertIn('样本不足', text)


class CliTest(TempDataDirTest):
    def run_cli(self, argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = fetch_log.main(argv + ['--data-dir', str(self.data_dir)])
        return code, buf.getvalue()

    def read_log(self):
        return json.loads((self.data_dir / fetch_log.LOG_FILE).read_text(encoding='utf-8'))

    def test_append_then_summary_round_trip(self):
        code, _ = self.run_cli(['append', '--at', iso(12, 0, 0)])
        self.assertEqual(code, 0)
        code, _ = self.run_cli(['append', '--at', iso(12, 0, 15), '--retries', '2'])
        self.assertEqual(code, 0)
        self.assertEqual(self.read_log()['fetches'], [iso(12, 0, 0), iso(12, 0, 15)])
        self.assertEqual(self.read_log()['days']['2026-10-12']['retries'], 2)

        code, out = self.run_cli(['summary', '--now', iso(12, 1, 0), '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['interval_p50'], 900.0)

    def test_failure_flag_records_a_failure_without_a_timestamp(self):
        self.run_cli(['append', '--at', iso(12, 0, 0), '--fail'])
        log = self.read_log()
        self.assertEqual(log['fetches'], [])
        self.assertEqual(log['days']['2026-10-12']['failed'], 1)

    def test_needs_summary_toggles_after_mark(self):
        day = '2026-10-12'
        code, out = self.run_cli(['needs-summary', '--day', day])
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), 'true')
        self.run_cli(['mark-summary', '--day', day])
        _, out = self.run_cli(['needs-summary', '--day', day])
        self.assertEqual(out.strip(), 'false')

    def test_summary_text_is_printable(self):
        self.run_cli(['append', '--at', iso(12, 0, 0)])
        code, out = self.run_cli(['summary', '--now', iso(12, 1, 0)])
        self.assertEqual(code, 0)
        self.assertIn('本机探针', out)


if __name__ == '__main__':
    unittest.main()

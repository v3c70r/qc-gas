#!/usr/bin/env python3
"""Unit tests for the data/ volume guard (`check_data_size.py`).

The guard has two jobs since issue #68: keep the whole committed `data/`
directory under budget, and keep the new station-level change store
(`data/history/station-changes.json`) from growing silently.
"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import check_data_size  # noqa: E402


class ScanTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_scan_reports_total_file_count_and_largest_file(self):
        (self.data_dir / 'a.json').write_bytes(b'x' * 100)
        hist = self.data_dir / 'history'
        hist.mkdir()
        (hist / 'station-changes.json').write_bytes(b'y' * 300)
        total, files = check_data_size.scan(self.data_dir)
        self.assertEqual(total, 400)
        self.assertEqual(dict(files)['history/station-changes.json'], 300)
        self.assertEqual(dict(files)['a.json'], 100)

    def test_scan_of_missing_directory_is_empty(self):
        total, files = check_data_size.scan(self.data_dir / 'nope')
        self.assertEqual(total, 0)
        self.assertEqual(files, [])


class BudgetTest(unittest.TestCase):
    def test_healthy_sizes_have_no_errors(self):
        self.assertEqual(check_data_size.budget_errors(12.0, 8.0, 0.2), [])

    def test_total_over_budget_is_an_error(self):
        errors = check_data_size.budget_errors(check_data_size.MAX_DATA_MB + 1, 8.0, 0.2)
        self.assertTrue(any('total' in e for e in errors))

    def test_station_changes_store_over_its_own_budget_is_an_error(self):
        errors = check_data_size.budget_errors(
            12.0, 8.0, check_data_size.MAX_STATION_CHANGES_MB + 1)
        self.assertTrue(any('station-changes' in e for e in errors))

    def test_station_changes_budget_is_far_above_the_measured_steady_state(self):
        # ~2.5k stations * ~90 B/entry ~= 0.22 MB; the guard must not trip on it.
        self.assertGreater(check_data_size.MAX_STATION_CHANGES_MB, 1)
        self.assertEqual(check_data_size.budget_errors(12.0, 8.0, 0.25), [])

    def test_metrics_file_over_its_own_budget_is_an_error(self):
        errors = check_data_size.budget_errors(
            12.0, 8.0, 0.2, metrics_kb=check_data_size.MAX_METRICS_KB + 1)
        self.assertTrue(any('metrics' in e for e in errors))

    def test_metrics_budget_allows_the_measured_file(self):
        # The rolling latency ledger keeps metrics.json a few KB.
        self.assertGreaterEqual(check_data_size.MAX_METRICS_KB, 50)
        self.assertEqual(check_data_size.budget_errors(12.0, 8.0, 0.2, metrics_kb=5), [])


if __name__ == '__main__':
    unittest.main()

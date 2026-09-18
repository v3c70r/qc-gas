#!/usr/bin/env python3
"""Regression tests for the station-level history store in append_history.py."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from append_history import update_station_history  # noqa: E402


class StationHistoryAlignmentTest(unittest.TestCase):
    def test_insert_out_of_order_day_reindexes_existing_arrays(self):
        store = {
            'v': 1,
            't': ['2026-09-16', '2026-09-18'],
            's': {
                'a,b': {'g': [179.9, 180.1]},
            },
        }
        out = update_station_history(
            store,
            {'a,b': {'g': 175.0}},
            '2026-09-17',
            '2026-08-01',
        )
        self.assertEqual(out['t'], ['2026-09-16', '2026-09-17', '2026-09-18'])
        self.assertEqual(out['s']['a,b']['g'], [179.9, 175.0, 180.1])

    def test_newest_day_append_still_works(self):
        store = {
            'v': 1,
            't': ['2026-09-16'],
            's': {'a,b': {'g': [179.9]}},
        }
        out = update_station_history(
            store,
            {'a,b': {'g': 180.4}},
            '2026-09-17',
            '2026-08-01',
        )
        self.assertEqual(out['t'], ['2026-09-16', '2026-09-17'])
        self.assertEqual(out['s']['a,b']['g'], [179.9, 180.4])

    def test_prunes_days_older_than_cutoff(self):
        store = {
            'v': 1,
            't': ['2026-08-01', '2026-09-18'],
            's': {'a,b': {'g': [170.0, 180.0]}},
        }
        out = update_station_history(
            store,
            {},
            '2026-09-19',
            '2026-09-01',
        )
        self.assertEqual(out['t'], ['2026-09-18', '2026-09-19'])
        self.assertEqual(out['s']['a,b']['g'], [180.0, None])


if __name__ == '__main__':
    unittest.main()

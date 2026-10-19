#!/usr/bin/env python3
"""Regression tests for the station-level history store in append_history.py."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import append_history  # noqa: E402
from append_history import (  # noqa: E402
    load_station_shards,
    merge_month_shard,
    merge_station_shards,
    save_station_shards,
    split_station_store,
    update_station_history,
    update_station_shards,
)


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


class StationHistoryShardingTest(unittest.TestCase):
    """Issue #114: the 1.36 MB single file became month shards.

    Only the shard of the *current* month may be rewritten, and only when its
    content actually changes — rewriting the whole table on every fetch was the
    last remaining volume lever (4.2 MiB/day of pack growth).
    """

    def test_new_day_lands_in_its_own_month_shard(self):
        shards = {
            '2026-09': {'v': 1, 'm': '2026-09', 't': ['2026-09-30'],
                        's': {'a,b': {'g': [179.9]}}},
        }
        out, changed = update_station_shards(
            shards, {'a,b': {'g': 180.4}}, '2026-10-01', '2026-08-01')
        self.assertEqual(sorted(out), ['2026-09', '2026-10'])
        self.assertEqual(out['2026-10']['t'], ['2026-10-01'])
        self.assertEqual(out['2026-10']['s']['a,b']['g'], [180.4])
        self.assertEqual(out['2026-10']['m'], '2026-10')
        # The previous month is untouched and must not be rewritten.
        self.assertEqual(out['2026-09']['s']['a,b']['g'], [179.9])
        self.assertEqual(changed, ['2026-10'])

    def test_recorded_day_is_final_so_later_fetches_do_not_rewrite(self):
        # Same day, different prices (a re-price inside the day): the daily tier
        # is a once-per-day snapshot, so the shard must stay untouched. Without
        # this the 65 daily fetches would rewrite the month 65 times.
        shards = {
            '2026-10': {'v': 1, 'm': '2026-10', 't': ['2026-10-19'],
                        's': {'a,b': {'g': [180.4]}}},
        }
        out, changed = update_station_shards(
            shards, {'a,b': {'g': 175.9}}, '2026-10-19', '2026-05-01')
        self.assertEqual(changed, [])
        self.assertEqual(out['2026-10']['s']['a,b']['g'], [180.4])

    def test_already_recorded_day_rewrites_nothing(self):
        shards = {
            '2026-10': {'v': 1, 'm': '2026-10', 't': ['2026-10-19'],
                        's': {'a,b': {'g': [180.4]}}},
        }
        out, changed = update_station_shards(
            shards, {'a,b': {'g': 180.4}}, '2026-10-19', '2026-05-01')
        self.assertEqual(changed, [])
        self.assertEqual(out['2026-10']['s']['a,b']['g'], [180.4])

    def test_expired_shard_is_pruned_and_reported_for_deletion(self):
        shards = {
            '2026-08': {'v': 1, 'm': '2026-08', 't': ['2026-08-01'],
                        's': {'a,b': {'g': [170.0]}}},
            '2026-09': {'v': 1, 'm': '2026-09', 't': ['2026-09-18'],
                        's': {'a,b': {'g': [180.0]}}},
        }
        out, changed = update_station_shards(
            shards, {}, '2026-09-19', '2026-09-01')
        self.assertEqual(sorted(out), ['2026-09'])
        # The caller needs the month name to delete the stale shard file.
        self.assertIn('2026-08', changed)
        self.assertEqual(out['2026-09']['t'], ['2026-09-18', '2026-09-19'])

    def test_days_before_the_cutoff_are_dropped_inside_a_retained_shard(self):
        shards = {
            '2026-09': {'v': 1, 'm': '2026-09', 't': ['2026-09-01', '2026-09-18'],
                        's': {'a,b': {'g': [170.0, 180.0], 's': [None, 240.0]}}},
        }
        out, changed = update_station_shards(
            shards, {}, '2026-09-19', '2026-09-10')
        self.assertEqual(out['2026-09']['t'], ['2026-09-18', '2026-09-19'])
        self.assertEqual(out['2026-09']['s']['a,b']['g'], [180.0, None])
        self.assertEqual(out['2026-09']['s']['a,b']['s'], [240.0, None])
        self.assertIn('2026-09', changed)

    def test_legacy_single_store_splits_into_month_shards(self):
        store = {
            'v': 1,
            't': ['2026-09-30', '2026-10-01'],
            's': {'a,b': {'g': [179.9, 180.4], 's': [None, 239.9]}},
        }
        shards = split_station_store(store)
        self.assertEqual(sorted(shards), ['2026-09', '2026-10'])
        self.assertEqual(shards['2026-09'], {
            'v': 1, 'm': '2026-09', 't': ['2026-09-30'],
            's': {'a,b': {'g': [179.9]}},
        })
        self.assertEqual(shards['2026-10'], {
            'v': 1, 'm': '2026-10', 't': ['2026-10-01'],
            's': {'a,b': {'g': [180.4], 's': [239.9]}},
        })

    def test_split_and_merge_round_trips_to_the_single_store_shape(self):
        store = {
            'v': 1,
            't': ['2026-09-30', '2026-10-01'],
            's': {'a,b': {'g': [179.9, 180.4], 's': [None, 239.9]}},
        }
        merged = merge_station_shards(split_station_store(store))
        self.assertEqual(merged['v'], 1)
        self.assertEqual(merged['t'], ['2026-09-30', '2026-10-01'])
        self.assertEqual(merged['s']['a,b']['g'], [179.9, 180.4])
        self.assertEqual(merged['s']['a,b']['s'], [None, 239.9])

    def test_merge_of_empty_shards_is_an_empty_store(self):
        merged = merge_station_shards({})
        self.assertEqual(merged['t'], [])
        self.assertEqual(merged['s'], {})

    def test_merging_months_unions_days_and_keeps_the_primary_values(self):
        primary = {'v': 1, 'm': '2026-10', 't': ['2026-10-01'],
                   's': {'a,b': {'g': [181.0]}}}
        secondary = {'v': 1, 'm': '2026-10', 't': ['2026-10-01', '2026-10-02'],
                     's': {'a,b': {'g': [179.0, 182.0]}, 'c,d': {'g': [170.0, 171.0]}}}
        merged = merge_month_shard(primary, secondary)
        self.assertEqual(merged['t'], ['2026-10-01', '2026-10-02'])
        self.assertEqual(merged['s']['a,b']['g'], [181.0, 182.0])
        self.assertEqual(merged['s']['c,d']['g'], [170.0, 171.0])
        self.assertEqual(merged['m'], '2026-10')


class StationHistoryMigrationTest(unittest.TestCase):
    """Issue #114 review round 1: the legacy file must never be dropped early.

    ``data/history/**`` is *the only copy that exists*, so a migration that
    deletes `data/history/station-history.json` before every month it carried
    has been written to the shard directory loses history permanently.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name) / 'data' / 'history'
        self.dir = root / 'station-history'
        self.legacy = root / 'station-history.json'
        self.dir.mkdir(parents=True)
        self._saved = (append_history.STATION_HISTORY_DIR,
                       append_history.STATION_HISTORY_FILE)
        append_history.STATION_HISTORY_DIR = self.dir
        append_history.STATION_HISTORY_FILE = self.legacy
        self.addCleanup(self._restore)

    def _restore(self):
        append_history.STATION_HISTORY_DIR, append_history.STATION_HISTORY_FILE = self._saved
        self._tmp.cleanup()

    def _write(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(obj), encoding='utf-8')

    def _read(self, path):
        return json.loads(path.read_text(encoding='utf-8'))

    def test_months_only_in_the_legacy_file_are_written_before_it_is_deleted(self):
        # Shard dir already has October, the legacy file still carries September.
        self._write(self.dir / '2026-10.json', {
            'v': 1, 'm': '2026-10', 't': ['2026-10-19'],
            's': {'a,b': {'g': [180.4]}},
        })
        self._write(self.legacy, {
            'v': 1, 't': ['2026-09-15', '2026-09-16'],
            's': {'a,b': {'g': [179.0, 179.5]}},
        })

        shards = load_station_shards()
        # Today is already recorded -> nothing "changed" in the update step.
        shards, changed = update_station_shards(
            shards, {'a,b': {'g': 180.4}}, '2026-10-19', '2026-05-01')
        self.assertEqual(changed, [])
        save_station_shards(shards, changed)

        self.assertFalse(self.legacy.exists(), 'legacy file should be gone after migration')
        september = self._read(self.dir / '2026-09.json')
        self.assertEqual(september['t'], ['2026-09-15', '2026-09-16'])
        self.assertEqual(september['s']['a,b']['g'], [179.0, 179.5])

        # A reload must still see both months (nothing lost on disk).
        reloaded = load_station_shards()
        self.assertEqual(sorted(reloaded), ['2026-09', '2026-10'])
        self.assertEqual(merge_station_shards(reloaded)['t'],
                         ['2026-09-15', '2026-09-16', '2026-10-19'])

    def test_overlapping_legacy_month_is_merged_with_the_disk_shard(self):
        self._write(self.dir / '2026-10.json', {
            'v': 1, 'm': '2026-10', 't': ['2026-10-01'],
            's': {'a,b': {'g': [181.0]}},
        })
        self._write(self.legacy, {
            'v': 1, 't': ['2026-10-01', '2026-10-02'],
            's': {'a,b': {'g': [179.0, 182.0]}},
        })

        shards = load_station_shards()
        save_station_shards(shards, [])
        self.assertFalse(self.legacy.exists())

        october = self._read(self.dir / '2026-10.json')
        self.assertEqual(october['t'], ['2026-10-01', '2026-10-02'])
        # The shard on disk is the source of truth for a day it already has.
        self.assertEqual(october['s']['a,b']['g'], [181.0, 182.0])


if __name__ == '__main__':
    unittest.main()

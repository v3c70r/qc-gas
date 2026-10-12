#!/usr/bin/env python3
"""End-to-end tests for the adaptive fetch CLIs.

`update_profile.py` folds a real snapshot into the learned change-time profile
and rewrites the daily poll schedule; `should_fetch.py` is the workflow gate
that decides whether a given run is allowed to hit upstream.
"""

import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import should_fetch  # noqa: E402
import update_profile  # noqa: E402

UTC = timezone.utc


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def feature(region, regular, coords=(-73.5, 45.5), **fuels):
    props = {'region': region, 'name': region, 'regular_price': regular}
    for fuel, value in fuels.items():
        props[fuel.replace('_price', '') + '_price'] = value
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': list(coords)},
        'properties': props,
    }


def stations(*features):
    return {'type': 'FeatureCollection', 'features': list(features),
            'metadata': {'generated_at': '2026-09-25T11:00:00Z', 'total_stations': len(features)}}


class TempDataDirTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def stations_file(self, *features):
        write_json(self.data_dir / 'stations.json', stations(*features))


class UpdateProfileCliTest(TempDataDirTest):
    def test_creates_profile_and_bounded_schedule_from_a_real_snapshot(self):
        self.stations_file(feature('Montréal', 170.0), feature('Estrie', 180.0))
        rc = update_profile.main([
            '--data-dir', str(self.data_dir),
            '--now', '2026-09-25T11:00:00Z',
        ])
        self.assertEqual(rc, 0)

        profile = read_json(self.data_dir / 'update-profile.json')
        self.assertEqual(profile['hours']['11']['samples'], 1)
        self.assertEqual(profile['last_observed']['Montréal']['regular'], 170.0)

        schedule = read_json(self.data_dir / 'fetch-schedule.json')
        self.assertEqual(len(schedule['slots']), schedule['budget_per_day'])
        self.assertEqual(schedule['budget_per_day'], update_profile.DEFAULT_BUDGET)
        self.assertEqual(schedule['slots'], sorted(schedule['slots']))
        self.assertEqual(len(schedule['slots']), len(set(schedule['slots'])))
        self.assertEqual(schedule['allocated_per_day'], len(schedule['slots']))

    def test_second_observation_records_the_change_on_the_fetch_hour(self):
        self.stations_file(feature('Montréal', 170.0), feature('Estrie', 180.0))
        update_profile.main(['--data-dir', str(self.data_dir), '--now', '2026-09-25T11:00:00Z'])

        self.stations_file(feature('Montréal', 174.0), feature('Estrie', 180.0))
        update_profile.main(['--data-dir', str(self.data_dir), '--now', '2026-09-25T18:00:00Z'])

        profile = read_json(self.data_dir / 'update-profile.json')
        self.assertEqual(profile['hours']['11']['samples'], 1)
        self.assertEqual(profile['hours']['18']['samples'], 1)
        self.assertEqual(profile['hours']['18']['change_events'], 1)
        self.assertEqual(profile['hours']['18']['changed_stations'], 1)
        self.assertEqual(profile['hours']['18']['observed_hours'], 2.0)  # gap capped
        self.assertEqual(profile['last_observed']['Montréal']['regular'], 174.0)

    def test_unchanged_snapshot_records_no_change(self):
        self.stations_file(feature('Montréal', 170.0))
        update_profile.main(['--data-dir', str(self.data_dir), '--now', '2026-09-25T11:00:00Z'])
        update_profile.main(['--data-dir', str(self.data_dir), '--now', '2026-09-25T12:00:00Z'])
        profile = read_json(self.data_dir / 'update-profile.json')
        self.assertEqual(profile['hours']['12']['samples'], 1)
        self.assertEqual(profile['hours']['12']['change_events'], 0)

    def test_budget_flag_controls_the_daily_request_count(self):
        self.stations_file(feature('Montréal', 170.0))
        update_profile.main(['--data-dir', str(self.data_dir),
                             '--now', '2026-09-25T11:00:00Z', '--budget', '12'])
        schedule = read_json(self.data_dir / 'fetch-schedule.json')
        self.assertEqual(schedule['budget_per_day'], 12)
        self.assertEqual(len(schedule['slots']), 12)

    def test_missing_snapshot_skips_writes(self):
        rc = update_profile.main(['--data-dir', str(self.data_dir),
                                  '--now', '2026-09-25T11:00:00Z'])
        self.assertEqual(rc, 0)
        self.assertFalse((self.data_dir / 'update-profile.json').exists())
        self.assertFalse((self.data_dir / 'fetch-schedule.json').exists())


class StationChangesStoreTest(TempDataDirTest):
    """Per-station change detection (issue #68) folded into update_profile."""

    def store(self):
        return read_json(self.data_dir / 'history' / 'station-changes.json')

    def run_profile(self, now):
        return update_profile.main(['--data-dir', str(self.data_dir), '--now', now])

    def test_first_run_records_seen_stations_without_a_change_time(self):
        self.stations_file(
            feature('Montréal', 170.0, coords=(-73.5, 45.5), super_price=190.0),
            feature('Montréal', 180.0, coords=(-73.6, 45.6)),
        )
        self.assertEqual(self.run_profile('2026-09-25T11:00:00Z'), 0)
        store = self.store()
        self.assertEqual(store['v'], 1)
        self.assertEqual(store['tiers']['max_age_days'], update_profile.STATION_CHANGES_DAYS)
        self.assertEqual(sorted(store['s']), ['-73.50000,45.50000', '-73.60000,45.60000'])
        entry = store['s']['-73.50000,45.50000']
        self.assertIsNone(entry['t'])                       # no baseline yet
        self.assertEqual(entry['u'], '2026-09-25T11:00:00Z')  # last_seen_at
        self.assertEqual(entry['p'], [170.0, 190.0, None])    # g, s, d
        self.assertEqual(store['last'],
                         {'at': '2026-09-25T11:00:00Z', 'observed': 2, 'changed': 0})

    def test_cancelling_moves_in_one_region_are_both_detected(self):
        self.stations_file(
            feature('Montréal', 170.0, coords=(-73.5, 45.5)),
            feature('Montréal', 180.0, coords=(-73.6, 45.6)),
        )
        self.run_profile('2026-09-25T11:00:00Z')
        # +5c on one station, -5c on the other: the region average is unchanged
        # (diff_regions sees nothing) but two stations really did re-price.
        self.stations_file(
            feature('Montréal', 175.0, coords=(-73.5, 45.5)),
            feature('Montréal', 175.0, coords=(-73.6, 45.6)),
        )
        self.run_profile('2026-09-25T11:15:00Z')

        store = self.store()
        self.assertEqual(store['last']['changed'], 2)
        self.assertEqual(
            update_profile.stations_changed_at(store, '2026-09-25T11:15:00Z'),
            ['-73.50000,45.50000', '-73.60000,45.60000'],
        )
        self.assertEqual(store['s']['-73.50000,45.50000']['t'], '2026-09-25T11:15:00Z')

        profile = read_json(self.data_dir / 'update-profile.json')
        self.assertEqual(profile['hours']['11']['changed_stations'], 0)   # region flat
        self.assertEqual(profile['hours']['11']['changed_stations_exact'], 2)
        self.assertEqual(profile['hours']['11']['stations_observed'], 2)
        self.assertEqual(profile['last_station_diff'],
                         {'at': '2026-09-25T11:15:00Z', 'observed': 2, 'changed': 2})

    def test_unchanged_station_advances_last_seen_but_not_last_changed(self):
        self.stations_file(feature('Estrie', 180.0, coords=(-71.9, 45.4)))
        self.run_profile('2026-09-25T11:00:00Z')
        self.stations_file(feature('Estrie', 181.0, coords=(-71.9, 45.4)))
        self.run_profile('2026-09-25T11:15:00Z')
        self.stations_file(feature('Estrie', 181.0, coords=(-71.9, 45.4)))
        self.run_profile('2026-09-25T11:30:00Z')

        entry = self.store()['s']['-71.90000,45.40000']
        self.assertEqual(entry['t'], '2026-09-25T11:15:00Z')  # last_changed_at
        self.assertEqual(entry['u'], '2026-09-25T11:30:00Z')  # last_seen_at

    def test_sub_epsilon_noise_is_not_a_change(self):
        self.stations_file(feature('Estrie', 180.0, coords=(-71.9, 45.4)))
        self.run_profile('2026-09-25T11:00:00Z')
        self.stations_file(feature('Estrie', 180.04, coords=(-71.9, 45.4)))
        self.run_profile('2026-09-25T11:15:00Z')
        self.assertIsNone(self.store()['s']['-71.90000,45.40000']['t'])
        self.assertEqual(self.store()['last']['changed'], 0)

    def test_stations_not_seen_for_the_retention_window_are_pruned(self):
        write_json(self.data_dir / 'history' / 'station-changes.json', {
            'v': 1,
            's': {
                '-70.00000,46.00000': {'t': '2026-01-01T00:00:00Z',
                                       'u': '2026-01-01T00:00:00Z', 'p': [170.0, None, None]},
                '-71.00000,46.10000': {'t': None,
                                       'u': '2026-09-20T00:00:00Z', 'p': [180.0, None, None]},
            },
        })
        self.stations_file(feature('Estrie', 180.0, coords=(-71.0, 46.1)))
        self.run_profile('2026-09-25T11:00:00Z')
        self.assertEqual(list(self.store()['s']), ['-71.00000,46.10000'])

    def test_missing_snapshot_does_not_touch_the_station_store(self):
        self.assertEqual(self.run_profile('2026-09-25T11:00:00Z'), 0)
        self.assertFalse((self.data_dir / 'history' / 'station-changes.json').exists())


class ShouldFetchCliTest(TempDataDirTest):
    def github_output(self):
        return self.data_dir / 'gh_output'

    def run_gate(self, now, event='schedule', extra=()):
        return should_fetch.main([
            '--data-dir', str(self.data_dir),
            '--now', now,
            '--event-name', event,
            '--github-output', str(self.github_output()),
            *extra,
        ])

    def test_bootstrap_without_state_is_due_and_records_the_fetch(self):
        rc = self.run_gate('2026-09-25T11:03:00Z')
        self.assertEqual(rc, 0)
        self.assertIn('due=true', self.github_output().read_text())
        state = read_json(self.data_dir / 'fetch-state.json')
        self.assertEqual(state['last_fetch_at'], '2026-09-25T11:03:00Z')
        self.assertEqual(state['fetches'], 1)

    def test_too_soon_after_the_previous_fetch_is_not_due(self):
        self.run_gate('2026-09-25T11:03:00Z')
        self.github_output().unlink()
        rc = self.run_gate('2026-09-25T11:06:00Z')
        self.assertEqual(rc, 0)
        self.assertIn('due=false', self.github_output().read_text())
        state = read_json(self.data_dir / 'fetch-state.json')
        self.assertEqual(state['last_fetch_at'], '2026-09-25T11:03:00Z')  # untouched
        self.assertEqual(state['fetches'], 1)

    def test_a_passed_slot_since_the_last_fetch_is_due(self):
        write_json(self.data_dir / 'fetch-schedule.json',
                   {'v': 1, 'budget_per_day': 4, 'slots': ['00:00', '11:00', '12:00', '18:00']})
        self.run_gate('2026-09-25T10:30:00Z')
        self.github_output().unlink()
        self.run_gate('2026-09-25T11:04:00Z')
        self.assertIn('due=true', self.github_output().read_text())

    def test_missing_schedule_falls_back_to_the_research_prior(self):
        rc = self.run_gate('2026-09-25T11:03:00Z')
        self.assertEqual(rc, 0)
        self.assertTrue((self.data_dir / 'fetch-state.json').exists())

    def test_non_schedule_events_always_fetch(self):
        write_json(self.data_dir / 'fetch-state.json',
                   {'v': 1, 'last_fetch_at': '2026-09-25T11:00:00Z', 'fetches': 5})
        self.run_gate('2026-09-25T11:01:00Z', event='push')
        self.assertIn('due=true', self.github_output().read_text())

    def test_dry_run_does_not_write_state(self):
        self.run_gate('2026-09-25T11:03:00Z', extra=('--dry-run',))
        self.assertFalse((self.data_dir / 'fetch-state.json').exists())
        self.assertIn('due=true', self.github_output().read_text())

    def test_gate_never_crashes_on_corrupt_state(self):
        (self.data_dir / 'fetch-state.json').write_text('{not json', encoding='utf-8')
        rc = self.run_gate('2026-09-25T11:03:00Z')
        self.assertEqual(rc, 0)
        self.assertIn('due=true', self.github_output().read_text())


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""End-to-end tests for the adaptive fetch CLIs.

`update_profile.py` folds a real snapshot into the learned change-time profile
and rewrites the daily poll schedule; `should_fetch.py` is the workflow gate
that decides whether a given run is allowed to hit upstream.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schedule_plan  # noqa: E402
import should_fetch  # noqa: E402
import update_profile  # noqa: E402
from fetch_strategy import DEFAULT_DAILY_BUDGET, LOCAL_TZ  # noqa: E402

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
        self.assertEqual(schedule['budget_per_day'], update_profile.DEFAULT_DAILY_BUDGET)
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

    def test_the_schedule_learns_the_budget_from_the_shared_env_var(self):
        """Issue #67: one env knob, not a constant burried in each script."""
        self.stations_file(feature('Montréal', 170.0))
        with mock.patch.dict(os.environ, {update_profile.BUDGET_ENV_VAR: '12'}):
            update_profile.main(['--data-dir', str(self.data_dir),
                                 '--now', '2026-09-25T11:00:00Z'])
        schedule = read_json(self.data_dir / 'fetch-schedule.json')
        self.assertEqual(schedule['budget_per_day'], 12)
        self.assertEqual(schedule['allocated_per_day'], 12)

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


class ExploreScheduleCliTest(TempDataDirTest):
    """Issue #69: the written schedule reserves an epsilon exploration share."""

    def seed_profile(self):
        hours = {str(h): {'samples': 5, 'observed_hours': 10.0, 'change_events': 0,
                          'changed_stations': 0} for h in range(24)}
        hours['3'] = {'samples': 0, 'observed_hours': 0.0, 'change_events': 0,
                      'changed_stations': 0}
        hours['8'] = {'samples': 0, 'observed_hours': 0.0, 'change_events': 0,
                      'changed_stations': 0}
        write_json(self.data_dir / 'update-profile.json', {'v': 1, 'hours': hours})

    def run_profile(self, *extra, now='2026-09-25T12:00:00Z'):
        self.stations_file(feature('Montréal', 170.0), feature('Estrie', 180.0))
        rc = update_profile.main(['--data-dir', str(self.data_dir), '--now', now, *extra])
        self.assertEqual(rc, 0)
        return read_json(self.data_dir / 'fetch-schedule.json')

    def test_schedule_records_the_exploration_share_and_its_slots(self):
        self.seed_profile()
        schedule = self.run_profile('--budget', '48', '--explore-frac', '0.05')
        self.assertEqual(schedule['explore_frac'], 0.05)
        self.assertEqual(schedule['explore_count'], len(schedule['explore_slots']))
        self.assertTrue(schedule['explore_slots'])
        # Identifiable, in the plan, and never a duplicate.
        self.assertTrue(set(schedule['explore_slots']).issubset(set(schedule['slots'])))
        self.assertEqual(len(schedule['slots']), len(set(schedule['slots'])))
        self.assertEqual(len(schedule['slots']), 48)

    def test_explore_slots_probe_the_least_observed_hours(self):
        self.seed_profile()
        schedule = self.run_profile('--budget', '48', '--explore-frac', '0.05')
        probed = {label[:2] for label in schedule['explore_slots']}
        self.assertEqual(probed, {'03', '08'})

    def test_zero_exploration_fraction_disables_it(self):
        self.seed_profile()
        schedule = self.run_profile('--budget', '48', '--explore-frac', '0')
        self.assertEqual(schedule['explore_frac'], 0)
        self.assertEqual(schedule['explore_slots'], [])
        self.assertEqual(schedule['explore_count'], 0)

    def test_explore_fraction_follows_the_shared_env_var(self):
        self.seed_profile()
        with mock.patch.dict(os.environ, {'FETCH_EXPLORE_FRAC': '0'}):
            schedule = self.run_profile('--budget', '48')
        self.assertEqual(schedule['explore_slots'], [])

    def test_exploration_never_grows_the_budget(self):
        self.seed_profile()
        schedule = self.run_profile('--budget', '48')
        self.assertEqual(len(schedule['slots']), 48)
        self.assertLessEqual(len(schedule['explore_slots']),
                             round(48 * schedule['explore_frac']))

    def test_a_probed_hour_starts_accumulating_samples(self):
        # Issue #69 acceptance: a fetch that lands on a probe slot folds into
        # the profile, so a previously never-sampled hour leaves observed_hours 0.
        self.seed_profile()
        schedule = self.run_profile('--budget', '48', '--explore-frac', '0.05')
        probe = schedule['explore_slots'][0]
        hour = probe[:2]
        self.assertEqual(
            read_json(self.data_dir / 'update-profile.json')['hours'][str(int(hour))]['samples'],
            0)
        self.run_profile('--budget', '48', '--explore-frac', '0.05',
                         now=f'2026-09-26T{probe}:00Z')
        rec = read_json(self.data_dir / 'update-profile.json')['hours'][str(int(hour))]
        self.assertEqual(rec['samples'], 1)
        self.assertGreater(rec['observed_hours'], 0)


class RegionLayeredScheduleTest(TempDataDirTest):
    """Issue #71: regional hourly weights + per-region poll coverage."""

    def run_profile(self, now, extra=()):
        return update_profile.main(['--data-dir', str(self.data_dir),
                                    '--now', now, *extra])

    def test_region_hourly_statistics_are_recorded_per_fetch(self):
        self.stations_file(feature('Montréal', 170.0, coords=(-73.5, 45.5)),
                           feature('Estrie', 180.0, coords=(-71.9, 45.4)))
        self.assertEqual(self.run_profile('2026-09-25T18:00:00Z'), 0)
        self.stations_file(feature('Montréal', 175.0, coords=(-73.5, 45.5)),
                           feature('Estrie', 180.0, coords=(-71.9, 45.4)))
        self.assertEqual(self.run_profile('2026-09-25T18:15:00Z'), 0)

        profile = read_json(self.data_dir / 'update-profile.json')
        regions = profile['regions']
        self.assertEqual(regions['Montréal']['stations'], 1)
        self.assertEqual(regions['Estrie']['stations'], 1)
        hour = regions['Montréal']['hours']['18']
        self.assertEqual(hour['samples'], 2)
        self.assertEqual(hour['changed_stations'], 1)  # one station re-priced
        self.assertEqual(hour['change_events'], 1)     # the region average moved
        self.assertEqual(regions['Estrie']['hours']['18']['changed_stations'], 0)
        # 18:15 -> the second quarter-hour mark of hour 18.
        self.assertEqual(profile['marks']['18']['1']['changed_stations'], 1)
        self.assertEqual(profile['hours']['18']['exact_samples'], 2)

    def regional_profile(self):
        """A learned profile whose regions peak at different UTC hours."""
        def hour(changed, observed, samples=16, events=16):
            return {'samples': samples, 'observed_hours': observed,
                    'change_events': events, 'changed_stations': changed}

        return {
            'v': 1,
            'hours': {},
            'regions': {
                'Montréal': {'stations': 678, 'hours': {
                    '4': hour(400.0, 4.0), '11': hour(20.0, 4.0, events=4)}},
                'Gaspésie-Îles-de-la-Madeleine': {'stations': 177, 'hours': {
                    '22': hour(90.0, 4.0), '11': hour(4.0, 4.0, events=2)}},
            },
        }

    def test_the_schedule_covers_every_regions_peak_even_on_a_small_budget(self):
        write_json(self.data_dir / 'update-profile.json', self.regional_profile())
        self.stations_file(feature('Montréal', 170.0))
        self.assertEqual(self.run_profile('2026-09-25T18:00:00Z',
                                          extra=('--budget', '12')), 0)

        schedule = read_json(self.data_dir / 'fetch-schedule.json')
        self.assertEqual(schedule['budget_per_day'], 12)
        self.assertEqual(len(schedule['slots']), 12)
        coverage = schedule['region_coverage']
        self.assertEqual(coverage['Montréal']['peaks'][0], 4)
        self.assertEqual(coverage['Gaspésie-Îles-de-la-Madeleine']['peaks'][-1], 22)
        covered_hours = {int(s[:2]) for s in schedule['slots']}
        for region, rec in coverage.items():
            self.assertTrue(rec['covered'], f'{region} peaks not covered')
            for h in rec['peaks']:
                self.assertIn(h, covered_hours)
        self.assertIn('04:00', schedule['slots'])
        self.assertIn('22:00', schedule['slots'])
        self.assertIn(4, schedule['coverage_hours'])

    def test_a_legacy_profile_without_regions_still_plans_a_schedule(self):
        write_json(self.data_dir / 'update-profile.json', {
            'v': 1,
            'hours': {'11': {'samples': 9, 'observed_hours': 9.0,
                             'change_events': 9, 'changed_stations': 9000.0}},
            'last_observed': None,
        })
        self.stations_file(feature('Montréal', 170.0))
        self.assertEqual(self.run_profile('2026-09-25T11:30:00Z',
                                          extra=('--budget', '24')), 0)

        schedule = read_json(self.data_dir / 'fetch-schedule.json')
        self.assertEqual(schedule['source'], 'learned')
        self.assertEqual(schedule['budget_per_day'], 24)
        self.assertEqual(schedule['allocated_per_day'], 24)
        self.assertEqual(len(schedule['hour_weights']), 24)
        self.assertIn('region_coverage', schedule)
        profile = read_json(self.data_dir / 'update-profile.json')
        self.assertEqual(profile['hours']['11']['samples'], 10)  # legacy fields kept
        self.assertEqual(sorted(profile['regions']), ['Montréal'])


class ShouldFetchCliTest(TempDataDirTest):
    def github_output(self):
        return self.data_dir / 'gh_output'

    def stale_state(self, last_fetch_at):
        write_json(self.data_dir / 'fetch-schedule.json',
                   {'v': 1, 'budget_per_day': 1, 'slots': ['00:00']})
        write_json(self.data_dir / 'fetch-state.json',
                   {'v': 1, 'last_fetch_at': last_fetch_at, 'fetches': 1})

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

    def test_gate_and_profile_share_one_budget_env_var(self):
        self.assertEqual(should_fetch.BUDGET_ENV_VAR, update_profile.BUDGET_ENV_VAR)
        self.assertEqual(should_fetch.BUDGET_ENV_VAR, 'FETCH_DAILY_BUDGET')
        self.assertEqual(should_fetch.MAX_AGE_ENV_VAR, 'FETCH_MAX_AGE_MINUTES')

    def test_env_budget_drives_the_fallback_plan(self):
        # No learned schedule on disk -> the research-prior plan is sized by the
        # same FETCH_DAILY_BUDGET the profile step writes with.
        with mock.patch.dict(os.environ, {should_fetch.BUDGET_ENV_VAR: '12'}):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                rc = should_fetch.main([
                    '--data-dir', str(self.data_dir),
                    '--now', '2026-09-25T11:03:00Z',
                    '--event-name', 'schedule',
                    '--github-output', str(self.github_output()),
                    '--dry-run',
                ])
        self.assertEqual(rc, 0)
        self.assertIn('12 slots/day', buf.getvalue())

    def test_default_freshness_net_is_two_hourly_wakes(self):
        self.stale_state('2026-09-25T11:00:00Z')
        self.run_gate('2026-09-25T13:00:00Z')  # exactly 120 min: still fresh
        self.assertIn('due=false', self.github_output().read_text())
        self.github_output().unlink()
        self.run_gate('2026-09-25T13:05:00Z')  # ... 5 min later it is stale
        self.assertIn('due=true', self.github_output().read_text())

    def test_env_max_age_override_tightens_the_net(self):
        self.stale_state('2026-09-25T11:00:00Z')
        with mock.patch.dict(os.environ, {should_fetch.MAX_AGE_ENV_VAR: '45'}):
            self.run_gate('2026-09-25T12:30:00Z')
        self.assertIn('due=true', self.github_output().read_text())

    def test_gate_never_crashes_on_corrupt_state(self):
        (self.data_dir / 'fetch-state.json').write_text('{not json', encoding='utf-8')
        rc = self.run_gate('2026-09-25T11:03:00Z')
        self.assertEqual(rc, 0)
        self.assertIn('due=true', self.github_output().read_text())

    def test_dry_run_flags_an_exploration_hit(self):
        write_json(self.data_dir / 'fetch-schedule.json', {
            'v': 1, 'budget_per_day': 4,
            'slots': ['11:00', '11:30'],
            'explore_frac': 0.5, 'explore_slots': ['11:00'],
        })
        write_json(self.data_dir / 'fetch-state.json',
                   {'v': 1, 'last_fetch_at': '2026-09-25T10:50:00Z', 'fetches': 1})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_gate('2026-09-25T11:04:00Z', extra=('--dry-run',))
        self.assertIn('[explore]', buf.getvalue())
        self.assertIn('due=true', self.github_output().read_text())

    def test_dry_run_does_not_flag_an_exploitation_hit(self):
        write_json(self.data_dir / 'fetch-schedule.json', {
            'v': 1, 'budget_per_day': 2,
            'slots': ['11:00', '11:30'],
            'explore_frac': 0.5, 'explore_slots': ['11:30'],
        })
        write_json(self.data_dir / 'fetch-state.json',
                   {'v': 1, 'last_fetch_at': '2026-09-25T10:50:00Z', 'fetches': 1})
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_gate('2026-09-25T11:04:00Z', extra=('--dry-run',))
        self.assertNotIn('[explore]', buf.getvalue())
        self.assertIn('due=true', self.github_output().read_text())


class TimetableScheduleTest(TempDataDirTest):
    """Issue #91: the written schedule is an explicit local timetable."""

    def run_profile(self, *extra, now='2026-10-16T18:27:25Z'):
        self.stations_file(feature('Montréal', 170.0), feature('Estrie', 180.0))
        rc = update_profile.main(['--data-dir', str(self.data_dir), '--now', now,
                                  *extra])
        self.assertEqual(rc, 0)
        return read_json(self.data_dir / 'fetch-schedule.json')

    def test_the_default_schedule_is_a_local_timetable(self):
        schedule = self.run_profile()
        self.assertEqual(schedule['v'], 2)
        self.assertEqual(schedule['timezone'], LOCAL_TZ)
        self.assertEqual(schedule['timetable'], schedule_plan.plan_timetable(
            budget=DEFAULT_DAILY_BUDGET)['timetable'])
        self.assertEqual(len(schedule['timetable']), DEFAULT_DAILY_BUDGET)
        self.assertEqual(schedule['budget_per_day'], DEFAULT_DAILY_BUDGET)
        self.assertEqual(schedule['allocated_per_day'], len(schedule['timetable']))
        self.assertTrue(schedule['constraints']['satisfied'])
        self.assertEqual(schedule['constraints']['violations'], [])
        self.assertEqual(schedule['plan_relearn_after_days'],
                         schedule_plan.RELEARN_AFTER_DAYS)
        self.assertIn('01:00', schedule['timetable'])   # the night floor
        self.assertIn('00:10', schedule['timetable'])   # the daily batch hour
        self.assertIn('06:20', schedule['timetable'])   # the 20-minute day

    def test_the_legacy_utc_slots_field_is_the_timetable_projection(self):
        schedule = self.run_profile()
        # 2026-10-16 is EDT (UTC-4): local 06:00 -> 10:00Z, local 00:00 -> 04:00Z.
        self.assertEqual(len(schedule['slots']), len(schedule['timetable']))
        self.assertEqual(schedule['slots'], sorted(schedule['slots']))
        self.assertIn('10:00', schedule['slots'])
        self.assertIn('04:00', schedule['slots'])

    def test_the_projection_keeps_the_local_plan_across_the_dst_switch(self):
        october = self.run_profile(now='2026-10-16T18:27:25Z')
        november = self.run_profile(now='2026-11-16T18:27:25Z')
        self.assertEqual(october['timetable'], november['timetable'])
        # EST is an hour behind EDT in UTC terms: the same local 06:00 slot
        # lands one hour later in UTC (10:00Z vs 11:00Z).
        self.assertIn('10:00', october['slots'])
        self.assertIn('11:00', november['slots'])

    def test_a_budget_below_the_gap_floor_records_the_shortfall(self):
        schedule = self.run_profile('--budget', '30')
        self.assertFalse(schedule['constraints']['satisfied'])
        self.assertEqual(schedule['constraints']['floor_per_day'],
                         DEFAULT_DAILY_BUDGET)
        self.assertGreater(schedule['constraints']['shortfall'], 0)
        # The legacy weighted plan stands so the #69/#71 guarantees survive.
        self.assertNotIn('timetable', schedule)
        self.assertEqual(len(schedule['slots']), 30)
        self.assertEqual(schedule['allocated_per_day'], 30)

    def test_extra_budget_follows_the_learned_exact_channel(self):
        """E_h comes from ``changed_stations_exact``, re-keyed onto local hours."""
        hours = {str(h): {'samples': 40, 'observed_hours': 40.0, 'exact_samples': 40,
                          'changed_stations_exact': 10.0, 'changed_stations': 1e6}
                 for h in range(24)}
        hours['16'] = {'samples': 40, 'observed_hours': 40.0, 'exact_samples': 40,
                       'changed_stations_exact': 5000.0, 'changed_stations': 1e6}
        write_json(self.data_dir / 'update-profile.json', {'v': 1, 'hours': hours})
        schedule = self.run_profile('--budget', '71')
        counts = schedule['hourly_counts']
        self.assertEqual(sum(int(v) for v in counts.values()), 71)
        # UTC 16:00 is local 12:00 in October (EDT): the extra slots land there.
        self.assertEqual(max(counts, key=lambda h: int(counts[h])), '12')
        self.assertGreater(int(counts['12']), 3)
        self.assertEqual(schedule['explore_count'], len(schedule['explore_slots']))

    def test_exploration_probes_the_least_observed_hours(self):
        hours = {str(h): {'samples': 5, 'observed_hours': 0.0,
                          'changed_stations_exact': 0.0, 'exact_samples': 0}
                 for h in range(24)}
        for h in range(24):
            hours[str(h)]['samples'] = 40
            hours[str(h)]['observed_hours'] = 40.0
        # Local 03:00 = UTC 07:00 in October: the only hour never sampled.
        hours['7']['observed_hours'] = 0.0
        hours['7']['samples'] = 0
        write_json(self.data_dir / 'update-profile.json', {'v': 1, 'hours': hours})
        schedule = self.run_profile()
        self.assertEqual(schedule['explore_slots'], ['03:00'])
        self.assertTrue(set(schedule['explore_slots']).issubset(
            set(schedule['timetable'])))


class TimetableGateTest(TempDataDirTest):
    """The gate reads the local timetable, and stays correct across DST."""

    def schedule(self, **extra):
        payload = {'v': 2, 'budget_per_day': 1, 'timetable': ['06:00'],
                   'timezone': LOCAL_TZ, 'slots': ['10:00'],
                   'explore_slots': [], 'explore_count': 0}
        payload.update(extra)
        write_json(self.data_dir / 'fetch-schedule.json', payload)

    def run_gate(self, now, extra=(), event='schedule'):
        return should_fetch.main([
            '--data-dir', str(self.data_dir), '--now', now,
            '--event-name', event,
            '--github-output', str(self.data_dir / 'gh_output'), *extra])

    def gate_output(self):
        return (self.data_dir / 'gh_output').read_text()

    def state(self, last_fetch_at):
        write_json(self.data_dir / 'fetch-state.json',
                   {'v': 1, 'last_fetch_at': last_fetch_at, 'fetches': 1})

    def test_load_slots_prefers_the_timetable(self):
        self.schedule()
        slots, source, explore, timetable, tz = should_fetch.load_slots(self.data_dir)
        self.assertEqual(source, 'timetable')
        self.assertEqual(timetable, ['06:00'])
        self.assertEqual(tz, LOCAL_TZ)
        self.assertEqual(slots, ['10:00'])

    def test_the_gate_fires_on_the_local_slot_after_the_switch(self):
        self.schedule()
        self.state('2026-11-16T10:30:00Z')
        # 11:03Z is 06:03 local (EST): the 06:00 local slot just passed.
        self.run_gate('2026-11-16T11:03:00Z')
        output = (self.data_dir / 'gh_output').read_text()
        self.assertIn('due=true', output)

    def test_the_gate_does_not_fire_before_the_local_slot(self):
        self.schedule()
        self.state('2026-11-16T09:30:00Z')
        self.run_gate('2026-11-16T10:03:00Z')
        output = (self.data_dir / 'gh_output').read_text()
        self.assertIn('due=false', output)

    def test_a_corrupt_timetable_falls_back_to_the_slots_field(self):
        self.schedule(timetable=['nonsense'])
        slots, source, explore, timetable, _tz = should_fetch.load_slots(self.data_dir)
        self.assertEqual(source, 'learned')
        self.assertEqual(slots, ['10:00'])
        self.assertEqual(timetable, [])

    def test_the_gate_still_reads_a_pre_91_schedule(self):
        write_json(self.data_dir / 'fetch-schedule.json',
                   {'v': 1, 'budget_per_day': 4,
                    'slots': ['00:00', '11:00', '12:00', '18:00']})
        slots, source, _explore, timetable, _tz = should_fetch.load_slots(self.data_dir)
        self.assertEqual(source, 'learned')
        self.assertEqual(timetable, [])
        self.assertIn('11:00', slots)


class DispatchGateTest(TempDataDirTest):
    """Issue #93: the local dispatch wakes the gate often, the timetable decides.

    A `repository_dispatch` (or `workflow_dispatch`) is an *external wake-up*:
    it must not be treated as "fetch unconditionally" any more — a 5-minute
    crontab wakes it 288 times a day, so the timetable has to be the thing that
    admits the real fetches. Not being part of the planned slots, a dispatch
    hit does not consume the plan budget (the reason string says so).
    """

    def schedule(self, **extra):
        payload = {'v': 2, 'budget_per_day': 1, 'timetable': ['06:00'],
                   'timezone': LOCAL_TZ, 'slots': ['10:00'],
                   'explore_slots': [], 'explore_count': 0}
        payload.update(extra)
        write_json(self.data_dir / 'fetch-schedule.json', payload)

    def state(self, last_fetch_at):
        write_json(self.data_dir / 'fetch-state.json',
                   {'v': 1, 'last_fetch_at': last_fetch_at, 'fetches': 1})

    def run_gate(self, now, extra=(), event='repository_dispatch'):
        return should_fetch.main([
            '--data-dir', str(self.data_dir), '--now', now,
            '--event-name', event,
            '--github-output', str(self.data_dir / 'gh_output'), *extra])

    def output(self):
        return (self.data_dir / 'gh_output').read_text()

    def test_a_dispatch_on_a_local_slot_is_due(self):
        self.schedule()
        self.state('2026-11-16T10:30:00Z')
        # 11:03Z is 06:03 local (EST): the 06:00 local slot just passed.
        self.run_gate('2026-11-16T11:03:00Z')
        self.assertIn('due=true', self.output())
        state = read_json(self.data_dir / 'fetch-state.json')
        self.assertEqual(state['event'], 'repository_dispatch')
        self.assertEqual(state['last_fetch_at'], '2026-11-16T11:03:00Z')

    def test_an_off_schedule_dispatch_is_not_due_and_changes_nothing(self):
        self.schedule()
        self.state('2026-11-16T09:30:00Z')
        self.run_gate('2026-11-16T10:03:00Z')       # 05:03 local: before the slot
        self.assertIn('due=false', self.output())
        state = read_json(self.data_dir / 'fetch-state.json')
        self.assertEqual(state['last_fetch_at'], '2026-11-16T09:30:00Z')
        self.assertEqual(state['fetches'], 1)

    def test_a_dispatch_right_after_a_fetch_respects_the_min_gap(self):
        self.schedule()
        self.state('2026-11-16T11:02:00Z')
        self.run_gate('2026-11-16T11:04:00Z')       # 2 min later
        self.assertIn('due=false', self.output())

    def test_workflow_dispatch_obeys_the_same_timetable(self):
        self.schedule()
        self.state('2026-11-16T10:30:00Z')
        self.run_gate('2026-11-16T11:03:00Z', event='workflow_dispatch')
        self.assertIn('due=true', self.output())

    def test_a_dispatch_hit_says_it_is_not_budget_counted(self):
        self.schedule()
        self.state('2026-11-16T10:30:00Z')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_gate('2026-11-16T11:03:00Z', extra=('--dry-run',))
        self.assertIn('not budget-counted', buf.getvalue())

    def test_push_events_still_fetch_unconditionally(self):
        self.schedule()
        self.state('2026-11-16T10:30:00Z')
        self.run_gate('2026-11-16T10:31:00Z', event='push')
        self.assertIn('due=true', self.output())

    def test_the_gate_reports_the_timetable_slot_count_as_the_budget(self):
        self.schedule(timetable=['06:00', '06:20'])
        self.state('2026-11-16T10:30:00Z')
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.run_gate('2026-11-16T10:31:00Z', extra=('--dry-run',))
        self.assertIn('2 local slots/day', buf.getvalue())


if __name__ == '__main__':
    unittest.main()

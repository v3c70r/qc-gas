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
    Path(path).write_text(json.dumps(obj), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def feature(region, regular):
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [-73.5, 45.5]},
        'properties': {'region': region, 'name': region, 'regular_price': regular},
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

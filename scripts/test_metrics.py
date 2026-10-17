#!/usr/bin/env python3
"""Tests for scripts/metrics.py — latency & freshness metrics (issue #70).

The metrics are pure functions over the committed stores (station change store,
stations snapshot, learned profile) plus a small rolling latency ledger kept in
``data/metrics.json``. These tests pin the math, the degradation rules and the
CLI/JSON contract the PM agent will read.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import metrics  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


class PercentileTest(unittest.TestCase):
    def test_median_interpolates_between_the_two_middle_values(self):
        self.assertAlmostEqual(metrics.percentile([1, 2, 3, 4], 50), 2.5)

    def test_p90_interpolates_on_a_larger_series(self):
        series = list(range(0, 100, 10))  # 0..90
        self.assertAlmostEqual(metrics.percentile(series, 90), 81.0)

    def test_single_value_is_returned_for_every_percentile(self):
        self.assertEqual(metrics.percentile([7], 95), 7.0)

    def test_empty_series_has_no_percentile(self):
        self.assertIsNone(metrics.percentile([], 50))


class LatencyTest(unittest.TestCase):
    def test_current_sample_is_observation_minus_source(self):
        observed = metrics.parse_iso('2026-10-12T11:01:12Z')
        source = metrics.parse_iso('2026-10-12T11:00:00Z')
        stats, samples = metrics.build_latency(observed, source, [])
        self.assertEqual(stats['count'], 1)
        self.assertEqual(stats['p50'], 72.0)
        self.assertEqual(stats['p95'], 72.0)
        self.assertEqual(samples[-1]['s'], 72.0)

    def test_rolling_ledger_keeps_previous_samples_and_appends_the_new_one(self):
        prev = [{'at': f'2026-10-12T0{i}:00:00Z', 's': i * 60} for i in (1, 2, 3)]
        stats, samples = metrics.build_latency(
            metrics.parse_iso('2026-10-12T04:00:00Z'),
            metrics.parse_iso('2026-10-12T03:59:30Z'),
            prev,
        )
        self.assertEqual([s['s'] for s in samples], [60.0, 120.0, 180.0, 30.0])
        self.assertEqual(stats['max'], 180.0)
        self.assertEqual(stats['count'], 4)

    def test_duplicate_observation_time_does_not_double_count(self):
        stamp = '2026-10-12T04:00:00Z'
        prev = [{'at': stamp, 's': 90.0}]
        _, samples = metrics.build_latency(
            metrics.parse_iso(stamp), metrics.parse_iso('2026-10-12T03:58:30Z'), prev)
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0]['s'], 90.0)

    def test_ledger_is_capped_to_the_newest_samples(self):
        prev = [{'at': f'2026-01-01T00:00:00Z', 's': float(i)} for i in range(metrics.MAX_LATENCY_SAMPLES + 50)]
        _, samples = metrics.build_latency(
            metrics.parse_iso('2026-10-12T04:00:00Z'),
            metrics.parse_iso('2026-10-12T03:59:00Z'),
            prev,
        )
        self.assertEqual(len(samples), metrics.MAX_LATENCY_SAMPLES)

    def test_negative_latency_is_ignored(self):
        stats, samples = metrics.build_latency(
            metrics.parse_iso('2026-10-12T03:00:00Z'),
            metrics.parse_iso('2026-10-12T04:00:00Z'),
            [],
        )
        self.assertIsNone(stats['p50'])
        self.assertEqual(stats['count'], 0)
        self.assertEqual(samples, [])

    def test_observation_at_picks_the_newest_of_the_available_stamps(self):
        store = {'generated_at': '2026-10-12T10:00:00Z',
                 'last': {'at': '2026-10-12T10:30:00Z'}}
        state = {'last_fetch_at': '2026-10-12T10:15:00Z'}
        self.assertEqual(metrics.observation_at(store, state),
                         metrics.parse_iso('2026-10-12T10:30:00Z'))

    def test_observation_at_is_none_without_any_stamp(self):
        self.assertIsNone(metrics.observation_at({}, {}))


class FreshnessTest(unittest.TestCase):
    def test_site_freshness_over_last_seen_at(self):
        store = {'s': {
            'a,b': {'u': '2026-10-12T11:50:00Z'},
            'c,d': {'u': '2026-10-12T11:00:00Z'},
        }}
        stats = metrics.site_freshness(store, NOW)
        self.assertEqual(stats['count'], 2)
        self.assertEqual(stats['p50'], 2100.0)
        self.assertEqual(stats['p90'], 3300.0)
        self.assertEqual(stats['max'], 3600.0)

    def test_site_freshness_empty_store(self):
        stats = metrics.site_freshness({}, NOW)
        self.assertEqual(stats['count'], 0)
        self.assertIsNone(stats['p50'])

    def test_site_freshness_ignores_clock_skew(self):
        store = {'s': {'a,b': {'u': '2026-10-12T12:00:30Z'}}}
        stats = metrics.site_freshness(store, NOW)
        self.assertEqual(stats['max'], 0.0)


class StaleSummaryTest(unittest.TestCase):
    def store(self):
        return {'s': {
            'k1': {'t': '2026-10-01T12:00:00Z', 'u': '2026-10-12T11:00:00Z'},
            'k2': {'t': '2026-09-01T12:00:00Z', 'u': '2026-10-12T11:00:00Z'},
            'k3': {'t': None, 'u': '2026-10-12T11:00:00Z'},
        }}

    def test_counts_shares_and_examples(self):
        out = metrics.stale_summary(self.store(), NOW)
        self.assertEqual(out['total'], 3)
        self.assertEqual(out['never_changed'], 1)
        self.assertEqual(out['over_7d'], 2)
        self.assertEqual(out['over_30d'], 1)
        self.assertAlmostEqual(out['over_7d_share'], 2 / 3, places=4)
        self.assertAlmostEqual(out['over_30d_share'], 1 / 3, places=4)
        # oldest first (the 41-day key precedes the 11-day one)
        self.assertEqual([e['key'] for e in out['examples']], ['k2', 'k1'])
        self.assertGreater(out['examples'][0]['days'], out['examples'][1]['days'])

    def test_empty_store_is_all_zero_and_share_zero(self):
        out = metrics.stale_summary({}, NOW)
        self.assertEqual(out['total'], 0)
        self.assertEqual(out['over_7d'], 0)
        self.assertEqual(out['over_7d_share'], 0.0)


class HourlyChangeTest(unittest.TestCase):
    def test_rate_is_changed_stations_over_observed_hours(self):
        profile = {'hours': {
            '11': {'changed_stations_exact': 30, 'observed_hours': 3.0},
            '12': {'changed_stations_exact': 0, 'observed_hours': 0.0},
        }}
        out = metrics.hourly_change_rate(profile)
        rows = {row['hour']: row for row in out['hours']}
        self.assertEqual(len(out['hours']), 24)
        self.assertEqual(rows[11]['changed'], 30)
        self.assertEqual(rows[11]['rate'], 10.0)
        self.assertIsNone(rows[12]['rate'])
        self.assertAlmostEqual(out['mean_rate'], 10.0)
        self.assertEqual(out['total_changed'], 30)

    def test_empty_profile_has_no_rate(self):
        out = metrics.hourly_change_rate({})
        self.assertIsNone(out['mean_rate'])
        self.assertEqual(len(out['hours']), 24)


class MetricsCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def seed(self):
        write_json(self.data_dir / 'stations.json', {
            'type': 'FeatureCollection',
            'features': [{'type': 'Feature', 'geometry': {'coordinates': [-73.5, 45.5]},
                          'properties': {'regular_price': 180.0}}],
            'metadata': {'generated_at': '2026-10-12T11:00:00Z', 'total_stations': 1},
        })
        write_json(self.data_dir / 'history' / 'station-changes.json', {
            'v': 1,
            'generated_at': '2026-10-12T11:01:12Z',
            'last': {'at': '2026-10-12T11:01:12Z', 'observed': 1, 'changed': 0},
            's': {'-73.50000,45.50000': {'t': '2026-10-01T00:00:00Z', 'u': '2026-10-12T11:01:12Z'}},
        })
        write_json(self.data_dir / 'fetch-state.json', {'v': 1, 'last_fetch_at': '2026-10-12T11:01:12Z'})
        write_json(self.data_dir / 'update-profile.json', {'v': 1, 'hours': {
            '11': {'changed_stations_exact': 12, 'observed_hours': 2.0},
        }})

    def run_cli(self, *extra):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = metrics.main(['--data-dir', str(self.data_dir),
                               '--now', '2026-10-12T12:00:00Z', *extra])
        return rc, buf.getvalue()

    def test_json_output_is_machine_readable_and_written_to_disk(self):
        self.seed()
        rc, out = self.run_cli('--json')
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertEqual(payload['v'], 1)
        self.assertEqual(payload['source_generated_at'], '2026-10-12T11:00:00Z')
        self.assertEqual(payload['latency_seconds']['p50'], 72.0)
        self.assertIn('freshness_seconds', payload)
        self.assertEqual(payload['stale']['over_7d'], 1)
        self.assertIn('hourly_changes', payload)

        on_disk = read_json(self.data_dir / 'metrics.json')
        self.assertEqual(on_disk['latency_seconds']['count'], 1)
        self.assertEqual(on_disk['latency_seconds']['samples'][-1]['s'], 72.0)

    def test_human_summary_mentions_the_key_numbers(self):
        self.seed()
        rc, out = self.run_cli()
        self.assertEqual(rc, 0)
        self.assertIn('Latency', out)
        self.assertIn('Freshness', out)
        self.assertIn('7d', out)

    def test_ledger_accumulates_across_runs(self):
        self.seed()
        write_json(self.data_dir / 'metrics.json',
                   {'latency_seconds': {'samples': [{'at': '2026-10-12T10:00:00Z', 's': 60.0}]}})
        self.run_cli('--json')
        on_disk = read_json(self.data_dir / 'metrics.json')
        self.assertEqual([s['s'] for s in on_disk['latency_seconds']['samples']], [60.0, 72.0])

    def test_latency_ledger_survives_in_its_own_persistent_file(self):
        # Issue #103 stopped committing data/metrics.json (pure derived snapshot),
        # so the rolling ledger needs a file of its own or every run would
        # report a single-sample latency again.
        self.seed()
        write_json(self.data_dir / 'metrics-ledger.json',
                   {'v': 1, 'samples': [{'at': '2026-10-12T10:00:00Z', 's': 60.0}]})
        self.run_cli('--json')
        on_disk = read_json(self.data_dir / 'metrics-ledger.json')
        self.assertEqual([s['s'] for s in on_disk['samples']], [60.0, 72.0])
        payload = read_json(self.data_dir / 'metrics.json')
        self.assertEqual(payload['latency_seconds']['count'], 2)
        self.assertEqual([s['s'] for s in payload['latency_seconds']['samples']], [60.0, 72.0])

    def test_ledger_file_does_not_grow_past_the_sample_cap(self):
        self.seed()
        samples = [{'at': f'2026-10-12T{h:02d}:00:00Z', 's': 10.0 + h}
                   for h in range(metrics.MAX_LATENCY_SAMPLES + 10)]
        write_json(self.data_dir / 'metrics-ledger.json', {'v': 1, 'samples': samples})
        self.run_cli('--json')
        on_disk = read_json(self.data_dir / 'metrics-ledger.json')
        self.assertEqual(len(on_disk['samples']), metrics.MAX_LATENCY_SAMPLES)

    def test_missing_inputs_degrade_without_crashing(self):
        rc, out = self.run_cli('--json')
        self.assertEqual(rc, 0)
        payload = json.loads(out)
        self.assertIsNone(payload['source_generated_at'])
        self.assertEqual(payload['latency_seconds']['count'], 0)
        self.assertEqual(payload['stale']['total'], 0)


if __name__ == '__main__':
    unittest.main()

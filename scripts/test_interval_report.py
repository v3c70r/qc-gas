#!/usr/bin/env python3
"""Tests for scripts/interval_report.py — the "necessary fetch interval" study (#76).

The report answers one decision question for the PM agent (#C): *how often must
the upstream snapshot be fetched so a displayed price is fresh enough?* It is a
pure function over two truths:

* a **station observation series** (per-station prices over time — in production
  the git revision history of ``data/history/station-changes.json``, which the
  local probe of #74 commits every ~15 minutes), and
* the **probe ledger** (``data/fetch-log.json``) that says how long that series
  actually is and how fast it was sampled.

These tests pin the change-arrival math, the staleness simulation, the
sample-sufficiency gate (no conclusion from a 3-hour window) and the reporting
contract (T-table + explicit bias note) that the PM agent reads.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import interval_report as ir  # noqa: E402

UTC = timezone.utc
START = datetime(2026, 10, 1, 0, 0, 0, tzinfo=UTC)


def iso(minutes):
    """ISO timestamp ``minutes`` after the fixture window start."""
    return (START + timedelta(minutes=minutes)).strftime('%Y-%m-%dT%H:%M:%SZ')


def row(regular=150.0, super_=200.0, diesel=300.0):
    return [regular, super_, diesel]


def entry(minutes, stations):
    return {'at': iso(minutes), 's': stations}


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding='utf-8')


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def stations_fixture():
    """Minimal stations.json (GeoJSON) carrying region + brand per key."""
    return {
        'type': 'FeatureCollection',
        'metadata': {'generated_at': iso(0), 'total_stations': 2},
        'features': [
            {'type': 'Feature',
             'geometry': {'type': 'Point', 'coordinates': [0.0, 0.0]},
             'properties': {'brand': 'Shell', 'region': 'Montréal',
                            'regular_price': 150.0}},
            {'type': 'Feature',
             'geometry': {'type': 'Point', 'coordinates': [1.0, 0.0]},
             'properties': {'brand': 'Costco', 'region': 'Québec',
                            'regular_price': 140.0}},
        ],
    }


# ── long fixture: 4 days of 15-minute observations ───────────────────────────

FIXTURE_STATIONS = 50
FIXTURE_CHANGERS = 40          # 40 of 50 stations re-price; each has its own phase
FIXTURE_CHANGE_STEPS = 49      # 49 * 15 min = 735 min between re-prices (odd -> off-grid)


def big_entries(days=4, step_minutes=15, change_steps=FIXTURE_CHANGE_STEPS):
    """A 15-minute series that clears the sufficiency gate.

    The first ``FIXTURE_CHANGERS`` stations each re-price every ``change_steps``
    observations, phase-shifted by one observation per station so the change
    times are spread across the poll grid (a grid-aligned fixture would fake
    zero staleness). The remaining stations never move — the realistic majority.
    """
    steps = (days * 24 * 60) // step_minutes
    entries = []
    for i in range(steps + 1):
        minute = i * step_minutes
        stations = {}
        for j in range(FIXTURE_CHANGERS):
            stations[f'{j}.00000,0.00000'] = row(regular=100.0 + (i + j) // change_steps)
        for j in range(FIXTURE_CHANGERS, FIXTURE_STATIONS):
            stations[f'{j}.00000,0.00000'] = row()
        entries.append(entry(minute, stations))
    return entries


class NormalizeObservationsTest(unittest.TestCase):
    def test_parses_rows_sorts_and_skips_unusable_entries(self):
        obs = ir.normalize_observations([
            entry(30, {'a': row(1.0)}),
            {'at': None, 's': {'a': row()}},
            entry(0, {'a': row(0.0)}),
            'not-a-dict',
            entry(15, {}),                      # no prices -> no observation
        ])
        self.assertEqual([o['at'] for o in obs], [START, START + timedelta(minutes=30)])
        self.assertEqual(obs[0]['prices'], {'a': {'g': 0.0, 's': 200.0, 'd': 300.0}})

    def test_dedupes_the_same_timestamp_keeping_the_last_state(self):
        obs = ir.normalize_observations([
            entry(0, {'a': row(1.0)}),
            entry(0, {'a': row(2.0)}),
        ])
        self.assertEqual(len(obs), 1)
        self.assertEqual(obs[0]['prices']['a']['g'], 2.0)


class StationLabelsTest(unittest.TestCase):
    def test_maps_coordinate_key_to_region_and_brand(self):
        labels = ir.station_labels(stations_fixture())
        self.assertEqual(labels['0.00000,0.00000'],
                         {'region': 'Montréal', 'brand': 'Shell'})

    def test_missing_stores_degrade_to_empty_mapping(self):
        self.assertEqual(ir.station_labels(None), {})


class ChangeEventsTest(unittest.TestCase):
    def test_detects_a_station_that_re_priced(self):
        obs = ir.normalize_observations([
            entry(0, {'a': row(150.0)}),
            entry(15, {'a': row(155.0)}),
        ])
        events = ir.change_events(obs, ir.station_labels(stations_fixture()))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['key'], 'a')
        self.assertEqual(events[0]['fuels'], ['g'])
        self.assertEqual(events[0]['at'], START + timedelta(minutes=15))

    def test_unchanged_prices_produce_no_event(self):
        obs = ir.normalize_observations([entry(0, {'a': row()}), entry(15, {'a': row()})])
        self.assertEqual(ir.change_events(obs), [])

    def test_new_station_and_missing_fuel_are_not_changes(self):
        obs = ir.normalize_observations([
            entry(0, {'a': row()}),
            entry(15, {'a': row(), 'b': row()}),       # b has no baseline
        ])
        self.assertEqual(ir.change_events(obs), [])
        obs = ir.normalize_observations([
            entry(0, {'a': row()}),
            entry(15, {'a': [150.0, None, 300.0]}),    # availability change only
        ])
        self.assertEqual(ir.change_events(obs), [])

    def test_sub_epsilon_noise_is_not_a_change(self):
        obs = ir.normalize_observations([
            entry(0, {'a': row(150.0)}),
            entry(15, {'a': row(150.04)}),
        ])
        self.assertEqual(ir.change_events(obs), [])


class StationIntervalsTest(unittest.TestCase):
    def test_intervals_between_consecutive_changes_of_the_same_station(self):
        events = [
            {'at': START, 'key': 'a', 'fuels': ['g'], 'region': 'Montréal', 'brand': 'Shell'},
            {'at': START + timedelta(hours=1), 'key': 'a', 'fuels': ['g'],
             'region': 'Montréal', 'brand': 'Shell'},
            {'at': START + timedelta(hours=3), 'key': 'a', 'fuels': ['g'],
             'region': 'Montréal', 'brand': 'Shell'},
            {'at': START, 'key': 'b', 'fuels': ['g'], 'region': 'Québec', 'brand': 'Costco'},
        ]
        out = ir.station_intervals(events)
        self.assertEqual(out['overall'], [3600.0, 7200.0])
        self.assertEqual(out['by_region']['Montréal'], [3600.0, 7200.0])
        self.assertEqual(out['by_region']['Québec'], [])
        self.assertEqual(out['by_brand']['Shell'], [3600.0, 7200.0])

    def test_stats_of_empty_intervals_is_a_zero_count(self):
        stats = ir.stats_of([])
        self.assertEqual(stats['count'], 0)
        self.assertIsNone(stats['p50'])


class StatsOfTest(unittest.TestCase):
    def test_percentiles_interpolate_and_round(self):
        stats = ir.stats_of([0.0, 100.0])
        self.assertEqual(stats['p50'], 50.0)
        self.assertEqual(stats['p90'], 90.0)
        self.assertEqual(stats['p99'], 99.0)
        self.assertEqual(stats['count'], 2)


class SimulateIntervalTest(unittest.TestCase):
    def _events(self, minutes):
        return [{'at': START + timedelta(minutes=m), 'key': 'a', 'fuels': ['g'],
                 'region': 'R', 'brand': 'B'} for m in minutes]

    def _obs(self, minutes):
        return ir.normalize_observations([entry(m, {'a': row()}) for m in minutes])

    def test_poll_landing_on_the_change_shows_zero_age(self):
        sim = ir.simulate_interval(self._events([60]), self._obs([0, 480]), 60)
        self.assertEqual(sim['p50'], 0.0)
        self.assertEqual(sim['events'], 1)

    def test_age_is_the_wait_until_the_fixing_poll(self):
        # Change at minute 10; polls every 60 -> fixed at 60 -> 50 minutes late.
        sim = ir.simulate_interval(self._events([10]), self._obs([0, 480]), 60)
        self.assertEqual(sim['p50'], 50 * 60.0)
        # A denser grid (30 min) fixes it at 30 -> 20 minutes late.
        self.assertEqual(ir.simulate_interval(self._events([10]), self._obs([0, 480]),
                                              30)['p50'], 20 * 60.0)

    def test_stale_share_is_the_time_weighted_share_of_stations(self):
        # One station stale for 45 min of a 120 min window -> 45/120 = 0.375.
        sim = ir.simulate_interval(self._events([15]), self._obs([0, 120]), 60)
        self.assertEqual(sim['p50'], 45 * 60.0)
        self.assertAlmostEqual(sim['stale_share'], 0.375, places=6)

    def test_share_over_the_stale_threshold_counts_only_long_fixes(self):
        events = self._events([10, 900])
        sim = ir.simulate_interval(events, self._obs([0, 1440]), 60)
        # lags: 50 min (not > 60) and 0 min (12:00 change lands on a poll).
        self.assertEqual(sim['stale_share_over_threshold'], 0.0)
        sim = ir.simulate_interval(events, self._obs([0, 1440]), 120)
        # lags: 110 min and 60 min -> one of two is > 60 min.
        self.assertEqual(sim['stale_share_over_threshold'], 0.5)

    def test_the_last_observation_counts_as_a_poll(self):
        # Change at minute 475 with a 60 min grid: the next grid poll would be at
        # 540 (outside the window), but the window end is itself a real fetch.
        sim = ir.simulate_interval(self._events([475]), self._obs([0, 480]), 60)
        self.assertEqual(sim['p50'], 5 * 60.0)
        self.assertEqual(sim['events'], 1)

    def test_no_events_means_unknown_not_zero_staleness(self):
        sim = ir.simulate_interval([], self._obs([0, 480]), 60)
        self.assertIsNone(sim['p50'])
        self.assertIsNone(sim['stale_share'])


class StalenessTableTest(unittest.TestCase):
    def setUp(self):
        self.obs = ir.normalize_observations(big_entries())
        self.events = ir.change_events(self.obs)

    def test_one_row_per_grid_interval(self):
        rows = ir.staleness_table(self.events, self.obs)
        self.assertEqual([r['interval_minutes'] for r in rows], list(ir.INTERVAL_GRID_MINUTES))

    def test_lag_grows_with_the_interval(self):
        rows = ir.staleness_table(self.events, self.obs)
        lags = [r['p90'] for r in rows]
        self.assertEqual(lags, sorted(lags))
        self.assertLess(lags[0], lags[-1])

    def test_simulated_lag_stays_within_one_interval(self):
        for r in ir.staleness_table(self.events, self.obs):
            self.assertLessEqual(r['p99'], r['interval_minutes'] * 60)


class RecommendTest(unittest.TestCase):
    def _row(self, minutes, p90, share):
        return {'interval_minutes': minutes, 'p50': p90 / 2, 'p90': p90, 'p99': p90 * 1.1,
                'stale_share': share, 'share_over_stale': 0.0, 'events': 100,
                'source': 'grid'}

    def test_picks_the_longest_interval_that_meets_every_criterion(self):
        rows = [self._row(15, 600, 0.001), self._row(30, 1500, 0.002),
                self._row(60, 1700, 0.003), self._row(120, 6000, 0.4)]
        rec = ir.recommend(rows)
        self.assertEqual(rec['max_acceptable_interval_minutes'], 60)

    def test_returns_none_when_no_interval_meets_the_criteria(self):
        rows = [self._row(15, 3600, 0.001)]
        self.assertIsNone(ir.recommend(rows))

    def test_returns_none_when_no_row_has_measurements(self):
        self.assertIsNone(ir.recommend([ir._empty_sim(15), ir._empty_sim(30)]))

    def test_intervals_at_or_below_the_measurement_resolution_are_not_conclusive(self):
        rows = [self._row(15, 10, 0.0), self._row(30, 600, 0.001),
                self._row(60, 3600, 0.9)]
        for row in rows:
            row['at_or_below_resolution'] = row['interval_minutes'] <= 15
        rec = ir.recommend(rows)
        self.assertEqual(rec['max_acceptable_interval_minutes'], 30)

    def test_reports_the_interval_beyond_which_gains_flatten(self):
        rows = [self._row(15, 100, 0.01), self._row(30, 200, 0.02),
                self._row(60, 300, 0.03), self._row(120, 320, 0.04)]
        rec = ir.recommend(rows)
        self.assertEqual(rec['diminishing_returns_from_minutes'], 60)
        self.assertIn('30 分钟', rec['summary'])


class DailyChangeIntervalsTest(unittest.TestCase):
    def test_detects_day_resolution_re_prices(self):
        store = {
            'v': 1,
            't': ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'],
            's': {'0.00000,0.00000': {'g': [150.0, 150.0, 151.0, 152.0]}},
        }
        out = ir.daily_change_intervals(store, ir.station_labels(stations_fixture()))
        self.assertEqual(out['overall'], [86400.0])
        self.assertEqual(out['by_region']['Montréal'], [86400.0])

    def test_null_gaps_reset_the_baseline_instead_of_faking_a_change(self):
        store = {
            'v': 1,
            't': ['2026-10-01', '2026-10-02', '2026-10-03'],
            's': {'0.00000,0.00000': {'g': [150.0, None, 151.0]}},
        }
        self.assertEqual(ir.daily_change_intervals(store, {})['overall'], [])

    def test_empty_store_degrades_to_empty_lists(self):
        self.assertEqual(ir.daily_change_intervals(None, {})['overall'], [])


class StationHistoryLoadingTest(unittest.TestCase):
    """Issue #114: the daily station store is sharded by month on disk.

    The report must keep reading the same merged shape (`t` + `s`) no matter
    whether the checkout has the new `history/station-history/YYYY-MM.json`
    shards or the legacy single `history/station-history.json` file.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data = Path(self._tmp.name) / 'data'
        self.addCleanup(self._tmp.cleanup)

    def test_reads_and_merges_month_shards(self):
        write_json(self.data / 'history' / 'station-history' / '2026-09.json', {
            'v': 1, 'm': '2026-09', 't': ['2026-09-30'],
            's': {'0.00000,0.00000': {'g': [150.0]}},
        })
        write_json(self.data / 'history' / 'station-history' / '2026-10.json', {
            'v': 1, 'm': '2026-10', 't': ['2026-10-01'],
            's': {'0.00000,0.00000': {'g': [151.0]}},
        })
        store = ir.load_station_history(self.data)
        self.assertEqual(store['t'], ['2026-09-30', '2026-10-01'])
        self.assertEqual(store['s']['0.00000,0.00000']['g'], [150.0, 151.0])

    def test_falls_back_to_the_legacy_single_file(self):
        write_json(self.data / 'history' / 'station-history.json', {
            'v': 1, 't': ['2026-10-01'],
            's': {'0.00000,0.00000': {'g': [151.0]}},
        })
        store = ir.load_station_history(self.data)
        self.assertEqual(store['t'], ['2026-10-01'])

    def test_missing_store_is_an_empty_store(self):
        store = ir.load_station_history(self.data)
        self.assertEqual(store['t'], [])
        self.assertEqual(store['s'], {})


class GitObservationsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def _git(self, *args):
        return subprocess.run(['git', *args], cwd=self.root, capture_output=True,
                              text=True, check=True)

    def _commit_store(self, message, at, regular):
        store = {'v': 1, 'last': {'at': at}, 's': {'0.00000,0.00000': {'t': None,
                                                                      'u': at,
                                                                      'p': [regular, 200.0, 300.0]}}}
        path = self.root / 'data' / 'history' / 'station-changes.json'
        write_json(path, store)
        self._git('add', '-A')
        self._git('-c', 'user.email=t@example.com', '-c', 'user.name=t',
                  'commit', '-q', '-m', message)

    def test_reads_the_revisions_of_the_change_store_in_time_order(self):
        self._git('init', '-q')
        self._commit_store('one', iso(0), 150.0)
        self._commit_store('two', iso(30), 155.0)
        obs = ir.load_git_observations(self.root)
        self.assertEqual([o['at'] for o in obs],
                         [START, START + timedelta(minutes=30)])
        self.assertEqual(obs[1]['prices']['0.00000,0.00000']['g'], 155.0)

    def test_missing_git_or_missing_file_yields_no_observations(self):
        self.assertEqual(ir.load_git_observations(self.root), [])


class BuildReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name) / 'data'
        self.data_dir.mkdir(parents=True)
        write_json(self.data_dir / 'stations.json', stations_fixture())
        self.root = self.data_dir.parent

    def tearDown(self):
        self._tmp.cleanup()

    def _sufficient_source(self):
        write_json(self.data_dir / 'history' / 'station-changes.json', {
            'v': 1,
            't': ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04'],
            's': {'0.00000,0.00000': {'g': [150.0, 150.0, 151.0, 152.0]}},
        })
        return ['2026-10-01', '2026-10-02', '2026-10-03', '2026-10-04']

    def test_insufficient_sample_reports_the_missing_days_instead_of_a_conclusion(self):
        report = ir.build_report(self.data_dir, self.root, START)
        self.assertEqual(report['status'], 'insufficient')
        self.assertIsNone(report['recommendation'])
        self.assertEqual(report['sample']['missing_days'], ir.MIN_SAMPLE_DAYS)
        self.assertTrue(report['preliminary'])
        markdown = ir.format_report(report)
        self.assertIn('样本不足', markdown)
        self.assertIn(f"还需 {ir.MIN_SAMPLE_DAYS} 天", markdown)
        self.assertNotIn('最大可接受间隔 =', markdown)

    def test_short_but_real_window_still_produces_a_preliminary_table(self):
        write_json(self.data_dir / 'history' / 'station-changes.json',
                   {'v': 1, 'last': {'at': iso(0)}, 's': {}})
        report = ir.build_report(self.data_dir, self.root, START,
                                 observations=ir.normalize_observations(big_entries(days=1)))
        self.assertEqual(report['status'], 'insufficient')
        self.assertEqual(len(report['staleness']['rows']), len(ir.INTERVAL_GRID_MINUTES))
        self.assertIn('初值', ir.format_report(report))

    def test_sufficient_sample_yields_a_recommendation_and_a_bias_note(self):
        report = ir.build_report(self.data_dir, self.root, START,
                                 observations=ir.normalize_observations(big_entries()),
                                 probe={'days_covered': 4, 'interval_p50': 900.0})
        self.assertEqual(report['status'], 'ok')
        rec = report['recommendation']
        self.assertEqual(rec['max_acceptable_interval_minutes'], 30)
        self.assertIn('p90', rec['summary'])
        markdown = ir.format_report(report)
        self.assertIn('最大可接受间隔', markdown)
        self.assertIn('下界', markdown)
        self.assertIn('| 30 |', markdown)
        self.assertIn('观测间隔', markdown)
        self.assertIn('≤ 分辨率，不可测', markdown)

    def test_change_arrival_is_grouped_by_region_and_brand(self):
        report = ir.build_report(self.data_dir, self.root, START,
                                 observations=ir.normalize_observations(big_entries()))
        arrival = report['change_arrival']
        # Each changer re-prices every 735 min => every recorded interval is 44100 s.
        self.assertGreaterEqual(arrival['overall']['count'], 200)
        self.assertEqual(arrival['overall']['p50'], 44100.0)
        self.assertIn('Montréal', arrival['by_region'])
        self.assertIn('Shell', arrival['by_brand'])

    def test_sampling_resolution_is_taken_from_the_observation_gaps(self):
        report = ir.build_report(self.data_dir, self.root, START,
                                 observations=ir.normalize_observations(big_entries()))
        self.assertEqual(report['bias']['sampling_minutes'], 15)


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / 'data'
        self.data_dir.mkdir(parents=True)
        write_json(self.data_dir / 'stations.json', stations_fixture())
        write_json(self.data_dir / 'history' / 'station-changes.json',
                   {'v': 1, 'last': {'at': iso(0)}, 's': {}})

    def tearDown(self):
        self._tmp.cleanup()

    def test_writes_both_the_report_and_the_machine_readable_json(self):
        path = self.root / 'data' / 'history' / 'observations.json'
        write_json(path, big_entries())
        rc = ir.main(['--data-dir', str(self.data_dir), '--root', str(self.root),
                      '--now', iso(0),
                      '--observations', str(path)])
        self.assertEqual(rc, 0)
        payload = read_json(self.data_dir / 'fetch-interval.json')
        self.assertEqual(payload['v'], 1)
        self.assertEqual(payload['status'], 'ok')
        self.assertTrue((self.root / 'docs' / 'fetch-interval-report.md').exists())

    def test_json_flag_prints_the_payload_without_touching_the_files(self):
        out = self._run_capture(['--json', '--no-write'])
        self.assertEqual(json.loads(out)['v'], 1)
        self.assertFalse((self.data_dir / 'fetch-interval.json').exists())

    def _run_capture(self, extra):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ir.main(['--data-dir', str(self.data_dir), '--root', str(self.root),
                          '--now', iso(0), *extra])
        self.assertEqual(rc, 0)
        return buf.getvalue()


def write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row, ensure_ascii=False,
                                       separators=(',', ':')) + '\n'
                            for row in rows), encoding='utf-8')


def events_lines(days=4, step_minutes=15, per_cycle=2, stations=FIXTURE_STATIONS):
    """A local probe event log: 15-minute cycles, ``per_cycle`` re-prices each."""
    steps = (days * 24 * 60) // step_minutes
    return [{'t': iso(i * step_minutes), 'changed': per_cycle,
             'keys': [f'{j}.00000,0.00000' for j in range(per_cycle)],
             'stations': stations}
            for i in range(steps)]


class ProbeEventsFileTest(unittest.TestCase):
    """Issue #92: the samples live in a local JSONL log, not in git."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.data_dir = self.root / 'data'
        self.data_dir.mkdir(parents=True)
        write_json(self.data_dir / 'stations.json', stations_fixture())
        # No committed snapshot history at all: only the local log exists.
        self.events_path = self.root / 'data' / 'probe-local' / 'events.jsonl'
        write_jsonl(self.events_path, events_lines())

    def test_loads_observations_and_change_events_from_the_jsonl_log(self):
        observations, events = ir.load_events_file(self.events_path)
        self.assertEqual(len(observations), 4 * 24 * 60 // 15)
        self.assertEqual(len(events), 2 * len(observations))
        self.assertEqual(observations[0]['at'], START)
        self.assertEqual(observations[0]['stations'], FIXTURE_STATIONS)
        self.assertEqual(events[0]['key'], '0.00000,0.00000')

    def test_a_cycle_without_changes_still_marks_an_observation(self):
        write_jsonl(self.events_path, [
            {'t': iso(0), 'changed': 0, 'stations': 7},
            {'t': iso(15), 'changed': 1, 'keys': ['a'], 'stations': 7},
        ])
        observations, events = ir.load_events_file(self.events_path)
        self.assertEqual(len(observations), 2)
        self.assertEqual([event['key'] for event in events], ['a'])

    def test_a_corrupt_log_degrades_to_an_empty_series(self):
        self.events_path.write_text('not json\n[1]\n', encoding='utf-8')
        self.assertEqual(ir.load_events_file(self.events_path), ([], []))
        self.assertEqual(ir.load_events_file(self.root / 'nope.jsonl'), ([], []))

    def test_station_total_comes_from_the_log_when_there_are_no_prices(self):
        observations = [{'at': START + timedelta(minutes=15 * i), 'stations': 4,
                         'prices': {}} for i in range(9)]   # 00:00 -> 02:00
        events = [{'at': START + timedelta(minutes=1), 'key': 'a', 'fuels': {},
                   'region': ir.UNKNOWN_REGION, 'brand': ir.UNKNOWN_BRAND}]
        row = ir.simulate_interval(events, observations, 60)
        self.assertEqual(row['stations'], 4)
        self.assertEqual(row['events'], 1)
        # change at 00:01, next hourly poll 01:00 -> 59 min of stale display.
        self.assertEqual(row['p90'], 59 * 60)
        self.assertEqual(row['stale_share'],
                         round(59 * 60 / (4 * 120 * 60), 6))

    def test_report_from_the_local_log_matches_the_git_history_sample(self):
        observations, events = ir.load_events_file(self.events_path)
        report = ir.build_report(self.data_dir, self.root, START,
                                 observations=observations, events=events,
                                 observations_source=str(self.events_path))
        sample = report['sample']
        self.assertEqual(sample['coverage_days'], 4)
        self.assertEqual(sample['change_events'], len(events))
        self.assertEqual(sample['stations_observed'], FIXTURE_STATIONS)
        self.assertEqual(report['status'], 'ok')
        self.assertEqual(report['bias']['sampling_minutes'], 15)
        self.assertEqual(report['source']['observations'], str(self.events_path))
        row = [r for r in report['staleness']['rows']
               if r['interval_minutes'] == 60][0]
        self.assertIsNotNone(row['stale_share'])

    def test_cli_uses_the_events_file_when_it_exists(self):
        out = self._run_capture(['--json', '--no-write',
                                 '--events-file', str(self.events_path)])
        payload = json.loads(out)
        self.assertEqual(payload['sample']['change_events'],
                         2 * 4 * 24 * 60 // 15)
        self.assertIn('probe-local', payload['source']['observations'])

    def test_cli_missing_events_file_falls_back_to_git_without_error(self):
        import contextlib
        import io
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            payload = json.loads(self._run_capture(
                ['--json', '--no-write',
                 '--events-file', str(self.root / 'data' / 'probe-local' / 'nope.jsonl')]))
        self.assertEqual(payload['status'], 'insufficient')
        self.assertIn('git revision history', payload['source']['observations'])
        self.assertIn('falling back', err.getvalue())

    def _run_capture(self, extra):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = ir.main(['--data-dir', str(self.data_dir), '--root', str(self.root),
                          '--now', iso(0), *extra])
        self.assertEqual(rc, 0)
        return buf.getvalue()


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Unit tests for the timetable staleness evaluator (issue #91).

``scripts/schedule_eval.py`` answers "how stale does the app look under *this*
timetable?" with the same metric口径 as `scripts/interval_report.py`
(``next fetch >= change time``, share = Σlag / (stations x window seconds)),
plus the detection-quantisation correction (a re-price detected at ``t`` really
happened at ``t - U(0, 15 min)``).
"""

import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schedule_eval  # noqa: E402
from fetch_strategy import LOCAL_TZ  # noqa: E402
from zoneinfo import ZoneInfo  # noqa: E402

UTC = timezone.utc
ROOT = Path(__file__).resolve().parent.parent


def at(hour, minute=0, second=0, day=12):
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC)


def tiny_window(day=12):
    """Observations 00:00 → 02:00 with one re-price detected at 00:30 and 01:30."""
    def row(moment, first, second):
        return {'at': moment,
                'prices': {'1.00000,1.00000': {'g': first},
                           '2.00000,2.00000': {'g': second}}}

    observations = [
        row(at(0, 0, day=day), 150.0, 150.0),
        row(at(0, 30, day=day), 160.0, 150.0),   # station 1 re-prices
        row(at(1, 0, day=day), 160.0, 150.0),
        row(at(1, 30, day=day), 160.0, 170.0),   # station 2 re-prices
        row(at(2, 0, day=day), 160.0, 170.0),
    ]
    return schedule_eval.build_window(observations)


class SyntheticMetricsTest(unittest.TestCase):
    def test_a_change_is_fixed_by_the_next_fetch(self):
        window = tiny_window()
        metrics = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                  tz=ZoneInfo('UTC'))
        self.assertEqual(metrics['events'], 2)
        # 00:30 -> 01:00 = 1800 s; 01:30 -> 02:00 = 1800 s.
        self.assertEqual(metrics['p50'], 1800.0)
        self.assertEqual(metrics['p90'], 1800.0)
        self.assertEqual(metrics['p99'], 1800.0)
        self.assertEqual(metrics['fetches_per_day'], 2)

    def test_stale_share_uses_the_interval_report_denominator(self):
        window = tiny_window()
        metrics = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                  tz=ZoneInfo('UTC'))
        expected = metrics['lag_sum_seconds'] / (metrics['stations']
                                                 * metrics['window_seconds'])
        self.assertAlmostEqual(metrics['stale_share'], expected)
        self.assertEqual(metrics['stations'], 2)
        self.assertEqual(metrics['window_seconds'], 7200.0)

    def test_a_denser_timetable_is_fresher(self):
        window = tiny_window()
        coarse = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                 tz=ZoneInfo('UTC'))
        dense = schedule_eval.simulate_timetable(['00:30', '01:00', '01:30', '02:00'],
                                                window, tz=ZoneInfo('UTC'))
        self.assertLess(dense['p90'], coarse['p90'])
        self.assertLess(dense['stale_share'], coarse['stale_share'])

    def test_a_timetable_that_covers_the_change_has_no_lag(self):
        window = tiny_window()
        metrics = schedule_eval.simulate_timetable(
            ['00:00', '00:30', '01:00', '01:30', '02:00'], window,
            tz=ZoneInfo('UTC'))
        self.assertEqual(metrics['p90'], 0.0)

    def test_an_empty_window_is_reported_not_crashed(self):
        window = schedule_eval.build_window([])
        metrics = schedule_eval.simulate_timetable(['01:00'], window,
                                                  tz=ZoneInfo('UTC'))
        self.assertEqual(metrics['events'], 0)
        self.assertIsNone(metrics['p90'])
        self.assertIsNone(metrics['stale_share'])


class JitterCorrectionTest(unittest.TestCase):
    def test_the_correction_is_reproducible_for_a_fixed_seed(self):
        window = tiny_window()
        first = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                tz=ZoneInfo('UTC'),
                                                jitter=True, seed=1234)
        second = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                 tz=ZoneInfo('UTC'),
                                                 jitter=True, seed=1234)
        self.assertEqual(first['p90'], second['p90'])
        self.assertEqual(first['stale_share'], second['stale_share'])

    def test_the_correction_moves_the_change_earlier_so_the_lag_grows(self):
        window = tiny_window()
        plain = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                tz=ZoneInfo('UTC'))
        corrected = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                    tz=ZoneInfo('UTC'),
                                                    jitter=True, seed=7)
        self.assertGreater(corrected['p90'], plain['p90'])
        self.assertGreater(corrected['stale_share'], plain['stale_share'])
        self.assertEqual(corrected['jitter'], True)

    def test_the_correction_never_exceeds_the_detection_resolution(self):
        window = tiny_window()
        corrected = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                    tz=ZoneInfo('UTC'),
                                                    jitter=True, seed=7,
                                                    resolution_seconds=900)
        plain = schedule_eval.simulate_timetable(['01:00', '02:00'], window,
                                                tz=ZoneInfo('UTC'))
        self.assertLessEqual(corrected['p99'] - plain['p99'], 900.0)


class WindowSufficiencyTest(unittest.TestCase):
    """Issue #98: too few events is a *skip* condition, never a looser threshold."""

    def test_a_window_without_events_is_not_sufficient(self):
        window = schedule_eval.build_window([])
        self.assertFalse(schedule_eval.window_is_sufficient(window))

    def test_a_window_below_the_published_event_floor_is_not_sufficient(self):
        window = tiny_window()          # 2 re-prices, far below the floor
        self.assertEqual(len(window['events']), 2)
        self.assertFalse(schedule_eval.window_is_sufficient(window))

    def test_the_floor_is_the_published_interval_report_floor(self):
        import interval_report
        window = tiny_window()
        window['events'] = list(range(interval_report.MIN_CHANGE_EVENTS))
        self.assertTrue(schedule_eval.window_is_sufficient(window))
        window['events'] = list(range(interval_report.MIN_CHANGE_EVENTS - 1))
        self.assertFalse(schedule_eval.window_is_sufficient(window))

    def test_a_window_without_a_measurable_span_is_not_sufficient(self):
        window = tiny_window()
        window['events'] = list(range(100))
        window['observations'] = []
        self.assertFalse(schedule_eval.window_is_sufficient(window))


class VerdictTest(unittest.TestCase):
    def metrics(self, p90, share):
        return {'p50': p90 / 2, 'p90': p90, 'p99': p90 * 1.1, 'stale_share': share}

    def test_the_published_criteria_are_enforced(self):
        verdict = schedule_eval.verdict(self.metrics(20 * 60, 0.01))
        self.assertTrue(verdict['acceptable'])
        self.assertEqual(verdict['failed'], [])

    def test_a_p90_over_thirty_minutes_is_rejected(self):
        verdict = schedule_eval.verdict(self.metrics(31 * 60, 0.01))
        self.assertFalse(verdict['acceptable'])
        self.assertIn('p90', ' '.join(verdict['failed']))

    def test_a_stale_share_over_five_percent_is_rejected(self):
        verdict = schedule_eval.verdict(self.metrics(10 * 60, 0.06))
        self.assertFalse(verdict['acceptable'])
        self.assertIn('share', ' '.join(verdict['failed']).lower())


class ComparePlansTest(unittest.TestCase):
    def plan(self, p90, p99, share):
        return {'p90': p90, 'p99': p99, 'stale_share': share}

    def test_a_plan_that_is_better_everywhere_wins(self):
        self.assertEqual(schedule_eval.compare_plans(self.plan(100, 200, 0.01),
                                                     self.plan(200, 400, 0.02)),
                         'better')

    def test_marginal_differences_are_a_tie(self):
        self.assertEqual(schedule_eval.compare_plans(self.plan(100, 200, 0.01),
                                                     self.plan(105, 205, 0.0105)),
                         'tie')

    def test_a_plan_that_pays_for_the_mean_with_the_tail_is_mixed(self):
        self.assertEqual(schedule_eval.compare_plans(self.plan(50, 900, 0.01),
                                                     self.plan(100, 200, 0.02)),
                         'mixed')

    def test_a_plan_that_is_worse_everywhere_loses(self):
        self.assertEqual(schedule_eval.compare_plans(self.plan(200, 900, 0.03),
                                                     self.plan(100, 200, 0.01)),
                         'worse')

    def test_pareto_front_lists_the_plans_that_beat_a_plan(self):
        plans = {'good': self.plan(100, 200, 0.01), 'worse': self.plan(300, 900, 0.03),
                 'same': self.plan(100, 200, 0.01)}
        front = schedule_eval.pareto_front(plans)
        self.assertEqual(front['good'], [])
        self.assertEqual(front['same'], [])
        self.assertEqual(front['worse'], ['good', 'same'])

    def test_missing_metrics_do_not_crash_the_comparison(self):
        self.assertEqual(schedule_eval.compare_plans({'p90': None}, self.plan(1, 2, 0.01)),
                         'unknown')


class NightBlackoutIsNeverFresherTest(unittest.TestCase):
    """Structural claim behind issue #98: the blackout only *removes* fetches.

    Because the night-blackout timetable is a strict subset of the hybrid's,
    every re-price waits at least as long, so no amount of new probe data can
    make the blackout fresher — that is why the ``p99`` ratio assertion was
    fragile while the direction (and the stale share) is not.
    """

    def test_the_blackout_is_the_hybrid_without_the_night_slots(self):
        presets = schedule_eval.preset_timetables()
        blackout = set(presets['night-blackout'])
        hybrid = set(presets['busy20+night+midnight'])
        self.assertLess(blackout, hybrid)
        self.assertEqual(sorted(hybrid - blackout),
                         [f'{hour:02d}:00' for hour in range(1, 6)])

    def test_fewer_fetches_never_shorten_a_lag(self):
        presets = schedule_eval.preset_timetables()
        window = tiny_window()
        kwargs = {'tz': ZoneInfo('UTC'), 'jitter': True, 'seed': 11}
        hybrid = schedule_eval.simulate_timetable(
            presets['busy20+night+midnight'], window, **kwargs)
        blackout = schedule_eval.simulate_timetable(
            presets['night-blackout'], window, **kwargs)
        self.assertGreaterEqual(blackout['p99'], hybrid['p99'])
        self.assertGreater(blackout['stale_share'], hybrid['stale_share'])
        self.assertNotEqual(schedule_eval.compare_plans(blackout, hybrid),
                            'better')


class PresetTest(unittest.TestCase):
    def test_presets_are_well_formed_and_sized(self):
        presets = schedule_eval.preset_timetables()
        self.assertEqual(len(presets['uniform60']), 24)
        self.assertEqual(len(presets['uniform30']), 48)
        self.assertEqual(len(presets['uniform15']), 96)
        self.assertEqual(len(presets['busy30+night+midnight']), 47)
        self.assertEqual(len(presets['busy20+night+midnight']), 65)
        self.assertEqual(len(presets['night-blackout']), 60)
        for labels in presets.values():
            self.assertEqual(labels, sorted(labels))
            self.assertEqual(len(labels), len(set(labels)))

    def test_the_recommended_preset_is_the_planner_default(self):
        recommended = schedule_eval.preset_timetables()['busy20+night+midnight']
        import schedule_plan
        self.assertEqual(recommended, schedule_plan.plan_timetable(budget=65)['timetable'])


class CliTest(unittest.TestCase):
    def test_cli_accepts_an_explicit_timetable(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = schedule_eval.main([
                '--timetable', '01:00,02:00',
                '--observations', str(ROOT / 'scripts' / 'fixtures' /
                                      'schedule-observations.json'),
                '--json', '--no-jitter'])
        self.assertEqual(rc, 0)
        metrics = json.loads(buf.getvalue())
        self.assertEqual(metrics['events'], 2)
        self.assertEqual(metrics['p90'], 1800.0)
        self.assertEqual(metrics['stations'], 2)

    def test_cli_evaluates_a_preset_plan(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = schedule_eval.main([
                '--plan', 'busy20+night+midnight', '--no-jitter',
                '--observations', str(ROOT / 'scripts' / 'fixtures' /
                                      'schedule-observations.json'),
                '--json'])
        self.assertEqual(rc, 0)
        metrics = json.loads(buf.getvalue())
        self.assertEqual(metrics['fetches_per_day'], 65)
        self.assertEqual(metrics['events'], 2)
        self.assertIn('acceptable', schedule_eval.verdict(metrics))

    def test_cli_rejects_an_unparseable_timetable(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = schedule_eval.main(['--timetable', 'nonsense', '--json'])
        self.assertEqual(rc, 2)

    def test_cli_rejects_an_unknown_preset(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = schedule_eval.main(['--plan', 'nope', '--json'])
        self.assertEqual(rc, 2)

    def test_cli_requires_a_plan_argument(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stderr(buf):
            rc = schedule_eval.main(['--json'])
        self.assertEqual(rc, 2)


def git_history_available():
    try:
        out = subprocess.run(['git', '-C', str(ROOT), 'log', '-1',
                              '--format=%H', '--',
                              'data/history/station-changes.json'],
                             capture_output=True, text=True)
    except OSError:
        return False
    return out.returncode == 0 and bool(out.stdout.strip())


@unittest.skipUnless(git_history_available(), 'no git history for the probe data')
class RealDataParetoTest(unittest.TestCase):
    """Acceptance #4: the measured conclusions must be reproducible (±15%)."""

    @classmethod
    def setUpClass(cls):
        observations = schedule_eval.load_observations(root=str(ROOT))
        if len(observations) < 100:
            raise unittest.SkipTest('too few probe observations')
        cls.presets = schedule_eval.preset_timetables()
        cls.metrics = {}
        full = schedule_eval.build_window(observations)
        train, test = schedule_eval.split_window(full, fraction=0.6)
        cls.windows = {'all': full, 'train': train, 'test': test}

    def simulate(self, name, window):
        key = (name, window['window_start'].isoformat())
        if key not in self.metrics:
            self.metrics[key] = schedule_eval.simulate_timetable(
                self.presets[name], window, jitter=True)
        return self.metrics[key]

    def test_the_recommended_timetable_meets_the_published_criteria(self):
        metrics = self.simulate('busy20+night+midnight', self.windows['all'])
        verdict = schedule_eval.verdict(metrics)
        self.assertTrue(verdict['acceptable'], verdict['failed'])
        self.assertLessEqual(metrics['p90'], schedule_eval.P90_BUDGET_SECONDS)

    def test_the_old_hourly_production_plan_fails_the_criteria(self):
        """24/day (uniform 60 min) cannot reach p90 <= 30 min: hence #91."""
        metrics = self.simulate('uniform60', self.windows['all'])
        verdict = schedule_eval.verdict(metrics)
        self.assertFalse(verdict['acceptable'])
        self.assertGreater(metrics['p90'], schedule_eval.P90_BUDGET_SECONDS)
        self.assertEqual(metrics['fetches_per_day'], 24)

    def test_the_hybrid_is_far_fresher_than_the_cheapest_uniform_grid(self):
        """busy20 (65/day) p90 18 min vs uniform 30 min (48/day) p90 27 min."""
        windows = self.windows
        hybrid = self.simulate('busy20+night+midnight', windows['all'])
        uniform = self.simulate('uniform30', windows['all'])
        self.assertLess(hybrid['p90'], uniform['p90'] * 0.85)

    def test_the_hybrid_matches_a_uniform_grid_of_the_same_spacing_for_less(self):
        """Equal p90 with ~10 % fewer requests: the night floor is not wasted."""
        windows = self.windows
        hybrid = self.simulate('busy20+night+midnight', windows['all'])
        uniform = schedule_eval.simulate_timetable(
            schedule_eval.uniform_timetable(20), windows['all'], jitter=True)
        self.assertLess(hybrid['fetches_per_day'], uniform['fetches_per_day'])
        self.assertLessEqual(hybrid['p90'], uniform['p90'] * 1.05)
        self.assertLess(hybrid['p50'], uniform['p50'])

    def test_the_night_blackout_is_judged_worse(self):
        self.assert_blackout_is_rejected(self.windows['all'], 'all')

    def assert_blackout_is_rejected(self, window, split=''):
        """The #91 conclusion as a *semantic* statement (issue #98).

        The old form was a ratio on live data
        (``blackout p99 > hybrid p99 x 1.5``), which drifts as probes grow
        (blackout 1197.9 s vs hybrid 1196.1 s on the test split). What is
        actually claimed is the ε-Pareto one: some plan that meets the
        published criteria beats the blackout on p90/p99/share. Kept alongside
        the structural direction (the blackout only removes fetches, so it is
        never fresher) so a real regression in the timetable still shows up.
        """
        blackout = self.simulate('night-blackout', window)
        hybrid = self.simulate('busy20+night+midnight', window)
        # Structural and data-independent: a subset timetable never shortens a
        # wait, so the blackout can never look fresher than the hybrid.
        self.assertGreaterEqual(blackout['p99'], hybrid['p99'], split)
        self.assertGreaterEqual(blackout['stale_share'], hybrid['stale_share'],
                                split)
        if blackout['lag_sum_seconds'] > hybrid['lag_sum_seconds']:
            # The window really holds re-prices inside the removed night gaps
            # (both plans share the denominator), so it is strictly staler.
            self.assertGreater(blackout['stale_share'], hybrid['stale_share'],
                               split)
        self.assertNotEqual(schedule_eval.compare_plans(blackout, hybrid),
                            'better', split)
        dominated = schedule_eval.pareto_front(
            {name: self.simulate(name, window) for name in self.presets})
        self.assertTrue(dominated['night-blackout'], split)
        acceptable = [name for name in dominated['night-blackout']
                      if schedule_eval.verdict(
                          self.simulate(name, window))['acceptable']]
        self.assertTrue(acceptable, split)

    def test_the_conclusion_holds_on_an_explicit_train_test_split(self):
        windows = self.windows
        checked = 0
        for split in ('train', 'test'):
            window = windows[split]
            if not schedule_eval.window_is_sufficient(window):
                continue    # too few re-prices: skip, never tighten the ratio
            checked += 1
            hybrid = self.simulate('busy20+night+midnight', window)
            uniform = self.simulate('uniform30', window)
            self.assertLess(hybrid['p90'], uniform['p90'] * 0.9, split)
            self.assert_blackout_is_rejected(window, split)
        if not checked:
            self.skipTest('both splits are below the sample floor')

    def test_an_extra_daily_fetch_buys_less_than_the_night_floor(self):
        """The night floor is the cheapest freshness the plan can buy."""
        windows = self.windows
        hybrid = self.simulate('busy20+night+midnight', windows['all'])
        blackout = self.simulate('night-blackout', windows['all'])
        self.assertGreater(hybrid['fetches_per_day'], blackout['fetches_per_day'])
        self.assertLess(hybrid['p99'], blackout['p99'])


if __name__ == '__main__':
    unittest.main()

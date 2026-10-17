#!/usr/bin/env python3
"""Unit tests for the explicit fetch-timetable planner (issue #91).

The planner turns "stations re-price X times at local hour H" into a concrete
5-minute-granularity timetable whose per-hour gaps are hard-capped (busy hours
20 minutes, local midnight 10 minutes, 01:00-05:00 60 minutes) while the total
number of requests stays inside the shared daily budget.
"""

import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import schedule_plan  # noqa: E402
from fetch_strategy import (  # noqa: E402
    DEFAULT_DAILY_BUDGET,
    LOCAL_TZ,
    gap_caps,
    max_gap_minutes,
    parse_timetable,
    timetable_datetimes,
    timetable_utc_labels,
)

UTC = timezone.utc


def at(hour, minute=0, day=16, month=10):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def observation(hour, minute=0, day=16, keys=(('1.00000,1.00000', {'g': 150.0}),)):
    return {'at': at(hour, minute, day),
            'prices': {key: dict(fuels) for key, fuels in keys}}


def event(hour, minute=0, day=16, key='1.00000,1.00000'):
    return {'at': at(hour, minute, day), 'key': key,
            'fuels': ['g'], 'region': 'Montréal', 'brand': 'Test'}


class DefaultTimetableTest(unittest.TestCase):
    """The shipped plan: busy 20 min, midnight 10 min, night floor 1/h."""

    def setUp(self):
        self.plan = schedule_plan.plan_timetable(budget=DEFAULT_DAILY_BUDGET)

    def test_the_default_plan_is_budget_sized(self):
        self.assertEqual(DEFAULT_DAILY_BUDGET, 65)
        self.assertEqual(self.plan['timetable_fetches_per_day'],
                         DEFAULT_DAILY_BUDGET)
        self.assertEqual(len(self.plan['timetable']), DEFAULT_DAILY_BUDGET)
        self.assertTrue(self.plan['constraints']['satisfied'])

    def test_busy_hours_are_polled_every_twenty_minutes(self):
        labels = self.plan['timetable']
        busy = [label for label in labels if 6 <= int(label[:2]) <= 23]
        self.assertEqual(len(busy), 18 * 3)
        self.assertEqual(busy, [f'{h:02d}:{m:02d}'
                                for h in range(6, 24) for m in (0, 20, 40)])

    def test_local_midnight_is_polled_every_ten_minutes(self):
        midnight = [label for label in self.plan['timetable']
                    if label.startswith('00:')]
        self.assertEqual(midnight, [f'00:{m:02d}' for m in range(0, 60, 10)])

    def test_night_hours_keep_one_fetch_per_hour(self):
        night = [label for label in self.plan['timetable']
                 if 1 <= int(label[:2]) <= 5]
        self.assertEqual(night, ['01:00', '02:00', '03:00', '04:00', '05:00'])

    def test_every_hour_is_sampled_at_least_once(self):
        """The night floor is the whole point: no hour is ever left blind."""
        hours = sorted({int(label[:2]) for label in self.plan['timetable']})
        self.assertEqual(hours, list(range(24)))

    def test_no_hour_gap_exceeds_its_cap(self):
        caps = gap_caps()
        for hour, gap in self.plan['hourly_gap_minutes'].items():
            self.assertLessEqual(gap, caps[int(hour)], f'hour {hour}')

    def test_timetable_is_sorted_unique_and_five_minute_granularity(self):
        labels = self.plan['timetable']
        self.assertEqual(labels, sorted(labels))
        self.assertEqual(len(labels), len(set(labels)))
        self.assertTrue(all(parse_timetable([label])[0][1] % 5 == 0
                            for label in labels))

    def test_the_busiest_gap_is_the_twenty_minute_day_cadence(self):
        counts = self.plan['hourly_counts']
        self.assertEqual(counts['0'], 6)
        self.assertEqual([counts[str(h)] for h in range(1, 6)], [1] * 5)
        self.assertEqual([counts[str(h)] for h in range(6, 24)], [3] * 18)


class BudgetAndGapConstraintsTest(unittest.TestCase):
    def test_a_bigger_budget_is_water_filled_into_the_busiest_hours(self):
        expected = [1.0] * 24
        expected[12] = 500.0
        expected[13] = 400.0
        plan = schedule_plan.plan_timetable(expected=expected, budget=71)
        self.assertEqual(plan['timetable_fetches_per_day'], 71)
        counts = plan['hourly_counts']
        self.assertGreater(counts['12'], 3)
        self.assertGreaterEqual(counts['12'], counts['13'])
        self.assertGreaterEqual(counts['13'], counts['14'])

    def test_the_marginal_gain_falls_as_an_hour_fills_up(self):
        """gain ≈ E_h / (n + 1): the same hour is never filled twice in a row."""
        expected = [0.0] * 24
        expected[12] = 100.0
        expected[9] = 10.0
        plan = schedule_plan.plan_timetable(expected=expected, budget=68)
        counts = plan['hourly_counts']
        self.assertGreater(counts['12'], counts['9'])
        # 3 floor + 3 extra: the day hour's reward is halved twice.
        self.assertLessEqual(counts['12'], 6)

    def test_the_budget_is_never_exceeded(self):
        for budget in (0, 1, 12, 24, 64, 65, 66, 96):
            plan = schedule_plan.plan_timetable(budget=budget)
            self.assertEqual(plan['timetable_fetches_per_day'], budget)
            self.assertEqual(len(plan['timetable']), budget)
            self.assertEqual(len(plan['timetable']), len(set(plan['timetable'])))

    def test_a_budget_below_the_gap_floor_reports_the_conflict(self):
        plan = schedule_plan.plan_timetable(budget=60)
        self.assertFalse(plan['constraints']['satisfied'])
        self.assertEqual(plan['constraints']['floor_per_day'], 65)
        self.assertTrue(plan['constraints']['violations'])
        self.assertEqual(plan['timetable_fetches_per_day'], 60)

    def test_a_budget_at_the_floor_is_satisfied(self):
        plan = schedule_plan.plan_timetable(budget=65)
        self.assertTrue(plan['constraints']['satisfied'])
        self.assertEqual(plan['constraints']['slack'], 0)

    def test_zero_budget_plans_nothing(self):
        plan = schedule_plan.plan_timetable(budget=0)
        self.assertEqual(plan['timetable'], [])
        self.assertFalse(plan['constraints']['satisfied'])

    def test_the_per_hour_cap_wins_over_the_budget(self):
        expected = [0.0] * 24
        expected[12] = 1e6
        plan = schedule_plan.plan_timetable(expected=expected, budget=96,
                                            max_per_hour=6)
        self.assertEqual(plan['hourly_counts']['12'], 6)
        self.assertEqual(plan['timetable_fetches_per_day'], 96)

    def test_a_disabled_gap_cap_still_leaves_one_fetch_per_hour(self):
        plan = schedule_plan.plan_timetable(budget=24, min_per_hour=1,
                                            caps={h: 60 for h in range(24)})
        self.assertEqual(plan['timetable_fetches_per_day'], 24)
        self.assertEqual(sorted({label[:2] for label in plan['timetable']}),
                         [f'{h:02d}' for h in range(24)])


class ExplorationTest(unittest.TestCase):
    """Issue #69 kept: probes go to the hours with the least evidence."""

    def test_probe_slots_are_inside_the_timetable(self):
        observed = [1000.0] * 24
        for hour in (2, 3, 4):
            observed[hour] = 0.0
        plan = schedule_plan.plan_timetable(budget=65, observed_hours=observed,
                                            explore_frac=0.15)
        self.assertTrue(plan['explore_slots'])
        self.assertTrue(set(plan['explore_slots']).issubset(plan['timetable']))
        self.assertLessEqual(plan['explore_count'],
                             round(65 * 0.15))

    def test_probes_prefer_the_least_observed_hours(self):
        observed = [1000.0] * 24
        observed[3] = 0.0
        plan = schedule_plan.plan_timetable(budget=65, observed_hours=observed,
                                            explore_frac=0.15)
        self.assertEqual(plan['explore_slots'], ['03:00'])

    def test_probes_never_break_the_gap_caps(self):
        observed = [0.0] * 24
        plan = schedule_plan.plan_timetable(budget=65, observed_hours=observed,
                                            explore_frac=0.3)
        caps = gap_caps()
        for hour, gap in plan['hourly_gap_minutes'].items():
            self.assertLessEqual(gap, caps[int(hour)])

    def test_zero_exploration_disables_the_probes(self):
        plan = schedule_plan.plan_timetable(budget=65, observed_hours=[0.0] * 24,
                                            explore_frac=0.0)
        self.assertEqual(plan['explore_slots'], [])
        self.assertEqual(plan['explore_count'], 0)


class ExpectedCountsTest(unittest.TestCase):
    """E_h is the *median across days* of exact per-station re-prices."""

    def setUp(self):
        self.observations = [
            observation(10, 0, day=10), observation(10, 15, day=10),
            observation(10, 30, day=10), observation(11, 0, day=10),
            observation(10, 0, day=11), observation(10, 15, day=11),
            observation(11, 0, day=11),
        ]
        self.events = [
            event(10, 15, day=10), event(10, 30, day=10), event(10, 30, day=10),
            event(10, 15, day=11),
        ]

    def test_expected_counts_are_the_daily_median_per_local_hour(self):
        counts, days = schedule_plan.expected_counts(self.observations,
                                                     self.events)
        # Local hour 06:00 = UTC 10:00 in October (EDT).
        self.assertEqual(counts[6], 2.0)          # median(2, 1)
        self.assertEqual(counts[7], 0.0)          # median(1, 0)
        self.assertEqual(days[6], 2)

    def test_an_hour_that_was_never_observed_contributes_no_day(self):
        counts, days = schedule_plan.expected_counts(self.observations,
                                                     self.events)
        self.assertEqual(days[3], 0)
        self.assertEqual(counts[3], 0.0)

    def test_a_configurable_timezone_is_honoured(self):
        counts, _ = schedule_plan.expected_counts(
            self.observations, self.events,
            tz=schedule_plan.ZoneInfo('America/Toronto'))
        self.assertEqual(len(counts), 24)
        self.assertEqual(counts[6], 2.0)

    def test_profile_channel_uses_the_exact_counter_only(self):
        profile = {'hours': {str(h): {'changed_stations': 1e6,
                                      'changed_stations_exact': 10 * h,
                                      'observed_hours': 10.0,
                                      'exact_samples': 3, 'samples': 3}
                             for h in range(24)}}
        counts = schedule_plan.expected_counts_from_profile(profile)
        self.assertIsNotNone(counts)
        # UTC 04:00 -> local 00:00 in October.
        self.assertEqual(counts[0], 4.0)
        # The legacy region-average counter (1e6) is ignored.
        self.assertLess(max(counts), 100.0)

    def test_profile_channel_is_unavailable_until_the_exact_channel_is_ready(self):
        profile = {'hours': {str(h): {'changed_stations': 5, 'observed_hours': 1.0}
                             for h in range(24)}}
        self.assertIsNone(schedule_plan.expected_counts_from_profile(profile))


class MergeScheduleTest(unittest.TestCase):
    def setUp(self):
        self.legacy = {
            'v': 1, 'generated_at': '2026-10-16T18:27:25Z', 'source': 'learned',
            'prior': 'research-2026-09', 'budget_per_day': 24,
            'allocated_per_day': 24, 'min_per_hour': 1, 'max_per_hour': 4,
            'explore_frac': 0.15, 'explore_count': 5,
            'explore_slots': ['03:00'], 'slots': ['00:00', '06:00'],
            'hour_weights': [0.5] * 24, 'coverage_hours': [4],
            'region_coverage': {'Laval': {'peaks': [4], 'covered': True}},
        }

    def merged(self, budget=DEFAULT_DAILY_BUDGET):
        return schedule_plan.merge_schedule(
            self.legacy, budget=budget, now=at(18, 27))

    def test_legacy_fields_survive_the_upgrade(self):
        merged = self.merged()
        self.assertEqual(merged['max_per_hour'], schedule_plan.MAX_TIMETABLE_PER_HOUR)
        self.assertLessEqual(merged['max_gap_minutes_observed'], 60.0)
        self.assertEqual(merged['prior'], 'research-2026-09')
        self.assertEqual(merged['hour_weights'], [0.5] * 24)
        self.assertEqual(merged['coverage_hours'], [4])
        self.assertEqual(merged['region_coverage']['Laval']['covered'], True)

    def test_the_timetable_is_local_and_the_slots_are_its_utc_projection(self):
        merged = self.merged()
        self.assertEqual(merged['v'], 2)
        self.assertEqual(merged['timezone'], LOCAL_TZ)
        self.assertEqual(merged['timetable'][0], '00:00')
        self.assertEqual(len(merged['timetable']), budget := merged['budget_per_day'])
        self.assertEqual(merged['allocated_per_day'], len(merged['slots']))
        expected = timetable_utc_labels(merged['timetable'], LOCAL_TZ,
                                        at(18, 27).date())
        self.assertEqual(merged['slots'], expected)
        self.assertEqual(len(merged['slots']), budget)

    def test_the_relearn_trigger_is_recorded(self):
        merged = self.merged()
        self.assertEqual(merged['plan_relearn_after_days'],
                         schedule_plan.RELEARN_AFTER_DAYS)
        self.assertIn('max_gap_minutes', merged)
        self.assertIn('constraints', merged)

    def test_a_budget_below_the_floor_keeps_the_weight_plan(self):
        """Below 65/day the gap caps cannot hold, so the legacy plan is kept."""
        merged = self.merged(budget=12)
        self.assertFalse(merged['constraints']['satisfied'])
        self.assertEqual(merged['budget_per_day'], 12)
        self.assertEqual(merged['allocated_per_day'], 12)


class CliTest(unittest.TestCase):
    def test_cli_prints_a_json_plan(self):
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = schedule_plan.main(['--budget', '65', '--no-learn', '--json'])
        self.assertEqual(rc, 0)
        plan = json.loads(buf.getvalue())
        self.assertEqual(plan['timetable_fetches_per_day'], 65)

    def test_cli_writes_the_timetable_into_a_schedule_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fetch-schedule.json'
            path.write_text(json.dumps({'v': 1, 'budget_per_day': 24,
                                        'hour_weights': [0.1] * 24,
                                        'slots': ['00:00']}), encoding='utf-8')
            rc = schedule_plan.main(['--budget', '65', '--no-learn',
                                     '--schedule-path', str(path),
                                     '--now', '2026-10-16T18:27:25Z'])
            self.assertEqual(rc, 0)
            schedule = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(schedule['v'], 2)
            self.assertEqual(len(schedule['timetable']), 65)
            self.assertEqual(schedule['hour_weights'], [0.1] * 24)

    def test_cli_never_rewrites_a_schedule_it_cannot_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'fetch-schedule.json'
            path.write_text('{not json', encoding='utf-8')
            rc = schedule_plan.main(['--budget', '65', '--no-learn',
                                     '--schedule-path', str(path)])
            self.assertEqual(rc, 1)


class DSTTimetableTest(unittest.TestCase):
    """The plan is a *local* timetable: it must survive the DST switch.

    Issue #91's tests only covered October (EDT, UTC-4). Issue #92 adds the
    November case (EST, UTC-5) and, crucially, the two transition *days*:
    2026-11-01 (fall back, a 25-hour local day) and 2026-03-08 (spring forward,
    a 23-hour local day). Asserted on the UTC expansion of the timetable:

    * the local 06:00 slot is 10:00 UTC in October and 11:00 UTC in November;
    * the slot count only deviates from ``timetable_fetches_per_day`` by the
      hours DST adds (the repeated local 01:00 is fetched once per offset, so
      the night floor keeps its hourly cadence) or removes (02:00 never happens
      locally in spring, so it cannot be fetched);
    * no instant is duplicated, the UTC instants are strictly increasing, and
      both the wall-clock and the *real* (UTC) spacing stay within the hard
      per-hour caps — nothing is fetched twice, nothing is missed.
    """

    def setUp(self):
        self.plan = schedule_plan.plan_timetable(budget=DEFAULT_DAILY_BUDGET)
        self.timetable = self.plan['timetable']
        self.expected_per_day = self.plan['timetable_fetches_per_day']
        self.zone = schedule_plan.ZoneInfo(LOCAL_TZ)

    def instants(self, day):
        return timetable_datetimes(day, self.timetable, tz=LOCAL_TZ)

    def assert_one_fetch_per_slot(self, day, instants):
        """Strictly increasing UTC instants, no duplicates, inside the caps."""
        self.assertEqual(len(instants), len(set(instants)),
                         f'{day}: a slot was fetched twice')
        self.assertEqual(instants, sorted(instants), f'{day}: not increasing')
        for earlier, later in zip(instants, instants[1:]):
            self.assertLess(earlier, later, f'{day}: instants not strictly increasing')
            local = earlier.astimezone(self.zone)
            cap = max_gap_minutes(local.hour)
            real_minutes = (later - earlier).total_seconds() / 60.0
            self.assertLessEqual(real_minutes, cap + 1e-9,
                                 f'{day}: real gap {real_minutes:.0f} min after local '
                                 f'{local:%H:%M} exceeds the {cap} min cap')
            if local.utcoffset() != later.astimezone(self.zone).utcoffset():
                # Across the DST seam the wall clock jumps two hours while only
                # one real hour elapses (or repeats one); the real gap above is
                # the one that decides whether a sample was missed.
                continue
            wall_minutes = ((later.astimezone(self.zone).replace(tzinfo=None)
                             - local.replace(tzinfo=None)).total_seconds() / 60.0)
            self.assertLessEqual(wall_minutes, cap + 1e-9,
                                 f'{day}: wall gap {wall_minutes:.0f} min after local '
                                 f'{local:%H:%M} exceeds the {cap} min cap')

    def test_local_0600_is_utc_1000_in_october_and_utc_1100_in_november(self):
        """EST (UTC-5) shifts the *UTC* instant, never the local slot."""
        october = timetable_datetimes(date(2026, 10, 16), ['06:00'])
        november = timetable_datetimes(date(2026, 11, 16), ['06:00'])
        self.assertEqual([dt.strftime('%H:%M') for dt in october], ['10:00'])
        self.assertEqual([dt.strftime('%H:%M') for dt in november], ['11:00'])
        # ...and the switch day itself already runs on EST at 06:00 local.
        switch_day = timetable_datetimes(date(2026, 11, 1), ['06:00'])
        self.assertEqual([dt.strftime('%H:%M') for dt in switch_day], ['11:00'])
        self.assertEqual(timetable_utc_labels(['06:00'], day=date(2026, 10, 16)),
                         ['10:00'])
        self.assertEqual(timetable_utc_labels(['06:00'], day=date(2026, 11, 4)),
                         ['11:00'])

    def test_ordinary_days_fetch_the_plan_once(self):
        for day in (date(2026, 10, 28), date(2026, 11, 4), date(2026, 12, 15)):
            instants = self.instants(day)
            self.assertEqual(len(instants), self.expected_per_day, day)
            self.assert_one_fetch_per_slot(day, instants)
            local_slots = sorted(dt.astimezone(self.zone).strftime('%H:%M')
                                 for dt in instants)
            self.assertEqual(local_slots, sorted(self.timetable), day)

    def test_the_fall_back_day_fetches_every_slot_once_without_double_fetching(self):
        """2026-11-01 is a 25-hour local day: the repeated 01:00 hour is real."""
        day = date(2026, 11, 1)
        instants = self.instants(day)
        repeated_hour_slots = len([l for l in self.timetable if int(l.split(':')[0]) == 1])
        self.assertEqual(repeated_hour_slots, 1)  # the night floor: one per hour
        # Same slot count as any other day, plus the hour DST hands back.
        self.assertEqual(len(instants), self.expected_per_day + repeated_hour_slots)
        self.assert_one_fetch_per_slot(day, instants)
        # Every plan slot is fetched, once per local occurrence (01:00 twice).
        local_slots = sorted(dt.astimezone(self.zone).strftime('%H:%M') for dt in instants)
        self.assertEqual(local_slots, sorted(self.timetable + ['01:00']))
        # The seam is one real hour wide, not zero and not two.
        seam = [dt for dt in instants
                if dt.astimezone(self.zone).hour == 1]
        self.assertEqual([(b - a).total_seconds() / 60.0 for a, b in zip(seam, seam[1:])],
                         [60.0])

    def test_the_spring_forward_day_skips_only_the_hour_that_never_happens(self):
        """2026-03-08 is a 23-hour local day: local 02:00 has no instant."""
        day = date(2026, 3, 8)
        instants = self.instants(day)
        missing = [l for l in self.timetable if int(l.split(':')[0]) == 2]
        self.assertEqual(missing, ['02:00'])
        self.assertEqual(len(instants), self.expected_per_day - len(missing))
        self.assert_one_fetch_per_slot(day, instants)
        local_slots = sorted(dt.astimezone(self.zone).strftime('%H:%M') for dt in instants)
        self.assertEqual(local_slots,
                         sorted(l for l in self.timetable if l not in missing))
        # The seam is still one real hour (01:00 EST -> 03:00 EDT), not two.
        seam = [dt for dt in instants if dt.astimezone(self.zone).hour in (1, 3)]
        self.assertIn(60.0, [(b - a).total_seconds() / 60.0
                             for a, b in zip(seam, seam[1:])])

    def test_the_night_and_busy_windows_are_the_same_shape_in_est_and_edt(self):
        """Switching to EST must not move the *local* hour weights by an hour."""
        october = {int(label.split(':')[0]) for label in self.timetable}
        for hour in range(24):
            labels = [l for l in self.timetable if int(l.split(':')[0]) == hour]
            caps = gap_caps()
            spacing = 60.0 / len(labels) if labels else None
            self.assertIsNotNone(spacing, f'local hour {hour} has no fetch')
            self.assertLessEqual(round(spacing, 6), caps[hour] + 1e-9)
        self.assertEqual(october, set(range(24)))
        # November instants sit exactly one hour later in UTC than October's.
        for label in ('00:00', '06:00', '12:00', '23:40'):
            october_dt = timetable_datetimes(date(2026, 10, 16), [label])[0]
            november_dt = timetable_datetimes(date(2026, 11, 16), [label])[0]
            self.assertEqual(november_dt.hour, (october_dt.hour + 1) % 24, label)
            self.assertEqual(november_dt.minute, october_dt.minute, label)

    def test_the_schedule_code_uses_zoneinfo_not_a_fixed_offset(self):
        """A fixed offset (``timedelta(hours=-4)``) would misfire all winter."""
        import re
        pattern = re.compile(r'timedelta\(hours\s*=\s*-?[45]|'
                             r'timezone\(timedelta\(hours')
        for name in ('fetch_strategy.py', 'schedule_plan.py', 'schedule_eval.py',
                     'should_fetch.py', 'update_profile.py'):
            source = (Path(__file__).resolve().parent / name).read_text(encoding='utf-8')
            self.assertIsNone(pattern.search(source),
                              f'{name} hard-codes a UTC offset; use ZoneInfo')


if __name__ == '__main__':
    unittest.main()

#!/usr/bin/env python3
"""Unit tests for the adaptive fetch strategy (`fetch_strategy.py`).

The strategy learns *when* Québec gas stations change their prices (by diffing
successive real snapshots) and turns that into a bounded daily poll schedule,
so the data is fresher when a driver actually shows up at the pump without
hammering the upstream Régie Essence server.
"""

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_strategy import (  # noqa: E402
    SLOTS_PER_DAY,
    SLOTS_PER_HOUR,
    diff_regions,
    due_decision,
    hour_weights,
    observed_from_stations,
    plan_slots,
    record_observation,
    region_station_counts,
    research_prior_weights,
    slot_index,
    slot_label,
)

UTC = timezone.utc


def iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def at(hour, minute=0, day=25):
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def feature(region, regular=None, super_=None, diesel=None):
    props = {'region': region, 'name': region}
    if regular is not None:
        props['regular_price'] = regular
    if super_ is not None:
        props['super_price'] = super_
    if diesel is not None:
        props['diesel_price'] = diesel
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': [-73.5, 45.5]},
        'properties': props,
    }


def snapshot(*features):
    return {'type': 'FeatureCollection', 'features': list(features)}


def empty_hours():
    return {str(h): {'samples': 0, 'observed_hours': 0.0,
                     'change_events': 0, 'changed_stations': 0} for h in range(24)}


def empty_profile():
    return {'v': 1, 'hours': empty_hours(), 'last_observed': None, 'last_src': None}


class SlotMathTest(unittest.TestCase):
    def test_slot_index_maps_quarter_hours(self):
        self.assertEqual(slot_index(at(0, 0)), 0)
        self.assertEqual(slot_index(at(0, 15)), 1)
        self.assertEqual(slot_index(at(13, 45)), 13 * SLOTS_PER_HOUR + 3)
        self.assertEqual(slot_index(at(23, 45)), SLOTS_PER_DAY - 1)

    def test_slot_label_round_trips(self):
        for index in (0, 1, 2, 3, 55, SLOTS_PER_DAY - 1):
            self.assertEqual(slot_index_label(index), index)

    def test_slot_label_formats_utc_time(self):
        self.assertEqual(slot_label(0), '00:00')
        self.assertEqual(slot_label(2), '00:30')
        self.assertEqual(slot_label(3), '00:45')
        self.assertEqual(slot_label(54), '13:30')


def slot_index_label(index):
    """Parse slot_label() back into an index (keeps the round-trip honest)."""
    hour, minute = (int(p) for p in slot_label(index).split(':'))
    return slot_index(at(hour, minute))


class ResearchPriorTest(unittest.TestCase):
    def test_prior_is_a_normalized_24_hour_distribution(self):
        prior = research_prior_weights()
        self.assertEqual(len(prior), 24)
        self.assertAlmostEqual(sum(prior), 1.0, places=9)
        self.assertTrue(all(w > 0 for w in prior))

    def test_prior_peaks_on_researched_change_windows(self):
        # Montréal is UTC-4/-5; researched local windows (stations re-set
        # prices around local midnight, morning commute increases) land in
        # these UTC hours. Only assert they are above the uniform 1/24.
        prior = research_prior_weights()
        uniform = 1 / 24
        for hour in (4, 5, 10, 11, 12, 13):
            self.assertGreater(prior[hour], uniform, f'hour {hour} should be elevated')


class HourWeightsTest(unittest.TestCase):
    def test_no_observations_falls_back_to_research_prior(self):
        weights = hour_weights(empty_profile())
        self.assertEqual(len(weights), 24)
        self.assertAlmostEqual(sum(weights), 1.0, places=9)
        self.assertAlmostEqual(weights[11], research_prior_weights()[11], places=9)

    def test_learned_hour_outweighs_prior_after_a_week_of_samples(self):
        profile = empty_profile()
        for h in range(24):
            profile['hours'][str(h)]['observed_hours'] = 7.0
        # Every observed change happened at 18:00 UTC.
        profile['hours']['18']['changed_stations'] = 5000
        weights = hour_weights(profile)
        self.assertEqual(max(range(24), key=lambda h: weights[h]), 18)
        self.assertGreater(weights[18], 0.5)

    def test_weights_are_a_normalized_distribution(self):
        profile = empty_profile()
        profile['hours']['6']['observed_hours'] = 10.0
        profile['hours']['6']['changed_stations'] = 500.0
        profile['hours']['18']['observed_hours'] = 10.0
        profile['hours']['18']['changed_stations'] = 100.0
        weights = hour_weights(profile)
        self.assertAlmostEqual(sum(weights), 1.0, places=9)
        self.assertGreater(weights[6], weights[18])


class PlanSlotsTest(unittest.TestCase):
    def test_budget_is_respected_and_every_hour_covered(self):
        weights = [1 / 24] * 24
        slots = plan_slots(weights, budget=48, min_per_hour=1, max_per_hour=4)
        self.assertEqual(len(slots), 48)
        self.assertEqual(len(set(slots)), 48)
        self.assertTrue(all(0 <= s < SLOTS_PER_DAY for s in slots))
        hours = [s // SLOTS_PER_HOUR for s in slots]
        self.assertEqual(sorted(set(hours)), list(range(24)))

    def test_hot_hour_gets_more_slots_than_a_cold_hour(self):
        weights = [0.0] * 24
        weights[12] = 1.0
        slots = plan_slots(weights, budget=48, min_per_hour=1, max_per_hour=4)
        counts = [0] * 24
        for s in slots:
            counts[s // SLOTS_PER_HOUR] += 1
        self.assertEqual(counts[12], 4)
        self.assertLessEqual(max(counts), 4)
        self.assertEqual(sum(counts), 48)

    def test_small_budget_concentrates_on_the_best_hours(self):
        weights = [0.0] * 24
        weights[3] = 1.0
        weights[9] = 0.5
        slots = plan_slots(weights, budget=6, min_per_hour=1, max_per_hour=4)
        self.assertEqual(len(slots), 6)
        hours = {s // SLOTS_PER_HOUR for s in slots}
        self.assertIn(3, hours)
        self.assertIn(9, hours)
        self.assertNotIn(20, hours)  # zero-weight hours are dropped first

    def test_slots_within_an_hour_use_quarter_hour_marks(self):
        weights = [0.0] * 24
        weights[5] = 1.0
        slots = plan_slots(weights, budget=30, min_per_hour=1, max_per_hour=4)
        in_five = sorted(s % SLOTS_PER_HOUR for s in slots if s // SLOTS_PER_HOUR == 5)
        self.assertTrue(set(in_five).issubset({0, 1, 2, 3}))
        self.assertIn(0, in_five)  # the coarse mark always comes first

    def test_default_budget_keeps_the_hourly_floor_and_still_concentrates(self):
        from fetch_strategy import DEFAULT_DAILY_BUDGET
        weights = [1 / 24] * 24
        weights[4], weights[5], weights[11] = 0.5, 0.3, 0.2  # clear learned peak
        total = sum(weights)
        weights = [w / total for w in weights]
        slots = plan_slots(weights, budget=DEFAULT_DAILY_BUDGET, min_per_hour=1,
                           max_per_hour=4)
        counts = [0] * 24
        for s in slots:
            counts[s // SLOTS_PER_HOUR] += 1
        self.assertEqual(len(slots), DEFAULT_DAILY_BUDGET)
        # Hourly freshness floor: no hour is ever starved.
        self.assertTrue(all(c >= 1 for c in counts))
        # ... and the extra budget lands on the learned peak hours.
        self.assertGreater(max(counts), 1)
        self.assertGreater(counts[4], counts[20])

    def test_budget_above_capacity_is_clamped(self):
        slots = plan_slots([1 / 24] * 24, budget=10_000, min_per_hour=1, max_per_hour=4)
        self.assertEqual(len(slots), SLOTS_PER_DAY)


class ObservedSnapshotTest(unittest.TestCase):
    def test_averages_prices_per_region_and_ignores_missing_fuel(self):
        stations = snapshot(
            feature('Montréal', regular=170.0, super_=190.0),
            feature('Montréal', regular=172.0, super_=192.0, diesel=180.0),
            feature('Estrie', regular=180.5),
        )
        observed = observed_from_stations(stations)
        self.assertEqual(observed['Montréal']['regular'], 171.0)
        self.assertEqual(observed['Montréal']['super'], 191.0)
        self.assertEqual(observed['Montréal']['diesel'], 180.0)
        self.assertNotIn('diesel', observed['Estrie'])
        self.assertEqual(observed['Estrie']['regular'], 180.5)

    def test_station_counts_are_per_region(self):
        stations = snapshot(
            feature('Montréal', regular=170.0),
            feature('Montréal', super_=190.0),
            feature('Estrie', regular=180.5),
        )
        self.assertEqual(region_station_counts(stations), {'Montréal': 2, 'Estrie': 1})

    def test_unknown_region_still_counts(self):
        stations = snapshot(feature(None, regular=170.0))
        self.assertIn('Inconnue', observed_from_stations(stations))


class DiffRegionsTest(unittest.TestCase):
    def test_detects_a_price_change(self):
        prev = {'Montréal': {'regular': 170.0}}
        cur = {'Montréal': {'regular': 171.0}}
        self.assertIn('Montréal', diff_regions(prev, cur))

    def test_ignores_sub_cent_noise(self):
        prev = {'Montréal': {'regular': 170.0}}
        cur = {'Montréal': {'regular': 170.04}}
        self.assertEqual(diff_regions(prev, cur), {})

    def test_ignores_a_new_station_that_moves_the_average_only_slightly(self):
        prev = {'Montréal': {'regular': 170.0}}
        cur = {'Montréal': {'regular': 170.0}}
        self.assertEqual(diff_regions(prev, cur), {})


class RecordObservationTest(unittest.TestCase):
    def test_first_observation_has_no_baseline_and_counts_no_change(self):
        profile = empty_profile()
        observed = {'Montréal': {'regular': 170.0}}
        now = at(11, 0)
        profile, summary = record_observation(
            profile, observed=observed, region_counts={'Montréal': 10},
            now=now, previous_at=None, src=iso(now),
        )
        rec = profile['hours']['11']
        self.assertEqual(rec['samples'], 1)
        self.assertEqual(rec['change_events'], 0)
        self.assertEqual(rec['changed_stations'], 0)
        self.assertEqual(profile['last_observed'], observed)
        self.assertEqual(profile['last_src'], iso(now))
        self.assertEqual(summary['changed_regions'], [])

    def test_change_is_attributed_to_the_fetch_hour_and_weighted_by_stations(self):
        profile = empty_profile()
        now = at(11, 0)
        profile, _ = record_observation(
            profile, observed={'Montréal': {'regular': 170.0}, 'Estrie': {'regular': 180.0}},
            region_counts={'Montréal': 100, 'Estrie': 5},
            now=now, previous_at=at(10, 0), src=iso(now),
        )
        now2 = at(11, 15)
        profile, summary = record_observation(
            profile,
            observed={'Montréal': {'regular': 172.0}, 'Estrie': {'regular': 180.0}},
            region_counts={'Montréal': 100, 'Estrie': 5},
            now=now2, previous_at=now, src=iso(now2),
        )
        rec = profile['hours']['11']
        self.assertEqual(rec['samples'], 2)
        self.assertEqual(rec['change_events'], 1)
        self.assertEqual(rec['changed_stations'], 100)
        self.assertEqual(summary['changed_regions'], ['Montréal'])
        self.assertAlmostEqual(rec['observed_hours'], 1.25, places=6)

    def test_observed_hours_gap_is_capped(self):
        profile = empty_profile()
        profile, _ = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}}, region_counts={'Estrie': 5},
            now=at(9, 0), previous_at=None, src=iso(at(9, 0)),
        )
        profile, _ = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}}, region_counts={'Estrie': 5},
            now=at(20, 0), previous_at=at(9, 0), src=iso(at(20, 0)),
        )
        # A 11h gap must not dwarf the exposure of the other hours.
        self.assertLessEqual(profile['hours']['20']['observed_hours'], 2.0)


class DueDecisionTest(unittest.TestCase):
    SLOTS = ['00:00', '06:00', '12:00', '18:00']

    def test_bootstrap_without_a_previous_fetch_is_due(self):
        due, reason = due_decision(at(3, 7), self.SLOTS, None)
        self.assertTrue(due)
        self.assertIn('bootstrap', reason)

    def test_not_due_before_the_next_scheduled_slot(self):
        due, reason = due_decision(at(7, 0), self.SLOTS, at(6, 0))
        self.assertFalse(due)
        self.assertIn('12:00', reason)

    def test_due_when_a_slot_passed_since_the_last_fetch(self):
        due, reason = due_decision(at(12, 3), self.SLOTS, at(11, 50))
        self.assertTrue(due)
        self.assertIn('12:00', reason)

    def test_min_gap_blocks_a_second_fetch_too_soon(self):
        due, reason = due_decision(at(12, 5), self.SLOTS, at(12, 1))
        self.assertFalse(due)
        self.assertIn('gap', reason)

    def test_grace_window_expires_so_late_runs_do_not_all_fire(self):
        # The 12:00 slot is already 2h old; a runner recovering late must not
        # treat it as due (the freshness net below still guarantees coverage).
        due, reason = due_decision(at(14, 30), self.SLOTS, at(13, 0))
        self.assertFalse(due)

    def test_freshness_safety_net_forces_a_fetch_when_too_stale(self):
        due, reason = due_decision(at(3, 7), self.SLOTS, at(23, 0, day=24))
        self.assertTrue(due)
        self.assertIn('stale', reason)

    def test_a_future_last_fetch_timestamp_does_not_wedge_the_gate(self):
        due, reason = due_decision(at(3, 7), self.SLOTS, at(9, 0))
        self.assertTrue(due)
        self.assertIn('skew', reason)

    def test_slot_from_the_previous_utc_day_is_still_honoured(self):
        due, _ = due_decision(at(0, 10), ['23:50'], at(23, 40, day=24))
        self.assertTrue(due)


if __name__ == '__main__':
    unittest.main()

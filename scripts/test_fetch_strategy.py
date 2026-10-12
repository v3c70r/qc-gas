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
    BUDGET_ENV_VAR,
    DEFAULT_DAILY_BUDGET,
    DEFAULT_EXPLORE_FRAC,
    DEFAULT_MAX_AGE_MINUTES,
    EXPLORE_FRAC_ENV_VAR,
    MAX_AGE_ENV_VAR,
    SLOTS_PER_DAY,
    SLOTS_PER_HOUR,
    diff_regions,
    diff_stations,
    due_decision,
    env_float,
    env_int,
    hour_weights,
    observed_from_stations,
    observed_hour_totals,
    plan_schedule,
    plan_slots,
    prices_from_row,
    prices_to_row,
    record_observation,
    region_station_counts,
    research_prior_weights,
    slot_index,
    slot_label,
    station_coord_key,
    station_prices,
)

UTC = timezone.utc


def iso(dt):
    return dt.strftime('%Y-%m-%dT%H:%M:%SZ')


def at(hour, minute=0, day=25):
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def feature(region, regular=None, super_=None, diesel=None, coords=(-73.5, 45.5)):
    props = {'region': region, 'name': region}
    if regular is not None:
        props['regular_price'] = regular
    if super_ is not None:
        props['super_price'] = super_
    if diesel is not None:
        props['diesel_price'] = diesel
    return {
        'type': 'Feature',
        'geometry': {'type': 'Point', 'coordinates': list(coords)},
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

    def test_default_budget_uses_one_slot_per_hour(self):
        """At the default budget (24) the plan uses every hourly wake-up (#77)."""
        weights = [1 / 24] * 24
        weights[4], weights[5], weights[11] = 0.5, 0.3, 0.2  # a clear learned peak
        total = sum(weights)
        weights = [w / total for w in weights]
        slots = plan_slots(weights, budget=DEFAULT_DAILY_BUDGET, min_per_hour=1,
                           max_per_hour=4)
        self.assertEqual(len(slots), DEFAULT_DAILY_BUDGET)
        # Budget == the hourly floor, so the learned weights cannot prune any
        # hour: every hour keeps exactly one slot (the coarse :00 mark).
        per_hour = [sum(1 for s in slots if s // SLOTS_PER_HOUR == h)
                    for h in range(24)]
        self.assertEqual(per_hour, [1] * 24)
        self.assertEqual(slots, [h * SLOTS_PER_HOUR for h in range(24)])

    def test_below_the_default_budget_the_learned_weights_still_prune(self):
        weights = [1 / 24] * 24
        weights[4], weights[5], weights[11] = 0.5, 0.3, 0.2  # clear learned peak
        total = sum(weights)
        weights = [w / total for w in weights]
        slots = plan_slots(weights, budget=48, min_per_hour=1, max_per_hour=4)
        counts = [0] * 24
        for s in slots:
            counts[s // SLOTS_PER_HOUR] += 1
        self.assertEqual(len(slots), 48)
        # Hourly freshness floor: no hour is ever starved.
        self.assertTrue(all(c >= 1 for c in counts))
        # ... and the extra budget lands on the learned peak hours.
        self.assertGreater(max(counts), 1)
        self.assertGreater(counts[4], counts[20])

    def test_budget_above_capacity_is_clamped(self):
        slots = plan_slots([1 / 24] * 24, budget=10_000, min_per_hour=1, max_per_hour=4)
        self.assertEqual(len(slots), SLOTS_PER_DAY)


class ExplorePlanningTest(unittest.TestCase):
    """Issue #69: reserve epsilon of the budget to probe under-sampled hours.

    The learner could only ever exploit the hours it had already seen: the
    gate kept landing on the busiest windows (§``data/update-profile.json``
    had ``observed_hours == 0`` for 03:00 and 08:00) so their weights were
    frozen at the research prior. Exploration spends a fixed fraction of the
    budget on the hours with the least observation time.
    """

    def weighted(self):
        # 12:00 is clearly the hot hour, 3 & 8 never observed at all.
        weights = [1 / 24] * 24
        weights[12] = 0.5
        total = sum(weights)
        return [w / total for w in weights]

    def observed(self, least=(3, 8)):
        return [0.0 if h in least else 12.0 for h in range(24)]

    def test_defaults_are_documented_and_env_overridable(self):
        self.assertEqual(DEFAULT_EXPLORE_FRAC, 0.15)
        self.assertEqual(EXPLORE_FRAC_ENV_VAR, 'FETCH_EXPLORE_FRAC')

    def test_zero_explore_fraction_is_exactly_the_legacy_plan(self):
        weights = self.weighted()
        observed = self.observed()
        slots, explore = plan_schedule(weights, budget=48, min_per_hour=1,
                                       max_per_hour=4, observed_hours=observed,
                                       explore_frac=0)
        self.assertEqual(explore, [])
        self.assertEqual(slots, plan_slots(weights, budget=48, min_per_hour=1,
                                           max_per_hour=4))

    def test_missing_observed_hours_disables_exploration(self):
        # The research-prior fallback has nothing to explore from.
        slots, explore = plan_schedule([1 / 24] * 24, budget=48, min_per_hour=1,
                                       max_per_hour=4, observed_hours=None)
        self.assertEqual(explore, [])
        self.assertEqual(slots, plan_slots([1 / 24] * 24, budget=48, min_per_hour=1,
                                           max_per_hour=4))

    def test_explore_slots_land_on_the_least_observed_hours(self):
        slots, explore = plan_schedule(self.weighted(), budget=48, min_per_hour=1,
                                       max_per_hour=4, observed_hours=self.observed(),
                                       explore_frac=0.08)
        self.assertEqual(len(slots), 48)
        self.assertTrue(explore)
        # Every explore slot probes an hour with zero observation time.
        self.assertTrue(all(s // SLOTS_PER_HOUR in (3, 8) for s in explore))

    def test_explore_slots_are_identifiable_and_never_duplicated(self):
        slots, explore = plan_schedule(self.weighted(), budget=48, min_per_hour=1,
                                       max_per_hour=4, observed_hours=self.observed(),
                                       explore_frac=DEFAULT_EXPLORE_FRAC)
        self.assertEqual(len(slots), len(set(slots)))
        self.assertEqual(len(explore), len(set(explore)))
        self.assertTrue(set(explore).issubset(set(slots)))

    def test_explore_uses_about_epsilon_of_the_budget(self):
        budget = 48
        _, explore = plan_schedule(self.weighted(), budget=budget, min_per_hour=1,
                                   max_per_hour=4, observed_hours=self.observed(),
                                   explore_frac=DEFAULT_EXPLORE_FRAC)
        self.assertLessEqual(len(explore), round(budget * DEFAULT_EXPLORE_FRAC))
        self.assertGreaterEqual(len(explore), int(budget * DEFAULT_EXPLORE_FRAC) - 1)

    def test_hot_hour_coverage_loss_stays_within_epsilon(self):
        weights = self.weighted()
        base = plan_slots(weights, budget=48, min_per_hour=1, max_per_hour=4)
        explored, _ = plan_schedule(weights, budget=48, min_per_hour=1,
                                    max_per_hour=4, observed_hours=self.observed(),
                                    explore_frac=DEFAULT_EXPLORE_FRAC)
        self.assertEqual(len(base), len(explored))
        base_counts = [base.count(h * SLOTS_PER_HOUR + m) for h in range(24) for m in (0, 1, 2, 3)]
        new_counts = [explored.count(h * SLOTS_PER_HOUR + m) for h in range(24) for m in (0, 1, 2, 3)]
        # 12:00 is the busiest hour; exploration must not gut it.
        self.assertGreaterEqual(new_counts[12 * SLOTS_PER_HOUR], base_counts[12 * SLOTS_PER_HOUR] - 1)
        lost_total = sum(max(0, b - n) for b, n in zip(base_counts, new_counts))
        self.assertLessEqual(lost_total, round(48 * DEFAULT_EXPLORE_FRAC))

    def test_explore_spreads_across_the_least_observed_hours(self):
        _, explore = plan_schedule([1 / 24] * 24, budget=48, min_per_hour=1,
                                   max_per_hour=4, observed_hours=self.observed(),
                                   explore_frac=0.08)
        probed = {s // SLOTS_PER_HOUR for s in explore}
        self.assertEqual(probed, {3, 8})

    def test_explore_prefers_unused_quarter_marks(self):
        # Hour 3 has one exploitation slot at :00; exploration should probe a
        # different mark so the hour is covered more densely (a run delayed by
        # more than the grace window still lands on a slot).
        observed = [12.0] * 24
        observed[3] = 0.0
        _, explore = plan_schedule([1 / 24] * 24, budget=48, min_per_hour=1,
                                   max_per_hour=4, observed_hours=observed,
                                   explore_frac=0.5)
        marks = {s % SLOTS_PER_HOUR for s in explore if s // SLOTS_PER_HOUR == 3}
        self.assertNotIn(0, marks)
        self.assertTrue(marks)

    def test_explore_fraction_is_clamped(self):
        observed = self.observed()
        _, explore = plan_schedule(self.weighted(), budget=48, min_per_hour=1,
                                   max_per_hour=4, observed_hours=observed,
                                   explore_frac=5.0)
        self.assertLessEqual(len(explore), 48)


class ObservedHourTotalsTest(unittest.TestCase):
    def test_reads_the_per_hour_observation_time(self):
        profile = empty_profile()
        profile['hours']['3']['observed_hours'] = 2.5
        totals = observed_hour_totals(profile)
        self.assertEqual(len(totals), 24)
        self.assertEqual(totals[3], 2.5)
        self.assertEqual(totals[8], 0.0)

    def test_missing_profile_is_all_zero(self):
        self.assertEqual(observed_hour_totals(None), [0.0] * 24)


class EnvFloatTest(unittest.TestCase):
    """One float override helper for the exploration fraction (issue #69)."""

    def test_reads_a_fraction(self):
        self.assertEqual(env_float('X', 0.15, environ={'X': '0.25'}), 0.25)

    def test_zero_disables_exploration_and_is_honoured(self):
        self.assertEqual(env_float('X', 0.15, environ={'X': '0'}), 0.0)

    def test_falls_back_on_missing_or_invalid_values(self):
        self.assertEqual(env_float('X', 0.15, environ={}), 0.15)
        self.assertEqual(env_float('X', 0.15, environ={'X': 'nonsense'}), 0.15)
        self.assertEqual(env_float('X', 0.15, environ={'X': '-0.2'}), 0.15)
        self.assertEqual(env_float('X', 0.15, environ={'X': '1.5'}), 0.15)


class FetchDefaultsTest(unittest.TestCase):
    """Issue #77: the shipped rhythm follows the *measured* trigger cadence."""

    def test_default_budget_is_one_poll_per_hourly_wake_up(self):
        # The fallback cron is hourly (`static.yml`), so the day holds 24
        # wake-ups and the default budget spends exactly one request each.
        # Sub-hourly crons are throttled away on this repo (measured p50 ~4.3 h
        # for `*/15`), which is why the sub-hourly cadence comes from the
        # external `repository_dispatch` trigger instead.
        self.assertEqual(SLOTS_PER_DAY, 96)
        self.assertEqual(DEFAULT_DAILY_BUDGET, SLOTS_PER_DAY // SLOTS_PER_HOUR)
        self.assertEqual(DEFAULT_DAILY_BUDGET, 24)

    def test_default_freshness_net_is_two_hourly_wakes(self):
        self.assertEqual(DEFAULT_MAX_AGE_MINUTES, 120)
        # Exactly 120 minutes without a fetch is still considered fresh (the net
        # must not be denser than the hourly plan)...
        due, reason = due_decision(at(3, 0), ['00:00'], at(1, 0))
        self.assertFalse(due, reason)
        # ...but one minute past it, it is stale.
        due, reason = due_decision(at(3, 1), ['00:00'], at(1, 0))
        self.assertTrue(due)
        self.assertIn('stale', reason)

    def test_freshness_threshold_is_used_verbatim(self):
        due, _ = due_decision(at(3, 7), ['00:00'], at(1, 0), max_age_minutes=180)
        self.assertFalse(due)


class EnvIntTest(unittest.TestCase):
    """One env override helper shared by both CLIs (issue #67)."""

    def test_env_var_names_are_stable(self):
        self.assertEqual(BUDGET_ENV_VAR, 'FETCH_DAILY_BUDGET')
        self.assertEqual(MAX_AGE_ENV_VAR, 'FETCH_MAX_AGE_MINUTES')

    def test_reads_a_positive_integer_override(self):
        self.assertEqual(env_int('X', 96, environ={'X': '12'}), 12)

    def test_falls_back_on_missing_or_invalid_values(self):
        self.assertEqual(env_int('X', 96, environ={}), 96)
        self.assertEqual(env_int('X', 96, environ={'X': 'nonsense'}), 96)
        self.assertEqual(env_int('X', 96, environ={'X': '0'}), 96)
        self.assertEqual(env_int('X', 96, environ={'X': '-4'}), 96)


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


class StationKeyTest(unittest.TestCase):
    def test_key_is_the_coordinates_rounded_to_five_decimals(self):
        feat = feature('Montréal', regular=170.0, coords=(-79.019574, 48.246759))
        self.assertEqual(station_coord_key(feat), '-79.01957,48.24676')

    def test_key_is_none_without_usable_coordinates(self):
        self.assertIsNone(station_coord_key({'geometry': None, 'properties': {}}))
        self.assertIsNone(station_coord_key(
            {'geometry': {'coordinates': [None]}, 'properties': {}}))

    def test_key_is_stable_across_snapshots(self):
        a = feature('Montréal', regular=170.0, coords=(-73.5, 45.5))
        b = feature('Montréal', regular=171.0, coords=(-73.5000001, 45.5000002))
        self.assertEqual(station_coord_key(a), station_coord_key(b))


class StationPricesTest(unittest.TestCase):
    def test_prices_are_keyed_by_coordinates_with_short_fuel_keys(self):
        stations = snapshot(
            feature('Montréal', regular=170.0, super_=190.0, coords=(-73.5, 45.5)),
            feature('Estrie', regular=180.5, coords=(-71.9, 45.4)),
        )
        prices = station_prices(stations)
        self.assertEqual(prices['-73.50000,45.50000'], {'g': 170.0, 's': 190.0})
        self.assertEqual(prices['-71.90000,45.40000'], {'g': 180.5})

    def test_missing_coordinates_are_skipped(self):
        stations = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': None, 'properties': {'regular_price': 170.0}},
        ]}
        self.assertEqual(station_prices(stations), {})

    def test_row_round_trips_gas_super_diesel(self):
        prices = {'g': 170.0, 'd': 180.0}
        self.assertEqual(prices_to_row(prices), [170.0, None, 180.0])
        self.assertEqual(prices_from_row(prices_to_row(prices)), prices)
        self.assertEqual(prices_to_row({}), [None, None, None])


class DiffStationsTest(unittest.TestCase):
    def test_detects_a_single_station_change(self):
        prev = {'a,b': {'g': 170.0}}
        cur = {'a,b': {'g': 175.0}}
        self.assertEqual(diff_stations(prev, cur), {'a,b': ['g']})

    def test_cancelling_moves_in_one_region_are_both_detected(self):
        # A station +5c and its neighbour -5c leave the region average flat;
        # the station-level diff must still report both.
        prev = {'a,b': {'g': 170.0}, 'c,d': {'g': 180.0}}
        cur = {'a,b': {'g': 175.0}, 'c,d': {'g': 175.0}}
        self.assertEqual(diff_stations(prev, cur), {'a,b': ['g'], 'c,d': ['g']})

    def test_ignores_sub_epsilon_noise(self):
        prev = {'a,b': {'g': 170.0}}
        cur = {'a,b': {'g': 170.04}}
        self.assertEqual(diff_stations(prev, cur), {})

    def test_epsilon_is_the_inclusive_threshold(self):
        prev = {'a,b': {'g': 170.0}}
        cur = {'a,b': {'g': 170.05}}
        self.assertEqual(diff_stations(prev, cur), {'a,b': ['g']})

    def test_new_station_has_no_baseline_and_is_not_a_change(self):
        self.assertEqual(diff_stations({}, {'a,b': {'g': 170.0}}), {})

    def test_disappearing_fuel_is_an_availability_change_not_a_price_change(self):
        prev = {'a,b': {'g': 170.0, 's': 190.0}}
        cur = {'a,b': {'g': 170.0}}
        self.assertEqual(diff_stations(prev, cur), {})

    def test_appearing_fuel_is_not_a_change(self):
        prev = {'a,b': {'g': 170.0}}
        cur = {'a,b': {'g': 170.0, 'd': 200.0}}
        self.assertEqual(diff_stations(prev, cur), {})

    def test_each_fuel_is_reported_independently(self):
        prev = {'a,b': {'g': 170.0, 's': 190.0, 'd': 200.0}}
        cur = {'a,b': {'g': 171.0, 's': 190.0, 'd': 205.0}}
        self.assertEqual(diff_stations(prev, cur), {'a,b': ['g', 'd']})

    def test_a_vanished_station_is_not_reported(self):
        prev = {'a,b': {'g': 170.0}, 'c,d': {'g': 180.0}}
        cur = {'a,b': {'g': 170.0}}
        self.assertEqual(diff_stations(prev, cur), {})

    def test_region_average_is_not_diluted_by_large_regions(self):
        # 140 flat stations plus one +5c move: the region average moves only
        # ~0.04c (below PRICE_EPSILON), but the station diff must catch it.
        prev = {f'k{i}': {'g': 170.0} for i in range(140)}
        cur = dict(prev)
        cur['k0'] = {'g': 175.0}
        self.assertEqual(diff_stations(prev, cur), {'k0': ['g']})
        self.assertEqual(diff_regions(
            {'R': {'regular': 170.0}}, {'R': {'regular': 170.04}}), {})


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

    def test_exact_station_counts_are_folded_in_next_to_the_region_ones(self):
        profile = empty_profile()
        profile, _ = record_observation(
            profile, observed={'Montréal': {'regular': 170.0}},
            region_counts={'Montréal': 140}, now=at(11, 0), previous_at=None,
            station_changes=0, stations_observed=2463,
        )
        profile, summary = record_observation(
            profile, observed={'Montréal': {'regular': 170.04}},
            region_counts={'Montréal': 140}, now=at(11, 15), previous_at=at(11, 0),
            station_changes=2, stations_observed=2463,
        )
        rec = profile['hours']['11']
        self.assertEqual(rec['changed_stations_exact'], 2)
        self.assertEqual(rec['stations_observed'], 2463)
        self.assertEqual(rec['changed_stations'], 0)  # region average never moved
        self.assertEqual(summary['changed_stations_exact'], 2)

    def test_omitting_station_counts_keeps_the_region_only_record(self):
        profile = empty_profile()
        profile, summary = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}}, region_counts={'Estrie': 5},
            now=at(9, 0), previous_at=None,
        )
        self.assertEqual(profile['hours']['9']['changed_stations_exact'], 0)
        self.assertEqual(summary['changed_stations_exact'], 0)


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

    def test_explore_slot_is_flagged_in_the_reason(self):
        due, reason = due_decision(at(12, 3), self.SLOTS, at(11, 50))
        self.assertTrue(due)
        self.assertNotIn('explore', reason)
        due, reason = due_decision(at(12, 3), self.SLOTS, at(11, 50),
                                   explore_slots=['12:00'])
        self.assertTrue(due)
        self.assertIn('[explore]', reason)


if __name__ == '__main__':
    unittest.main()

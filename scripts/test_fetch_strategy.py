#!/usr/bin/env python3
"""Unit tests for the adaptive fetch strategy (`fetch_strategy.py`).

The strategy learns *when* Québec gas stations change their prices (by diffing
successive real snapshots) and turns that into a bounded daily poll schedule,
so the data is fresher when a driver actually shows up at the pump without
hammering the upstream Régie Essence server.
"""

import sys
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fetch_strategy import (  # noqa: E402
    BUDGET_ENV_VAR,
    DEFAULT_DAILY_BUDGET,
    DEFAULT_EXPLORE_FRAC,
    DEFAULT_MAX_AGE_MINUTES,
    EXACT_READY_SAMPLES,
    EXPLORE_FRAC_ENV_VAR,
    MARK_MIN_SAMPLES,
    MAX_AGE_ENV_VAR,
    REGION_PRIOR_HOURS,
    SLOTS_PER_DAY,
    SLOTS_PER_HOUR,
    coverage_hours,
    diff_regions,
    diff_stations,
    due_decision,
    env_float,
    env_int,
    exact_channel_ready,
    format_timetable,
    gap_caps,
    hour_rates,
    hour_weights,
    learned_mark_orders,
    local_zone,
    max_gap_minutes,
    observed_from_stations,
    observed_hour_totals,
    parse_timetable,
    plan_schedule,
    plan_slots,
    prices_from_row,
    prices_to_row,
    record_observation,
    region_hour_weights,
    region_station_counts,
    research_prior_weights,
    slot_hour_counts,
    slot_index,
    slot_indices,
    slot_label,
    spread_offsets,
    station_coord_key,
    station_prices,
    station_regions,
    timetable_datetimes,
    timetable_utc_labels,
    validate_timetable,
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

    def test_a_hourly_budget_still_uses_one_slot_per_hour(self):
        """An explicit hourly budget keeps the pre-#91 shape (issue #77)."""
        weights = [1 / 24] * 24
        weights[4], weights[5], weights[11] = 0.5, 0.3, 0.2  # a clear learned peak
        total = sum(weights)
        weights = [w / total for w in weights]
        slots = plan_slots(weights, budget=24, min_per_hour=1, max_per_hour=4)
        self.assertEqual(len(slots), 24)
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
    """Issue #77 measured the *old* trigger cadence; #91 spends the budget."""

    def test_default_budget_buys_the_hybrid_timetable(self):
        # Issue #91: 4.1 days of 15-minute probe observations (#74) show a 121x
        # spread between the local midnight batch (486 re-priced stations/day)
        # and the 01:00-05:00 dead zone (4-23/day). The shipped plan spends 65
        # requests/day on that histogram: busy 06:00-23:00 every 20 min, local
        # midnight every 10 min and one fetch per hour at night, which reproduces
        # p90 18 min / stale share 1.2 % on the measured series (criterion: p90
        # <= 30 min, share <= 5 %).
        self.assertEqual(SLOTS_PER_DAY, 96)
        self.assertEqual(DEFAULT_DAILY_BUDGET, 65)

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


def one_hot(*hours):
    """24-length weight vector with mass on the given hours (test helper)."""
    weights = [0.0] * 24
    for h in hours:
        weights[h] = 1.0
    total = sum(weights)
    return [w / total for w in weights]


def region_hour(changed_stations, observed_hours, samples=4, change_events=None):
    """One region-hour record: stations that re-priced over the exposure."""
    return {
        'samples': samples,
        'observed_hours': float(observed_hours),
        'change_events': samples if change_events is None else change_events,
        'changed_stations': changed_stations,
    }


def region_block(stations, hours):
    """profile['regions'][name] block; ``hours`` maps hour (int) -> record."""
    return {'stations': stations,
            'hours': {str(h): rec for h, rec in hours.items()}}


def saturated_profile(changed_stations=1000.0, observed_hours=2.0, exact=None):
    """Global profile whose region-average channel is perfectly flat.

    ``changed_stations`` is the legacy region-average estimate: whole regions
    count in full as soon as one of their fuel averages moved, so in practice it
    is (nearly) the same number in every hour - the saturation measured on the
    live profile (745-1235 stations, 1.65x). ``exact`` optionally adds the
    per-station counters of issue #68.
    """
    profile = empty_profile()
    for h in range(24):
        rec = profile['hours'][str(h)]
        rec.update(samples=4, observed_hours=observed_hours, change_events=4,
                   changed_stations=changed_stations)
        if exact is not None:
            rec.update(exact_samples=4, stations_observed=2463,
                       changed_stations_exact=exact.get(h, 100))
    return profile


class StationRegionsTest(unittest.TestCase):
    def test_maps_every_priced_station_key_to_its_region(self):
        stations = snapshot(
            feature('Montréal', regular=170.0, coords=(-73.5, 45.5)),
            feature('Estrie', regular=180.0, coords=(-71.9, 45.4)),
        )
        self.assertEqual(station_regions(stations),
                         {'-73.50000,45.50000': 'Montréal', '-71.90000,45.40000': 'Estrie'})

    def test_unkeyable_stations_are_skipped(self):
        stations = {'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'geometry': None, 'properties': {'region': 'Estrie'}},
        ]}
        self.assertEqual(station_regions(stations), {})


class HourRatesTest(unittest.TestCase):
    """Expected changed stations per hour, and the detector that measures it."""

    def test_region_channel_counts_a_whole_region_when_its_average_moves(self):
        rates, observed = hour_rates(saturated_profile(), channel='region')
        self.assertEqual(rates, [500.0] * 24)
        self.assertEqual(observed, 48.0)

    def test_region_channel_saturates_at_a_flat_distribution(self):
        rates, _ = hour_rates(saturated_profile(), channel='region')
        self.assertLessEqual(max(rates) / min(rates), 1.65)

    def test_exact_channel_preserves_the_real_hourly_contrast(self):
        profile = saturated_profile(exact={4: 5000, 17: 200})
        rates, _ = hour_rates(profile, channel='exact')
        self.assertEqual(max(range(24), key=lambda h: rates[h]), 4)
        self.assertGreater(max(rates) / min(rates), 1.65)

    def test_unobserved_hours_have_no_rate(self):
        profile = saturated_profile()
        profile['hours']['3']['observed_hours'] = 0.0
        rates, _ = hour_rates(profile, channel='region')
        self.assertEqual(rates[3], 0.0)

    def test_exact_channel_is_ready_only_once_it_covers_every_observed_hour(self):
        profile = saturated_profile(exact={})
        self.assertTrue(exact_channel_ready(profile))
        profile['hours']['7']['exact_samples'] = 0
        self.assertFalse(exact_channel_ready(profile))

    def test_exact_channel_is_not_ready_on_a_thin_sample(self):
        profile = saturated_profile(exact={})
        for h in range(24):
            profile['hours'][str(h)]['exact_samples'] = 1
        self.assertLess(24, EXACT_READY_SAMPLES)
        self.assertFalse(exact_channel_ready(profile))

    def test_auto_switches_to_exact_only_once_it_is_ready(self):
        profile = saturated_profile(exact={4: 5000})
        exact_rates, _ = hour_rates(profile, channel='exact')
        self.assertEqual(hour_rates(profile)[0], exact_rates)
        profile['hours']['7']['exact_samples'] = 0
        region_rates, _ = hour_rates(profile, channel='region')
        self.assertEqual(hour_rates(profile)[0], region_rates)


class RegionLayeredWeightsTest(unittest.TestCase):
    """Hierarchical shrinkage: little regional exposure -> global weights."""

    def base_profile(self):
        return saturated_profile()

    def test_a_region_without_observations_uses_the_global_distribution(self):
        profile = self.base_profile()
        profile['regions'] = {'Estrie': {'stations': 10, 'hours': {}}}
        self.assertEqual(hour_weights(profile, region='Estrie'), hour_weights(profile))

    def test_a_region_with_little_exposure_stays_close_to_the_global_distribution(self):
        profile = self.base_profile()
        base = hour_weights(profile)
        # One quiet fetch hour in the region: exposure far below
        # REGION_PRIOR_HOURS, so the posterior must stay at the global weights.
        profile['regions'] = {'Estrie': region_block(
            300, {3: region_hour(10, 0.25, samples=1, change_events=1)})}
        weights = hour_weights(profile, region='Estrie')
        self.assertEqual(len(weights), 24)
        self.assertLess(abs(weights[3] - base[3]), 0.02)
        self.assertAlmostEqual(sum(weights), 1.0, places=9)

    def test_a_region_with_plenty_of_exposure_follows_its_own_peak(self):
        profile = self.base_profile()
        # Four days of 15-minute fetches at 04:00 and 11:00 UTC: 10 of the 78
        # stations re-price at 04:00, one fuel at 11:00.
        profile['regions'] = {'Nord-du-Quebec': region_block(78, {
            4: region_hour(160, 4.0, samples=16, change_events=16),
            11: region_hour(8, 4.0, samples=16, change_events=2),
        })}
        weights = hour_weights(profile, region='Nord-du-Quebec')
        self.assertEqual(max(range(24), key=lambda h: weights[h]), 4)
        self.assertGreater(weights[4], hour_weights(profile)[4])
        self.assertGreater(REGION_PRIOR_HOURS, 8.0)  # still a shrunk estimate

    def test_region_weights_are_a_normalized_distribution(self):
        profile = self.base_profile()
        profile['regions'] = {'Estrie': region_block(300, {
            18: region_hour(500, 10.0, samples=40)})}
        weights = hour_weights(profile, region='Estrie')
        self.assertAlmostEqual(sum(weights), 1.0, places=9)
        self.assertTrue(all(w >= 0 for w in weights))

    def test_province_weights_learn_from_the_regions_not_the_region_average(self):
        profile = self.base_profile()
        base = hour_weights(profile)
        # Two regions with complementary peaks: the province expectation must
        # rise in both hours even though the region-average channel is flat.
        profile['regions'] = {
            'Montreal': region_block(678, {4: region_hour(400, 4.0)}),
            'Gaspesie': region_block(177, {17: region_hour(80, 4.0)}),
        }
        weights = hour_weights(profile)
        self.assertGreater(weights[4], base[4])
        self.assertGreater(weights[17], base[17])
        self.assertAlmostEqual(sum(weights), 1.0, places=9)

    def test_legacy_profile_without_regions_is_unchanged(self):
        profile = saturated_profile()
        weights = hour_weights(profile)
        self.assertEqual(len(weights), 24)
        self.assertAlmostEqual(sum(weights), 1.0, places=9)

    def test_region_hour_weights_covers_every_observed_region(self):
        profile = self.base_profile()
        profile['regions'] = {
            'Estrie': region_block(300, {18: region_hour(500, 10.0)}),
            'Laval': {'stations': 210, 'hours': {}},
        }
        weights = region_hour_weights(profile)
        self.assertEqual(sorted(weights), ['Estrie'])  # Laval has no observations yet
        self.assertAlmostEqual(sum(weights['Estrie']), 1.0, places=9)


class RegionCoverageTest(unittest.TestCase):
    def test_coverage_is_the_union_of_the_regional_peak_hours(self):
        weights = {
            'A': one_hot(3, 5, 9),
            'B': one_hot(5, 20),
            'C': one_hot(0),
        }
        # A peaks at 3 and 5, B at 5 and 20, C at 0: the union is what must be
        # polled (each region contributes its top REGION_PEAK_HOURS hours).
        self.assertEqual(sorted(coverage_hours(weights, top=2)), [0, 3, 5, 20])

    def test_a_region_that_follows_the_province_pattern_pins_no_hours(self):
        flat = [1 / 24] * 24
        self.assertEqual(coverage_hours({'Montréal': flat}, baseline=flat), [])

    def test_a_region_without_a_hot_hour_pins_nothing(self):
        # Below REGION_PEAK_RATIO x its own uniform share: no genuine peak.
        baseline = [0.25 / 23] * 24
        baseline[4] = 0.75
        flat = [0.9 / 24] * 24
        self.assertEqual(coverage_hours({'Côte-Nord': flat}, baseline=baseline), [])

    def test_an_hour_must_stand_out_from_the_province_to_be_pinned(self):
        baseline = [0.25 / 23] * 24
        baseline[4] = 0.75                       # the province hot hour: 04:00
        slight = list(baseline)
        slight[4] *= 1.1                         # only ~1.1x the province share
        clear = [0.0] * 24
        clear[22], clear[4] = 0.7, 0.3           # 22:00 is this region's own peak
        self.assertEqual(coverage_hours({'A': slight}, baseline=baseline), [])
        self.assertEqual(coverage_hours({'B': clear}, baseline=baseline), [22])

    def test_pinned_hours_are_ranked_by_how_distinctive_they_are(self):
        baseline = [0.25 / 23] * 24
        baseline[4] = 0.75
        mild = [0.0] * 24
        mild[9], mild[4] = 0.4, 0.6              # 9:00 stands out the most
        strong = [0.0] * 24
        strong[22], strong[4] = 0.9, 0.1
        ranked = coverage_hours({'A': mild, 'B': strong}, baseline=baseline)
        self.assertLess(ranked.index(22), ranked.index(9))

    def test_no_regions_means_no_coverage_constraint(self):
        self.assertEqual(coverage_hours({}), [])

    def test_coverage_hours_are_allocated_even_below_the_hourly_floor(self):
        slots = plan_slots([1 / 24] * 24, budget=10, min_per_hour=1,
                           coverage_hours=[3, 7])
        counts = slot_hour_counts(slots)
        self.assertEqual(len(slots), 10)
        self.assertEqual(len(set(slots)), 10)
        self.assertGreaterEqual(counts[3], 1)
        self.assertGreaterEqual(counts[7], 1)

    def test_coverage_never_exceeds_the_budget(self):
        slots = plan_slots(one_hot(20), budget=3, min_per_hour=1,
                           coverage_hours=[1, 2, 3, 4, 5])
        counts = slot_hour_counts(slots)
        self.assertEqual(len(slots), 3)
        # Only half of the (tiny) budget may go to pins, best one first, and the
        # rest still lands on the volume peak.
        self.assertEqual(counts[1], 1)
        self.assertEqual(counts[20], 2)
        self.assertEqual(sum(counts[2:6]), 0)

    def test_a_tight_budget_pins_in_the_priority_order_it_is_given(self):
        # coverage_hours() ranks hours by how much their pin protects; a budget
        # too small to pin everything must spend on the top of that list.
        slots = plan_slots(one_hot(12), budget=4, min_per_hour=0,
                           coverage_hours=[22, 9, 0])
        counts = slot_hour_counts(slots)
        self.assertEqual(len(slots), 4)
        self.assertEqual(counts[22], 1)
        self.assertEqual(counts[9], 1)
        self.assertEqual(counts[0], 0)   # the third-ranked pin lost the tie

    def test_the_hourly_floor_wins_over_the_pins(self):
        # Issue #77 keeps an hourly freshness floor; a regional pin must never
        # take it away (the floor already samples every region hourly).
        slots = plan_slots([1 / 24] * 24, budget=30, min_per_hour=1, max_per_hour=4,
                           coverage_hours=[5, 6, 7, 8])
        counts = slot_hour_counts(slots)
        self.assertTrue(all(c >= 1 for c in counts))
        self.assertEqual(len(slots), 30)

    def test_pins_never_spend_more_than_half_of_the_discretionary_budget(self):
        weights = [1 / 24] * 24
        weights[18] = 0.5                # the volume peak must stay reachable
        total = sum(weights)
        weights = [w / total for w in weights]
        slots = plan_slots(weights, budget=12, min_per_hour=1, max_per_hour=4,
                           coverage_hours=list(range(1, 11)))
        counts = slot_hour_counts(slots)
        self.assertEqual(len(slots), 12)
        self.assertGreaterEqual(counts[18], 3)   # the water-fill kept its share
        self.assertGreaterEqual(sum(1 for h in range(1, 11) if counts[h]), 6)

    def test_pins_go_to_the_hours_the_province_under_serves(self):
        # Ranking is distinctiveness (region share / province share), not volume:
        # the pin exists for the hours the water-fill would never reach, so a
        # remote region's window must beat a barely-deviating big-city one.
        baseline = [0.25 / 23] * 24
        baseline[4] = 0.75                       # the province hot hour
        remote = [0.0] * 24
        remote[4], remote[22] = 0.6, 0.4         # peaks away from 04:00
        near = [0.0] * 24
        near[4], near[9] = 0.9, 0.1              # 09:00 barely deviates
        ranked = coverage_hours({'remote': remote, 'near': near}, baseline=baseline)
        self.assertEqual(ranked[0], 22)
        self.assertLess(ranked.index(22), ranked.index(9))

    def test_an_empty_coverage_set_keeps_the_original_plan(self):
        weights = [1 / 24] * 24
        self.assertEqual(plan_slots(weights, budget=48, min_per_hour=1, max_per_hour=4),
                         plan_slots(weights, budget=48, min_per_hour=1, max_per_hour=4,
                                    coverage_hours=[]))

    def test_coverage_is_applied_to_the_exploitation_part_only(self):
        # Exploration (#69) is opt-in on evidence and must keep probing the
        # least-observed hours; the regional pins never swallow its share.
        weights = [1 / 24] * 24
        observed = [12.0] * 24
        observed[3] = 0.0
        slots, explore = plan_schedule(weights, budget=48, min_per_hour=1,
                                       max_per_hour=4, observed_hours=observed,
                                       explore_frac=0.04, coverage_hours=[22])
        self.assertEqual(len(slots), 48)
        self.assertTrue(all(s // SLOTS_PER_HOUR == 3 for s in explore))
        self.assertIn(22 * SLOTS_PER_HOUR, slots)   # the pin survived the split


class LearnedMarkOrderTest(unittest.TestCase):
    """Quarter-hour discrimination inside an hour (issue #71 point 2)."""

    def profile_with_marks(self, hour, samples, changed):
        profile = empty_profile()
        profile['marks'] = {str(hour): {
            str(mark): {'samples': samples, 'changed_stations': changed.get(mark, 0)}
            for mark in range(4)
        }}
        return profile

    def test_a_hour_with_too_few_samples_keeps_the_default_mark_order(self):
        profile = self.profile_with_marks(5, MARK_MIN_SAMPLES // 4 - 1, {1: 100})
        self.assertEqual(learned_mark_orders(profile), {})

    def test_a_busy_hour_lists_its_busiest_mark_first(self):
        profile = self.profile_with_marks(5, MARK_MIN_SAMPLES,
                                         {0: 10, 1: 100, 2: 50, 3: 0})
        self.assertEqual(learned_mark_orders(profile)[5], (1, 2, 0, 3))

    def test_plan_slots_follows_the_learned_mark_order_for_extra_slots(self):
        # Slots are returned sorted, so compare which marks survived: the two
        # busiest learned marks instead of the two coarsest default marks.
        slots = plan_slots(one_hot(5), budget=2, min_per_hour=0,
                           mark_orders={5: (3, 2, 0, 1)})
        self.assertEqual(sorted(s % SLOTS_PER_HOUR for s in slots), [2, 3])

    def test_without_learned_marks_the_coarse_marks_come_first(self):
        slots = plan_slots(one_hot(5), budget=2, min_per_hour=0)
        self.assertEqual(sorted(s % SLOTS_PER_HOUR for s in slots), [0, 2])


class RecordRegionObservationTest(unittest.TestCase):
    def test_regional_counters_are_folded_in_next_to_the_global_ones(self):
        profile = empty_profile()
        profile, _ = record_observation(
            profile, observed={'Montréal': {'regular': 170.0}, 'Estrie': {'regular': 180.0}},
            region_counts={'Montréal': 100, 'Estrie': 5},
            now=at(11, 0), previous_at=None, stations_observed=105,
            station_changes=0, region_changed_stations={'Montréal': 0, 'Estrie': 0})
        profile, _ = record_observation(
            profile, observed={'Montréal': {'regular': 172.0}, 'Estrie': {'regular': 180.0}},
            region_counts={'Montréal': 100, 'Estrie': 5},
            now=at(11, 15), previous_at=at(11, 0), stations_observed=105,
            station_changes=12, region_changed_stations={'Montréal': 12, 'Estrie': 0})

        regions = profile['regions']
        self.assertEqual(regions['Montréal']['stations'], 100)
        self.assertEqual(regions['Estrie']['stations'], 5)
        hour = regions['Montréal']['hours']['11']
        self.assertEqual(hour['samples'], 2)
        self.assertEqual(hour['observed_hours'], 0.25)
        self.assertEqual(hour['change_events'], 1)      # the region average moved
        self.assertEqual(hour['changed_stations'], 12)  # exact per-station count
        self.assertEqual(regions['Estrie']['hours']['11']['changed_stations'], 0)
        self.assertEqual(regions['Estrie']['hours']['11']['change_events'], 0)

    def test_a_quiet_hour_is_never_invented_for_an_unobserved_region_hour(self):
        profile = empty_profile()
        profile, _ = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}},
            region_counts={'Estrie': 5}, now=at(9, 0), previous_at=None,
            station_changes=0, region_changed_stations={'Estrie': 0})
        self.assertEqual(sorted(profile['regions']['Estrie']['hours']), ['9'])

    def test_marks_record_the_quarter_hour_of_the_fetch(self):
        profile = empty_profile()
        profile, _ = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}},
            region_counts={'Estrie': 5}, now=at(9, 45), previous_at=None,
            station_changes=7, region_changed_stations={'Estrie': 7})
        profile, _ = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}},
            region_counts={'Estrie': 5}, now=at(9, 45, day=26), previous_at=at(9, 45),
            station_changes=3, region_changed_stations={'Estrie': 3})
        mark = profile['marks']['9']['3']
        self.assertEqual(mark['samples'], 2)
        self.assertEqual(mark['changed_stations'], 10)
        self.assertNotIn('0', profile['marks']['9'])

    def test_without_the_exact_channel_no_marks_are_recorded(self):
        profile = empty_profile()
        profile, _ = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}},
            region_counts={'Estrie': 5}, now=at(9, 45), previous_at=None)
        self.assertEqual(profile.get('marks'), None)

    def test_a_legacy_profile_gains_the_regional_block_on_the_next_fetch(self):
        profile = empty_profile()
        self.assertNotIn('regions', profile)
        profile, _ = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}},
            region_counts={'Estrie': 5}, now=at(9, 0), previous_at=None,
            station_changes=0, region_changed_stations={'Estrie': 0})
        self.assertEqual(sorted(profile['regions']), ['Estrie'])
        self.assertEqual(profile['hours']['9']['samples'], 1)  # legacy fields kept

    def test_the_exact_sample_counter_is_kept_per_hour(self):
        profile = empty_profile()
        profile, _ = record_observation(
            profile, observed={'Estrie': {'regular': 180.0}},
            region_counts={'Estrie': 5}, now=at(9, 0), previous_at=None,
            station_changes=0, region_changed_stations={'Estrie': 0})
        self.assertEqual(profile['hours']['9']['exact_samples'], 1)


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


class LocalTimetableTest(unittest.TestCase):
    """Issue #91: the plan is a *local* timetable, not a set of UTC hours."""

    def test_gap_caps_are_ten_sixty_and_twenty_minutes(self):
        caps = gap_caps()
        self.assertEqual(caps[0], 10)
        self.assertEqual([caps[h] for h in range(1, 6)], [60] * 5)
        self.assertEqual([caps[h] for h in range(6, 24)], [20] * 18)
        self.assertEqual(max_gap_minutes(0), 10)
        self.assertEqual(max_gap_minutes(4), 60)
        self.assertEqual(max_gap_minutes(12), 20)

    def test_spread_offsets_are_unique_five_minute_marks(self):
        for count in range(1, 13):
            marks = spread_offsets(count)
            self.assertEqual(len(marks), count)
            self.assertEqual(len(set(marks)), count)
            self.assertTrue(all(mark % 5 == 0 for mark in marks), marks)
            self.assertTrue(all(0 <= mark < 60 for mark in marks), marks)
        self.assertEqual(spread_offsets(3), [0, 20, 40])
        self.assertEqual(spread_offsets(6), [0, 10, 20, 30, 40, 50])
        self.assertEqual(spread_offsets(0), [])

    def test_parse_timetable_drops_what_it_cannot_use(self):
        self.assertEqual(parse_timetable(['06:00', 'nonsense', '25:00', '07:03']),
                         [(6, 0)])
        self.assertEqual(parse_timetable([None, 7]), [])

    def test_validate_timetable_reports_the_problems(self):
        self.assertEqual(validate_timetable(['00:00', '00:10', '23:55']), [])
        self.assertEqual(validate_timetable([]),
                         ['timetable must be a non-empty list of "HH:MM" labels'])
        self.assertTrue(validate_timetable(['00:03']))
        self.assertTrue(validate_timetable(['00:10', '00:00']))
        self.assertTrue(validate_timetable(['00:00', '00:00']))

    def test_format_timetable_sorts_and_dedupes(self):
        self.assertEqual(format_timetable([(6, 0), (0, 10), (6, 0)]),
                         ['00:10', '06:00'])

    def test_a_local_timetable_shifts_with_daylight_saving_time(self):
        """October is EDT (UTC-4), November is EST (UTC-5): 06:00 stays 06:00."""
        timetable = ['06:00', '00:00']
        october = timetable_datetimes(date(2026, 10, 16), timetable)
        november = timetable_datetimes(date(2026, 11, 16), timetable)
        self.assertEqual([dt.hour for dt in october], [4, 10])
        self.assertEqual([dt.hour for dt in november], [5, 11])
        self.assertEqual(timetable_utc_labels(timetable, day=date(2026, 10, 16)),
                         ['04:00', '10:00'])
        self.assertEqual(timetable_utc_labels(timetable, day=date(2026, 11, 16)),
                         ['05:00', '11:00'])

    def test_a_transition_day_keeps_one_unique_instant_per_slot(self):
        timetable = [f'{h:02d}:{m:02d}' for h in range(24) for m in (0, 30)]
        for day in (date(2026, 3, 8), date(2026, 11, 1)):
            instants = timetable_datetimes(day, timetable)
            self.assertEqual(len(set(instants)), len(instants))
        spring = [dt.strftime('%H:%M') for dt in
                  timetable_datetimes(date(2026, 3, 8), ['01:30', '02:00', '02:30', '03:30'])]
        # 02:00-02:59 never happens locally: those slots are dropped, and the
        # neighbours are still one hour apart (06:30Z -> 07:30Z).
        self.assertEqual(spring, ['06:30', '07:30'])
        fall = [dt.strftime('%H:%M') for dt in
                timetable_datetimes(date(2026, 11, 1), ['01:00', '02:00'])]
        # 01:00 happens twice (EDT then EST), so the night floor keeps its
        # hourly cadence across the fall-back seam.
        self.assertEqual(fall, ['05:00', '06:00', '07:00'])

    def test_the_schedule_projection_is_the_utc_grid_the_gate_uses(self):
        timetable = ['00:00', '06:00', '23:40']
        labels = timetable_utc_labels(timetable, day=date(2026, 10, 16))
        self.assertEqual(labels, ['03:40', '04:00', '10:00'])
        self.assertEqual({i // SLOTS_PER_HOUR for i in slot_indices(labels)},
                         {3, 4, 10})

    def test_a_gated_slot_fires_at_the_local_time_after_the_switch(self):
        timetable = ['06:00']
        # 2026-11-16 11:03Z = 06:03 local (EST) -> the 06:00 slot just passed.
        now = datetime(2026, 11, 16, 11, 3, tzinfo=UTC)
        due, reason = due_decision(now, [], datetime(2026, 11, 16, 10, 30, tzinfo=UTC),
                                   timetable=timetable)
        self.assertTrue(due, reason)
        # ...and one hour earlier (05:03 local) it is not due yet.
        due, reason = due_decision(datetime(2026, 11, 16, 10, 3, tzinfo=UTC), [],
                                   datetime(2026, 11, 16, 9, 30, tzinfo=UTC),
                                   timetable=timetable)
        self.assertFalse(due, reason)
        self.assertIn('local', reason)

    def test_the_gate_keeps_the_legacy_utc_behaviour_without_a_timetable(self):
        due, reason = due_decision(at(11, 4), ['11:00'], at(10, 30))
        self.assertTrue(due, reason)
        due, reason = due_decision(at(10, 4), ['11:00'], at(9, 30))
        self.assertFalse(due, reason)
        self.assertIn('UTC', reason)

    def test_a_local_explore_label_is_tagged(self):
        due, reason = due_decision(at(11, 4), [], at(10, 30),
                                   timetable=['06:00', '07:00'],
                                   explore_slots=['07:00'])
        self.assertTrue(due, reason)
        self.assertIn('[explore]', reason)

    def test_an_unknown_timezone_falls_back_to_utc(self):
        self.assertEqual(str(local_zone('Not/AZone')), 'UTC')
        self.assertEqual(timetable_utc_labels(['06:00'], tz='Not/AZone',
                                              day=date(2026, 10, 16)),
                         ['06:00'])


if __name__ == '__main__':
    unittest.main()

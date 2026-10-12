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
    DEFAULT_MAX_AGE_MINUTES,
    EXACT_READY_SAMPLES,
    MARK_MIN_SAMPLES,
    MAX_AGE_ENV_VAR,
    REGION_PEAK_HOURS,
    REGION_PRIOR_HOURS,
    SLOTS_PER_DAY,
    SLOTS_PER_HOUR,
    coverage_hours,
    diff_regions,
    diff_stations,
    due_decision,
    env_int,
    exact_channel_ready,
    hour_rates,
    hour_weights,
    learned_mark_orders,
    observed_from_stations,
    plan_slots,
    prices_from_row,
    prices_to_row,
    record_observation,
    region_hour_weights,
    region_station_counts,
    research_prior_weights,
    slot_hour_counts,
    slot_index,
    slot_label,
    station_coord_key,
    station_prices,
    station_regions,
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

    def test_default_budget_covers_every_quarter_hour_slot(self):
        """At the default budget (96) the plan uses every wake-up (issue #67)."""
        weights = [1 / 24] * 24
        weights[4], weights[5], weights[11] = 0.5, 0.3, 0.2  # a clear learned peak
        total = sum(weights)
        weights = [w / total for w in weights]
        slots = plan_slots(weights, budget=DEFAULT_DAILY_BUDGET, min_per_hour=1,
                           max_per_hour=4)
        self.assertEqual(len(slots), DEFAULT_DAILY_BUDGET)
        # Budget == capacity, so the learned weights no longer prune anything:
        # every quarter-hour slot of the day is polled.
        self.assertEqual(slots, list(range(SLOTS_PER_DAY)))

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


class FetchDefaultsTest(unittest.TestCase):
    """Issue #67: the fetch rhythm follows the upstream snapshot cadence."""

    def test_default_budget_is_one_poll_per_quarter_hour_slot(self):
        # Upstream regenerates its snapshot every 5 minutes on the quarter-hour
        # grid and the workflow wakes every 15 minutes, so the whole day is 96
        # slots and the default budget spends exactly one request per slot.
        self.assertEqual(SLOTS_PER_DAY, 96)
        self.assertEqual(DEFAULT_DAILY_BUDGET, SLOTS_PER_DAY)

    def test_default_freshness_net_is_90_minutes(self):
        self.assertEqual(DEFAULT_MAX_AGE_MINUTES, 90)
        # Two hours without a fetch is stale *by default* now (it was not at 3h).
        due, reason = due_decision(at(3, 7), ['00:00'], at(1, 0))
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
    is (nearly) the same number in every hour. ``exact`` optionally adds the
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
        slots = plan_slots([1 / 24] * 24, budget=3, min_per_hour=1,
                           coverage_hours=[1, 2, 3, 4, 5])
        counts = slot_hour_counts(slots)
        self.assertEqual(len(slots), 3)
        self.assertEqual(sum(1 for h in (1, 2, 3, 4, 5) if counts[h] >= 1), 3)

    def test_a_tight_budget_pins_in_the_priority_order_it_is_given(self):
        # coverage_hours() ranks hours by how region-specific they are; a budget
        # too small to pin everything must spend on the top of that list.
        slots = plan_slots([1 / 24] * 24, budget=2, min_per_hour=0,
                           coverage_hours=[22, 9, 0])
        self.assertEqual(sorted(s // SLOTS_PER_HOUR for s in slots), [9, 22])

    def test_an_empty_coverage_set_keeps_the_original_plan(self):
        weights = [1 / 24] * 24
        self.assertEqual(plan_slots(weights, budget=48, min_per_hour=1, max_per_hour=4),
                         plan_slots(weights, budget=48, min_per_hour=1, max_per_hour=4,
                                    coverage_hours=[]))


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


if __name__ == '__main__':
    unittest.main()

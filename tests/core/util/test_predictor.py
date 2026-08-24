#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
#      this list of conditions and the following disclaimer.
#    * Redistributions in binary form must reproduce the above copyright notice,
#      this list of conditions and the following disclaimer in the documentation
#      and/or other materials provided with the distribution.
#    * Neither the name of Virtual Cable S.L. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import datetime
import math
import typing

from django.utils import timezone

from uds.core import consts
from uds.core import types
from uds.core.util.cache import Cache
from uds.core.util.stats import predictor
from uds.models import StatsCountersAccum

from ...utils.test import UDSTestCase


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, tzinfo=datetime.timezone.utc)


class PredictorPureTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def _profile_value_equals_hour(self, weeks: int = 2) -> predictor.Profile:
        """Builds a profile where each sample value equals its local hour."""
        samples: list[predictor.Sample] = []
        for day in range(weeks * 7):
            base = _utc(2026, 1, 5) + datetime.timedelta(days=day)
            for hour in range(24):
                when = base + datetime.timedelta(hours=hour)
                samples.append(predictor.Sample(when=when, mean=float(hour), max=float(hour), count=6))
        return predictor.build_profile(samples, owner_id=1, counter_type=types.stats.CounterType.INUSE)

    def test_percentiles_empty(self) -> None:
        self.assertEqual(predictor._percentiles([]), (0.0, 0.0, 0.0))

    def test_percentiles_single(self) -> None:
        self.assertEqual(predictor._percentiles([5.0]), (5.0, 5.0, 5.0))

    def test_percentiles_ordered(self) -> None:
        p50, p75, p90 = predictor._percentiles([float(i) for i in range(1, 101)])
        self.assertLessEqual(p50, p75)
        self.assertLessEqual(p75, p90)
        self.assertLessEqual(p90, 100.0)

    def test_build_profile_basic(self) -> None:
        profile = self._profile_value_equals_hour(weeks=2)
        cell = profile.cells.get((0, 10))
        self.assertIsNotNone(cell)
        assert cell is not None
        self.assertAlmostEqual(cell.mean, 10.0)
        self.assertEqual(cell.n, 2)
        self.assertEqual(profile.hourly[10].n, 14)
        self.assertEqual(profile.total_samples, 2 * 7 * 24)

    def test_build_profile_cell_max(self) -> None:
        profile = self._profile_value_equals_hour(weeks=2)
        cell = profile.cells.get((0, 5))
        assert cell is not None
        self.assertAlmostEqual(cell.max, 5.0)

    def test_cell_for_fallback_hourly(self) -> None:
        samples = [predictor.Sample(when=_utc(2026, 1, 5, 10), mean=10.0, max=10.0, count=6)]
        profile = predictor.build_profile(samples, owner_id=1, counter_type=types.stats.CounterType.INUSE)
        self.assertIsNone(profile.cells.get((2, 10)))
        self.assertIsNotNone(profile.hourly.get(10))
        cell = profile.cell_for(_utc(2026, 1, 7, 10))
        self.assertIsNotNone(cell)

    def test_cell_for_none_when_no_data(self) -> None:
        samples = [predictor.Sample(when=_utc(2026, 1, 5, 10), mean=10.0, max=10.0, count=6)]
        profile = predictor.build_profile(samples, owner_id=1, counter_type=types.stats.CounterType.INUSE)
        self.assertIsNone(profile.cell_for(_utc(2026, 1, 6, 3)))

    def test_confidence_empty(self) -> None:
        profile = predictor.build_profile([], owner_id=1, counter_type=types.stats.CounterType.INUSE)
        self.assertEqual(predictor.confidence(profile), 0.0)

    def test_confidence_grows_with_history(self) -> None:
        low = predictor.confidence(self._profile_value_equals_hour(weeks=2))
        high = predictor.confidence(self._profile_value_equals_hour(weeks=9))
        self.assertGreater(high, low)
        self.assertAlmostEqual(high, 1.0)

    def test_forecast_returns_points(self) -> None:
        profile = self._profile_value_equals_hour(weeks=2)
        points = predictor.forecast(profile, _utc(2026, 1, 5, 0), 24)
        self.assertEqual(len(points), 24)
        self.assertEqual(points[0].when, _utc(2026, 1, 5, 0))
        for point, expected_hour in zip(points, range(24), strict=True):
            assert point.cell is not None
            self.assertAlmostEqual(point.cell.mean, float(expected_hour))

    def test_forecast_zero_hours(self) -> None:
        profile = self._profile_value_equals_hour(weeks=2)
        self.assertEqual(predictor.forecast(profile, _utc(2026, 1, 5, 0), 0), [])

    def test_detect_anomaly_matching(self) -> None:
        profile = self._profile_value_equals_hour(weeks=2)
        recent = [predictor.Sample(when=_utc(2026, 1, 19, 10), mean=10.0, max=10.0, count=6)]
        self.assertLess(predictor.detect_anomaly(profile, recent), consts.predictions.ANOMALY_THRESHOLD)

    def test_detect_anomaly_broken(self) -> None:
        profile = self._profile_value_equals_hour(weeks=2)
        recent = [predictor.Sample(when=_utc(2026, 1, 19, 10), mean=100.0, max=100.0, count=6)]
        self.assertGreaterEqual(predictor.detect_anomaly(profile, recent), consts.predictions.ANOMALY_THRESHOLD)

    def test_detect_anomaly_no_matching_cell(self) -> None:
        samples = [predictor.Sample(when=_utc(2026, 1, 5, 10), mean=10.0, max=10.0, count=6)]
        profile = predictor.build_profile(samples, owner_id=1, counter_type=types.stats.CounterType.INUSE)
        recent = [predictor.Sample(when=_utc(2026, 1, 6, 3), mean=5.0, max=5.0, count=6)]
        self.assertEqual(predictor.detect_anomaly(profile, recent), 0.0)


class PredictorCacheRecommendationsTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def _profile_with_value(
        self, value: float, counter: types.stats.CounterType = types.stats.CounterType.INUSE
    ) -> predictor.Profile:
        """Builds a profile where every (dow, hour) cell has the same value."""
        samples = [
            predictor.Sample(
                when=_utc(2026, 1, 5) + datetime.timedelta(days=d, hours=h),
                mean=value,
                max=value,
                count=6,
            )
            for d in range(14)
            for h in range(24)
        ]
        return predictor.build_profile(samples, owner_id=1, counter_type=counter)

    def test_no_data_when_empty_profiles(self) -> None:
        inuse = predictor.build_profile([], owner_id=1, counter_type=types.stats.CounterType.INUSE)
        cached = predictor.build_profile([], owner_id=1, counter_type=types.stats.CounterType.CACHED)
        recs = predictor.cache_recommendations(
            inuse, cached, cache_l1_srvs=10, cache_l2_srvs=5, initial_srvs=1, max_srvs=20
        )
        self.assertEqual(len(recs), 24)
        self.assertTrue(all(r.verdict == "NO_DATA" for r in recs))

    def test_starved_when_inuse_exceeds_cache_l1(self) -> None:
        inuse = self._profile_with_value(15.0)  # p50=15 >= cache_l1=10
        cached = self._profile_with_value(2.0, types.stats.CounterType.CACHED)
        recs = predictor.cache_recommendations(
            inuse, cached, cache_l1_srvs=10, cache_l2_srvs=5, initial_srvs=1, max_srvs=50
        )
        self.assertTrue(all(r.verdict == "STARVED" for r in recs))
        self.assertTrue(all(r.priority == "high" for r in recs))

    def test_saturated_when_inuse_hits_max(self) -> None:
        inuse = self._profile_with_value(50.0)  # p90=50 >= max_srvs=50
        cached = self._profile_with_value(0.0, types.stats.CounterType.CACHED)
        recs = predictor.cache_recommendations(
            inuse, cached, cache_l1_srvs=10, cache_l2_srvs=5, initial_srvs=1, max_srvs=50
        )
        self.assertTrue(all(r.verdict == "SATURATED" for r in recs))

    def test_excess_when_inuse_below_cache_and_cached_present(self) -> None:
        inuse = self._profile_with_value(2.0)  # p90=2 < cache_l1=10
        cached = self._profile_with_value(8.0, types.stats.CounterType.CACHED)
        recs = predictor.cache_recommendations(
            inuse, cached, cache_l1_srvs=10, cache_l2_srvs=5, initial_srvs=1, max_srvs=50
        )
        self.assertTrue(all(r.verdict == "EXCESS" for r in recs))
        self.assertTrue(all(r.priority == "medium" for r in recs))

    def test_ok_when_inuse_within_bounds(self) -> None:
        inuse = self._profile_with_value(5.0)  # p50=5 < cache_l1=10, p90=5 < 10
        cached = self._profile_with_value(0.0, types.stats.CounterType.CACHED)  # no cache present
        recs = predictor.cache_recommendations(
            inuse, cached, cache_l1_srvs=10, cache_l2_srvs=5, initial_srvs=1, max_srvs=50
        )
        self.assertTrue(all(r.verdict == "OK" for r in recs))
        self.assertTrue(all(r.priority == "low" for r in recs))

    def test_no_cache_l1_means_ok_or_no_data(self) -> None:
        inuse = self._profile_with_value(5.0)
        cached = self._profile_with_value(0.0, types.stats.CounterType.CACHED)
        recs = predictor.cache_recommendations(
            inuse, cached, cache_l1_srvs=0, cache_l2_srvs=0, initial_srvs=1, max_srvs=50
        )
        # With cache_l1=0, no EXCESS/STARVED; either OK or SATURATED
        self.assertTrue(all(r.verdict in ("OK", "SATURATED", "NO_DATA") for r in recs))

    def test_returns_24_slots(self) -> None:
        inuse = self._profile_with_value(5.0)
        cached = self._profile_with_value(3.0, types.stats.CounterType.CACHED)
        recs = predictor.cache_recommendations(
            inuse, cached, cache_l1_srvs=10, cache_l2_srvs=5, initial_srvs=1, max_srvs=50
        )
        self.assertEqual(len(recs), 24)
        self.assertEqual([r.hour for r in recs], list(range(24)))


class PredictorLoadSamplesTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def _create_hourly_accum(self, pool_id: int, counter: types.stats.CounterType, hours: int) -> None:
        records = [
            StatsCountersAccum(
                owner_type=types.stats.CounterOwnerType.SERVICEPOOL,
                owner_id=pool_id,
                counter_type=counter,
                interval_type=StatsCountersAccum.IntervalType.HOUR,
                stamp=int((_utc(2026, 1, 5, 0) + datetime.timedelta(hours=i + 1)).timestamp()),
                v_count=6,
                v_sum=6 * i,
                v_max=i,
                v_min=0,
            )
            for i in range(hours)
        ]
        StatsCountersAccum.objects.bulk_create(records)

    def test_load_samples_returns_mean_and_max(self) -> None:
        self._create_hourly_accum(1, types.stats.CounterType.INUSE, 24)
        samples = predictor.load_samples(
            1, types.stats.CounterType.INUSE, since=_utc(2026, 1, 5, 0), to=_utc(2026, 1, 6, 0)
        )
        self.assertEqual(len(samples), 24)
        self.assertAlmostEqual(samples[0].mean, 0.0)
        self.assertAlmostEqual(samples[1].mean, 1.0)
        self.assertEqual(samples[1].max, 1)

    def test_load_samples_skips_dummy_rows(self) -> None:
        StatsCountersAccum.objects.create(
            owner_type=types.stats.CounterOwnerType.SERVICEPOOL,
            owner_id=1,
            counter_type=types.stats.CounterType.INUSE,
            interval_type=StatsCountersAccum.IntervalType.HOUR,
            stamp=int(_utc(2026, 1, 5, 1).timestamp()),
            v_count=0,
            v_sum=0,
            v_max=0,
            v_min=0,
        )
        self.assertEqual(
            len(
                predictor.load_samples(
                    1, types.stats.CounterType.INUSE, since=_utc(2026, 1, 5, 0), to=_utc(2026, 1, 6, 0)
                )
            ),
            0,
        )

    def test_load_samples_respects_time_window(self) -> None:
        self._create_hourly_accum(1, types.stats.CounterType.INUSE, 24)
        samples = predictor.load_samples(
            1,
            types.stats.CounterType.INUSE,
            since=_utc(2026, 1, 5, 5),
            to=_utc(2026, 1, 5, 10),
        )
        self.assertEqual(len(samples), 5)


class PredictorGetProfileTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)
        Cache.delete(consts.predictions.PROFILE_CACHE_OWNER)

    @typing.override
    def tearDown(self) -> None:
        Cache.delete(consts.predictions.PROFILE_CACHE_OWNER)
        super().tearDown()

    def test_get_profile_empty_not_cached(self) -> None:
        profile = predictor.get_profile(9999, types.stats.CounterType.INUSE)
        self.assertEqual(profile.total_samples, 0)
        self.assertEqual(predictor.confidence(profile), 0.0)

    def test_get_profile_caches_and_reuses(self) -> None:
        now = timezone.now()
        records = [
            StatsCountersAccum(
                owner_type=types.stats.CounterOwnerType.SERVICEPOOL,
                owner_id=1,
                counter_type=types.stats.CounterType.INUSE,
                interval_type=StatsCountersAccum.IntervalType.HOUR,
                stamp=int((now - datetime.timedelta(hours=24 - i)).replace(minute=0, second=0).timestamp()),
                v_count=6,
                v_sum=6 * 5,
                v_max=5,
                v_min=0,
            )
            for i in range(24)
        ]
        StatsCountersAccum.objects.bulk_create(records)
        profile = predictor.get_profile(1, types.stats.CounterType.INUSE)
        self.assertGreater(profile.total_samples, 0)
        cached = Cache(consts.predictions.PROFILE_CACHE_OWNER).get("1-3")
        self.assertIsInstance(cached, predictor.Profile)


class PredictorBandRecommendationsTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def _slot(self, hour: int, verdict: str, *, p50: float = 0.0, p90: float = 0.0) -> predictor.CacheSlotRecommendation:
        return predictor.CacheSlotRecommendation(
            hour=hour,
            verdict=verdict,
            inuse_p50=p50,
            inuse_p90=p90,
            cached_mean=0.0,
            action=f"action for {hour}",
            priority="low",
        )

    def test_worst_verdict_wins_in_band(self) -> None:
        slots = [self._slot(h, "OK") for h in range(24)]
        slots[8] = self._slot(8, "STARVED", p50=12.0, p90=18.0)
        bands = predictor.band_recommendations(slots, cache_l1_srvs=10, max_srvs=50)
        morning = next(b for b in bands if b.band == "morning")
        self.assertEqual(morning.verdict, "STARVED")
        self.assertEqual(morning.reason, "action for 8")

    def test_starved_band_suggests_peak_plus_headroom(self) -> None:
        slots = [self._slot(h, "STARVED", p50=12.0, p90=18.0) for h in range(24)]
        bands = predictor.band_recommendations(slots, cache_l1_srvs=10, max_srvs=50)
        self.assertTrue(
            all(b.suggested_cache_l1 == 18 + consts.predictions.CACHE_HEADROOM for b in bands)
        )

    def test_suggestion_never_above_max_srvs(self) -> None:
        slots = [self._slot(h, "STARVED", p50=40.0, p90=40.0) for h in range(24)]
        bands = predictor.band_recommendations(slots, cache_l1_srvs=10, max_srvs=20)
        self.assertTrue(all(b.suggested_cache_l1 == 20 for b in bands))

    def test_excess_band_lowers_cache(self) -> None:
        slots = [self._slot(h, "EXCESS", p50=2.0, p90=3.0) for h in range(24)]
        bands = predictor.band_recommendations(slots, cache_l1_srvs=10, max_srvs=50)
        self.assertTrue(all(b.suggested_cache_l1 == 3 for b in bands))

    def test_ok_and_saturated_keep_current_size(self) -> None:
        for verdict in ("OK", "SATURATED", "NO_DATA"):
            slots = [self._slot(h, verdict, p50=1.0, p90=2.0) for h in range(24)]
            bands = predictor.band_recommendations(slots, cache_l1_srvs=7, max_srvs=50)
            self.assertTrue(all(b.suggested_cache_l1 == 7 for b in bands), verdict)

    def test_all_hours_belong_to_exactly_one_band(self) -> None:
        slots = [self._slot(h, "OK") for h in range(24)]
        bands = predictor.band_recommendations(slots, cache_l1_srvs=1, max_srvs=10)
        hours = [hour for band in bands for hour in band.hours]
        self.assertEqual(sorted(hours), list(range(24)))
        self.assertEqual(len(bands), len(consts.predictions.DAY_BANDS))


class PredictorCrossPoolTest(UDSTestCase):
    def _usage(self, name: str, verdict: str, *, l1: int, suggested: int) -> predictor.PoolHourUsage:
        return predictor.PoolHourUsage(
            pool_name=name,
            verdict=verdict,
            inuse_p90=10.0,
            cache_l1_srvs=l1,
            suggested_cache_l1=suggested,
        )

    def test_pairs_hungry_with_surplus(self) -> None:
        notes = predictor.cross_pool_notes(
            {
                8: [
                    self._usage("hungry", "STARVED", l1=5, suggested=12),
                    self._usage("spare", "EXCESS", l1=10, suggested=3),
                ]
            }
        )
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0].hour, 8)
        self.assertEqual([p.pool_name for p in notes[0].hungry], ["hungry"])
        self.assertEqual(notes[0].lendable, 7)

    def test_ignores_hours_without_both_sides(self) -> None:
        notes = predictor.cross_pool_notes(
            {
                8: [self._usage("hungry", "STARVED", l1=5, suggested=12)],
                9: [self._usage("spare", "EXCESS", l1=10, suggested=3)],
            }
        )
        self.assertEqual(notes, [])


class PredictorAnnualComponentTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def _yearly_samples(self, days: int, *, amplitude: float = 10.0) -> list[predictor.Sample]:
        """One sample per day following a yearly sine wave around a mean of 20."""
        start = _utc(2025, 1, 1)
        samples: list[predictor.Sample] = []
        for day in range(days):
            when = start + datetime.timedelta(days=day)
            value = 20.0 + amplitude * math.sin(2.0 * math.pi * day / 365.25)
            samples.append(predictor.Sample(when=when, mean=value, max=value, count=6))
        return samples

    def test_no_fit_without_enough_history(self) -> None:
        samples = self._yearly_samples(consts.predictions.MIN_DAYS_FOR_ANNUAL_FIT - 10)
        self.assertIsNone(predictor.fit_annual_component(samples))

    def test_no_fit_without_samples(self) -> None:
        self.assertIsNone(predictor.fit_annual_component([]))

    def test_fit_recovers_the_yearly_wave(self) -> None:
        samples = self._yearly_samples(400)
        component = predictor.fit_annual_component(samples)
        self.assertIsNotNone(component)
        assert component is not None
        peak = _utc(2025, 1, 1) + datetime.timedelta(days=91)
        trough = _utc(2025, 1, 1) + datetime.timedelta(days=274)
        self.assertGreater(component.value_at(peak), component.value_at(trough))
        self.assertAlmostEqual(component.value_at(peak), 30.0, delta=1.5)
        self.assertGreater(component.factor_at(peak), 1.0)
        self.assertLess(component.factor_at(trough), 1.0)

    def test_factor_is_clamped(self) -> None:
        samples = self._yearly_samples(400, amplitude=1000.0)
        component = predictor.fit_annual_component(samples)
        assert component is not None
        factors = [
            component.factor_at(_utc(2025, 1, 1) + datetime.timedelta(days=d)) for d in range(0, 365, 7)
        ]
        self.assertTrue(all(consts.predictions.ANNUAL_FACTOR_MIN <= f <= consts.predictions.ANNUAL_FACTOR_MAX for f in factors))

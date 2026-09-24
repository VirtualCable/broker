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

import collections.abc
import datetime
import math
import typing
from unittest import mock

from django.utils import timezone

from uds import models
from uds.core import consts, types
from uds.core.util.cache import Cache
from uds.core.util.config import GlobalConfig
from uds.core.util.stats import predictor, saturation

from ...fixtures import servers as servers_fixtures
from ...utils.test import UDSTestCase

COUNTER = types.stats.CounterType


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, tzinfo=datetime.timezone.utc)


def _config_value(name: str, value: int) -> typing.Any:
    """Patch a GlobalConfig value (saturation reads them at call time)."""
    return mock.patch.object(GlobalConfig, name, mock.Mock(as_int=mock.Mock(return_value=value)))


def _daily_series(
    days: int,
    value_for_day: collections.abc.Callable[[int], float],
    *,
    start: datetime.datetime | None = None,
) -> list[predictor.Sample]:
    """Hourly samples for `days` days (24 samples per day, all sharing that day's value)."""
    base = start or _utc(2026, 1, 5)
    return [
        predictor.Sample(
            when=base + datetime.timedelta(days=d, hours=h),
            mean=value_for_day(d),
            max=value_for_day(d),
            count=6,
        )
        for d in range(days)
        for h in range(24)
    ]


def _profile(samples: collections.abc.Sequence[predictor.Sample]) -> predictor.Profile:
    return predictor.build_profile(samples, owner_id=1, counter_type=COUNTER.DISK)


def _now_after(days: int) -> datetime.datetime:
    """A `now` just past the end of a `days`-day series built with `_daily_series`."""
    return _utc(2026, 1, 5) + datetime.timedelta(days=days, hours=1)


class SaturationPureTest(UDSTestCase):
    """evaluate_metric / fit_growth over synthetic series, no DB counters."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def test_no_data_without_samples(self) -> None:
        metric = saturation.evaluate_metric(
            counter=COUNTER.DISK,
            profile=_profile([]),
            samples=[],
            threshold=85.0,
            now=_utc(2026, 3, 2),
        )
        self.assertEqual(metric.status, saturation.SaturationStatus.NO_DATA)
        self.assertIsNone(metric.days_to_saturation)

    def test_saturated_when_p90_over_threshold(self) -> None:
        samples = _daily_series(56, lambda d: 90.0)
        now = _now_after(56)
        metric = saturation.evaluate_metric(
            counter=COUNTER.DISK, profile=_profile(samples), samples=samples, threshold=85.0, now=now
        )
        self.assertEqual(metric.status, saturation.SaturationStatus.SATURATED)
        self.assertEqual(metric.days_to_saturation, 0)
        self.assertEqual(metric.saturation_date, now)

    def test_known_slope_gives_exact_days(self) -> None:
        # 0.4 %/day keeps the last week clearly below the anomaly gate (a
        # steeper slope over 8 weeks makes the recent window deviate enough
        # from the cell mean to be read as a broken pattern)
        samples = _daily_series(56, lambda d: 10.0 + 0.4 * d)
        now = _now_after(56)
        metric = saturation.evaluate_metric(
            counter=COUNTER.DISK, profile=_profile(samples), samples=samples, threshold=85.0, now=now
        )
        assert metric.growth is not None
        self.assertEqual(metric.growth.method, "linear")
        self.assertAlmostEqual(metric.growth.slope_per_day, 0.4, delta=1e-9)
        self.assertAlmostEqual(metric.growth.r2, 1.0, delta=1e-9)
        self.assertEqual(metric.status, saturation.SaturationStatus.GROWING)
        # The projection anchors on the current p90, not on the fitted intercept
        assert metric.days_to_saturation is not None
        self.assertEqual(
            metric.days_to_saturation,
            math.ceil((85.0 - metric.current_p90) / metric.growth.slope_per_day),
        )
        self.assertLessEqual(metric.days_to_saturation, consts.forecasts.SATURATION_MAX_HORIZON_DAYS)
        assert metric.saturation_date is not None
        self.assertEqual(metric.saturation_date, now + datetime.timedelta(days=metric.days_to_saturation))

    def test_flat_series_is_stable(self) -> None:
        samples = _daily_series(56, lambda d: 10.0)
        metric = saturation.evaluate_metric(
            counter=COUNTER.DISK,
            profile=_profile(samples),
            samples=samples,
            threshold=85.0,
            now=_now_after(56),
        )
        self.assertEqual(metric.status, saturation.SaturationStatus.STABLE)
        self.assertIsNone(metric.days_to_saturation)

    def test_shrinking_series(self) -> None:
        samples = _daily_series(56, lambda d: 70.0 - 0.5 * d)
        metric = saturation.evaluate_metric(
            counter=COUNTER.DISK,
            profile=_profile(samples),
            samples=samples,
            threshold=85.0,
            now=_now_after(56),
        )
        assert metric.growth is not None
        self.assertLess(metric.growth.slope_per_day, 0.0)
        self.assertEqual(metric.status, saturation.SaturationStatus.SHRINKING)
        self.assertIsNone(metric.days_to_saturation)

    def test_low_confidence_is_unreliable(self) -> None:
        # Only two weeks: enough slope, not enough history to trust a projection
        samples = _daily_series(14, lambda d: 10.0 + 5.0 * d)
        metric = saturation.evaluate_metric(
            counter=COUNTER.DISK,
            profile=_profile(samples),
            samples=samples,
            threshold=85.0,
            now=_now_after(14),
        )
        self.assertLess(metric.confidence, consts.forecasts.SATURATION_MIN_CONFIDENCE)
        self.assertEqual(metric.status, saturation.SaturationStatus.UNRELIABLE)
        self.assertIsNone(metric.days_to_saturation)
        self.assertIsNone(metric.saturation_date)

    def test_recent_anomaly_is_unreliable_with_current_values(self) -> None:
        # Eight weeks at 10, with the last 7 days broken to 100 (a holiday, an
        # outage, a policy change: the weekly pattern no longer predicts).
        samples = _daily_series(49, lambda d: 10.0)
        samples += _daily_series(7, lambda d: 100.0, start=_utc(2026, 1, 5) + datetime.timedelta(days=49))
        now = _now_after(56)
        metric = saturation.evaluate_metric(
            counter=COUNTER.DISK, profile=_profile(samples), samples=samples, threshold=85.0, now=now
        )
        self.assertEqual(metric.status, saturation.SaturationStatus.UNRELIABLE)
        # The current peak is still reported, just with no future projection
        self.assertGreater(metric.current_p90, 20.0)
        self.assertIsNone(metric.days_to_saturation)
        self.assertIsNone(metric.saturation_date)

    def test_beyond_horizon_has_no_date(self) -> None:
        samples = _daily_series(56, lambda d: 10.0 + 0.05 * d)
        metric = saturation.evaluate_metric(
            counter=COUNTER.DISK,
            profile=_profile(samples),
            samples=samples,
            threshold=85.0,
            now=_now_after(56),
        )
        assert metric.growth is not None
        self.assertEqual(metric.status, saturation.SaturationStatus.GROWING_BEYOND_HORIZON)
        self.assertIsNone(metric.days_to_saturation)
        self.assertIsNone(metric.saturation_date)

    def test_annual_method_wins_with_long_history(self) -> None:
        # 400 days of a yearly sine plus a clear linear trend: the annual fit
        # must decide the slope, not a plain line over the daily peaks.
        samples = _daily_series(400, lambda d: 20.0 + 0.1 * d + 5.0 * math.sin(2.0 * math.pi * d / 365.25))
        fit = saturation.fit_growth(samples)
        assert fit is not None
        self.assertEqual(fit.method, "annual")
        self.assertGreater(fit.days, consts.forecasts.MIN_DAYS_FOR_ANNUAL_FIT)
        self.assertAlmostEqual(fit.slope_per_day, 0.1, delta=0.02)

    def test_linear_method_with_short_history(self) -> None:
        samples = _daily_series(56, lambda d: 10.0 + 0.5 * d)
        fit = saturation.fit_growth(samples)
        assert fit is not None
        self.assertEqual(fit.method, "linear")
        self.assertAlmostEqual(fit.slope_per_day, 0.5, delta=1e-9)

    def test_fit_growth_needs_two_days(self) -> None:
        self.assertIsNone(saturation.fit_growth(_daily_series(1, lambda d: 10.0)))
        self.assertIsNone(saturation.fit_growth([]))

    def test_daily_peaks_uses_maximum(self) -> None:
        samples = [
            predictor.Sample(when=_utc(2026, 1, 5, 3), mean=5.0, max=30.0, count=6),
            predictor.Sample(when=_utc(2026, 1, 5, 10), mean=9.0, max=50.0, count=6),
            predictor.Sample(when=_utc(2026, 1, 6, 10), mean=4.0, max=8.0, count=6),
        ]
        self.assertEqual(
            saturation.daily_peaks(samples),
            [(_utc(2026, 1, 5).date(), 50.0), (_utc(2026, 1, 6).date(), 8.0)],
        )


class SaturationLoadJoinTest(UDSTestCase):
    """Composite load recomposition from its cpu/memory/users parts."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def test_hour_without_memory_is_dropped_not_zeroed(self) -> None:
        weights = types.servers.ServerStatsWeights()  # .3/.6/.1, max 100 users (already normalized)
        hours = range(24)
        cpu = [predictor.Sample(when=_utc(2026, 1, 5, h), mean=10.0, max=10.0, count=6) for h in hours]
        memory = [s for s in cpu if s.when.hour != 20]  # hour 20 has no memory sample
        users = [predictor.Sample(when=_utc(2026, 1, 5, h), mean=10.0, max=10.0, count=6) for h in hours]

        load = saturation.load_composite_samples(cpu, memory, users, weights)
        self.assertEqual(len(load), 23)
        self.assertEqual([s.when.hour for s in load], [h for h in hours if h != 20])
        # 0.3*10 + 0.6*10 + 0.1*100*min(1, 10/100) == 10.0
        self.assertAlmostEqual(load[0].mean, 10.0)

    def test_users_component_saturates_at_max_expected(self) -> None:
        weights = types.servers.ServerStatsWeights()
        hours = range(24)
        cpu = [predictor.Sample(when=_utc(2026, 1, 5, h), mean=0.0, max=0.0, count=6) for h in hours]
        memory = [predictor.Sample(when=_utc(2026, 1, 5, h), mean=0.0, max=0.0, count=6) for h in hours]
        users = [predictor.Sample(when=_utc(2026, 1, 5, h), mean=500.0, max=500.0, count=6) for h in hours]
        load = saturation.load_composite_samples(cpu, memory, users, weights)
        # The users factor clamps at 1.0, contributing the whole users weight (10 on 0-100)
        self.assertAlmostEqual(load[0].mean, 10.0)


class SaturationThresholdsTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def test_users_falls_back_to_group_weights(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        with _config_value("STATS_SATURATION_USERS", 0), _config_value("STATS_SATURATION_CONNECTIONS", 0):
            thresholds = saturation.resolve_thresholds(group)
        self.assertEqual(thresholds.users, float(group.weights.max_expected_users))
        self.assertIsNone(thresholds.connections)

    def test_global_users_config_wins_over_group(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        with _config_value("STATS_SATURATION_USERS", 250), _config_value("STATS_SATURATION_CONNECTIONS", 50):
            thresholds = saturation.resolve_thresholds(group)
        self.assertEqual(thresholds.users, 250.0)
        self.assertEqual(thresholds.connections, 50.0)

    def test_no_group_without_users_config_disables_metric(self) -> None:
        with _config_value("STATS_SATURATION_USERS", 0), _config_value("STATS_SATURATION_CONNECTIONS", 0):
            thresholds = saturation.resolve_thresholds(None)
        self.assertIsNone(thresholds.users)


class SaturationServerTest(UDSTestCase):
    """server_saturation / group_saturation over seeded StatsCountersAccum."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)
        Cache.delete(consts.forecasts.PROFILE_CACHE_OWNER)

    @typing.override
    def tearDown(self) -> None:
        Cache.delete(consts.forecasts.PROFILE_CACHE_OWNER)
        super().tearDown()

    def _seed_disk(self, server: models.Server, value_for_day: collections.abc.Callable[[int], float]) -> None:
        """Eight weeks of hourly DISK samples ending "now" (stamps mark interval end)."""
        now = timezone.now().replace(minute=0, second=0, microsecond=0)
        total = 8 * 7 * 24
        records = [
            models.StatsCountersAccum(
                owner_type=types.stats.CounterOwnerType.SERVER,
                owner_id=server.id,
                counter_type=COUNTER.DISK,
                interval_type=models.StatsCountersAccum.IntervalType.HOUR,
                stamp=int((now - datetime.timedelta(hours=total - 1 - i)).timestamp()),
                v_count=6,
                v_sum=int(6 * value_for_day(i // 24)),
                v_max=int(value_for_day(i // 24)),
                v_min=0,
            )
            for i in range(total)
        ]
        models.StatsCountersAccum.objects.bulk_create(records)

    def test_unmanaged_server_reports_no_data(self) -> None:
        server = servers_fixtures.create_server()  # no group, no counters
        view = saturation.server_saturation(server)
        self.assertFalse(view.has_data)
        self.assertEqual(view.status, saturation.SaturationStatus.NO_DATA)
        self.assertEqual(view.group_name, "")
        self.assertIsNone(view.weights)

    def test_server_without_group_has_no_load_metric(self) -> None:
        server = servers_fixtures.create_server()
        self._seed_disk(server, lambda d: 50.0)
        view = saturation.server_saturation(server)
        self.assertEqual([m.counter for m in view.metrics], [COUNTER.DISK])  # load needs group weights
        self.assertTrue(view.has_data)
        self.assertEqual(len(view.forecast[COUNTER.DISK]), consts.forecasts.SATURATION_FORECAST_HOURS_DEFAULT)

    def test_load_metric_present_with_group(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=1)
        server = group.servers.get()
        self._seed_disk(server, lambda d: 50.0)
        view = saturation.server_saturation(server)
        counters = [m.counter for m in view.metrics]
        self.assertIn(COUNTER.LOAD, counters)
        self.assertIn(COUNTER.DISK, counters)
        # cpu/memory/users were never seeded: the load join yields nothing
        load_metric = next(m for m in view.metrics if m.counter is COUNTER.LOAD)
        self.assertEqual(load_metric.status, saturation.SaturationStatus.NO_DATA)

    def test_metrics_filter_restricts_evaluation(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=1)
        server = group.servers.get()
        self._seed_disk(server, lambda d: 50.0)
        view = saturation.server_saturation(server, metrics=[COUNTER.DISK])
        self.assertEqual([m.counter for m in view.metrics], [COUNTER.DISK])

    def test_seeded_growing_disk_projects_within_horizon(self) -> None:
        server = servers_fixtures.create_server()  # no group: only DISK is evaluated
        self._seed_disk(server, lambda d: 50.0 + 0.25 * d)
        view = saturation.server_saturation(server)
        disk = next(m for m in view.metrics if m.counter is COUNTER.DISK)
        self.assertEqual(disk.status, saturation.SaturationStatus.GROWING)
        assert disk.growth is not None
        # v_max is an integer column, so the seeded staircase fits at ~0.25/day
        self.assertAlmostEqual(disk.growth.slope_per_day, 0.25, delta=0.02)
        assert disk.days_to_saturation is not None
        self.assertGreater(disk.days_to_saturation, 0)
        self.assertLessEqual(disk.days_to_saturation, consts.forecasts.SATURATION_MAX_HORIZON_DAYS)
        self.assertIsNotNone(disk.saturation_date)
        self.assertEqual(view.saturating_in_days(), disk.days_to_saturation)

    def test_seeded_slow_growth_falls_beyond_horizon(self) -> None:
        server = servers_fixtures.create_server()
        self._seed_disk(server, lambda d: 50.0 + 0.05 * d)
        view = saturation.server_saturation(server)
        disk = next(m for m in view.metrics if m.counter is COUNTER.DISK)
        assert disk.growth is not None
        self.assertGreater(disk.growth.slope_per_day, 0.0)
        self.assertEqual(disk.status, saturation.SaturationStatus.GROWING_BEYOND_HORIZON)
        self.assertIsNone(disk.days_to_saturation)
        self.assertIsNone(disk.saturation_date)
        self.assertIsNone(view.saturating_in_days())

    def test_cached_view_survives_counter_removal(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=1)
        server = group.servers.get()
        self._seed_disk(server, lambda d: 50.0)
        first = saturation.server_saturation_cached(server)
        models.StatsCountersAccum.objects.all().delete()
        second = saturation.server_saturation_cached(server)
        self.assertEqual([m.status for m in second.metrics], [m.status for m in first.metrics])
        self.assertTrue(second.has_data)  # served from the cache, not the emptied table

    def test_group_rollup_takes_the_first_to_saturate(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=2)
        hot, cold = sorted(group.servers.all(), key=lambda s: s.id)
        self._seed_disk(hot, lambda d: 60.0 + 0.5 * d)
        self._seed_disk(cold, lambda d: 60.0)

        rollup = saturation.group_saturation(group)
        self.assertEqual(rollup.status, saturation.SaturationStatus.GROWING)
        self.assertEqual(len(rollup.per_server), 2)
        by_label = {r.label: r for r in rollup.per_server}
        self.assertEqual(by_label[hot.hostname].status, saturation.SaturationStatus.GROWING)
        self.assertEqual(by_label[cold.hostname].status, saturation.SaturationStatus.STABLE)
        # Only the hot server projects: the group minimum is its days
        self.assertIsNotNone(by_label[hot.hostname].saturating_in_days)
        self.assertIsNone(by_label[cold.hostname].saturating_in_days)
        self.assertEqual(rollup.min_days_to_saturation, by_label[hot.hostname].saturating_in_days)
        self.assertEqual(rollup.worst_server_label, hot.hostname)
        self.assertEqual(rollup.group_name, group.name)

    def test_group_rollup_reports_the_saturated_server(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=2)
        hot, cold = sorted(group.servers.all(), key=lambda s: s.id)
        self._seed_disk(hot, lambda d: 96.0)  # over the default disk threshold (95)
        self._seed_disk(cold, lambda d: 50.0)

        rollup = saturation.group_saturation(group)
        self.assertEqual(rollup.status, saturation.SaturationStatus.SATURATED)
        self.assertEqual(rollup.worst_server_label, hot.hostname)


class SaturationForecastTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def test_forecast_flags_saturated_hours(self) -> None:
        samples = _daily_series(56, lambda d: 90.0)
        points = saturation.build_forecast(_profile(samples), 85.0, _now_after(56), 24)
        self.assertEqual(len(points), 24)
        self.assertTrue(all(p.saturated for p in points))
        self.assertAlmostEqual(points[0].value, 90.0)

    def test_forecast_without_data(self) -> None:
        points = saturation.build_forecast(_profile([]), 85.0, _utc(2026, 3, 2), 12)
        self.assertEqual(len(points), 12)
        self.assertTrue(all(not p.saturated and p.value == 0.0 for p in points))


class SaturationStatusTest(UDSTestCase):
    def test_worst_status_ordering(self) -> None:
        s = saturation.SaturationStatus
        self.assertEqual(saturation.worst_status([s.STABLE, s.GROWING]), s.GROWING)
        self.assertEqual(saturation.worst_status([s.GROWING, s.SATURATED]), s.SATURATED)
        self.assertEqual(saturation.worst_status([s.NO_DATA, s.UNRELIABLE]), s.UNRELIABLE)
        self.assertEqual(saturation.worst_status([s.SHRINKING, s.STABLE]), s.STABLE)
        self.assertEqual(saturation.worst_status([]), s.NO_DATA)

    def test_status_severity_ranks_worst_first(self) -> None:
        s = saturation.SaturationStatus
        ranks = [saturation.status_severity(status) for status in s]
        self.assertEqual(sorted(ranks), list(range(len(ranks))))
        self.assertLess(saturation.status_severity(s.SATURATED), saturation.status_severity(s.GROWING))
        self.assertLess(saturation.status_severity(s.GROWING), saturation.status_severity(s.STABLE))
        self.assertEqual(saturation.status_severity(s.NO_DATA), len(ranks) - 1)

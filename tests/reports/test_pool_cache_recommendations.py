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
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
import csv

"""
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import datetime
import typing

from django.utils import timezone

from uds.core import consts, types
from uds.core.util.stats import predictor
from uds.reports.stats import pool_cache_recommendations

from ..fixtures import services as services_fixtures
from ..utils.test import UDSTestCase


def _utc(year: int, month: int, day: int, hour: int = 0) -> datetime.datetime:
    return datetime.datetime(year, month, day, hour, tzinfo=datetime.timezone.utc)


def _profile_with_value(value: float, counter: types.stats.CounterType) -> predictor.Profile:
    """Builds a profile where every (dow, hour) cell holds the same value."""
    samples = [
        predictor.Sample(
            when=_utc(2026, 1, 5) + datetime.timedelta(days=d, hours=h), mean=value, max=value, count=6
        )
        for d in range(14)
        for h in range(24)
    ]
    return predictor.build_profile(samples, owner_id=1, counter_type=counter)


class PoolCacheRecommendationsReportTest(UDSTestCase):
    """Tests the data assembly of the report, with the profiles stubbed out."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)
        self.service = services_fixtures.create_db_service(services_fixtures.create_db_provider())

    def _report(
        self, usage_by_pool: dict[int, tuple[float, float]]
    ) -> pool_cache_recommendations.PoolCacheRecommendationsReport:
        """Returns a report whose profiles yield the given (inuse, cached) per pool."""

        def get_profile(
            owner_id: int, counter_type: types.stats.CounterType, **_kwargs: typing.Any
        ) -> predictor.Profile:
            inuse, cached = usage_by_pool[owner_id]
            value = inuse if counter_type == types.stats.CounterType.INUSE else cached
            return _profile_with_value(value, counter_type)

        report = pool_cache_recommendations.PoolCacheRecommendationsReport()
        report.pools.value = ["0-0-0-0"]
        self.patch_profiles(get_profile)
        return report

    def patch_profiles(self, get_profile: typing.Any) -> None:
        original = predictor.get_profile
        predictor.get_profile = get_profile
        self.addCleanup(setattr, predictor, "get_profile", original)

    def _pool(self, **kwargs: typing.Any) -> typing.Any:
        pool = services_fixtures.create_db_servicepool(self.service)
        for field, value in kwargs.items():
            setattr(pool, field, value)
        pool.save()
        return pool

    def test_inactive_pools_are_excluded(self) -> None:
        active = self._pool(cache_l1_srvs=10, max_srvs=50)
        inactive = self._pool(cache_l1_srvs=10, max_srvs=50, state=types.states.State.REMOVED)
        report = self._report({active.id: (5.0, 0.0), inactive.id: (5.0, 0.0)})
        names = [p["pool"] for p in report.get_data()["pools"]]
        self.assertEqual(names, [active.name])

    def test_bands_and_hourly_detail_are_reported(self) -> None:
        pool = self._pool(cache_l1_srvs=10, max_srvs=50)
        report = self._report({pool.id: (15.0, 2.0)})
        data = report.get_data()["pools"][0]
        self.assertEqual(len(data["bands"]), 3)
        self.assertEqual(len(data["slots"]), 24)
        self.assertEqual(data["verdict"], "STARVED")
        self.assertTrue(all(b["verdict"] == "STARVED" for b in data["bands"]))

    def test_starved_pool_suggests_a_calendar_action(self) -> None:
        pool = self._pool(cache_l1_srvs=10, max_srvs=50)
        report = self._report({pool.id: (15.0, 2.0)})
        actions = report.get_data()["pools"][0]["calendar_actions"]
        self.assertEqual(len(actions), 3)
        self.assertTrue(all(a["action"] == "CACHEL1" for a in actions))
        self.assertTrue(all(a["size"] == 16 for a in actions))

    def test_healthy_pool_suggests_nothing(self) -> None:
        pool = self._pool(cache_l1_srvs=10, max_srvs=50)
        report = self._report({pool.id: (5.0, 0.0)})
        data = report.get_data()["pools"][0]
        self.assertEqual(data["verdict"], "OK")
        self.assertEqual(data["calendar_actions"], [])

    def test_pools_sorted_by_severity(self) -> None:
        healthy = self._pool(cache_l1_srvs=10, max_srvs=50)
        starved = self._pool(cache_l1_srvs=10, max_srvs=50)
        saturated = self._pool(cache_l1_srvs=10, max_srvs=50)
        report = self._report(
            {healthy.id: (5.0, 0.0), starved.id: (15.0, 2.0), saturated.id: (50.0, 0.0)}
        )
        verdicts = [p["verdict"] for p in report.get_data()["pools"]]
        self.assertEqual(verdicts, ["SATURATED", "STARVED", "OK"])

    def test_cross_pool_pairs_surplus_with_hungry(self) -> None:
        hungry = self._pool(cache_l1_srvs=10, max_srvs=50)
        spare = self._pool(cache_l1_srvs=10, max_srvs=50)
        report = self._report({hungry.id: (15.0, 2.0), spare.id: (2.0, 8.0)})
        cross = report.get_data()["cross_pool"]
        self.assertEqual(len(cross), 24)
        self.assertEqual(cross[0]["hungry"], hungry.name)
        self.assertEqual(cross[0]["surplus"], spare.name)
        self.assertEqual(cross[0]["lendable"], 8)

    def test_header_carries_retention_warning_data(self) -> None:
        pool = self._pool(cache_l1_srvs=1, max_srvs=10)
        data = self._report({pool.id: (1.0, 0.0)}).get_data()
        self.assertGreater(data["stats_duration"], 0)
        self.assertEqual(data["training_weeks"], consts.predictions.TRAINING_WEEKS)

"""
Copyright (c) 2026 Virtual Cable S.L.
All rights reserved.

Redistribution and use in source and binary forms, with or without modification,
are permitted provided that the following conditions are met:

   * Redistributions of source code must retain the above copyright notice,
     this list of conditions and the following disclaimer.
   * Redistributions in binary form must reproduce the above copyright notice,
     this list of conditions and the following disclaimer in the documentation
     and/or other materials provided with the distribution.
   * Neither the name of Virtual Cable S.L. nor the names of its contributors
     may be used to endorse or promote products derived from this software
     without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import collections.abc
import datetime
import typing

from django.utils import timezone

from uds import models
from uds.core import consts, types
from uds.core.util.cache import Cache
from uds.core.util.stats import saturation

from ....fixtures import servers as servers_fixtures
from ....utils import rest

COUNTER = types.stats.CounterType


class ServersForecastTest(rest.test.RESTTestCase):
    """Tests for the server/group saturation forecast REST custom methods."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)
        Cache.delete(consts.forecasts.PROFILE_CACHE_OWNER)

    @typing.override
    def tearDown(self) -> None:
        Cache.delete(consts.forecasts.PROFILE_CACHE_OWNER)
        super().tearDown()

    def _create_group_with_server(self) -> tuple[models.ServerGroup, models.Server]:
        group = servers_fixtures.create_server_group(num_servers=1)
        return group, group.servers.get()

    @staticmethod
    def _server_forecast_url(group: models.ServerGroup, server: models.Server) -> str:
        return f"servers/groups/{group.uuid}/servers/{server.uuid}/forecast"

    def _seed_disk(self, server: models.Server, value_for_day: collections.abc.Callable[[int], float]) -> None:
        """Eight weeks of hourly DISK samples ending "now" (same shape as the core tests)."""
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

    def test_forecast_requires_login(self) -> None:
        group, server = self._create_group_with_server()
        response = self.client.rest_get(self._server_forecast_url(group, server))
        self.assertEqual(response.status_code, 403)

    def test_server_forecast_shape_and_growth(self) -> None:
        self.login()
        group, server = self._create_group_with_server()
        # gentle ramp so the recent window does not trip the anomaly gate
        self._seed_disk(server, lambda d: 50.0 + 0.25 * d)

        response = self.client.rest_get(self._server_forecast_url(group, server))

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["id"], server.uuid)
        self.assertEqual(body["group"], group.name)
        self.assertTrue(body["has_data"])
        # the group has weights, so the composite load is evaluated (empty: only disk seeded)
        counters = [m["counter"] for m in body["metrics"]]
        self.assertIn("load", counters)
        self.assertIn("disk", counters)
        disk = next(m for m in body["metrics"] if m["counter"] == "disk")
        self.assertEqual(disk["status"], str(saturation.SaturationStatus.GROWING))
        self.assertIsNotNone(disk["saturation_date"])
        self.assertTrue(disk["projected"])
        self.assertEqual(disk["threshold"], consts.forecasts.SATURATION_DISK_DEFAULT)
        assert disk["growth"] is not None
        self.assertEqual(disk["growth"]["method"], "linear")
        # hourly forecast points under every evaluated counter, epoch-stamps ascending
        points = body["forecast"]["disk"]
        self.assertEqual(len(points), consts.forecasts.SATURATION_FORECAST_HOURS_DEFAULT)
        stamps = [p["stamp"] for p in points]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(isinstance(p["saturated"], bool) for p in points))

    def test_server_forecast_metric_filter(self) -> None:
        self.login()
        group, server = self._create_group_with_server()
        self._seed_disk(server, lambda d: 50.0)

        response = self.client.rest_get(f"{self._server_forecast_url(group, server)}?metric=disk&hours=24")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual([m["counter"] for m in body["metrics"]], ["disk"])
        self.assertEqual(len(body["forecast"]["disk"]), 24)

    def test_server_forecast_unknown_metric_is_bad_request(self) -> None:
        self.login()
        group, server = self._create_group_with_server()

        response = self.client.rest_get(f"{self._server_forecast_url(group, server)}?metric=bogus")

        self.assertEqual(response.status_code, 400, response.content)

    def test_server_forecast_hours_are_capped(self) -> None:
        self.login()
        group, server = self._create_group_with_server()
        self._seed_disk(server, lambda d: 50.0)

        response = self.client.rest_get(f"{self._server_forecast_url(group, server)}?hours=99999")

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            len(response.json()["forecast"]["disk"]), consts.forecasts.SATURATION_FORECAST_HOURS_MAX
        )

    def test_group_forecast_rollup(self) -> None:
        self.login()
        group = servers_fixtures.create_server_group(num_servers=2)
        hot, cold = sorted(group.servers.all(), key=lambda s: s.id)
        self._seed_disk(hot, lambda d: 96.0)  # over the default disk threshold (95)
        self._seed_disk(cold, lambda d: 50.0)

        response = self.client.rest_get(f"servers/groups/{group.uuid}/forecast")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body["group"], group.name)
        self.assertEqual(body["status"], str(saturation.SaturationStatus.SATURATED))
        self.assertEqual(body["worst_server"], hot.hostname)
        self.assertEqual(len(body["per_server"]), 2)
        by_id = {row["id"]: row for row in body["per_server"]}
        self.assertEqual(by_id[hot.uuid]["status"], str(saturation.SaturationStatus.SATURATED))
        self.assertEqual(by_id[hot.uuid]["saturating_in_days"], 0)
        self.assertEqual(by_id[cold.uuid]["status"], str(saturation.SaturationStatus.STABLE))

    def test_group_forecast_requires_login(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=0)
        response = self.client.rest_get(f"servers/groups/{group.uuid}/forecast")
        self.assertEqual(response.status_code, 403)

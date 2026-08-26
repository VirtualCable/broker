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
"""
Tests for the server stats chart REST endpoint (ServersServers.stats custom method).
"""

import datetime

from django.utils import timezone

from uds import models
from uds.core import types

from ....fixtures import servers as servers_fixtures
from ....utils import rest


class ServersStatsTest(rest.test.RESTTestCase):
    def _create_group_with_server(self) -> tuple[models.ServerGroup, models.Server]:
        group = servers_fixtures.create_server_group(num_servers=1)
        return group, group.servers.get()

    @staticmethod
    def _stats_url(group: models.ServerGroup, server: models.Server) -> str:
        return f"servers/groups/{group.uuid}/servers/{server.uuid}/stats"

    def _seed_hour_accum(
        self, server: models.Server, counter: types.stats.CounterType, hours: int = 24, value: int = 50
    ) -> None:
        now = timezone.now()
        records = [
            models.StatsCountersAccum(
                owner_type=types.stats.CounterOwnerType.SERVER,
                owner_id=server.id,
                counter_type=counter,
                interval_type=models.StatsCountersAccum.IntervalType.HOUR,
                stamp=int(
                    (now - datetime.timedelta(hours=hours - i)).replace(minute=0, second=0, microsecond=0).timestamp()
                ),
                v_count=6,
                v_sum=6 * value,
                v_max=value,
                v_min=0,
            )
            for i in range(hours)
        ]
        models.StatsCountersAccum.objects.bulk_create(records)

    def _seed_day_accum(
        self, server: models.Server, counter: types.stats.CounterType, days: int = 7, value: int = 40
    ) -> None:
        # DAY buckets are aligned to UTC day boundaries (epoch % 86400), same convention
        # used by the accumulator, so the query window lines up with the records.
        epoch = int(timezone.now().timestamp())
        day_bucket = epoch - (epoch % 86400)
        records = [
            models.StatsCountersAccum(
                owner_type=types.stats.CounterOwnerType.SERVER,
                owner_id=server.id,
                counter_type=counter,
                interval_type=models.StatsCountersAccum.IntervalType.DAY,
                stamp=day_bucket - i * 86400,
                v_count=24,
                v_sum=24 * value,
                v_max=value,
                v_min=0,
            )
            for i in range(1, days + 1)
        ]
        models.StatsCountersAccum.objects.bulk_create(records)

    def test_stats_requires_login(self) -> None:
        group, server = self._create_group_with_server()
        response = self.client.rest_get(self._stats_url(group, server))
        self.assertEqual(response.status_code, 403)

    def test_stats_hour_returns_accumulated_points(self) -> None:
        self.login()
        group, server = self._create_group_with_server()
        self._seed_hour_accum(server, types.stats.CounterType.CPU)

        response = self.client.rest_get(f"{self._stats_url(group, server)}?counter=cpu&interval=hour&since=1")

        self.assertEqual(response.status_code, 200, response.content)
        points = response.json()
        self.assertEqual(len(points), 24)
        self.assertTrue(all(p["value"] == 50 for p in points))
        # Stamps are epoch seconds, ascending
        stamps = [p["stamp"] for p in points]
        self.assertEqual(stamps, sorted(stamps))
        self.assertTrue(all(isinstance(s, int) for s in stamps))

    def test_stats_day_returns_accumulated_points(self) -> None:
        self.login()
        group, server = self._create_group_with_server()
        self._seed_day_accum(server, types.stats.CounterType.MEMORY)

        response = self.client.rest_get(f"{self._stats_url(group, server)}?counter=memory&interval=day&since=7")

        self.assertEqual(response.status_code, 200, response.content)
        points = response.json()
        self.assertEqual(len(points), 7)
        self.assertTrue(all(p["value"] == 40 for p in points))

    def test_stats_empty_server_is_zero_padded(self) -> None:
        self.login()
        group, server = self._create_group_with_server()

        response = self.client.rest_get(f"{self._stats_url(group, server)}?counter=cpu&interval=hour&since=1")

        self.assertEqual(response.status_code, 200, response.content)
        points = response.json()
        self.assertEqual(len(points), 24)
        self.assertTrue(all(p["value"] == 0 for p in points))

    def test_stats_unknown_counter_defaults_to_cpu(self) -> None:
        self.login()
        group, server = self._create_group_with_server()
        self._seed_hour_accum(server, types.stats.CounterType.CPU)

        response = self.client.rest_get(f"{self._stats_url(group, server)}?counter=bogus&interval=hour&since=1")

        self.assertEqual(response.status_code, 200, response.content)
        points = response.json()
        self.assertEqual(len(points), 24)
        self.assertTrue(all(p["value"] == 50 for p in points))

    def test_stats_all_returns_all_counters(self) -> None:
        self.login()
        group, server = self._create_group_with_server()
        self._seed_hour_accum(server, types.stats.CounterType.CPU)

        # No counter param -> defaults to "all"
        response = self.client.rest_get(f"{self._stats_url(group, server)}?interval=hour&since=1")

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(set(body.keys()), {"cpu", "memory", "users", "connections", "disk"})
        # cpu is seeded, the rest are zero-padded
        self.assertEqual(len(body["cpu"]), 24)
        self.assertTrue(all(p["value"] == 50 for p in body["cpu"]))
        for name in ("memory", "users", "connections", "disk"):
            self.assertEqual(len(body[name]), 24)
            self.assertTrue(all(p["value"] == 0 for p in body[name]))

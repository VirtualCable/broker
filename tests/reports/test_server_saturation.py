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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import csv
import datetime
import io
import typing

from django.template.loader import render_to_string
from django.utils import timezone

from uds import models
from uds.core import types
from uds.core.util.stats import saturation
from uds.reports.stats import server_saturation

from ..fixtures import servers as servers_fixtures
from ..utils.test import UDSTestCase

SaturationStatus = saturation.SaturationStatus

THRESHOLDS = saturation.SaturationThresholds(load=80.0, disk=90.0, users=100.0, connections=None)


def _view(
    server: models.Server,
    group_name: str,
    status: SaturationStatus,
    *,
    days: int | None = None,
    weeks: float = 40.0,
) -> saturation.ServerSaturation:
    saturation_date: datetime.datetime | None = None
    if days is not None and status is not SaturationStatus.SATURATED:
        saturation_date = timezone.now() + datetime.timedelta(days=days)
    metric = saturation.MetricSaturation(
        counter=types.stats.CounterType.LOAD,
        status=status,
        current_p90=70.0,
        current_max=85.0,
        threshold=THRESHOLDS.load,
        growth=saturation.GrowthFit(slope_per_day=0.5, intercept=1.0, r2=0.9, method="linear", days=60),
        days_to_saturation=days,
        saturation_date=saturation_date,
        confidence=0.8,
        weeks_of_history=weeks,
    )
    return saturation.ServerSaturation(
        server_id=server.id,
        server_uuid=server.uuid,
        label=server.hostname,
        group_name=group_name,
        weights=None,
        thresholds=THRESHOLDS,
        metrics=[metric],
        forecast={},
    )


class ServerSaturationReportTest(UDSTestCase):
    """Tests the data assembly of the report, with the saturation engine stubbed out."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)

    def _patch_engine(self, views: dict[int, saturation.ServerSaturation]) -> None:
        original = saturation.server_saturation_cached
        saturation.server_saturation_cached = lambda server, **_kwargs: views[server.id]
        self.addCleanup(setattr, saturation, "server_saturation_cached", original)

    def _report(self) -> server_saturation.ServerSaturationReport:
        report = server_saturation.ServerSaturationReport()
        report.server_groups.value = ["0-0-0-0"]
        return report

    def _group(self, num_servers: int) -> models.ServerGroup:
        return servers_fixtures.create_server_group(num_servers=num_servers)

    def test_servers_sorted_worst_first(self) -> None:
        group = self._group(3)
        stable, growing, saturated = list(group.servers.all().order_by("hostname", "ip"))
        self._patch_engine(
            {
                stable.id: _view(stable, group.name, SaturationStatus.STABLE),
                growing.id: _view(growing, group.name, SaturationStatus.GROWING, days=30),
                saturated.id: _view(saturated, group.name, SaturationStatus.SATURATED),
            }
        )
        servers = self._report().get_data()["groups"][0]["servers"]
        self.assertEqual(
            [s["server"] for s in servers],
            [saturated.hostname, growing.hostname, stable.hostname],
        )

    def test_same_status_sorted_by_soonest_saturation(self) -> None:
        group = self._group(2)
        far, soon = list(group.servers.all().order_by("hostname", "ip"))
        self._patch_engine(
            {
                far.id: _view(far, group.name, SaturationStatus.GROWING, days=90),
                soon.id: _view(soon, group.name, SaturationStatus.GROWING, days=5),
            }
        )
        servers = self._report().get_data()["groups"][0]["servers"]
        self.assertEqual([s["server"] for s in servers], [soon.hostname, far.hostname])

    def test_attention_list_only_carries_saturated_and_growing(self) -> None:
        group = self._group(3)
        stable, growing, saturated = list(group.servers.all().order_by("hostname", "ip"))
        self._patch_engine(
            {
                stable.id: _view(stable, group.name, SaturationStatus.STABLE),
                growing.id: _view(growing, group.name, SaturationStatus.GROWING, days=30),
                saturated.id: _view(saturated, group.name, SaturationStatus.SATURATED),
            }
        )
        attention = self._report().get_data()["attention"]
        self.assertEqual([s["server"] for s in attention], [saturated.hostname, growing.hostname])

    def test_short_history_is_flagged(self) -> None:
        group = self._group(2)
        long_history, short_history = list(group.servers.all().order_by("hostname", "ip"))
        self._patch_engine(
            {
                long_history.id: _view(long_history, group.name, SaturationStatus.STABLE, weeks=30.0),
                short_history.id: _view(
                    short_history, group.name, SaturationStatus.UNRELIABLE, weeks=4.0
                ),
            }
        )
        flags = {s["server"]: s["short_history"] for s in self._report().get_data()["groups"][0]["servers"]}
        self.assertFalse(flags[long_history.hostname])
        self.assertTrue(flags[short_history.hostname])

    def test_unmanaged_groups_are_excluded(self) -> None:
        managed = self._group(1)
        unmanaged = servers_fixtures.create_server_group(
            type=types.servers.ServerType.UNMANAGED, num_servers=1
        )
        server = managed.servers.first()
        assert server is not None
        self._patch_engine({server.id: _view(server, managed.name, SaturationStatus.STABLE)})
        names = [g["group"] for g in self._report().get_data()["groups"]]
        self.assertIn(managed.name, names)
        self.assertNotIn(unmanaged.name, names)

    def test_template_renders_every_group(self) -> None:
        group = self._group(1)
        server = group.servers.first()
        assert server is not None
        self._patch_engine({server.id: _view(server, group.name, SaturationStatus.GROWING, days=30)})
        rendered = render_to_string(
            "uds/reports/stats/server-saturation.html", self._report().get_data()
        )
        self.assertIn(group.name, rendered)
        self.assertIn(server.hostname, rendered)

    def test_csv_carries_one_row_per_metric(self) -> None:
        group = self._group(2)
        first, second = list(group.servers.all().order_by("hostname", "ip"))
        self._patch_engine(
            {
                first.id: _view(first, group.name, SaturationStatus.SATURATED),
                second.id: _view(second, group.name, SaturationStatus.STABLE),
            }
        )
        report = server_saturation.ServerSaturationReportCSV()
        report.server_groups.value = ["0-0-0-0"]
        rows = list(csv.reader(io.StringIO(report.generate().decode())))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][1], first.hostname)
        self.assertEqual(rows[2][1], second.hostname)

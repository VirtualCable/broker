#
# Copyright (c) 2026 Virtual Cable S.L.U.
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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
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
Smoke tests for the server-oriented reports. Each report is instantiated with a
minimal valid form payload and `generate()` is invoked, over seeded accumulated
counters, so query/template/chart errors are caught.
"""

import datetime
import logging
import random
import typing

import pytest

from django.utils import timezone

from uds import models
from uds.core import types
from uds.reports.lists.servers import ServersListReport
from uds.reports.lists.servers import ServersListReportCSV
from uds.reports.stats.server_balance import ServerBalanceReport
from uds.reports.stats.server_balance import ServerBalanceReportCSV
from uds.reports.stats.server_load import ServerLoadReport
from uds.reports.stats.server_load import ServerLoadReportCSV
from uds.reports.stats.server_usage import ServerUsageReport
from uds.reports.stats.server_usage import ServerUsageReportCSV

from ...fixtures import servers as fixtures_servers
from ...utils.test import UDSTransactionTestCase

logger: logging.Logger = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.filterwarnings("ignore::UserWarning"),
    pytest.mark.filterwarnings("ignore::DeprecationWarning"),
]

HOURS: typing.Final[int] = 48
SEEDED_COUNTERS: typing.Final[tuple[types.stats.CounterType, ...]] = (
    types.stats.CounterType.CPU,
    types.stats.CounterType.MEMORY,
    types.stats.CounterType.USERS,
    types.stats.CounterType.CONNECTIONS,
    types.stats.CounterType.DISK,
)


class _ServerReportsBase(UDSTransactionTestCase):
    group: models.ServerGroup
    start_date: datetime.date
    end_date: datetime.date

    def setUp(self) -> None:
        super().setUp()
        self.group = fixtures_servers.create_server_group(
            type=types.servers.ServerType.SERVER, subtype="rds", num_servers=3
        )
        end = timezone.now().replace(minute=0, second=0, microsecond=0)
        start = end - datetime.timedelta(hours=HOURS)
        self.start_date = start.date()
        self.end_date = end.date()
        self._seed_accumulated(int(start.timestamp()))

    def _seed_accumulated(self, start_stamp: int) -> None:
        rng = random.Random(0)
        rows: list[models.StatsCountersAccum] = []
        start_stamp -= start_stamp % 3600
        for server in self.group.servers.all():
            for counter_type in SEEDED_COUNTERS:
                for hour in range(HOURS):
                    values = [rng.randint(0, 100) for _ in range(6)]
                    rows.append(
                        models.StatsCountersAccum(
                            owner_id=server.id,
                            owner_type=types.stats.CounterOwnerType.SERVER,
                            counter_type=counter_type,
                            interval_type=models.StatsCountersAccum.IntervalType.HOUR,
                            stamp=start_stamp + hour * 3600,
                            v_count=len(values),
                            v_sum=sum(values),
                            v_max=max(values),
                            v_min=min(values),
                        )
                    )
        models.StatsCountersAccum.objects.bulk_create(rows)


class ServerUsageTest(_ServerReportsBase):
    def test_generate(self) -> None:
        for cls in (ServerUsageReport, ServerUsageReportCSV):
            r = cls()
            r.server_groups.value = [self.group.uuid]  # type: ignore[assignment]
            r.start_date.value = self.start_date
            r.end_date.value = self.end_date
            self.assertGreater(len(r.generate()), 0)

    def test_summary_matches_seeded_counters(self) -> None:
        r = ServerUsageReport()
        r.server_groups.value = [self.group.uuid]  # type: ignore[assignment]
        r.start_date.value = self.start_date
        r.end_date.value = self.end_date

        _stamps, _series, summary = r.get_data()

        self.assertEqual(len(summary), self.group.servers.count())
        for row in summary:
            self.assertEqual(row["group"], self.group.name)
            # Each seeded hour carries 6 samples, and the report must not drop any of them
            self.assertEqual(row["samples"], HOURS * 6)
            self.assertGreater(row["peak_users"], 0)
            self.assertGreaterEqual(row["peak_users"], float(row["avg_users"]))

    def test_range_without_data_reports_zero(self) -> None:
        # The accumulated-counters window must follow the requested dates, not "now"
        old_day = (timezone.now() - datetime.timedelta(days=20)).date()

        r = ServerUsageReport()
        r.server_groups.value = [self.group.uuid]  # type: ignore[assignment]
        r.start_date.value = old_day
        r.end_date.value = old_day

        _stamps, _series, summary = r.get_data()

        self.assertEqual(len(summary), self.group.servers.count())
        for row in summary:
            self.assertEqual(row["samples"], 0)
            self.assertEqual(row["peak_users"], 0)

    def test_series_share_one_x_axis(self) -> None:
        # Servers report for different spans; charting needs one bucket grid for all of them
        partial = self.group.servers.all()[0]
        models.StatsCountersAccum.objects.filter(
            owner_type=types.stats.CounterOwnerType.SERVER, owner_id=partial.id
        ).delete()

        r = ServerUsageReport()
        r.server_groups.value = [self.group.uuid]  # type: ignore[assignment]
        r.start_date.value = self.start_date
        r.end_date.value = self.end_date

        stamps, series, _summary = r.get_data()

        self.assertEqual(len(series), self.group.servers.count())
        for entry in series:
            self.assertEqual(len(entry["users"]), len(stamps))
            self.assertEqual(len(entry["connections"]), len(stamps))
        self.assertGreater(len(r.generate()), 0)


class ServerLoadTest(_ServerReportsBase):
    def test_generate(self) -> None:
        for cls in (ServerLoadReport, ServerLoadReportCSV):
            r = cls()
            r.server_groups.value = [self.group.uuid]  # type: ignore[assignment]
            r.start_date.value = self.start_date
            r.end_date.value = self.end_date
            r.threshold.value = 80
            self.assertGreater(len(r.generate()), 0)

    def test_threshold_bounds_overloaded_hours(self) -> None:
        def _overloaded(threshold: int) -> list[int]:
            r = ServerLoadReport()
            r.server_groups.value = [self.group.uuid]  # type: ignore[assignment]
            r.start_date.value = self.start_date
            r.end_date.value = self.end_date
            r.threshold.value = threshold
            return [row["hours_overloaded"] for row in r.get_data()]

        never = _overloaded(100)
        always = _overloaded(1)
        for low, high in zip(never, always):
            self.assertLessEqual(low, high)
        self.assertEqual(always, [HOURS] * self.group.servers.count())


class ServerBalanceTest(_ServerReportsBase):
    def test_generate(self) -> None:
        for cls in (ServerBalanceReport, ServerBalanceReportCSV):
            r = cls()
            r.server_groups.value = [self.group.uuid]  # type: ignore[assignment]
            r.start_date.value = self.start_date
            r.end_date.value = self.end_date
            self.assertGreater(len(r.generate()), 0)

    def test_shares_add_up_to_one_hundred(self) -> None:
        r = ServerBalanceReport()
        r.server_groups.value = [self.group.uuid]  # type: ignore[assignment]
        r.start_date.value = self.start_date
        r.end_date.value = self.end_date

        groups_rows, servers_rows = r.get_data()

        self.assertEqual(len(groups_rows), 1)
        self.assertEqual(groups_rows[0]["idle_servers"], 0)
        self.assertAlmostEqual(sum(row["share_value"] for row in servers_rows), 100.0, places=6)


class ServersListTest(_ServerReportsBase):
    def test_generate(self) -> None:
        for cls in (ServersListReport, ServersListReportCSV):
            r = cls()
            r.server_group.value = "0-0-0-0"  # type: ignore[assignment]
            r.only_stale.value = False
            self.assertGreater(len(r.generate()), 0)

    def test_only_stale_drops_servers_reporting_now(self) -> None:
        reporting = self.group.servers.all()[0]
        reporting.stats = types.servers.ServerStats(memtotal=1024, memused=512, current_users=1)

        def _listed(only_stale: bool) -> list[str]:
            r = ServersListReport()
            r.server_group.value = "0-0-0-0"  # type: ignore[assignment]
            r.only_stale.value = only_stale
            return [row["server"] for row in r.get_data()]

        self.assertIn(reporting.hostname, _listed(False))
        self.assertNotIn(reporting.hostname, _listed(True))
        self.assertEqual(len(_listed(True)), self.group.servers.count() - 1)

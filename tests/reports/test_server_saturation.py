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

import collections.abc
import csv
import datetime
import io
import typing
from unittest import mock

from django.template.loader import render_to_string
from django.utils import timezone

from uds import models
from uds.core import consts
from uds.core import reports
from uds.core import types
from uds.core.util.cache import Cache
from uds.core.util.stats import saturation
from uds.reports.stats import server_saturation

from ..fixtures import servers as servers_fixtures
from ..utils.test import UDSTestCase

SaturationStatus = saturation.SaturationStatus
COUNTER = types.stats.CounterType

THRESHOLDS = saturation.SaturationThresholds(load=80.0, disk=90.0, users=100.0, connections=None)

GROWTH = saturation.GrowthFit(slope_per_day=0.5, intercept=1.0, r2=0.9, method="linear", days=60)


def _metric(
    counter: types.stats.CounterType,
    status: SaturationStatus,
    *,
    days: int | None = None,
    weeks: float = 40.0,
    p90: float = 70.0,
    maximum: float = 85.0,
    threshold: float = 80.0,
    confidence: float = 0.8,
    growth: saturation.GrowthFit | None = GROWTH,
) -> saturation.MetricSaturation:
    saturation_date: datetime.datetime | None = None
    if days is not None and status is not SaturationStatus.SATURATED:
        saturation_date = timezone.now() + datetime.timedelta(days=days)
    return saturation.MetricSaturation(
        counter=counter,
        status=status,
        current_p90=p90,
        current_max=maximum,
        threshold=threshold,
        growth=growth,
        days_to_saturation=days,
        saturation_date=saturation_date,
        confidence=confidence,
        weeks_of_history=weeks,
    )


def _with_metrics(
    server: models.Server,
    group_name: str,
    metrics: list[saturation.MetricSaturation],
) -> saturation.ServerSaturation:
    return saturation.ServerSaturation(
        server_id=server.id,
        server_uuid=server.uuid,
        label=server.hostname,
        group_name=group_name,
        weights=None,
        thresholds=THRESHOLDS,
        metrics=metrics,
        forecast={},
    )


def _view(
    server: models.Server,
    group_name: str,
    status: SaturationStatus,
    *,
    days: int | None = None,
    weeks: float = 40.0,
) -> saturation.ServerSaturation:
    metric = _metric(COUNTER.LOAD, status, days=days, weeks=weeks, threshold=THRESHOLDS.load or 0.0)
    return _with_metrics(server, group_name, [metric])


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

    def _csv_report(self) -> server_saturation.ServerSaturationReportCSV:
        report = server_saturation.ServerSaturationReportCSV()
        report.server_groups.value = ["0-0-0-0"]
        return report

    def _group(self, num_servers: int) -> models.ServerGroup:
        return servers_fixtures.create_server_group(num_servers=num_servers)

    def _servers_of(self, group: models.ServerGroup) -> list[models.Server]:
        return list(group.servers.all().order_by("hostname", "ip"))

    def test_servers_sorted_worst_first(self) -> None:
        group = self._group(3)
        stable, growing, saturated = self._servers_of(group)
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
        far, soon = self._servers_of(group)
        self._patch_engine(
            {
                far.id: _view(far, group.name, SaturationStatus.GROWING, days=90),
                soon.id: _view(soon, group.name, SaturationStatus.GROWING, days=5),
            }
        )
        servers = self._report().get_data()["groups"][0]["servers"]
        self.assertEqual([s["server"] for s in servers], [soon.hostname, far.hostname])

    def test_server_without_projection_sorts_after_one_that_projects(self) -> None:
        group = self._group(2)
        projects, endless = self._servers_of(group)
        self._patch_engine(
            {
                projects.id: _view(projects, group.name, SaturationStatus.GROWING, days=200),
                endless.id: _view(endless, group.name, SaturationStatus.GROWING),
            }
        )
        servers = self._report().get_data()["groups"][0]["servers"]
        self.assertEqual([s["server"] for s in servers], [projects.hostname, endless.hostname])

    def test_attention_list_only_carries_saturated_and_growing(self) -> None:
        group = self._group(3)
        stable, growing, saturated = self._servers_of(group)
        self._patch_engine(
            {
                stable.id: _view(stable, group.name, SaturationStatus.STABLE),
                growing.id: _view(growing, group.name, SaturationStatus.GROWING, days=30),
                saturated.id: _view(saturated, group.name, SaturationStatus.SATURATED),
            }
        )
        attention = self._report().get_data()["attention"]
        self.assertEqual([s["server"] for s in attention], [saturated.hostname, growing.hostname])

    def test_attention_is_sorted_across_groups(self) -> None:
        first_group = self._group(1)
        second_group = self._group(1)
        soon = first_group.servers.get()
        urgent = second_group.servers.get()
        self._patch_engine(
            {
                soon.id: _view(soon, first_group.name, SaturationStatus.GROWING, days=40),
                urgent.id: _view(urgent, second_group.name, SaturationStatus.SATURATED),
            }
        )
        attention = self._report().get_data()["attention"]
        self.assertEqual([s["server"] for s in attention], [urgent.hostname, soon.hostname])
        self.assertEqual([s["group"] for s in attention], [second_group.name, first_group.name])

    def test_every_selected_group_carries_its_own_servers(self) -> None:
        first_group = self._group(2)
        second_group = self._group(1)
        views = {
            server.id: _view(server, group.name, SaturationStatus.STABLE)
            for group in (first_group, second_group)
            for server in self._servers_of(group)
        }
        self._patch_engine(views)
        groups = {g["group"]: g["servers"] for g in self._report().get_data()["groups"]}
        self.assertEqual(len(groups[first_group.name]), 2)
        self.assertEqual(len(groups[second_group.name]), 1)

    def test_selecting_one_group_excludes_the_others(self) -> None:
        wanted = self._group(1)
        other = self._group(1)
        views = {
            server.id: _view(server, group.name, SaturationStatus.STABLE)
            for group in (wanted, other)
            for server in self._servers_of(group)
        }
        self._patch_engine(views)
        report = self._report()
        report.server_groups.value = [wanted.uuid]
        names = [g["group"] for g in report.get_data()["groups"]]
        self.assertEqual(names, [wanted.name])

    def test_group_without_servers_yields_an_empty_server_list(self) -> None:
        group = self._group(0)
        self._patch_engine({})
        groups = self._report().get_data()["groups"]
        self.assertEqual(groups, [{"group": group.name, "servers": []}])

    def test_selecting_nothing_yields_no_groups(self) -> None:
        self._group(1)
        self._patch_engine({})
        report = self._report()
        report.server_groups.value = []
        data = report.get_data()
        self.assertEqual(data["groups"], [])
        self.assertEqual(data["attention"], [])

    def test_a_history_too_short_to_trust_is_reported_by_the_status(self) -> None:
        group = self._group(2)
        long_history, short_history = self._servers_of(group)
        self._patch_engine(
            {
                long_history.id: _view(long_history, group.name, SaturationStatus.STABLE, weeks=6.0),
                short_history.id: _view(
                    short_history, group.name, SaturationStatus.UNRELIABLE, weeks=0.5
                ),
            }
        )
        names = {s["server"]: s["status_name"] for s in self._report().get_data()["groups"][0]["servers"]}
        self.assertEqual(
            names[long_history.hostname], str(server_saturation.STATUS_NAMES[SaturationStatus.STABLE])
        )
        self.assertEqual(
            names[short_history.hostname], str(server_saturation.STATUS_NAMES[SaturationStatus.UNRELIABLE])
        )

    def test_no_data_server_is_reported_and_sorts_last(self) -> None:
        group = self._group(2)
        stable, empty = self._servers_of(group)
        self._patch_engine(
            {
                stable.id: _view(stable, group.name, SaturationStatus.STABLE),
                empty.id: _with_metrics(empty, group.name, [_metric(COUNTER.DISK, SaturationStatus.NO_DATA)]),
            }
        )
        servers = self._report().get_data()["groups"][0]["servers"]
        self.assertEqual([s["server"] for s in servers], [stable.hostname, empty.hostname])
        self.assertFalse(servers[1]["has_data"])
        self.assertTrue(servers[0]["has_data"])

    def test_server_without_metrics_has_no_data(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        self._patch_engine({server.id: _with_metrics(server, group.name, [])})
        row = self._report().get_data()["groups"][0]["servers"][0]
        self.assertFalse(row["has_data"])
        self.assertEqual(row["status"], SaturationStatus.NO_DATA)
        self.assertEqual(row["metrics"], [])
        self.assertEqual(row["weeks_of_history"], 0.0)

    def test_server_status_is_the_worst_of_its_metrics(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        self._patch_engine(
            {
                server.id: _with_metrics(
                    server,
                    group.name,
                    [
                        _metric(COUNTER.LOAD, SaturationStatus.STABLE),
                        _metric(COUNTER.DISK, SaturationStatus.SATURATED),
                        _metric(COUNTER.USERS, SaturationStatus.SHRINKING),
                    ],
                )
            }
        )
        row = self._report().get_data()["groups"][0]["servers"][0]
        self.assertEqual(row["status"], SaturationStatus.SATURATED)

    def test_earliest_saturation_date_wins_across_metrics(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        self._patch_engine(
            {
                server.id: _with_metrics(
                    server,
                    group.name,
                    [
                        _metric(COUNTER.LOAD, SaturationStatus.GROWING, days=60),
                        _metric(COUNTER.DISK, SaturationStatus.GROWING, days=10),
                    ],
                )
            }
        )
        row = self._report().get_data()["groups"][0]["servers"][0]
        self.assertEqual(row["saturating_in_days"], 10)
        assert row["saturation_date"] is not None
        self.assertEqual(row["saturation_date"], min(m["saturation_date"] for m in row["metrics"]))

    def test_weeks_of_history_is_the_longest_of_its_metrics(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        self._patch_engine(
            {
                server.id: _with_metrics(
                    server,
                    group.name,
                    [
                        _metric(COUNTER.LOAD, SaturationStatus.STABLE, weeks=4.0),
                        _metric(COUNTER.DISK, SaturationStatus.STABLE, weeks=31.0),
                    ],
                )
            }
        )
        row = self._report().get_data()["groups"][0]["servers"][0]
        self.assertEqual(row["weeks_of_history"], 31.0)

    def test_metric_without_growth_reports_no_slope(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        self._patch_engine(
            {
                server.id: _with_metrics(
                    server, group.name, [_metric(COUNTER.DISK, SaturationStatus.NO_DATA, growth=None)]
                )
            }
        )
        metric = self._report().get_data()["groups"][0]["servers"][0]["metrics"][0]
        self.assertIsNone(metric["slope_per_day"])
        self.assertIsNone(metric["r2"])
        self.assertEqual(metric["method"], "")

    def test_metric_values_are_rounded(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        growth = saturation.GrowthFit(
            slope_per_day=0.123456, intercept=1.0, r2=0.9123, method="linear", days=60
        )
        self._patch_engine(
            {
                server.id: _with_metrics(
                    server,
                    group.name,
                    [
                        _metric(
                            COUNTER.DISK,
                            SaturationStatus.GROWING,
                            days=12,
                            weeks=40.28,
                            p90=70.126,
                            maximum=85.984,
                            threshold=80.567,
                            confidence=0.876,
                            growth=growth,
                        )
                    ],
                )
            }
        )
        row = self._report().get_data()["groups"][0]["servers"][0]
        metric = row["metrics"][0]
        self.assertEqual(metric["current_p90"], 70.13)
        self.assertEqual(metric["current_max"], 85.98)
        self.assertEqual(metric["threshold"], 80.57)
        self.assertEqual(metric["confidence"], 0.88)
        self.assertEqual(metric["weeks_of_history"], 40.3)
        self.assertEqual(metric["slope_per_day"], 0.1235)
        self.assertEqual(metric["r2"], 0.91)
        self.assertEqual(row["weeks_of_history"], 40.3)

    def test_every_metric_and_status_has_a_readable_name(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        metrics = [
            _metric(counter, status)
            for counter, status in zip(server_saturation.METRIC_NAMES, server_saturation.STATUS_NAMES)
        ]
        self._patch_engine({server.id: _with_metrics(server, group.name, metrics)})
        rows = self._report().get_data()["groups"][0]["servers"][0]["metrics"]
        self.assertEqual(len(rows), len(metrics))
        for row in rows:
            self.assertTrue(row["metric"])
            self.assertTrue(row["status_name"])

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

    def test_context_carries_the_training_and_stats_windows(self) -> None:
        self._patch_engine({})
        data = self._report().get_data()
        self.assertEqual(data["training_weeks"], consts.forecasts.TRAINING_WEEKS)
        self.assertEqual(data["training_days"], consts.forecasts.TRAINING_WEEKS * 7)
        self.assertGreater(data["stats_duration"], 0)

    def test_init_gui_offers_all_groups_and_hides_unmanaged_ones(self) -> None:
        managed = self._group(1)
        unmanaged = servers_fixtures.create_server_group(
            type=types.servers.ServerType.UNMANAGED, num_servers=1
        )
        report = self._report()
        with mock.patch.object(report.server_groups, "set_choices") as set_choices:
            report.init_gui()
        ids = [choice.id for choice in set_choices.call_args[0][0]]
        self.assertEqual(ids[0], "0-0-0-0")
        self.assertIn(managed.uuid, ids)
        self.assertNotIn(unmanaged.uuid, ids)

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

    def test_template_renders_the_attention_section(self) -> None:
        group = self._group(2)
        stable, saturated = self._servers_of(group)
        self._patch_engine(
            {
                stable.id: _view(stable, group.name, SaturationStatus.STABLE),
                saturated.id: _view(saturated, group.name, SaturationStatus.SATURATED),
            }
        )
        rendered = render_to_string("uds/reports/stats/server-saturation.html", self._report().get_data())
        self.assertIn("Servers needing attention", rendered)

    def test_template_says_when_a_group_has_no_servers(self) -> None:
        self._group(0)
        self._patch_engine({})
        rendered = render_to_string("uds/reports/stats/server-saturation.html", self._report().get_data())
        self.assertIn("The group has no servers.", rendered)
        self.assertNotIn("Servers needing attention", rendered)

    def test_template_says_when_nothing_is_selected(self) -> None:
        self._patch_engine({})
        report = self._report()
        report.server_groups.value = []
        rendered = render_to_string("uds/reports/stats/server-saturation.html", report.get_data())
        self.assertIn("No server groups selected.", rendered)

    def test_generate_returns_a_pdf(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        self._patch_engine({server.id: _view(server, group.name, SaturationStatus.GROWING, days=30)})
        self.assertTrue(self._report().generate().startswith(b"%PDF"))

    def test_csv_carries_one_row_per_metric(self) -> None:
        group = self._group(2)
        first, second = self._servers_of(group)
        self._patch_engine(
            {
                first.id: _view(first, group.name, SaturationStatus.SATURATED),
                second.id: _view(second, group.name, SaturationStatus.STABLE),
            }
        )
        rows = list(csv.reader(io.StringIO(self._csv_report().generate().decode())))
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1][1], first.hostname)
        self.assertEqual(rows[2][1], second.hostname)

    def test_csv_header_has_fifteen_columns_and_rows_match_it(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        self._patch_engine(
            {
                server.id: _with_metrics(
                    server,
                    group.name,
                    [
                        _metric(COUNTER.LOAD, SaturationStatus.GROWING, days=20),
                        _metric(COUNTER.DISK, SaturationStatus.STABLE),
                    ],
                )
            }
        )
        rows = list(csv.reader(io.StringIO(self._csv_report().generate().decode())))
        self.assertEqual(len(rows[0]), 15)
        self.assertEqual(len(rows), 3)
        self.assertTrue(all(len(row) == 15 for row in rows))
        self.assertEqual([row[0] for row in rows[1:]], [group.name] * 2)

    def test_csv_writes_blanks_for_the_missing_values(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        self._patch_engine(
            {
                server.id: _with_metrics(
                    server, group.name, [_metric(COUNTER.DISK, SaturationStatus.NO_DATA, growth=None)]
                )
            }
        )
        rows = list(csv.reader(io.StringIO(self._csv_report().generate().decode())))
        days, date, slope, r2 = rows[1][8], rows[1][9], rows[1][12], rows[1][13]
        self.assertEqual([days, date, slope, r2], ["", "", "", ""])

    def test_csv_writes_the_saturation_date_as_isoformat(self) -> None:
        group = self._group(1)
        server = group.servers.get()
        view = _with_metrics(server, group.name, [_metric(COUNTER.DISK, SaturationStatus.GROWING, days=7)])
        self._patch_engine({server.id: view})
        rows = list(csv.reader(io.StringIO(self._csv_report().generate().decode())))
        expected = view.metrics[0].saturation_date
        assert expected is not None
        self.assertEqual(rows[1][8], "7")
        self.assertEqual(rows[1][9], expected.isoformat())

    def test_csv_of_an_empty_selection_has_only_the_header(self) -> None:
        self._patch_engine({})
        report = self._csv_report()
        report.server_groups.value = []
        rows = list(csv.reader(io.StringIO(report.generate().decode())))
        self.assertEqual(len(rows), 1)


class ServerSaturationRegistrationTest(UDSTestCase):
    """The reports are only reachable from the admin if the factory knows them."""

    def test_both_variants_are_registered(self) -> None:
        for report_cls in (
            server_saturation.ServerSaturationReport,
            server_saturation.ServerSaturationReportCSV,
        ):
            self.assertTrue(reports.factory().has(report_cls.uuid))
            self.assertIs(reports.factory().get_type(report_cls.uuid), report_cls)

    def test_uuids_are_unique_among_every_report(self) -> None:
        uuids = [report_cls.uuid for report_cls in reports.factory().values()]
        self.assertEqual(len(uuids), len(set(uuids)))

    def test_the_csv_variant_is_not_encoded(self) -> None:
        csv_report = server_saturation.ServerSaturationReportCSV
        self.assertEqual(csv_report.mime_type, "text/csv")
        self.assertFalse(csv_report.encoded)
        self.assertNotEqual(csv_report.uuid, server_saturation.ServerSaturationReport.uuid)


class ServerSaturationReportEndToEndTest(UDSTestCase):
    """Runs the report against the real engine over seeded counters."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        timezone.activate(datetime.timezone.utc)
        Cache.delete(consts.forecasts.PROFILE_CACHE_OWNER)

    @typing.override
    def tearDown(self) -> None:
        Cache.delete(consts.forecasts.PROFILE_CACHE_OWNER)
        super().tearDown()

    def _seed_disk(
        self, server: models.Server, value_for_day: collections.abc.Callable[[int], float]
    ) -> None:
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

    def _report(self) -> server_saturation.ServerSaturationReport:
        report = server_saturation.ServerSaturationReport()
        report.server_groups.value = ["0-0-0-0"]
        return report

    def test_growing_server_reaches_the_attention_list(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=2)
        hot, cold = sorted(group.servers.all(), key=lambda s: s.id)
        self._seed_disk(hot, lambda d: 60.0 + 0.5 * d)
        self._seed_disk(cold, lambda d: 60.0)

        data = self._report().get_data()
        attention = data["attention"]
        self.assertEqual([row["server"] for row in attention], [hot.hostname])
        self.assertEqual(attention[0]["status"], SaturationStatus.GROWING)
        self.assertIsNotNone(attention[0]["saturating_in_days"])
        self.assertIsNotNone(attention[0]["saturation_date"])

        by_server = {row["server"]: row for row in data["groups"][0]["servers"]}
        self.assertEqual(by_server[cold.hostname]["status"], SaturationStatus.STABLE)
        self.assertTrue(by_server[cold.hostname]["has_data"])

    def test_saturated_server_sorts_before_the_growing_one(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=2)
        full, growing = sorted(group.servers.all(), key=lambda s: s.id)
        self._seed_disk(full, lambda d: 96.0)  # over the default disk threshold (95)
        self._seed_disk(growing, lambda d: 60.0 + 0.5 * d)

        servers = self._report().get_data()["groups"][0]["servers"]
        self.assertEqual([row["server"] for row in servers], [full.hostname, growing.hostname])
        self.assertEqual(servers[0]["status"], SaturationStatus.SATURATED)

    def test_server_without_counters_reports_no_data(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=1)
        server = group.servers.get()

        row = self._report().get_data()["groups"][0]["servers"][0]
        self.assertEqual(row["server"], server.hostname)
        self.assertFalse(row["has_data"])
        self.assertEqual(row["status"], SaturationStatus.NO_DATA)
        self.assertIsNone(row["saturating_in_days"])

    def test_history_never_exceeds_the_training_window(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=1)
        server = group.servers.get()
        self._seed_disk(server, lambda d: 60.0)

        row = self._report().get_data()["groups"][0]["servers"][0]
        self.assertLessEqual(row["weeks_of_history"], consts.forecasts.TRAINING_WEEKS)

    def test_csv_agrees_with_the_assembled_data(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=2)
        for server in group.servers.all():
            self._seed_disk(server, lambda d: 60.0 + 0.5 * d)

        data = self._report().get_data()
        expected_rows = sum(len(row["metrics"]) for row in data["groups"][0]["servers"])

        report = server_saturation.ServerSaturationReportCSV()
        report.server_groups.value = ["0-0-0-0"]
        rows = list(csv.reader(io.StringIO(report.generate().decode())))
        self.assertEqual(len(rows) - 1, expected_rows)
        self.assertEqual({row[0] for row in rows[1:]}, {group.name})

    def test_generate_returns_a_pdf_over_real_data(self) -> None:
        group = servers_fixtures.create_server_group(num_servers=1)
        self._seed_disk(group.servers.get(), lambda d: 60.0 + 0.5 * d)
        self.assertTrue(self._report().generate().startswith(b"%PDF"))

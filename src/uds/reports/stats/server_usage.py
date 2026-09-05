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
import datetime
import io
import logging
import typing

import django.template.defaultfilters as filters

from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import types
from uds.core.reports import graphs
from uds.models import StatsCountersAccum

from .servers_base import ServersStatsReport
from .servers_base import accumulated
from .servers_base import server_label
from .servers_base import servers_of

logger: logging.Logger = logging.getLogger(__name__)

WIDTH: typing.Final[float] = 19.2
HEIGHT: typing.Final[float] = 10.8
DPI: typing.Final[int] = 100
SIZE: typing.Final[tuple[float, float, int]] = (WIDTH, HEIGHT, DPI)

# Above this many days, hourly buckets make the charts unreadable
HOURLY_MAX_DAYS: typing.Final[int] = 3

# More stacked series than this and neither the colours nor the legend are readable
CHARTED_SERVERS: typing.Final[int] = 12


class ServerUsageReport(ServersStatsReport):
    filename = "server_usage.pdf"
    name = _("Server groups usage by date")
    description = _("Users and connections served by each server of a group over a period")
    uuid = "af4a1975-987d-4f61-a7bf-ee4cec47a883"

    server_groups = ServersStatsReport.server_groups
    start_date = ServersStatsReport.start_date
    end_date = ServersStatsReport.end_date

    def get_data(
        self,
    ) -> tuple[list[int], list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
        start, end = self.date_range()
        interval = (
            StatsCountersAccum.IntervalType.HOUR
            if (end - start).days <= HOURLY_MAX_DAYS
            else StatsCountersAccum.IntervalType.DAY
        )
        series: list[dict[str, typing.Any]] = []
        summary: list[dict[str, typing.Any]] = []
        stamps: list[int] = []

        for group in self.selected_groups():
            for server in servers_of(group):
                users = accumulated(server.id, types.stats.CounterType.USERS, interval, start, end)
                conns = accumulated(server.id, types.stats.CounterType.CONNECTIONS, interval, start, end)
                if not stamps:
                    stamps = [s.stamp for s in users]

                label = f"{group.name} / {server_label(server)}"
                series.append(
                    {
                        "label": label,
                        "users": [s.max for s in users],
                        "connections": [s.max for s in conns],
                    }
                )
                summary.append(
                    {
                        "group": group.name,
                        "server": server_label(server),
                        "peak_users": max((s.max for s in users), default=0),
                        "avg_users": f"{_average(users):.2f}",
                        "peak_connections": max((s.max for s in conns), default=0),
                        "avg_connections": f"{_average(conns):.2f}",
                        "samples": sum(s.count for s in users),
                    }
                )

        summary.sort(key=lambda r: (r["group"], -r["peak_users"]))
        return stamps, series, summary

    @typing.override
    def generate(self) -> bytes:
        stamps, series, summary = self.get_data()

        if not stamps or not series:
            return self.template_as_pdf(
                "uds/reports/stats/server-usage.html",
                dct={
                    "data": summary,
                    "beginning": self.start_date.as_date(),
                    "ending": self.end_date.as_date(),
                    "has_charts": False,
                },
                header=gettext("Server groups usage by date"),
                water=gettext("Server groups usage"),
            )

        charted = sorted(series, key=lambda s: max(s["users"], default=0), reverse=True)[:CHARTED_SERVERS]

        x_label_format = "SHORT_DATE_FORMAT" if len(stamps) > 48 else "SHORT_DATETIME_FORMAT"

        # Only ~12 labels fit on the X axis without overlapping
        label_step = max(1, len(stamps) // 12)

        def _tick(val: int) -> str:
            index = int(val)
            if index < 0 or index >= len(stamps) or index % label_step:
                return ""
            return filters.date(timezone.make_aware(datetime.datetime.fromtimestamp(stamps[index])), x_label_format)

        graph_users = io.BytesIO()
        graphs.bar_chart(
            SIZE,
            {
                "title": gettext("Peak users by server"),
                "x": stamps,
                "xtickFnc": _tick,
                "xlabel": gettext("Date"),
                "y": [{"label": s["label"], "data": s["users"]} for s in charted],
                "ylabel": gettext("Users"),
            },
            graph_users,
        )

        graph_conns = io.BytesIO()
        graphs.bar_chart(
            SIZE,
            {
                "title": gettext("Peak connections by server"),
                "x": stamps,
                "xtickFnc": _tick,
                "xlabel": gettext("Date"),
                "y": [{"label": s["label"], "data": s["connections"]} for s in charted],
                "ylabel": gettext("Connections"),
            },
            graph_conns,
        )

        return self.template_as_pdf(
            "uds/reports/stats/server-usage.html",
            dct={
                "data": summary,
                "beginning": self.start_date.as_date(),
                "ending": self.end_date.as_date(),
                "has_charts": True,
                "charted_servers": len(charted),
                "total_servers": len(series),
            },
            header=gettext("Server groups usage by date"),
            water=gettext("Server groups usage"),
            images={"graph1": graph_users.getvalue(), "graph2": graph_conns.getvalue()},
        )


class ServerUsageReportCSV(ServerUsageReport):
    filename = "server_usage.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "5869e727-7c81-4ebd-8869-17f7af3cd6ee"

    server_groups = ServerUsageReport.server_groups
    start_date = ServerUsageReport.start_date
    end_date = ServerUsageReport.end_date

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Group"),
                gettext("Server"),
                gettext("Peak users"),
                gettext("Average users"),
                gettext("Peak connections"),
                gettext("Average connections"),
                gettext("Samples"),
            ]
        )
        for v in self.get_data()[2]:
            writer.writerow(
                [
                    v["group"],
                    v["server"],
                    v["peak_users"],
                    v["avg_users"],
                    v["peak_connections"],
                    v["avg_connections"],
                    v["samples"],
                ]
            )
        return output.getvalue().encode()


def _average(stats: list[types.stats.AccumStat]) -> float:
    total = sum(s.sum for s in stats)
    count = sum(s.count for s in stats)
    return total / count if count else 0.0

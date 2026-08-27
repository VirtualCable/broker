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

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import types
from uds.core.reports import graphs
from uds.core.ui import gui
from uds.models import StatsCountersAccum

from .servers_base import ServersStatsReport
from .servers_base import accumulated
from .servers_base import server_label
from .servers_base import servers_of

logger = logging.getLogger(__name__)

WIDTH, HEIGHT, DPI = 19.2, 10.8, 100
SIZE = (WIDTH, HEIGHT, DPI)

# More bars than this and the server names on the X axis overlap
CHARTED_SERVERS: typing.Final[int] = 12


class ServerLoadReport(ServersStatsReport):
    filename = "server_load.pdf"
    name = _("Server resource load")
    description = _("CPU, memory and disk usage of servers, and time spent over a threshold")
    uuid = "8ea8ebae-a4de-4d2f-8040-1cf53f8b5424"

    server_groups = ServersStatsReport.server_groups
    start_date = ServersStatsReport.start_date
    end_date = ServersStatsReport.end_date

    threshold = gui.NumericField(
        order=4,
        label=_("Overload threshold (%)"),
        length=3,
        min_value=1,
        max_value=100,
        default=80,
        tooltip=_("An hour counts as overloaded when CPU or memory peak reaches this percentage"),
        required=True,
    )

    def get_data(self) -> list[dict[str, typing.Any]]:
        start, end = self.date_range()
        threshold = self.threshold.as_int()

        rows: list[dict[str, typing.Any]] = []
        for group in self.selected_groups():
            for server in servers_of(group):
                cpu = _hourly(server.id, types.stats.CounterType.CPU, start, end)
                mem = _hourly(server.id, types.stats.CounterType.MEMORY, start, end)
                disk = _hourly(server.id, types.stats.CounterType.DISK, start, end)

                reported = [i for i in range(len(cpu)) if cpu[i].count or mem[i].count]
                overloaded = sum(1 for i in reported if cpu[i].max >= threshold or mem[i].max >= threshold)

                rows.append(
                    {
                        "group": group.name,
                        "server": server_label(server),
                        "cpu_avg": _average(cpu),
                        "cpu_peak": max((s.max for s in cpu), default=0),
                        "mem_avg": _average(mem),
                        "mem_peak": max((s.max for s in mem), default=0),
                        "disk_avg": _average(disk),
                        "disk_peak": max((s.max for s in disk), default=0),
                        "hours_reported": len(reported),
                        "hours_overloaded": overloaded,
                        "pct_overloaded": (overloaded * 100.0 / len(reported)) if reported else 0.0,
                    }
                )

        rows.sort(key=lambda r: r["pct_overloaded"], reverse=True)
        return rows

    @typing.override
    def generate(self) -> bytes:
        rows = self.get_data()

        images: dict[str, bytes] | None = None
        charted = 0
        if rows:
            graph_cpu = io.BytesIO()
            charted = _top_servers_bar_chart(rows, "cpu_avg", gettext("Average CPU usage"), gettext("CPU %"), graph_cpu)

            graph_mem = io.BytesIO()
            _top_servers_bar_chart(rows, "mem_avg", gettext("Average memory usage"), gettext("Memory %"), graph_mem)

            images = {"graph1": graph_cpu.getvalue(), "graph2": graph_mem.getvalue()}

        return self.template_as_pdf(
            "uds/reports/stats/server-load.html",
            dct={
                "data": [_as_display(r) for r in rows],
                "beginning": self.start_date.as_date(),
                "ending": self.end_date.as_date(),
                "threshold": self.threshold.as_int(),
                "has_charts": images is not None,
                "charted_servers": charted,
                "total_servers": len(rows),
            },
            header=gettext("Server resource load"),
            water=gettext("UDS Report of server resource load"),
            images=images,
        )


class ServerLoadReportCSV(ServerLoadReport):
    filename = "server_load.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "1076292f-197d-4ac2-b02f-45e0e6c7a69d"

    server_groups = ServerLoadReport.server_groups
    start_date = ServerLoadReport.start_date
    end_date = ServerLoadReport.end_date
    threshold = ServerLoadReport.threshold

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Group"),
                gettext("Server"),
                gettext("CPU average %"),
                gettext("CPU peak %"),
                gettext("Memory average %"),
                gettext("Memory peak %"),
                gettext("Disk average %"),
                gettext("Disk peak %"),
                gettext("Hours reported"),
                gettext("Hours overloaded"),
                gettext("Overloaded %"),
            ]
        )
        for r in self.get_data():
            d = _as_display(r)
            writer.writerow(
                [
                    d["group"],
                    d["server"],
                    d["cpu_avg"],
                    d["cpu_peak"],
                    d["mem_avg"],
                    d["mem_peak"],
                    d["disk_avg"],
                    d["disk_peak"],
                    d["hours_reported"],
                    d["hours_overloaded"],
                    d["pct_overloaded"],
                ]
            )
        return output.getvalue().encode()


def _hourly(
    server_id: int,
    counter_type: types.stats.CounterType,
    since: datetime.datetime,
    to: datetime.datetime,
) -> list[types.stats.AccumStat]:
    return accumulated(server_id, counter_type, StatsCountersAccum.IntervalType.HOUR, since, to)


def _average(stats: list[types.stats.AccumStat]) -> float:
    total = sum(s.sum for s in stats)
    count = sum(s.count for s in stats)
    return total / count if count else 0.0


def _as_display(row: dict[str, typing.Any]) -> dict[str, typing.Any]:
    return row | {
        "cpu_avg": f"{row['cpu_avg']:.1f}",
        "mem_avg": f"{row['mem_avg']:.1f}",
        "disk_avg": f"{row['disk_avg']:.1f}",
        "pct_overloaded": f"{row['pct_overloaded']:.1f}",
    }


def _top_servers_bar_chart(
    rows: list[dict[str, typing.Any]], key: str, title: str, ylabel: str, output: io.BytesIO
) -> int:
    top = sorted(rows, key=lambda r: r[key], reverse=True)[:CHARTED_SERVERS]
    labels = [f"{r['group']} / {r['server']}" for r in top]
    graphs.bar_chart(
        SIZE,
        {
            "title": title,
            "x": labels,
            "xtickFnc": lambda v: labels[int(v)] if 0 <= int(v) < len(labels) else "",
            "xlabel": gettext("Server"),
            "y": [{"label": ylabel, "data": [r[key] for r in top]}],
            "ylabel": ylabel,
        },
        output,
    )
    return len(top)

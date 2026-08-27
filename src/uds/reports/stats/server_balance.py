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
import io
import logging
import statistics
import typing

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import types
from uds.core.reports import graphs
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


class ServerBalanceReport(ServersStatsReport):
    filename = "server_balance.pdf"
    name = _("Server group balancing")
    description = _("How evenly users are spread across the servers of each group")
    uuid = "3dfdb907-44ce-4214-b42f-fcd9326e82fb"

    server_groups = ServersStatsReport.server_groups
    start_date = ServersStatsReport.start_date
    end_date = ServersStatsReport.end_date

    def get_data(self) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
        start, end = self.date_range()

        servers_rows: list[dict[str, typing.Any]] = []
        groups_rows: list[dict[str, typing.Any]] = []

        for group in self.selected_groups():
            servers = servers_of(group)
            if not servers:
                continue

            measures: list[tuple[str, int, int]] = []
            for server in servers:
                users = accumulated(
                    server.id,
                    types.stats.CounterType.USERS,
                    StatsCountersAccum.IntervalType.HOUR,
                    start,
                    end,
                )
                measures.append(
                    (
                        server_label(server),
                        sum(s.sum for s in users),
                        max((s.max for s in users), default=0),
                    )
                )

            group_total = sum(m[1] for m in measures)
            even_share = 100.0 / len(measures)
            shares: list[float] = []

            for name, user_hours, peak in measures:
                share = (user_hours * 100.0 / group_total) if group_total else 0.0
                shares.append(share)
                servers_rows.append(
                    {
                        "group": group.name,
                        "server": name,
                        "user_hours": user_hours,
                        "peak_users": peak,
                        "share": f"{share:.1f}",
                        "share_value": share,
                        "deviation": f"{share - even_share:+.1f}",
                    }
                )

            groups_rows.append(
                {
                    "group": group.name,
                    "servers": len(measures),
                    "even_share": f"{even_share:.1f}",
                    "max_share": f"{max(shares):.1f}",
                    "min_share": f"{min(shares):.1f}",
                    "spread": f"{max(shares) - min(shares):.1f}",
                    "stdev": f"{(statistics.pstdev(shares) if len(shares) > 1 else 0.0):.1f}",
                    "idle_servers": sum(1 for m in measures if m[1] == 0),
                }
            )

        servers_rows.sort(key=lambda r: (r["group"], -r["share_value"]))
        return groups_rows, servers_rows

    @typing.override
    def generate(self) -> bytes:
        groups_rows, servers_rows = self.get_data()

        images: dict[str, bytes] | None = None
        charted = sorted(servers_rows, key=lambda r: r["share_value"], reverse=True)[:CHARTED_SERVERS]
        if servers_rows:
            labels = [f"{r['group']} / {r['server']}" for r in charted]
            graph = io.BytesIO()
            graphs.bar_chart(
                SIZE,
                {
                    "title": gettext("Share of served users by server"),
                    "x": labels,
                    "xtickFnc": lambda v: labels[int(v)] if 0 <= int(v) < len(labels) else "",
                    "xlabel": gettext("Server"),
                    "y": [
                        {
                            "label": gettext("Share %"),
                            "data": [r["share_value"] for r in charted],
                        }
                    ],
                    "ylabel": gettext("Share %"),
                },
                graph,
            )
            images = {"graph1": graph.getvalue()}

        return self.template_as_pdf(
            "uds/reports/stats/server-balance.html",
            dct={
                "groups": groups_rows,
                "data": servers_rows,
                "beginning": self.start_date.as_date(),
                "ending": self.end_date.as_date(),
                "has_charts": images is not None,
                "charted_servers": len(charted),
                "total_servers": len(servers_rows),
            },
            header=gettext("Server group balancing"),
            water=gettext("UDS Report of server group balancing"),
            images=images,
        )


class ServerBalanceReportCSV(ServerBalanceReport):
    filename = "server_balance.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "cd2b1598-4439-44da-a3a9-140744bdd65c"

    server_groups = ServerBalanceReport.server_groups
    start_date = ServerBalanceReport.start_date
    end_date = ServerBalanceReport.end_date

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Group"),
                gettext("Server"),
                gettext("User hours"),
                gettext("Peak users"),
                gettext("Share %"),
                gettext("Deviation from even share"),
            ]
        )
        for r in self.get_data()[1]:
            writer.writerow([r["group"], r["server"], r["user_hours"], r["peak_users"], r["share"], r["deviation"]])
        return output.getvalue().encode()

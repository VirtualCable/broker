# -*- coding: utf-8 -*-
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

from uds.core.managers.stats import StatsManager
from uds.core.ui import gui
from uds.core.util import stats
from uds.models import ServicePool

from .base import StatsReport

logger = logging.getLogger(__name__)


def _to_int(value: str) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


class TunnelUsageReport(StatsReport):
    filename = "tunnel_usage.pdf"
    name = _("Tunnel usage")
    description = _("Tunnel sessions opened/closed, durations and bytes per pool")
    uuid = "ef4be537-f19b-44bc-bb9b-8fa279d2371f"

    pools = StatsReport.pools
    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    @typing.override
    def init_gui(self) -> None:
        vals = [gui.choice_item("0-0-0-0", gettext("ALL POOLS"))] + [
            gui.choice_item(v.uuid, v.name) for v in ServicePool.objects.all().order_by("name") if v.uuid
        ]
        self.pools.set_choices(vals)

    def get_data(self) -> list[dict[str, typing.Any]]:
        start = self.start_date.as_timestamp()
        end = self.end_date.as_timestamp()

        if "0-0-0-0" in self.pools.value:
            qs = ServicePool.objects.all()
        else:
            qs = ServicePool.objects.filter(uuid__in=self.pools.value)

        pool_map: dict[int, str] = dict(qs.values_list("id", "name"))
        if not pool_map:
            return []

        topen = stats.events.types.stats.EventType.TUNNEL_OPEN
        tclose = stats.events.types.stats.EventType.TUNNEL_CLOSE

        rows = (
            StatsManager.manager()
            .enumerate_events(
                stats.events.types.stats.EventOwnerType.SERVICEPOOL,
                (topen, tclose),
                owner_id=list(pool_map.keys()),
                since=start,
                to=end,
            )
            .values("owner_id", "event_type", "fld1", "fld2", "fld3")
        )

        agg: dict[int, dict[str, int]] = {
            pid: {"opens": 0, "closes": 0, "duration": 0, "sent": 0, "received": 0} for pid in pool_map
        }
        for r in rows:
            entry = agg[r["owner_id"]]
            if r["event_type"] == topen:
                entry["opens"] += 1
            else:
                entry["closes"] += 1
                # TUNNEL_CLOSE: fld1=duration, fld2=sent, fld3=received
                entry["duration"] += _to_int(r["fld1"])
                entry["sent"] += _to_int(r["fld2"])
                entry["received"] += _to_int(r["fld3"])

        result: list[dict[str, typing.Any]] = []
        for pid, pool_name in pool_map.items():
            e = agg[pid]
            avg_duration = (e["duration"] // e["closes"]) if e["closes"] else 0
            result.append(
                {
                    "pool": pool_name,
                    "opens": e["opens"],
                    "closes": e["closes"],
                    "duration": str(datetime.timedelta(seconds=e["duration"])),
                    "avg_duration": str(datetime.timedelta(seconds=avg_duration)),
                    "sent": e["sent"],
                    "received": e["received"],
                }
            )

        result.sort(key=lambda r: r["opens"], reverse=True)
        return result

    @typing.override
    def generate(self) -> bytes:
        items = self.get_data()
        return self.template_as_pdf(
            "uds/reports/stats/tunnel-usage.html",
            dct={
                "data": items,
                "beginning": self.start_date.as_date(),
                "ending": self.end_date.as_date(),
            },
            header=gettext("Tunnel usage"),
            water=gettext("UDS Report of tunnel usage"),
        )


class TunnelUsageReportCSV(TunnelUsageReport):
    filename = "tunnel_usage.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "b0e38c72-a135-4f92-8700-b00753598bb2"

    pools = TunnelUsageReport.pools
    start_date = TunnelUsageReport.start_date
    end_date = TunnelUsageReport.end_date

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Pool"),
                gettext("Opens"),
                gettext("Closes"),
                gettext("Total time"),
                gettext("Mean time"),
                gettext("Bytes sent"),
                gettext("Bytes received"),
            ]
        )
        for v in self.get_data():
            writer.writerow(
                [
                    v["pool"],
                    v["opens"],
                    v["closes"],
                    v["duration"],
                    v["avg_duration"],
                    v["sent"],
                    v["received"],
                ]
            )
        return output.getvalue().encode()

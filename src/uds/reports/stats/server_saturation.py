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
Server saturation forecast report, built on top of the saturation engine.

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import csv
import datetime
import io
import logging
import typing

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import consts
from uds.core import types
from uds.core.util import config
from uds.core.util.stats import saturation

from .servers_base import ServersStatsReport
from .servers_base import servers_of

logger = logging.getLogger(__name__)

# A shorter history makes the yearly trend guesswork, which is why servers show up
# as unreliable even when they do report stats
RELIABLE_WEEKS: typing.Final[float] = 26.0

METRIC_NAMES: typing.Final[dict[types.stats.CounterType, str]] = {
    types.stats.CounterType.LOAD: _("Load"),
    types.stats.CounterType.DISK: _("Disk"),
    types.stats.CounterType.USERS: _("Users"),
    types.stats.CounterType.CONNECTIONS: _("Connections"),
}

STATUS_NAMES: typing.Final[dict[saturation.SaturationStatus, str]] = {
    saturation.SaturationStatus.SATURATED: _("Saturated"),
    saturation.SaturationStatus.GROWING: _("Growing, will saturate"),
    saturation.SaturationStatus.GROWING_BEYOND_HORIZON: _("Growing, saturates beyond horizon"),
    saturation.SaturationStatus.UNRELIABLE: _("Not enough history"),
    saturation.SaturationStatus.STABLE: _("Stable"),
    saturation.SaturationStatus.SHRINKING: _("Shrinking"),
    saturation.SaturationStatus.NO_DATA: _("No data"),
}

# Statuses that put a server on the attention list of the report
ATTENTION_STATUSES: typing.Final[tuple[saturation.SaturationStatus, ...]] = (
    saturation.SaturationStatus.SATURATED,
    saturation.SaturationStatus.GROWING,
)


class ServerSaturationReport(ServersStatsReport):
    filename = "server_saturation.pdf"
    name = _("Saturation forecast per server")
    description = _("Forecast of when every server saturates, and which of its metrics saturates first")
    uuid = "71aa354c-46f5-4589-9284-44f03afeedd8"

    # The saturation engine trains over its own window, so a date range would be ignored
    server_groups = ServersStatsReport.server_groups

    def get_data(self) -> dict[str, typing.Any]:
        groups_data: list[dict[str, typing.Any]] = []
        attention: list[dict[str, typing.Any]] = []

        for group in self.selected_groups():
            servers = [
                _as_server_row(saturation.server_saturation_cached(server))
                for server in servers_of(group)
            ]
            servers.sort(key=_worst_first)
            groups_data.append({"group": group.name, "servers": servers})
            attention.extend(row for row in servers if row["status"] in ATTENTION_STATUSES)

        attention.sort(key=_worst_first)

        return {
            "groups": groups_data,
            "attention": attention,
            "training_weeks": consts.forecasts.TRAINING_WEEKS,
            "reliable_weeks": RELIABLE_WEEKS,
            "stats_duration": config.GlobalConfig.STATS_DURATION.as_int(),
        }

    @typing.override
    def generate(self) -> bytes:
        return self.template_as_pdf(
            "uds/reports/stats/server-saturation.html",
            dct=self.get_data(),
            header=gettext("Saturation forecast per server"),
            water=gettext("UDS Report of server saturation forecast"),
        )


def _worst_first(row: dict[str, typing.Any]) -> tuple[int, float]:
    """Most severe status first, and within the same status the soonest saturation."""
    days = row["saturating_in_days"]
    return saturation.status_severity(row["status"]), days if days is not None else float("inf")


def _as_server_row(view: saturation.ServerSaturation) -> dict[str, typing.Any]:
    weeks = max((m.weeks_of_history for m in view.metrics), default=0.0)
    return {
        "server": view.label,
        "group": view.group_name,
        "status": view.status,
        "status_name": gettext(str(STATUS_NAMES.get(view.status, str(view.status)))),
        "has_data": view.has_data,
        "saturating_in_days": view.saturating_in_days(),
        "saturation_date": _earliest_saturation_date(view),
        "weeks_of_history": round(weeks, 1),
        "short_history": weeks < RELIABLE_WEEKS,
        "metrics": [_as_metric_row(metric) for metric in view.metrics],
    }


def _earliest_saturation_date(view: saturation.ServerSaturation) -> datetime.datetime | None:
    dates = [m.saturation_date for m in view.metrics if m.saturation_date is not None]
    return min(dates) if dates else None


def _as_metric_row(metric: saturation.MetricSaturation) -> dict[str, typing.Any]:
    return {
        "metric": gettext(str(METRIC_NAMES.get(metric.counter, metric.counter.name.lower()))),
        "status": metric.status,
        "status_name": gettext(str(STATUS_NAMES.get(metric.status, str(metric.status)))),
        "current_p90": round(metric.current_p90, 2),
        "current_max": round(metric.current_max, 2),
        "threshold": round(metric.threshold, 2),
        "days_to_saturation": metric.days_to_saturation,
        "saturation_date": metric.saturation_date,
        "confidence": round(metric.confidence, 2),
        "weeks_of_history": round(metric.weeks_of_history, 1),
        "slope_per_day": round(metric.growth.slope_per_day, 4) if metric.growth else None,
        "r2": round(metric.growth.r2, 2) if metric.growth else None,
        "method": metric.growth.method if metric.growth else "",
    }


class ServerSaturationReportCSV(ServerSaturationReport):
    filename = "server_saturation.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "f033ea4c-613b-4430-8892-05bed5ce5dce"

    server_groups = ServerSaturationReport.server_groups

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Group"),
                gettext("Server"),
                gettext("Server status"),
                gettext("Metric"),
                gettext("Metric status"),
                gettext("Current p90"),
                gettext("Current max"),
                gettext("Threshold"),
                gettext("Days to saturation"),
                gettext("Saturation date"),
                gettext("Confidence"),
                gettext("Weeks of history"),
                gettext("Slope per day"),
                gettext("R2"),
                gettext("Method"),
            ]
        )
        for group in self.get_data()["groups"]:
            for server in group["servers"]:
                for metric in server["metrics"]:
                    writer.writerow(
                        [
                            group["group"],
                            server["server"],
                            server["status_name"],
                            metric["metric"],
                            metric["status_name"],
                            metric["current_p90"],
                            metric["current_max"],
                            metric["threshold"],
                            metric["days_to_saturation"] if metric["days_to_saturation"] is not None else "",
                            metric["saturation_date"].isoformat() if metric["saturation_date"] else "",
                            metric["confidence"],
                            metric["weeks_of_history"],
                            metric["slope_per_day"] if metric["slope_per_day"] is not None else "",
                            metric["r2"] if metric["r2"] is not None else "",
                            metric["method"],
                        ]
                    )
        return output.getvalue().encode()

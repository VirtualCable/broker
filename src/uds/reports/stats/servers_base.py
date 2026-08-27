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
import datetime
import typing

from django.db.models import QuerySet
from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import types
from uds.core.ui import gui
from uds.core.util.stats import counters
from uds.models import Server
from uds.models import ServerGroup
from uds.models import StatsCountersAccum

from .base import StatsReport

ALL_GROUPS: typing.Final[str] = "0-0-0-0"


class ServersStatsReport(StatsReport):
    """Common selection and time range handling for reports over server counters."""

    server_groups = gui.MultiChoiceField(
        order=1,
        label=_("Server groups"),
        tooltip=_("Server groups to include on the report"),
        required=True,
    )

    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    @typing.override
    def init_gui(self) -> None:
        vals = [gui.choice_item(ALL_GROUPS, gettext("ALL SERVER GROUPS"))] + [
            gui.choice_item(g.uuid, f"{g.name} ({g.subtype or g.server_type.as_str()})")
            for g in managed_server_groups().order_by("name")
            if g.uuid
        ]
        self.server_groups.set_choices(vals)

    def selected_groups(self) -> list[ServerGroup]:
        groups = managed_server_groups()
        if ALL_GROUPS not in self.server_groups.value:
            groups = groups.filter(uuid__in=self.server_groups.value)
        return list(groups.order_by("name"))

    def date_range(self) -> tuple[datetime.datetime, datetime.datetime]:
        start = timezone.make_aware(datetime.datetime.combine(self.start_date.as_date(), datetime.time.min))
        end = timezone.make_aware(datetime.datetime.combine(self.end_date.as_date(), datetime.time.max))
        return start, end


def managed_server_groups() -> QuerySet[ServerGroup]:
    # UNMANAGED servers never report stats, so their groups would only add empty rows
    return ServerGroup.objects.exclude(type=types.servers.ServerType.UNMANAGED.value)


def servers_of(group: ServerGroup) -> list[Server]:
    return list(group.servers.all().order_by("hostname", "ip"))


def server_label(server: Server) -> str:
    return server.hostname or server.ip


def accumulated(
    server_id: int,
    counter_type: types.stats.CounterType,
    interval: StatsCountersAccum.IntervalType,
    since: datetime.datetime,
    to: datetime.datetime,
) -> list[types.stats.AccumStat]:
    """Accumulated counters of a server, over a fixed bucket grid.

    The stats manager neither stops at `to` nor returns the same number of buckets for
    every server, and asking it for a bucket count makes it re-anchor `since`. Buckets are
    matched back by their own stamp so the series of every server share one X axis.
    """
    seconds = interval.seconds()
    grid = bucket_grid(interval, since, to)
    by_stamp: dict[int, types.stats.AccumStat] = {}
    for stat in counters.enumerate_accumulated_counters(
        interval_type=interval,
        counter_type=counter_type,
        owner_type=types.stats.CounterOwnerType.SERVER,
        owner_id=server_id,
        since=since,
        to=to,
    ):
        if stat.count:
            by_stamp[stat.stamp - stat.stamp % seconds] = stat

    return [by_stamp.get(stamp, types.stats.AccumStat(stamp, 0, 0, 0, 0)) for stamp in grid]


def bucket_grid(
    interval: StatsCountersAccum.IntervalType,
    since: datetime.datetime,
    to: datetime.datetime,
) -> list[int]:
    seconds = interval.seconds()
    start = int(since.timestamp())
    return list(range(start - start % seconds, int(to.timestamp()) + 1, seconds))

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

from django.db.models import F
from django.db.models import Window
from django.db.models.functions import Lag
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core.managers.stats import StatsManager
from uds.core.ui import gui
from uds.core.util import stats
from uds.models import Authenticator
from uds.models import ServicePool

from .base import StatsReport

logger = logging.getLogger(__name__)


class GroupEntry(typing.TypedDict):
    group: str
    sessions: int
    time: int
    users: set[str]


class UsageByGroupReport(StatsReport):
    filename = "usage_by_group.pdf"
    name = _("Usage by group")
    description = _("Aggregate session time and counts by AD/LDAP group of an authenticator")
    uuid = "51e81239-4fd8-4f0f-9f46-d2111295d978"

    authenticator = gui.ChoiceField(
        order=1,
        label=_("Authenticator"),
        tooltip=_("Authenticator whose groups will be used to aggregate"),
        required=True,
    )

    pools = StatsReport.pools
    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    @typing.override
    def init_gui(self) -> None:
        self.authenticator.set_choices(
            [gui.choice_item(v.uuid, v.name) for v in Authenticator.objects.all().order_by("name")]
        )
        vals = [gui.choice_item("0-0-0-0", gettext("ALL POOLS"))] + [
            gui.choice_item(v.uuid, v.name) for v in ServicePool.objects.all().order_by("name") if v.uuid
        ]
        self.pools.set_choices(vals)

    def get_data(self) -> tuple[list[dict[str, typing.Any]], str, int]:
        try:
            auth = Authenticator.objects.get(uuid=self.authenticator.value)
        except Authenticator.DoesNotExist:
            return [], "", 0

        # username -> group names (a user can belong to many groups).
        user_groups: dict[str, list[str]] = {}
        for username, group_name in auth.users.values_list("name", "groups__name"):
            if not username or group_name is None:
                continue
            user_groups.setdefault(username, []).append(group_name)

        if "0-0-0-0" in self.pools.value:
            pool_ids = list(ServicePool.objects.values_list("id", flat=True))
        else:
            pool_ids = list(ServicePool.objects.filter(uuid__in=self.pools.value).values_list("id", flat=True))
        if not pool_ids:
            return [], auth.name, 0

        start = self.start_date.as_timestamp()
        end = self.end_date.as_timestamp()
        login = stats.events.types.stats.EventType.LOGIN
        logout = stats.events.types.stats.EventType.LOGOUT

        partition = [F("owner_id"), F("fld4")]
        items = (
            StatsManager.manager()
            .enumerate_events(
                stats.events.types.stats.EventOwnerType.SERVICEPOOL,
                (login, logout),
                owner_id=pool_ids,
                since=start,
                to=end,
            )
            .annotate(
                prev_type=Window(Lag("event_type"), partition_by=partition, order_by=[F("stamp")]),
                prev_stamp=Window(Lag("stamp"), partition_by=partition, order_by=[F("stamp")]),
            )
            .values("event_type", "stamp", "fld4", "prev_type", "prev_stamp")
        )

        groups: dict[str, GroupEntry] = {}
        unmatched_users = 0
        unmatched_seconds = 0
        unmatched_sessions = 0
        for i in items:
            if i["event_type"] != logout or i["prev_type"] != login:
                continue
            duration = i["stamp"] - i["prev_stamp"]
            if duration < 0:
                continue
            username = i["fld4"] or ""
            grp_names = user_groups.get(username)
            if not grp_names:
                unmatched_users += 1
                unmatched_seconds += duration
                unmatched_sessions += 1
                continue
            for g in grp_names:
                entry = groups.get(g)
                if entry is None:
                    entry = GroupEntry(group=g, sessions=0, time=0, users=set())
                    groups[g] = entry
                entry["sessions"] += 1
                entry["time"] += duration
                entry["users"].add(username)

        rows: list[dict[str, typing.Any]] = []
        for g in groups.values():
            rows.append(
                {
                    "group": g["group"],
                    "sessions": g["sessions"],
                    "users": len(g["users"]),
                    "time": str(datetime.timedelta(seconds=g["time"])),
                    "time_seconds": g["time"],
                    "avg": str(datetime.timedelta(seconds=g["time"] // g["sessions"])),
                }
            )
        if unmatched_sessions:
            rows.append(
                {
                    "group": gettext("(unmatched users)"),
                    "sessions": unmatched_sessions,
                    "users": unmatched_users,
                    "time": str(datetime.timedelta(seconds=unmatched_seconds)),
                    "time_seconds": unmatched_seconds,
                    "avg": str(datetime.timedelta(seconds=unmatched_seconds // unmatched_sessions)),
                }
            )

        rows.sort(key=lambda r: r["time_seconds"], reverse=True)
        return rows, auth.name, sum(r["time_seconds"] for r in rows)

    @typing.override
    def generate(self) -> bytes:
        rows, auth_name, _total = self.get_data()
        return self.template_as_pdf(
            "uds/reports/stats/usage-by-group.html",
            dct={
                "data": rows,
                "auth": auth_name,
                "beginning": self.start_date.as_date(),
                "ending": self.end_date.as_date(),
            },
            header=gettext("Usage by group of {}").format(auth_name),
            water=gettext("UDS Report of usage by group"),
        )


class UsageByGroupReportCSV(UsageByGroupReport):
    filename = "usage_by_group.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "5022e2f4-866a-4a70-86bc-2ce2d9b6f6bb"

    authenticator = UsageByGroupReport.authenticator
    pools = UsageByGroupReport.pools
    start_date = UsageByGroupReport.start_date
    end_date = UsageByGroupReport.end_date

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Group"),
                gettext("Sessions"),
                gettext("Distinct users"),
                gettext("Total time"),
                gettext("Average per session"),
            ]
        )
        rows, _auth, _total = self.get_data()
        for v in rows:
            writer.writerow([v["group"], v["sessions"], v["users"], v["time"], v["avg"]])
        return output.getvalue().encode()

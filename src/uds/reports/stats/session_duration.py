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
import io
import logging
import typing

from django.db.models import F, Window
from django.db.models.functions import Lag
from django.utils.translation import gettext, gettext_lazy as _

from uds.core.managers.stats import StatsManager
from uds.core.ui import gui
from uds.core.util import stats
from uds.models import ServicePool

from .base import StatsReport

logger = logging.getLogger(__name__)


# (label, lower_bound_seconds, upper_bound_seconds_exclusive)
BUCKETS: typing.Final[tuple[tuple[str, int, int], ...]] = (
    ("< 5 min", 0, 5 * 60),
    ("5-30 min", 5 * 60, 30 * 60),
    ("30 min - 2 h", 30 * 60, 2 * 3600),
    ("2 - 8 h", 2 * 3600, 8 * 3600),
    ("> 8 h", 8 * 3600, 24 * 3600 * 365),
)


class SessionDurationReport(StatsReport):
    filename = "session_duration.pdf"
    name = _("Session duration histogram")
    description = _("Distribution of session durations across selected pools")
    uuid = "6bd74f52-7ce3-4877-88cd-153fe3781801"

    pools = StatsReport.pools
    start_date = StatsReport.start_date
    end_date = StatsReport.end_date

    @typing.override
    def init_gui(self) -> None:
        vals = [gui.choice_item("0-0-0-0", gettext("ALL POOLS"))] + [
            gui.choice_item(v.uuid, v.name) for v in ServicePool.objects.all().order_by("name") if v.uuid
        ]
        self.pools.set_choices(vals)

    def get_data(self) -> tuple[list[dict[str, typing.Any]], int, int]:
        start = self.start_date.as_timestamp()
        end = self.end_date.as_timestamp()

        if "0-0-0-0" in self.pools.value:
            pool_ids = list(ServicePool.objects.values_list("id", flat=True))
        else:
            pool_ids = list(ServicePool.objects.filter(uuid__in=self.pools.value).values_list("id", flat=True))
        if not pool_ids:
            return [], 0, 0

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
            .values("event_type", "stamp", "prev_type", "prev_stamp")
        )

        counts = [0] * len(BUCKETS)
        total_seconds = 0
        total_sessions = 0
        for i in items:
            if i["event_type"] != logout or i["prev_type"] != login:
                continue
            duration = i["stamp"] - i["prev_stamp"]
            if duration < 0:
                continue
            total_seconds += duration
            total_sessions += 1
            for idx, (_label, lo, hi) in enumerate(BUCKETS):
                if lo <= duration < hi:
                    counts[idx] += 1
                    break

        rows: list[dict[str, typing.Any]] = []
        for (label, _lo, _hi), c in zip(BUCKETS, counts):
            pct = (c * 100.0 / total_sessions) if total_sessions else 0.0
            rows.append({"bucket": label, "count": c, "pct": "{:.1f}".format(pct)})

        return rows, total_sessions, total_seconds

    @typing.override
    def generate(self) -> bytes:
        rows, total_sessions, total_seconds = self.get_data()
        avg_seconds = (total_seconds // total_sessions) if total_sessions else 0
        return self.template_as_pdf(
            "uds/reports/stats/session-duration.html",
            dct={
                "data": rows,
                "total_sessions": total_sessions,
                "total_seconds": total_seconds,
                "avg_seconds": avg_seconds,
                "beginning": self.start_date.as_date(),
                "ending": self.end_date.as_date(),
            },
            header=gettext("Session duration histogram"),
            water=gettext("UDS Report of session durations"),
        )


class SessionDurationReportCSV(SessionDurationReport):
    filename = "session_duration.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "318ad9e3-e5ed-404d-bd57-d3db2fc63556"

    pools = SessionDurationReport.pools
    start_date = SessionDurationReport.start_date
    end_date = SessionDurationReport.end_date

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([gettext("Bucket"), gettext("Count"), gettext("Percent")])
        rows, _ts, _tot = self.get_data()
        for v in rows:
            writer.writerow([v["bucket"], v["count"], v["pct"]])
        return output.getvalue().encode()

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
import re
import typing

from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import types
from uds.core.ui import gui
from uds.core.util import dateutils
from uds.models import Log

from .base import ListReport

logger = logging.getLogger(__name__)


class EntryUserDict(typing.TypedDict):
    user: str
    requests: int
    errors: int
    last_seen: datetime.datetime
    paths: dict[str, int]


_LOG_RX = re.compile(r"(?P<ip>[^\[ ]*) *(?P<user>.*?): \[(?P<method>[^/]*)/(?P<response_code>[^\]]*)\] (?P<request>.*)")


class AdminActivityReport(ListReport):
    filename = "admin_activity.pdf"
    name = _("Administrators activity")
    description = _("Aggregated REST audit activity per administrator user")
    uuid = "34507a71-7d45-4f80-a2ca-b9acafe5aea6"

    start_date = gui.DateField(
        order=2,
        label=_("Starting date"),
        tooltip=_("starting date for report"),
        default=dateutils.start_of_month,
        required=True,
    )

    end_date = gui.DateField(
        order=3,
        label=_("Finish date"),
        tooltip=_("finish date for report"),
        default=dateutils.tomorrow,
        required=True,
    )

    top_paths = gui.NumericField(
        order=4,
        label=_("Top endpoints per user"),
        length=3,
        min_value=1,
        max_value=50,
        default=5,
        tooltip=_("Number of most-used endpoints to show per user"),
        required=True,
    )

    @staticmethod
    def _path_only(request: str) -> str:
        # 'request' from the REST audit log: "<METHOD> <full path with query>".
        parts = request.split(" ", 1)
        path = parts[1] if len(parts) > 1 else parts[0]
        return path.split("?", 1)[0]

    def get_data(self) -> list[dict[str, typing.Any]]:
        start = timezone.make_aware(datetime.datetime.combine(self.start_date.as_date(), datetime.time.min))
        end = timezone.make_aware(datetime.datetime.combine(self.end_date.as_date(), datetime.time.max))

        users: dict[str, EntryUserDict] = {}
        for entry in Log.objects.filter(
            created__gte=start,
            created__lte=end,
            source=types.log.LogSource.REST,
            owner_type=types.log.LogObjectType.SYSLOG,
        ).values("created", "data"):
            m = _LOG_RX.match(entry["data"])
            if not m:
                continue
            user = m.group("user") or gettext("Unknown")
            try:
                code = int(m.group("response_code"))
            except (TypeError, ValueError):
                code = 500
            created = entry["created"]
            entry_user = users.get(user)
            if entry_user is None:
                entry_user = EntryUserDict(
                    user=user,
                    requests=0,
                    errors=0,
                    last_seen=created,
                    paths={},
                )
                users[user] = entry_user

            entry_user["requests"] += 1
            if code >= 400:
                entry_user["errors"] += 1
            if created > entry_user["last_seen"]:
                entry_user["last_seen"] = created
            path = self._path_only(m.group("request"))
            entry_user["paths"][path] = entry_user["paths"].get(path, 0) + 1

        top = self.top_paths.as_int()
        rows: list[dict[str, typing.Any]] = []
        for u in users.values():
            top_paths = sorted(u["paths"].items(), key=lambda x: x[1], reverse=True)[:top]
            err_pct = (u["errors"] * 100.0 / u["requests"]) if u["requests"] else 0.0
            rows.append(
                {
                    "user": u["user"],
                    "requests": u["requests"],
                    "errors": u["errors"],
                    "error_pct": "{:.1f}".format(err_pct),
                    "last_seen": u["last_seen"],
                    "top_paths": "; ".join(f"{p} ({c})" for p, c in top_paths),
                }
            )

        rows.sort(key=lambda r: r["requests"], reverse=True)
        return rows

    @typing.override
    def generate(self) -> bytes:
        rows = self.get_data()
        return self.template_as_pdf(
            "uds/reports/lists/admin-activity.html",
            dct={
                "data": rows,
                "beginning": self.start_date.as_date(),
                "ending": self.end_date.as_date(),
            },
            header=gettext("Administrators activity"),
            water=gettext("UDS Report of admin activity"),
        )


class AdminActivityReportCSV(AdminActivityReport):
    filename = "admin_activity.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "d4e24000-a281-4dba-9eb1-02e5e171897a"

    start_date = AdminActivityReport.start_date
    end_date = AdminActivityReport.end_date
    top_paths = AdminActivityReport.top_paths

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("User"),
                gettext("Requests"),
                gettext("Errors"),
                gettext("Error %"),
                gettext("Last seen"),
                gettext("Top endpoints"),
            ]
        )
        for v in self.get_data():
            writer.writerow(
                [
                    v["user"],
                    v["requests"],
                    v["errors"],
                    v["error_pct"],
                    v["last_seen"],
                    v["top_paths"],
                ]
            )
        return output.getvalue().encode()

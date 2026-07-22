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
from uds.models import Authenticator
from uds.models import Log

from .base import ListReport

logger = logging.getLogger(__name__)


# Login log pattern from uds.core.auths.auth.log_login:
#   "user {username} has {log_string} from {ip} where os is {os}"
_LOGIN_RX = re.compile(r"user (?P<user>.+?) has (?P<message>.+?) from (?P<ip>\S+) where os is (?P<os>.+)")


class EntryDict(typing.TypedDict):
    auth: str
    user: str
    attempts: int
    ips: set[str]
    last_attempt: datetime.datetime


class FailedLoginsReport(ListReport):
    filename = "failed_logins.pdf"
    name = _("Failed logins")
    description = _("Failed authentication attempts (including MFA failures) per authenticator")
    uuid = "46d0befa-843c-495e-a97d-9e32f57a12bc"

    authenticator = gui.ChoiceField(
        order=1,
        label=_("Authenticator"),
        tooltip=_("Authenticator to filter (or all)"),
        required=True,
    )

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

    @typing.override
    def init_gui(self) -> None:
        self.authenticator.set_choices(
            [gui.choice_item("0-0-0-0", gettext("ALL AUTHENTICATORS"))]
            + [gui.choice_item(v.uuid, v.name) for v in Authenticator.objects.all().order_by("name")]
        )

    def get_data(
        self,
    ) -> tuple[list[dict[str, typing.Any]], list[dict[str, typing.Any]]]:
        start = timezone.make_aware(datetime.datetime.combine(self.start_date.as_date(), datetime.time.min))
        end = timezone.make_aware(datetime.datetime.combine(self.end_date.as_date(), datetime.time.max))

        if self.authenticator.value == "0-0-0-0":
            auth_qs = Authenticator.objects.all()
        else:
            auth_qs = Authenticator.objects.filter(uuid=self.authenticator.value)

        auth_map: dict[int, str] = dict(auth_qs.values_list("id", "name"))
        if not auth_map:
            return [], []

        rows = (
            Log.objects.filter(
                created__gte=start,
                created__lte=end,
                source=types.log.LogSource.WEB,
                owner_type=types.log.LogObjectType.AUTHENTICATOR,
                owner_id__in=list(auth_map.keys()),
                level__gte=types.log.LogLevel.ERROR,
            )
            .order_by("-created")
            .values("created", "data", "owner_id")
        )

        detail: list[dict[str, typing.Any]] = []
        per_user: dict[tuple[str, str], EntryDict] = {}
        for r in rows:
            auth_name = auth_map.get(r["owner_id"], "")
            m = _LOGIN_RX.match(r["data"])
            if m:
                user = m.group("user")
                ip = m.group("ip")
                message = m.group("message")
            else:
                user = ""
                ip = ""
                message = r["data"]
            created = r["created"]
            detail.append(
                {
                    "date": created,
                    "auth": auth_name,
                    "user": user,
                    "ip": ip,
                    "message": message,
                }
            )
            key = (auth_name, user)
            entry = per_user.get(key)
            if entry is None:
                entry = EntryDict(
                    auth=auth_name,
                    user=user,
                    attempts=0,
                    ips=set(),
                    last_attempt=created,
                )
                per_user[key] = entry
            entry["attempts"] += 1
            if ip:
                entry["ips"].add(ip)
            if created > entry["last_attempt"]:
                entry["last_attempt"] = created

        summary = sorted(
            (
                {
                    "auth": v["auth"],
                    "user": v["user"],
                    "attempts": v["attempts"],
                    "ips": ", ".join(sorted(v["ips"])),
                    "last_attempt": v["last_attempt"],
                }
                for v in per_user.values()
            ),
            key=lambda r: r["attempts"],
            reverse=True,
        )

        return summary, detail

    @typing.override
    def generate(self) -> bytes:
        summary, detail = self.get_data()
        return self.template_as_pdf(
            "uds/reports/lists/failed-logins.html",
            dct={
                "summary": summary,
                "detail": detail,
                "beginning": self.start_date.as_date(),
                "ending": self.end_date.as_date(),
            },
            header=gettext("Failed logins"),
            water=gettext("UDS Report of failed logins"),
        )


class FailedLoginsReportCSV(FailedLoginsReport):
    filename = "failed_logins.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "8ae87ec8-7fdd-4772-b86e-51bde4d61b80"

    authenticator = FailedLoginsReport.authenticator
    start_date = FailedLoginsReport.start_date
    end_date = FailedLoginsReport.end_date

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Date"),
                gettext("Authenticator"),
                gettext("User"),
                gettext("IP"),
                gettext("Message"),
            ]
        )
        _summary, detail = self.get_data()
        for v in detail:
            writer.writerow([v["date"], v["auth"], v["user"], v["ip"], v["message"]])
        return output.getvalue().encode()

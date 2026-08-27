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

from django.utils import timezone
from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core.ui import gui
from uds.models import ServerGroup

from .base import ListReport

logger = logging.getLogger(__name__)

ALL_GROUPS: typing.Final[str] = "0-0-0-0"


class ServersListReport(ListReport):
    filename = "servers.pdf"
    name = _("Servers list")
    description = _("Inventory of registered servers, with their last reported stats")
    uuid = "28132a23-9289-4e5c-92e3-313a871b6a7b"

    server_group = gui.ChoiceField(
        order=1,
        label=_("Server group"),
        tooltip=_("Server group to list (or all)"),
        required=True,
    )

    only_stale = gui.CheckBoxField(
        order=2,
        label=_("Only servers not reporting"),
        tooltip=_("List only servers whose last reported stats are stale or missing"),
        default=False,
    )

    @typing.override
    def init_gui(self) -> None:
        vals = [gui.choice_item(ALL_GROUPS, gettext("ALL SERVER GROUPS"))] + [
            gui.choice_item(g.uuid, f"{g.name} ({g.subtype or g.server_type.as_str()})")
            for g in ServerGroup.objects.all().order_by("name")
            if g.uuid
        ]
        self.server_group.set_choices(vals)

    def get_data(self) -> list[dict[str, typing.Any]]:
        groups = ServerGroup.objects.all()
        if self.server_group.value != ALL_GROUPS:
            groups = groups.filter(uuid=self.server_group.value)

        only_stale = self.only_stale.as_bool()
        rows: list[dict[str, typing.Any]] = []
        for group in groups.order_by("name").prefetch_related("servers"):
            for server in group.servers.all().order_by("hostname", "ip"):
                stats = server.stats
                if only_stale and stats is not None and stats.is_valid:
                    continue

                rows.append(
                    {
                        "group": group.name,
                        "server": server.hostname or server.ip,
                        "ip": server.ip,
                        "type": server.server_type.as_str(),
                        "subtype": server.subtype,
                        "os": server.os_type,
                        "version": server.version,
                        "maintenance": gettext("Yes") if server.maintenance_mode else gettext("No"),
                        "locked_until": server.locked_until or "",
                        "last_stats": _stamp_as_str(stats.stamp) if stats else gettext("Never"),
                        "users": stats.current_users if stats else "",
                        "cpu": f"{stats.cpuused * 100:.1f}" if stats else "",
                        "memory": (f"{stats.memused * 100 / stats.memtotal:.1f}" if stats and stats.memtotal else ""),
                    }
                )

        return rows

    @typing.override
    def generate(self) -> bytes:
        return self.template_as_pdf(
            "uds/reports/lists/servers.html",
            dct={"data": self.get_data(), "now": timezone.now()},
            header=gettext("Servers list"),
            water=gettext("UDS Servers list"),
        )


class ServersListReportCSV(ServersListReport):
    filename = "servers.csv"
    mime_type = "text/csv"
    encoded = False
    uuid = "27d008f2-2ab1-411b-b20d-af660a3f7516"

    server_group = ServersListReport.server_group
    only_stale = ServersListReport.only_stale

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                gettext("Group"),
                gettext("Server"),
                gettext("IP"),
                gettext("Type"),
                gettext("Subtype"),
                gettext("OS"),
                gettext("Version"),
                gettext("Maintenance"),
                gettext("Locked until"),
                gettext("Last stats"),
                gettext("Users"),
                gettext("CPU %"),
                gettext("Memory %"),
            ]
        )
        for r in self.get_data():
            writer.writerow(
                [
                    r["group"],
                    r["server"],
                    r["ip"],
                    r["type"],
                    r["subtype"],
                    r["os"],
                    r["version"],
                    r["maintenance"],
                    r["locked_until"],
                    r["last_stats"],
                    r["users"],
                    r["cpu"],
                    r["memory"],
                ]
            )
        return output.getvalue().encode()


def _stamp_as_str(stamp: float) -> str:
    if not stamp:
        return gettext("Never")
    return str(timezone.make_aware(datetime.datetime.fromtimestamp(stamp)))

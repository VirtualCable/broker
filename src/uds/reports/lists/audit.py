# -*- coding: utf-8 -*-

#
# Copyright (c) 2022 Virtual Cable S.L.
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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import collections.abc
import csv
import io
import logging
import typing

from django.utils.translation import gettext
from django.utils.translation import gettext_lazy as _

from uds.core import types
from uds.core.ui import gui
from uds.core.util import dateutils
from uds.models import Log

from .base import ListReport

logger = logging.getLogger(__name__)

RESPONSE_CODES: typing.Final[dict[str, str]] = {
    "200": "OK",
    "400": "Bad Request",
    "401": "Unauthorized",
    "403": "Forbidden",
    "404": "Not Found",
    "405": "Method Not Allowed",
    "500": "Internal Server Error",
    "501": "Not Implemented",
}

RESPONSE_CODE_GROUPS: typing.Final[dict[int, str]] = {
    1: "Informational",
    2: "Success",
    3: "Redirection",
    4: "Client Error",
    5: "Server Error",
}


def _decode_response_code(code: str) -> str:
    try:
        group = int(code) // 100
    except ValueError:
        group = -1  # Unknown

    return code + "/" + RESPONSE_CODES.get(code, RESPONSE_CODE_GROUPS.get(group, "Unknown"))


def _parse_log_data(data: str) -> tuple[str, str, str, str, str] | None:
    """Splits a syslog REST entry into (ip, user, method, response_code, request).

    Two formats are produced by uds.REST.log:
      * log_operation: "ip [user]: [method/response_code] request"
      * log_audit:     "ip [user]: action request"  (no method/response_code)

    Parsed by splitting instead of by regex: data is attacker-influenced (it embeds the
    request path), and the previous regexes backtracked polynomially on crafted input.
    """
    head, sep, rest = data.partition("]: ")
    if not sep:
        return None

    ip, sep, user = head.partition(" [")
    if not sep:
        return None

    if rest.startswith("["):
        method_and_code, sep, request = rest[1:].partition("] ")
        method, code_sep, code = method_and_code.partition("/")
        if sep and code_sep:
            return ip, user, method, _decode_response_code(code), request

    # Audit action row (log_audit): no method/response_code
    return ip, user, "AUDIT", "", rest


class ListReportAuditCSV(ListReport):
    name = _("Audit Log list")  # Report name
    description = _("List administration audit logs")  # Report description
    filename = "audit.csv"
    mime_type = "text/csv"
    encoded = False
    # PDF Report of audit logs is extremely slow on pdf, so we will use csv only

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

    uuid = "b5f5ebc8-44e9-11ed-97a9-efa619da6a49"

    # Generator of data
    def gen_data(
        self,
    ) -> collections.abc.Generator[
        tuple[typing.Any, typing.Any, typing.Any, typing.Any, typing.Any, typing.Any, typing.Any], None, None
    ]:
        # as_datetime() already returns an aware datetime, so no make_aware here
        start = self.start_date.as_datetime().replace(hour=0, minute=0, second=0, microsecond=0)
        end = self.end_date.as_datetime().replace(hour=23, minute=59, second=59, microsecond=999999)
        for i in Log.objects.filter(
            created__gte=start,
            created__lte=end,
            source=types.log.LogSource.REST,
            owner_type=types.log.LogObjectType.SYSLOG,
        ).order_by("-created"):
            # extract ip, user, method, response_code and request from data field
            parsed = _parse_log_data(i.data)
            if not parsed:
                continue

            ip, user, method, response_code, request = parsed
            yield (
                i.created,
                i.level_as_str,
                ip,
                user,
                method,
                response_code,
                request,
            )

    @typing.override
    def generate(self) -> bytes:
        output = io.StringIO()
        writer = csv.writer(output)

        writer.writerow(
            [
                gettext("Date"),
                gettext("Level"),
                gettext("IP"),
                gettext("User"),
                gettext("Method"),
                gettext("Response code"),
                gettext("Request"),
            ]
        )

        for line in self.gen_data():
            writer.writerow(line)

        return output.getvalue().encode()

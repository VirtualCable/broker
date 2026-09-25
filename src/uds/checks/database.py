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
Author: Andres Schumann, aschumann at virtualcable dot es

Checks on the database server.
"""

import datetime
import typing

from django.utils import timezone
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds.core import types
from uds.core.checks import Check, CheckOutcome
from uds.core.util.model import sql_now

_MAX_TIME_DIFFERENCE: typing.Final[datetime.timedelta] = datetime.timedelta(seconds=60)


class DbAppTimeMismatchCheck(Check):
    """Time of the database server against the time of this UDS server."""

    id: typing.ClassVar[str] = "db-app-time-mismatch"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Compares the time of the database server with the time of this UDS server and fails when "
        "they differ by more than 60 seconds. UDS takes the time from the database, so "
        "expirations and schedules are off. Sync both with NTP and check that the database time "
        "zone matches TIME_ZONE."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        # sql_now() is the database time; a naive database time is read in TIME_ZONE,
        # so a time zone mismatch shows up here as a whole number of hours
        difference = abs((sql_now() - timezone.now()).total_seconds())
        if difference > _MAX_TIME_DIFFERENCE.total_seconds():
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _(
                    "The database time differs from this server's time by {seconds:.0f} seconds."
                    " UDS takes the time from the database, so expirations and schedules are off."
                    " Sync both with NTP and check that the database time zone matches TIME_ZONE."
                ).format(seconds=difference),
            )
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("The database and this server agree on the time."),
        )

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

Checks on the state of user services.
"""

import datetime
import typing

from django.db.models import Count, Q
from django.utils.translation import gettext as _

from uds import models
from uds.core import types
from uds.core.checks import Check, CheckOutcome
from uds.core.types.states import State
from uds.core.util.config import GlobalConfig
from uds.core.util.model import sql_now

_MAX_EXAMPLES: typing.Final[int] = 5

# An assigned user service not used for this long only takes capacity
_STALE_AGE: typing.Final[datetime.timedelta] = datetime.timedelta(days=90)


def _pools_summary(user_services: "models.QuerySet[models.UserService]") -> str:
    by_pool = (
        user_services.values("deployed_service__name")
        .annotate(total=Count("id"))
        .order_by("-total", "deployed_service__name")
    )
    summary = ", ".join(f"{row['deployed_service__name']} ({row['total']})" for row in by_pool[:_MAX_EXAMPLES])
    if by_pool.count() > _MAX_EXAMPLES:
        summary += "..."
    return summary


class UserServicesStuckPreparingCheck(Check):
    """User services that never become ready: still preparing, or usable but the actor never reported."""

    id: typing.ClassVar[str] = "user-services-stuck-preparing"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        max_age = datetime.timedelta(seconds=GlobalConfig.MAX_INITIALIZING_TIME.as_int())
        stuck = models.UserService.objects.filter(
            Q(state=State.PREPARING) | Q(state=State.USABLE, os_state=State.PREPARING),
            state_date__lt=sql_now() - max_age,
        )
        count = stuck.count()
        if count:
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _(
                    "{count} user service(s) have been preparing for more than {hours:.0f}h: {pools}."
                    " Nothing removes them on their own; check the provider and whether the actor"
                    " inside the machine can reach the broker."
                ).format(count=count, hours=max_age.total_seconds() / 3600, pools=_pools_summary(stuck)),
            )
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("No user services stuck in preparation."),
        )


class StaleUserServicesCheck(Check):
    """Assigned user services not used for months."""

    id: typing.ClassVar[str] = "stale-user-services"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        limit = sql_now() - _STALE_AGE
        stale = models.UserService.objects.filter(
            state=State.USABLE,
            cache_level=0,
            in_use=False,
            in_use_date__lt=limit,
            creation_date__lt=limit,
        )
        count = stale.count()
        if count:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{count} assigned user service(s) have not been used for more than {days} days: {pools}."
                    " They count against the pool limits and keep capacity on the provider."
                ).format(count=count, days=_STALE_AGE.days, pools=_pools_summary(stale)),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("No assigned user services unused for more than {days} days.").format(days=_STALE_AGE.days),
        )

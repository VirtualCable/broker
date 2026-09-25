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

Checks on what the OS managers ask the actors to do.
"""

import collections
import functools
import operator
import typing

from django.db.models import Q
from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds import models
from uds.core import types
from uds.core.checks import Check, CheckOutcome

from .logs import _window

_MAX_EXAMPLES: typing.Final[int] = 5

# Error lines the actors send to the broker when a domain join fails:
# 4.0 actor ("Error joining domain", "Error join machine to domain") and
# 5.0 actor ("NetJoinDomain ... failed" on Windows, "realm join ... failed" on Linux)
_DOMAIN_JOIN_MARKERS: typing.Final[tuple[str, ...]] = (
    "Error joining domain",
    "Error join machine to domain",
    "NetJoinDomain",
    "realm join",
)


class DomainJoinFailures24hCheck(Check):
    """Domain join errors reported by the actors in the last 24h."""

    id: typing.ClassVar[str] = "domain-join-failures-24h"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Counts the domain join errors reported by the actors in the last 24 hours, grouped by "
        "service pool. Check the account, password and OU of the OS manager and that the machines "
        "can reach the domain controllers."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        failed_ids = models.Log.objects.filter(
            functools.reduce(operator.or_, (Q(data__icontains=marker) for marker in _DOMAIN_JOIN_MARKERS)),
            created__gte=_window(),
            owner_type=types.log.LogObjectType.USERSERVICE,
            source=types.log.LogSource.ACTOR,
            level__gte=types.log.LogLevel.ERROR,
        ).values_list("owner_id", flat=True)
        failures = collections.Counter(failed_ids)
        if failures:
            by_pool: collections.Counter[str] = collections.Counter()
            for userservice_id, pool_name in models.UserService.objects.filter(
                id__in=failures.keys()
            ).values_list("id", "deployed_service__name"):
                by_pool[pool_name] += failures[userservice_id]
            examples = ", ".join(f"{name} ({total})" for name, total in by_pool.most_common(_MAX_EXAMPLES))
            if len(by_pool) > _MAX_EXAMPLES:
                examples += "..."
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{count} domain join error(s) reported by the actors in the last 24h: {examples}."
                    " Check the account, password and OU of the OS manager and that the machines reach"
                    " the domain controllers."
                ).format(count=sum(failures.values()), examples=examples or _("machines already removed")),
                [f"{name} ({total})" for name, total in by_pool.most_common()],
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("No domain join errors reported by the actors in the last 24h."),
        )

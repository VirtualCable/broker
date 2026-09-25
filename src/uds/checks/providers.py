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

Checks on service providers.
"""

import collections
import typing

from django.utils.translation import gettext as _

from uds import models
from uds.core import types
from uds.core.checks import Check, CheckOutcome

from .logs import _window

_MAX_EXAMPLES: typing.Final[int] = 5

# Errors of a single provider in the last 24h above which it is flagged
_ERRORS_THRESHOLD: typing.Final[int] = 10


class ProviderErrors24hCheck(Check):
    """Providers with repeated errors in the last 24h."""

    id: typing.ClassVar[str] = "provider-errors-24h"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        errors = models.Log.objects.filter(created__gte=_window(), level__gte=types.log.LogLevel.ERROR)
        by_provider: collections.Counter[str] = collections.Counter()

        # Machine errors are logged on the user service, not on the provider
        userservice_errors = collections.Counter(
            errors.filter(
                owner_type=types.log.LogObjectType.USERSERVICE,
                source=types.log.LogSource.SERVICE,
            ).values_list("owner_id", flat=True)
        )
        for userservice_id, provider_name in models.UserService.objects.filter(
            id__in=userservice_errors.keys()
        ).values_list("id", "deployed_service__service__provider__name"):
            by_provider[provider_name] += userservice_errors[userservice_id]

        provider_errors = collections.Counter(
            errors.filter(owner_type=types.log.LogObjectType.PROVIDER).values_list("owner_id", flat=True)
        )
        for provider_id, provider_name in models.Provider.objects.filter(
            id__in=provider_errors.keys()
        ).values_list("id", "name"):
            by_provider[provider_name] += provider_errors[provider_id]

        failing = [(name, total) for name, total in by_provider.most_common() if total >= _ERRORS_THRESHOLD]
        if failing:
            examples = ", ".join(f"{name} ({total})" for name, total in failing[:_MAX_EXAMPLES])
            if len(failing) > _MAX_EXAMPLES:
                examples += "..."
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _(
                    "Providers with {threshold} or more errors in the last 24h: {examples}."
                    " Check their credentials and the hypervisor logs."
                ).format(threshold=_ERRORS_THRESHOLD, examples=examples),
            )
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("No provider has repeated errors in the last 24h."),
        )


class ProviderInMaintenanceModeCheck(Check):
    """Providers left in maintenance mode."""

    id: typing.ClassVar[str] = "provider-in-maintenance-mode"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        names = list(
            models.Provider.objects.filter(maintenance_mode=True)
            .order_by("name")
            .values_list("name", flat=True)
        )
        if names:
            return (
                types.checks.CheckSeverity.LOW,
                False,
                _(
                    "Providers in maintenance mode: {names}."
                    " No machines are created or removed on them until maintenance is turned off."
                ).format(names=", ".join(names)),
            )
        return (
            types.checks.CheckSeverity.LOW,
            True,
            _("No providers in maintenance mode."),
        )

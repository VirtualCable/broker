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

Manual check that asks every authenticator able to assess itself.
"""

import typing

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds import models
from uds.core import types
from uds.core.checks import CheckOutcome, ManualCheck

_MAX_EXAMPLES: typing.Final[int] = 5

_SEVERITY_ORDER: typing.Final[list[types.checks.CheckSeverity]] = list(types.checks.CheckSeverity)


class AuthenticatorsHealthCheck(ManualCheck):
    """Aggregates the ``health_check()`` of every authenticator that implements it."""

    id: typing.ClassVar[str] = "authenticators-health"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Asks every authenticator that knows how to assess itself for problems. Today that covers "
        "SAML, which reports signing certificates of the IdP that have expired, expire within 30 "
        "days or cannot be read. The details list every problem found, worst first."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        problems: list[tuple[types.checks.CheckSeverity, str]] = []
        for authenticator in models.Authenticator.objects.all().order_by("name"):
            if not authenticator.get_type().has_health_check():
                continue
            try:
                outcomes = authenticator.get_instance().health_check()
            except Exception as e:
                problems.append((types.checks.CheckSeverity.MEDIUM, f"{authenticator.name}: {e}"))
                continue
            problems.extend(
                (severity, f"{authenticator.name}: {message}")
                for severity, ok, message, *_details in outcomes
                if not ok
            )

        if problems:
            problems.sort(key=lambda problem: _SEVERITY_ORDER.index(problem[0]))
            details = "; ".join(message for _severity, message in problems[:_MAX_EXAMPLES])
            if len(problems) > _MAX_EXAMPLES:
                details += "..."
            return (problems[0][0], False, details, [message for _severity, message in problems])
        return (
            types.checks.CheckSeverity.HIGH,
            True,
            _("All authenticators that can assess themselves report no problems."),
        )

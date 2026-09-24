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
Author: Adolfo Gómez, dkmaster at dkmon dot com

Check for service pool publications stuck in a transitional state.

A publication goes through ``PREPARING`` (being built on the provider) and, on
removal, through ``REMOVING``/``CANCELING``. All of them are expected to finish
on their own; one that stays beyond the configured maximum age means the
publication task is stuck (broken provider, lost machine, ...). This is a
``HEALTH`` category check: the pool keeps working with its current usable
publication, but the stuck task will never complete by itself.
"""

import datetime
import typing

from django.utils.translation import gettext as _

from uds import models
from uds.core import types
from uds.core.checks import Check, CheckOutcome
from uds.core.types.states import State
from uds.core.util.config import GlobalConfig
from uds.core.util.model import sql_now

# Maximum examples included in the failure message
_MAX_EXAMPLES: typing.Final[int] = 5

# States a publication is not expected to stay in for long
_TRANSITIONAL_STATES: typing.Final[list[str]] = [
    State.PREPARING,
    State.REMOVING,
    State.CANCELING,
]


class StuckPublicationsCheck(Check):
    """Publications stuck in a transitional state (preparing/removing/canceling)."""

    id: typing.ClassVar[str] = "stuck-publications"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH

    @typing.override
    def run(self) -> CheckOutcome:
        max_age = datetime.timedelta(seconds=GlobalConfig.MAX_PUBLICATION_TIME.as_int())
        limit = sql_now() - max_age

        stuck = (
            models.ServicePoolPublication.objects.filter(
                state__in=_TRANSITIONAL_STATES,
                state_date__lt=limit,
            )
            .select_related("deployed_service")
            .order_by("state_date")
        )
        if stuck:
            now = sql_now()
            examples = ", ".join(
                f"{publication.deployed_service.name} rev {publication.revision}"
                f" ({State.from_str(publication.state).name.lower()}, {(now - publication.state_date).total_seconds() / 3600:.0f}h)"
                for publication in stuck[:_MAX_EXAMPLES]
            )
            if stuck.count() > _MAX_EXAMPLES:
                examples += "..."
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{n} publication(s) stuck in a transitional state for more than {hours}h: {examples}."
                    " The publication task will not complete by itself; check the provider and retry or cancel."
                ).format(n=stuck.count(), hours=max_age.total_seconds() / 3600, examples=examples),
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("No publications stuck in transitional states for more than {hours}h.").format(
                hours=max_age.total_seconds() / 3600
            ),
        )

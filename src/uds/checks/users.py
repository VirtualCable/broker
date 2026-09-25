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

Manual check over the users of every authenticator.
"""

import collections
import typing
import unicodedata

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds import models
from uds.core import types
from uds.core.checks import CheckOutcome, ManualCheck

_MAX_EXAMPLES: typing.Final[int] = 5


def _normalized(name: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


class DuplicateUsersInAuthenticatorCheck(ManualCheck):
    """Users of one authenticator whose names only differ in case, spacing or equivalent characters."""

    id: typing.ClassVar[str] = "duplicate-users-in-authenticator"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Looks for users of the same authenticator whose names only differ in case, spacing or "
        "equivalent Unicode characters. Some databases allow them, and each copy gets its own "
        "services and permissions. Remove or merge the duplicated users listed in the details."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        # Walks every user: that is why this check is manual
        names: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
        for authenticator_name, name in (
            models.User.objects.order_by().values_list("manager__name", "name").iterator()
        ):
            names[(authenticator_name, _normalized(name))].append(name)

        duplicates = [
            (authenticator, found) for (authenticator, _key), found in names.items() if len(found) > 1
        ]
        if duplicates:
            examples = ", ".join(
                f"{authenticator}: {' / '.join(repr(name) for name in found)}"
                for authenticator, found in duplicates[:_MAX_EXAMPLES]
            )
            if len(duplicates) > _MAX_EXAMPLES:
                examples += "..."
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "{count} user name(s) are duplicated inside an authenticator once case and spacing are"
                    " ignored: {examples}. Each copy gets its own services and permissions."
                ).format(count=len(duplicates), examples=examples),
                [
                    f"{authenticator}: {' / '.join(repr(name) for name in found)}"
                    for authenticator, found in duplicates
                ],
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _("No duplicated user names inside the authenticators."),
        )

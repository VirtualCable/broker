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

Check for the deferred deletion queues (``uds.workers.deferred_deleter``).

Elements parked there (VMs to stop, stopping, to delete or deleting) normally
drain in minutes. One that stays beyond the configured maximum age means the
target platform is probably failing to stop or delete machines, so the check
flags the oldest ones. This is a ``HEALTH`` category check: nothing exploitable,
but it degrades the platform (stray machines keep consuming resources).
"""

import datetime
import typing

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds.core import types
from uds.core.checks import AutomaticCheck, CheckOutcome
from uds.core.types.deferred_deletion import DeletionInfo, DeferredStorageGroup
from uds.core.util.config import GlobalConfig
from uds.core.util.model import sql_now

# Maximum examples included in the failure message
_MAX_EXAMPLES: typing.Final[int] = 5


class DeferredDeletionStuckCheck(AutomaticCheck):
    """Elements stuck in the deferred deletion queues."""

    id: typing.ClassVar[str] = "deferred-deletion-stuck"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Looks for machines that have been waiting in the deferred deletion queues for longer "
        "than MAX_DEFERRED_DELETION_TIME. It usually means the platform is failing to stop or "
        "delete them. Check the provider; the details list every stuck machine."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        max_age = datetime.timedelta(seconds=GlobalConfig.MAX_DEFERRED_DELETION_TIME.as_int())
        limit = sql_now() - max_age

        total = 0
        stuck: list[str] = []
        now = sql_now()
        for group in DeferredStorageGroup:
            with DeletionInfo.deferred_storage.as_dict(group) as storage_dict:
                total += len(storage_dict)
                for _key, info in storage_dict.unlocked_items():
                    if info.created < limit:
                        age_hours = (now - info.created).total_seconds() / 3600
                        stuck.append(f"{group.value}/{info.vmid} ({age_hours:.0f}h)")

        if stuck:
            examples = ", ".join(stuck[:_MAX_EXAMPLES]) + ("..." if len(stuck) > _MAX_EXAMPLES else "")
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "Deferred deletion queues hold {total} element(s); {stuck} of them older than {hours}h:"
                    " {examples}. The target platform is probably failing to stop or delete machines."
                ).format(
                    total=total, stuck=len(stuck), hours=max_age.total_seconds() / 3600, examples=examples
                ),
                stuck,
            )
        return (
            types.checks.CheckSeverity.MEDIUM,
            True,
            _(
                "Deferred deletion queues are within limits ({total} element(s), none older than {hours}h)."
            ).format(total=total, hours=max_age.total_seconds() / 3600),
        )

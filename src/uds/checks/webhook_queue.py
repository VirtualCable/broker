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

Check for the webhook notification delivery queue.

This is a ``HEALTH`` category check: an ever-growing queue is not exploitable,
but it degrades the system, so administrators get notified before that happens.
"""

import typing

from django.utils.translation import gettext as _
from django.utils.translation import gettext_noop

from uds.core import types
from uds.core.checks import AutomaticCheck, CheckOutcome

# Soft threshold: warn that the queue is growing
SOFT_THRESHOLD: typing.Final[int] = 2000
# Hard threshold: the queue is close to the enqueue cap and may degrade the system
HARD_THRESHOLD: typing.Final[int] = 10000


class WebhookQueueSizeCheck(AutomaticCheck):
    """Webhook notification queue size within control."""

    id: typing.ClassVar[str] = "webhook-queue-size"
    category: typing.ClassVar[types.checks.CheckCategory] = types.checks.CheckCategory.HEALTH
    description: typing.ClassVar[str] = gettext_noop(
        "Counts the webhook notifications waiting to be delivered: from 2000 it warns, and from "
        "10000 the queue is close to its limit and new events will be dropped. A growing queue "
        "means an endpoint is failing or unreachable. Check the webhook notifiers and their "
        "endpoints."
    )

    @typing.override
    def run(self) -> CheckOutcome:
        # Imported here to avoid pulling notifier modules unless this check runs
        from uds.notifiers.webhook import queue

        count = queue.pending_count()
        if count > HARD_THRESHOLD:
            return (
                types.checks.CheckSeverity.HIGH,
                False,
                _(
                    "Webhook notification queue holds {n} pending events (near the {cap} enqueue cap)."
                    " Check the destination endpoints: undeliverable events will be dropped when the cap is reached."
                ).format(n=count, cap=queue.MAX_PENDING),
            )
        if count > SOFT_THRESHOLD:
            return (
                types.checks.CheckSeverity.MEDIUM,
                False,
                _(
                    "Webhook notification queue is growing ({n} pending events)."
                    " Some webhook endpoint is probably failing or unreachable."
                ).format(n=count),
            )
        return (
            types.checks.CheckSeverity.INFO,
            True,
            _("Webhook notification queue size is under control ({n} pending events).").format(n=count),
        )

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
"""

import logging
import time
import typing

import requests

from uds.core import jobs
from uds.core.util import security
from uds.core.util.backoff import Backoff
from uds.core.util.config import GlobalConfig
from uds.models import Notifier

from . import queue
from .queue import QueuedRequest

logger: logging.Logger = logging.getLogger(__name__)

# Maximum elements sent per notifier on each run (strict FIFO per stream)
MAX_PER_NOTIFIER: typing.Final[int] = 128
# Initial backoff (seconds) after a delivery failure
BACKOFF_SEED: typing.Final[int] = 5


class WebhookDispatcherJob(jobs.Job):
    """
    Sends queued webhook notifications.

    The scheduler guarantees a single execution cluster-wide (DB lock), so
    this job is the only consumer of the webhook delivery queue. Elements are
    sent in strict FIFO order per notifier: any failure (connection error or
    HTTP >= 400) blocks that notifier's stream until the next run, applying
    an exponential backoff capped by the notifier configuration.
    """

    friendly_name = "Webhook Dispatcher"

    @typing.override
    def next_execution_delay(self) -> int:
        return max(1, GlobalConfig.WEBHOOK_DISPATCH_FREQUENCY.as_int())

    @typing.override
    def run(self) -> None:
        existing_notifiers = set(Notifier.objects.values_list("uuid", flat=True))
        now = time.time()

        # Partition queue: due elements grouped by notifier, expired/orphaned dropped.
        # The scan is capped (MAX_SCAN_PER_RUN) so an accumulated queue cannot make
        # a single run unbounded; the rest is deferred to following runs.
        due: dict[str, list[tuple[str, QueuedRequest]]] = {}
        to_remove: list[str] = []
        for scan_index, (key, element) in enumerate(queue.all_elements()):
            if scan_index >= queue.MAX_SCAN_PER_RUN:
                logger.warning(
                    "Webhook queue scan limit reached (%d elements), deferring the rest to the next run",
                    queue.MAX_SCAN_PER_RUN,
                )
                break
            if element.notifier_uuid not in existing_notifiers:
                to_remove.append(key)
                continue
            if now - element.created > element.retention_seconds:
                logger.warning(
                    "Dropping expired webhook notification for %s (attempts: %d)",
                    element.url,
                    element.attempts,
                )
                to_remove.append(key)
                continue
            due.setdefault(element.notifier_uuid, []).append((key, element))

        queue.remove(to_remove)

        if not due:
            return

        with security.secure_requests_session() as session:
            for notifier_uuid, elements in due.items():
                elements.sort(key=lambda item: item[1].created)  # FIFO
                self._drain(session, notifier_uuid, elements[:MAX_PER_NOTIFIER])

    def _drain(
        self, session: requests.Session, notifier_uuid: str, elements: list[tuple[str, QueuedRequest]]
    ) -> None:
        if not elements:
            return
        first = elements[0][1]
        backoff = Backoff(owner=queue.QUEUE_OWNER, fail_time=BACKOFF_SEED, max_time=first.backoff_cap_seconds)
        if backoff.is_bad(notifier_uuid):
            logger.debug(
                "Webhook endpoint %s is in backoff, skipping %d queued notifications",
                first.url,
                len(elements),
            )
            return

        for key, element in elements:
            try:
                response = session.request(
                    element.method,
                    element.url,
                    headers=element.headers,
                    data=element.body.encode("utf-8"),
                    timeout=element.timeout,
                    verify=element.verify_ssl,
                )
                if response.status_code >= 400:
                    # Strict FIFO: any HTTP error blocks the stream until next run
                    self._mark_failure(backoff, notifier_uuid, key, element, f"HTTP {response.status_code}")
                    return
            except requests.RequestException as e:
                self._mark_failure(backoff, notifier_uuid, key, element, str(e))
                return
            queue.remove([key])
            backoff.clear_bad(notifier_uuid)

    def _mark_failure(
        self, backoff: Backoff, notifier_uuid: str, key: str, element: QueuedRequest, reason: str
    ) -> None:
        logger.warning(
            "Webhook delivery to %s failed (%s), will retry. Attempts so far: %d",
            element.url,
            reason,
            element.attempts + 1,
        )
        element.attempts += 1
        queue.update(key, element)
        backoff.mark_bad(notifier_uuid)

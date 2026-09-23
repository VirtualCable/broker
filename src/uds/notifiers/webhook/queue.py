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

Durable queue for pending webhook notifications, backed by ``Storage``.

One Storage entry per event (random key), so concurrent enqueues from
different cluster nodes never collide. The dispatcher job (cluster-wide
exclusive) is the only consumer.
"""

import collections.abc
import dataclasses
import logging
import typing
import uuid

from uds.core.util.storage import Storage

logger: logging.Logger = logging.getLogger(__name__)

QUEUE_OWNER: typing.Final[str] = "webhook-notifications-queue"

# Hard cap on pending elements. When reached, new notifications are dropped
# (with an error log) instead of growing the queue indefinitely. The security
# checker will have flagged the situation well before this point.
MAX_PENDING: typing.Final[int] = 10000

# Maximum elements scanned by the dispatcher on each run. Pure prevention:
# with an accumulated queue, each run only examines this window (the rest is
# deferred to following runs), bounding memory and work per run.
MAX_SCAN_PER_RUN: typing.Final[int] = 4096


@dataclasses.dataclass
class QueuedRequest:
    """
    A fully rendered webhook request, ready to be sent as-is.

    All notifier configuration is snapshotted at enqueue time, so the
    dispatcher job needs no access to the notifier instance.
    """

    notifier_uuid: str
    # Creation timestamp (unix epoch), used for FIFO ordering and retention
    created: float
    # Number of failed delivery attempts (informational, for logs)
    attempts: int
    method: str
    url: str
    headers: dict[str, str]
    body: str
    timeout: int
    verify_ssl: bool
    # Per-notifier snapshot (Advanced tab values at enqueue time)
    retention_seconds: int
    backoff_cap_seconds: int


def _storage() -> Storage:
    return Storage(QUEUE_OWNER)


def enqueue(request: QueuedRequest) -> bool:
    """
    Adds a request to the queue.

    Returns False (and does not enqueue) if the queue has reached MAX_PENDING.
    """
    if pending_count() >= MAX_PENDING:
        return False
    with _storage().as_dict(group=request.notifier_uuid) as dct:
        dct[uuid.uuid4().hex] = request
    return True


def pending_count() -> int:
    """Number of elements currently on the queue."""
    with _storage().as_dict() as dct:
        return len(dct)


def all_elements() -> collections.abc.Iterator[tuple[str, QueuedRequest]]:
    """
    Lazily iterates every queued element as (key, request).

    Values are decoded on demand from the database, one at a time; nothing is
    materialized up front. Keys are the Storage logical keys, required for
    removal/update.
    """
    with _storage().as_dict() as dct:
        yield from dct.items_iter()


def remove(keys: collections.abc.Iterable[str]) -> None:
    """Removes the given keys from the queue (ignores unknown keys)."""
    if not keys:
        return
    with _storage().as_dict() as dct:
        for key in keys:
            dct.delete(key)


def update(key: str, request: QueuedRequest) -> None:
    """Replaces a queued element (e.g. to bump its attempts counter)."""
    with _storage().as_dict(group=request.notifier_uuid) as dct:
        dct[key] = request

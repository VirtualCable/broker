#
# Copyright (c) 2026 Virtual Cable S.L.
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without modification,
# are permitted provided that the following conditions are met:
#
#    * Redistributions of source code must retain the above copyright notice,
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
# FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT,
# TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
# OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
#

"""
Author: Adolfo Gómez, dkmaster at dkmon dot com

Periodic cleanup of expired MFA remember-device records.

Records live in the ``mfs`` storage namespace, keyed by the ``mfa_status``
cookie value, as ``(user_unique_id, created, ip, expires)``. Since the
``remember_device`` window is configured per MFA provider (with no global
upper bound), each record carries its own expiry instant (4th field, written
by ``uds.web.views.mfa``); legacy records written before that field existed
are purged after a conservative global age cap instead.
"""

import logging
import pickle  # nosec: storage data is owned by the broker
import time
import typing

from uds.core.jobs import Job
from uds.core.util import storage
from uds.models.storage import Storage as DBStorageModel

logger: logging.Logger = logging.getLogger(__name__)

# Cadence: once a week. Records carry their own expiry instant, so the exact
# delay is not critical (expired records just linger at most a week).
WEEKLY_EXECUTION_DELAY: typing.Final[int] = 7 * 24 * 60 * 60

# Legacy records (written before the expiry field existed) have no per-record
# expiry. No remember_device window should exceed this; anything older is dead.
LEGACY_MAX_AGE_SECONDS: typing.Final[int] = 365 * 24 * 60 * 60


class MFARememberDeviceCleaner(Job):
    """Purges expired MFA remember-device records from the 'mfs' storage."""

    friendly_name = "MFA Remember Device Cleaner"

    @typing.override
    def next_execution_delay(self) -> int:
        return WEEKLY_EXECUTION_DELAY

    @typing.override
    def run(self) -> None:
        store: storage.Storage = storage.Storage("mfs")
        now: float = time.time()
        to_remove: list[str] = []
        for db_key, data, _attr1 in store.filter():
            try:
                record: typing.Any = pickle.loads(data)  # nosec: storage data is owned by the broker
            except Exception:
                # Corrupted record: it can never be a valid remember-device
                # entry, so drop it
                to_remove.append(db_key)
                continue
            record_items = typing.cast(
                "tuple[typing.Any, ...] | None", record if isinstance(record, tuple) else None
            )
            if record_items is None or len(record_items) < 2:
                # Not a remember-device record shape (this storage namespace is
                # only used by the MFA remember-device feature)
                to_remove.append(db_key)
                continue
            created = record_items[1] if isinstance(record_items[1], (int, float)) else 0.0
            if len(record_items) >= 4 and isinstance(record_items[3], (int, float)):
                expires: float = float(record_items[3])
            else:
                expires = created + LEGACY_MAX_AGE_SECONDS
            if expires < now:
                to_remove.append(db_key)
        if to_remove:
            DBStorageModel.objects.filter(key__in=to_remove).delete()
            logger.info("Removed %s expired MFA remember-device records", len(to_remove))
        else:
            logger.debug("No expired MFA remember-device records found")

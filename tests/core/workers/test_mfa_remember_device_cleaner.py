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
Tests for the MFA remember-device cleaner worker: expired records (own expiry
instant or legacy age cap) are purged from the 'mfs' storage, valid ones kept.
"""

import pickle  # nosec: storage data is owned by the broker
import time
import typing

from uds.core.environment import Environment
from uds.core.util import storage
from uds.workers.mfa_remember_device_cleaner import (
    LEGACY_MAX_AGE_SECONDS,
    WEEKLY_EXECUTION_DELAY,
    MFARememberDeviceCleaner,
)

from tests.utils.test import UDSTestCase


def _now() -> float:
    return time.time()


class MFARememberDeviceCleanerTest(UDSTestCase):
    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.store: storage.Storage = storage.Storage("mfs")

    def _put(self, key: str, record: typing.Any) -> None:
        self.store.save_pickled(key, record)

    def _remaining_records(self) -> list[typing.Any]:
        return [pickle.loads(data) for _key, data, _attr1 in self.store.filter()]

    def test_weekly_execution_delay(self) -> None:
        cleaner = MFARememberDeviceCleaner(Environment.testing_environment())
        self.assertEqual(cleaner.next_execution_delay(), WEEKLY_EXECUTION_DELAY)
        self.assertEqual(WEEKLY_EXECUTION_DELAY, 7 * 24 * 60 * 60)

    def test_expired_record_with_expiry_field_is_removed(self) -> None:
        now = _now()
        self._put("expired", ("user-a", now - 1000, "10.0.0.1", now - 1))
        self._put("valid", ("user-b", now - 1000, "10.0.0.2", now + 100000))
        cleaner = MFARememberDeviceCleaner(Environment.testing_environment())
        cleaner.run()
        remaining = self._remaining_records()
        self.assertNotIn(("user-a", now - 1000, "10.0.0.1", now - 1), remaining)
        self.assertIn(("user-b", now - 1000, "10.0.0.2", now + 100000), remaining)

    def test_legacy_expired_record_is_removed_by_age_cap(self) -> None:
        now = _now()
        # Legacy shape: 3-tuple without expiry field
        self._put("legacy-old", ("user-c", now - LEGACY_MAX_AGE_SECONDS - 10, "10.0.0.3"))
        self._put("legacy-recent", ("user-d", now - 1000, "10.0.0.4"))
        cleaner = MFARememberDeviceCleaner(Environment.testing_environment())
        cleaner.run()
        remaining = self._remaining_records()
        self.assertNotIn(("user-c", now - LEGACY_MAX_AGE_SECONDS - 10, "10.0.0.3"), remaining)
        self.assertIn(("user-d", now - 1000, "10.0.0.4"), remaining)

    def test_invalid_shape_record_is_removed(self) -> None:
        # This storage namespace is only used by the MFA remember-device
        # feature, so malformed entries are dead data
        self._put("garbage", 123)
        cleaner = MFARememberDeviceCleaner(Environment.testing_environment())
        cleaner.run()
        remaining = self._remaining_records()
        self.assertNotIn(123, remaining)

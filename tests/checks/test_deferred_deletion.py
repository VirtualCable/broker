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

Tests for the deferred deletion stuck check (uds.checks.deferred_deletion).
"""

import datetime
import typing

from uds.core import types
from uds.core.checks import runner as runner_module
from uds.core.types.deferred_deletion import DeletionInfo, DeferredStorageGroup
from uds.core.util.model import sql_now

from ..utils.test import UDSTransactionTestCase


class DeferredDeletionStuckCheckTest(UDSTransactionTestCase):
    def _run_check(self) -> types.checks.CheckResult:
        results = {result.id: result for result in runner_module.run_checks()}
        self.assertIn("deferred-deletion-stuck", results)
        return results["deferred-deletion-stuck"]

    def _enqueue(
        self,
        group: DeferredStorageGroup,
        vmid: str,
        *,
        service_uuid: str = "00000000-0000-0000-0000-000000000001",
        age: datetime.timedelta | None = None,
    ) -> None:
        """Enqueues a DeletionInfo, optionally backdating its creation time."""
        DeletionInfo.create_on_storage(group, vmid, service_uuid)
        if age is not None:
            with DeletionInfo.deferred_storage.as_dict(group, atomic=True) as storage_dict:
                info = storage_dict[DeletionInfo.generate_key(service_uuid, vmid)]
                typing.cast("DeletionInfo", info).created = sql_now() - age
                storage_dict[DeletionInfo.generate_key(service_uuid, vmid)] = info

    def test_passes_with_empty_queues(self) -> None:
        result = self._run_check()
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.category, types.checks.CheckCategory.HEALTH)

    def test_passes_with_fresh_elements(self) -> None:
        self._enqueue(DeferredStorageGroup.DELETING, "vm-1", age=datetime.timedelta(minutes=5))
        self._enqueue(DeferredStorageGroup.STOPPING, "vm-2", age=datetime.timedelta(minutes=1))

        result = self._run_check()
        self.assertTrue(result.ok, result.message)

    def test_fails_with_stale_element(self) -> None:
        self._enqueue(
            DeferredStorageGroup.TO_DELETE,
            "vm-stuck",
            age=datetime.timedelta(hours=48),  # default max age is 24h
        )
        self._enqueue(DeferredStorageGroup.DELETING, "vm-fresh", age=datetime.timedelta(minutes=5))

        result = self._run_check()
        self.assertFalse(result.ok, result.message)
        self.assertEqual(result.severity, types.checks.CheckSeverity.MEDIUM)
        self.assertIn("vm-stuck", result.message)
        self.assertNotIn("vm-fresh", result.message)

    def test_counts_pending_elements_on_message(self) -> None:
        self._enqueue(DeferredStorageGroup.TO_STOP, "vm-a")
        self._enqueue(DeferredStorageGroup.TO_STOP, "vm-b")

        result = self._run_check()
        self.assertTrue(result.ok, result.message)
        self.assertIn("2", result.message)

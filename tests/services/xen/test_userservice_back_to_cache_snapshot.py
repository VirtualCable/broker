
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
"""

import contextlib
import dataclasses
import typing
from unittest import mock

from uds.core import types
from uds.services.Xen.xen import types as xen_types

from ...utils.helpers import limited_iterator
from ...utils.test import UDSTransactionTestCase
from . import fixtures

if typing.TYPE_CHECKING:
    from uds.services.Xen.deployment import XenLinkedUserService

SNAPSHOT_SERVICE_VALUES: typing.Final = {
    "put_back_to_cache": "snapshot",
    "try_soft_shutdown": True,
}
SNAPSHOT_TASK: typing.Final = "OpaqueRef:snapshot-task"


class TestXenBackToCacheSnapshot(UDSTransactionTestCase):
    task_status: xen_types.TaskStatus

    def setUp(self) -> None:
        super().setUp()
        fixtures.clean()
        fixtures.set_all_vm_state(xen_types.PowerState.HALTED)
        self.task_status = xen_types.TaskStatus.SUCCESS

    def tearDown(self) -> None:
        fixtures.clean()
        super().tearDown()

    def task_info(self, task_ref: str, *args: typing.Any, **kwargs: typing.Any) -> xen_types.TaskInfo:
        return dataclasses.replace(fixtures.TASK_INFO, opaque_ref=task_ref, status=self.task_status)

    @contextlib.contextmanager
    def setup_data(
        self, **service_values: typing.Any
    ) -> typing.Iterator[tuple["XenLinkedUserService", mock.MagicMock]]:
        with fixtures.patched_provider() as provider:
            api = typing.cast(mock.MagicMock, provider.api)
            api.get_task_info.side_effect = self.task_info
            api.restore_snapshot.side_effect = None
            api.restore_snapshot.return_value = SNAPSHOT_TASK
            api.create_snapshot.side_effect = None
            api.create_snapshot.return_value = SNAPSHOT_TASK
            service = fixtures.create_service_linked(provider=provider, **service_values)
            userservice = fixtures.create_userservice_linked(service=service)
            userservice.publication()._vmid = "1"
            userservice._vmid = fixtures.VMS_INFO[0].opaque_ref
            api.reset_mock()
            yield userservice, api

    def run_until_not_running(
        self, userservice: "XenLinkedUserService", state: types.states.TaskState
    ) -> types.states.TaskState:
        for _ in limited_iterator(lambda: state == types.states.TaskState.RUNNING, limit=128):
            state = userservice.check_state()
        return state

    def test_back_to_cache_waits_for_restore_task(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            newest_snapshot = fixtures.VMS_INFO[0]
            api.list_snapshots.side_effect = None
            api.list_snapshots.return_value = [newest_snapshot, fixtures.VMS_INFO[1]]

            self.task_status = xen_types.TaskStatus.PENDING
            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            for _ in range(5):
                state = userservice.check_state()

            api.restore_snapshot.assert_called_once_with(newest_snapshot.opaque_ref)
            self.assertEqual(state, types.states.TaskState.RUNNING)
            self.assertEqual(
                userservice._current_op(), types.services.Operation.BACK_TO_CACHE_SNAPSHOT_RECOVER
            )
            self.assertEqual(userservice._task, SNAPSHOT_TASK)
            api.start_vm.assert_not_called()

            self.task_status = xen_types.TaskStatus.SUCCESS
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.start_vm.assert_called()

    def test_back_to_cache_failed_restore_task_does_not_put_machine_in_error(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            self.task_status = xen_types.TaskStatus.FAILURE
            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.restore_snapshot.assert_called_once()
            api.start_vm.assert_called()

    def test_deploy_for_cache_creates_snapshot_and_waits_for_it(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            api.list_snapshots.side_effect = None
            api.list_snapshots.return_value = []
            userservice._vmid = ""

            state = userservice.deploy_for_cache(level=types.services.CacheLevel.L1)
            for _ in limited_iterator(
                lambda: userservice._current_op() != types.services.Operation.WAIT, limit=128
            ):
                state = userservice.check_state()
            state = userservice.process_ready_from_os_manager(None)

            self.task_status = xen_types.TaskStatus.PENDING
            for _ in range(5):
                state = userservice.check_state()
            self.assertEqual(
                userservice._current_op(), types.services.Operation.BACK_TO_CACHE_SNAPSHOT_CREATE
            )
            self.assertEqual(state, types.states.TaskState.RUNNING)
            api.shutdown_vm.assert_called()

            self.task_status = xen_types.TaskStatus.SUCCESS
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.create_snapshot.assert_called_once_with(userservice._vmid, name="UDS Snapshot")

    def test_back_to_cache_without_snapshot_option_does_not_touch_snapshots(self) -> None:
        with self.setup_data(put_back_to_cache="yes") as (userservice, api):
            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.list_snapshots.assert_not_called()
            api.restore_snapshot.assert_not_called()

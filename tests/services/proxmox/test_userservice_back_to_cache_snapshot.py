
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
"""
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import contextlib
import dataclasses
import typing
from unittest import mock

from uds.core import types
from uds.services.Proxmox.proxmox import exceptions as prox_exceptions
from uds.services.Proxmox.proxmox import types as prox_types

from ...utils.helpers import limited_iterator
from ...utils.test import UDSTransactionTestCase
from . import fixtures

if typing.TYPE_CHECKING:
    from uds.services.Proxmox.deployment_linked import ProxmoxUserserviceLinked

SNAPSHOT_SERVICE_VALUES: typing.Final = {"put_back_to_cache": "snapshot"}
SNAPSHOT_UPID: typing.Final = "snapshot-upid"
TASK_RUNNING: typing.Final = "running"
TASK_FAILED: typing.Final = "snapshot failed"
TASK_UNREACHABLE: typing.Final = "unreachable"
TASK_INFO_BROKEN: typing.Final = "broken"


class TestProxmoxBackToCacheSnapshot(UDSTransactionTestCase):
    snapshot_task_state: str

    def setUp(self) -> None:
        super().setUp()
        fixtures.clear()
        fixtures.set_all_vm_state(prox_types.VMStatus.STOPPED)
        self.snapshot_task_state = "OK"

    def tearDown(self) -> None:
        fixtures.clear()
        super().tearDown()

    def task_info(self, node: str, upid: str, *args: typing.Any, **kwargs: typing.Any) -> prox_types.TaskStatus:
        if upid != SNAPSHOT_UPID or self.snapshot_task_state == "OK":
            return fixtures.TASK_STATUS
        if self.snapshot_task_state == TASK_UNREACHABLE:
            raise prox_exceptions.ProxmoxConnectionError("unreachable")
        if self.snapshot_task_state == TASK_INFO_BROKEN:
            raise prox_exceptions.ProxmoxError("broken task info")
        if self.snapshot_task_state == TASK_RUNNING:
            return dataclasses.replace(fixtures.TASK_STATUS, upid=upid, status=TASK_RUNNING, exitstatus="")
        return dataclasses.replace(fixtures.TASK_STATUS, upid=upid, exitstatus=self.snapshot_task_state)

    @contextlib.contextmanager
    def setup_data(
        self, **service_values: typing.Any
    ) -> typing.Iterator[tuple["ProxmoxUserserviceLinked", mock.MagicMock]]:
        snapshot_task = dataclasses.replace(fixtures.UPID, upid=SNAPSHOT_UPID)
        with fixtures.patched_provider() as provider:
            api = typing.cast(mock.MagicMock, provider.api)
            api.get_task_info.side_effect = self.task_info
            for snapshot_method in (api.create_snapshot, api.restore_snapshot):
                snapshot_method.side_effect = None
                snapshot_method.return_value = snapshot_task
            service = fixtures.create_service_linked(provider=provider, **service_values)
            userservice = fixtures.create_userservice_linked(service=service)
            userservice._vmid = str(fixtures.VMINFO_LIST[0].id)
            api.reset_mock()
            yield userservice, api

    def run_until_not_running(
        self, userservice: "ProxmoxUserserviceLinked", state: types.states.TaskState
    ) -> types.states.TaskState:
        for _ in limited_iterator(lambda: state == types.states.TaskState.RUNNING, limit=128):
            state = userservice.check_state()
        return state

    def test_back_to_cache_waits_for_restore_task(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            self.snapshot_task_state = TASK_RUNNING
            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            for _ in range(5):
                state = userservice.check_state()

            api.restore_snapshot.assert_called_once_with(int(userservice._vmid), name=fixtures.SNAPSHOTS_INFO[0].name)
            self.assertEqual(state, types.states.TaskState.RUNNING)
            self.assertEqual(userservice._current_op(), types.services.Operation.BACK_TO_CACHE_SNAPSHOT_RECOVER)
            api.start_vm.assert_not_called()

            self.snapshot_task_state = "OK"
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.start_vm.assert_called()

    def test_back_to_cache_failed_restore_task_does_not_put_machine_in_error(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            self.snapshot_task_state = TASK_FAILED
            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.restore_snapshot.assert_called_once()
            api.start_vm.assert_called()

    def test_back_to_cache_keeps_waiting_while_proxmox_is_unreachable(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            self.snapshot_task_state = TASK_UNREACHABLE
            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            for _ in range(5):
                state = userservice.check_state()

            self.assertEqual(state, types.states.TaskState.RUNNING)
            self.assertEqual(userservice._current_op(), types.services.Operation.BACK_TO_CACHE_SNAPSHOT_RECOVER)
            api.start_vm.assert_not_called()

    def test_back_to_cache_unreadable_restore_task_does_not_put_machine_in_error(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            self.snapshot_task_state = TASK_INFO_BROKEN
            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.start_vm.assert_called()

    def test_back_to_cache_without_snapshot_does_not_reuse_previous_task(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            self.snapshot_task_state = TASK_RUNNING
            userservice._store_task(dataclasses.replace(fixtures.UPID, upid=SNAPSHOT_UPID))
            fixtures.SNAPSHOTS_INFO.clear()

            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.restore_snapshot.assert_not_called()
            api.start_vm.assert_called()

    def test_deploy_for_cache_creates_snapshot_and_waits_for_it(self) -> None:
        with self.setup_data(**SNAPSHOT_SERVICE_VALUES) as (userservice, api):
            fixtures.SNAPSHOTS_INFO.clear()
            userservice._vmid = ""

            state = userservice.deploy_for_cache(level=types.services.CacheLevel.L1)
            for _ in limited_iterator(lambda: userservice._current_op() != types.services.Operation.WAIT, limit=128):
                state = userservice.check_state()
            state = userservice.process_ready_from_os_manager(None)

            self.snapshot_task_state = TASK_RUNNING
            for _ in range(5):
                state = userservice.check_state()
            self.assertEqual(userservice._current_op(), types.services.Operation.BACK_TO_CACHE_SNAPSHOT_CREATE)
            self.assertEqual(state, types.states.TaskState.RUNNING)

            self.snapshot_task_state = "OK"
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.create_snapshot.assert_called_once_with(int(userservice._vmid), name="UDS_Snapshot")

    def test_back_to_cache_without_snapshot_option_does_not_touch_snapshots(self) -> None:
        with self.setup_data(put_back_to_cache="yes") as (userservice, api):
            state = userservice.move_to_cache(types.services.CacheLevel.L1)
            state = self.run_until_not_running(userservice, state)

            self.assertEqual(state, types.states.TaskState.FINISHED, userservice._error_debug_info)
            api.get_current_vm_snapshot.assert_not_called()
            api.restore_snapshot.assert_not_called()

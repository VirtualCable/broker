# -*- coding: utf-8 -*-

#
# Copyright (c) 2024 Virtual Cable S.L.
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

import typing
from unittest import mock

from uds.core import types
from tests.services.openshift import fixtures
from tests.utils.test import UDSTransactionTestCase


class TestOpenshiftPublication(UDSTransactionTestCase):
    def setUp(self) -> None:
        super().setUp()
        fixtures.clear()
        # Moved from class body to here
        # api.stop_vm_instance will be set in test methods as needed

    def test_op_create_and_checker(self) -> None:
        """
        Test op_create and op_create_checker flow
        """
        with fixtures.patched_provider() as provider:
            api = typing.cast(mock.MagicMock, provider.api)
            service = fixtures.create_service(provider=provider)
            publication = fixtures.create_publication(service=service)

            api.get_vm_pvc_or_dv_name.return_value = ("test-pvc", "pvc")
            api.get_pvc_size.return_value = "10Gi"
            api.create_vm_from_pvc.return_value = True
            api.wait_for_datavolume_clone_progress.return_value = True
            # Return a mock for get_datavolume_phase with is_error()=False and is_succeeded()=True
            dv_phase_mock = mock.Mock()
            dv_phase_mock.is_error.return_value = False
            dv_phase_mock.is_succeeded.return_value = True
            api.get_datavolume_phase.return_value = dv_phase_mock
            # Mock get_vm_info to return a mock object with is_usable method for every call
            vm_info_mock = mock.MagicMock()
            vm_info_mock.is_usable.return_value = True
            api.get_vm_info.side_effect = lambda name: vm_info_mock  # type: ignore

            # Patch get_datavolume_phase to return a mock with is_error and is_ready
            dv_phase_mock = mock.MagicMock()
            dv_phase_mock.is_error.return_value = False
            dv_phase_mock.is_ready.return_value = True
            api.get_datavolume_phase.return_value = dv_phase_mock

            publication.op_create()
            # get_vm_info will return vm_info_mock, so op_create_checker should not fail
            state = publication.op_create_checker()
            self.assertEqual(state, types.states.TaskState.FINISHED)

            # Simulate error in datavolume phase
            dv_phase_mock.is_error.return_value = True
            dv_phase_mock.is_ready.return_value = False
            state = publication.op_create_checker()
            self.assertEqual(state, types.states.TaskState.ERROR)

            # Simulate not ready and not error
            dv_phase_mock.is_error.return_value = False
            dv_phase_mock.is_ready.return_value = False
            vm_info_mock.is_usable.return_value = False
            state = publication.op_create_checker()
            self.assertEqual(state, types.states.TaskState.RUNNING)

    def test_op_create_completed_and_checker(self) -> None:
        """
        Test op_create_completed and op_create_completed_checker flow
        """
        with fixtures.patched_provider() as provider:
            api: mock.MagicMock = typing.cast(mock.MagicMock, provider.api)
            service = fixtures.create_service(provider=provider)
            publication = fixtures.create_publication(service=service)

            # VM running
            running_status = mock.Mock()
            running_status.is_running.return_value = True
            running_vm = mock.Mock()
            running_vm.status = running_status

            def get_vm_info_side_effect(name: str, **kwargs: typing.Any) -> mock.Mock | None:
                return running_vm if name == "test-vm" else None

            api.get_vm_info.side_effect = get_vm_info_side_effect
            publication._name = "test-vm"
            publication.op_create_completed()
            api.stop_vm.assert_called_with("test-vm")

            # VM stopped
            stopped_status = mock.Mock()
            stopped_status.is_running.return_value = False
            stopped_vm = mock.Mock()
            stopped_vm.status = stopped_status

            api.get_vm_info.side_effect = None
            api.get_vm_info.return_value = stopped_vm
            api.stop_vm.reset_mock()
            publication.op_create_completed()
            api.stop_vm.assert_called_with("test-vm")

            # VM not found (get_vm_info returns None)
            api.get_vm_info.return_value = None
            api.stop_vm.reset_mock()
            with self.assertRaises(AttributeError):
                publication.op_create_completed()
            api.stop_vm.assert_not_called()

            # Checker: VM stopped
            api.get_vm_info.return_value = stopped_vm
            state = publication.op_create_completed_checker()
            self.assertEqual(state, types.states.TaskState.FINISHED)

            # Checker: VM not found
            api.get_vm_info.return_value = None
            state = publication.op_create_completed_checker()
            self.assertEqual(state, types.states.TaskState.FINISHED)

    def test_publication_create(self) -> None:
        """
        Test publication creation (publish)
        """
        with fixtures.patched_provider() as provider:
            api = typing.cast(mock.MagicMock, provider.api)
            service = fixtures.create_service(provider=provider)
            publication = fixtures.create_publication(service=service)

            api.get_vm_pvc_or_dv_name.return_value = ("test-pvc", "pvc")
            api.get_pvc_size.return_value = "10Gi"
            api.create_vm_from_pvc.return_value = True
            api.wait_for_datavolume_clone_progress.return_value = True

            # Mock get_datavolume_phase to always return a successful phase
            dv_phase_mock = mock.Mock()
            dv_phase_mock.is_error.return_value = False
            dv_phase_mock.is_succeeded.return_value = True
            api.get_datavolume_phase.return_value = dv_phase_mock

            # Mock get_vm_info to always return a usable VM
            def vm_info_side_effect(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
                vm = mock.Mock()
                vm.status = mock.Mock()
                vm.name = publication._name
                vm.interfaces = [mock.Mock(mac_address="00:11:22:33:44:55")]
                vm.is_usable = mock.Mock(return_value=True)
                vm.is_running = mock.Mock(return_value=False)
                return vm

            api.get_vm_info.side_effect = vm_info_side_effect

            # Set attributes directly on the MagicMock api
            api.get_token = mock.Mock(return_value="dummy-token")
            api.connect = mock.Mock(return_value=mock.Mock(headers={}))
            api.session = mock.Mock(headers={})
            api.get_vm_interfaces = mock.Mock(return_value=[mock.Mock(mac_address="00:11:22:33:44:55")])
            api.do_request = mock.Mock(return_value={"status": {"interfaces": [{"mac_address": "00:11:22:33:44:55"}]}})

            state = publication.publish()
            self.assertEqual(state, types.states.State.RUNNING)

            # Ensure all subsequent check_state calls see a successful state
            for _ in range(10):
                state = publication.check_state()
                if state == types.states.TaskState.FINISHED:
                    break
            self.assertEqual(state, types.states.TaskState.FINISHED)
            self.assertEqual(publication.get_template_id(), publication._name)

    def test_get_template_id(self) -> None:
        """
        Test template ID retrieval (get_template_id)
        """
        service = fixtures.create_service()
        publication = fixtures.create_publication(service=service)
        publication._name = "test-template"
        template_id = publication.get_template_id()
        self.assertEqual(template_id, "test-template")

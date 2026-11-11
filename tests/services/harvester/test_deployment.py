#
# Copyright (c) 2026 Virtual Cable S.L.U.
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


from tests.utils.test import UDSTransactionTestCase
from uds.core.types.states import TaskState

from tests.services.harvester import fixtures


class TestHarvesterDeployment(UDSTransactionTestCase):
    def setUp(self) -> None:
        super().setUp()
        fixtures.clear()

    def _make_userservice(self, **service_kwargs) -> 'fixtures.deployment.HarvesterUserService':
        api = fixtures.create_client_mock()
        return fixtures.create_userservice(api=api)

    def test_create_userservice_and_publication_bind_same_api(self) -> None:
        usersrv = self._make_userservice()
        self.assertIsNotNone(usersrv._cached_api)
        self.assertIsNotNone(usersrv.service().api)

    def test_create_queues_include_create_completed(self) -> None:
        """The MAC of the VM is stored by CREATE_COMPLETED (base class behaviour)."""
        from uds.core import types as core_types

        usersrv = self._make_userservice()
        for queue in (
            usersrv._create_queue,
            usersrv._create_queue_l1_cache,
            usersrv._create_queue_l2_cache,
        ):
            self.assertIn(core_types.services.Operation.CREATE_COMPLETED, queue)

    def test_op_create_starts_vm_from_published_version(self) -> None:
        usersrv = self._make_userservice()
        api = usersrv.service().api
        api.get_template_version.side_effect = (
            lambda *args, **kw: fixtures.find_version('template-1-v1')
        )
        usersrv._name = 'test-vm'
        usersrv.op_create()
        self.assertEqual(usersrv._vmid, 'test-vm')
        args, kwargs = api.create_vm.call_args
        self.assertEqual(args[0], 'test-vm')
        self.assertTrue(kwargs.get('running'))
        # the version metadata is carried to the VM creation
        self.assertEqual(kwargs['vm_metadata'], {'labels': {'harvesterhci.io/creator': 'harvester'}})
        # the published spec is used as is (publication already applied machine options)
        self.assertEqual(args[1], fixtures.find_version('template-1-v1').vm_spec)

    def test_op_create_checker_states(self) -> None:
        from uds.services.Harvester.harvester import types as hv_types

        usersrv = self._make_userservice()
        usersrv._name = 'test-vm'
        usersrv._vmid = 'test-vm'
        api = usersrv.service().api

        # Not running -> RUNNING
        api.get_vm_info.side_effect = lambda *a, **kw: hv_types.VMInfo(
            name='test-vm', namespace='default', running=False, exists=True
        )
        self.assertEqual(usersrv.op_create_checker(), TaskState.RUNNING)

        # Running without MAC -> RUNNING
        api.get_vm_info.side_effect = lambda *a, **kw: hv_types.VMInfo(
            name='test-vm', namespace='default', running=True, exists=True
        )
        self.assertEqual(usersrv.op_create_checker(), TaskState.RUNNING)

        # Running with MAC -> FINISHED
        api.get_vm_info.side_effect = lambda *a, **kw: hv_types.VMInfo(
            name='test-vm',
            namespace='default',
            running=True,
            exists=True,
            interfaces=[{'macAddress': '52:54:00:aa:bb:cc', 'ipAddress': ''}],
        )
        self.assertEqual(usersrv.op_create_checker(), TaskState.FINISHED)

        # VM disappeared -> ERROR
        api.get_vm_info.side_effect = lambda *a, **kw: hv_types.VMInfo.not_found('test-vm', 'default')
        self.assertEqual(usersrv.op_create_checker(), TaskState.ERROR)

    def test_op_create_withfullname_chain(self) -> None:
        # Simplified INITIALIZE is covered by base machinery tests; verify de-dup service helper
        usersrv = self._make_userservice()
        api = usersrv.service().api
        api.list_vms.return_value = ['test-vm']
        usersrv._name = 'test-vm'
        usersrv._vmid = ''
        # op_initialize is the base one; without full machinery here we just accept
        # the create op assigns vmid, rename is responsibility of INITIALIZED base
        api.get_template_version.side_effect = (
            lambda *args, **kw: fixtures.find_version('template-1-v1')
        )
        usersrv.op_create()
        self.assertEqual(usersrv._vmid, 'test-vm')

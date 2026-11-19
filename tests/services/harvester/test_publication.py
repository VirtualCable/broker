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
from uds.services.Harvester.harvester import (
    exceptions as fixtures_exceptions,
)

from tests.services.harvester import fixtures


class TestHarvesterPublication(UDSTransactionTestCase):
    def setUp(self) -> None:
        super().setUp()
        fixtures.clear()

    def _make_publication(self, **service_kwargs) -> fixtures.publication.HarvesterTemplatePublication:
        api = fixtures.create_client_mock()
        return fixtures.create_publication(api=api, **service_kwargs)

    def test_op_create_publishes_version_from_default(self) -> None:
        pub = self._make_publication()
        api = pub.service().api
        pub.op_create()
        # _vmid is the published version name, uds-prefixed
        self.assertTrue(pub._vmid.startswith('uds-'))
        self.assertEqual(pub._vmid, pub._version_name)
        # client was called with the template of the selected default version
        args, kwargs = api.create_template_version.call_args
        self.assertEqual(kwargs.get('template_id') or args[1], 'default/template-1')
        # the published vm spec is the base spec (machine options at zero keep it)
        base = fixtures.find_version('template-1-v1')
        published = pub.service().api.create_template_version.call_args.kwargs['vm_spec']
        self.assertEqual(published, base.vm_spec)

    def test_op_create_applies_machine_options(self) -> None:
        pub = self._make_publication(cores=8, memory=8192)
        pub.op_create()
        kwargs = pub.service().api.create_template_version.call_args.kwargs
        published = kwargs['vm_spec']
        domain = published['template']['spec']['domain']
        self.assertEqual(domain['cpu']['cores'], 8)
        self.assertEqual(domain['resources']['limits']['memory'], '8192Mi')

    def test_op_create_checker_running_then_finished(self) -> None:
        pub = self._make_publication()
        pub.op_create()
        version_name = pub._version_name
        # Not ready -> RUNNING
        pub.service().api.get_template_version.side_effect = None
        from uds.services.Harvester.harvester import types as hv_types

        pub.service().api.get_template_version.return_value = hv_types.TemplateVersion(
            name=version_name, namespace='default', version=2, ready=False
        )
        self.assertEqual(pub.op_create_checker(), TaskState.RUNNING)
        pub.service().api.get_template_version.return_value = hv_types.TemplateVersion(
            name=version_name, namespace='default', version=2, ready=True
        )
        self.assertEqual(pub.op_create_checker(), TaskState.FINISHED)

    def test_op_create_checker_version_disappeared(self) -> None:
        pub = self._make_publication()
        pub.op_create()
        pub.service().api.get_template_version.side_effect = (
            fixtures_exceptions.HarvesterNotFoundError('template version not found')
        )
        self.assertEqual(pub.op_create_checker(), TaskState.ERROR)

    def test_op_delete_removes_version(self) -> None:
        pub = self._make_publication()
        pub.op_create()
        version_name = pub._version_name
        pub.op_delete()
        # version removed from the fixture store
        with self.assertRaises(fixtures_exceptions.HarvesterNotFoundError):
            fixtures.find_version(version_name)

    def test_op_delete_completed_checker_waits_for_removal(self) -> None:
        pub = self._make_publication()
        pub.op_create()
        self.assertEqual(pub.op_delete_completed_checker(), TaskState.RUNNING)
        pub.op_delete()
        self.assertEqual(pub.op_delete_completed_checker(), TaskState.FINISHED)

    def test_destroy_does_not_finish_while_version_exists(self) -> None:
        pub = self._make_publication()
        pub.op_create()
        pub.service().api.delete_template_version.side_effect = None
        pub.destroy()
        for _ in range(3):
            self.assertEqual(pub.check_state(), TaskState.RUNNING)
        fixtures.VERSIONS[:] = [v for v in fixtures.VERSIONS if v.name != pub._version_name]
        self.assertEqual(pub.check_state(), TaskState.FINISHED)

    def test_get_template_id(self) -> None:
        pub = self._make_publication()
        pub.op_create()
        self.assertEqual(pub.get_template_id(), f'default/{pub._version_name}')

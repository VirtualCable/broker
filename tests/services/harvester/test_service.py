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


from tests.utils.test import UDSTestCase
from uds.core import exceptions

from tests.services.harvester import fixtures


class TestHarvesterService(UDSTestCase):
    def setUp(self) -> None:
        super().setUp()
        fixtures.clear()

    def test_init_gui_lists_templates_with_default_version(self) -> None:
        """Templates are offered by default version, skipping our own publications."""
        with fixtures.patched_provider() as prov:
            prov.api.list_templates.return_value = [
                fixtures.TEMPLATES[0].__class__(
                    name='uds-pub-x', namespace='default', default_version_id='default/uds-pub-x-v1'
                )
            ] + list(fixtures.TEMPLATES)
            srv = fixtures.create_service(provider=prov)
            srv.init_gui()
            choices = [c.id for c in srv.template._field_info.choices]
            self.assertNotIn('default/uds-pub-x-v1', choices, 'publication templates leaked to GUI')

    def test_machine_options_from_fields(self) -> None:
        srv = fixtures.create_service(cores=4, memory=4096)
        self.assertEqual(srv.machine_options(), {'cores': 4, 'memory_mb': 4096})

    def test_apply_machine_options_cores_and_memory(self) -> None:
        srv = fixtures.create_service(cores=4, memory=4096)
        spec = {
            'running': False,
            'template': {
                'spec': {
                    'domain': {
                        'cpu': {'cores': 1},
                        'resources': {
                            'requests': {'memory': '1Gi'},
                            'limits': {'cpu': '1', 'memory': '1Gi'},
                        },
                    },
                },
            },
        }
        result = srv.apply_machine_options(spec)
        domain = result['template']['spec']['domain']
        self.assertEqual(domain['cpu']['cores'], 4)
        self.assertNotIn('limits', domain['cpu'])
        self.assertEqual(domain['resources']['limits']['cpu'], '4')
        self.assertEqual(domain['resources']['limits']['memory'], '4096Mi')
        self.assertEqual(domain['resources']['requests']['memory'], '4096Mi')

    def test_apply_machine_options_zero_keeps_template(self) -> None:
        srv = fixtures.create_service(cores=0, memory=0)
        spec = {
            'running': False,
            'template': {
                'spec': {
                    'domain': {
                        'cpu': {'cores': 2},
                        'resources': {
                            'requests': {'memory': '1Gi'},
                            'limits': {'cpu': '2', 'memory': '1Gi'},
                        },
                    },
                },
            },
        }
        result = srv.apply_machine_options(spec)
        domain = result['template']['spec']['domain']
        self.assertEqual(domain['cpu']['cores'], 2)
        self.assertEqual(domain['resources']['limits']['cpu'], '2')
        self.assertEqual(domain['resources']['limits']['memory'], '1Gi')

    def test_get_mac_empty_when_not_started(self) -> None:
        """With an empty vmid, the service returns empty (MAC is not known yet)."""
        with fixtures.patched_provider() as prov:
            srv = fixtures.create_service(provider=prov)
            self.assertEqual(srv.get_mac(None, ''), '')

    def test_get_ip_raises_when_no_interfaces(self) -> None:
        with fixtures.patched_provider() as prov:
            srv = fixtures.create_service(provider=prov)
            with self.assertRaises(exceptions.services.generics.NotFoundError):
                srv.get_ip(None, 'vm-x')

    def test_find_duplicates(self) -> None:
        with fixtures.patched_provider() as prov:
            prov.api.list_vms.return_value = ['vm-a', 'vm-b']
            srv = fixtures.create_service(provider=prov)
            self.assertEqual(list(srv.find_duplicates('vm-a', 'mac')), ['vm-a'])

    def test_lifecycle_delegates_to_api(self) -> None:
        from uds.services.Harvester.harvester import types as hv_types

        with fixtures.patched_provider() as prov:
            srv = fixtures.create_service(provider=prov)
            fixtures.VMS[:] = [hv_types.VMInfo(name='vm-x', namespace='default', running=True)]
            srv.start(None, 'vm-x')
            prov.api.set_vm_running.assert_any_call('vm-x', True)
            srv.stop(None, 'vm-x')
            prov.api.set_vm_running.assert_any_call('vm-x', False)
            srv.shutdown(None, 'vm-x')
            prov.api.set_vm_running.assert_any_call('vm-x', False)
            srv.execute_delete('vm-x')
            prov.api.delete_vm.assert_called_once_with('vm-x')

    def test_is_running_and_is_deleted(self) -> None:
        with fixtures.patched_provider() as prov:
            srv = fixtures.create_service(provider=prov)
            from uds.services.Harvester.harvester import types as hv_types

            fixtures.VMS[:] = [hv_types.VMInfo(name='vm-a', namespace='default', running=True)]
            self.assertTrue(srv.is_running(None, 'vm-a'))
            self.assertTrue(srv.is_deleted('vm-missing'))
            self.assertFalse(srv.is_deleted('vm-a'))

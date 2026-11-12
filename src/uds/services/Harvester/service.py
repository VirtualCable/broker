"""
Author: Andres Schumann, aschumann at virtualcable dot es
"""

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

import collections.abc
import logging
import typing

from django.utils.translation import gettext_lazy as _
from uds.core import exceptions, types
from uds.core.services.generics.dynamic.publication import DynamicPublication
from uds.core.services.generics.dynamic.service import DynamicService
from uds.core.services.generics.dynamic.userservice import DynamicUserService
from uds.core.ui import gui
from uds.core.util import fields, validators

from .deployment import HarvesterUserService
from .publication import HarvesterTemplatePublication

logger: logging.Logger = logging.getLogger(__name__)

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from .harvester.client import HarvesterClient
    from .provider import HarvesterProvider


class HarvesterService(DynamicService):
    type_name = _('VM clone')
    type_type = 'HarvesterVMService'
    type_description = _('This service provides access to cloned VMs on SUSE Virtualization (Harvester)')
    icon_file = 'service.png'

    uses_cache = True  # Cache are running machine awaiting to be assigned
    cache_tooltip = _('Number of desired VMs to keep running waiting for an user')
    uses_cache_l2 = True  # L2 Cache are running machines in suspended state
    cache_tooltip_l2 = _('Number of desired VMs to keep stopped waiting for use')
    needs_osmanager = True  # If the service needs a s.o. manager (managers are related to agents provided by services itselfs, i.e. virtual machines with agent)
    can_reset = True

    must_stop_before_deletion = False

    # Deployments & deploys
    publication_type = HarvesterTemplatePublication
    user_service_type = HarvesterUserService

    services_type_provided = types.services.ServiceType.VDI

    template = gui.ChoiceField(
        order=3,
        label=_('Template'),
        tooltip=_('Harvester template (its default version) to use for the publication'),
        required=True,
    )

    basename = fields.basename_field(order=4)
    lenname = fields.lenname_field(order=5)

    try_soft_shutdown = DynamicService.try_soft_shutdown  # Before deleting, try a soft shutdown
    maintain_on_error = DynamicService.maintain_on_error  # If an error occurs, maintain the service
    put_back_to_cache = fields.put_back_to_cache_field(
        order=105,
        tab=types.ui.Tab.ADVANCED,
        has_snapshot=False,
    )

    cores = gui.NumericField(
        order=20,
        label=_('Cores'),
        length=3,
        default=0,
        min_value=0,
        tab=types.ui.Tab.MACHINE,
        tooltip=_('Number of cores of the new VMs. Zero value means "keep template cores"'),
        required=True,
    )
    memory = gui.NumericField(
        order=21,
        label=_('Memory (Mb)'),
        length=7,
        default=0,
        min_value=0,
        tab=types.ui.Tab.MACHINE,
        tooltip=_('Memory of the new VMs. Zero value means "keep template memory"'),
        required=True,
    )

    prov_uuid = gui.HiddenField(value=None)

    _cached_api: 'HarvesterClient | None' = None

    @property
    def api(self) -> 'HarvesterClient':
        if self._cached_api is None:
            self._cached_api = self.provider().api
        return self._cached_api

    @typing.override
    def initialize(self, values: 'types.core.ValuesType') -> None:
        if not values:
            return
        self.basename.value = validators.validate_basename(self.basename.value, length=self.lenname.as_int())

    @typing.override
    def init_gui(self) -> None:
        """Fill the choices in the GUI."""
        self.prov_uuid.value = self.provider().get_uuid()

        # Templates are grouped by template; we offer the default version of each one
        self.template.set_choices(
            [
                gui.choice_item(template.default_version_id, f'{template.name} ({template.namespace})')
                for template in self.provider().api.list_templates(self.provider().namespace.value)
                if template.is_usable() and not template.name.startswith('uds-')
            ]
        )

    @typing.override
    def provider(self) -> 'HarvesterProvider':
        return typing.cast('HarvesterProvider', super().provider())

    @typing.override
    def get_basename(self) -> str:
        """Returns configured basename for machines"""
        return self.basename.value

    @typing.override
    def get_lenname(self) -> int:
        """Returns configured length for machine names"""
        return self.lenname.as_int()

    # Utility
    @typing.override
    def sanitized_name(self, name: str) -> str:
        """Sanitizes a name for kubernetes (only allowed chars)"""
        return self.provider().sanitized_name(name)

    @typing.override
    def find_duplicates(self, name: str, mac: str) -> collections.abc.Iterable[str]:
        """
        Finds duplicate VMs by name.
        """
        for vm in self.api.list_vms():
            if vm == name:
                yield vm

    @typing.override
    def is_available(self) -> bool:
        """
        Checks if provider is available
        """
        return self.provider().is_available()

    @typing.override
    def get_ip(self, caller_instance: 'DynamicUserService | DynamicPublication | None', vmid: str) -> str:
        """
        Returns the ip of the machine. Raises if not found.
        """
        interfaces = self.api.get_vm_interfaces(vmid)
        if interfaces and interfaces[0].get('ipAddress'):
            return interfaces[0]['ipAddress']
        raise exceptions.services.generics.NotFoundError(f'IP address not found for VM {vmid}')

    @typing.override
    def get_mac(
        self,
        caller_instance: 'DynamicUserService | DynamicPublication | None',
        vmid: str,
        *,
        for_unique_id: bool = False,
    ) -> str:
        """
        Returns the mac of the machine. Empty if not yet known (KubeVirt assigns
        the MAC of the pod network when the VMI starts, so first deploy waits).
        """
        if vmid == '':
            return ''
        interfaces = self.api.get_vm_interfaces(vmid)
        if interfaces and interfaces[0].get('macAddress'):
            return interfaces[0]['macAddress']
        return ''

    @typing.override
    def is_running(self, caller_instance: 'DynamicUserService | DynamicPublication | None', vmid: str) -> bool:
        """
        Checks if the VM instance is currently running.
        """
        return self.api.get_vm_info(vmid).running

    @typing.override
    def start(self, caller_instance: 'DynamicUserService | DynamicPublication | None', vmid: str) -> None:
        self.api.set_vm_running(vmid, True)

    @typing.override
    def stop(self, caller_instance: 'DynamicUserService | DynamicPublication | None', vmid: str) -> None:
        self.api.set_vm_running(vmid, False)

    @typing.override
    def shutdown(self, caller_instance: 'DynamicUserService | DynamicPublication | None', vmid: str) -> None:
        self.api.set_vm_running(vmid, False)

    @typing.override
    def execute_delete(self, vmid: str) -> None:
        logger.debug('Deleting Harvester VM %s', vmid)
        self.api.delete_vm(vmid)

    @typing.override
    def is_deleted(self, vmid: str) -> bool:
        """Checks if the VM is deleted (kubevirt removes the vm object)."""
        logger.debug('Checking if VM %s is deleted', vmid)
        return self.api.get_vm_info(vmid).exists is False

    # cpu/memory overrides for the created VMs, applied over the template version spec
    def machine_options(self) -> dict[str, 'typing.Any']:
        """Returns the machine options (cores, memory) chosen for this service."""
        return {
            'cores': self.cores.as_int(),
            'memory_mb': self.memory.as_int(),
        }

    def apply_machine_options(self, vm_spec: dict[str, typing.Any]) -> dict[str, typing.Any]:
        """
        Applies the service machine options on a kubevirt VM spec (mutates and returns it).
        Zero values keep the template settings (that is the only behaviour used now:
        the publication applies the options, deployments use the published spec).
        """
        template = vm_spec.get('template', {}).get('spec', {}).get('domain', {})
        machine = self.machine_options()
        cpu = template.setdefault('cpu', {})
        resources = template.setdefault('resources', {})
        if machine['cores']:
            cpu['cores'] = machine['cores']
            old = resources.get('limits') or {}
            resources['limits'] = {**old, 'cpu': str(machine['cores'])}
        if machine['memory_mb']:
            memory_str = f"{machine['memory_mb']}Mi"
            old = resources.get('limits') or {}
            resources['limits'] = {**old, 'memory': memory_str}
            resources.setdefault('requests', {})['memory'] = memory_str
        return vm_spec

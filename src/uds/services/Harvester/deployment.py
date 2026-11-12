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

import logging
import typing

from uds.core import exceptions, types
from uds.core.services.generics.dynamic.userservice import DynamicUserService
from uds.core.util import autoserializable

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from .publication import HarvesterTemplatePublication
    from .service import HarvesterService

logger: logging.Logger = logging.getLogger(__name__)


class HarvesterUserService(DynamicUserService, autoserializable.AutoSerializable):
    """
    This class generates the user consumable elements of the service tree.

    Harvester VMs are created from the published template version, and the VM
    object is known by name from the very first moment.
    """

    can_set_ip = False  # We cannot set IP on Harvester (pod network does it)
    can_cache_ip = False  # We cannot cache IP on Harvester

    # Custom queue
    _create_queue: typing.ClassVar[list[types.services.Operation]] = [
        types.services.Operation.INITIALIZE,  # Used in base class to remove duplicates
        types.services.Operation.CREATE,  # Creating already starts the vm
        types.services.Operation.CREATE_COMPLETED,  # Stores the MAC address for the unique_id
        types.services.Operation.FINISH,
    ]
    _create_queue_l1_cache: typing.ClassVar[list[types.services.Operation]] = [
        types.services.Operation.INITIALIZE,
        types.services.Operation.CREATE,
        types.services.Operation.CREATE_COMPLETED,
        types.services.Operation.FINISH,
    ]
    _create_queue_l2_cache: typing.ClassVar[list[types.services.Operation]] = [
        types.services.Operation.INITIALIZE,
        types.services.Operation.CREATE,
        types.services.Operation.CREATE_COMPLETED,
        types.services.Operation.WAIT,
        types.services.Operation.SUSPEND,
        types.services.Operation.FINISH,
    ]

    @typing.override
    def service(self) -> 'HarvesterService':
        """
        Get the Harvester service.
        """
        return typing.cast('HarvesterService', super().service())

    @typing.override
    def publication(self) -> 'HarvesterTemplatePublication':
        """
        Get the Harvester publication.
        """
        pub = super().publication()
        if pub is None:
            raise exceptions.services.generics.NotFoundError('No publication for this element!')
        return typing.cast('HarvesterTemplatePublication', pub)

    @typing.override
    def op_create(self) -> None:
        """
        Creates the VM from the published template version. The publication
        already carries the machine options, so the published spec is used as is
        (keeping the template values for cores/memory when the service was not
        configured with explicit ones).
        """
        api = self.service().api
        published_version = api.get_template_version(
            self.service().provider().namespace.value, self.publication().get_template_id()
        )
        self._name = self.service().sanitized_name(self._name)
        self._vmid = self._name

        api.create_vm(
            self._vmid,
            published_version.vm_spec,
            running=True,
            vm_metadata=published_version.vm_metadata,
        )

    @typing.override
    def op_create_checker(self) -> types.states.TaskState:
        """
        Waits until the VM is running and has a MAC address assigned.
        """
        api = self.service().api
        vm_info = api.get_vm_info(self._vmid)

        if vm_info.exists is False:
            logger.error('VM %s does not exist', self._vmid)
            return types.states.TaskState.ERROR

        if not vm_info.running:
            logger.debug('VM %s not running yet', self._vmid)
            return types.states.TaskState.RUNNING

        if not vm_info.first_mac:
            logger.debug('VM %s has no MAC address yet', self._vmid)
            return types.states.TaskState.RUNNING

        logger.debug('VM %s running (mac %s, ip %s)', self._vmid, vm_info.first_mac, vm_info.first_ip)
        return types.states.TaskState.FINISHED

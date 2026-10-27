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

import logging
import typing

from uds.core import types

from .deployment import OpenshiftUserService
from .openshift import exceptions as oshift_exceptions
from .openshift import types as oshift_types

if typing.TYPE_CHECKING:
    from .service_linked import OpenshiftLinkedService

logger: logging.Logger = logging.getLogger(__name__)

# printableStatus values a new VM does not recover from without someone fixing the cluster
FAILED_VM_STATES: typing.Final[frozenset[oshift_types.State]] = frozenset(
    {
        oshift_types.State.ERROR,
        oshift_types.State.FAILED,
        oshift_types.State.CRASH_LOOP_BACK_OFF,
        oshift_types.State.DENIED,
        oshift_types.State.ERROR_UNSCHEDULABLE,
        oshift_types.State.ERROR_PVC_NOT_FOUND,
        oshift_types.State.DATA_VOLUME_ERROR,
        oshift_types.State.ERR_IMAGE_PULL,
        oshift_types.State.IMAGE_PULL_BACK_OFF,
        oshift_types.State.ERROR_VM_STATE_IN_USE,
    }
)


class OpenshiftLinkedUserService(OpenshiftUserService):
    """
    User service of linked clones. Persistent ones are cloned like the VM clone service;
    non persistent ones run over the publication disk, so there is no DataVolume to wait for.
    """

    @typing.override
    def service(self) -> "OpenshiftLinkedService":
        return typing.cast("OpenshiftLinkedService", super().service())

    @typing.override
    def op_create(self) -> None:
        if self.service().is_persistent():
            super().op_create()
            return

        self._waiting_name = True
        api = self.service().api
        publication_vm_name = self.publication()._name
        self._name = self.service().sanitized_name(self._name)

        source_pvc_name, _vol_type = api.get_vm_pvc_or_dv_name(publication_vm_name)
        api.create_ephemeral_vm(
            source_vm_name=publication_vm_name,
            new_vm_name=self._name,
            source_pvc_name=source_pvc_name,
            options=self.service().machine_options(),
        )

    @typing.override
    def op_create_checker(self) -> types.states.TaskState:
        if self.service().is_persistent():
            return super().op_create_checker()

        api = self.service().api
        interfaces = api.get_vm_interfaces(self._name)
        if not interfaces or not interfaces[0].mac_address:
            try:
                status = api.get_vm_info(self._name).status
            except oshift_exceptions.OpenshiftNotFoundError:
                return types.states.TaskState.RUNNING  # Not visible yet right after the POST
            if status in FAILED_VM_STATES:
                return self.error(f"VM {self._name} failed to start: {status}")
            return types.states.TaskState.RUNNING

        self._vmid = self._name
        self._waiting_name = False
        return types.states.TaskState.FINISHED

"""
Author: Andres Schumann, aschumann at virtualcable dot es
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import copy
import logging
import typing

from uds.core import types
from uds.core.services.generics.dynamic.publication import DynamicPublication
from uds.core.util import autoserializable

from .harvester import exceptions as hv_exceptions

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from .service import HarvesterService

logger: logging.Logger = logging.getLogger(__name__)


class HarvesterTemplatePublication(DynamicPublication, autoserializable.AutoSerializable):
    """
    Publication for the Harvester dynamic service.

    The publication creates a new template version on Harvester containing the
    base template VM spec with the service machine options applied. User VMs are
    created from that published version, so removing the version removes the
    publication.
    """

    # Not applying publication wait: template versions are a fast, atomic object

    _version_name = autoserializable.StringField(default='')

    def service(self) -> 'HarvesterService':
        """
        Get the Harvester service.
        """
        return typing.cast('HarvesterService', super().service())

    @typing.override
    def op_create(self) -> None:
        """
        Creates the published template version.
        """
        api = self.service().api
        base_version_ref = self.service().template.value

        base_version = api.get_template_version(self.service().provider().namespace.value, base_version_ref)
        # Apply machine options over the base spec (don't mutate the base response)
        vm_spec = copy.deepcopy(base_version.vm_spec)
        vm_spec = self.service().apply_machine_options(vm_spec)

        version = api.create_template_version(
            namespace=self.service().provider().namespace.value,
            template_id=base_version.template_id,
            description=f'uds-pub {self._name}',
            vm_spec=vm_spec,
        )
        self._version_name = version.name
        self._vmid = version.name

    @typing.override
    def op_create_checker(self) -> types.states.TaskState:
        """
        Waits until the version is ready (ready condition true).
        """
        api = self.service().api
        try:
            version = api.get_template_version(self.service().provider().namespace.value, self._version_name)
        except hv_exceptions.HarvesterNotFoundError:
            self._error(f'Published version {self._version_name} disappeared.')
            return types.states.TaskState.ERROR

        if version.ready is True:
            logger.debug('Published version %s ready (number %s)', self._version_name, version.version)
            return types.states.TaskState.FINISHED

        logger.debug('Waiting for version %s to be ready', self._version_name)
        return types.states.TaskState.RUNNING

    @typing.override
    def op_delete(self) -> None:
        """
        Removes the published template version.
        """
        self.service().api.delete_template_version(
            self.service().provider().namespace.value, self._version_name
        )

    @typing.override
    def op_delete_completed_checker(self) -> types.states.TaskState:
        try:
            self.service().api.get_template_version(
                self.service().provider().namespace.value, self._version_name
            )
        except hv_exceptions.HarvesterNotFoundError:
            return types.states.TaskState.FINISHED
        return types.states.TaskState.RUNNING

    # Here ends the publication needed methods.
    # Methods provided below are specific for this publication

    @typing.override
    def get_template_id(self) -> str:
        """
        Returns the published template version reference (used by deployments).
        """
        return f'{self.service().provider().namespace.value}/{self._version_name}'

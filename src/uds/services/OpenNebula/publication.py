#
# Copyright (c) 2012-2019 Virtual Cable S.L.
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
Author: Andres Schumann, aschumann at virtualcable dot es
"""

import logging
import typing

from uds.core import types
from uds.core.services.generics.dynamic.publication import DynamicPublication

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from .service import OpenNebulaLiveService

logger: logging.Logger = logging.getLogger(__name__)


class OpenNebulaLivePublication(DynamicPublication):
    """
    This class provides the publication of a service for OpenNebula

    The publication is a template cloned from the base one, its id is kept as the "vmid" of the publication
    """

    suggested_delay = 2  # : Suggested recheck time if publication is unfinished in seconds

    @typing.override
    def service(self) -> "OpenNebulaLiveService":
        return typing.cast("OpenNebulaLiveService", super().service())

    @typing.override
    def op_create(self) -> None:
        """
        Realizes the publication of the service
        """
        self._vmid = self.service().make_template(self._name)

    @typing.override
    def op_create_checker(self) -> types.states.TaskState:
        """
        Checks state of publication creation
        """
        if self.service().check_template_published(self._vmid) is False:
            return types.states.TaskState.RUNNING

        return types.states.TaskState.FINISHED

    @typing.override
    def get_template_id(self) -> str:
        """
        Returns the template id associated with the publication
        """
        return self._vmid

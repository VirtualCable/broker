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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import enum
import logging
import pickle  # nosec: not insecure, we are loading our own data
import typing

from uds.core import consts, types
from uds.core.services.generics.dynamic.userservice import DynamicUserService
from uds.core.util import autoserializable

from . import on

# Not imported at runtime, just for type checking
if typing.TYPE_CHECKING:
    from .publication import OpenNebulaLivePublication
    from .service import OpenNebulaLiveService

logger: logging.Logger = logging.getLogger(__name__)


class OldOperation(enum.IntEnum):
    """
    Operation values used by the 4.0 OpenNebula deployment queue
    """

    CREATE = 0
    START = 1
    SHUTDOWN = 2
    REMOVE = 3
    WAIT = 4
    ERROR = 5
    FINISH = 6
    RETRY = 7

    UNKNOWN = 99

    @staticmethod
    def from_int(value: int) -> "OldOperation":
        try:
            return OldOperation(value)
        except ValueError:
            return OldOperation.UNKNOWN

    def to_operation(self) -> types.services.Operation:
        return {
            OldOperation.CREATE: types.services.Operation.CREATE,
            OldOperation.START: types.services.Operation.START,
            OldOperation.SHUTDOWN: types.services.Operation.SHUTDOWN,
            OldOperation.REMOVE: types.services.Operation.DELETE,
            OldOperation.WAIT: types.services.Operation.WAIT,
            OldOperation.ERROR: types.services.Operation.ERROR,
            OldOperation.FINISH: types.services.Operation.FINISH,
            OldOperation.RETRY: types.services.Operation.RETRY,
        }.get(self, types.services.Operation.UNKNOWN)


class OldDeploymentData(autoserializable.AutoSerializable):
    """
    Reads the queue as stored by the 4.0 deployment, before casting its values to OldOperation
    """

    _queue = autoserializable.ListField[int]()


class OpenNebulaLiveDeployment(DynamicUserService):
    """
    This class generates the user consumable elements of the service tree.

    The logic for managing OpenNebula deployments (user machines in this case) is here.
    """

    suggested_delay = 6

    # 4.0 stored the queue with OldOperation values under version 0
    serialization_version = 1

    # OpenNebula removes the machines whatever their power state is, so there is no need to stop them first
    _destroy_queue: typing.ClassVar[list[types.services.Operation]] = [
        types.services.Operation.DELETE,
        types.services.Operation.DELETE_COMPLETED,
        types.services.Operation.FINISH,
    ]

    # For typing check only...
    @typing.override
    def service(self) -> "OpenNebulaLiveService":
        return typing.cast("OpenNebulaLiveService", super().service())

    # For typing check only...
    @typing.override
    def publication(self) -> "OpenNebulaLivePublication":
        return typing.cast("OpenNebulaLivePublication", super().publication())

    @typing.override
    def unmarshal(self, data: bytes) -> None:
        if autoserializable.is_autoserializable_data(data):
            super().unmarshal(data)
            if self._serialization_version == 0:
                self._unmarshal_old_queue(data)
            return

        vals = data.split(b"\1")
        if vals[0] == b"v1":
            self._name = vals[1].decode("utf8")
            self._ip = vals[2].decode("utf8")
            self._mac = vals[3].decode("utf8")
            self._vmid = vals[4].decode("utf8")
            self._reason = vals[5].decode("utf8")
            self._queue = [OldOperation.from_int(i).to_operation() for i in pickle.loads(vals[6])]  # nosec

        self.mark_for_upgrade()  # Flag so manager can save it again with new format

    def _unmarshal_old_queue(self, data: bytes) -> None:
        old_data = OldDeploymentData()
        old_data.unmarshal(data)
        self._queue = [OldOperation.from_int(i).to_operation() for i in old_data._queue]
        self.mark_for_upgrade()

    @typing.override
    def get_vmname(self) -> str:
        name = self.get_name()
        if name == consts.NO_MORE_NAMES:
            raise Exception(
                "No more names available for this service. (Increase digits for this service to fix)"
            )

        return self.service().sanitized_name(name)

    @typing.override
    def get_console_connection(self) -> types.services.ConsoleConnectionInfo | None:
        return self.service().get_console_connection(self._vmid)

    def desktop_login(
        self, username: str, password: str, domain: str = ""
    ) -> types.services.ConsoleConnectionInfo | None:
        return self.service().desktop_login(self._vmid, username, password, domain)

    def _check_machine_exists(self) -> None:
        state = self.service().get_machine_state(self._vmid)
        # If we want to check an state and machine does not exists, it is an error
        if state in (on.types.VmState.UNKNOWN, on.types.VmState.DONE):
            raise Exception("Machine not found")

    @typing.override
    def op_create(self) -> None:
        """
        Deploys a machine from template for user/cache
        """
        template_id = self.publication().get_template_id()
        name = self.get_vmname()

        self._vmid = self.service().deploy_from_template(name, template_id)
        if not self._vmid:
            raise Exception("Can't create machine")

    @typing.override
    def op_create_checker(self) -> types.states.TaskState:
        """
        Checks the state of a deploy for an user or cache
        """
        self._check_machine_exists()
        return (
            types.states.TaskState.FINISHED
            if self.service().is_running(self, self._vmid)
            else types.states.TaskState.RUNNING
        )

    @typing.override
    def op_start_checker(self) -> types.states.TaskState:
        """
        Checks if machine has started
        """
        self._check_machine_exists()
        return super().op_start_checker()

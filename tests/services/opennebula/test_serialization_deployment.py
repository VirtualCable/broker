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
#    * Neither the name of Virtual Cable S.L.U. nor the names of its contributors
#      may be used to endorse or promote products derived from this software
#      without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
# ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""
import pickle

from uds.core import types
from uds.core.environment import Environment
from uds.core.util import autoserializable

from uds.services.OpenNebula.deployment import OldOperation, OpenNebulaLiveDeployment

from ...utils.test import UDSTestCase

Operation = types.services.Operation

V1_QUEUE = [OldOperation.CREATE, OldOperation.REMOVE, OldOperation.RETRY]
V1_DATA = b"v1\x01name\x01ip\x01mac\x01vmid\x01reason\x01" + pickle.dumps(V1_QUEUE, protocol=0)


class Deployment40(autoserializable.AutoSerializable):
    """
    Same fields and version as the 4.0 deployment, used to produce its serialized bytes
    """

    _name = autoserializable.StringField(default="")
    _ip = autoserializable.StringField(default="")
    _mac = autoserializable.StringField(default="")
    _vmid = autoserializable.StringField(default="")
    _reason = autoserializable.StringField(default="")
    _queue = autoserializable.ListField[int]()


def serialized_by_40(queue: list[int]) -> bytes:
    old = Deployment40()
    old._name = "name"
    old._ip = "ip"
    old._mac = "mac"
    old._vmid = "vmid"
    old._reason = "reason"
    old._queue = queue
    return old.marshal()


class OpenNebulaDeploymentSerializationTest(UDSTestCase):
    def deployment_from(self, data: bytes) -> OpenNebulaLiveDeployment:
        instance = OpenNebulaLiveDeployment(environment=Environment.testing_environment(), service=None)  # type: ignore
        instance.unmarshal(data)
        return instance

    def assert_fields_restored(self, instance: OpenNebulaLiveDeployment) -> None:
        self.assertEqual(instance._name, "name")
        self.assertEqual(instance._ip, "ip")
        self.assertEqual(instance._mac, "mac")
        self.assertEqual(instance._vmid, "vmid")
        self.assertEqual(instance._reason, "reason")

    def test_unmarshal_v1_translates_queue(self) -> None:
        instance = self.deployment_from(V1_DATA)

        self.assert_fields_restored(instance)
        self.assertEqual(instance._queue, [Operation.CREATE, Operation.DELETE, Operation.RETRY])
        self.assertTrue(instance.needs_upgrade())

    def test_unmarshal_40_data_translates_queue(self) -> None:
        instance = self.deployment_from(serialized_by_40([int(op) for op in V1_QUEUE]))

        self.assert_fields_restored(instance)
        self.assertEqual(instance._queue, [Operation.CREATE, Operation.DELETE, Operation.RETRY])
        self.assertTrue(instance.needs_upgrade())

    def test_unmarshal_40_data_maps_unknown_values(self) -> None:
        instance = self.deployment_from(serialized_by_40([42, int(OldOperation.FINISH)]))

        self.assertEqual(instance._queue, [Operation.UNKNOWN, Operation.FINISH])

    def test_remarshal_uses_current_version(self) -> None:
        instance = self.deployment_from(V1_DATA)
        instance.mark_for_upgrade(False)

        data = instance.marshal()
        reloaded = self.deployment_from(data)

        self.assertEqual(reloaded._serialization_version, 1)
        self.assertEqual(reloaded._queue, instance._queue)
        self.assert_fields_restored(reloaded)
        self.assertFalse(reloaded.needs_upgrade())

    def test_old_operation_mapping(self) -> None:
        expected: dict[OldOperation, Operation] = {
            OldOperation.CREATE: Operation.CREATE,
            OldOperation.START: Operation.START,
            OldOperation.SHUTDOWN: Operation.SHUTDOWN,
            OldOperation.REMOVE: Operation.DELETE,
            OldOperation.WAIT: Operation.WAIT,
            OldOperation.ERROR: Operation.ERROR,
            OldOperation.FINISH: Operation.FINISH,
            OldOperation.RETRY: Operation.RETRY,
            OldOperation.UNKNOWN: Operation.UNKNOWN,
        }
        for old, new in expected.items():
            with self.subTest(old=old):
                self.assertEqual(old.to_operation(), new)
        self.assertEqual(OldOperation.from_int(1234), OldOperation.UNKNOWN)

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
from uds.core import types
from uds.core.environment import Environment
from uds.core.util import autoserializable

from uds.services.OpenNebula.publication import OpenNebulaLivePublication

from ...utils.test import UDSTestCase

Operation = types.services.Operation


class Publication40(autoserializable.AutoSerializable):
    """
    Same fields and version as the 4.0 publication, used to produce its serialized bytes
    """

    _name = autoserializable.StringField(default="")
    _reason = autoserializable.StringField(default="")
    _template_id = autoserializable.StringField(default="")
    _state = autoserializable.StringField(default="r")
    _destroy_after = autoserializable.BoolField(default=False)


def serialized_by_40(state: str, destroy_after: bool = False) -> bytes:
    old = Publication40()
    old._name = "name"
    old._reason = "reason"
    old._template_id = "template_id"
    old._state = state
    old._destroy_after = destroy_after
    return old.marshal()


class OpenNebulaPublicationSerializationTest(UDSTestCase):
    def publication_from(self, data: bytes) -> OpenNebulaLivePublication:
        instance = OpenNebulaLivePublication(environment=Environment.testing_environment(), service=None)  # type: ignore
        instance.unmarshal(data)
        return instance

    def assert_fields_restored(self, instance: OpenNebulaLivePublication) -> None:
        self.assertEqual(instance._name, "name")
        self.assertEqual(instance._reason, "reason")
        self.assertEqual(instance._vmid, "template_id")
        self.assertEqual(instance.get_template_id(), "template_id")

    def test_unmarshal_v1_keeps_template_as_vmid(self) -> None:
        instance = self.publication_from(b"v1\tname\treason\ttemplate_id\tok")

        self.assert_fields_restored(instance)
        self.assertEqual(instance._queue, [Operation.FINISH])
        self.assertFalse(instance._is_flagged_for_destroy)
        self.assertTrue(instance.needs_upgrade())

    def test_unmarshal_40_data_maps_state_to_queue(self) -> None:
        expected = {
            "ok": [Operation.FINISH],
            "error": [Operation.ERROR],
            "running": [Operation.CREATE, Operation.FINISH],
        }
        for state, queue in expected.items():
            with self.subTest(state=state):
                instance = self.publication_from(serialized_by_40(state))

                self.assert_fields_restored(instance)
                self.assertEqual(instance._queue, queue)
                self.assertTrue(instance.needs_upgrade())

    def test_unmarshal_40_data_keeps_destroy_flag(self) -> None:
        instance = self.publication_from(serialized_by_40("ok", destroy_after=True))

        self.assertTrue(instance._is_flagged_for_destroy)

    def test_remarshal_uses_current_version(self) -> None:
        instance = self.publication_from(serialized_by_40("ok", destroy_after=True))
        instance.mark_for_upgrade(False)

        reloaded = self.publication_from(instance.marshal())

        self.assertEqual(reloaded._serialization_version, 1)
        self.assert_fields_restored(reloaded)
        self.assertEqual(reloaded._queue, [Operation.FINISH])
        self.assertTrue(reloaded._is_flagged_for_destroy)
        self.assertFalse(reloaded.needs_upgrade())

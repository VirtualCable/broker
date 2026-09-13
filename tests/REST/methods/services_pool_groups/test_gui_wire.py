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
``src/uds/REST/methods/services_pool_groups.py`` handler contract.

Freezes the ``/gallery/servicespoolgroups/gui`` payload the admin form
renders after the mcp-gui-coupling overlay: the image foreign key is hidden
from the *agent* surface (an agent cannot point at uploaded binaries it
cannot discover) but stays on the human form exactly as before, and the
overlay never leaks into the serialized element.

Unlike the cosmetic ``comments`` of networks, ``image_id`` *is* a handler
save field: the PUT does persist it. The hidden annotation documents what
the agent cannot provide, not what the PUT drops -- pinned here so the two
never silently converge into a wrong assumption.

Reference: src/uds/REST/methods/services_pool_groups.py
           tests/mcp/mutability/test_types_pool_groups.py (the agent
           surface side of the same annotation)
"""

import typing

from uds.core import types
from uds.REST.methods.services_pool_groups import ServicesPoolGroups

from ....utils import rest

_TEST_FOR_TYPE: typing.Final[str] = "service_pool_group"


class PoolGroupsGuiWireTest(rest.test.RESTTestCase):
    """The REST gui payload is untouched by the mutability overlay."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _element(self, name: str) -> dict[str, typing.Any]:
        """One gui element payload: ``{"name", "gui", "value"}`` (no overlay slot)."""
        response = self.client.rest_get(f"gallery/servicespoolgroups/gui/{_TEST_FOR_TYPE}")
        self.assertEqual(response.status_code, 200, response.content)
        elements = {element["name"]: element for element in response.json()}
        self.assertIn(name, elements, f"missing gui element {name}")
        return elements[name]

    def test_image_id_still_reaches_the_admin_form(self) -> None:
        self.assertEqual(self._element("image_id")["gui"]["type"], types.ui.FieldType.IMAGECHOICE.value)

    def test_wire_payload_has_no_overlay_leak(self) -> None:
        """GuiElement.as_dict() must not serialize the overlay slot."""
        for name in ("image_id", "priority"):
            self.assertEqual(sorted(self._element(name)), ["gui", "name", "value"])


class PoolGroupsFieldsToSaveTest(rest.test.RESTTestCase):
    """The premise of the hidden annotation: image_id IS persisted."""

    def test_image_id_is_a_save_field(self) -> None:
        names = [
            name for name, _modifier in ServicesPoolGroups.parse_save_fields(ServicesPoolGroups.FIELDS_TO_SAVE)
        ]
        self.assertEqual(sorted(names), ["comments", "image_id", "name", "priority"])

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
``src/uds/REST/methods/tunnels_management.py`` handler contract.

Freezes the ``/tunnels/gui`` payload the admin form renders: the gui
fields are exactly the handler's saved columns (``tags`` travels in the
PUT, like every other model here), and no mutability overlay is in play,
so the serialized element stays the plain ``{"name", "gui", "value"}``
the human form consumes.

Reference: src/uds/REST/methods/tunnels_management.py
           tests/mcp/mutability/test_types_tunnels.py (the agent surface
           side of the same derivation)
"""

import typing

from uds.core import types
from uds.REST.methods.tunnels_management import Tunnels

from ....utils import rest


class TunnelsGuiWireTest(rest.test.RESTTestCase):
    """The REST gui payload is untouched by the mutability overlay."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _element(self, name: str) -> dict[str, typing.Any]:
        """One gui element payload: ``{"name", "gui", "value"}`` (no overlay slot)."""
        response = self.client.rest_get("tunnels/tunnels/gui")
        self.assertEqual(response.status_code, 200, response.content)
        elements = {element["name"]: element for element in response.json()}
        self.assertIn(name, elements, f"missing gui element {name}")
        return elements[name]

    def test_tags_still_reaches_the_admin_form(self) -> None:
        self.assertEqual(self._element("tags")["gui"]["type"], types.ui.FieldType.TAGLIST.value)

    def test_wire_payload_has_no_overlay_leak(self) -> None:
        """GuiElement.as_dict() must not serialize the overlay slot."""
        for name in ("tags", "host", "port"):
            self.assertEqual(sorted(self._element(name)), ["gui", "name", "value"])


class TunnelsFieldsToSaveTest(rest.test.RESTTestCase):
    """The premise of the gui derivation: the PUT saves tags."""

    def test_tags_is_a_save_field(self) -> None:
        names = [name for name, _modifier in Tunnels.parse_save_fields(Tunnels.FIELDS_TO_SAVE)]
        self.assertEqual(sorted(names), ["comments", "host", "name", "port", "tags"])

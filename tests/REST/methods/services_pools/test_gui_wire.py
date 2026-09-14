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
``src/uds/REST/methods/services_pools.py`` handler contract.

Freezes the ``/services_pools/gui`` payload the admin form renders after the
mcp-gui-coupling overlays: the image and pool-group pickers stay on the
human form exactly as before (the PUT does persist them, travelling as
immutable context) while the hidden annotations remove them only from the
*agent* surface. The overlays must never leak into the serialized element.

Reference: src/uds/REST/methods/services_pools.py
           tests/mcp/mutability/test_types_service_pools.py (the agent
           surface side of the same annotations)
"""

import typing

from uds.core import types
from uds.REST.methods.services_pools import ServicesPools

from ....fixtures.services import create_db_provider, create_db_service
from ....utils import rest


class ServicesPoolsGuiWireTest(rest.test.RESTTestCase):
    """The REST gui payload is untouched by the mutability overlays."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        # The pools gui refuses to build without at least one service
        create_db_service(create_db_provider())

    def _element(self, name: str) -> dict[str, typing.Any]:
        """One gui element payload: ``{"name", "gui", "value"}`` (no overlay slot)."""
        response = self.client.rest_get("servicespools/gui")
        self.assertEqual(response.status_code, 200, response.content)
        elements = {element["name"]: element for element in response.json()}
        self.assertIn(name, elements, f"missing gui element {name}")
        return elements[name]

    def test_hidden_choices_still_reach_the_admin_form(self) -> None:
        self.assertEqual(self._element("image_id")["gui"]["type"], types.ui.FieldType.IMAGECHOICE.value)
        self.assertEqual(self._element("pool_group_id")["gui"]["type"], types.ui.FieldType.IMAGECHOICE.value)

    def test_wire_payload_has_no_overlay_leak(self) -> None:
        """GuiElement.as_dict() must not serialize the overlay slot."""
        for name in ("image_id", "pool_group_id", "publish_on_save", "visible"):
            self.assertEqual(sorted(self._element(name)), ["gui", "name", "value"])


class ServicesPoolsFieldsToSaveTest(rest.test.RESTTestCase):
    """The premise of the hidden annotations: image/pool_group ARE saved."""

    def test_hidden_fields_are_save_fields(self) -> None:
        names = {name for name, _modifier in ServicesPools.parse_save_fields(ServicesPools.FIELDS_TO_SAVE)}
        for name in ("image_id", "pool_group_id"):
            self.assertIn(name, names)

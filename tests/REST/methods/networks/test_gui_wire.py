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
``src/uds/REST/methods/networks.py`` handler contract.

The network model (``UUIDModel`` + tags) has no comments column, so the
form must not offer the stock comments field: the admin gui would render
an input the PUT silently drops. The gui fields and the handler
``FIELDS_TO_SAVE`` must stay in lockstep -- the mutability surface derives
from both (see tests/mcp/mutability/test_types_networks.py) -- and this
pins the wire payload the admin form feeds on.

Reference: src/uds/REST/methods/networks.py
"""

import typing

from uds.REST.methods.networks import Networks

from ....utils import rest

# The /gui route passes the for_type through; Networks.get_gui ignores it
# (single type), so any value returns the same form.
_TEST_FOR_TYPE: typing.Final[str] = "network"


class NetworksGuiWireTest(rest.test.RESTTestCase):
    """The /networks/gui payload matches what the handler can actually save."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _element_names(self) -> list[str]:
        response = self.client.rest_get(f"networks/gui/{_TEST_FOR_TYPE}")
        self.assertEqual(response.status_code, 200, response.content)
        return [element["name"] for element in response.json()]

    def test_comments_is_not_on_the_form(self) -> None:
        # The model has no comments column: offering it would be a dead input
        self.assertNotIn("comments", self._element_names())

    def test_form_fields_follow_the_builder(self) -> None:
        self.assertEqual(self._element_names(), ["name", "tags", "net_string"])

    def test_wire_elements_have_no_overlay_leak(self) -> None:
        """GuiElement.as_dict() must not serialize the overlay slot."""
        response = self.client.rest_get(f"networks/gui/{_TEST_FOR_TYPE}")
        self.assertEqual(response.status_code, 200, response.content)
        for element in response.json():
            self.assertEqual(sorted(element), ["gui", "name", "value"])


class NetworksFieldsToSaveTest(rest.test.RESTTestCase):
    """Gui fields == save fields: the two surfaces cannot drift."""

    def test_save_fields_match_the_form(self) -> None:
        saved = sorted(name for name, _modifier in Networks.parse_save_fields(Networks.FIELDS_TO_SAVE))
        form = [element.name for element in Networks.get_gui(typing.cast(typing.Any, None), _TEST_FOR_TYPE)]
        self.assertEqual(saved, sorted(form))

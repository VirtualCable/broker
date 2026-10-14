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
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
``src/uds/REST/methods/transports.py`` field order on the wire.

``GuiBuilder.add_fields`` (src/uds/core/util/ui.py:178) assigns every
element a fresh ``gui.order`` from its own counter, so the ``order=``
declared on the field never reaches the admin: the form follows the
order in which the fields are declared, and the admin builds the tabs in
that same order (Main first, Advanced last, the rest as their first field
appears). The tunnel fields have to be declared first, as the other
tunneled transports do, or the Tunnel tab lands behind every other tab.

Reference: src/uds/REST/methods/transports.py
           src/uds/core/util/ui.py

Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import typing

from ....utils import rest

TUNNELED_EMBEDDED_RDP: typing.Final[str] = "TRDPEmbeddedTransport"
TUNNELED_RDP: typing.Final[str] = "TSRDPTransport"

# Stock fields the builder prepends to every transport form
STOCK_FIELDS: typing.Final[list[str]] = [
    "name",
    "comments",
    "tags",
    "priority",
    "networks",
    "net_filtering",
]

# First field the builder appends after the transport's own ones
FIRST_APPENDED_FIELD: typing.Final[str] = "pools"


class TransportsGuiOrderTest(rest.test.RESTTestCase):
    """The /transports/gui payload is what the admin renders, in that order."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _elements(self, for_type: str) -> list[dict[str, typing.Any]]:
        response = self.client.rest_get(f"transports/gui/{for_type}")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _names(self, for_type: str) -> list[str]:
        return [element["name"] for element in self._elements(for_type)]

    def test_the_builder_renumbers_every_field(self) -> None:
        """The declared `order` cannot survive the builder counter, hence declaration order."""
        for index, element in enumerate(self._elements(TUNNELED_EMBEDDED_RDP)):
            if element["name"] == FIRST_APPENDED_FIELD:
                break
            self.assertEqual(element["gui"]["order"], index, element["name"])

    def test_the_tunnel_fields_come_right_after_the_stock_fields(self) -> None:
        names = self._names(TUNNELED_EMBEDDED_RDP)
        self.assertEqual(names[: len(STOCK_FIELDS)], STOCK_FIELDS)
        self.assertEqual(names[len(STOCK_FIELDS) : len(STOCK_FIELDS) + 2], ["tunnel", "startup_time"])

    def test_the_tunnel_fields_land_where_the_tunneled_rdp_transport_puts_them(self) -> None:
        reference = self._names(TUNNELED_RDP)
        embedded = self._names(TUNNELED_EMBEDDED_RDP)
        self.assertEqual(
            [reference.index("tunnel"), reference.index("startup_time")],
            [embedded.index("tunnel"), embedded.index("startup_time")],
        )

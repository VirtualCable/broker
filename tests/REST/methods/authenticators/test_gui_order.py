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
# AND EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
# IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
# INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
# BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
# DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
# LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
# OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF
# ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
"""
Field order of the authenticator form.

4.0 built this form with fixed order values (add_default_fields), and name came
before priority. In 5.0 the GuiBuilder rewrites the order of every stock field
with a counter, so the call order is the on screen order.

Author: Andrés Schumann, aschumann at virtualcable dot net
"""

import logging
import typing

from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)

TEST_AUTH_TYPE: typing.Final[str] = "InternalDBAuth"


class AuthenticatorsGuiOrderTest(rest.test.RESTTestCase):
    """Freezes the position of the stock fields of the authenticator form."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _field_names(self) -> list[str]:
        response = self.client.rest_get(f"authenticators/gui/{TEST_AUTH_TYPE}")
        self.assertEqual(response.status_code, 200, response.content)
        return [field["name"] for field in response.json()]

    def test_name_is_the_first_field(self) -> None:
        self.assertEqual(self._field_names()[0], "name")

    def test_priority_comes_after_name(self) -> None:
        names = self._field_names()
        self.assertLess(names.index("name"), names.index("priority"))

    def test_stock_fields_keep_their_relative_order(self) -> None:
        names = self._field_names()
        stock = [name for name in names if name in ("name", "small_name", "priority", "comments", "tags")]
        self.assertEqual(stock, ["name", "small_name", "priority", "comments", "tags"])

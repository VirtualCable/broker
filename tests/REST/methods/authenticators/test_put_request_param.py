#
# Copyright (c) 2025 Virtual Cable S.L.
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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""

import logging
import typing
from unittest import mock

from uds.auths.InternalDB.authenticator import InternalDBAuth

from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)

TEST_AUTH_TYPE: typing.Final[str] = "InternalDBAuth"

BASE_PAYLOAD: typing.Final[dict[str, typing.Any]] = {
    "comments": "request param test",
    "data_type": TEST_AUTH_TYPE,
    "tags": [],
    "priority": 1,
    "state": "A",
    "net_filtering": "D",
}


class PutRequestParamTest(rest.test.RESTTestCase):
    """The request must reach initialize() on updates, as it does on creations."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _create_authenticator(self) -> str:
        response = self.client.rest_put(
            "authenticators", data=BASE_PAYLOAD | {"name": "request-param", "small_name": "reqparam"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast(str, response.json()["id"])

    def _initialize_values_on(self, path: str, data: dict[str, typing.Any]) -> dict[str, typing.Any]:
        seen: dict[str, typing.Any] = {}
        original = InternalDBAuth.initialize

        def _capture(instance: InternalDBAuth, values: dict[str, typing.Any] | None) -> None:
            if values is not None:
                seen.update(values)
            original(instance, values)

        with mock.patch.object(InternalDBAuth, "initialize", _capture):
            response = self.client.rest_put(path, data=data)
        self.assertEqual(response.status_code, 200, response.content)
        return seen

    def test_create_passes_request(self) -> None:
        seen = self._initialize_values_on(
            "authenticators", BASE_PAYLOAD | {"name": "on-create", "small_name": "oncreate"}
        )
        self.assertIn("_request", seen)

    def test_update_passes_request(self) -> None:
        uuid = self._create_authenticator()
        seen = self._initialize_values_on(
            f"authenticators/{uuid}",
            BASE_PAYLOAD | {"name": "on-update", "small_name": "onupdate"},
        )
        self.assertIn("_request", seen)

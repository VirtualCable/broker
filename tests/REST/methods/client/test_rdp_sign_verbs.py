#
# Copyright (c) 2024 Virtual Cable S.L.
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
"""
import typing
from unittest import mock

from uds.REST.methods.client import Client

from ....utils import test


class ClientRdpSignVerbsTest(test.UDSTestCase):
    """rdp_sign is reachable through both PUT (legacy) and POST (current)."""

    def _build(self, sentinel: dict[str, typing.Any]) -> tuple[Client, mock.MagicMock]:
        client = object.__new__(Client)
        client._args = ["a-ticket", "rdp_sign"]
        client._params = {}
        signer = mock.MagicMock(return_value=sentinel)
        client._sign_rdp_ticket = signer  # type: ignore[method-assign]
        return client, signer

    def test_put_signs_ticket(self) -> None:
        sentinel: dict[str, typing.Any] = {"result": "signed"}
        client, signer = self._build(sentinel)

        self.assertIs(client.put(), sentinel)
        signer.assert_called_once_with("a-ticket")

    def test_post_signs_ticket(self) -> None:
        sentinel: dict[str, typing.Any] = {"result": "signed"}
        client, signer = self._build(sentinel)

        self.assertIs(client.post(), sentinel)
        signer.assert_called_once_with("a-ticket")

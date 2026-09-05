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

import logging
import typing

from tests.utils import rest
from uds import models

# from unittest import mock
from uds.core import types
from uds.core.managers.crypto import CryptoManager
from uds.core.managers.crypto import kem
from uds.core.util.model import sql_now

logger: logging.Logger = logging.getLogger(__name__)


class TicketTest(rest.test.RESTTestCase):
    """
    Test ticket functionality
    """

    server_token: str
    valid_ticket: str
    ip: str
    cm: typing.ClassVar[CryptoManager]
    kyber_public_key: typing.ClassVar[str]  # Base 64 encoded
    kyber_private_key: typing.ClassVar[str]  # Base 64 encoded

    @classmethod
    @typing.override
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.cm = CryptoManager.manager()
        cls.kyber_public_key, cls.kyber_private_key = kem.generate_keypair()

    @typing.override
    def setUp(self) -> None:
        super().setUp()

        sg = models.ServerGroup.objects.create(
            name="Test Tunnel Group", type=types.servers.ServerType.TUNNEL.value, subtype=""
        )

        # Create a ticket server
        raw_token = models.Server.create_token()
        server = models.Server.objects.create(
            register_username="tester",
            register_ip="127.0.0.1",
            ip="127.0.0.1",
            hostname="localhost",
            type=types.servers.ServerType.TUNNEL.value,
            stamp=sql_now(),
            subtype="",
            token_hash=models.Server.hash_token(raw_token),
        )
        server.groups.add(sg)
        self.server_token = raw_token

        # Create a userservice
        userservice = self.user_services[0]
        if not userservice.user:
            userservice.user = self.users[0]
            userservice.save()

        self.ip = userservice.get_instance().get_ip()

        # Create a valid ticket for testing
        self.valid_ticket = models.TicketStore.create_for_tunnel(
            userservice,
            remotes=[
                types.tickets.TunnelTicketRemote(
                    "",
                    1234,
                )
            ],
        )
        # Store a shared secret (32 bytes)
        models.TicketStore.set_shared_secret(self.valid_ticket, b"\x01" * 32)

    @staticmethod
    def get_url() -> str:
        """
        Returns the URL for ticket requests
        """
        return "/uds/rest/tunnel/ticket"

    # ------------------------------------------------------------------
    # Modern path: ``Authorization: Bearer sk-<token>`` header.
    # The body token field is no longer read by the server: requests
    # must authenticate via the Authorization header (HTTP standard).
    # ------------------------------------------------------------------
    def _bearer_sk(self, token: str) -> None:
        """Set the ``Authorization`` header on the test client for the
        new ``Bearer sk-<token>`` auth path.  Cleaned up in tearDown so
        subsequent tests are unaffected.

        Note: the key in ``uds_headers`` is the canonical HTTP header
        name (``Authorization``, *without* the ``HTTP_`` META prefix).
        Django's test client translates it to ``HTTP_AUTHORIZATION`` in
        ``request.META`` when the request is built.
        """
        self.client.add_header("Authorization", f"Bearer sk-{token}")
        self.addCleanup(self._clear_bearer)

    def _clear_bearer(self) -> None:
        self.client.uds_headers.pop("Authorization", None)

    def test_request_authorization_header_valid_start(self) -> None:
        """
        Modern auth path: ``Authorization: Bearer sk-<token>`` is honoured.
        The body no longer carries a ``token`` field at all — the server
        authenticates exclusively from the Authorization header.
        """
        self._bearer_sk(self.server_token)
        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                ticket=self.valid_ticket,
                command="start",
                ip="127.0.0.1",
                kem_kyber_key=self.kyber_public_key,
            ).as_dict(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        encrypted_data = response.json()
        data = self.cm.decrypted_dict(
            encrypted_data,
            self.valid_ticket,
            self.kyber_private_key,
        )
        r = types.tickets.TunnelTicketResponse.from_dict(data)
        self.assertEqual(r.remotes[0].host, self.ip)
        self.assertEqual(r.remotes[0].port, 1234)
        self.assertIsInstance(r.notify, str)
        self.assertEqual(r.shared_secret, "01" * 32)

    def test_request_authorization_header_valid_stop(self) -> None:
        """
        New auth path on a ``stop`` command: header takes precedence.
        """
        self._bearer_sk(self.server_token)
        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                ticket=self.valid_ticket,
                command="stop",
                ip="127.0.0.1",
                sent=1024,
                recv=2048,
            ).as_dict(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json(), {})

    def test_request_authorization_header_invalid_token(self) -> None:
        """
        New auth path with an unknown token in the header: 403.
        """
        self._bearer_sk("definitely_not_a_real_token")
        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                ticket=self.valid_ticket,
                command="start",
                ip="127.0.0.1",
                kem_kyber_key=self.kyber_public_key,
            ).as_dict(),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

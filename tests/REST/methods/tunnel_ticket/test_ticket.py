# -*- coding: utf-8 -*-
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
import logging

# from unittest import mock

from uds.core import types
from uds import models
from uds.core.managers.crypto import CryptoManager, kem
from uds.core.util.model import sql_now

from tests.utils import rest


logger = logging.getLogger(__name__)


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
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.cm = CryptoManager.manager()
        cls.kyber_public_key, cls.kyber_private_key = kem.generate_keypair()

    def setUp(self) -> None:
        super().setUp()

        sg = models.ServerGroup.objects.create(
            name='Test Tunnel Group', type=types.servers.ServerType.TUNNEL.value, subtype=''
        )

        # Create a ticket server
        server = models.Server.objects.create(
            register_username='tester',
            register_ip='127.0.0.1',
            ip='127.0.0.1',
            hostname='localhost',
            type=types.servers.ServerType.TUNNEL.value,
            stamp=sql_now(),
            subtype='',
        )
        server.groups.add(sg)
        self.server_token = server.token

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
                    '',
                    1234,
                )
            ],
        )
        # Store a shared secret (32 bytes)
        models.TicketStore.set_shared_secret(self.valid_ticket, b'\x01' * 32)

    @staticmethod
    def get_url_legacy(ticket: str, token: str, msg: str) -> str:
        """
        Returns the URL for ticket requests
        """
        return f'/uds/rest/tunnel/ticket/{ticket}/{msg}/{token}'

    @staticmethod
    def get_url() -> str:
        """
        Returns the URL for ticket requests
        """
        return f'/uds/rest/tunnelpq/ticket'

    def test_legacy_request_invalid_token(self) -> None:
        """
        Test ticket request with invalid token
        """
        response = self.client.get(
            self.get_url_legacy(
                self.valid_ticket,
                'invalid_token',
                '127.0.0.1',
            ),
        )
        self.assertEqual(response.status_code, 403)

    def test_legacy_request_invalid_ticket(self) -> None:
        """
        Test ticket request with invalid ticket
        """
        response = self.client.get(
            self.get_url_legacy(
                'invalid_ticket',
                self.server_token,
                '127.0.0.1',
            ),
        )
        self.assertEqual(response.status_code, 403)

    def test_legacy_request_valid_ticket_start(self) -> None:
        """
        Test ticket request with valid ticket and start
        """
        response = self.client.get(
            self.get_url_legacy(
                self.valid_ticket,
                self.server_token,
                '127.0.0.1',  # Start message is the source IP, compat with 4.x
            ),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        r = types.tickets.TunnelTicketLegacyResponse.from_dict(
            data
        )  # Just to check it can be created without errors

        self.assertEqual(r.host, self.ip)  #
        self.assertEqual(r.port, 1234)
        self.assertIsInstance(r.notify, str)
        self.assertEqual(r.shared_secret, '01' * 32)  # Hex representation

    def test_legacy_request_valid_ticket_stop(self) -> None:
        """
        Test ticket request with valid ticket and stop
        """
        response = self.client.get(
            self.get_url_legacy(
                self.valid_ticket,
                self.server_token,
                'stop',  # Stop message
            ),
            query_params={
                'sent': '1024',
                'recv': '2048',
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_request_invalid_token(self) -> None:
        """
        Test ticket request with invalid token
        """
        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                token='invalid_token',
                ticket=self.valid_ticket,
                command='start',
                ip='127.0.0.1',
                kem_kyber_key=self.kyber_public_key,
            ).as_dict(),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_request_invalid_kem_key(self) -> None:
        """
        Test ticket request with invalid token
        """
        # Invalid base64 key
        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                token=self.server_token,
                ticket=self.valid_ticket,
                command='start',
                ip='127.0.0.1',
                kem_kyber_key='invalid_kem_key',
            ).as_dict(),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

        # Valid key, but invalid for Kyber (basically, invalid length)
        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                token=self.server_token,
                ticket=self.valid_ticket,
                command='start',
                ip='127.0.0.1',
                kem_kyber_key='AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAg==',
            ).as_dict(),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_request_invalid_ticket(self) -> None:
        """
        Test ticket request with invalid ticket
        """
        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                token=self.server_token,
                ticket='invalid_ticket',
                command='start',
                ip='127.0.0.1',
                kem_kyber_key=self.kyber_public_key,
            ).as_dict(),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 403)

    def test_request_valid_ticket_start(self) -> None:
        """
        Test ticket request with valid ticket and start
        """

        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                token=self.server_token,
                ticket=self.valid_ticket,
                command='start',
                ip='127.0.0.1',
                kem_kyber_key=self.kyber_public_key,
            ).as_dict(),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        encrypted_data = response.json()
        # Decrytpt reponse to process it
        data = self.cm.decrypted_dict(
            encrypted_data,
            self.valid_ticket,
            self.kyber_private_key,
        )

        r = types.tickets.TunnelTicketResponse.from_dict(data)  # Just to check it can be created without errors

        self.assertEqual(r.remotes[0].host, self.ip)  #
        self.assertEqual(r.remotes[0].port, 1234)
        self.assertIsInstance(r.notify, str)
        self.assertEqual(r.shared_secret, '01' * 32)  # Hex representation

    def test_request_valid_ticket_stop(self) -> None:
        """
        Test ticket request with valid ticket and stop
        This response is not encrypted, just an empty dict is returned
        """
        response = self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                token=self.server_token,
                ticket=self.valid_ticket,
                command='stop',
                ip='127.0.0.1',
                sent=1024,
                recv=2048,
            ).as_dict(),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data, {})  # Stop returns empty response

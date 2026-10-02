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
Author: Janier Rodríguez, jrodriguez at virtualcable dot es
"""
import typing
from unittest import mock

from django.test import override_settings

from uds.core import consts
from uds.core import types
from uds.core.exceptions.services import ServiceNotReadyError
from uds.core.managers.crypto import CryptoManager
from uds.models import TicketStore, User
from uds.REST.methods.client import Client

from ....utils import test

SCRAMBLER: typing.Final[str] = 'a-scrambler'


@override_settings(DEBUG=False)
class ClientTicketLifetimeTest(test.UDSTestCase):
    """A service still in preparation must leave the launch ticket usable for the client retry."""

    def _build(self) -> tuple[Client, str]:
        ticket = TicketStore.create(
            {
                'user': 'a-user-uuid',
                'service': 'a-service-uuid',
                'transport': 'a-transport-uuid',
                'password': CryptoManager.manager().symmetric_encrypt('a-password', SCRAMBLER),
            }
        )
        client = object.__new__(Client)
        client._args = [ticket, SCRAMBLER]
        client._params = {'version': consts.system.VERSION}
        client._request = mock.MagicMock()
        return client, ticket

    def test_service_in_preparation_keeps_ticket(self) -> None:
        client, ticket = self._build()

        with mock.patch('uds.REST.methods.client.User'), mock.patch(
            'uds.REST.methods.client.UserServiceManager'
        ) as manager:
            manager.manager.return_value.get_user_service_info.side_effect = ServiceNotReadyError()
            result = client.process(ticket, SCRAMBLER)

        self.assertEqual(result['error']['message'], types.errors.Error.SERVICE_IN_PREPARATION.message)
        self.assertTrue(result['error']['is_retryable'])
        self.assertTrue(TicketStore.objects.filter(uuid=ticket).exists())

    def test_served_request_consumes_ticket(self) -> None:
        client, ticket = self._build()

        with mock.patch('uds.REST.methods.client.User'), mock.patch(
            'uds.REST.methods.client.UserServiceManager'
        ) as manager:
            manager.manager.return_value.get_user_service_info.return_value.ip = '172.27.0.1'
            client.process(ticket, SCRAMBLER)

        self.assertFalse(TicketStore.objects.filter(uuid=ticket).exists())

    def test_unresolvable_user_consumes_ticket(self) -> None:
        client, ticket = self._build()

        with mock.patch('uds.REST.methods.client.User') as user_model:
            user_model.objects.get.side_effect = User.DoesNotExist
            result = client.process(ticket, SCRAMBLER)

        self.assertEqual(result['error']['message'], 'Invalid request')
        self.assertFalse(TicketStore.objects.filter(uuid=ticket).exists())

    def test_unknown_ticket_is_denied(self) -> None:
        client, ticket = self._build()
        TicketStore.objects.filter(uuid=ticket).delete()

        result = client.process(ticket, SCRAMBLER)

        self.assertEqual(result['error']['message'], types.errors.Error.ACCESS_DENIED.message)

    def test_invalidate_only_consumes_the_ticket_of_its_owner(self) -> None:
        ticket = TicketStore.create({'user': 'a-user-uuid'}, owner='an-owner')

        TicketStore.invalidate(ticket)
        self.assertTrue(TicketStore.objects.filter(uuid=ticket).exists())

        TicketStore.invalidate(ticket, owner='an-owner')
        self.assertFalse(TicketStore.objects.filter(uuid=ticket).exists())

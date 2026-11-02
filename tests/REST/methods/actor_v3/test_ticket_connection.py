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
Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing

from tests.utils import rest
from uds import models
from uds.core import types
from uds.core.managers.crypto import CryptoManager
from uds.core.managers.crypto import kem

logger: logging.Logger = logging.getLogger(__name__)


class TicketConnectionTest(rest.test.RESTTestCase):
    """
    Test actorv3 "connection" ticket functionality (start & stop commands)

    Connection tickets carry a single dummy remote (host/port are ignored,
    they exist just to transport the "extra" data), and authenticate via
    the actor's (rotated) token.
    """

    userservice: models.UserService
    actor_token: str
    connection_ticket: str
    userservice_ip: str

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
        self.userservice = self.user_services[0]
        if not self.userservice.user:
            self.userservice.user = self.users[0]
            self.userservice.save()

        self.userservice_ip = self.userservice.get_instance().get_ip()

        # Rotate actor token, as a real initialized actor will do
        self.actor_token = models.user_service.create_actor_token()
        self.userservice.token_hash = models.user_service.hash_actor_token(self.actor_token)
        self.userservice.save(update_fields=["token_hash"])

        self.connection_ticket = self._ticket_for(self.userservice)

    @staticmethod
    def get_url() -> str:
        """
        Returns the URL for the actor connection ticket requests
        """
        return "/uds/rest/actor/v3/ticket/connection"

    def _post(
        self,
        *,
        command: str,
        ticket: str,
        ip: str = "127.0.0.1",
        token: str | None = None,
        kem_kyber_key: str = "",
        sent: int = 0,
        recv: int = 0,
    ) -> typing.Any:
        """Posts a connection ticket request, authenticating with the actor token"""
        return self.client.post(
            self.get_url(),
            data=types.tickets.TunnelTicketRequest(
                ticket=ticket,
                command=command,
                ip=ip,
                kem_kyber_key=kem_kyber_key,
                sent=sent,
                recv=recv,
            ).as_dict()
            | {"token": token if token is not None else self.actor_token},
            content_type="application/json",
        )

    def _start(self, ticket: str, **kwargs: typing.Any) -> typing.Any:
        return self._post(command="start", ticket=ticket, kem_kyber_key=self.kyber_public_key, **kwargs)

    def _start_ok(self, ticket: str) -> types.tickets.TunnelTicketResponse:
        """Performs a valid start and returns the decrypted response"""
        response = self._start(ticket)
        self.assertEqual(response.status_code, 200, response.content)
        # Actor responses are wrapped by rest_result, encrypted payload is in "result"
        data = self.cm.decrypted_dict(response.json()["result"], ticket, self.kyber_private_key)
        return types.tickets.TunnelTicketResponse.from_dict(data)

    def _other_userservice(self) -> models.UserService:
        """A managed userservice of another user, with its own connection ticket"""
        other = self.user_services[2]
        if not other.user:
            other.user = self.users[1]
            other.save()
        return other

    def _ticket_for(self, userservice: models.UserService, extra: dict[str, typing.Any] | None = None) -> str:
        """Creates a connection ticket with a single dummy remote carrying extra data"""
        return models.TicketStore.create_for_tunnel(
            userservice,
            remotes=[
                types.tickets.TunnelTicketRemote(
                    "127.0.0.1",  # Dummy host, ignored on connections
                    0,  # Dummy port, ignored on connections
                    extra=extra if extra is not None else {"title": "Test Connection"},
                )
            ],
        )

    # ------------------------------------------------------------------
    # start
    # ------------------------------------------------------------------
    def test_connection_start(self) -> None:
        """
        A valid start returns an encrypted response with a notify ticket,
        the (dummy) remotes with their extra data, and no shared secret
        """
        r = self._start_ok(self.connection_ticket)

        # The dummy remote travels untouched in the response, extra preserved
        self.assertEqual(len(r.remotes), 1)
        self.assertEqual(r.remotes[0].host, "127.0.0.1")
        self.assertEqual(r.remotes[0].port, 0)
        self.assertEqual(r.remotes[0].extra, {"title": "Test Connection"})
        self.assertEqual(r.shared_secret, "")
        self.assertTrue(r.enable_udp)
        self.assertIsInstance(r.notify, str)

        # Notify ticket points to our userservice and keeps the remotes (and extra)
        notify = models.TicketStore.get_for_tunnel(r.notify)
        self.assertIsNotNone(notify.userservice)
        self.assertEqual(notify.userservice.uuid if notify.userservice else None, self.userservice.uuid)
        self.assertEqual(len(notify.remotes), 1)
        self.assertEqual(notify.remotes[0].extra, {"title": "Test Connection"})

    def test_connection_start_consumes_ticket(self) -> None:
        """
        The connection ticket is consumed on a successful start and cannot be reused
        """
        self._start_ok(self.connection_ticket)

        # Ticket format is uuid + 8 chars owner, so uuid is all but the last 8 chars
        self.assertFalse(models.TicketStore.objects.filter(uuid=self.connection_ticket[:-8]).exists())

        response = self._start(self.connection_ticket)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("error"), "Invalid ticket")

    def test_connection_start_requires_remote(self) -> None:
        """
        A ticket without remotes is refused (extra needs at least one remote to
        travel with) and it is not consumed. Host/port values are not checked,
        they are ignored on connections.
        """
        ticket = models.TicketStore.create_for_tunnel(self.userservice, remotes=[])

        response = self._start(ticket)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(models.TicketStore.objects.filter(uuid=ticket[:-8]).exists())

    def test_connection_start_records_open_event(self) -> None:
        """
        A successful start records a CONNECTION_OPEN event on the service pool
        """
        self._start_ok(self.connection_ticket)

        events = models.StatsEvents.objects.filter(
            owner_id=self.userservice.service_pool.id,
            event_type=types.stats.EventType.CONNECTION_OPEN,
        )
        self.assertEqual(events.count(), 1)
        event = events.first()
        assert event is not None
        assert self.userservice.user is not None
        self.assertEqual(event.fld1, self.userservice.user.pretty_name)  # username
        self.assertEqual(event.fld2, "127.0.0.1")  # srcip
        self.assertEqual(event.fld3, self.userservice_ip)  # dstip, the userservice itself

    def test_connection_start_invalid_ticket(self) -> None:
        """
        A well formed but non existent ticket returns an error, not an exception
        """
        response = self._start("a" * 48)  # Valid length (uuid + owner), non existent
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("error"), "Invalid ticket")

    def test_connection_start_invalid_ip(self) -> None:
        """
        An invalid source IP is refused and does not consume the ticket
        """
        response = self._start(self.connection_ticket, ip="not_an_ip")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(models.TicketStore.objects.filter(uuid=self.connection_ticket[:-8]).exists())

    def test_connection_start_invalid_token(self) -> None:
        """
        An invalid actor token is refused and does not consume the ticket
        """
        response = self._start(self.connection_ticket, token="invalid")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(models.TicketStore.objects.filter(uuid=self.connection_ticket[:-8]).exists())

    def test_connection_start_ticket_from_other_userservice(self) -> None:
        """
        A valid actor token cannot start a connection ticket owned by another userservice,
        and that ticket is left untouched
        """
        other_ticket = self._ticket_for(self._other_userservice())

        response = self._start(other_ticket)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(models.TicketStore.objects.filter(uuid=other_ticket[:-8]).exists())

    def test_connection_start_invalid_command(self) -> None:
        """
        An unknown command is refused and does not consume the ticket
        """
        response = self._post(command="bogus", ticket=self.connection_ticket)
        self.assertEqual(response.status_code, 403)
        self.assertTrue(models.TicketStore.objects.filter(uuid=self.connection_ticket[:-8]).exists())

    # ------------------------------------------------------------------
    # stop
    # ------------------------------------------------------------------
    def test_connection_stop(self) -> None:
        """
        A valid stop (with the notify ticket obtained on start) records
        a CONNECTION_CLOSE event with sent/received counters and duration
        """
        r = self._start_ok(self.connection_ticket)

        response = self._post(command="stop", ticket=r.notify, sent=1024, recv=2048)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertIsNone(response.json().get("result"))

        events = models.StatsEvents.objects.filter(
            owner_id=self.userservice.service_pool.id,
            event_type=types.stats.EventType.CONNECTION_CLOSE,
        )
        self.assertEqual(events.count(), 1)
        event = events.first()
        assert event is not None
        self.assertNotEqual(event.fld1, "")  # duration is present
        self.assertEqual(event.fld2, "1024")  # sent
        self.assertEqual(event.fld3, "2048")  # received

    def test_connection_stop_invalid_ticket(self) -> None:
        """
        A well formed but non existent notify ticket returns an error, not an exception
        """
        response = self._post(command="stop", ticket="a" * 48, sent=1, recv=2)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("error"), "Invalid ticket")

    def test_connection_stop_ticket_from_other_userservice(self) -> None:
        """
        A valid actor token cannot stop a connection of another userservice
        """
        other_ticket = self._ticket_for(self._other_userservice())

        response = self._post(command="stop", ticket=other_ticket, sent=1, recv=2)
        self.assertEqual(response.status_code, 403)
        # Ticket of the other userservice is left untouched
        self.assertTrue(models.TicketStore.objects.filter(uuid=other_ticket[:-8]).exists())
        # And no close event has been recorded
        self.assertEqual(
            models.StatsEvents.objects.filter(event_type=types.stats.EventType.CONNECTION_CLOSE).count(),
            0,
        )

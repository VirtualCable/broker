"""
Tests for the transport-ticket update endpoint ownership binding.
"""

import json
import pickle
import typing

from django.urls import reverse

from uds import models

from ...utils.web import test


class TransportTicketUpdateTest(test.WEBTestCase):
    """Only the ticket owner may update a standard transport ticket."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.owner = self.plain_users[0]
        self.attacker = self.plain_users[1]

    def _make_ticket(self) -> str:
        data = {
            "service": "A0000000-0000-0000-0000-000000000000",
            "transport": "T0000000-0000-0000-0000-000000000000",
            "user": self.owner.uuid,
            "password": b"original-encrypted",
        }
        return models.TicketStore.create(data)

    def _update(self, ticket_id: str, scrambler: str) -> "typing.Any":
        return self.client.post(
            reverse(
                "webapi.transport.update_transport_ticket",
                kwargs={"ticket_id": ticket_id, "scrambler": scrambler},
            ),
            data=json.dumps({"username": "attacker-user", "password": "attacker-pass", "domain": "EVIL"}),
            content_type="application/json",
        )

    def test_other_user_cannot_update_standard_ticket(self) -> None:
        ticket_id = self._make_ticket()
        self.login(user=self.attacker, as_admin=False)

        response = self._update(ticket_id, "a" * 32)
        self.assertEqual(response.status_code, 200, response.content)

        stored = models.TicketStore.objects.get(uuid=ticket_id)
        data = pickle.loads(stored.data)  # server-generated ticket data
        self.assertNotIn("username", data)
        self.assertNotIn("domain", data)
        self.assertEqual(data["user"], self.owner.uuid)

    def test_owner_can_update_standard_ticket(self) -> None:
        ticket_id = self._make_ticket()
        self.login(user=self.owner, as_admin=False)

        response = self._update(ticket_id, "a" * 32)
        self.assertEqual(response.status_code, 200, response.content)

        stored = models.TicketStore.objects.get(uuid=ticket_id)
        data = pickle.loads(stored.data)  # server-generated ticket data
        self.assertEqual(data.get("username"), "attacker-user")
        self.assertEqual(data.get("domain"), "EVIL")

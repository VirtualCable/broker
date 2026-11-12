"""
Author: Andres Schumann, aschumann at virtualcable dot es

A ticket read with invalidate=True is consumed once: a reader that lost the race to another
consumer must not receive the data.
"""

import typing
from unittest import mock

from uds.models import TicketStore

from tests.utils.test import UDSTestCase


class TicketStoreClaimTest(UDSTestCase):
    def _create(self) -> str:
        return TicketStore.create({"user": "a-user-uuid", "service": "a-service-uuid"})

    def test_single_read_returns_data_and_removes_ticket(self) -> None:
        ticket = self._create()
        data = TicketStore.get(ticket)
        self.assertEqual(data["user"], "a-user-uuid")
        self.assertFalse(TicketStore.objects.filter(uuid=ticket).exists())

    def test_reader_that_lost_the_race_gets_nothing(self) -> None:
        ticket = self._create()
        original_get = TicketStore.objects.get

        def read_then_other_consumer_wins(*args: typing.Any, **kwargs: typing.Any) -> typing.Any:
            stored = original_get(*args, **kwargs)
            TicketStore.invalidate(ticket)  # the other consumer deletes the row after this read
            return stored

        with (
            mock.patch.object(TicketStore.objects, "get", side_effect=read_then_other_consumer_wins),
            self.assertRaises(TicketStore.DoesNotExist),
        ):
            TicketStore.get(ticket)

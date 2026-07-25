# -*- coding: utf-8 -*-
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
Tests for the ``cb_ticket`` plumbing on ``gui.ChoiceField``.

Two flows coexist:

- **Legacy** (no ``set_cb_ticket`` called): ``cb_ticket`` is absent from
  ``fills``. ``gui_description()`` returns ``fills`` unchanged. The backend
  keeps the current behaviour (params go through the query string).
- **Secured** (the service opts in): ``set_cb_ticket`` populates a dict under
  ``fills.cb_ticket``. ``gui_description()`` creates a real ``TicketStore``
  entry and replaces the dict by its uuid. The GUI never sees the dict.
"""
from __future__ import annotations

import typing

from django.db import transaction

from uds.core import types
from uds.core.ui import gui

from ...utils.test import UDSTestCase


def _dummy_callback(parameters: dict[str, typing.Any]) -> types.ui.CallbackResultType:
    """Minimal callback used by the test fields."""
    return ()


class CbTicketLegacyTest(UDSTestCase):
    """Behaviour when no service calls ``set_cb_ticket`` (default)."""

    def test_choice_field_without_fills_does_not_carry_cb_ticket(self) -> None:
        field = gui.ChoiceField(
            label="Region",
            choices=[gui.choice_item("us-east-1", "US East 1")],
        )
        info = field.gui_description()
        # No fills → no cb_ticket to worry about.
        self.assertIsNone(info.fills)

    def test_choice_field_with_fills_but_no_set_cb_ticket_keeps_fills_unchanged(
        self,
    ) -> None:
        field = gui.ChoiceField(
            label="Region",
            choices=[gui.choice_item("us-east-1", "US East 1")],
            fills={
                "callback_name": "sampleCallback",
                "function": _dummy_callback,
                "parameters": ["prov_uuid", "region"],
            },
        )
        original_fills = dict(field._field_info.fills or {})

        info = field.gui_description()
        self.assertIsNotNone(info.fills)
        # cb_ticket must not appear because set_cb_ticket was never called.
        self.assertNotIn("cb_ticket", info.fills)  # type: ignore[operator]
        # The original fills dict is not mutated.
        self.assertEqual(field._field_info.fills, original_fills)


class CbTicketSetTest(UDSTestCase):
    """``ChoiceField.set_cb_ticket`` builds the storage dict."""

    def test_set_cb_ticket_without_fills_raises(self) -> None:
        field = gui.ChoiceField(
            label="Plain",
            choices=[gui.choice_item("a", "A")],
        )
        with self.assertRaises(ValueError):
            field.set_cb_ticket("prov_uuid", "ignored")

    def test_set_cb_ticket_accumulates_keys(self) -> None:
        field = gui.ChoiceField(
            label="Region",
            choices=[gui.choice_item("us-east-1", "US East 1")],
            fills={
                "callback_name": "sampleCallback",
                "function": _dummy_callback,
                "parameters": ["prov_uuid", "region"],
            },
        )
        field.set_cb_ticket("prov_uuid", "provider-uuid-xyz")
        field.set_cb_ticket("region", "us-east-1")

        fills = field._field_info.fills
        assert fills is not None
        self.assertEqual(
            fills["cb_ticket"],  # type: ignore[reportTypedDictNotRequiredAccess]
            {"prov_uuid": "provider-uuid-xyz", "region": "us-east-1"},
        )


class CbTicketGuiDescriptionTest(UDSTestCase):
    """``gui_description()`` swaps the dict by the ticket uuid."""

    def test_gui_description_emits_a_ticket_for_cb_ticket_dict(self) -> None:
        field = gui.ChoiceField(
            label="Region",
            choices=[gui.choice_item("us-east-1", "US East 1")],
            fills={
                "callback_name": "sampleCallback",
                "function": _dummy_callback,
                "parameters": ["prov_uuid", "region"],
            },
        )
        field.set_cb_ticket("prov_uuid", "provider-uuid-xyz")

        with transaction.atomic():
            info = field.gui_description()
            self.assertIsNotNone(info.fills)
            cb_ticket = typing.cast(str, info.fills["cb_ticket"])  # type: ignore[reportTypedDictNotRequiredAccess]
            self.assertIsInstance(cb_ticket, str)
            # Resolves back to the dict we set.
            from uds.models import TicketStore  # noqa: PLC0415 -- local import to keep top-level clean

            stored = TicketStore.objects.get(uuid=cb_ticket)
            from uds.core.consts import ticket as ticket_consts  # noqa: PLC0415

            self.assertEqual(stored.validity, ticket_consts.CB_TICKET_VALIDITY_TIME)
            transaction.set_rollback(True)

        # After serialisation, the field's fills.cb_ticket is the uuid (string)
        # — the original dict was replaced in-place so subsequent calls don't
        # recreate the ticket (see ``isinstance(cb_ticket, dict)`` guard).
        self.assertIsInstance(
            field._field_info.fills["cb_ticket"],  # type: ignore[reportTypedDictNotRequiredAccess]
            str,
        )

    def test_gui_description_returns_a_copy_with_replaced_dict(self) -> None:
        field = gui.ChoiceField(
            label="Region",
            choices=[gui.choice_item("us-east-1", "US East 1")],
            fills={
                "callback_name": "sampleCallback",
                "function": _dummy_callback,
                "parameters": ["prov_uuid", "region"],
            },
        )
        field.set_cb_ticket("prov_uuid", "provider-uuid-xyz")
        with transaction.atomic():
            info = field.gui_description()
            self.assertIsNotNone(info.fills)
            self.assertNotIsInstance(info.fills["cb_ticket"], dict)  # type: ignore[index]
            self.assertIsInstance(info.fills["cb_ticket"], str)  # type: ignore[index]
            transaction.set_rollback(True)
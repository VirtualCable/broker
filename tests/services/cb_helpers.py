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
Helpers shared by the OSS secure-callback tests.

The migration replaces the naked ``prov_uuid`` in callback ``parameters`` with
an opaque ``cb_ticket`` uuid backed by a ``TicketStore`` entry. This helper
verifies that contract end-to-end by instantiating the real service, calling
``init_gui()`` and ``gui_description()``, and checking the round-trip through
``TicketStore.get``.
"""

from __future__ import annotations

import collections.abc
import typing

from django.db import transaction

from uds.core import types
from uds.core.services import Service
from uds.models import TicketStore

from ..utils.test import UDSTestCase


def _field_info(elements: list[types.ui.GuiElement], field_name: str) -> types.ui.FieldInfo:
    for element in elements:
        if element.name == field_name:
            return element.gui
    raise AssertionError(f"missing field {field_name!r} in gui_description output")


def _fills_dict(info: types.ui.FieldInfo) -> dict[str, typing.Any]:
    fills = info.fills
    if not isinstance(fills, dict):
        raise AssertionError(f"field has no fills dict (got {fills!r})")
    return typing.cast("dict[str, typing.Any]", fills)


def assert_secure_callbacks(
    test: UDSTestCase,
    service: Service,
    field_names: collections.abc.Iterable[str],
) -> None:
    """Verify the secure-callback contract on ``service`` for each field.

    For every field that used to carry ``prov_uuid`` in its ``parameters``:

    * ``gui_description()`` must NOT expose ``'prov_uuid'`` in ``parameters``.
    * ``gui_description()`` MUST emit a ``cb_ticket`` uuid string.
    * That uuid must resolve (via ``TicketStore.get``) to a dict whose
      ``prov_uuid`` key carries the provider's uuid.
    """
    service.init_gui()
    with transaction.atomic():
        elements = service.gui_description()
        for field_name in field_names:
            fills = _fills_dict(_field_info(elements, field_name))
            test.assertNotIn(
                "prov_uuid",
                fills.get("parameters", []),
                msg=f"{field_name!r}: parameters must not carry 'prov_uuid' after migration",
            )
            cb_ticket = fills.get("cb_ticket")
            test.assertIsInstance(
                cb_ticket,
                str,
                msg=f"{field_name!r}: fills.cb_ticket must be a uuid string, got {cb_ticket!r}",
            )
            stored = TicketStore.get(typing.cast(str, cb_ticket), invalidate=False)
            test.assertEqual(
                stored.get("prov_uuid"),
                service.provider().get_uuid(),
                msg=f"{field_name!r}: cb_ticket must resolve to a dict with prov_uuid == provider uuid",
            )
        transaction.set_rollback(True)

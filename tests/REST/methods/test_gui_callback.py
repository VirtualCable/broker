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
# DISCLAIMED. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDER BE LIABLE FOR
# ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

"""
Tests for ``src/uds/REST/methods/gui_callback.py``: the dispatcher that
resolves ``cb_ticket`` and merges its data into the params before
invoking the callback. See plan in ``doc/plan/secure_callbacks.md``.

Scope is intentionally narrow: only the cb_ticket resolution branch.
The cb_ticket *creation* (when ``cb_ticket`` is a dict in ``Filler``)
is covered in ``tests/core/ui/test_cb_ticket_field.py``.
"""

import typing
from unittest import mock

from uds import models
from uds.core import exceptions
from uds.core import types
from uds.core.ui import gui
from uds.REST.methods.gui_callback import Callback

from tests.utils.test import UDSTestCase

# Callback name registered in setUp. Underscore-prefixed to avoid colliding
# with any real callback registered by other tests.
_TEST_CB: typing.Final[str] = "__test_gui_callback__"


class GuiCallbackDispatcherTests(UDSTestCase):
    """The Callback handler resolves ``cb_ticket`` before invoking the callback."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.received: list[dict[str, typing.Any]] = []

        def _stub(params: dict[str, str]) -> list[types.ui.CallbackResultItem]:
            self.received.append(params)
            return [{"name": "result", "choices": []}]

        gui.callbacks[_TEST_CB] = _stub

    @typing.override
    def tearDown(self) -> None:
        gui.callbacks.pop(_TEST_CB, None)
        super().tearDown()

    def _make_callback(self, args: list[str], params: dict[str, typing.Any]) -> Callback:
        """Build a Callback skipping Handler.__init__ (no request/headers wiring needed)."""
        cb = Callback.__new__(Callback)
        cb._args = args
        cb._params = params
        return cb

    def test_no_ticket_passes_params_directly(self) -> None:
        """Without cb_ticket, the dispatcher forwards ``_params`` verbatim."""
        params: dict[str, typing.Any] = {"a": "1", "b": "2"}
        cb = self._make_callback([_TEST_CB], params)
        with mock.patch.object(models.TicketStore, "get") as m_get:
            cb.get()
        m_get.assert_not_called()
        self.assertEqual(self.received, [{"a": "1", "b": "2"}])

    def test_valid_ticket_is_merged(self) -> None:
        """A valid cb_ticket is resolved and its dict is merged into the params."""
        cb = self._make_callback(
            [_TEST_CB],
            {"cb_ticket": "abc", "query_param": "from-query"},
        )
        with mock.patch.object(models.TicketStore, "get", return_value={"prov_uuid": "u-1"}) as m_get:
            cb.get()
        m_get.assert_called_once_with("abc", invalidate=False)
        self.assertEqual(
            self.received,
            [{"query_param": "from-query", "prov_uuid": "u-1"}],
        )

    def test_ticket_data_wins_on_collision(self) -> None:
        """If a key is in both query and ticket, the ticket value wins."""
        cb = self._make_callback(
            [_TEST_CB],
            {"cb_ticket": "abc", "shared": "from-query"},
        )
        with mock.patch.object(models.TicketStore, "get", return_value={"shared": "from-ticket"}):
            cb.get()
        self.assertEqual(self.received, [{"shared": "from-ticket"}])

    def test_ticket_does_not_leak_into_callback(self) -> None:
        """``cb_ticket`` is consumed by the dispatcher and never reaches the callback."""
        cb = self._make_callback(
            [_TEST_CB],
            {"cb_ticket": "abc", "keep": "y"},
        )
        with mock.patch.object(models.TicketStore, "get", return_value={"a": "1"}):
            cb.get()
        self.assertEqual(self.received, [{"keep": "y", "a": "1"}])
        self.assertNotIn("cb_ticket", self.received[0])

    def test_invalid_ticket_raises_request_error(self) -> None:
        """A missing or expired cb_ticket yields a RequestError; the callback is not called."""
        cb = self._make_callback([_TEST_CB], {"cb_ticket": "missing"})
        with mock.patch.object(
            models.TicketStore,
            "get",
            side_effect=models.TicketStore.DoesNotExist,
        ):
            with self.assertRaises(exceptions.rest.RequestError):
                cb.get()
        self.assertEqual(self.received, [])

    def test_handler_does_not_mutate_self_params(self) -> None:
        """The dispatcher works on a copy; ``self._params`` keeps ``cb_ticket`` for next call."""
        original: dict[str, typing.Any] = {"cb_ticket": "abc", "x": "y"}
        cb = self._make_callback([_TEST_CB], dict(original))
        with mock.patch.object(models.TicketStore, "get", return_value={"a": "1"}):
            cb.get()
        self.assertEqual(cb._params, original)
        self.assertIn("cb_ticket", cb._params)

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
Contract tests for the ``/dashboard`` endpoint.

Freezes the external semantics the administration dashboard (and the MCP
``get_dashboard`` tool built on top of it) rely on: the look-back window
parameter travels on the query string, is clamped to a sane range and
never fails the request, the payload always carries the kpis plus one
entry per widget, and the endpoint is admin-only.

The handler reads its parameters through ``self.params`` (the
dispatcher's canonical channel) instead of the raw query string, so both
the HTTP GET and the in-process MCP proxy reach it the same way; these
tests pin that the HTTP side is undisturbed by the change.

Reference: src/uds/REST/methods/dashboard.py

Author: Adolfo Gómez, dkmaster at dkmon dot com
"""

import logging
import typing

from ....utils import rest

logger: logging.Logger = logging.getLogger(__name__)

#: One distinct window per test: the payload is cached per day-range, so
#: sharing a range between tests would let one warm the other's answer.
_WIDGETS: typing.Final[tuple[str, ...]] = (
    "kpis",
    "peak_concurrency",
    "pool_saturation",
    "cache_efficiency",
    "tunnel_usage",
    "client_platforms",
    "top_users",
    "session_duration",
    "userservice_errors",
    "failed_logins",
)


class DashboardTest(rest.test.RESTTestCase):
    """Freezes the external contract of GET /dashboard/data."""

    def _data(self, query: str = "") -> dict[str, typing.Any]:
        self.login()
        response = self.client.rest_get(f"dashboard/data{query}")
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", response.json())

    def test_payload_carries_every_widget(self) -> None:
        data = self._data("?days=31&flush=1")
        self.assertEqual(data["days"], 31)
        for widget in _WIDGETS:
            self.assertIn(widget, data)
        self.assertIsInstance(data["kpis"], dict)

    def test_days_is_clamped_to_the_sane_range(self) -> None:
        self.assertEqual(self._data("?days=99999&flush=1")["days"], 365)
        self.assertEqual(self._data("?days=0&flush=1")["days"], 1)

    def test_garbage_days_fall_back_to_the_default(self) -> None:
        self.assertEqual(self._data("?days=abc&flush=1")["days"], 30)

    def test_dashboard_alias_without_data_segment(self) -> None:
        self.login()
        response = self.client.rest_get("dashboard?days=33&flush=1")
        self.assertEqual(response.status_code, 200, response.content)
        data = typing.cast("dict[str, typing.Any]", response.json())
        self.assertEqual(data["days"], 33)
        self.assertIn("kpis", data)

    def test_staff_is_denied(self) -> None:
        self.login(as_admin=False)
        response = self.client.rest_get("dashboard/data")
        self.assertEqual(response.status_code, 403)

    def test_anonymous_is_denied(self) -> None:
        response = self.client.rest_get("dashboard/data")
        self.assertEqual(response.status_code, 403)

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

from uds import models

from ....utils import rest

EMAIL_TYPE: typing.Final[str] = "emailNotifications"
WEBHOOK_EVENT_TYPE: typing.Final[str] = "webhookNotifications"
WEBHOOK_LOG_TYPE: typing.Final[str] = "webhookLogNotifications"


class NotifiersLevelTest(rest.test.RESTTestCase):
    """The level only filters log notifications, so the admin shows it only where it applies."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _gui_field_names(self, for_type: str) -> list[str]:
        response = self.client.rest_get(f"messaging/notifiers/gui/{for_type}")
        self.assertEqual(response.status_code, 200, response.content)
        return [element["name"] for element in response.json()]

    def _table_field(self, name: str) -> dict[str, typing.Any]:
        response = self.client.rest_get("messaging/notifiers/tableinfo")
        self.assertEqual(response.status_code, 200, response.content)
        fields: list[dict[str, typing.Any]] = response.json()["fields"]
        return next(field[name] for field in fields if name in field)

    def test_event_only_notifier_has_no_level_field(self) -> None:
        self.assertNotIn("level", self._gui_field_names(WEBHOOK_EVENT_TYPE))

    def test_log_notifiers_keep_the_level_field_before_enabled(self) -> None:
        for for_type in (WEBHOOK_LOG_TYPE, EMAIL_TYPE):
            with self.subTest(for_type=for_type):
                names = self._gui_field_names(for_type)
                self.assertLess(names.index("level"), names.index("enabled"))

    def test_table_level_column_maps_stored_values_to_level_names(self) -> None:
        level_column = self._table_field("level")
        self.assertEqual(level_column["type"], "dictionary")
        self.assertEqual(
            level_column["dict"],
            {str(level.value): level.name for level in models.LogLevel},
        )

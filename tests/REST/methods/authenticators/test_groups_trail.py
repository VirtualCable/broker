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
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
"""
Group trail tests: REST mutations leave readable log entries.

The group log type is registered (LogObjectType.GROUP) and the handler
writes on it: creation, modification (with a summary of what changed),
memberships added via add_to_group and the deletion (recorded on the
parent authenticator, because the group trail dies with the row).
"""

import logging
import typing

from uds import models
from uds.core.util import log

from tests.utils import rest

logger: logging.Logger = logging.getLogger(__name__)


class GroupsTrailTest(rest.test.RESTTestCase):
    """Freezes the trail written by the groups REST handler."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()

    def _messages(self, obj: "models.Group | models.Authenticator") -> list[str]:
        return [str(entry["message"]) for entry in log.get_logs(obj)]

    def test_creation_and_update_write_the_trail(self) -> None:
        response = self.client.rest_put(
            f"authenticators/{self.auth.uuid}/groups",
            {"type": "normal", "name": "trail-group", "comments": "trail test", "state": "A", "skip_mfa": "F"},
        )
        self.assertEqual(response.status_code, 200, response.content.decode(errors="replace"))
        group = models.Group.objects.get(uuid=response.json()["id"])

        self.assertTrue(
            any("Created normal group trail-group by" in message for message in self._messages(group)),
            self._messages(group),
        )

        response = self.client.rest_put(
            f"authenticators/{self.auth.uuid}/groups/{group.uuid}",
            {"type": "normal", "name": "trail-group", "state": "I", "comments": "trail test", "skip_mfa": "F"},
        )
        self.assertEqual(response.status_code, 200, response.content.decode(errors="replace"))

        self.assertTrue(
            any(
                "Modified group trail-group by" in message and "changed state" in message
                for message in self._messages(group)
            ),
            self._messages(group),
        )

    def test_meta_membership_changes_are_reported(self) -> None:
        member = self.simple_groups[0]
        response = self.client.rest_put(
            f"authenticators/{self.auth.uuid}/groups",
            {
                "type": "meta",
                "name": "trail-meta",
                "groups": member.uuid,
                "comments": "",
                "state": "A",
                "skip_mfa": "F",
            },
        )
        self.assertEqual(response.status_code, 200, response.content.decode(errors="replace"))
        meta = models.Group.objects.get(uuid=response.json()["id"])

        self.assertTrue(
            any("Created meta group trail-meta by" in message for message in self._messages(meta)),
            self._messages(meta),
        )

    def test_delete_is_recorded_on_the_authenticator_trail(self) -> None:
        response = self.client.rest_put(
            f"authenticators/{self.auth.uuid}/groups",
            {"type": "normal", "name": "doomed-group", "comments": "", "state": "A", "skip_mfa": "F"},
        )
        self.assertEqual(response.status_code, 200, response.content.decode(errors="replace"))
        group = models.Group.objects.get(uuid=response.json()["id"])

        response = self.client.rest_delete(f"authenticators/{self.auth.uuid}/groups/{group.uuid}")
        self.assertEqual(response.status_code, 200, response.content.decode(errors="replace"))

        self.assertTrue(
            any("Deleted group doomed-group by" in message for message in self._messages(self.auth)),
            self._messages(self.auth),
        )

    def test_add_to_group_logs_both_sides_and_refuses_external(self) -> None:
        from types import SimpleNamespace
        from unittest import mock

        user = self.plain_users[0]
        group = self.simple_groups[0]

        response = self.client.rest_post(
            f"authenticators/{self.auth.uuid}/users/{user.uuid}/add_to_group",
            {"group": group.uuid},
        )
        self.assertEqual(response.status_code, 200, response.content.decode(errors="replace"))
        self.assertTrue(
            any(f"Added user {user.name} by" in message for message in self._messages(group)),
            self._messages(group),
        )

        # External authenticators sync their membership from the provider:
        # manual additions would not survive the next refresh
        patcher = mock.patch.object(
            models.Authenticator,
            "get_instance",
            return_value=SimpleNamespace(external_source=True),
        )
        with patcher:
            response = self.client.rest_post(
                f"authenticators/{self.auth.uuid}/users/{user.uuid}/add_to_group",
                {"group": group.uuid},
            )
        self.assertEqual(response.status_code, 400, response.content.decode(errors="replace"))

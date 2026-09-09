#
# Copyright (c) 2025 Virtual Cable S.L.
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
Read-only management API tests for /flows/management (admin) and its actions detail.

The flows are created by their owners through the store (as the
proposal API/MCP does); this handler must only allow inspecting and
deleting them (admins), refusing create/edit with a clean 403.
"""

import logging
import typing

from uds.core import types
from uds.mutability import FlowStore
from uds.models import ActionFlow

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.utils import rest

logger: logging.Logger = logging.getLogger(__name__)

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


class FlowsRestTest(rest.test.RESTTestCase):
    """/flows/management management surface."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.store = FlowStore()
        users = create_db_users(create_db_authenticator(), number_of_users=2)
        self.owner = users[0]
        self.staff = users[1]
        self.flow = self.store.create_flow(
            owner=self.owner,
            agent="test-agent/1.0",
            name="test flow",
            justification="testing",
        )
        self.first = self.store.add_action(
            self.flow,
            action_type="provider.update",
            target_uuid="target-1",
            values={"name": "renamed"},
            base_values={"name": "old"},
            base_etag="abc123",
        )
        self.second = self.store.add_action(
            self.flow,
            action_type="provider.update",
            target_uuid="target-2",
            values={"comments": "new comment"},
            base_values={"comments": "old comment"},
            base_etag="def456",
        )

    def _get_json(self, path: str) -> dict[str, typing.Any]:
        response = self.client.rest_get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", response.json())

    def test_overview_lists_flows(self) -> None:
        items: list[dict[str, typing.Any]] = self.client.rest_get("flows/management/overview").json()
        self.assertEqual(len(items), 1)
        item = items[0]
        self.assertEqual(item["id"], self.flow.uuid)
        self.assertEqual(item["status"], "pending")
        self.assertEqual(item["owner"], self.owner.name)
        self.assertEqual(item["actions_count"], 2)

    def test_get_item(self) -> None:
        item = self._get_json(f"flows/management/{self.flow.uuid}")
        self.assertEqual(item["id"], self.flow.uuid)
        self.assertEqual(item["name"], "test flow")
        self.assertEqual(item["justification"], "testing")
        self.assertEqual(item["actions_count"], 2)

    def test_actions_detail_listed_in_order(self) -> None:
        items: list[dict[str, typing.Any]] = self.client.rest_get(f"flows/management/{self.flow.uuid}/actions").json()
        self.assertEqual([i["order"] for i in items], [1, 2])
        self.assertEqual(items[0]["id"], self.first.uuid)
        self.assertEqual(items[0]["values"], {"name": "renamed"})
        self.assertEqual(items[1]["target_uuid"], "target-2")

    def test_get_single_action(self) -> None:
        item = self._get_json(f"flows/management/{self.flow.uuid}/actions/{self.first.uuid}")
        self.assertEqual(item["action_type"], "provider.update")
        self.assertEqual(item["status"], "pending")

    def test_actions_table_info(self) -> None:
        table = self._get_json(f"flows/management/{self.flow.uuid}/actions/tableinfo")
        self.assertIn("title", table)

    def test_post_create_refused(self) -> None:
        response = self.client.rest_post("flows/management", data={"name": "nope", "justification": "no"})
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(ActionFlow.objects.count(), 1)

    def test_put_create_refused(self) -> None:
        response = self.client.rest_put("flows/management", data={"name": "nope", "justification": "no"})
        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(ActionFlow.objects.count(), 1)

    def test_put_edit_refused(self) -> None:
        response = self.client.rest_put(
            f"flows/management/{self.flow.uuid}", data={"name": "changed", "justification": "changed"}
        )
        self.assertEqual(response.status_code, 403, response.content)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.name, "test flow")

    def test_delete_flow_removes_actions(self) -> None:
        response = self.client.rest_delete(f"flows/management/{self.flow.uuid}")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(ActionFlow.objects.filter(uuid=self.flow.uuid).exists())
        self.assertEqual(self.store.list_actions(owner_uuid=str(self.owner.uuid)), [])

    def test_actions_cannot_be_written_or_deleted(self) -> None:
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/actions", data={"action_type": "x"})
        self.assertEqual(response.status_code, 400, response.content)

        response = self.client.rest_put(
            f"flows/management/{self.flow.uuid}/actions/{self.first.uuid}",
            data={"values": {"name": "changed"}},
        )
        self.assertEqual(response.status_code, 400, response.content)

        response = self.client.rest_delete(f"flows/management/{self.flow.uuid}/actions/{self.first.uuid}")
        self.assertEqual(response.status_code, 400, response.content)
        self.first.refresh_from_db()


class FlowsPermissionsTest(rest.test.RESTTestCase):
    """Staff sees only flows it has permission over."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.store = FlowStore()
        users = create_db_users(create_db_authenticator(), number_of_users=2)
        self.owner = users[0]
        self.staff = create_db_users(create_db_authenticator(), is_staff=True)[0]
        self.flow = self.store.create_flow(owner=self.owner, name="hidden flow")
        self.store.add_action(
            self.flow,
            action_type="provider.update",
            target_uuid="target-1",
            values={"name": "x"},
            base_values={"name": "y"},
            base_etag="etag",
        )

    def test_staff_without_permission_sees_nothing(self) -> None:
        self.login_with_api_token(user=self.staff, as_admin=False)
        items: list[dict[str, typing.Any]] = self.client.rest_get("flows/management/overview").json()
        self.assertEqual(items, [])

        response = self.client.rest_get(f"flows/management/{self.flow.uuid}")
        # Opaque 404 (existing framework behavior for single items without
        # permission: it does not reveal the item exists)
        self.assertEqual(response.status_code, 404, response.content)

        response = self.client.rest_get(f"flows/management/{self.flow.uuid}/actions")
        self.assertEqual(response.status_code, 403, response.content)

        response = self.client.rest_delete(f"flows/management/{self.flow.uuid}")
        self.assertEqual(response.status_code, 403, response.content)

    def test_staff_with_permission_can_inspect_but_not_edit(self) -> None:
        from uds.core.util import permissions as perm_utils

        perm_utils.add_user_permission(self.staff, self.flow, types.permissions.PermissionType.ALL)
        self.login_with_api_token(user=self.staff, as_admin=False)

        items: list[dict[str, typing.Any]] = self.client.rest_get("flows/management/overview").json()
        self.assertEqual([i["id"] for i in items], [self.flow.uuid])

        response = self.client.rest_get(f"flows/management/{self.flow.uuid}/actions")
        self.assertEqual(response.status_code, 200, response.content)

        # Still refused to write: the refusal is by design, not by permission
        response = self.client.rest_put(f"flows/management/{self.flow.uuid}", data={"name": "changed"})
        self.assertEqual(response.status_code, 403, response.content)

    def test_staff_cannot_create_flows(self) -> None:
        self.login_with_api_token(user=self.staff, as_admin=False)
        response = self.client.rest_post("flows/management", data={"name": "nope"})
        self.assertEqual(response.status_code, 403, response.content)

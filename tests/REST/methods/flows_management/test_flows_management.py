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
from unittest import mock

from asgiref.sync import async_to_sync
from django.test import TransactionTestCase

from uds.core import consts, types
from uds.core.types.mcp import FlowActionStatus, FlowStatus
from uds.core.util import permissions
from uds.models.user import create_api_token, hash_api_token
from uds.mutability import FlowStore
from uds.mutability.types_providers import ProviderUpdate
from uds.models import ActionFlow

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.fixtures.services import create_db_provider
from tests.utils import rest
from tests.utils.test import UDSAsyncClient

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
        items: list[dict[str, typing.Any]] = self.client.rest_get(
            f"flows/management/{self.flow.uuid}/actions"
        ).json()
        self.assertEqual([i["order"] for i in items], [1, 2])
        self.assertEqual(items[0]["id"], self.first.uuid)
        self.assertEqual(items[0]["values"], {"name": "renamed"})
        self.assertEqual(items[1]["target_uuid"], "target-2")

    def test_get_single_action(self) -> None:
        item = self._get_json(f"flows/management/{self.flow.uuid}/actions/{self.first.uuid}")
        self.assertEqual(item["action_type"], "provider.update")
        self.assertEqual(item["status"], "pending")

    def test_actions_detail_exposes_admin_view_data(self) -> None:
        items: list[dict[str, typing.Any]] = self.client.rest_get(
            f"flows/management/{self.flow.uuid}/actions"
        ).json()
        # Admin view: live compliance indicator and approval snapshot (empty until approved)
        self.assertIn("compliance", items[0])
        self.assertIn("snap_info", items[0])
        # The fake targets do not exist: unverifiable is never "ok"
        self.assertEqual(items[0]["compliance"], "conflict")
        self.assertEqual(items[0]["snap_info"], {})

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
        response = self.client.rest_post(
            f"flows/management/{self.flow.uuid}/actions", data={"action_type": "x"}
        )
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


class FlowsApproveRejectTest(rest.test.RESTTestCase):
    """Flow lock/approve/reject and the per-action approve/skip methods.

    The flow launches only after every action is approved or skipped;
    pass 1 (launch gate) revokes actions whose whole target changed.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.store = FlowStore()
        self.owner = create_db_users(create_db_authenticator(), number_of_users=1)[0]
        self.provider = create_db_provider()
        self.flow = self.store.create_flow(owner=self.owner, name="provider rename")
        action_type = ProviderUpdate()
        values = {"name": "renamed by agent"}
        base_values, base_etag = action_type.snapshot_and_fingerprint(self.provider, values)
        self.action = self.store.add_action(
            self.flow,
            action_type="provider.update",
            target_uuid=self.provider.uuid,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
        )

    def _actions_url(self) -> str:
        return f"flows/management/{self.flow.uuid}/actions"

    def _approve_action(self) -> typing.Any:
        return self.client.rest_post(f"{self._actions_url()}/{self.action.uuid}/approve", data={})

    def _skip_action(self) -> typing.Any:
        return self.client.rest_post(f"{self._actions_url()}/{self.action.uuid}/skip", data={})

    def _approve(self) -> dict[str, typing.Any]:
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/approve", data={})
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", response.json())

    # ------------------------------------------------- per-action endpoints

    def test_approve_action_freezes_live_state_and_acquires_flow(self) -> None:
        response = self._approve_action()
        self.assertEqual(response.status_code, 200, response.content)
        body = typing.cast("dict[str, typing.Any]", response.json())
        self.assertEqual(body["status"], FlowActionStatus.APPROVED)
        self.assertEqual(body["compliance"], "ok")
        self.assertEqual(body["snap_info"]["current_values"], {"name": self.provider.name})
        self.assertTrue(body["snap_info"]["approved_by"])
        self.action.refresh_from_db()
        # Untouched target: the frozen fingerprint matches the proposal base
        self.assertEqual(self.action.approved_etag, self.action.base_etag)
        self.assertEqual(self.action.approved_values, {"name": self.provider.name})
        # First admin touch acquires the flow
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)

    def test_approve_action_refused_once_flow_is_decided(self) -> None:
        self.store.reject_flow(self.flow, admin="admin-1")
        response = self._approve_action()
        self.assertEqual(response.status_code, 400, response.content)

    def test_skip_action_marks_and_acquires_flow(self) -> None:
        response = self._skip_action()
        self.assertEqual(response.status_code, 200, response.content)
        body = typing.cast("dict[str, typing.Any]", response.json())
        self.assertEqual(body["status"], FlowActionStatus.SKIPPED)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)

    def test_launch_without_approved_actions_refused(self) -> None:
        self._skip_action()
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/approve", data={})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("no approved actions", str(response.json()))

    def test_lock_endpoint_acquires_flow(self) -> None:
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/lock", data={})
        self.assertEqual(response.status_code, 200, response.content)
        body = typing.cast("dict[str, typing.Any]", response.json())
        self.assertEqual(body["status"], FlowStatus.LOCKED)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)

    def test_reject_works_on_locked_flow(self) -> None:
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/lock", data={})
        self.assertEqual(response.status_code, 200, response.content)
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/reject", data={"reason": "nope"})
        self.assertEqual(response.status_code, 200, response.content)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.REJECTED)
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, FlowActionStatus.SKIPPED)

    # -------------------------------------------------------- flow approval

    def test_approve_requires_all_actions_ready(self) -> None:
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/approve", data={})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("approved, skipped or executed", str(response.json()))
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.PENDING)

    def test_approve_executes_flow_in_order(self) -> None:
        self.assertEqual(self._approve_action().status_code, 200)
        with mock.patch("uds.mutability.types_providers.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="provider updated")
            summary = self._approve()

        self.assertEqual(summary["status"], "executed")
        self.assertEqual(len(summary["results"]), 1)
        self.assertTrue(summary["results"][0]["ok"])

        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.EXECUTED)
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, FlowActionStatus.EXECUTED)
        # CAS freeze: approved_etag was stored (identical to base here)
        self.assertEqual(self.action.approved_etag, self.action.base_etag)
        self.assertEqual(
            self.action.properties.get("result"),
            f'Provider "{self.provider.name}" updated',
        )

    def test_approve_refuses_and_revokes_drifted_actions(self) -> None:
        self.assertEqual(self._approve_action().status_code, 200)
        # The target drifted after the action was approved
        self.provider.name = "changed by a human"
        self.provider.save()

        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/approve", data={})
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("revoked", str(response.json()))

        # The flow stays locked; only the drifted action is revoked
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.LOCKED)
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, FlowActionStatus.REVOKED)

        # Recovery: approve the revoked action again (fresh freeze) and launch
        self.assertEqual(self._approve_action().status_code, 200)
        with mock.patch("uds.mutability.types_providers.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="provider updated")
            summary = self._approve()
        self.assertEqual(summary["status"], "executed")

    def test_approve_twice_refused(self) -> None:
        self.assertEqual(self._approve_action().status_code, 200)
        with mock.patch("uds.mutability.types_providers.RestProxy") as proxy_cls:
            proxy_cls.return_value.execute = mock.AsyncMock(return_value="ok")
            self._approve()
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/approve", data={})
        self.assertEqual(response.status_code, 400, response.content)

    def test_reject_flow_and_skip_actions(self) -> None:
        response = self.client.rest_post(
            f"flows/management/{self.flow.uuid}/reject", data={"reason": "not now"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        body = typing.cast("dict[str, typing.Any]", response.json())
        self.assertEqual(body["status"], "rejected")
        self.assertEqual(body["note"], "not now")

        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.REJECTED)
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, FlowActionStatus.SKIPPED)

    # ------------------------------------------------------------ permissions

    def test_non_admin_cannot_approve(self) -> None:
        staff = create_db_users(create_db_authenticator(), is_staff=True)[0]
        self.login_with_api_token(user=staff, as_admin=False)
        response = self.client.rest_post(f"flows/management/{self.flow.uuid}/approve", data={})
        self.assertEqual(response.status_code, 403, response.content)
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.PENDING)

    def test_non_admin_cannot_approve_or_skip_actions(self) -> None:
        staff = create_db_users(create_db_authenticator(), is_staff=True)[0]
        self.login_with_api_token(user=staff, as_admin=False)
        self.assertEqual(self._approve_action().status_code, 403)
        self.assertEqual(self._skip_action().status_code, 403)
        self.action.refresh_from_db()
        self.assertEqual(self.action.status, FlowActionStatus.PENDING)


class FlowsApproveAsgiTest(TransactionTestCase):
    """Approve → execute through the real ASGI stack, no mocks.

    Drives ``django.test.AsyncClient`` (which goes through the real
    ``ASGIHandler``, exactly as a production server would) with
    ``async_to_sync`` from the sync test method. Regression guard for the
    plain ``asyncio.run`` inside the sync view: under ASGI the handler
    thread owns an asgiref ``CurrentThreadExecutor``, and the action's
    ``sync_to_async(thread_sensitive=True)`` used to submit onto its own
    thread ("You cannot submit onto CurrentThreadExecutor from its own
    thread"). The executor now goes through ``async_to_sync``, as the MCP
    surface does.

    Extends ``TransactionTestCase`` because under ASGI the sync view body
    runs on the thread-sensitive worker thread, whose DB connection only
    sees committed data (TestCase's open transaction would hide the
    fixtures from it).
    """

    @typing.override
    def setUp(self) -> None:
        self.authenticator = create_db_authenticator()
        self.admin = create_db_users(self.authenticator, number_of_users=1, is_staff=True, is_admin=True)[0]
        self.raw_token = create_api_token()
        self.admin.token_hash = hash_api_token(self.raw_token)
        self.admin.save(update_fields=["token_hash"])
        self.provider = create_db_provider()

    def test_approve_executes_through_asgi_stack(self) -> None:
        permissions.add_user_permission(self.admin, self.provider, types.permissions.PermissionType.MANAGEMENT)
        store = FlowStore()
        flow = store.create_flow(owner=self.admin, name="asgi rename", justification="asgi path")
        action_type = ProviderUpdate()
        values = {
            "name": "renamed through asgi",
            "comments": self.provider.comments,
            "tags": [],
        }
        base_values, base_etag = action_type.snapshot_and_fingerprint(self.provider, values)
        action = store.add_action(
            flow,
            action_type="provider.update",
            target_uuid=self.provider.uuid,
            values=values,
            base_values=base_values,
            base_etag=base_etag,
        )
        client = UDSAsyncClient()
        client.add_header(consts.auth.AUTHORIZATION_HEADER, f"Bearer {self.raw_token}")

        # Freeze (per-action approve) and launch, both through the ASGI stack
        frozen = async_to_sync(client.rest_post)(
            f"flows/management/{flow.uuid}/actions/{action.uuid}/approve", data={}
        )
        self.assertEqual(frozen.status_code, 200, frozen.content)
        launched = async_to_sync(client.rest_post)(f"flows/management/{flow.uuid}/approve", data={})
        self.assertEqual(launched.status_code, 200, launched.content)
        summary = launched.json()

        # No mocks: the real execution went through the provider REST
        # machinery and updated (committed) the provider
        self.assertEqual(summary["status"], FlowStatus.EXECUTED)
        self.assertTrue(summary["results"][0]["ok"], summary)
        self.provider.refresh_from_db()
        self.assertEqual(self.provider.name, "renamed through asgi")
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.EXECUTED)

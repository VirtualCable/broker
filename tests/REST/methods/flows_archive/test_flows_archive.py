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
Archive surface tests for /flows/archive (admin, read-only).

Covers the approval/archive partition (which side a flow belongs to, the
opaque wrong-side 404), the read-only contract of the archive, and the
owner history horizon that trims /flows/own for non-admin staff.
"""

import datetime
import typing
from unittest import mock

from uds.core.types.mcp import FlowStatus
from uds.core.util.config import GlobalConfig
from uds.mutability import FlowStore
from uds.models import ActionFlow

from tests.fixtures.authenticators import create_db_authenticator, create_db_users
from tests.utils import rest

# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false, reportUnknownVariableType=false


def _retention_days(days: int) -> typing.Any:
    """Patch the approval-surface retention window (read at call time)."""
    return mock.patch.object(
        GlobalConfig, "MCP_APPROVAL_RETENTION_DAYS", mock.Mock(as_int=mock.Mock(return_value=days))
    )


class FlowsPartitionTest(rest.test.RESTTestCase):
    """The approval/archive partition: every flow lives on exactly one side.

    Decided flows linger on approval inside the retention window and
    move to the archive afterwards; expired flows move immediately;
    drafts belong to neither surface.
    """

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.store = FlowStore()
        self.owner = create_db_users(create_db_authenticator(), number_of_users=1)[0]

    def _decided_flow(self, *, decide: str) -> ActionFlow:
        flow = self.store.create_flow(owner=self.owner, name=f"{decide} flow")
        self.store.add_action(
            flow,
            action_type="provider.update",
            target_uuid="target-1",
            values={"name": "x"},
            base_values={"name": "y"},
            base_etag="etag",
        )
        self.store.submit_flow(flow, actor_uuid=self.owner.uuid)
        if decide == "rejected":
            self.store.reject_flow(flow, admin="admin-1")
        elif decide == "cancelled":
            self.store.cancel_flow(flow, actor_uuid=self.owner.uuid)
        else:
            raise AssertionError(f"unknown decision {decide}")
        flow.refresh_from_db()
        return flow

    def _archive_overview(self) -> list[typing.Any]:
        return typing.cast("list[typing.Any]", self.client.rest_get("flows/archive/overview").json())

    def _approval_overview(self) -> list[typing.Any]:
        return typing.cast("list[typing.Any]", self.client.rest_get("flows/approval/overview").json())

    def test_recently_decided_stays_on_approval(self) -> None:
        flow = self._decided_flow(decide="rejected")
        with _retention_days(7):
            self.assertIn(flow.uuid, {i["id"] for i in self._approval_overview()})
            self.assertNotIn(flow.uuid, {i["id"] for i in self._archive_overview()})

    def test_decided_past_window_is_archived(self) -> None:
        flow = self._decided_flow(decide="rejected")
        ActionFlow.objects.filter(uuid=flow.uuid).update(
            decided_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=8)
        )
        with _retention_days(7):
            self.assertNotIn(flow.uuid, {i["id"] for i in self._approval_overview()})
            self.assertIn(flow.uuid, {i["id"] for i in self._archive_overview()})

    def test_partition_is_a_disjoint_cover(self) -> None:
        """No flow appears on both surfaces, and both together list all."""
        recent = self._decided_flow(decide="rejected")
        old = self._decided_flow(decide="cancelled")
        ActionFlow.objects.filter(uuid=old.uuid).update(
            decided_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
        )
        open_flow = self.store.create_flow(owner=self.owner, name="pending one")
        self.store.add_action(
            open_flow,
            action_type="provider.update",
            target_uuid="t",
            values={"name": "x"},
            base_values={"name": "y"},
            base_etag="e",
        )
        self.store.submit_flow(open_flow, actor_uuid=self.owner.uuid)
        draft = self.store.create_flow(owner=self.owner, name="a draft")

        with _retention_days(7):
            approval = {i["id"] for i in self._approval_overview()}
            archive = {i["id"] for i in self._archive_overview()}

        self.assertEqual(approval & archive, set())
        self.assertEqual(approval, {recent.uuid, open_flow.uuid})
        self.assertEqual(archive, {old.uuid})
        self.assertNotIn(draft.uuid, approval | archive)

    def test_expired_flows_archive_immediately(self) -> None:
        flow = self.store.create_flow(owner=self.owner, name="abandoned draft")
        self.store.add_action(
            flow,
            action_type="provider.update",
            target_uuid="t",
            values={"name": "x"},
            base_values={"name": "y"},
            base_etag="e",
        )
        ActionFlow.objects.filter(uuid=flow.uuid).update(
            due_date=datetime.datetime.now(datetime.UTC) - datetime.timedelta(minutes=1)
        )
        self.store.get_flow(flow.uuid)  # lazy expiry
        flow.refresh_from_db()
        self.assertEqual(flow.status, FlowStatus.EXPIRED)
        with _retention_days(365):  # even with a huge window
            self.assertNotIn(flow.uuid, {i["id"] for i in self._approval_overview()})
            self.assertIn(flow.uuid, {i["id"] for i in self._archive_overview()})

    def test_zero_retention_archives_decided_at_once(self) -> None:
        flow = self._decided_flow(decide="rejected")
        with _retention_days(0):
            self.assertNotIn(flow.uuid, {i["id"] for i in self._approval_overview()})
            self.assertIn(flow.uuid, {i["id"] for i in self._archive_overview()})


class FlowsArchiveReadOnlyTest(rest.test.RESTTestCase):
    """The archive is the audit trail: nothing on it can be written."""

    @typing.override
    def setUp(self) -> None:
        super().setUp()
        self.login()
        self.store = FlowStore()
        self.owner = create_db_users(create_db_authenticator(), number_of_users=1)[0]
        self.flow = self.store.create_flow(owner=self.owner, name="archived flow")
        self.action = self.store.add_action(
            self.flow,
            action_type="provider.update",
            target_uuid="target-1",
            values={"name": "x"},
            base_values={"name": "y"},
            base_etag="etag",
        )
        self.store.submit_flow(self.flow, actor_uuid=self.owner.uuid)
        self.store.reject_flow(self.flow, admin="admin-1", reason="not needed")
        ActionFlow.objects.filter(uuid=self.flow.uuid).update(
            decided_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=30)
        )

    def test_reads_work_on_the_archive(self) -> None:
        item = self.client.rest_get(f"flows/archive/{self.flow.uuid}")
        self.assertEqual(item.status_code, 200, item.content)
        body = typing.cast("dict[str, typing.Any]", item.json())
        self.assertEqual(body["status"], FlowStatus.REJECTED)
        self.assertIsNotNone(body["decided_at"])
        self.assertEqual(body["decided_note"], "not needed")

        actions = self.client.rest_get(f"flows/archive/{self.flow.uuid}/actions")
        self.assertEqual(actions.status_code, 200, actions.content)
        items = typing.cast("list[dict[str, typing.Any]]", actions.json())
        self.assertEqual([i["id"] for i in items], [self.action.uuid])
        # Read-only view still shows the review data
        self.assertIn("compliance", items[0])
        self.assertIn("snap_info", items[0])

    def test_flows_table_offers_the_review_columns(self) -> None:
        """The archive's table is the approval's, with decided_at in place of due_date."""
        table = self._get_json("flows/archive/tableinfo")
        self.assertEqual(
            [name for field in table["fields"] for name in field],
            [
                "name",
                "justification",
                "status",
                "compliance",
                "owner",
                "actions_count",
                "created",
                "decided_at",
            ],
        )
        self.assertIn("compliance", table["filter_fields"])
        self.assertEqual(table["row_style"], {"prefix": "row-compliance-", "field": "compliance"})

    def _get_json(self, path: str) -> dict[str, typing.Any]:
        response = self.client.rest_get(path)
        self.assertEqual(response.status_code, 200, response.content)
        return typing.cast("dict[str, typing.Any]", response.json())

    def test_delete_is_denied_not_hidden(self) -> None:
        """The archive denies with a clean 403: the flow exists, it is immutable."""
        response = self.client.rest_delete(f"flows/archive/{self.flow.uuid}")
        self.assertEqual(response.status_code, 403, response.content)
        self.assertTrue(ActionFlow.objects.filter(uuid=self.flow.uuid).exists())

    def test_no_domain_operations_are_exposed(self) -> None:
        for verb, path in (
            (self.client.rest_post, f"flows/archive/{self.flow.uuid}/approve"),
            (self.client.rest_post, f"flows/archive/{self.flow.uuid}/reject"),
            (self.client.rest_post, f"flows/archive/{self.flow.uuid}/lock"),
            (self.client.rest_post, f"flows/archive/{self.flow.uuid}/actions/{self.action.uuid}/approve"),
            (self.client.rest_post, f"flows/archive/{self.flow.uuid}/actions/{self.action.uuid}/skip"),
        ):
            response = verb(path, data={})
            self.assertEqual(response.status_code, 400, f"{path}: {response.content}")
        self.flow.refresh_from_db()
        self.assertEqual(self.flow.status, FlowStatus.REJECTED)

    def test_write_paths_are_refused(self) -> None:
        response = self.client.rest_put(
            f"flows/archive/{self.flow.uuid}", data={"name": "changed", "justification": "nope"}
        )
        self.assertEqual(response.status_code, 403, response.content)
        response = self.client.rest_post("flows/archive", data={"name": "nope", "justification": "no"})
        self.assertEqual(response.status_code, 403, response.content)

    def test_wrong_side_lookup_is_opaque(self) -> None:
        """The wrong-side lookup is the same opaque 404 as a draft."""
        self.assertEqual(self.client.rest_get(f"flows/approval/{self.flow.uuid}").status_code, 404)
        self.assertEqual(self.client.rest_get(f"flows/approval/{self.flow.uuid}/actions").status_code, 404)
        self.assertEqual(self.client.rest_delete(f"flows/approval/{self.flow.uuid}").status_code, 404)
        self.assertTrue(ActionFlow.objects.filter(uuid=self.flow.uuid).exists())
